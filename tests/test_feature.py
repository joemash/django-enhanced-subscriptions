from datetime import timedelta
from decimal import Decimal
from unittest.mock import Mock, patch

import pytest
from django.core.cache import cache
from django.http import HttpResponse, HttpResponseForbidden
from django.test import RequestFactory
from django.utils import timezone
from model_bakery import baker

from subscription.billing import UsageBasedBilling
from subscription.feature import CachedFeatureChecker, FeatureChecker, requires_feature
from subscription.models.feature import (
    Feature,
    FeatureType,
    FeatureUsage,
    PlanFeature,
    PricingModel,
    PricingTier,
)
from subscription.models.plan import MONTH, PlanCost


@pytest.fixture(autouse=True)
def clear_cache():
    """Clear cache before each test."""
    cache.clear()
    yield


@pytest.fixture
def subscription_plan():
    return baker.make("SubscriptionPlan")


@pytest.fixture
def non_feature_subscription_plan():
    return baker.make("SubscriptionPlan")


@pytest.fixture
def plan_cost(subscription_plan):
    return baker.make(
        PlanCost,
        plan=subscription_plan,
        recurrence_period=1,
        recurrence_unit=MONTH,
        cost=Decimal("0"),
    )


@pytest.fixture
def non_feature_plan_cost(non_feature_subscription_plan):
    return baker.make(
        PlanCost,
        plan=non_feature_subscription_plan,
        recurrence_period=1,
        recurrence_unit=MONTH,
        cost=Decimal("25"),
    )


@pytest.fixture
def user_subscription(user, plan_cost):
    current = timezone.now()
    return baker.make(
        "UserSubscription",
        user=user,
        subscription=plan_cost,
        date_billing_start=current,
        date_billing_next=current + timedelta(days=30),
        active=True,
    )


@pytest.fixture
def non_feature_user_subscription(user, non_feature_plan_cost):
    current = timezone.now()
    return baker.make(
        "UserSubscription",
        user=user,
        subscription=non_feature_plan_cost,
        date_billing_start=current,
        date_billing_next=current + timedelta(days=30),
    )


@pytest.fixture
def feature():
    return baker.make(
        Feature,
        code="test_feature",
        feature_type=FeatureType.USAGE.value,
        pricing_model=PricingModel.FLAT.value,
        reset_on_billing=True,
    )


@pytest.fixture
def plan_feature(subscription_plan, feature):
    return baker.make(
        PlanFeature,
        plan=subscription_plan,
        feature=feature,
        enabled=True,
        quota=100,
        rate_limit=10,
        rate_window=timedelta(hours=1),
        overage_rate=Decimal("0.50"),
    )


class TestFeatureModel:
    def test_feature_creation(self):
        """Test basic feature creation with all field types."""
        feature = baker.make(
            Feature,
            name="Test Feature",
            code="test_feature",
            description="Test description",
            feature_type=FeatureType.QUOTA.value,
            pricing_model=PricingModel.TIERED.value,
            unit="requests",
        )

        assert feature.name == "Test Feature"
        assert feature.code == "test_feature"
        assert feature.feature_type == FeatureType.QUOTA.value
        assert feature.pricing_model == PricingModel.TIERED.value
        assert feature.unit == "requests"

    def test_feature_str_representation(self):
        """Test string representation of Feature model."""
        feature = baker.make(Feature, name="Test Feature")
        assert str(feature) == "Test Feature"


class TestPlanFeatureModel:
    def test_plan_feature_creation(self):
        """Test creation of plan feature with all fields."""
        plan_feature = baker.make(
            PlanFeature,
            enabled=True,
            quota=1000,
            rate_limit=100,
            rate_window=timedelta(hours=1),
            overage_rate=Decimal("0.10"),
        )

        assert plan_feature.enabled is True
        assert plan_feature.quota == 1000
        assert plan_feature.rate_limit == 100
        assert plan_feature.rate_window == timedelta(hours=1)
        assert plan_feature.overage_rate == Decimal("0.10")

    def test_unique_constraint(self, subscription_plan, feature):
        """Test that plan-feature combination must be unique."""
        baker.make(PlanFeature, plan=subscription_plan, feature=feature)

        with pytest.raises(Exception):  # Django will raise an IntegrityError
            baker.make(PlanFeature, plan=subscription_plan, feature=feature)


class TestCachedFeatureChecker:
    def test_cache_hit(self, user_subscription, feature, plan_feature):
        """Test that feature access check uses cache."""
        checker = CachedFeatureChecker(user_subscription)

        # First check should hit database
        access1 = checker.can_access(feature.code)

        # Modify database record (shouldn't affect cached result)
        plan_feature.enabled = False
        plan_feature.save()

        # Second check should use cache
        access2 = checker.can_access(feature.code)

        assert access1.allowed
        assert access2.allowed

    def test_cache_invalidation(self, user_subscription, feature):
        """Test that incrementing usage invalidates cache."""
        checker = CachedFeatureChecker(user_subscription)

        # Cache initial access check
        checker.can_access(feature.code)

        # Increment usage should invalidate cache
        checker.increment_usage(feature.code)

        # Next access check should hit database
        cache_key = checker._get_cache_key(feature.code)
        assert cache.get(cache_key) is None


