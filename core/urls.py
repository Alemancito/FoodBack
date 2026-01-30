from django.contrib import admin
from django.urls import path, include # <--- 1. IMPORTANTE: 'include' debe estar aquí
from django.conf import settings
from django.conf.urls.static import static

urlpatterns = [
    path('admin/', admin.site.urls),
    # 2. IMPORTANTE: Esto le dice a Django "La portada es la app de pedidos"
    path('', include('pedidos.urls')), 
]

# Configuración para que se vean las fotos de las hamburguesas
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)