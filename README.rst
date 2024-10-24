About
-----
This Django library provides a comprehensive solution for managing 
subscriptions, feature access and wallet functionality

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

Installation
------------

1. Install package

.. code-block:: python

   pip install django-enhanced-subcriptions


2. Add "django-enhanced-subcriptions" to your ``INSTALLED_APPS`` setting:

   .. code-block:: python

      INSTALLED_APPS = [
          ...
          'django-enhanced-subcriptions',
      ]

3. Run migrations:

   .. code-block:: python

      python manage.py migrate

4. Override the below config in ``settings.py`` 

   .. code-block:: python

      SUBSCRIPTION = {
        "CACHE_TIMEOUT_SECONDS":  60,
        "BASE_RETRY_DELAY_SECONDS": 300,
        "FIXED_INTERVAL_DELAY": 3600,
        "MAX_RETRY_ATTEMPTS": 3,
        "CACHE_TIMEOUT_MINUTES": 5,
        "GRACE_PERIOD_DAYS": 1,
        "ENABLE_ADMIN": True, 
      }

Documentation
-------------

`https://django-enhanced-subscriptions.readthedocs.io/en/latest/index.html`
