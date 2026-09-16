"""
Django settings for core project.
Optimizado para Railway + WhiteNoise + Cloudinary.
Django 4.2 LTS
"""

from pathlib import Path
import os
from dotenv import load_dotenv
from decouple import config

# ===============================
# BASE
# ===============================
load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent

FOODBACK_ENVIRONMENT = os.getenv(
    "FOODBACK_ENVIRONMENT",
    "development",
).strip().lower()

if FOODBACK_ENVIRONMENT not in {
    "development",
    "production",
}:
    raise RuntimeError(
        "FOODBACK_ENVIRONMENT inválido: "
        f"{FOODBACK_ENVIRONMENT!r}. "
        "Valores permitidos: development, production."
    )

IS_PRODUCTION = (
    FOODBACK_ENVIRONMENT == "production"
)

# ===============================
# SEGURIDAD
# ===============================

SECRET_KEY = os.getenv(
    "SECRET_KEY",
    "",
).strip()

if IS_PRODUCTION:
    if not SECRET_KEY:
        raise RuntimeError(
            "SECRET_KEY es obligatoria en producción."
        )

    if SECRET_KEY == "django-insecure-dev-key":
        raise RuntimeError(
            "SECRET_KEY insegura en producción."
        )

else:
    # Solo desarrollo local.
    if not SECRET_KEY:
        SECRET_KEY = "django-insecure-dev-key"


DEBUG = not IS_PRODUCTION


def _lista_env(
    nombre,
    default="",
):
    return [
        valor.strip()
        for valor in os.getenv(
            nombre,
            default,
        ).split(",")
        if valor.strip()
    ]


if IS_PRODUCTION:
    ALLOWED_HOSTS = _lista_env(
        "FOODBACK_ALLOWED_HOSTS"
    )

    if not ALLOWED_HOSTS:
        raise RuntimeError(
            "FOODBACK_ALLOWED_HOSTS es obligatorio "
            "en producción."
        )

    CSRF_TRUSTED_ORIGINS = _lista_env(
        "FOODBACK_CSRF_TRUSTED_ORIGINS"
    )

    if not CSRF_TRUSTED_ORIGINS:
        raise RuntimeError(
            "FOODBACK_CSRF_TRUSTED_ORIGINS es obligatorio "
            "en producción."
        )

else:
    ALLOWED_HOSTS = [
        "localhost",
        "127.0.0.1",
        "[::1]",
        "testserver",
        ".ngrok-free.app",
        ".ngrok-free.dev",
        ".devtunnels.ms",
        ".foodbacksv.com",
    ]

    CSRF_TRUSTED_ORIGINS = [
        "https://*.ngrok-free.app",
        "https://*.ngrok-free.dev",
        "https://*.devtunnels.ms",
        "https://foodbacksv.com",
        "https://*.foodbacksv.com",
    ]


SECURE_PROXY_SSL_HEADER = (
    "HTTP_X_FORWARDED_PROTO",
    "https",
)

SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"

# Audita rechazos CSRF sin guardar tokens/cookies ni cambiar
# la respuesta estándar 403 de Django.
CSRF_FAILURE_VIEW = "pedidos.audit.csrf_failure_view"

if IS_PRODUCTION:
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_CONTENT_TYPE_NOSNIFF = True

    # Railway/proxy termina TLS, pero Django debe
    # tratar toda la aplicación pública como HTTPS.
    SECURE_SSL_REDIRECT = True

    # Empezamos con HSTS corto. Cuando producción
    # haya sido validada podremos aumentarlo.
    SECURE_HSTS_SECONDS = 3600

    # No activamos todavía estas opciones más agresivas.
    SECURE_HSTS_INCLUDE_SUBDOMAINS = False
    SECURE_HSTS_PRELOAD = False

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



# =========================================================
# PASSWORD RESET
# =========================================================

FOODBACK_PASSWORD_RESET_CODE_TTL_SECONDS = config(
    "FOODBACK_PASSWORD_RESET_CODE_TTL_SECONDS",
    default=600,
    cast=int,
)

FOODBACK_PASSWORD_RESET_MAX_ATTEMPTS = config(
    "FOODBACK_PASSWORD_RESET_MAX_ATTEMPTS",
    default=5,
    cast=int,
)

