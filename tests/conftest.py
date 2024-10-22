import pytest
from django.contrib.auth import get_user_model
from model_bakery import baker

from subscription.billing import UsageBasedBilling
from subscription.manager import PlanManager
from subscription.models.feature import FeatureType
from tests.factories import (
    plan_cost_recipe,
    subscription_plan_recipe,
    user_subscription_recipe,
    wallet_recipe,
)

User = get_user_model()


@pytest.fixture(autouse=True)
def enable_db_access_for_all_tests(db):
    pass


@pytest.fixture
def user():
    return baker.make(User)


@pytest.fixture
def wallet(user):
    return wallet_recipe.make(user=user)


@pytest.fixture
def subscription_plan():
    return subscription_plan_recipe.make()


@pytest.fixture
def plan_cost(subscription_plan):
    return plan_cost_recipe.make(plan=subscription_plan)


@pytest.fixture
def user_subscription(user, plan_cost):
    return user_subscription_recipe.make(user=user, subscription=plan_cost)


@pytest.fixture
def feature_plan():
    return baker.make("SubscriptionPlan", is_feature_based=True)


@pytest.fixture
def feature():
    return baker.make(
        "Feature", code="test_feature", feature_type=FeatureType.USAGE.value
    )


@pytest.fixture
def plan_feature(feature, feature_plan):
    return baker.make("PlanFeature", plan=feature_plan, feature=feature, enabled=True)


@pytest.fixture
def subscription(user, feature_plan):
    return baker.make("UserSubscription", user=user, subscription__plan=feature_plan)


@pytest.fixture
def plan_manager():
    return PlanManager()


@pytest.fixture
def usage_based_billing():
    return UsageBasedBilling()
