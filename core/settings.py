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
DATABASES = {
    'default': dj_database_url.config(
        default='sqlite:///' + str(BASE_DIR / 'db.sqlite3'),
        conn_max_age=600
    )
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