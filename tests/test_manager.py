from datetime import timedelta
from decimal import Decimal
from unittest.mock import Mock, patch

import pytest
from django.utils import timezone
from model_bakery import baker

from subscription.manager import BillingBreakdown, BillingType, PlanManager
from subscription.models.feature import (
    Feature,
    FeatureType,
    FeatureUsage,
    PlanFeature,
    PricingModel,
)
from subscription.models.plan import MONTH, PlanCost, SubscriptionPlan, UserSubscription
from subscription.models.wallet import TransactionStatus, TransactionType
from tests.factories import plan_cost_recipe, user_subscription_recipe, wallet_recipe


@pytest.fixture
def subscription_plan():
    return baker.make(
        SubscriptionPlan, plan_name="Test Plan", is_feature_based=False, grace_period=7
    )


@pytest.fixture
def plan_cost(subscription_plan):
    return baker.make(
        PlanCost,
        plan=subscription_plan,
        recurrence_period=1,
        recurrence_unit=MONTH,
        cost=Decimal("10.00"),
    )


@pytest.fixture
def subscription(user, plan_cost):
    current = timezone.now()
    return baker.make(
        UserSubscription,
        user=user,
        subscription=plan_cost,
        date_billing_start=current,
        date_billing_next=current + timedelta(days=30),
        active=False,
        cancelled=False,
    )


@pytest.fixture
def feature():
    return baker.make(
        Feature,
        name="API Calls",
        code="api_calls",
        feature_type=FeatureType.USAGE.value,
        pricing_model=PricingModel.FLAT.value,
        unit="calls",
    )


@pytest.fixture
def plan_feature(subscription_plan, feature):
    return baker.make(
        PlanFeature,
        plan=subscription_plan,
        feature=feature,
        enabled=True,
        quota=1000,
        overage_rate=Decimal("0.01"),
    )


@pytest.fixture
def feature_usage(subscription, feature):
    return baker.make(
        FeatureUsage,
        subscription=subscription,
        feature=feature,
        quantity=500,
        last_reset=timezone.now(),
    )


