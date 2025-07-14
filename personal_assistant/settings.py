import os
from pathlib import Path

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent


# Quick-start development settings - unsuitable for production
# See https://docs.djangoproject.com/en/5.2/howto/deployment/checklist/

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = 'django-insecure-uumrcu3+qqjweuz&l$)yt_n+tsl^colq%wic^m)^m#%zg2*+bs'

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = False

ALLOWED_HOSTS = ["*"]


# Application definition


# import dj_database_url

# DATABASES = {
#     'default': dj_database_url.config(conn_max_age=600, ssl_require=True)
# }


INSTALLED_APPS = [
    # 'jazzmin',
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'core',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
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

            ],
        },
    },
]



WSGI_APPLICATION = 'personal_assistant.wsgi.application'


# Database
# https://docs.djangoproject.com/en/5.2/ref/settings/#databases

# DATABASES = {
#     'default': {
#         'ENGINE': 'django.db.backends.sqlite3',
#         'NAME': BASE_DIR / 'db.sqlite3',
#     }
# }

# supabase database
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': 'postgres',  # Jina la database
        'USER': 'postgres.nkuvivgcokcfjyjnzcmw', 
        'PASSWORD': 'NyumbaChap', 
        'HOST': 'aws-0-eu-north-1.pooler.supabase.com', 
        'PORT': '5432',
         }
}


# Password validation
# https://docs.djangoproject.com/en/5.2/ref/settings/#auth-password-validators

AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]


# Internationalization
# https://docs.djangoproject.com/en/5.2/topics/i18n/

LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'Africa/Dar_es_Salaam'
USE_TZ = True
USE_I18N = True



# Static files (CSS, JavaScript, Images)
STATIC_URL = 'static/'

# Directory where collectstatic will collect static files for production
STATIC_ROOT = os.path.join(BASE_DIR, 'staticfiles')
# Additional locations of static files (optional, useful if you have global static files)
STATICFILES_DIRS = [
    BASE_DIR / "core/static",  # ← hii
]
STATICFILES_STORAGE = 'whitenoise.storage.CompressedManifestStaticFilesStorage'
MIDDLEWARE.insert(1, 'whitenoise.middleware.WhiteNoiseMiddleware')

# Default primary key field type
# https://docs.djangoproject.com/en/5.2/ref/settings/#default-auto-field

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
LOGIN_URL = '/login/'
LOGIN_REDIRECT_URL = '/'
#LOGIN_REDIRECT_URL = 'dashboard'  
LOGOUT_REDIRECT_URL = 'login'     # Baada ya logout, rudi login page


EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
EMAIL_HOST = "smtp.zoho.com"  # SMTP server ya Zoho
EMAIL_PORT = 587
EMAIL_USE_TLS = True
EMAIL_HOST_USER = "info@nyumbachap.online"  # Badilisha na email yako ya Zoho
EMAIL_HOST_PASSWORD = "Chipindi@123"  # Badilisha na password yako ya Zoho
DEFAULT_FROM_EMAIL = EMAIL_HOST_USER



# JAZZMIN_SETTINGS = {
#     "site_title": "wILife Admin",
#     "site_header": "wILife Dashboard",
#     "welcome_sign": "Welcome to wILife system",
#     "copyright": "© 2025 wILife",
#     "show_sidebar": True,
#     "navigation_expanded": True,
#     "hide_apps": [],
#     "order_with_respect_to": ["auth", "myapp"],
#     "custom_links": {
#         "auth": [{
#             "name": "Website Home",
#             "url": "/",
#             "icon": "fas fa-home",
#             "permissions": ["auth.view_user"]
#         }]
#     }
# }
