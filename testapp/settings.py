from pathlib import Path

# Add all environment variables to config.env in root directory
ROOT_DIR = Path(__file__).parent.absolute()


INSTALLED_APPS = (
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.sites",
    # Third party apps
    "subscription",
)

# https://docs.djangoproject.com/en/2.0/topics/http/middleware/
MIDDLEWARE = (
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
)

# urls
ROOT_URLCONF = 'urls'

# DEBUG SETTINGS
# Used for testapp - DO NOT USE IN PRODUCTION
DEBUG = True
TEMPLATE_DEBUG = True
SQL_DEBUG = True

# BASE DJANGO SETTINGS
SECRET_KEY = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ1234567890'
SITE_ID = 1
INTERNAL_IPS = ('127.0.0.1',)
APPEND_SLASH = True

# ADMIN SETTINGS
ADMINS = (
    # ('Your Name', 'your_email@domain.com'),
)
MANAGERS = ADMINS

# EMAIL SETTINGS
EMAIL_BACKEND = 'django.core.mail.backends.console.EmailBackend'

# LOCALIZATION SETTINGS
USE_TZ = True
TIME_ZONE = 'UTC'
LANGUAGE_CODE = 'en-ca'
LANGUAGES = [
    ('en-ca', 'English')
]
USE_I18N = True
USE_L10N = True

# DATABASE SETTINGS
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': str(ROOT_DIR.joinpath('db.sqlite3')),
    }
}

# TEMPLATE SETTINGS
TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [
            ROOT_DIR.joinpath('templates'),
        ],
        'OPTIONS': {
            'loaders': [
                'django.template.loaders.filesystem.Loader',
                'django.template.loaders.app_directories.Loader',
            ],
            'context_processors': [
                'django.contrib.auth.context_processors.auth',
                'django.template.context_processors.request',
                'django.template.context_processors.debug',
                'django.template.context_processors.i18n',
                'django.template.context_processors.media',
                'django.template.context_processors.static',
                'django.contrib.messages.context_processors.messages',
            ],
        }
    }
]

# AUTHENTICATION SETTINGS
AUTHENTICATION_BACKENDS = (
    'django.contrib.auth.backends.ModelBackend',
)
LOGIN_REDIRECT_URL = '/'
LOGOUT_REDIRECT_URL = '/'

# STATIC SETTINGS
STATIC_URL = '/static/'
STATIC_ROOT = ROOT_DIR.joinpath('static')
