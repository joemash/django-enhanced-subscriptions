from datetime import timedelta
from decimal import Decimal
from unittest.mock import MagicMock

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone
from model_bakery import baker

from subscription.error_handling import (
    ErrorHandler,
    RetryStrategy,
    SubscriptionErrorLog,
    SubscriptionErrorType,
)
from subscription.models.plan import PlanCost, SubscriptionPlan, UserSubscription
from subscription.models.wallet import TransactionStatus, Wallet


@pytest.fixture
def subscription_setup():
    """Create basic subscription setup for testing."""
    plan = baker.make(SubscriptionPlan, plan_name="Test Plan", grace_period=3)
    plan_cost = baker.make(
        PlanCost,
        plan=plan,
        cost=Decimal("10.00"),
        recurrence_period=1,
        recurrence_unit="month",
    )
    user = baker.make("auth.User")
    wallet = baker.make(Wallet, user=user, balance=Decimal("100.00"))
    subscription = baker.make(
        UserSubscription,
        user=user,
        subscription=plan_cost,
        active=True,
        cancelled=False,
        date_billing_start=timezone.now(),
        date_billing_next=timezone.now() + timedelta(days=30),
    )
    return {
        "plan": plan,
        "plan_cost": plan_cost,
        "user": user,
        "wallet": wallet,
        "subscription": subscription,
    }


@pytest.fixture
def error_handler():
    """Create ErrorHandler with mocked manager."""
    mock_manager = MagicMock()
    return ErrorHandler(mock_manager)


@pytest.mark.django_db
class TestErrorHandler:
    def test_determine_retry_strategy(self, error_handler):
        """Test retry strategy determination for different error types."""
        # Test insufficient funds case
        error = ValidationError("insufficient_funds")
        assert (
            error_handler._determine_retry_strategy(error) == RetryStrategy.MANUAL.value
        )

        # Test validation error case
        error = ValidationError("invalid_input")
        assert (
            error_handler._determine_retry_strategy(error)
            == RetryStrategy.FIXED_INTERVAL.value
        )

        # Test timeout error case
        error = Exception("Connection timeout")
        assert (
            error_handler._determine_retry_strategy(error)
            == RetryStrategy.IMMEDIATE.value
        )

        # Test default case
        error = Exception("Generic error")
        assert (
            error_handler._determine_retry_strategy(error)
            == RetryStrategy.EXPONENTIAL_BACKOFF.value
        )

    def test_handle_payment_error_insufficient_funds(
        self, error_handler, subscription_setup
    ):
        """Test handling of insufficient funds error."""
        error = ValidationError("insufficient_funds")
        subscription = subscription_setup["subscription"]

        error_handler.handle_payment_error(subscription, error)

        error_log = SubscriptionErrorLog.objects.get(subscription_id=subscription.id)
        assert error_log.error_type == SubscriptionErrorType.INSUFFICIENT_FUNDS.value
        assert error_log.retry_strategy == RetryStrategy.MANUAL.value
        assert error_log.resolved is False
        assert (
            error_log.next_retry_time is None
        )  # Manual strategy should have no retry time

        error_handler.manager.notify_insufficient_funds.assert_called_once_with(
            subscription, required_amount=subscription.subscription.cost
        )

    def test_handle_payment_error_with_retry_strategies(
        self, error_handler, subscription_setup
    ):
        """Test handling of payment errors with different retry strategies."""
        subscription = subscription_setup["subscription"]

        # Test immediate retry
        error = Exception("Connection timeout")
        error_handler.handle_payment_error(subscription, error)
        error_log = SubscriptionErrorLog.objects.last()
        assert error_log.retry_strategy == RetryStrategy.IMMEDIATE.value
        assert error_log.next_retry_time <= timezone.now() + timedelta(seconds=31)

    def test_manual_retry(self, error_handler, subscription_setup):
        """Test manual retry functionality."""
        subscription = subscription_setup["subscription"]
        error_log = baker.make(
            SubscriptionErrorLog,
            error_type=SubscriptionErrorType.INSUFFICIENT_FUNDS.value,
            subscription_id=subscription.id,
            user_id=subscription.user.id,
            retry_strategy=RetryStrategy.MANUAL.value,
            retry_count=0,
        )

        # Mock successful payment
        mock_transaction = MagicMock()
        mock_transaction.status = TransactionStatus.SUCCESS.value
        error_handler.manager.process_payment.return_value = mock_transaction

        # Test manual retry
        error_handler.manual_retry(error_log)

        error_log.refresh_from_db()
        assert error_log.retry_count == 0
        assert error_log.resolved is False
        assert error_log.resolution_timestamp is None


@pytest.mark.django_db
class TestRetryOperation:
    @pytest.fixture
    def retry_setup(self, subscription_setup):
        """Setup for retry operation tests."""
        error_log = baker.make(
            SubscriptionErrorLog,
            error_type=SubscriptionErrorType.PAYMENT_FAILED.value,
            subscription_id=subscription_setup["subscription"].id,
            user_id=subscription_setup["user"].id,
            retry_count=0,
            resolved=False,
            retry_strategy=RetryStrategy.EXPONENTIAL_BACKOFF.value,
        )
        return {**subscription_setup, "error_log": error_log}

    def test_retry_respects_strategy(self, error_handler, subscription_setup):
        """Test that retry operation respects retry strategy."""
        # Test manual strategy not auto-retried
        manual_error = baker.make(
            SubscriptionErrorLog,
            error_type=SubscriptionErrorType.INSUFFICIENT_FUNDS.value,
            subscription_id=subscription_setup["subscription"].id,
            retry_strategy=RetryStrategy.MANUAL.value,
        )
        error_handler.retry_failed_operation(manual_error)
        manual_error.refresh_from_db()
        assert manual_error.retry_count == 0  # Should not increment for manual strategy

        # Test automatic strategies are retried
        auto_error = baker.make(
            SubscriptionErrorLog,
            error_type=SubscriptionErrorType.PAYMENT_FAILED.value,
            subscription_id=subscription_setup["subscription"].id,
            retry_strategy=RetryStrategy.EXPONENTIAL_BACKOFF.value,
        )
        error_handler.retry_failed_operation(auto_error)
        auto_error.refresh_from_db()
        assert auto_error.retry_count == 1  # Should increment for automatic strategies
