"""
Django settings for core project.
Optimized for Railway & Cloudinary by NovaCode Studio 🦁
Django Version: 6.0.1
"""

from pathlib import Path
import os
import dj_database_url
from dotenv import load_dotenv

# Cargar variables de entorno (.env)
load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent

# --- SEGURIDAD ---
SECRET_KEY = os.getenv('SECRET_KEY', 'django-insecure-dev-key-change-me-in-prod')
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
    
    # 1. Cloudinary Storage (Antes de staticfiles)
    'cloudinary_storage',
    'django.contrib.staticfiles',
    # 2. Cloudinary Lib (Después de staticfiles)
    'cloudinary',
    
    'pedidos', # Tu app (Aquí es donde tienes tu carpeta static real)
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    # --- WHITENOISE (Motor de archivos estáticos) ---
    "whitenoise.middleware.WhiteNoiseMiddleware",
    
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

# --- BASE DE DATOS ---
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
LANGUAGE_CODE = 'es-mx'
TIME_ZONE = 'America/Mexico_City'
USE_I18N = True
USE_TZ = True

# --- ARCHIVOS ESTÁTICOS (CSS, JS) ---
STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'

# IMPORTANTE: Comenté esta línea porque tu carpeta 'static' está dentro de 'pedidos',
# no en la raíz. Django buscará automáticamente dentro de 'pedidos/static'.
# STATICFILES_DIRS = [BASE_DIR / 'static'] 

# --- CONFIGURACIÓN MAESTRA (Django 6.0) ---
STORAGES = {
    "staticfiles": {
        # CAMBIO AQUÍ: Quitamos "Compressed" del nombre
        "BACKEND": "whitenoise.storage.ManifestStaticFilesStorage",
    },
    "default": {
        "BACKEND": "cloudinary_storage.storage.MediaCloudinaryStorage",
    },
}

# --- PARCHE DE COMPATIBILIDAD ---
# CAMBIO AQUÍ: También quitamos "Compressed"
STATICFILES_STORAGE = "whitenoise.storage.ManifestStaticFilesStorage"

# --- CONFIGURACIÓN CLOUDINARY ---
CLOUDINARY_STORAGE = {
    'CLOUD_NAME': os.getenv('CLOUDINARY_CLOUD_NAME'),
    'API_KEY': os.getenv('CLOUDINARY_API_KEY'),
    'API_SECRET': os.getenv('CLOUDINARY_API_SECRET'),
}