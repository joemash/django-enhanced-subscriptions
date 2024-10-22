Usage
=====

.. _features:

Features
--------
+ Associates features with subscription plans and define limits
+ Manage a user wallet for managing subscription payments, refunds and credits
+ Records all wallet transactions including subscription payments, cancellation and refunds
+ Capture details for a subscription plan
+ Define cost and frequency of billing for a plan
+ Associates user's to specific subscriptions
+ Define feature that can be included in subscription plans
+ Define pricing tiers for features with tiered pricing
+ Tracks usage of features by subscribed users

Pricing model
-------------

FLAT
~~~~ 
A pricing model where a fixed rate applies
either per unit of usage or overage beyond a certain threshold.
For example a flat rate of $0.05 per SMS sent.

TIERED
~~~~~~ 
A model where the price per unit varies depending on the
total quantity used. Different levels of usage are charged at different rates.
For example $10/unit for the first 100 units, then $8/unit for the next 200 units

VOLUME 
~~~~~~
All units of usage are charged at the same rate, but the rate is determined by
the total volume used. The more you use, the lower the rate.
For example  for 1-100 users, it might cost $10/user 101-500 users, the rate drops to $8/user
    
PACKAGE
~~~~~~~
Users are charged for predefined packages or bundles.
For example 500 SMS for $20. If the user needs more, they purchase another package

Feature types
-------------

USAGE
~~~~~
Represents features that are always allowed but charge based on consumption.
For example, "Pay per API call" or "Storage used".

QUOTA
~~~~~
Represents features with a fixed limit over the billing period.
For example, "500 API calls per month" or "10 team members".
    
RATE 
~~~~
Represents features with time-window based limits.
For example, "100 API calls per hour" or "10 exports per day".
These get reset after their time window expires.

BOOLEAN 
~~~~~~~
Represents features that are either on or off (enabled/disabled).
For example, "Access to API" or "Dark Mode Support".


Error handling
--------------
Comprehensive Error Tracking:
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

+ Logs all errors with detailed context
+ Tracks retry attempts and their outcomes
+ Maintains error history for auditing
+ Uses appropriate database indexes for efficient querying


Flexible Retry Strategies:
~~~~~~~~~~~~~~~~~~~~~~~~~~
+ Exponential backoff for transient errors
+ Immediate retry for urgent operations like refunds
+ Fixed interval retries for predictable issues
+ Manual intervention option for complex cases

Recovery Mechanisms:
~~~~~~~~~~~~~~~~~~~~
+ Automated retry processing
+ Manual intervention triggers
+ Clear audit trail of all attempts
+ State transition management


Set up periodic tasks to process retries:
In your task scheduler (e.g., Celery)

.. code-block:: python
   @periodic_task(run_every=timedelta(minutes=5))
   def process_subscription_retries():
      RetryManager().process_pending_retries()

Add monitoring for unresolved errors:

In your monitoring system
.. code-block:: python
   def check_subscription_errors():
      report = RetryManager().get_failed_subscriptions_report()
      if report.filter(count__gt=0).exists():
         alert_operations_team(report)
