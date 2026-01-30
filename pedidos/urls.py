from django.urls import path
from . import views
from .views import obtener_ubicacion_ip, CustomLoginView, logout_view # <--- NUEVOS IMPORTS

urlpatterns = [
    path('', views.menu_view, name='menu'),
    # Rutas para acciones del carrito
    path('agregar/<int:producto_id>/', views.cart_add, name='add_to_cart'),
    path('limpiar/', views.cart_clear, name='clean_cart'),
    path('checkout/', views.checkout_view, name='checkout'),
    path('exito/<int:pedido_id>/', views.pedido_exito_view, name='pedido_exito'),
    
    # --- RUTAS DE AUTENTICACIÓN (NUEVAS) ---
    path('login/', CustomLoginView.as_view(), name='login_custom'),
    path('logout/', logout_view, name='logout'),

    # Dashboards (Ahora protegidos)
    path('dashboard/', views.dashboard_admin_view, name='dashboard_admin'),
    path('reparto/', views.dashboard_delivery_view, name='dashboard_delivery'),
    
    path('eliminar-item/<str:producto_id>/', views.eliminar_item_carrito, name='eliminar_item'),
    path('api/geo-ip/', obtener_ubicacion_ip, name='geo_ip'),
    path('pagar/<int:pedido_id>/', views.pagar_wompi_view, name='pagar_wompi'),
    path('wompi-respuesta/', views.wompi_respuesta_view, name='wompi_respuesta'),
    path('dashboard/settings/', views.admin_settings_view, name='admin_settings'),
    path('dashboard/settings/eliminar/<int:excepcion_id>/', views.eliminar_excepcion_view, name='eliminar_excepcion'),
    path('pedido/<int:pedido_id>/rastrear/', views.order_tracker_view, name='order_tracker'),
    path('api/pedido/<int:pedido_id>/status/', views.api_order_status, name='api_order_status'),
    path('dashboard/metricas/', views.dashboard_metrics_view, name='dashboard_metrics'),
    path('mi-perfil/', views.perfil_usuario_view, name='perfil_usuario'),

]