class TestPlanManage:

    def test_initialization(self, plan_manager):
        assert plan_manager.error_handler is not None
        assert plan_manager.usage_billing is not None

    @pytest.mark.django_db
    def test_determine_billing_type_regular(self, plan_manager, subscription):
        billing_type = plan_manager._determine_billing_type(subscription)
        assert billing_type == BillingType.REGULAR

    @pytest.mark.django_db
    def test_determine_billing_type_feature_based(
        self, plan_manager, subscription, plan_feature
    ):
        subscription.subscription.cost = Decimal("0")
        subscription.subscription.save()

        # Create feature with usage type
        feature = baker.make(
            Feature,
            feature_type=FeatureType.USAGE.value,
            pricing_model=PricingModel.FLAT.value,
        )
        baker.make(
            PlanFeature,
            plan=subscription.subscription.plan,
            feature=feature,
            enabled=True,
        )

        billing_type = plan_manager._determine_billing_type(subscription)
        assert billing_type == BillingType.FEATURE_BASED

    @pytest.mark.django_db
    def test_determine_billing_type_hybrid(self, plan_manager, subscription):
        # Create feature with usage type
        feature = baker.make(
            Feature,
            feature_type=FeatureType.USAGE.value,
            pricing_model=PricingModel.FLAT.value,
        )
        baker.make(
            PlanFeature,
            plan=subscription.subscription.plan,
            feature=feature,
            enabled=True,
        )

        billing_type = plan_manager._determine_billing_type(subscription)
        assert billing_type == BillingType.HYBRID

    def test_process_new_subscription_success(self, user, wallet, user_subscription):
        """Test processing new subscription with sufficient funds."""
        manager = PlanManager()

        wallet.deposit(Decimal("100.00"))
        manager.process_new(user_subscription)

        user_subscription.refresh_from_db()
        assert user_subscription.active is True
        assert user_subscription.cancelled is False
        assert user_subscription.date_billing_last is not None
        assert user_subscription.date_billing_next is not None

    def test_batch_subscription_processing(self, user, wallet):
        """Test processing multiple subscriptions in batch."""
        manager = PlanManager()
        wallet.deposit(Decimal("100.00"))
        # Create subscriptions in different states
        subscriptions = {
            "expired": user_subscription_recipe.make(
                user=user,
                date_billing_end=timezone.now() - timedelta(days=1),
                active=True,
            ),
            "new": user_subscription_recipe.make(
                user=user, date_billing_start=timezone.now() - timedelta(days=1)
            ),
            "due": user_subscription_recipe.make(
                user=user,
                date_billing_next=timezone.now() - timedelta(days=1),
                active=True,
            ),
        }

        manager.process_subscriptions()

        # Refresh all subscriptions from db
        for key, sub in subscriptions.items():
            sub.refresh_from_db()

        assert subscriptions["expired"].active is False
        assert subscriptions["expired"].cancelled is True
        assert subscriptions["new"].active is True
        assert (
            subscriptions["due"].date_billing_next
            > subscriptions["due"].date_billing_last
        )

    def test_failed_payment_handling(self, user, wallet, user_subscription):
        """Test handling of failed payments."""
        manager = PlanManager()
        wallet.balance = Decimal("0.00")
        wallet.save()

        assert user_subscription.active is False
        # Mock notification method
        manager.notify_payment_error = Mock()

        # Process subscription with insufficient funds
        manager.process_due(user_subscription)

        user_subscription.refresh_from_db()
        assert (
            user_subscription.active is True
        )  # Should remain active until grace period
        assert manager.notify_payment_error.called

    @pytest.mark.parametrize(
        "billing_unit,period,expected_days",
        [
            ("week", 2, 14),
            ("month", 1, 30),
            ("year", 1, 365),
        ],
    )
    def test_prorated_refund_calculation(
        self, user, billing_unit, period, expected_days
    ):
        """Test prorated refund calculations for different billing periods."""
        wallet = wallet_recipe.make(user=user, balance=Decimal("100.00"))
        cost = plan_cost_recipe.make(
            recurrence_unit=billing_unit, recurrence_period=period
        )
        subscription = user_subscription_recipe.make(user=user, subscription=cost)

        payment = wallet.process_subscription_payment(
            Decimal("30.00"), subscription.subscription, description="refund"
        )

        with patch("django.utils.timezone.now") as mock_now:
            mock_now.return_value = payment.created_at + timedelta(days=1)
            refund = wallet.process_subscription_cancellation(
                subscription, prorate=True
            )

        expected_refund = round(
            Decimal("30.00") * (expected_days - 1) / expected_days, 4
        )
        assert abs(expected_refund) <= abs(refund.amount)
        assert refund.transaction_type == TransactionType.REFUND.value
        assert refund.status == TransactionStatus.SUCCESS.value


class TestSubscriptionProcessing:

    @pytest.mark.django_db
    def test_process_new_subscription_success(self, plan_manager, subscription, wallet):
        assert subscription.active is False
        plan_manager.process_new(subscription)
        subscription.refresh_from_db()
        assert subscription.active is True
        assert subscription.date_billing_last is not None
        assert subscription.date_billing_next is not None

    @pytest.mark.django_db
    def test_process_expired_subscription(self, plan_manager, subscription, wallet):
        plan_manager.process_expired(subscription)
        subscription.refresh_from_db()
        assert subscription.active is False
        assert subscription.cancelled is True

    @pytest.mark.django_db
    def test_process_due_subscription_success(self, plan_manager, subscription, wallet):
        subscription.active = True
        subscription.save()
        original_next_billing = subscription.date_billing_next
        plan_manager.process_due(subscription)
        subscription.refresh_from_db()
        assert subscription.date_billing_next > original_next_billing
        assert subscription.active is True


