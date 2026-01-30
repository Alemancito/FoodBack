"""
Django settings for core project.
Optimized for Railway & Cloudinary by NovaCode Studio 🦁
Django Version: 6.0.1
"""

from pathlib import Path
import os
import dj_database_url # <--- Necesario para Base de Datos
from dotenv import load_dotenv

# Cargar variables de entorno (.env)
load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent

# --- SEGURIDAD ---
# Si no hay clave en .env, usa una por defecto (SOLO para desarrollo)
SECRET_KEY = os.getenv('SECRET_KEY', 'django-insecure-dev-key-change-me-in-prod')

# DEBUG: True en local, False en Producción (si existe RAILWAY_ENVIRONMENT)
DEBUG = 'RAILWAY_ENVIRONMENT' not in os.environ

ALLOWED_HOSTS = ['*']

CSRF_TRUSTED_ORIGINS = [
    'https://*.ngrok-free.dev', 
    'https://*.ngrok-free.app',
    'https://*.railway.app', 
    'https://*.up.railway.app'
]

# --- APLICACIONES ---
INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    
    # 1. Cloudinary Storage (Arriba de staticfiles)
    'cloudinary_storage',
    'django.contrib.staticfiles',
    # 2. Cloudinary Lib (Abajo de staticfiles)
    'cloudinary',
    
    'pedidos', # Tu app
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    
    # --- WHITENOISE (Motor de archivos estáticos) ---
    "whitenoise.middleware.WhiteNoiseMiddleware",  # <--- VITAL
    
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'core.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'], # <--- Asegúrate de que apunte a tu carpeta templates
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

# --- BASE DE DATOS (HÍBRIDA) ---
# Usa SQLite en tu PC y PostgreSQL en Railway automáticamente
DATABASES = {
    'default': dj_database_url.config(
        default='sqlite:///' + str(BASE_DIR / 'db.sqlite3'),
        conn_max_age=600
    )
}

# --- VALIDACIÓN DE PASSWORD ---
AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

# --- INTERNACIONALIZACIÓN ---
LANGUAGE_CODE = 'es-mx' # Puesto en Español México
TIME_ZONE = 'America/Mexico_City' # Ajusta a tu zona horaria
USE_I18N = True
USE_TZ = True

# --- ARCHIVOS ESTÁTICOS (CSS, JS) ---
STATIC_URL = 'static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
STATICFILES_DIRS = [BASE_DIR / 'static'] # Si tienes carpeta static global

# --- CONFIGURACIÓN MAESTRA DE ALMACENAMIENTO (Django 5.0 / 6.0) ---
# Aquí es donde ocurre la magia para Cloudinary y WhiteNoise
STORAGES = {
    # 1. Archivos Estáticos (CSS/JS) -> WhiteNoise (Comprimido y Rápido)
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
    },
    # 2. Archivos Media (Imágenes subidas) -> Cloudinary (Nube)
    "default": {
        "BACKEND": "cloudinary_storage.storage.MediaCloudinaryStorage",
    },
}

# --- CONFIGURACIÓN CLOUDINARY ---
CLOUDINARY_STORAGE = {
    'CLOUD_NAME': os.getenv('CLOUDINARY_CLOUD_NAME'),
    'API_KEY': os.getenv('CLOUDINARY_API_KEY'),
    'API_SECRET': os.getenv('CLOUDINARY_API_SECRET'),
}

MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'