FOODBACK_PASSWORD_RESET_GRANT_TTL_SECONDS = config(
    "FOODBACK_PASSWORD_RESET_GRANT_TTL_SECONDS",
    default=600,
    cast=int,
)

if not (
    300
    <= FOODBACK_PASSWORD_RESET_CODE_TTL_SECONDS
    <= 1800
):
    raise RuntimeError(
        "FOODBACK_PASSWORD_RESET_CODE_TTL_SECONDS "
        "debe estar entre 300 y 1800 segundos."
    )

if not (
    1
    <= FOODBACK_PASSWORD_RESET_MAX_ATTEMPTS
    <= 10
):
    raise RuntimeError(
        "FOODBACK_PASSWORD_RESET_MAX_ATTEMPTS "
        "debe estar entre 1 y 10."
    )

if not (
    300
    <= FOODBACK_PASSWORD_RESET_GRANT_TTL_SECONDS
    <= 1800
):
    raise RuntimeError(
        "FOODBACK_PASSWORD_RESET_GRANT_TTL_SECONDS "
        "debe estar entre 300 y 1800 segundos."
    )


FOODBACK_PASSWORD_RESET_REQUEST_IP_LIMIT = config(
    "FOODBACK_PASSWORD_RESET_REQUEST_IP_LIMIT",
    default=10,
    cast=int,
)

FOODBACK_PASSWORD_RESET_REQUEST_EMAIL_LIMIT = config(
    "FOODBACK_PASSWORD_RESET_REQUEST_EMAIL_LIMIT",
    default=3,
    cast=int,
)

FOODBACK_PASSWORD_RESET_VERIFY_IP_LIMIT = config(
    "FOODBACK_PASSWORD_RESET_VERIFY_IP_LIMIT",
    default=30,
    cast=int,
)

FOODBACK_PASSWORD_RESET_REQUEST_WINDOW_SECONDS = config(
    "FOODBACK_PASSWORD_RESET_REQUEST_WINDOW_SECONDS",
    default=900,
    cast=int,
)

FOODBACK_PASSWORD_RESET_REQUEST_BLOCK_SECONDS = config(
    "FOODBACK_PASSWORD_RESET_REQUEST_BLOCK_SECONDS",
    default=900,
    cast=int,
)


# =========================================================
# EMAIL
# =========================================================

EMAIL_BACKEND = config(
    "FOODBACK_EMAIL_BACKEND",
    default=(
        "django.core.mail.backends.smtp.EmailBackend"
        if IS_PRODUCTION
        else
        "django.core.mail.backends.console.EmailBackend"
    ),
)

DEFAULT_FROM_EMAIL = config(
    "FOODBACK_DEFAULT_FROM_EMAIL",
    default="no-reply@foodback.local",
)

EMAIL_HOST = config(
    "FOODBACK_EMAIL_HOST",
    default="",
)

EMAIL_PORT = config(
    "FOODBACK_EMAIL_PORT",
    default=587,
    cast=int,
)

EMAIL_HOST_USER = config(
    "FOODBACK_EMAIL_HOST_USER",
    default="",
)

EMAIL_HOST_PASSWORD = config(
    "FOODBACK_EMAIL_HOST_PASSWORD",
    default="",
)

EMAIL_USE_TLS = config(
    "FOODBACK_EMAIL_USE_TLS",
    default=True,
    cast=bool,
)

EMAIL_TIMEOUT = config(
    "FOODBACK_EMAIL_TIMEOUT",
    default=10,
    cast=int,
)


# =========================================================
# SECURITY INCIDENTS / FOUNDATION ALERTING
# =========================================================

# Ventana usada para correlacionar eventos equivalentes
# dentro del mismo incidente.
FOODBACK_SECURITY_INCIDENT_WINDOW_SECONDS = config(
    "FOODBACK_SECURITY_INCIDENT_WINDOW_SECONDS",
    default=300,
    cast=int,
)

# Tiempo mínimo entre avisos del mismo incidente.
# Evita tormentas de correo durante ataques sostenidos.
FOODBACK_SECURITY_ALERT_COOLDOWN_SECONDS = config(
    "FOODBACK_SECURITY_ALERT_COOLDOWN_SECONDS",
    default=300,
    cast=int,
)

