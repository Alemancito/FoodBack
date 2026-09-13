"""
Django settings for core project.
Optimizado para Railway + WhiteNoise + Cloudinary.
Django 4.2 LTS
"""

from pathlib import Path
import os
import dj_database_url
from dotenv import load_dotenv
from decouple import config

# ===============================
# BASE
# ===============================
load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent

# ===============================
# SEGURIDAD
# ===============================
SECRET_KEY = os.getenv('SECRET_KEY', 'django-insecure-dev-key')

DEBUG = 'RAILWAY_ENVIRONMENT' not in os.environ

ALLOWED_HOSTS = ['*']

CSRF_TRUSTED_ORIGINS = [
    'https://*.railway.app',
    'https://*.up.railway.app',
    'https://*.ngrok-free.app',
    'https://*.ngrok-free.dev',
    'https://*.devtunnels.ms',
    'https://foodbacksv.com',
    'https://*.foodbacksv.com',
]

SECURE_PROXY_SSL_HEADER = (
    'HTTP_X_FORWARDED_PROTO',
    'https'
)

FOODBACK_TRUST_X_REAL_IP = config(
    "FOODBACK_TRUST_X_REAL_IP",
    default=False,
    cast=bool,
)


FOODBACK_BASE_DOMAIN = config(
    "FOODBACK_BASE_DOMAIN",
    default="foodbacksv.com",
)

FOODBACK_DEFAULT_TENANT_SLUG = config(
    "FOODBACK_DEFAULT_TENANT_SLUG",
    default="rancheritos",
)

# ===============================
# APLICACIONES
# ===============================
INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',

    # Cloudinary SOLO para media
    'cloudinary',

    # Tu app
    'pedidos',
]

# ===============================
# MIDDLEWARE
# ===============================
MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',

    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'pedidos.middleware.TenantContextMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

# ===============================
# URLS / TEMPLATES
# ===============================
ROOT_URLCONF = 'core.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'core.wsgi.application'

# ===============================
# BASE DE DATOS
# ===============================

if 'RAILWAY_ENVIRONMENT' in os.environ:
    # Producción: Railway administra DATABASE_URL.
    DATABASES = {
        'default': dj_database_url.config(
            default=os.getenv('DATABASE_URL'),
            conn_max_age=600,
        )
    }

else:
    # Desarrollo local: PostgreSQL con separación de privilegios.
    DB_MODE = os.getenv('FOODBACK_DB_MODE', 'app').strip().lower()

    DB_ROLES = {
        'app': (
            'FOODBACK_DB_APP_USER',
            'FOODBACK_DB_APP_PASSWORD',
        ),
        'migrator': (
            'FOODBACK_DB_MIGRATOR_USER',
            'FOODBACK_DB_MIGRATOR_PASSWORD',
        ),
        'test': (
            'FOODBACK_DB_TEST_USER',
            'FOODBACK_DB_TEST_PASSWORD',
        ),
    }

    if DB_MODE not in DB_ROLES:
        raise RuntimeError(
            f"FOODBACK_DB_MODE inválido: {DB_MODE!r}. "
            "Valores permitidos: app, migrator, test."
        )

    user_env, password_env = DB_ROLES[DB_MODE]

    db_user = os.getenv(user_env)
    db_password = os.getenv(password_env)

    if not db_user or not db_password:
        raise RuntimeError(
            f"Faltan credenciales PostgreSQL para el modo {DB_MODE!r}. "
            f"Revisa {user_env} y {password_env}."
        )

    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.postgresql',
            'NAME': os.getenv('FOODBACK_DB_NAME', 'foodback_local'),
            'USER': db_user,
            'PASSWORD': db_password,
            'HOST': os.getenv('FOODBACK_DB_HOST', '127.0.0.1'),
            'PORT': os.getenv('FOODBACK_DB_PORT', '5432'),
            'CONN_MAX_AGE': 600,
        }
    }

# ===============================
# PASSWORDS
# ===============================
AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

# ===============================
# INTERNACIONALIZACIÓN
# ===============================
LANGUAGE_CODE = 'es-sv'
TIME_ZONE = 'America/El_Salvador'
USE_I18N = True
USE_TZ = True

# ===============================
# ARCHIVOS ESTÁTICOS
# ===============================
STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
STATICFILES_STORAGE = 'whitenoise.storage.StaticFilesStorage'

# ===============================
# MEDIA (IMÁGENES → CLOUDINARY)
# ===============================
DEFAULT_FILE_STORAGE = 'cloudinary_storage.storage.MediaCloudinaryStorage'

CLOUDINARY_STORAGE = {
    'CLOUD_NAME': os.getenv('CLOUDINARY_CLOUD_NAME'),
    'API_KEY': os.getenv('CLOUDINARY_API_KEY'),
    'API_SECRET': os.getenv('CLOUDINARY_API_SECRET'),
}

MEDIA_URL = '/media/'

# ===============================
# WOMPI / FOODBACK
# ===============================
# Estas variables se leen desde .env con decouple.config en views.py:
# WOMPI_APP_ID
# WOMPI_API_SECRET
# WOMPI_AUTH_URL=https://id.wompi.sv/connect/token
# WOMPI_API_URL=https://api.wompi.sv/EnlacePago
# WOMPI_NOTIFICATION_EMAIL=correo@tuempresa.com
# FOODBACK_SUBSCRIPTION_PRICE=50.00
# PENDING_ORDER_EXPIRATION_MINUTES=45

# ===============================
# OTROS
# ===============================
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# --- CONFIGURACIÓN DE SESIÓN (MODO TURNO FIJO) ---
SESSION_COOKIE_AGE = 54000
SESSION_SAVE_EVERY_REQUEST = False
SESSION_EXPIRE_AT_BROWSER_CLOSE = False




