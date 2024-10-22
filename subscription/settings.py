from django.conf import settings


def get_subscription_config():
    subscription_config = getattr(settings, "SUBSCRIPTION", {})
    return {
        "CACHE_TIMEOUT_SECONDS": subscription_config.get("CACHE_TIMEOUT_SECONDS", 60),
        "BASE_RETRY_DELAY_SECONDS": subscription_config.get(
            "BASE_RETRY_DELAY_SECONDS", 300
        ),
        "FIXED_INTERVAL_DELAY": subscription_config.get("FIXED_INTERVAL_DELAY", 3600),
        "MAX_RETRY_ATTEMPTS": subscription_config.get("MAX_RETRY_ATTEMPTS", 3),
        "CACHE_TIMEOUT_MINUTES": subscription_config.get("CACHE_TIMEOUT_MINUTES", 5),
        "GRACE_PERIOD_DAYS": subscription_config.get("GRACE_PERIOD_DAYS", 1),
        "ENABLE_ADMIN": subscription_config.get("ENABLE_ADMIN", True),
    }


CONFIG = get_subscription_config()
