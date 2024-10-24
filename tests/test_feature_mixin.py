from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.http import HttpResponse
from django.test import RequestFactory
from django.utils import timezone
from django.views.generic import View
from model_bakery import baker

from subscription.feature import FeatureRequiredMixin
from subscription.models.feature import Feature, FeatureType, PlanFeature
from subscription.models.plan import PlanCost, SubscriptionPlan, UserSubscription

User = get_user_model()


@pytest.fixture
def request_factory():
    return RequestFactory()


@pytest.fixture
def user():
    return baker.make(User)


@pytest.fixture
def subscription_plan():
    return baker.make(SubscriptionPlan, is_feature_based=True)


@pytest.fixture
def plan_cost(subscription_plan):
    return baker.make(
        PlanCost,
        plan=subscription_plan,
        cost=10.00,
        recurrence_period=1,
        recurrence_unit="month",
    )


@pytest.fixture
def feature():
    return baker.make(
        Feature, code="test_feature", feature_type=FeatureType.BOOLEAN.value
    )


@pytest.fixture
def additional_feature():
    return baker.make(
        Feature, code="additional_feature", feature_type=FeatureType.BOOLEAN.value
    )


@pytest.fixture
def plan_feature(subscription_plan, feature):
    return baker.make(
        PlanFeature, plan=subscription_plan, feature=feature, enabled=True
    )


@pytest.fixture
def additional_plan_feature(subscription_plan, additional_feature):
    return baker.make(
        PlanFeature, plan=subscription_plan, feature=additional_feature, enabled=True
    )


@pytest.fixture
def active_subscription(user, plan_cost):
    now = timezone.now()
    return baker.make(
        UserSubscription,
        user=user,
        subscription=plan_cost,
        active=True,
        cancelled=False,
        date_billing_start=now - timedelta(days=1),
        date_billing_end=now + timedelta(days=30),
    )


class SingleFeatureView(FeatureRequiredMixin, View):
    required_features = {"test_feature"}

    def get(self, request, *args, **kwargs):
        return HttpResponse("OK")


class MultiFeatureView(FeatureRequiredMixin, View):
    required_features = {"test_feature", "additional_feature"}

    def get(self, request, *args, **kwargs):
        return HttpResponse("OK")


class NoFeatureView(FeatureRequiredMixin, View):
    def get(self, request, *args, **kwargs):
        return HttpResponse("OK")


@pytest.mark.django_db
class TestFeatureRequiredMixin:
    def test_single_feature_access_granted(
        self, request_factory, user, active_subscription, plan_feature
    ):
        """Test access is granted when user has required feature."""
        view = SingleFeatureView.as_view()
        request = request_factory.get("/")
        request.user = user

        response = view(request)
        assert response.status_code == 200

    def test_single_feature_access_denied_inactive_subscription(
        self, request_factory, user, active_subscription, plan_feature
    ):
        """Test access is denied when subscription is inactive."""
        active_subscription.active = False
        active_subscription.save()

        view = SingleFeatureView.as_view()
        request = request_factory.get("/")
        request.user = user

        with pytest.raises(PermissionDenied):
            view(request)

    def test_single_feature_access_denied_cancelled_subscription(
        self, request_factory, user, active_subscription, plan_feature
    ):
        """Test access is denied when subscription is cancelled."""
        active_subscription.cancelled = True
        active_subscription.save()

        view = SingleFeatureView.as_view()
        request = request_factory.get("/")
        request.user = user

        with pytest.raises(PermissionDenied):
            view(request)

    def test_single_feature_access_denied_expired_subscription(
        self, request_factory, user, active_subscription, plan_feature
    ):
        """Test access is denied when subscription has expired."""
        active_subscription.date_billing_end = timezone.now() - timedelta(days=1)
        active_subscription.save()

        view = SingleFeatureView.as_view()
        request = request_factory.get("/")
        request.user = user

        with pytest.raises(PermissionDenied):
            view(request)

    def test_single_feature_access_denied_future_subscription(
        self, request_factory, user, active_subscription, plan_feature
    ):
        """Test access is denied when subscription hasn't started yet."""
        active_subscription.date_billing_start = timezone.now() + timedelta(days=1)
        active_subscription.save()

        view = SingleFeatureView.as_view()
        request = request_factory.get("/")
        request.user = user

        with pytest.raises(PermissionDenied):
            view(request)

    def test_multi_feature_access_granted(
        self,
        request_factory,
        user,
        active_subscription,
        plan_feature,
        additional_plan_feature,
    ):
        """Test access is granted when user has all required features."""
        view = MultiFeatureView.as_view()
        request = request_factory.get("/")
        request.user = user

        response = view(request)
        assert response.status_code == 200

    def test_multi_feature_access_denied_missing_feature(
        self, request_factory, user, active_subscription, plan_feature
    ):
        """Test access is denied when user is missing one required feature."""
        view = MultiFeatureView.as_view()
        request = request_factory.get("/")
        request.user = user

        with pytest.raises(PermissionDenied):
            view(request)

    def test_no_feature_requirements(self, request_factory, user, active_subscription):
        """Test access is granted when no features are required."""
        view = NoFeatureView.as_view()
        request = request_factory.get("/")
        request.user = user

        response = view(request)
        assert response.status_code == 200

    def test_disabled_feature(
        self, request_factory, user, active_subscription, plan_feature
    ):
        """Test access is denied when feature is disabled in plan."""
        plan_feature.enabled = False
        plan_feature.save()

        view = SingleFeatureView.as_view()
        request = request_factory.get("/")
        request.user = user

        with pytest.raises(PermissionDenied):
            view(request)

    def test_no_subscription(self, request_factory, user):
        """Test access is denied when user has no subscription."""
        view = SingleFeatureView.as_view()
        request = request_factory.get("/")
        request.user = user

        with pytest.raises(PermissionDenied):
            view(request)

    @pytest.mark.parametrize(
        "required_feature",
        [
            "nonexistent_feature",
            "",
            None,
        ],
    )
    def test_invalid_feature_codes(
        self, request_factory, user, active_subscription, required_feature
    ):
        """Test access is denied for invalid feature codes."""

        # Define the view class without setting `required_feature`
        class InvalidFeatureView(FeatureRequiredMixin, View):
            def get(self, request, *args, **kwargs):
                return HttpResponse("OK")

        # Dynamically set the `required_feature` after class definition
        InvalidFeatureView.required_features = {required_feature}

        view = InvalidFeatureView.as_view()
        request = request_factory.get("/")
        request.user = user

        with pytest.raises(PermissionDenied):
            view(request)