# =========================================================
# FOODBACK - RATE LIMITS
# =========================================================

FOODBACK_LOGIN_IP_LIMIT = config(
    "FOODBACK_LOGIN_IP_LIMIT",
    default=30,
    cast=int,
)

FOODBACK_LOGIN_USER_LIMIT = config(
    "FOODBACK_LOGIN_USER_LIMIT",
    default=8,
    cast=int,
)

FOODBACK_LOGIN_WINDOW_SECONDS = config(
    "FOODBACK_LOGIN_WINDOW_SECONDS",
    default=600,
    cast=int,
)

FOODBACK_LOGIN_BLOCK_SECONDS = config(
    "FOODBACK_LOGIN_BLOCK_SECONDS",
    default=900,
    cast=int,
)

# =========================================================
# FOODBACK - CHECKOUT RATE LIMIT
# =========================================================

FOODBACK_CHECKOUT_SESSION_LIMIT = config(
    "FOODBACK_CHECKOUT_SESSION_LIMIT",
    default=10,
    cast=int,
)

FOODBACK_CHECKOUT_IP_LIMIT = config(
    "FOODBACK_CHECKOUT_IP_LIMIT",
    default=120,
    cast=int,
)

FOODBACK_CHECKOUT_WINDOW_SECONDS = config(
    "FOODBACK_CHECKOUT_WINDOW_SECONDS",
    default=600,
    cast=int,
)

FOODBACK_CHECKOUT_BLOCK_SECONDS = config(
    "FOODBACK_CHECKOUT_BLOCK_SECONDS",
    default=600,
    cast=int,
)

# =========================================================
# FOODBACK - WOMPI PAYMENT START RATE LIMIT
# =========================================================

FOODBACK_WOMPI_START_SESSION_LIMIT = config(
    "FOODBACK_WOMPI_START_SESSION_LIMIT",
    default=10,
    cast=int,
)

FOODBACK_WOMPI_START_IP_LIMIT = config(
    "FOODBACK_WOMPI_START_IP_LIMIT",
    default=120,
    cast=int,
)

FOODBACK_WOMPI_START_WINDOW_SECONDS = config(
    "FOODBACK_WOMPI_START_WINDOW_SECONDS",
    default=600,
    cast=int,
)

FOODBACK_WOMPI_START_BLOCK_SECONDS = config(
    "FOODBACK_WOMPI_START_BLOCK_SECONDS",
    default=600,
    cast=int,
)

# =========================================================
# FOODBACK - RESUME PAYMENT RATE LIMIT
# =========================================================

FOODBACK_PAYMENT_RESUME_SESSION_LIMIT = config(
    "FOODBACK_PAYMENT_RESUME_SESSION_LIMIT",
    default=10,
    cast=int,
)

FOODBACK_PAYMENT_RESUME_IP_LIMIT = config(
    "FOODBACK_PAYMENT_RESUME_IP_LIMIT",
    default=120,
    cast=int,
)

FOODBACK_PAYMENT_RESUME_WINDOW_SECONDS = config(
    "FOODBACK_PAYMENT_RESUME_WINDOW_SECONDS",
    default=600,
    cast=int,
)

FOODBACK_PAYMENT_RESUME_BLOCK_SECONDS = config(
    "FOODBACK_PAYMENT_RESUME_BLOCK_SECONDS",
    default=600,
    cast=int,
)

# =========================================================
# FOODBACK - SUBSCRIPTION PAYMENT RATE LIMIT
# =========================================================

FOODBACK_SUBSCRIPTION_PAYMENT_USER_LIMIT = config(
    "FOODBACK_SUBSCRIPTION_PAYMENT_USER_LIMIT",
    default=10,
    cast=int,
)

FOODBACK_SUBSCRIPTION_PAYMENT_IP_LIMIT = config(
    "FOODBACK_SUBSCRIPTION_PAYMENT_IP_LIMIT",
    default=60,
    cast=int,
)

FOODBACK_SUBSCRIPTION_PAYMENT_WINDOW_SECONDS = config(
    "FOODBACK_SUBSCRIPTION_PAYMENT_WINDOW_SECONDS",
    default=600,
    cast=int,
)

FOODBACK_SUBSCRIPTION_PAYMENT_BLOCK_SECONDS = config(
    "FOODBACK_SUBSCRIPTION_PAYMENT_BLOCK_SECONDS",
    default=600,
    cast=int,
)

# =========================================================
# FOODBACK - WOMPI WEBHOOK
# =========================================================

FOODBACK_WOMPI_WEBHOOK_MAX_BYTES = config(
    "FOODBACK_WOMPI_WEBHOOK_MAX_BYTES",
    default=262144,
    cast=int,
)

# =========================================================
# FOODBACK - PENDING ORDER ACTIONS RATE LIMIT
# =========================================================

FOODBACK_PENDING_ORDER_ACTION_SESSION_LIMIT = config(
    "FOODBACK_PENDING_ORDER_ACTION_SESSION_LIMIT",
    default=10,
    cast=int,
)

FOODBACK_PENDING_ORDER_ACTION_IP_LIMIT = config(
    "FOODBACK_PENDING_ORDER_ACTION_IP_LIMIT",
    default=120,
    cast=int,
)

FOODBACK_PENDING_ORDER_ACTION_WINDOW_SECONDS = config(
    "FOODBACK_PENDING_ORDER_ACTION_WINDOW_SECONDS",
    default=600,
    cast=int,
)

FOODBACK_PENDING_ORDER_ACTION_BLOCK_SECONDS = config(
    "FOODBACK_PENDING_ORDER_ACTION_BLOCK_SECONDS",
    default=600,
    cast=int,
)