from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from enum import Enum
from typing import Dict, List, Optional

from django.core.exceptions import ValidationError
from django.db.models import Q
from django.utils import timezone

from subscription.billing import UsageBasedBilling
from subscription.error_handling import ErrorHandler
from subscription.models.feature import FeatureType, FeatureUsage, PlanFeature
from subscription.models.plan import UserSubscription
from subscription.models.wallet import TransactionStatus, Wallet
from subscription.settings import CONFIG


class BillingType(Enum):
    REGULAR = "regular"
    FEATURE_BASED = "feature_based"
    HYBRID = "hybrid"


@dataclass
class BillingBreakdown:
    base_cost: Decimal
    feature_charges: List[Dict]
    total_amount: Decimal
    billing_type: BillingType


@dataclass
class PaymentResult:
    success: bool
    transaction: Optional[object] = None
    error: Optional[Exception] = None
    breakdown: Optional[BillingBreakdown] = None


class PlanManager:
    """Manager object to handle both regular and feature-based subscription billing."""

    def __init__(self):
        self.error_handler = ErrorHandler(self)
        self.usage_billing = UsageBasedBilling()

    def process_subscriptions(self):
        """Calls all required subscription processing functions."""
        current = timezone.now()

        self._process_subscription_batch(
            UserSubscription.objects.filter(
                Q(active=True) & Q(cancelled=False) & Q(date_billing_end__lte=current)
            ),
            self.process_expired,
        )

        self._process_subscription_batch(
            UserSubscription.objects.filter(
                Q(active=False)
                & Q(cancelled=False)
                & Q(date_billing_start__lte=current)
            ),
            self.process_new,
        )

        self._process_subscription_batch(
            UserSubscription.objects.filter(
                Q(active=True) & Q(cancelled=False) & Q(date_billing_next__lte=current)
            ),
            self.process_due,
        )

    def _process_subscription_batch(self, queryset, processor_func):
        """Safely process a batch of subscriptions with error handling."""
        for subscription in queryset:
            try:
                processor_func(subscription)
            except Exception as e:
                self.error_handler.handle_payment_error(subscription, e)

    def _process_final_usage_payment(
        self, subscription: UserSubscription, feature_charges: List[Dict]
    ):
        """Process final payment for any remaining feature usage."""
        if not feature_charges:
            return

        total_amount = sum(
            Decimal(str(charge["charges"]["total"])) for charge in feature_charges
        )

        if total_amount > Decimal("0"):
            wallet = Wallet.objects.get_or_create_wallet(subscription.user)
            wallet.process_subscription_payment(
                amount=total_amount,
                plan_cost=subscription.subscription,
                description="Final usage charges",
            )

    def process_expired(self, subscription):
        """Handle expired subscriptions with feature cleanup."""
        wallet = Wallet.objects.get_or_create_wallet(subscription.user)

        try:
            # Calculate any final feature charges
            final_charges = self._calculate_feature_charges(subscription)
            if final_charges:
                # Process final payment for usage
                self._process_final_usage_payment(subscription, final_charges)

            refund_result = self._process_refund(wallet, subscription)
            if not refund_result.success and refund_result.error:
                self.error_handler.handle_refund_error(
                    subscription, refund_result.transaction, refund_result.error
                )
        except Exception as e:
            self.error_handler.handle_refund_error(subscription, None, e)

        subscription.active = False
        subscription.cancelled = True
        subscription.save()

        self.notify_expired(subscription)

    def _process_refund(self, wallet, subscription) -> PaymentResult:
        """Process refund with error handling."""
        try:
            transaction = wallet.process_subscription_cancellation(
                subscription=subscription,
                prorate=True,
                description=f"Automatic refund for expired subscription - {subscription.subscription.plan.plan_name}",  # noqa
            )
            return PaymentResult(success=True, transaction=transaction)
        except ValidationError as e:
            return PaymentResult(success=False, error=e)
        except Exception as e:
            return PaymentResult(success=False, error=e)

    def process_new(self, subscription):
        """Handles processing of a new subscription."""
        payment_result = self._process_subscription_payment(subscription)

        if payment_result.success:
            self._activate_subscription(subscription)
            self.notify_new(subscription)
            self.notify_payment_success(subscription)
        else:
            self.notify_payment_error(subscription)
            if payment_result.error:
                self.error_handler.handle_payment_error(
                    subscription, payment_result.error
                )

    def process_due(self, subscription):
        """Handles processing of a due subscription."""
        payment_result = self._process_subscription_payment(subscription)

        if payment_result.success:
            self._update_billing_dates(subscription)
            self.notify_payment_success(subscription)
        else:
            self._handle_failed_renewal(subscription)
            self.notify_payment_error(subscription)
            if payment_result.error:
                self.error_handler.handle_payment_error(
                    subscription, payment_result.error
                )

    def _determine_billing_type(self, subscription: UserSubscription) -> BillingType:
        """Determine the billing type based on plan configuration."""
        plan_cost = subscription.subscription
        has_base_cost = plan_cost.cost and plan_cost.cost > Decimal("0")
        has_feature_billing = PlanFeature.objects.filter(
            plan=plan_cost.plan, feature__feature_type=FeatureType.USAGE.value
        ).exists()

        if has_base_cost and has_feature_billing:
            return BillingType.HYBRID
        elif has_feature_billing:
            return BillingType.FEATURE_BASED
        else:
            return BillingType.REGULAR

    def _calculate_billing_breakdown(
        self, subscription: UserSubscription
    ) -> BillingBreakdown:
        """Calculate complete billing breakdown including base cost and features."""
        billing_type = self._determine_billing_type(subscription)
        base_cost = Decimal("0")
        feature_charges = []

        # Calculate base subscription cost if applicable
        if billing_type in [BillingType.REGULAR, BillingType.HYBRID]:
            base_cost = (
                Decimal(str(subscription.subscription.cost))
                if subscription.subscription.cost
                else Decimal(0)
            )

        # Calculate feature charges if applicable
        if billing_type in [BillingType.FEATURE_BASED, BillingType.HYBRID]:
            feature_charges = self._calculate_feature_charges(subscription)

        total_amount = base_cost + sum(
            Decimal(str(charge["charges"]["total"])) for charge in feature_charges
        )

        return BillingBreakdown(
            base_cost=base_cost,
            feature_charges=feature_charges,
            total_amount=total_amount,
            billing_type=billing_type,
        )

    def _calculate_feature_charges(self, subscription: UserSubscription) -> List[Dict]:
        """Calculate charges for all billable features."""
        feature_charges = []

        # Get all feature usage records for this subscription
        usage_records = FeatureUsage.objects.filter(
            subscription=subscription,
            feature__feature_type=FeatureType.USAGE.value,  # Only get usage-based features
        ).select_related("feature")
        for usage in usage_records:
            if usage.quantity > 0:
                charges = self.usage_billing.calculate_charges(
                    subscription=subscription,
                    feature_code=usage.feature.code,
                    quantity=usage.quantity,
                )

                if "error" not in charges:
                    feature_charges.append(
                        {
                            "feature": usage.feature,
                            "usage": usage.quantity,
                            "charges": charges,
                        }
                    )

        return feature_charges

    def _process_subscription_payment(self, subscription) -> PaymentResult:
        """Process subscription payment handling both regular and feature-based billing."""
        try:
            wallet = Wallet.objects.get_or_create_wallet(subscription.user)

            # Calculate all charges
            billing_breakdown = self._calculate_billing_breakdown(subscription)

            if billing_breakdown.total_amount <= Decimal("0"):
                # Handle free plans
                self._handle_free_plan_renewal(subscription)
                return PaymentResult(success=True, breakdown=billing_breakdown)

            # Process payment
            transaction = wallet.process_subscription_payment(
                amount=billing_breakdown.total_amount,
                plan_cost=subscription.subscription,
                description=self._generate_payment_description(
                    subscription, billing_breakdown
                ),
            )

            if transaction and transaction.status == TransactionStatus.SUCCESS.value:
                self._handle_successful_payment(subscription, billing_breakdown)
                return PaymentResult(
                    success=True, transaction=transaction, breakdown=billing_breakdown
                )

            return PaymentResult(success=False)

        except ValidationError as e:
            return PaymentResult(success=False, error=e)
        except Exception as e:
            return PaymentResult(success=False, error=e)

    def _handle_successful_payment(
        self, subscription: UserSubscription, breakdown: BillingBreakdown
    ):
        """Handle post-payment actions based on billing type."""
        # Reset feature usage if needed
        self._reset_usage_counters(subscription)

        # Update billing dates
        self._update_billing_dates(subscription)

        # Store billing breakdown for reporting
        subscription.last_billing_breakdown = breakdown
        subscription.save()

        # Trigger any necessary notifications
        self._notify_payment_success(subscription, breakdown)

    def _handle_free_plan_renewal(self, subscription: UserSubscription):
        """Handle renewal for free plans."""
        self._reset_usage_counters(subscription)
        self._update_billing_dates(subscription)
        subscription.active = True
        subscription.save()

    def _generate_payment_description(
        self, subscription: UserSubscription, breakdown: BillingBreakdown
    ) -> str:
        """Generate detailed payment description based on billing type."""
        description = [
            f"Subscription payment - {subscription.subscription.plan.plan_name}"
        ]

        if breakdown.billing_type != BillingType.FEATURE_BASED:
            description.append(f"Base plan cost: ${breakdown.base_cost}")

        if breakdown.feature_charges:
            description.append("\nFeature charges:")
            for charge in breakdown.feature_charges:
                description.append(
                    f"- {charge['feature'].name}: "
                    f"{charge['usage']} units = "
                    f"${charge['charges']['total']}"
                )

        description.append(f"\nTotal amount: ${breakdown.total_amount}")
        return "\n".join(description)

    def _reset_usage_counters(self, subscription: UserSubscription):
        """Reset usage counters based on feature configuration."""
        FeatureUsage.objects.filter(
            subscription=subscription, feature__reset_on_billing=True
        ).update(quantity=0, last_reset=timezone.now())

    def _notify_payment_success(
        self, subscription: UserSubscription, breakdown: BillingBreakdown
    ):
        """Send notifications with billing type specific details."""
        notification_data = {
            "subscription": subscription,
            "billing_type": breakdown.billing_type,
            "total_amount": breakdown.total_amount,
            "base_cost": breakdown.base_cost if breakdown.base_cost > 0 else None,
            "feature_charges": (
                breakdown.feature_charges if breakdown.feature_charges else None
            ),
        }
        print(notification_data)

    def _activate_subscription(self, subscription):
        """Activate a new subscription and set billing dates."""
        current = timezone.now()
        next_billing = subscription.subscription.next_billing_datetime(
            subscription.date_billing_start
        )

        subscription.date_billing_last = current
        subscription.date_billing_next = next_billing
        subscription.active = True
        subscription.save()

    def _update_billing_dates(self, subscription):
        """Update billing dates for successful renewal."""
        current = timezone.now()
        next_billing = subscription.subscription.next_billing_datetime(
            subscription.date_billing_next
        )

        subscription.date_billing_last = current
        subscription.date_billing_next = next_billing
        subscription.save()

    def _handle_failed_renewal(self, subscription):
        """Handle failed renewal within grace period."""
        plan_cost = subscription.subscription
        grace_period = plan_cost.plan.grace_period

        if grace_period > CONFIG["GRACE_PERIOD_DAYS"]:
            current = timezone.now()
            next_billing_date = subscription.date_billing_next + timedelta(
                days=grace_period
            )
            next_billing = plan_cost.next_billing_datetime(next_billing_date)

            subscription.date_billing_last = current
            subscription.date_billing_next = next_billing
            subscription.active = True
            subscription.save()

    def notify_insufficient_funds(self, subscription, required_amount):
        """Notify user about insufficient funds."""
        pass

    def notify_subscription_cancelled(self, subscription, reason):
        """Notify user about subscription cancellation."""
        pass

    def notify_expired(self, subscription):
        pass

    def notify_new(self, subscription):
        pass

    def notify_payment_error(self, subscription):
        pass

    def notify_payment_success(self, subscription):
        pass