class TestFeatureCharges:

    @pytest.mark.django_db
    def test_calculate_feature_charges(
        self, plan_manager, subscription, feature, plan_feature
    ):
        usage = baker.make(
            FeatureUsage, subscription=subscription, feature=feature, quantity=500
        )

        charges = plan_manager._calculate_feature_charges(subscription)
        assert len(charges) == 1
        assert charges[0]["feature"] == usage.feature
        assert charges[0]["usage"] == usage.quantity
        assert charges[0]["charges"]["total"] == Decimal("5.00")

    @pytest.mark.django_db
    def test_calculate_billing_breakdown_hybrid(
        self, plan_manager, subscription, feature, plan_feature
    ):
        # Create usage record
        usage = baker.make(
            FeatureUsage, subscription=subscription, feature=feature, quantity=500
        )

        breakdown = plan_manager._calculate_billing_breakdown(subscription)

        assert breakdown.billing_type == BillingType.HYBRID
        charges = breakdown.feature_charges
        assert charges[0]["feature"] == usage.feature
        assert charges[0]["usage"] == usage.quantity
        assert charges[0]["charges"]["total"] == Decimal("5.00")
        assert breakdown.base_cost == Decimal("10")
        assert breakdown.total_amount == Decimal(breakdown.base_cost) + Decimal(
            charges[0]["charges"]["total"]
        )


class TestErrorHandling:

    @pytest.mark.django_db
    def test_process_new_subscription_insufficient_funds(
        self, plan_manager, subscription, wallet
    ):
        assert subscription.active is False
        wallet.balance = Decimal("0.00")
        wallet.save()
        plan_manager.process_new(subscription)
        subscription.refresh_from_db()
        assert subscription.active is False

    @pytest.mark.django_db
    def test_process_expired_refund_error(self, plan_manager, subscription, wallet):
        plan_manager.process_expired(subscription)
        subscription.refresh_from_db()
        assert subscription.active is False
        assert subscription.cancelled is True


class TestGracePeriod:

    @pytest.mark.django_db
    def test_handle_failed_renewal_with_grace_period(self, plan_manager, subscription):
        original_next_billing = subscription.date_billing_next
        plan_manager._handle_failed_renewal(subscription)
        subscription.refresh_from_db()
        assert subscription.active is True
        assert subscription.date_billing_next > original_next_billing
        assert subscription.date_billing_next > subscription.date_billing_last

    @pytest.mark.django_db
    def test_handle_failed_renewal_no_grace_period(self, plan_manager, subscription):
        subscription.subscription.plan.grace_period = 0
        subscription.subscription.plan.save()
        original_next_billing = subscription.date_billing_next

        plan_manager._handle_failed_renewal(subscription)

        subscription.refresh_from_db()
        assert subscription.date_billing_next == original_next_billing


class TestUsageReset:

    @pytest.mark.django_db
    def test_reset_usage_counters(self, plan_manager, subscription, feature):
        usage = baker.make(
            FeatureUsage, subscription=subscription, feature=feature, quantity=500
        )

        plan_manager._reset_usage_counters(subscription)

        usage.refresh_from_db()
        assert usage.quantity == 0
        assert usage.last_reset is not None


class TestPaymentDescriptions:

    def test_generate_payment_description_regular(self, plan_manager, subscription):
        breakdown = BillingBreakdown(
            base_cost=Decimal("10.00"),
            feature_charges=[],
            total_amount=Decimal("10.00"),
            billing_type=BillingType.REGULAR,
        )

        description = plan_manager._generate_payment_description(
            subscription, breakdown
        )

        assert "Base plan cost: $10.00" in description
        assert "Total amount: $10.00" in description

    def test_generate_payment_description_hybrid(
        self, plan_manager, subscription, feature
    ):
        breakdown = BillingBreakdown(
            base_cost=Decimal("10.00"),
            feature_charges=[
                {
                    "feature": feature,
                    "usage": 500,
                    "charges": {"total": Decimal("5.00")},
                }
            ],
            total_amount=Decimal("15.00"),
            billing_type=BillingType.HYBRID,
        )

        description = plan_manager._generate_payment_description(
            subscription, breakdown
        )

        assert "Base plan cost: $10.00" in description
        assert "API Calls: 500 units = $5.00" in description
        assert "Total amount: $15.00" in description
