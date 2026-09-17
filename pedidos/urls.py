from django.urls import path

from . import views
from .api_errors import professional_api_endpoint
from .support import support_report_error_view
from .views import CustomLoginView, logout_view
from .views import pagar_suscripcion_view, wompi_suscripcion_respuesta_view

urlpatterns = [
    path('', views.menu_view, name='menu'),
    path('agregar/<int:producto_id>/', views.cart_add, name='add_to_cart'),
    path('limpiar/', views.cart_clear, name='clean_cart'),
    path('eliminar-item/<str:producto_id>/', views.eliminar_item_carrito, name='eliminar_item'),
    path(
        'soporte/reportar-error/',
        support_report_error_view,
        name='support_report_error',
    ),
    path('checkout/', views.checkout_view, name='checkout'),
    path('exito/<uuid:tracking_token>/', views.pedido_exito_view, name='pedido_exito'),
    path('pedido/<uuid:tracking_token>/rastrear/', views.order_tracker_view, name='order_tracker'),
    path('pedido/<uuid:tracking_token>/retomar-pago/', views.retomar_pago_view, name='retomar_pago'),
    path('pedido/<uuid:tracking_token>/cancelar-pendiente/', views.cancelar_pedido_pendiente_view, name='cancelar_pedido_pendiente'),
    path('dashboard/cambiar-sucursal/', views.cambiar_sucursal_view, name='cambiar_sucursal'),
    path('pedido/<uuid:tracking_token>/ocultar-pendiente/', views.ocultar_pedido_pendiente_view, name='ocultar_pedido_pendiente'),
    path('login/', CustomLoginView.as_view(), name='login_custom'),
    path('logout/', logout_view, name='logout'),
    path('password-reset/', views.password_reset_request_view, name='password_reset_request'),
    path('password-reset/verificar/', views.password_reset_verify_view, name='password_reset_verify'),
    path('password-reset/nueva/', views.password_reset_new_view, name='password_reset_new'),
    path('dashboard/', views.dashboard_admin_view, name='dashboard_admin'),
    path('reparto/', views.dashboard_delivery_view, name='dashboard_delivery'),
    path(
        'api/pedido/<uuid:tracking_token>/status/',
        professional_api_endpoint(views.api_order_status),
        name='api_order_status',
    ),
    path(
        'api/dashboard/sync/',
        professional_api_endpoint(views.api_dashboard_admin_sync),
        name='api_dashboard_admin_sync',
    ),
    path(
        'api/reparto/sync/',
        professional_api_endpoint(views.api_delivery_sync),
        name='api_delivery_sync',
    ),
    path('pagar/<uuid:tracking_token>/', views.pagar_wompi_view, name='pagar_wompi'),
    path('wompi-respuesta/', views.wompi_respuesta_view, name='wompi_respuesta'),
    path('wompi-webhook/', views.wompi_webhook_view, name='wompi_webhook'),
    path('dashboard/settings/', views.admin_settings_view, name='admin_settings'),
    path('dashboard/settings/eliminar/<int:excepcion_id>/', views.eliminar_excepcion_view, name='eliminar_excepcion'),
    path('dashboard/metricas/', views.dashboard_metrics_view, name='dashboard_metrics'),
    path('mi-perfil/', views.perfil_usuario_view, name='perfil_usuario'),
    path('pagar-suscripcion/', pagar_suscripcion_view, name='pagar_suscripcion'),
    path('wompi-suscripcion-respuesta/', wompi_suscripcion_respuesta_view, name='wompi_suscripcion_respuesta'),
]
