from dataclasses import dataclass
from decimal import Decimal
from functools import wraps
from typing import Any, Dict, Optional

from django.core.cache import cache
from django.http import HttpResponseForbidden
from django.utils import timezone

from subscription.models.feature import (
    Feature,
    FeatureType,
    FeatureUsage,
    PlanFeature,
    PricingModel,
)
from subscription.models.plan import UserSubscription
from subscription.settings import CONFIG


@dataclass
class FeatureAccess:
    allowed: bool
    remaining: Optional[int] = None
    error: Optional[str] = None


class CachedFeatureChecker:
    """Handles feature access checking with caching."""

    CACHE_KEY_PREFIX = "feature_access:"
    CACHE_TIMEOUT = CONFIG["CACHE_TIMEOUT_MINUTES"]

    def __init__(self, subscription: UserSubscription):
        self.subscription = subscription

    def _get_cache_key(self, feature_code: str) -> str:
        return f"{self.CACHE_KEY_PREFIX}{self.subscription.id}:{feature_code}"

    def can_access(self, feature_code: str) -> FeatureAccess:
        """Check if user can access a feature with caching."""
        cache_key = self._get_cache_key(feature_code)
        cached_result = cache.get(cache_key)

        if cached_result is not None:
            return cached_result

        checker = FeatureChecker(self.subscription)
        result = checker.can_access(feature_code)

        cache.set(cache_key, result, self.CACHE_TIMEOUT)
        return result

    def increment_usage(self, feature_code: str, quantity: int = 1) -> None:
        """Increment usage and invalidate cache."""
        checker = FeatureChecker(self.subscription)
        checker.increment_usage(feature_code, quantity)

        # Invalidate cache for this feature
        cache_key = self._get_cache_key(feature_code)
        cache.delete(cache_key)


class FeatureChecker:
    """Handles checking feature access and tracking usage."""

    def __init__(self, subscription: "UserSubscription"):
        self.subscription = subscription

    def can_access(self, feature_code: str) -> FeatureAccess:
        """
        Check if user can access a feature and track usage if needed.
        This appears to be a design where:

        BOOLEAN features control access only
        boolean features are just on/off switches with no quantity to charge
        RATE features control access with time-window restrictions
        QUOTA features control access and can incur overage charges
        USAGE features are always allowed but incur charges based on use

        """
        try:
            feature = Feature.objects.get(code=feature_code)
            plan_feature = PlanFeature.objects.get(
                plan=self.subscription.subscription.plan, feature=feature
            )

            if not plan_feature.enabled:
                return FeatureAccess(
                    allowed=False, error="Feature not available in current plan"
                )

            if feature.feature_type == FeatureType.BOOLEAN.value:
                return FeatureAccess(allowed=True)

            usage, _ = FeatureUsage.objects.get_or_create(
                subscription=self.subscription, feature=feature
            )

            if feature.feature_type == FeatureType.QUOTA.value:
                remaining = plan_feature.quota - usage.quantity
                return FeatureAccess(
                    allowed=remaining > 0,
                    remaining=remaining,
                    error="Quota exceeded" if remaining <= 0 else None,
                )

            elif feature.feature_type == FeatureType.RATE.value:
                if self._should_reset_usage(usage, plan_feature.rate_window):
                    self._reset_usage(usage)

                remaining = plan_feature.rate_limit - usage.quantity
                return FeatureAccess(
                    allowed=remaining > 0,
                    remaining=remaining,
                    error="Rate limit exceeded" if remaining <= 0 else None,
                )

            elif feature.feature_type == FeatureType.USAGE.value:
                # Usage-based features are always allowed but may incur charges
                return FeatureAccess(allowed=True)

        except (Feature.DoesNotExist, PlanFeature.DoesNotExist):
            return FeatureAccess(allowed=False, error="Feature not found")

    def increment_usage(self, feature_code: str, quantity: int = 1) -> None:
        """Increment usage counter for a feature."""
        try:
            feature = Feature.objects.get(code=feature_code)
            usage, _ = FeatureUsage.objects.get_or_create(
                subscription=self.subscription, feature=feature
            )
            usage.quantity += quantity
            usage.save()

        except Feature.DoesNotExist:
            pass

    def _should_reset_usage(self, usage: FeatureUsage, window) -> bool:
        """Check if usage should be reset based on rate window."""
        if not window:
            return False
        return timezone.now() - usage.last_reset > window

    def _reset_usage(self, usage: FeatureUsage) -> None:
        """Reset usage counter and update last reset time."""
        usage.quantity = 0
        usage.last_reset = timezone.now()
        usage.save()


