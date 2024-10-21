from datetime import timedelta
from decimal import Decimal

from django.utils import timezone
from model_bakery.recipe import Recipe, foreign_key

from subscription.models.plan import MONTH, PlanCost, SubscriptionPlan, UserSubscription
from subscription.models.wallet import Wallet

wallet_recipe = Recipe(Wallet, balance=Decimal("100.00"))

subscription_plan_recipe = Recipe(
    SubscriptionPlan,
    plan_name="Premium Plan",
    grace_period=2,
)

plan_cost_recipe = Recipe(
    PlanCost,
    plan=foreign_key(subscription_plan_recipe),
    cost=Decimal("10.00"),
    recurrence_period=1,
    recurrence_unit=MONTH,
)

user_subscription_recipe = Recipe(
    UserSubscription,
    subscription=foreign_key(plan_cost_recipe),
    date_billing_start=timezone.now,
    date_billing_end=lambda: timezone.now() + timedelta(days=30),
    date_billing_next=lambda: timezone.now() + timedelta(days=30),
    active=False,
    cancelled=False,
)
