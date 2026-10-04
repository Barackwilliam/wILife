import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

# Load .env for local development. On Render, env vars come from the dashboard.
try:
    from dotenv import load_dotenv
    load_dotenv(BASE_DIR / ".env")
except ImportError:
    pass


def env(key, default=None, required=False):
    """Read an environment variable. Fail loudly at boot if a required one is missing."""
    value = os.environ.get(key, default)
    if required and not value:
        raise RuntimeError(
            f"Missing required environment variable: {key}. "
            f"Set it in .env locally, or in the Render dashboard in production."
        )
    return value


def env_bool(key, default=False):
    return str(os.environ.get(key, str(default))).strip().lower() in ("1", "true", "yes", "on")


# ---------------------------------------------------------------------------
# Core security
# ---------------------------------------------------------------------------

SECRET_KEY = env("DJANGO_SECRET_KEY", required=True)

DEBUG = env_bool("DJANGO_DEBUG", False)

# Comma-separated list, e.g. "wilife.onrender.com,localhost,127.0.0.1"
ALLOWED_HOSTS = ["*"]

CSRF_TRUSTED_ORIGINS = [
    o.strip() for o in env("DJANGO_CSRF_TRUSTED_ORIGINS", "").split(",") if o.strip()
]

if not DEBUG:
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True


# ---------------------------------------------------------------------------
# Applications
# ---------------------------------------------------------------------------

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'django.contrib.sitemaps',
    'core',
    'news',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'personal_assistant.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [os.path.join(BASE_DIR, 'templates')],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'core.context_processors.current_year',
                'core.context_processors.unread_notifications',
                'news.context_processors.news',
            ],
        },
    },
]

WSGI_APPLICATION = 'personal_assistant.wsgi.application'


# ---------------------------------------------------------------------------
# Database (Supabase Postgres)
# ---------------------------------------------------------------------------

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': env("DB_NAME", "postgres"),
        'USER': env("DB_USER", required=True),
        'PASSWORD': env("DB_PASSWORD", required=True),
        'HOST': env("DB_HOST", required=True),
        'PORT': env("DB_PORT", "5432"),
        'CONN_MAX_AGE': int(env("DB_CONN_MAX_AGE", "60")),
    }
}


# ---------------------------------------------------------------------------
# Agent configuration
# ---------------------------------------------------------------------------

# Shared secret protecting the /agent/tick/ endpoint. Generate a long random value.
AGENT_TICK_TOKEN = env("AGENT_TICK_TOKEN", "")
AGENT_ENABLED = env_bool("AGENT_ENABLED", True)
AGENT_TICK_BUDGET_SECONDS = int(env("AGENT_TICK_BUDGET_SECONDS", "20"))
AGENT_STALE_REMINDER_HOURS = int(env("AGENT_STALE_REMINDER_HOURS", "24"))
WHATSAPP_ENABLED = env_bool("WHATSAPP_ENABLED", True)
# baileys = Node bridge in whatsapp_bridge/ (no 24h window); meta = Cloud API
WHATSAPP_PROVIDER = env("WHATSAPP_PROVIDER", "baileys")
WHATSAPP_BRIDGE_URL = env("WHATSAPP_BRIDGE_URL", "")
WHATSAPP_BRIDGE_KEY = env("WHATSAPP_BRIDGE_KEY", "")
WHATSAPP_TOKEN = env("WHATSAPP_TOKEN", "")
WHATSAPP_PHONE_NUMBER_ID = env("WHATSAPP_PHONE_NUMBER_ID", "")
WHATSAPP_API_VERSION = env("WHATSAPP_API_VERSION", "v21.0")
AGENT_DEFAULT_RECIPIENT = env("AGENT_DEFAULT_RECIPIENT", "")
AGENT_DEFAULT_COUNTRY_CODE = env("AGENT_DEFAULT_COUNTRY_CODE", "255")

# Approvals
AGENT_APPROVAL_TTL_HOURS = int(env("AGENT_APPROVAL_TTL_HOURS", "24"))

# Brain (Groq)
AGENT_BRAIN_ENABLED = env_bool("AGENT_BRAIN_ENABLED", False)
GROQ_API_KEY = env("GROQ_API_KEY", "")
GROQ_MODEL = env("GROQ_MODEL", "llama-3.3-70b-versatile")

