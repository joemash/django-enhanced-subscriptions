## About
This Django library provides a comprehensive solution for managing subscriptions, feature access and wallet functionality

## Installation

```bash
pip install django-enhanced-subcriptions
```

## Quick start

1. Add "django-enhanced-subcriptions" to your INSTALLED_APPS setting:

```python
INSTALLED_APPS = [
    ...
    'django-enhanced-subcriptions',
]
```

2. Run migrations:

```bash
python manage.py migrate
```

## Development

To set up the development environment:

1. Clone the repository
2. Create a virtual environment and activate it
3. Install development dependencies:

```bash
pip install -e ".[dev]"
```

4. Run tests:
```bash
python -m pytest tests

OR

pytest tests/
```

## Install test dependencies

pip install -e ".[test]"

## Making migrations

python testapp/manage.py makemigrations

python testapp/manage.py makemigrations <app_name> --empty

## Migrate

python testapp/manage.py migrate

Read the tutorial here:

https://docs.readthedocs.io/en/stable/tutorial/


Comprehensive Error Tracking:

Logs all errors with detailed context
Tracks retry attempts and their outcomes
Maintains error history for auditing
Uses appropriate database indexes for efficient querying


Flexible Retry Strategies:

Exponential backoff for transient errors
Immediate retry for urgent operations like refunds
Fixed interval retries for predictable issues
Manual intervention option for complex cases

Recovery Mechanisms:

Automated retry processing
Manual intervention triggers
Clear audit trail of all attempts
State transition management


Set up periodic tasks to process retries:

In your task scheduler (e.g., Celery)

@periodic_task(run_every=timedelta(minutes=5))
def process_subscription_retries():
    RetryManager().process_pending_retries()

Add monitoring for unresolved errors:

In your monitoring system

def check_subscription_errors():
    report = RetryManager().get_failed_subscriptions_report()
    if report.filter(count__gt=0).exists():
        alert_operations_team(report)


