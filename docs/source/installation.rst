Quick start
===========

.. _installation:

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
