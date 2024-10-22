from decimal import Decimal
from typing import Any, Dict

from subscription.models.feature import Feature, FeatureType, PlanFeature, PricingModel
from subscription.models.plan import UserSubscription


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