# Opt-in explícito. En desarrollo permanece desactivado
# salvo que queramos probar el flujo de alertas.
FOODBACK_SECURITY_ALERTS_ENABLED = config(
    "FOODBACK_SECURITY_ALERTS_ENABLED",
    default=False,
    cast=bool,
)

# Uno o varios destinatarios separados por coma.
# Está pensado principalmente para Foundation/Superadmin.
FOODBACK_SECURITY_ALERT_EMAIL = config(
    "FOODBACK_SECURITY_ALERT_EMAIL",
    default="",
)

if not (
    60
    <= FOODBACK_SECURITY_INCIDENT_WINDOW_SECONDS
    <= 3600
):
    raise RuntimeError(
        "FOODBACK_SECURITY_INCIDENT_WINDOW_SECONDS debe estar "
        "entre 60 y 3600 segundos."
    )

if not (
    60
    <= FOODBACK_SECURITY_ALERT_COOLDOWN_SECONDS
    <= 86400
):
    raise RuntimeError(
        "FOODBACK_SECURITY_ALERT_COOLDOWN_SECONDS debe estar "
        "entre 60 y 86400 segundos."
    )

if (
    FOODBACK_SECURITY_ALERTS_ENABLED
    and not FOODBACK_SECURITY_ALERT_EMAIL.strip()
):
    raise RuntimeError(
        "FOODBACK_SECURITY_ALERT_EMAIL es obligatorio cuando "
        "FOODBACK_SECURITY_ALERTS_ENABLED=True."
    )

if (
    IS_PRODUCTION
    and FOODBACK_SECURITY_ALERTS_ENABLED
    and EMAIL_BACKEND
        == "django.core.mail.backends.console.EmailBackend"
):
    raise RuntimeError(
        "Las alertas de seguridad no pueden usar ConsoleEmailBackend "
        "en producción."
    )


# =========================================================
# AUDITORÍA / RETENCIÓN
# =========================================================

# Los eventos crudos ocupan más volumen que los incidentes resumidos.
# Conservamos auditoría 180 días e incidentes resueltos 365 días por
# defecto. Los incidentes activos nunca se purgan automáticamente.
FOODBACK_AUDIT_RETENTION_DAYS = config(
    "FOODBACK_AUDIT_RETENTION_DAYS",
    default=180,
    cast=int,
)

FOODBACK_SECURITY_INCIDENT_RETENTION_DAYS = config(
    "FOODBACK_SECURITY_INCIDENT_RETENTION_DAYS",
    default=365,
    cast=int,
)

FOODBACK_AUDIT_CLEANUP_BATCH_SIZE = config(
    "FOODBACK_AUDIT_CLEANUP_BATCH_SIZE",
    default=1000,
    cast=int,
)

if not (
    30
    <= FOODBACK_AUDIT_RETENTION_DAYS
    <= 3650
):
    raise RuntimeError(
        "FOODBACK_AUDIT_RETENTION_DAYS debe estar entre "
        "30 y 3650 días."
    )

if not (
    30
    <= FOODBACK_SECURITY_INCIDENT_RETENTION_DAYS
    <= 3650
):
    raise RuntimeError(
        "FOODBACK_SECURITY_INCIDENT_RETENTION_DAYS debe estar "
        "entre 30 y 3650 días."
    )

if (
    FOODBACK_SECURITY_INCIDENT_RETENTION_DAYS
    < FOODBACK_AUDIT_RETENTION_DAYS
):
    raise RuntimeError(
        "FOODBACK_SECURITY_INCIDENT_RETENTION_DAYS debe ser "
        "mayor o igual que FOODBACK_AUDIT_RETENTION_DAYS."
    )

