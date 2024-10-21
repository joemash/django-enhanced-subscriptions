from enum import Enum
from uuid import uuid4

from django.db import models


class SubscriptionErrorType(Enum):
    PAYMENT_FAILED = "payment_failed"
    INSUFFICIENT_FUNDS = "insufficient_funds"
    VALIDATION_ERROR = "validation_error"
    REFUND_ERROR = "refund_error"
    STATE_TRANSITION_ERROR = "state_transition_error"
    SYSTEM_ERROR = "system_error"


class RetryStrategy(Enum):
    IMMEDIATE = "immediate"
    EXPONENTIAL_BACKOFF = "exponential_backoff"
    FIXED_INTERVAL = "fixed_interval"
    MANUAL = "manual"


class SubscriptionErrorLog(models.Model):
    """Logs all subscription-related errors for auditing and recovery."""

    id = models.UUIDField(
        default=uuid4,
        editable=False,
        primary_key=True,
        verbose_name="ID",
    )
    error_type = models.CharField(
        max_length=50, choices=[(t.value, t.name) for t in SubscriptionErrorType]
    )
    subscription_id = models.UUIDField()
    user_id = models.UUIDField()
    timestamp = models.DateTimeField(auto_now_add=True)
    details = models.JSONField()
    retry_count = models.IntegerField(default=0)
    resolved = models.BooleanField(default=False)
    resolution_timestamp = models.DateTimeField(null=True)
    retry_strategy = models.CharField(
        max_length=50,
        choices=[(s.value, s.name) for s in RetryStrategy],
        default=RetryStrategy.EXPONENTIAL_BACKOFF.value,
    )
    next_retry_time = models.DateTimeField(null=True)

    class Meta:
        indexes = [
            models.Index(fields=["subscription_id", "resolved"]),
            models.Index(fields=["error_type", "resolved"]),
            models.Index(fields=["next_retry_time"]),
        ]