class TestFeatureChecker:
    def test_boolean_feature_access(self, user_subscription, feature, plan_feature):
        """Test access to boolean feature."""
        checker = FeatureChecker(user_subscription)
        access = checker.can_access(feature.code)
        assert access.allowed is True

    def test_disabled_feature_access(self, user_subscription, feature, plan_feature):
        """Test access to disabled feature."""
        plan_feature.enabled = False
        plan_feature.save()

        checker = FeatureChecker(user_subscription)
        access = checker.can_access(feature.code)
        assert access.allowed is False
        assert access.error == "Feature not available in current plan"

    def test_quota_feature_access(self, user_subscription, feature, plan_feature):
        """Test access to quota-based feature."""
        feature.feature_type = FeatureType.QUOTA.value
        feature.save()

        checker = FeatureChecker(user_subscription)

        # Test within quota
        access = checker.can_access(feature.code)
        assert access.allowed is True
        assert access.remaining == plan_feature.quota

        # Test exceeding quota - use get_or_create to handle unique constraint
        usage, _ = FeatureUsage.objects.get_or_create(
            subscription=user_subscription,
            feature=feature,
            defaults={"quantity": plan_feature.quota},
        )
        if usage.quantity != plan_feature.quota:
            usage.quantity = plan_feature.quota
            usage.save()

        access = checker.can_access(feature.code)
        assert access.allowed is False
        assert access.remaining == 0
        assert access.error == "Quota exceeded"

    def test_rate_limited_feature_access(
        self, user_subscription, feature, plan_feature
    ):
        """Test access to rate-limited feature."""
        feature.feature_type = FeatureType.RATE.value
        feature.save()

        checker = FeatureChecker(user_subscription)

        # Test within rate limit
        access = checker.can_access(feature.code)
        assert access.allowed is True
        assert access.remaining == plan_feature.rate_limit

        # Test exceeding rate limit - use get_or_create to handle unique constraint
        usage, _ = FeatureUsage.objects.get_or_create(
            subscription=user_subscription,
            feature=feature,
            defaults={"quantity": plan_feature.rate_limit},
        )
        if usage.quantity != plan_feature.rate_limit:
            usage.quantity = plan_feature.rate_limit
            usage.save()

        access = checker.can_access(feature.code)
        assert access.allowed is False
        assert access.error == "Rate limit exceeded"

    def test_usage_feature_access(self, user_subscription, feature, plan_feature):
        """Test access to usage-based feature."""
        feature.feature_type = FeatureType.USAGE.value
        feature.save()

        checker = FeatureChecker(user_subscription)
        access = checker.can_access(feature.code)
        assert access.allowed is True


class TestFeatureDecorator:
    request_factory = RequestFactory()

    def test_requires_feature_decorator(self, user_subscription, feature, plan_feature):
        """Test the @requires_feature decorator."""
        request_factory = RequestFactory()

        @requires_feature("test_feature")
        def test_view(request):
            return HttpResponse(status=200)

        # Ensure user_subscription is active
        user_subscription.active = True
        user_subscription.cancelled = False
        user_subscription.save()

        with patch("subscription.feature.CachedFeatureChecker") as MockChecker:
            checker_mock = MockChecker.return_value

            # Simulate feature access allowed
            checker_mock.can_access.return_value = Mock(allowed=True)

            # Test authenticated request with feature access
            request = request_factory.get("/")
            request.user = user_subscription.user
            response = test_view(request)
            assert response.status_code == 200

            # Simulate unauthenticated request
            request = request_factory.get("/")
            request.user = Mock(is_authenticated=False)
            response = test_view(request)
            assert isinstance(response, HttpResponseForbidden)

            # Simulate feature access denied
            checker_mock.can_access.return_value = Mock(
                allowed=False, error="Feature disabled"
            )
            request = request_factory.get("/")
            request.user = user_subscription.user
            response = test_view(request)
            assert isinstance(response, HttpResponseForbidden)

            # Test inactive subscription
            user_subscription.active = False
            user_subscription.save()
            request = request_factory.get("/")
            request.user = user_subscription.user
            response = test_view(request)
            assert isinstance(response, HttpResponseForbidden)