class FeatureMiddleware:
    """Middleware for automatic feature checking and usage tracking."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)

        # Track API usage if applicable
        if hasattr(request, "_feature_usage"):
            subscription = getattr(request.user, "subscription", None)
            if subscription:
                checker = CachedFeatureChecker(subscription)
                for feature_code, quantity in request._feature_usage.items():
                    checker.increment_usage(feature_code, quantity)

        return response


def requires_feature(feature_code: str, increment: bool = True):
    """Decorator for views that require feature access."""

    def decorator(view_func):
        @wraps(view_func)
        def wrapped_view(request, *args, **kwargs):
            if not hasattr(request, "user") or not request.user.is_authenticated:
                return HttpResponseForbidden("Authentication required")

            subscription = getattr(request.user, "subscription", None)
            if not subscription:
                return HttpResponseForbidden("No active subscription")

            checker = CachedFeatureChecker(subscription)
            access = checker.can_access(feature_code)

            if not access.allowed:
                return HttpResponseForbidden(access.error or "Feature not available")

            if increment:
                # Store usage for later processing by middleware
                if not hasattr(request, "_feature_usage"):
                    request._feature_usage = {}
                request._feature_usage[feature_code] = (
                    request._feature_usage.get(feature_code, 0) + 1
                )

            return view_func(request, *args, **kwargs)

        return wrapped_view

    return decorator


class UsageBasedBilling:
    """
    Usage based billing calculations with support for different pricing models.

    The actual billing calculations focus primarily on:

    QUOTA features: Only charging for usage above the quota
    USAGE features: Charging for all usage

    There's no special handling for BOOLEAN features because they don't incur charges.
    """

    def calculate_charges(
        self, subscription: "UserSubscription", feature_code: str, quantity: int = 1
    ) -> Dict[str, Any]:
        """Calculate charges for a specific feature usage."""
        try:
            feature = Feature.objects.get(code=feature_code)
            plan_feature = PlanFeature.objects.get(
                plan=subscription.subscription.plan, feature=feature
            )

            # Validate feature type is billable
            if feature.feature_type == FeatureType.BOOLEAN.value:
                return {
                    "total": Decimal("0"),
                    "message": "Boolean features do not incur charges",
                }

            if feature.feature_type == FeatureType.RATE.value:
                # Rate features are controlled by time-window access limits
                return {
                    "total": Decimal("0"),
                    "message": "Rate-limited features do not incur charges",
                }

            # Handle different pricing models for USAGE and QUOTA types
            if feature.feature_type in (
                FeatureType.USAGE.value,
                FeatureType.QUOTA.value,
            ):
                if feature.pricing_model == PricingModel.FLAT.value:
                    return self._calculate_flat_rate(plan_feature, quantity)

                elif feature.pricing_model == PricingModel.TIERED.value:
                    return self._calculate_tiered_price(plan_feature, quantity)

                elif feature.pricing_model == PricingModel.VOLUME.value:
                    return self._calculate_volume_price(plan_feature, quantity)

                elif feature.pricing_model == PricingModel.PACKAGE.value:
                    return self._calculate_package_price(plan_feature, quantity)

                else:
                    return {
                        "error": f"Unsupported pricing model: {feature.pricing_model}"
                    }

            return {
                "error": f"Unsupported feature type for billing: {feature.feature_type}"
            }

        except Feature.DoesNotExist:
            return {"error": f"Feature not found: {feature_code}"}
        except PlanFeature.DoesNotExist:
            return {"error": f"Feature {feature_code} not configured for plan"}

    def _calculate_flat_rate(
        self, plan_feature: "PlanFeature", quantity: int
    ) -> Dict[str, Any]:
        """Calculate charges for flat-rate pricing."""
        feature = plan_feature.feature

        # For USAGE type features, charge for all usage
        if feature.feature_type == FeatureType.USAGE.value:
            if not plan_feature.overage_rate:
                return {
                    "error": "Usage feature has no rate configured",
                    "total": Decimal("0"),
                }

            total = Decimal(str(quantity)) * plan_feature.overage_rate
            return {
                "quantity": quantity,
                "rate": plan_feature.overage_rate,
                "total": total,
                "type": "usage",
            }

        # For QUOTA type features, only charge for overage
        quota = plan_feature.quota or 0
        if quantity <= quota:
            return {
                "total": Decimal("0"),
                "quota": quota,
                "usage": quantity,
                "type": "quota",
                "message": "Within quota limits",
            }

        if not plan_feature.overage_rate:
            return {
                "error": "Quota feature has no overage rate configured",
                "total": Decimal("0"),
            }

        overage = quantity - quota
        total = Decimal(str(overage)) * plan_feature.overage_rate

        return {
            "quantity": overage,
            "rate": plan_feature.overage_rate,
            "total": total,
            "quota": quota,
            "usage": quantity,
            "type": "quota",
        }

    def _calculate_tiered_price(
        self, plan_feature: "PlanFeature", quantity: int
    ) -> Dict[str, Any]:
        """Calculate charges for tiered pricing."""
        total = Decimal("0")
        tiers_used = []

        for tier in plan_feature.pricing_tiers.all():
            tier_quantity = 0

            if quantity > tier.start_quantity:
                tier_end = tier.end_quantity or quantity
                tier_quantity = min(
                    quantity - tier.start_quantity, tier_end - tier.start_quantity
                )

                tier_cost = (tier_quantity * tier.unit_price) + tier.flat_fee
                total += tier_cost

                tiers_used.append(
                    {
                        "tier_start": tier.start_quantity,
                        "tier_end": tier_end,
                        "quantity": tier_quantity,
                        "unit_price": tier.unit_price,
                        "flat_fee": tier.flat_fee,
                        "cost": tier_cost,
                    }
                )

        return {"total": total, "tiers": tiers_used}

    def _calculate_volume_price(
        self, plan_feature: "PlanFeature", quantity: int
    ) -> Dict[str, Any]:
        """Calculate charges based on total volume."""
        applicable_tier = None

        for tier in plan_feature.pricing_tiers.all():
            if quantity >= tier.start_quantity and (
                tier.end_quantity is None or quantity <= tier.end_quantity
            ):
                applicable_tier = tier
                break

        if applicable_tier:
            total = (quantity * applicable_tier.unit_price) + applicable_tier.flat_fee
            return {
                "quantity": quantity,
                "unit_price": applicable_tier.unit_price,
                "flat_fee": applicable_tier.flat_fee,
                "total": total,
            }
        return {"error": "No applicable pricing tier found"}

    def _calculate_package_price(
        self, plan_feature: "PlanFeature", quantity: int
    ) -> Dict[str, Any]:
        """Calculate charges for package-based pricing."""
        packages_needed = (quantity + plan_feature.quota - 1) // plan_feature.quota
        total = packages_needed * plan_feature.overage_rate

        return {
            "packages": packages_needed,
            "package_size": plan_feature.quota,
            "package_price": plan_feature.overage_rate,
            "total": total,
        }
