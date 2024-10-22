from enum import Enum

from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _


class FeatureType(Enum):
    # BOOLEAN represents features that are either on or off (enabled/disabled).
    # For example, "Access to API" or "Dark Mode Support".
    BOOLEAN = "boolean"
    # QUOTA: Represents features with a fixed limit over the billing period.
    # For example, "500 API calls per month" or "10 team members".
    QUOTA = "quota"
    # RATE Represents features with time-window based limits.
    # For example, "100 API calls per hour" or "10 exports per day".
    # These get reset after their time window expires.
    RATE = "rate"
    # USAGE: Represents features that are always allowed but charge based on consumption.
    # For example, "Pay per API call" or "Storage used".
    USAGE = "usage"


class PricingModel(Enum):
    # FLAT a pricing model where a fixed rate applies
    # either per unit of usage or overage beyond a certain threshold.
    # For example a flat rate of $0.05 per SMS sent.
    FLAT = "flat"
    # TIERED is a model where the price per unit varies depending on the
    # total quantity used. Different levels of usage are charged at different rates.
    # For example $10/unit for the first 100 units, then $8/unit for the next 200 units
    TIERED = "tiered"
    # VOLUME - All units of usage are charged at the same rate, but the rate is determined by
    # the total volume used. The more you use, the lower the rate.
    # E.g 1-100 users, it might cost $10/user 101-500 users, the rate drops to $8/user
    VOLUME = "volume"  # Price determined by total volume
    # PACKAGE - users are charged for predefined packages or bundles.
    # E.g 500 SMS for $20. If the user needs more, they purchase another package
    PACKAGE = "package"  # Pre-purchased packages of units


class Feature(models.Model):
    """Represents a feature that can be included in subscription plans."""

    name = models.CharField(max_length=100, unique=True)
    code = models.SlugField(
        max_length=100,
        unique=True,
        help_text=_("Unique code used to check feature access in code"),
    )
    description = models.TextField(blank=True)
    feature_type = models.CharField(
        max_length=20,
        choices=[(t.value, t.name) for t in FeatureType],
        default=FeatureType.BOOLEAN.value,
    )
    pricing_model = models.CharField(
        max_length=20,
        choices=[(t.value, t.name) for t in PricingModel],
        default=PricingModel.FLAT.value,
    )
    unit = models.CharField(
        max_length=50,
        blank=True,
        help_text=_("Unit of measurement for quota/usage features"),
    )
    reset_on_billing = models.BooleanField(
        default=True, help_text=_("Whether to reset usage counters on billing")
    )

    def __str__(self):
        return self.name


class PlanFeature(models.Model):
    """Associates features with subscription plans and defines limits."""

    plan = models.ForeignKey(
        "SubscriptionPlan", on_delete=models.CASCADE, related_name="plan_features"
    )
    feature = models.ForeignKey(
        Feature, on_delete=models.CASCADE, related_name="plan_features"
    )
    enabled = models.BooleanField(
        default=True, help_text=_("Whether this feature is enabled for the plan")
    )
    quota = models.IntegerField(
        null=True,
        blank=True,
        help_text=_("Maximum allowed quantity for quota features"),
    )
    rate_limit = models.IntegerField(
        null=True,
        blank=True,
        help_text=_("Number of operations allowed per time window"),
    )
    rate_window = models.DurationField(
        null=True, blank=True, help_text=_("Time window for rate limiting")
    )
    overage_rate = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        help_text=_("Cost per unit when exceeding quota"),
    )

    class Meta:
        unique_together = ("plan", "feature")

    def __str__(self):
        return f"{self.plan.plan_name} {self.feature.name}"


class PricingTier(models.Model):
    """Defines pricing tiers for features with tiered pricing."""

    plan_feature = models.ForeignKey(
        "PlanFeature", on_delete=models.CASCADE, related_name="pricing_tiers"
    )
    start_quantity = models.IntegerField(help_text=_("Starting quantity for this tier"))
    end_quantity = models.IntegerField(
        null=True,
        blank=True,
        help_text=_("Ending quantity for this tier (null for unlimited)"),
    )
    unit_price = models.DecimalField(
        max_digits=10, decimal_places=2, help_text=_("Price per unit in this tier")
    )
    flat_fee = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0,
        help_text=_("Fixed fee for this tier"),
    )

    class Meta:
        ordering = ["start_quantity"]


class FeatureUsage(models.Model):
    """Tracks usage of features by subscribed users."""

    subscription = models.ForeignKey(
        "UserSubscription", on_delete=models.CASCADE, related_name="feature_usage"
    )
    feature = models.ForeignKey(
        Feature, on_delete=models.CASCADE, related_name="usage_records"
    )
    quantity = models.IntegerField(default=0)
    last_reset = models.DateTimeField(default=timezone.now)

    class Meta:
        unique_together = ("subscription", "feature")
