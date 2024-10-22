from django.contrib import admin

from subscription import models
from subscription.settings import CONFIG


class PlanCostInline(admin.TabularInline):
    """Inline admin class for the PlanCost model."""

    model = models.PlanCost
    fields = (
        "slug",
        "recurrence_period",
        "recurrence_unit",
        "cost",
    )
    extra = 0


class PricingTierAdmin(admin.ModelAdmin):
    """Inline admin class for the PricingTier model."""

    fields = (
        "plan_feature",
        "start_quantity",
        "end_quantity",
        "unit_price",
        "flat_fee",
    )


class PlanFeatureInline(admin.TabularInline):
    """Inline admin class for the PlanFeature model."""

    model = models.PlanFeature
    fields = (
        "feature",
        "enabled",
        "quota",
        "rate_limit",
        "rate_window",
        "overage_rate",
    )
    extra = 0


class FeatureAdmin(admin.ModelAdmin):
    """Admin class for the Feature model."""

    fields = (
        "name",
        "code",
        "description",
        "feature_type",
        "pricing_model",
        "unit",
        "reset_on_billing",
    )
    list_display = (
        "name",
        "code",
        "feature_type",
        "pricing_model",
    )
    prepopulated_fields = {"code": ("name",)}
    search_fields = ("name", "code")
    list_filter = ("feature_type", "pricing_model", "reset_on_billing")


class SubscriptionPlanAdmin(admin.ModelAdmin):
    """Admin class for the SubscriptionPlan model."""

    fields = (
        "plan_name",
        "slug",
        "is_feature_based",
        "plan_description",
        "tags",
        "grace_period",
    )
    inlines = [PlanCostInline, PlanFeatureInline]
    list_display = ("plan_name",)
    prepopulated_fields = {"slug": ("plan_name",)}


class UserSubscriptionAdmin(admin.ModelAdmin):
    """Admin class for the UserSubscription model."""

    fields = (
        "user",
        "date_billing_start",
        "date_billing_end",
        "date_billing_last",
        "date_billing_next",
        "active",
        "cancelled",
    )
    list_display = (
        "user",
        "date_billing_last",
        "date_billing_next",
        "active",
        "cancelled",
    )


class PlanTagAdmin(admin.ModelAdmin):
    """Admin class for the PlanTag model."""

    list_display = ("tag",)
    search_fields = ("tag",)
    ordering = ("tag",)


if CONFIG["ENABLE_ADMIN"]:
    admin.site.register(models.SubscriptionPlan, SubscriptionPlanAdmin)
    admin.site.register(models.UserSubscription, UserSubscriptionAdmin)
    admin.site.register(models.Feature, FeatureAdmin)
    admin.site.register(models.PlanTag, PlanTagAdmin)
    admin.site.register(models.PricingTier, PricingTierAdmin)
