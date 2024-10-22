"""Models for the Subscriptions app."""

from datetime import timedelta
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.utils.translation import gettext_lazy as _

# Convenience references for units for plan recurrence billing
# ----------------------------------------------------------------------------
ONCE = "once"
SECOND = "second"
MINUTE = "minute"
HOUR = "hour"
DAY = "day"
WEEK = "week"
MONTH = "month"
YEAR = "year"
RECURRENCE_UNIT_CHOICES = (
    (ONCE, "once"),
    (SECOND, "second"),
    (MINUTE, "minute"),
    (HOUR, "hour"),
    (DAY, "day"),
    (WEEK, "week"),
    (MONTH, "month"),
    (YEAR, "year"),
)


class PlanTag(models.Model):
    """A tag for a subscription plan."""

    tag = models.CharField(
        help_text=_("the tag name"),
        max_length=64,
        unique=True,
    )

    class Meta:
        ordering = ("tag",)

    def __str__(self):
        return self.tag


class SubscriptionPlan(models.Model):
    """Details for a subscription plan."""

    id = models.UUIDField(
        default=uuid4,
        editable=False,
        primary_key=True,
        verbose_name="ID",
    )
    plan_name = models.CharField(
        help_text=_("the name of the subscription plan"),
        max_length=128,
    )
    is_feature_based = models.BooleanField(
        default=False,
        help_text=_("whether this plan is regular of feature based"),
    )
    slug = models.SlugField(
        blank=True,
        help_text=_("slug to reference the subscription plan"),
        max_length=128,
        null=True,
        unique=True,
    )
    plan_description = models.CharField(
        blank=True,
        help_text=_("a description of the subscription plan"),
        max_length=512,
        null=True,
    )
    tags = models.ManyToManyField(
        PlanTag,
        blank=True,
        help_text=_("any tags associated with this plan"),
        related_name="plans",
    )
    grace_period = models.PositiveIntegerField(
        default=0,
        help_text=_(
            "how many days after the subscription ends before the "
            "subscription expires"
        ),
    )

    class Meta:
        ordering = ("plan_name",)
        permissions = (("subscriptions", "Can interact with subscription details"),)

    def __str__(self):
        return self.plan_name


class PlanCost(models.Model):
    """Cost and frequency of billing for a plan."""

    id = models.UUIDField(
        default=uuid4,
        editable=False,
        primary_key=True,
        verbose_name="ID",
    )
    plan = models.ForeignKey(
        SubscriptionPlan,
        help_text=_("the subscription plan for these cost details"),
        on_delete=models.CASCADE,
        related_name="costs",
    )
    slug = models.SlugField(
        blank=True,
        help_text=_("slug to reference these cost details"),
        max_length=128,
        null=True,
        unique=True,
    )
    recurrence_period = models.PositiveSmallIntegerField(
        default=1,
        help_text=_("how often the plan is billed (per recurrence unit)"),
        validators=[MinValueValidator(1)],
    )
    recurrence_unit = models.CharField(
        choices=RECURRENCE_UNIT_CHOICES,
        default=MONTH,
        max_length=50,
    )
    cost = models.DecimalField(
        blank=True,
        decimal_places=4,
        help_text=_("the cost per recurrence of the plan"),
        max_digits=19,
        null=True,
    )

    class Meta:
        ordering = (
            "recurrence_unit",
            "recurrence_period",
            "cost",
        )

    def next_billing_datetime(self, current):
        """Calculates next billing date for provided datetime.

        Parameters:
            current (datetime): The current datetime to compare
                against.

        Returns:
            datetime: The next time billing will be due.
        """
        if self.recurrence_unit == SECOND:
            delta = timedelta(seconds=self.recurrence_period)
        elif self.recurrence_unit == MINUTE:
            delta = timedelta(minutes=self.recurrence_period)
        elif self.recurrence_unit == HOUR:
            delta = timedelta(hours=self.recurrence_period)
        elif self.recurrence_unit == DAY:
            delta = timedelta(days=self.recurrence_period)
        elif self.recurrence_unit == WEEK:
            delta = timedelta(weeks=self.recurrence_period)
        elif self.recurrence_unit == MONTH:
            # Adds the average number of days per month as per:
            # http://en.wikipedia.org/wiki/Month#Julian_and_Gregorian_calendars
            # This handle any issues with months < 31 days and leap years
            delta = timedelta(days=30.4368 * self.recurrence_period)
        elif self.recurrence_unit == YEAR:
            # Adds the average number of days per year as per:
            # http://en.wikipedia.org/wiki/Year#Calendar_year
            # This handle any issues with leap years
            delta = timedelta(days=365.2425 * self.recurrence_period)
        else:
            # If no recurrence period, no next billing datetime
            return None

        return current + delta


class UserSubscription(models.Model):
    """Details of a user's specific subscription."""

    id = models.UUIDField(
        default=uuid4,
        editable=False,
        primary_key=True,
        verbose_name="ID",
    )
    user = models.ForeignKey(
        get_user_model(),
        help_text=_("the user this subscription applies to"),
        null=True,
        on_delete=models.CASCADE,
        related_name="subscription",
    )
    subscription = models.ForeignKey(
        PlanCost,
        help_text=_("the plan costs and billing frequency for this user"),
        null=True,
        on_delete=models.CASCADE,
        related_name="subscription",
    )
    date_billing_start = models.DateTimeField(
        blank=True,
        help_text=_("the date to start billing this subscription"),
        null=True,
        verbose_name="billing start date",
    )
    date_billing_end = models.DateTimeField(
        blank=True,
        help_text=_("the date to finish billing this subscription"),
        null=True,
        verbose_name="billing start end",
    )
    date_billing_last = models.DateTimeField(
        blank=True,
        help_text=_("the last date this plan was billed"),
        null=True,
        verbose_name="last billing date",
    )
    date_billing_next = models.DateTimeField(
        blank=True,
        help_text=_("the next date billing is due"),
        null=True,
        verbose_name="next start date",
    )
    active = models.BooleanField(
        default=True,
        help_text=_("whether this subscription is active or not"),
    )
    cancelled = models.BooleanField(
        default=False,
        help_text=_("whether this subscription is cancelled or not"),
    )

    def clean(self):
        if self.date_billing_end and self.date_billing_end <= self.date_billing_start:
            raise ValidationError("End date must be after start date")

    class Meta:
        ordering = (
            "user",
            "date_billing_start",
        )