# JamiiTek connector
JAMIITEK_DB_DSN = env("JAMIITEK_DB_DSN", "")
JAMIITEK_OVERDUE_INVOICE_SQL = env("JAMIITEK_OVERDUE_INVOICE_SQL", "")
AGENT_INVOICE_WATCH_ENABLED = env_bool("AGENT_INVOICE_WATCH_ENABLED", True)
AGENT_INVOICE_WATCH_HOUR = int(env("AGENT_INVOICE_WATCH_HOUR", "8"))
AGENT_INVOICE_DRAFTS_ENABLED = env_bool("AGENT_INVOICE_DRAFTS_ENABLED", False)
AGENT_INVOICE_DRAFT_AFTER_DAYS = int(env("AGENT_INVOICE_DRAFT_AFTER_DAYS", "7"))
AGENT_INVOICE_DRAFT_MAX = int(env("AGENT_INVOICE_DRAFT_MAX", "3"))

# WhatsApp inbound webhook
WHATSAPP_VERIFY_TOKEN = env("WHATSAPP_VERIFY_TOKEN", "")
WHATSAPP_APP_SECRET = env("WHATSAPP_APP_SECRET", "")


# ---------------------------------------------------------------------------
# Passwords / i18n
# ---------------------------------------------------------------------------

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'Africa/Dar_es_Salaam'
USE_TZ = True
USE_I18N = True


# ---------------------------------------------------------------------------
# Static files
# ---------------------------------------------------------------------------

STATIC_URL = 'static/'
STATIC_ROOT = os.path.join(BASE_DIR, 'staticfiles')
_local_static = BASE_DIR / "core" / "static"
STATICFILES_DIRS = [_local_static] if _local_static.is_dir() else []
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}

# ---------------------------------------------------------------------------
# Media (news cover images) — Supabase Storage bucket via its S3 API.
# Same variables as the Tryvis project. Supabase dashboard → Storage → create a
# PUBLIC bucket, then Storage → S3 Connection for the endpoint and keys.
# When not configured, covers are kept in the database instead (still works,
# just heavier) — Render's own disk is wiped on every deploy, so never there.
# ---------------------------------------------------------------------------
SUPABASE_S3_ACCESS_KEY = env("SUPABASE_S3_ACCESS_KEY", "")
SUPABASE_S3_ENDPOINT = env("SUPABASE_S3_ENDPOINT", "")  # https://<project-ref>.supabase.co/storage/v1/s3
USE_SUPABASE_STORAGE = env_bool("USE_SUPABASE_STORAGE", bool(SUPABASE_S3_ACCESS_KEY and SUPABASE_S3_ENDPOINT))

if USE_SUPABASE_STORAGE:
    AWS_ACCESS_KEY_ID = SUPABASE_S3_ACCESS_KEY
    AWS_SECRET_ACCESS_KEY = env("SUPABASE_S3_SECRET_KEY", "")
    AWS_STORAGE_BUCKET_NAME = env("SUPABASE_BUCKET_NAME", "wilife-media")
    AWS_S3_ENDPOINT_URL = SUPABASE_S3_ENDPOINT
    AWS_S3_REGION_NAME = env("SUPABASE_S3_REGION", "eu-north-1")
    AWS_S3_ADDRESSING_STYLE = "path"
    AWS_DEFAULT_ACL = None
    AWS_QUERYSTRING_AUTH = False
    AWS_S3_FILE_OVERWRITE = False
    # Files never change once written (new names on regeneration) — let browsers and CDNs keep them.
    AWS_S3_OBJECT_PARAMETERS = {"CacheControl": "public, max-age=31536000, immutable"}
    # Links must use the PUBLIC object path, not the S3 API path (which returns 403 in a browser).
    _supabase_project = SUPABASE_S3_ENDPOINT.rstrip("/").removesuffix("/storage/v1/s3")
    AWS_S3_CUSTOM_DOMAIN = f'{_supabase_project.split("://", 1)[-1]}/storage/v1/object/public/{AWS_STORAGE_BUCKET_NAME}'
    MEDIA_URL = f"{_supabase_project}/storage/v1/object/public/{AWS_STORAGE_BUCKET_NAME}/"
    STORAGES["default"] = {"BACKEND": "storages.backends.s3.S3Storage"}

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
LOGIN_URL = '/login/'
LOGIN_REDIRECT_URL = '/dashboard/'
LOGOUT_REDIRECT_URL = 'login'