class TestUsageBasedBilling:
    @pytest.fixture
    def billing(self):
        return UsageBasedBilling()

    @pytest.fixture
    def tiered_pricing(self, plan_feature):
        # Clear any existing tiers first
        PricingTier.objects.filter(plan_feature=plan_feature).delete()

        tiers = [
            baker.make(
                PricingTier,
                plan_feature=plan_feature,
                start_quantity=0,
                end_quantity=100,
                unit_price=Decimal("1.00"),
                flat_fee=Decimal("0"),
            ),
            baker.make(
                PricingTier,
                plan_feature=plan_feature,
                start_quantity=100,  # Changed from 101 to 100 to avoid gap
                end_quantity=1000,
                unit_price=Decimal("0.75"),
                flat_fee=Decimal("10"),
            ),
            baker.make(
                PricingTier,
                plan_feature=plan_feature,
                start_quantity=1000,  # Changed from 1001 to 1000 to avoid gap
                end_quantity=None,
                unit_price=Decimal("0.50"),
                flat_fee=Decimal("20"),
            ),
        ]
        return tiers

    def test_flat_rate_usage_billing(
        self, billing, user_subscription, feature, plan_feature
    ):
        """Test flat-rate billing for USAGE type features (pay-as-you-go)."""
        feature.feature_type = FeatureType.USAGE.value
        feature.pricing_model = PricingModel.FLAT.value
        feature.save()

        plan_feature.overage_rate = Decimal("0.50")
        plan_feature.save()

        # Test usage-based billing (should charge for all usage)
        charges = billing.calculate_charges(user_subscription, feature.code, 50)
        assert charges["total"] == Decimal("25.00")  # 50 * 0.50

        charges = billing.calculate_charges(user_subscription, feature.code, 150)
        assert charges["total"] == Decimal("75.00")  # 150 * 0.50

    def test_flat_rate_quota_billing(
        self, billing, user_subscription, feature, plan_feature
    ):
        """Test flat-rate billing for QUOTA type features (with free quota)."""
        feature.feature_type = FeatureType.QUOTA.value
        feature.pricing_model = PricingModel.FLAT.value
        feature.save()

        plan_feature.quota = 100
        plan_feature.overage_rate = Decimal("0.50")
        plan_feature.save()

        # Test within quota (should be free)
        charges = billing.calculate_charges(user_subscription, feature.code, 50)
        assert charges["total"] == Decimal("0")

        # Test overage (should only charge for usage beyond quota)
        charges = billing.calculate_charges(user_subscription, feature.code, 150)
        assert charges["total"] == Decimal("25.00")  # (150 - 100) * 0.50

    def test_tiered_billing(
        self, billing, user_subscription, feature, plan_feature, tiered_pricing
    ):
        """Test tiered billing calculations."""
        feature.pricing_model = PricingModel.TIERED.value
        feature.save()

        charges = billing.calculate_charges(user_subscription, feature.code, 150)

        # Detailed calculation:
        # First tier (0-100): 100 units * $1.00 = $100.00
        # Second tier (100-150): 50 units * $0.75 + $10 flat fee = $47.50
        # Total: $147.5
        assert charges["total"] == Decimal("147.5")
        assert len(charges["tiers"]) == 2

    def test_volume_billing(
        self, billing, user_subscription, feature, plan_feature, tiered_pricing
    ):
        """Test volume-based billing calculations."""
        feature.pricing_model = PricingModel.VOLUME.value
        feature.save()

        charges = billing.calculate_charges(user_subscription, feature.code, 150)
        assert charges["total"] == Decimal("122.50")  # (150 * 0.75 + 10)

    def test_package_billing(self, billing, user_subscription, feature, plan_feature):
        """Test package-based billing calculations."""
        feature.pricing_model = PricingModel.PACKAGE.value
        feature.save()

        charges = billing.calculate_charges(user_subscription, feature.code, 250)
        assert charges["packages"] == 3  # ceil(250/100)
        assert charges["total"] == Decimal("1.50")  # 3 * 0.50


class TestProcessSubscriptionPayment:

    def test_feature_based_plan(
        self, user_subscription, wallet, plan_manager, plan_feature
    ):
        feature_usage = baker.make(
            "subscription.FeatureUsage",
            subscription=user_subscription,
            feature=plan_feature.feature,
            quantity=5,
        )
        plan_manager._process_subscription_payment(user_subscription)
        wallet.refresh_from_db()
        assert wallet.balance == Decimal("97.5")  # 100 - 2.5 billing features
        feature_usage.refresh_from_db()
        assert feature_usage.quantity == 0  # Reset usage after payment

    def test_non_feature_based_plan(
        self, non_feature_user_subscription, wallet, plan_manager
    ):
        non_feature_user_subscription.subscription.plan.is_feature_based = False
        non_feature_user_subscription.subscription.plan.save()
        plan_manager._process_subscription_payment(non_feature_user_subscription)

        wallet.refresh_from_db()

        assert wallet.balance == Decimal("75.00")  # 100 - 25 Only base plan cost