if not (
    100
    <= FOODBACK_AUDIT_CLEANUP_BATCH_SIZE
    <= 10000
):
    raise RuntimeError(
        "FOODBACK_AUDIT_CLEANUP_BATCH_SIZE debe estar entre "
        "100 y 10000."
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
    'pedidos.middleware.AuditExceptionMiddleware',
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

DB_MODE = os.getenv(
    "FOODBACK_DB_MODE",
    "app",
).strip().lower()

DB_ROLES = {
    "app": (
        "FOODBACK_DB_APP_USER",
        "FOODBACK_DB_APP_PASSWORD",
    ),
    "migrator": (
        "FOODBACK_DB_MIGRATOR_USER",
        "FOODBACK_DB_MIGRATOR_PASSWORD",
    ),
    "test": (
        "FOODBACK_DB_TEST_USER",
        "FOODBACK_DB_TEST_PASSWORD",
    ),
}

if DB_MODE not in DB_ROLES:
    raise RuntimeError(
        f"FOODBACK_DB_MODE inválido: {DB_MODE!r}. "
        "Valores permitidos: app, migrator, test."
    )

if (
    IS_PRODUCTION
    and DB_MODE == "test"
):
    raise RuntimeError(
        "FOODBACK_DB_MODE='test' no está permitido "
        "en producción."
    )

user_env, password_env = (
    DB_ROLES[DB_MODE]
)

db_user = os.getenv(
    user_env
)

db_password = os.getenv(
    password_env
)

if not db_user or not db_password:
    raise RuntimeError(
        "Faltan credenciales PostgreSQL "
        f"para el modo {DB_MODE!r}. "
        f"Revisa {user_env} y {password_env}."
    )


if IS_PRODUCTION:
    db_name = os.getenv(
        "FOODBACK_DB_NAME"
    )

    db_host = os.getenv(
        "FOODBACK_DB_HOST"
    )

    db_port = os.getenv(
        "FOODBACK_DB_PORT"
    )

    valores_faltantes = [
        nombre
        for nombre, valor in (
            (
                "FOODBACK_DB_NAME",
                db_name,
            ),
            (
                "FOODBACK_DB_HOST",
                db_host,
            ),
            (
                "FOODBACK_DB_PORT",
                db_port,
            ),
        )
        if not valor
    ]

    if valores_faltantes:
        raise RuntimeError(
            "Faltan variables PostgreSQL "
            "de producción: "
            + ", ".join(
                valores_faltantes
            )
        )

else:
    db_name = os.getenv(
        "FOODBACK_DB_NAME",
        "foodback_local",
    )

    db_host = os.getenv(
        "FOODBACK_DB_HOST",
        "127.0.0.1",
    )

    db_port = os.getenv(
        "FOODBACK_DB_PORT",
        "5432",
    )


DATABASES = {
    "default": {
        "ENGINE":
            "django.db.backends.postgresql",

        "NAME":
            db_name,

        "USER":
            db_user,

        "PASSWORD":
            db_password,

        "HOST":
            db_host,

        "PORT":
            db_port,

        "CONN_MAX_AGE":
            600,
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

# Duración máxima absoluta de una sesión autenticada
# del personal de FoodBack.
#
# 54000 segundos = 15 horas.
FOODBACK_STAFF_SESSION_MAX_AGE = config(
    "FOODBACK_STAFF_SESSION_MAX_AGE",
    default=54000,
    cast=int,
)

if not (
    3600
    <= FOODBACK_STAFF_SESSION_MAX_AGE
    <= 86400
):
    raise RuntimeError(
        "FOODBACK_STAFF_SESSION_MAX_AGE debe estar "
        "entre 3600 y 86400 segundos."
    )

# Duración predeterminada de cookies de sesión.
SESSION_COOKIE_AGE = 54000

# No convertir la sesión en sliding expiration
# simplemente por recibir requests.
SESSION_SAVE_EVERY_REQUEST = False

# Cerrar el navegador o apagar la pantalla no debe
# terminar el turno prematuramente.
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

# =========================================================
# LOGGING OPERATIVO
# =========================================================

# Railway/Uvicorn capturan stdout/stderr. Mantenemos logs humanos
# compactos y dejamos los detalles investigables estructurados en BD
# mediante AuditEvent/SecurityIncident.
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "foodback": {
            "format": (
                "%(asctime)s %(levelname)s "
                "%(name)s %(message)s"
            ),
        },
    },
    "handlers": {
        "foodback_console": {
            "class": "logging.StreamHandler",
            "formatter": "foodback",
        },
    },
    "loggers": {
        "foodback.audit": {
            "handlers": ["foodback_console"],
            "level": "INFO",
            "propagate": False,
        },
        "foodback.security_incidents": {
            "handlers": ["foodback_console"],
            "level": "INFO",
            "propagate": False,
        },
        "foodback.views": {
            "handlers": ["foodback_console"],
            "level": "INFO",
            "propagate": False,
        },
        "foodback.runtime": {
            "handlers": ["foodback_console"],
            "level": "INFO",
            "propagate": False,
        },
    },
}
