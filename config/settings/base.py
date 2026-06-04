"""
Shared Django settings — everything secret or environment-specific from env vars.
"""

import os
from pathlib import Path

import dj_database_url
from dotenv import load_dotenv

from config.logging import LOGGING  # noqa: F401 — used by Django

# Build paths relative to the repo root (one level above config/)
BASE_DIR = Path(__file__).resolve().parent.parent.parent

# Load .env from repo root
load_dotenv(BASE_DIR / ".env")

# --- Security ---

SECRET_KEY = os.environ["DJANGO_SECRET_KEY"]

ALLOWED_HOSTS = [
    h.strip()
    for h in os.environ.get("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")
    if h.strip()
]

CSRF_TRUSTED_ORIGINS = [
    o.strip()
    for o in os.environ.get("DJANGO_CSRF_TRUSTED_ORIGINS", "").split(",")
    if o.strip()
]

# --- Application definition ---

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # Local
    "accounts",
    "core",
    "integrations",
    "maintenance",
    "comms",
    "accounting",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

# --- Database ---

DATABASES = {
    "default": dj_database_url.config(
        default="postgresql://localhost:5432/vesta_dashboard",
        conn_max_age=600,
    ),
}

# --- Auth ---

AUTH_USER_MODEL = "accounts.User"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# --- i18n ---

LANGUAGE_CODE = "en-us"
TIME_ZONE = "America/New_York"
USE_I18N = True
USE_TZ = True

# --- Static files ---

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedStaticFilesStorage"},
}

# --- Misc ---

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --- Integration credentials ---

RENTVINE = {
    "SUBDOMAIN": os.environ.get("RENTVINE_SUBDOMAIN", ""),
    "API_KEY": os.environ.get("RENTVINE_API_KEY", ""),
    "API_SECRET": os.environ.get("RENTVINE_API_SECRET", ""),
}

PROPERTY_MELD = {
    "BASE_URL": os.environ.get(
        "PROPERTY_MELD_BASE_URL", "https://api.propertymeld.com/api/v2"
    ),
    "CLIENT_ID": os.environ.get("PROPERTY_MELD_CLIENT_ID", ""),
    "CLIENT_SECRET": os.environ.get("PROPERTY_MELD_CLIENT_SECRET", ""),
    "MANAGEMENT_ID": os.environ.get("PROPERTY_MELD_MANAGEMENT_ID", ""),
}

# --- AI ---

ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-6")

# --- SendGrid ---

SENDGRID_API_KEY = os.environ.get("SENDGRID_API_KEY", "")
COMMS_FROM_EMAIL = os.environ.get(
    "COMMS_FROM_EMAIL",
    "Vesta Property Management <support@vestapm.com>",
)
COMMS_CC_EMAIL = os.environ.get("COMMS_CC_EMAIL", "accounting@vestapm.com")
COMMS_SENDGRID_MONTHLY_TEMPLATE_ID = os.environ.get(
    "COMMS_SENDGRID_MONTHLY_TEMPLATE_ID",
    "d-3083295c8c354ccfb8365e9cec9760ae",
)
