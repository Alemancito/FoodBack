"""
Django settings for core project.
Optimizado para Railway + WhiteNoise + Cloudinary (FORMA CORRECTA)
Django 4.2 LTS
"""

from pathlib import Path
import os
import dj_database_url
from dotenv import load_dotenv

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
    'https://*.ngrok-free.app',  # Por si acaso te da una .app
    'https://*.ngrok-free.dev',  # ESTA ES LA QUE TE DIO TU CONSOLA
]

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
# ARCHIVOS ESTÁTICOS (ADMIN, CSS, JS)
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
# OTROS
# ===============================
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'


# --- CONFIGURACIÓN DE SESIÓN (MODO TURNO FIJO) ---

# 1. Duración exacta: 15 Horas en segundos
# Matemática: 15 horas * 60 minutos * 60 segundos = 54000
SESSION_COOKIE_AGE = 54000 

# 2. EL TRUCO: False
# Esto hace que el tiempo NO se reinicie con cada clic o pedido.
# El cronómetro arranca al login y corta a las 15h exactas, hagas lo que hagas.
SESSION_SAVE_EVERY_REQUEST = False 

# 3. (Opcional) Si cierran el navegador por error y vuelven a abrir,
# siguen logueados (siempre y cuando estén dentro de las 15h).
SESSION_EXPIRE_AT_BROWSER_CLOSE = False