# ---------------------------------------------------------------------------
# Email
#
# NOTE: Render free web services block outbound ports 25, 465 and 587, so SMTP
# will NOT work there. Password-reset emails will silently fail on the free tier.
# Set EMAIL_BACKEND to the console backend when SMTP is unavailable, and rely on
# WhatsApp for anything that actually has to reach a human.
# ---------------------------------------------------------------------------

EMAIL_BACKEND = env("EMAIL_BACKEND", "django.core.mail.backends.smtp.EmailBackend")
EMAIL_HOST = env("EMAIL_HOST", "smtp.zoho.com")
EMAIL_PORT = int(env("EMAIL_PORT", "587"))
EMAIL_USE_TLS = env_bool("EMAIL_USE_TLS", True)
EMAIL_HOST_USER = env("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = env("EMAIL_HOST_PASSWORD", "")
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", EMAIL_HOST_USER)


# ---------------------------------------------------------------------------
# Logging — the agent runs unattended, so its logs are the only way to see it work
# ---------------------------------------------------------------------------

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "simple": {"format": "[{asctime}] {levelname} {name}: {message}", "style": "{"},
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "simple"},
    },
    "loggers": {
        "core.agent": {"handlers": ["console"], "level": "INFO", "propagate": False},
    },
    "root": {"handlers": ["console"], "level": "WARNING"},
}


# Telegram — preferred channel for messages to William (no 24-hour window)
TELEGRAM_ENABLED = env_bool("TELEGRAM_ENABLED", False)
TELEGRAM_BOT_TOKEN = env("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_WEBHOOK_SECRET = env("TELEGRAM_WEBHOOK_SECRET", "")
AGENT_TELEGRAM_CHAT_ID = env("AGENT_TELEGRAM_CHAT_ID", "")


# Self channel: email | telegram | whatsapp | all
AGENT_SELF_CHANNEL = env("AGENT_SELF_CHANNEL", "email")

# Email channel (Resend over HTTPS — SMTP ports are blocked on Render free)
EMAIL_CHANNEL_ENABLED = env_bool("EMAIL_CHANNEL_ENABLED", False)
RESEND_API_KEY = env("RESEND_API_KEY", "")
AGENT_EMAIL_FROM = env("AGENT_EMAIL_FROM", "")
AGENT_EMAIL_TO = env("AGENT_EMAIL_TO", "")

# ---------------------------------------------------------------------------
# wILife Habari (public news site)
# ---------------------------------------------------------------------------
SITE_URL = env("SITE_URL", "")                      # e.g. https://www.wlife.online — used in links sent by email/WhatsApp
if not SITE_URL and CSRF_TRUSTED_ORIGINS:
    # Links in messages must be absolute. Fall back to the custom domain the
    # site already trusts (prefer it over the *.onrender.com address).
    SITE_URL = next((o for o in CSRF_TRUSTED_ORIGINS if "onrender.com" not in o), CSRF_TRUSTED_ORIGINS[0])
SITE_URL = SITE_URL.rstrip("/")
NEWS_ENABLED = env_bool("NEWS_ENABLED", False)      # turn the daily drafting on
NEWS_DRAFT_HOUR = int(env("NEWS_DRAFT_HOUR", "5"))  # local hour drafting starts
NEWS_GIVE_UP_HOUR = int(env("NEWS_GIVE_UP_HOUR", "10"))
NEWS_STOP_HOUR = int(env("NEWS_STOP_HOUR", "16"))     # keep retrying missing slots until this hour
NEWS_MODEL = env("NEWS_MODEL", "")                  # defaults to GROQ_MODEL
JAMIITEK_WHATSAPP = env("JAMIITEK_WHATSAPP", "")    # e.g. 255712345678 for "Wasiliana nasi" buttons
JAMIITEK_EMAIL = env("JAMIITEK_EMAIL", "")
INDEXNOW_KEY = env("INDEXNOW_KEY", "")              # any 8–128 hex chars; served at /<key>.txt
NEWS_CACHE_SECONDS = int(env("NEWS_CACHE_SECONDS", "300"))
# Comma-separated official profiles (Facebook, X, Instagram, YouTube, LinkedIn) — used as schema.org sameAs
SOCIAL_LINKS = [u.strip() for u in env("SOCIAL_LINKS", "").split(",") if u.strip()]
try:
    import json as _json_cfg
    NEWS_FEEDS = _json_cfg.loads(env("NEWS_FEEDS", "") or "{}")  # {"tanzania": ["https://.../feed/"], ...}
except ValueError:
    NEWS_FEEDS = {}
