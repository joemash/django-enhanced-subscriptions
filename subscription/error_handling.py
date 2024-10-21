from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.utils import timezone

from subscription.models.error import (
    RetryStrategy,
    SubscriptionErrorLog,
    SubscriptionErrorType,
)
from subscription.models.plan import UserSubscription
from subscription.models.wallet import TransactionStatus
from subscription.settings import CONFIG


class ErrorHandler:
    """Handles subscription system errors and recovery strategies."""

    MAX_RETRY_ATTEMPTS = CONFIG["MAX_RETRY_ATTEMPTS"]
    BASE_RETRY_DELAY = CONFIG["BASE_RETRY_DELAY_SECONDS"]
    FIXED_INTERVAL_DELAY = CONFIG["FIXED_INTERVAL_DELAY"]

    def __init__(self, manager):
        self.manager = manager

    def handle_payment_error(self, subscription, error):
        """Handle payment processing errors with retry logic."""
        # Create error log with appropriate type and strategy
        error_type = (
            SubscriptionErrorType.INSUFFICIENT_FUNDS.value
            if isinstance(error, ValidationError) and "insufficient_funds" in str(error)
            else SubscriptionErrorType.PAYMENT_FAILED.value
        )

        retry_strategy = self._determine_retry_strategy(error)

        error_log = SubscriptionErrorLog.objects.create(
            error_type=error_type,
            subscription_id=subscription.id,
            user_id=subscription.user.id,
            retry_strategy=retry_strategy,
            details={
                "error": str(error),
                "subscription_plan": str(subscription.subscription.plan.plan_name),
                "amount": str(subscription.subscription.cost),
            },
        )

        # Schedule retry based on strategy
        self._schedule_retry(subscription, error_log)

        # Notify user if insufficient funds
        if error_type == SubscriptionErrorType.INSUFFICIENT_FUNDS.value:
            self.manager.notify_insufficient_funds(
                subscription, required_amount=subscription.subscription.cost
            )

    def _determine_retry_strategy(self, error):
        """Determine the appropriate retry strategy based on error type."""
        if isinstance(error, ValidationError):
            if "insufficient_funds" in str(error):
                return RetryStrategy.MANUAL.value
            return RetryStrategy.FIXED_INTERVAL.value
        elif "timeout" in str(error).lower():
            return RetryStrategy.IMMEDIATE.value
        else:
            return RetryStrategy.EXPONENTIAL_BACKOFF.value

    def _schedule_retry(self, subscription, error_log):
        """Schedule retry based on the selected retry strategy."""
        if error_log.retry_count >= self.MAX_RETRY_ATTEMPTS:
            self._handle_max_retries_exceeded(subscription, error_log)
            return

        next_retry = timezone.now()

        if error_log.retry_strategy == RetryStrategy.IMMEDIATE.value:
            # Immediate retry - add small buffer to prevent overwhelming
            next_retry += timezone.timedelta(seconds=30)

        elif error_log.retry_strategy == RetryStrategy.EXPONENTIAL_BACKOFF.value:
            delay = self.BASE_RETRY_DELAY * (2**error_log.retry_count)
            next_retry += timezone.timedelta(seconds=delay)

        elif error_log.retry_strategy == RetryStrategy.FIXED_INTERVAL.value:
            next_retry += timezone.timedelta(seconds=self.FIXED_INTERVAL_DELAY)

        elif error_log.retry_strategy == RetryStrategy.MANUAL.value:
            next_retry = None  # No automatic retry for manual strategy

        error_log.next_retry_time = next_retry
        error_log.save()

    def handle_refund_error(self, subscription, original_transaction, error):
        """Handle refund processing errors."""
        # Refunds typically need immediate attention
        error_log = SubscriptionErrorLog.objects.create(
            error_type=SubscriptionErrorType.REFUND_ERROR.value,
            subscription_id=subscription.id,
            user_id=subscription.user.id,
            retry_strategy=RetryStrategy.IMMEDIATE.value,
            details={
                "error": str(error),
                "original_transaction_id": str(original_transaction.id),
                "refund_amount": str(original_transaction.amount),
            },
        )
        self._schedule_retry(subscription, error_log)

    @transaction.atomic
    def retry_failed_operation(self, error_log):
        """Retry a failed operation based on error type and strategy."""
        if error_log.resolved or error_log.retry_count >= self.MAX_RETRY_ATTEMPTS:
            return

        # Don't retry manual strategy unless explicitly triggered
        if error_log.retry_strategy == RetryStrategy.MANUAL.value:
            return

        error_log.retry_count += 1

        try:
            if error_log.error_type in [
                SubscriptionErrorType.PAYMENT_FAILED.value,
                SubscriptionErrorType.INSUFFICIENT_FUNDS.value,
            ]:
                subscription = UserSubscription.objects.get(
                    id=error_log.subscription_id
                )
                payment_result = self.manager.process_payment(
                    subscription.user, subscription
                )

                if (
                    payment_result
                    and payment_result.status == TransactionStatus.SUCCESS.value
                ):
                    error_log.resolved = True
                    error_log.resolution_timestamp = timezone.now()
                else:
                    self._schedule_retry(subscription, error_log)

            elif error_log.error_type == SubscriptionErrorType.REFUND_ERROR.value:
                # Handle refund retry logic
                pass

        except Exception as e:
            error_log.details["retry_error"] = str(e)
            self._schedule_retry(subscription, error_log)

        error_log.save()

    def manual_retry(self, error_log):
        """Explicitly trigger retry for manual strategy errors."""
        if error_log.retry_strategy == RetryStrategy.MANUAL.value:
            self.retry_failed_operation(error_log)

    def _handle_max_retries_exceeded(self, subscription, error_log):
        """Handle cases where maximum retries have been exceeded."""
        error_log.retry_strategy = RetryStrategy.MANUAL.value
        error_log.save()

        # Cancel subscription if max retries exceeded
        if not subscription.cancelled:
            subscription.active = False
            subscription.cancelled = True
            subscription.save()

            self.manager.notify_subscription_cancelled(
                subscription, reason="payment_failure_max_retries"
            )


class RetryManager:
    """Manages retry operations for failed subscription operations."""

    def __init__(self):
        self.error_handler = ErrorHandler(self)

    def process_pending_retries(self):
        """Process all pending retry operations."""
        pending_errors = SubscriptionErrorLog.objects.filter(
            resolved=False, next_retry_time__lte=timezone.now()
        ).select_for_update()

        for error in pending_errors:
            self.error_handler.retry_failed_operation(error)

    def get_failed_subscriptions_report(self):
        """Generate report of failed subscriptions."""
        return (
            SubscriptionErrorLog.objects.filter(resolved=False)
            .values("error_type")
            .annotate(
                count=models.Count("id"),
                oldest_error=models.Min("timestamp"),
                retry_attempts=models.Avg("retry_count"),
            )
        )
