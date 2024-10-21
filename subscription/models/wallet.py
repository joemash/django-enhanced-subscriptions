from datetime import timedelta
from decimal import Decimal
from enum import Enum
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _


class TransactionType(Enum):
    """Enum for different types of wallet transactions."""

    DEPOSIT = "deposit"
    SUBSCRIPTION = "subscription"
    REFUND = "refund"
    CANCELLATION = "cancellation"


class TransactionStatus(Enum):
    """Enum for transaction status."""

    SUCCESS = "success"
    FAILED = "failed"
    PENDING = "pending"
    REFUNDED = "refunded"
    CANCELLED = "cancelled"


class RefundReason(Enum):
    """Enum for refund reasons."""

    CANCELLATION = "cancellation"
    SERVICE_ISSUE = "service_issue"
    BILLING_ERROR = "billing_error"
    CUSTOMER_REQUEST = "customer_request"
    OTHER = "other"


class WalletManager(models.Manager):
    """Manager class for Wallet model with business logic."""

    def get_or_create_wallet(self, user):
        """Get or create a wallet for the given user."""
        wallet, _ = self.get_or_create(user=user, defaults={"balance": Decimal("0.00")})
        return wallet


class Wallet(models.Model):
    """User's wallet for managing subscription payments and credits."""

    id = models.UUIDField(
        default=uuid4,
        editable=False,
        primary_key=True,
        verbose_name="ID",
    )
    user = models.OneToOneField(
        get_user_model(),
        on_delete=models.CASCADE,
        related_name="wallet",
        help_text=_("The user who owns this wallet"),
    )
    balance = models.DecimalField(
        max_digits=19,
        decimal_places=4,
        default=Decimal("0.0000"),
        help_text=_("Current balance in the wallet"),
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = WalletManager()

    class Meta:
        ordering = ("-created_at",)

    def __str__(self):
        return f"Wallet for {self.user.username} - Balance: {self.balance}"

    @transaction.atomic
    def deposit(self, amount: Decimal, description: str = ""):
        """Add funds to the wallet."""
        if amount <= 0:
            raise ValidationError(_("Deposit amount must be positive"))

        self.balance += amount
        self.save()

        return WalletTransaction.objects.create(
            wallet=self,
            amount=amount,
            transaction_type=TransactionType.DEPOSIT.value,
            status=TransactionStatus.SUCCESS.value,
            description=description or "Wallet deposit",
            balance_after=self.balance,
        )

    @transaction.atomic
    def process_subscription_payment(self, amount, plan_cost, description):
        """Process a subscription payment."""
        if amount <= 0:
            raise ValidationError(_("Payment amount must be positive"))

        if self.balance < amount:
            raise ValidationError(_("Insufficient funds in wallet"))

        self.balance -= amount
        self.save()

        return WalletTransaction.objects.create(
            wallet=self,
            amount=amount,
            transaction_type=TransactionType.SUBSCRIPTION.value,
            status=TransactionStatus.SUCCESS.value,
            description=description,
            balance_after=self.balance,
            subscription_details={
                "plan_id": str(plan_cost.plan.id),
                "plan_name": plan_cost.plan.plan_name,
                "billing_period": plan_cost.recurrence_period,
                "billing_unit": plan_cost.recurrence_unit,
            },
        )

    @transaction.atomic
    def process_refund(
        self,
        original_transaction: "WalletTransaction",
        amount: Decimal = None,
        reason: RefundReason = RefundReason.OTHER,
        description: str = "",
    ):
        """
        Process a refund for a previous transaction.

        Args:
            original_transaction: The transaction to refund
            amount: Refund amount (if None, full amount is refunded)
            reason: Reason for the refund
            description: Additional description
        """
        if original_transaction.wallet != self:
            raise ValidationError(_("Transaction does not belong to this wallet"))

        if original_transaction.status == TransactionStatus.REFUNDED.value:
            raise ValidationError(_("Transaction has already been refunded"))

        refund_amount = amount or original_transaction.amount
        if refund_amount > original_transaction.amount:
            raise ValidationError(
                _("Refund amount cannot exceed original transaction amount")
            )

        # Process the refund
        self.balance += refund_amount
        self.save()

        # Create refund transaction
        refund_transaction = WalletTransaction.objects.create(
            wallet=self,
            amount=refund_amount,
            transaction_type=TransactionType.REFUND.value,
            status=TransactionStatus.SUCCESS.value,
            description=description
            or f"Refund for transaction {original_transaction.id}",
            balance_after=self.balance,
            related_transaction=original_transaction,
            refund_details={
                "original_transaction_id": str(original_transaction.id),
                "refund_reason": reason.value,
                "full_refund": refund_amount == original_transaction.amount,
            },
        )

        # Update original transaction status
        original_transaction.status = TransactionStatus.REFUNDED.value
        original_transaction.save()

        return refund_transaction

    @transaction.atomic
    def process_subscription_cancellation(
        self, subscription, prorate: bool = True, description: str = ""
    ) -> "WalletTransaction":
        """
        Process a subscription cancellation and handle any refunds.

        Args:
            subscription: The subscription being cancelled
            prorate: Whether to prorate the refund based on unused time
            description: Additional description
        """
        # Find the last subscription payment
        if not subscription:
            return

        last_payment = (
            self.transactions.filter(
                transaction_type=TransactionType.SUBSCRIPTION.value,
                subscription_details__plan_id=str(subscription.subscription.plan.id),
            )
            .order_by("-created_at")
            .first()
        )

        if not last_payment:
            return None

        if prorate:
            # Calculate prorated refund amount
            billing_period = subscription.subscription.recurrence_period
            billing_unit = subscription.subscription.recurrence_unit

            # Calculate the unused time portion
            if billing_unit in ["month", "year"]:
                total_days = 30.4368 if billing_unit == "month" else 365.2425
                total_days *= billing_period

                days_used = (timezone.now() - last_payment.created_at).days
                days_remaining = max(0, total_days - days_used)

                refund_amount = (
                    Decimal(days_remaining) / Decimal(total_days)
                ) * last_payment.amount
            else:
                # For other billing units, calculate based on exact time difference
                total_seconds = (
                    subscription.subscription.next_billing_datetime(
                        last_payment.created_at
                    )
                    - last_payment.created_at
                )
                seconds_used = timezone.now() - last_payment.created_at
                seconds_remaining = max(timedelta(0), total_seconds - seconds_used)

                refund_amount = (
                    Decimal(seconds_remaining.total_seconds())
                    / Decimal(total_seconds.total_seconds())
                ) * last_payment.amount
        else:
            refund_amount = last_payment.amount

        # Process the refund
        return self.process_refund(
            original_transaction=last_payment,
            amount=refund_amount.quantize(Decimal("0.0001")),
            reason=RefundReason.CANCELLATION,
            description=description
            or f"Cancellation refund for {subscription.subscription.plan.plan_name}",
        )

    def get_statement(self, start_date=None, end_date=None):
        """Get wallet statement for the specified period."""
        transactions = self.transactions.all()

        if start_date:
            transactions = transactions.filter(created_at__gte=start_date)
        if end_date:
            transactions = transactions.filter(created_at__lte=end_date)

        return transactions.order_by("created_at")


class WalletTransaction(models.Model):
    """Records all transactions including subscription payments and refunds."""

    id = models.UUIDField(
        default=uuid4,
        editable=False,
        primary_key=True,
        verbose_name="ID",
    )
    wallet = models.ForeignKey(
        Wallet,
        on_delete=models.CASCADE,
        related_name="transactions",
        help_text=_("The wallet this transaction belongs to"),
    )
    amount = models.DecimalField(
        max_digits=19, decimal_places=4, help_text=_("Transaction amount")
    )
    transaction_type = models.CharField(
        max_length=20,
        choices=[(t.value, t.name) for t in TransactionType],
        help_text=_("Type of transaction"),
    )
    status = models.CharField(
        max_length=20,
        choices=[(s.value, s.name) for s in TransactionStatus],
        help_text=_("Status of the transaction"),
    )
    balance_after = models.DecimalField(
        max_digits=19,
        decimal_places=4,
        help_text=_("Wallet balance after this transaction"),
    )
    description = models.CharField(
        max_length=255, blank=True, help_text=_("Description of the transaction")
    )
    subscription_details = models.JSONField(
        null=True,
        blank=True,
        help_text=_("Additional details for subscription transactions"),
    )
    refund_details = models.JSONField(
        null=True, blank=True, help_text=_("Additional details for refund transactions")
    )
    related_transaction = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="refunds",
        help_text=_("Related transaction (for refunds)"),
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at",)
