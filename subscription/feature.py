from dataclasses import dataclass
from functools import wraps
from typing import Optional, Set

from django.contrib.auth.mixins import UserPassesTestMixin
from django.core.cache import cache
from django.http import HttpResponseForbidden
from django.utils import timezone

from subscription.models.feature import Feature, FeatureType, FeatureUsage, PlanFeature
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


def requires_feature(feature_code: str):
    """
    Decorator to check feature access for API views

    Usage:
    @requires_feature('advanced_analytics')
    def get(self, request):
        ...

    @requires_feature('api_calls')
    def post(self, request):
        ...
    """

    def decorator(view_func):
        @wraps(view_func)
        def wrapped_view(request, *args, **kwargs):
            if not hasattr(request, "user") or not request.user.is_authenticated:
                return HttpResponseForbidden("Authentication required")

            try:
                subscription = request.user.subscription.filter(
                    active=True, cancelled=False
                ).first()

                if not subscription:
                    return HttpResponseForbidden("No active subscription")

                # Check subscription dates
                now = timezone.now()
                if (
                    subscription.date_billing_start
                    and subscription.date_billing_start > now
                ):
                    return HttpResponseForbidden("Subscription has not started yet")

                if (
                    subscription.date_billing_end
                    and subscription.date_billing_end < now
                ):
                    return HttpResponseForbidden("Subscription has expired")

                # Proceed with feature check
                checker = CachedFeatureChecker(subscription)
                access = checker.can_access(feature_code)

                if not access.allowed:
                    return HttpResponseForbidden(
                        access.error or "Feature not available"
                    )

                return view_func(request, *args, **kwargs)

            except Exception as e:
                return HttpResponseForbidden(f"Invalid subscription status: {e}")

        return wrapped_view

    return decorator


class FeatureRequiredMixin(UserPassesTestMixin):
    """Mixin for class-based views to check feature access."""

    required_features: Set[str] = set()

    def test_func(self):
        if not self.request.user.is_authenticated:
            return False

        subscription = self.request.user.subscription.filter(
            active=True, cancelled=False
        ).first()

        if not subscription:
            return False

        # Check subscription dates
        now = timezone.now()
        if subscription.date_billing_start and subscription.date_billing_start > now:
            return False

        if subscription.date_billing_end and subscription.date_billing_end < now:
            return False

        checker = CachedFeatureChecker(subscription)

        return all(
            checker.can_access(feature).allowed for feature in self.required_features
        )
