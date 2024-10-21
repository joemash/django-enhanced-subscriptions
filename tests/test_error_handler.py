from datetime import timedelta
from decimal import Decimal
from unittest.mock import MagicMock

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone
from model_bakery import baker

from subscription.error_handling import (
    ErrorHandler,
    RetryManager,
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

        # Verify manager notification was called
        error_handler.manager.notify_insufficient_funds.assert_called_once_with(
            subscription, required_amount=subscription.subscription.cost
        )

    def test_handle_payment_error_with_retry(self, error_handler, subscription_setup):
        """Test handling of payment error with retry scheduling."""
        error = Exception("Payment processing failed")
        subscription = subscription_setup["subscription"]

        error_handler.handle_payment_error(subscription, error)

        error_log = SubscriptionErrorLog.objects.get(subscription_id=subscription.id)
        assert error_log.error_type == SubscriptionErrorType.PAYMENT_FAILED.value
        assert error_log.retry_strategy == RetryStrategy.EXPONENTIAL_BACKOFF.value
        assert error_log.retry_count == 0
        assert error_log.next_retry_time > timezone.now()

    def test_max_retries_exceeded(self, error_handler, subscription_setup):
        """Test behavior when max retries are exceeded."""
        subscription = subscription_setup["subscription"]
        error_log = baker.make(
            SubscriptionErrorLog,
            error_type=SubscriptionErrorType.PAYMENT_FAILED.value,
            subscription_id=subscription.id,
            user_id=subscription.user.id,
            retry_count=ErrorHandler.MAX_RETRY_ATTEMPTS,
        )

        error_handler._handle_max_retries_exceeded(subscription, error_log)

        # Verify subscription was cancelled
        subscription.refresh_from_db()
        assert subscription.cancelled is True
        assert subscription.active is False

        # Verify error log was updated
        error_log.refresh_from_db()
        assert error_log.retry_strategy == RetryStrategy.MANUAL.value

        # Verify notification was sent
        error_handler.manager.notify_subscription_cancelled.assert_called_once_with(
            subscription, reason="payment_failure_max_retries"
        )


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
        )
        return {**subscription_setup, "error_log": error_log}

    def test_successful_payment_retry(self, error_handler, retry_setup):
        """Test successful payment retry operation."""
        # Mock successful payment
        mock_transaction = MagicMock()
        mock_transaction.status = TransactionStatus.SUCCESS.value
        error_handler.manager.process_payment.return_value = mock_transaction

        error_handler.retry_failed_operation(retry_setup["error_log"])

        # Verify error log was updated
        retry_setup["error_log"].refresh_from_db()
        assert retry_setup["error_log"].resolved is True
        assert retry_setup["error_log"].resolution_timestamp is not None
        assert retry_setup["error_log"].retry_count == 1

    def test_failed_payment_retry(self, error_handler, retry_setup):
        """Test failed payment retry operation."""
        # Mock failed payment
        error_handler.manager.process_payment.return_value = None

        error_handler.retry_failed_operation(retry_setup["error_log"])

        # Verify error log was updated
        retry_setup["error_log"].refresh_from_db()
        assert retry_setup["error_log"].resolved is False
        assert retry_setup["error_log"].retry_count == 1
        assert retry_setup["error_log"].next_retry_time > timezone.now()


@pytest.mark.django_db
class TestRetryManager:
    def test_process_pending_retries(self):
        """Test processing of pending retries."""
        # Create test data
        current_time = timezone.now()
        subscription = baker.make(UserSubscription)

        # Create error logs with different next_retry_times
        past_retry = baker.make(
            SubscriptionErrorLog,
            error_type=SubscriptionErrorType.PAYMENT_FAILED.value,
            subscription_id=subscription.id,
            next_retry_time=current_time - timedelta(minutes=5),
            resolved=False,
        )
        baker.make(
            SubscriptionErrorLog,
            error_type=SubscriptionErrorType.PAYMENT_FAILED.value,
            subscription_id=subscription.id,
            next_retry_time=current_time + timedelta(minutes=5),
            resolved=False,
        )

        # Mock error handler
        mock_error_handler = MagicMock()
        retry_manager = RetryManager()
        retry_manager.error_handler = mock_error_handler

        # Process retries
        retry_manager.process_pending_retries()

        # Verify only past retry was processed
        mock_error_handler.retry_failed_operation.assert_called_once_with(past_retry)

    def test_failed_subscriptions_report(self):
        """Test generation of failed subscriptions report."""
        subscription = baker.make(UserSubscription)
        # Create multiple error logs with different types
        baker.make(
            SubscriptionErrorLog,
            error_type=SubscriptionErrorType.PAYMENT_FAILED.value,
            subscription_id=subscription.id,
            resolved=False,
            _quantity=3,
        )
        baker.make(
            SubscriptionErrorLog,
            error_type=SubscriptionErrorType.INSUFFICIENT_FUNDS.value,
            subscription_id=subscription.id,
            resolved=False,
            _quantity=2,
        )

        retry_manager = RetryManager()
        report = retry_manager.get_failed_subscriptions_report()

        assert len(report) == 2
        payment_failed = next(
            r
            for r in report
            if r["error_type"] == SubscriptionErrorType.PAYMENT_FAILED.value
        )
        insufficient_funds = next(
            r
            for r in report
            if r["error_type"] == SubscriptionErrorType.INSUFFICIENT_FUNDS.value
        )

        assert payment_failed["count"] == 3
        assert insufficient_funds["count"] == 2
