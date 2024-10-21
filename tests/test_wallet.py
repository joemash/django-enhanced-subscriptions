from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.utils import timezone
from model_bakery import baker

from subscription.models.plan import UserSubscription
from subscription.models.wallet import (
    RefundReason,
    TransactionStatus,
    TransactionType,
    WalletTransaction,
)
from tests.factories import plan_cost_recipe, wallet_recipe

User = get_user_model()


class TestWallet:
    def test_wallet_creation(self, user):
        """Test wallet creation with initial balance."""
        wallet = wallet_recipe.make(user=user, balance=Decimal("50.00"))
        assert wallet.balance == Decimal("50.00")
        assert str(wallet) == f"Wallet for {user.username} - Balance: 50.00"

    def test_deposit(self, wallet):
        """Test depositing money into wallet."""
        initial_balance = wallet.balance
        amount = Decimal("25.00")

        transaction = wallet.deposit(amount, "Test deposit")

        assert transaction.transaction_type == TransactionType.DEPOSIT.value
        assert transaction.status == TransactionStatus.SUCCESS.value
        assert transaction.amount == amount
        assert wallet.balance == initial_balance + amount

    def test_deposit_negative_amount(self, wallet):
        """Test that depositing negative amount raises error."""
        with pytest.raises(ValidationError):
            wallet.deposit(Decimal("-10.00"))

    def test_process_subscription_payment(self, wallet, user_subscription):
        """Test processing subscription payment."""
        initial_balance = wallet.balance
        amount = Decimal("10.00")

        transaction = wallet.process_subscription_payment(
            amount, user_subscription.subscription, "subscription"
        )

        assert transaction.transaction_type == TransactionType.SUBSCRIPTION.value
        assert transaction.status == TransactionStatus.SUCCESS.value
        assert transaction.amount == amount
        assert wallet.balance == initial_balance - amount
        assert (
            transaction.subscription_details["plan_name"]
            == user_subscription.subscription.plan.plan_name
        )

    def test_multiple_subscription_payments(self, user):
        """Test processing multiple subscription payments."""
        wallet = wallet_recipe.make(user=user, balance=Decimal("1000.00"))
        subscriptions = baker.make(
            UserSubscription,
            user=user,
            _quantity=3,
            subscription=plan_cost_recipe.make(cost=Decimal("20.00")),
        )

        for sub in subscriptions:
            transaction = wallet.process_subscription_payment(
                Decimal("20.00"), sub.subscription, "subscription"
            )
            assert transaction.status == TransactionStatus.SUCCESS.value

        assert wallet.balance == Decimal("940.00")
        assert wallet.transactions.count() == 3

    def test_insufficient_funds(self, wallet, user_subscription):
        """Test payment with insufficient funds."""
        wallet.balance = Decimal("5.00")
        with pytest.raises(ValidationError):
            wallet.process_subscription_payment(
                Decimal("10.00"), user_subscription.subscription, "subscription"
            )

    def test_refund_full_amount(self, wallet, user_subscription):
        """Test processing full refund."""
        payment = wallet.process_subscription_payment(
            Decimal("10.00"), user_subscription.subscription, "subscription"
        )
        initial_balance = wallet.balance

        refund = wallet.process_refund(
            payment,
            reason=RefundReason.CUSTOMER_REQUEST,
            description="Full refund test",
        )

        assert refund.transaction_type == TransactionType.REFUND.value
        assert refund.amount == payment.amount
        assert wallet.balance == initial_balance + payment.amount
        assert payment.status == TransactionStatus.REFUNDED.value

    def test_refund_partial_amount(self, wallet, user_subscription):
        """Test processing partial refund."""
        payment = wallet.process_subscription_payment(
            Decimal("10.00"), user_subscription.subscription, "subscription"
        )
        initial_balance = wallet.balance

        refund = wallet.process_refund(
            payment,
            amount=Decimal("5.00"),
            reason=RefundReason.CUSTOMER_REQUEST,
            description="Partial refund test",
        )

        assert refund.amount == Decimal("5.00")
        assert wallet.balance == initial_balance + Decimal("5.00")
        assert refund.refund_details["full_refund"] is False

    def test_subscription_cancellation_with_prorate(self, wallet, user_subscription):
        """Test subscription cancellation with prorated refund."""
        payment = wallet.process_subscription_payment(
            Decimal("10.00"), user_subscription.subscription, "cancellation"
        )
        initial_balance = wallet.balance

        with patch("django.utils.timezone.now") as mock_now:
            mock_now.return_value = payment.created_at + timedelta(days=15)
            refund = wallet.process_subscription_cancellation(
                user_subscription, prorate=True
            )

        assert refund.transaction_type == TransactionType.REFUND.value
        assert Decimal("4.00") < refund.amount < Decimal("6.00")
        assert wallet.balance > initial_balance
        assert refund.refund_details["refund_reason"] == RefundReason.CANCELLATION.value

    def test_get_statement_with_date_range(self, wallet):
        """Test getting wallet statement with date filtering."""
        # Create transactions at different dates
        past_transaction = baker.make(
            WalletTransaction,
            wallet=wallet,
            amount=Decimal("10.00"),
            created_at=timezone.now() - timedelta(days=10),
        )
        present_transaction = baker.make(
            WalletTransaction, wallet=wallet, amount=Decimal("20.00")
        )

        start_date = timezone.now() - timedelta(days=5)
        end_date = timezone.now() + timedelta(days=1)

        statement = wallet.get_statement(start_date, end_date)
        assert len(statement) == 1
        assert present_transaction in statement
        assert past_transaction not in statement
