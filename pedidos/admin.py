from django.contrib import admin
from django.utils.html import format_html
from django.utils.safestring import mark_safe
from decouple import config

from .models import (
    Categoria,
    Producto,
    Cliente,
    Pedido,
    DetallePedido,
    ConfiguracionNegocio,
    DiaEspecial,
    OpcionProducto,
    Extra,
    PagoWompi,
    AuditEvent,
    SecurityIncident,
)


@admin.register(SecurityIncident)
class SecurityIncidentAdmin(admin.ModelAdmin):
    """
    Vista temporal read-only de incidentes correlacionados.

    La gestión definitiva (reconocer/resolver/investigar) vivirá en
    Foundation/Superadmin; aquí evitamos cambios manuales sin auditoría.
    """

    list_display = (
        "ultimo_visto_en",
        "estado",
        "severidad",
        "titulo",
        "contador_eventos",
        "evento_clave",
        "ip",
        "tenant_nombre",
        "sucursal_nombre",
        "notificaciones_enviadas",
    )

    list_filter = (
        "estado",
        "severidad",
        "categoria",
        "evento_clave",
        "ultimo_visto_en",
    )

    search_fields = (
        "public_id",
        "fingerprint",
        "titulo",
        "descripcion",
        "evento_clave",
        "actor_username",
        "actor_role",
        "tenant_nombre",
        "sucursal_nombre",
        "ip",
        "ruta",
    )

    ordering = (
        "-ultimo_visto_en",
    )

    list_per_page = 100

    def get_readonly_fields(
        self,
        request,
        obj=None,
    ):
        return tuple(
            field.name
            for field in self.model._meta.fields
        )

    def has_add_permission(
        self,
        request,
    ):
        return False

    def has_change_permission(
        self,
        request,
        obj=None,
    ):
        return False

    def has_delete_permission(
        self,
        request,
        obj=None,
    ):
        return False


@admin.register(AuditEvent)
class AuditEventAdmin(admin.ModelAdmin):
    """
    Vista interna temporal de auditoría.

    El dashboard Foundation/Superadmin tendrá después una
    interfaz propia, pero desde esta fase podemos investigar
    eventos sin tocar código ni editar registros históricos.
    """

    list_display = (
        "creado_en",
        "severidad",
        "categoria",
        "evento",
        "resultado",
        "actor_username",
        "actor_role",
        "tenant_nombre",
        "sucursal_nombre",
        "ip",
    )

    list_filter = (
        "severidad",
        "categoria",
        "resultado",
        "fuente",
        "actor_role",
        "creado_en",
    )

    search_fields = (
        "evento",
        "descripcion",
        "actor_username",
        "tenant_nombre",
        "sucursal_nombre",
        "ip",
        "request_id",
        "public_id",
        "fingerprint",
        "objeto_tipo",
        "objeto_id",
    )

    ordering = (
        "-creado_en",
    )

    list_per_page = 100

    def get_readonly_fields(
        self,
        request,
        obj=None,
    ):
        return tuple(
            field.name
            for field in self.model._meta.fields
        )

    def has_add_permission(
        self,
        request,
    ):
        return False

    def has_change_permission(
        self,
        request,
        obj=None,
    ):
        return False

    def has_delete_permission(
        self,
        request,
        obj=None,
    ):
        return False


class OpcionProductoInline(admin.TabularInline):
    model = OpcionProducto
    extra = 1


@admin.register(Extra)
class ExtraAdmin(admin.ModelAdmin):
    list_display = ('nombre', 'precio', 'disponible')
    list_editable = ('precio', 'disponible')
    search_fields = ('nombre',)


@admin.register(Producto)
class ProductoAdmin(admin.ModelAdmin):
    list_display = ('mostrar_imagen', 'nombre', 'categoria', 'precio', 'disponible')
    list_filter = ('categoria', 'disponible')
    search_fields = ('nombre',)
    list_editable = ('precio', 'disponible')
    filter_horizontal = ('extras',)
    inlines = [OpcionProductoInline]

    def mostrar_imagen(self, obj):
        if obj.imagen:
            return format_html(
                '<img src="{}" width="45" height="45" style="border-radius:8px; object-fit:cover;" />',
                obj.imagen.url
            )
        return "❌"

    mostrar_imagen.short_description = "Foto"


class DetallePedidoInline(admin.TabularInline):
    model = DetallePedido
    extra = 0
    readonly_fields = ('subtotal',)
    filter_horizontal = ('extras',)


@admin.register(Pedido)
class PedidoAdmin(admin.ModelAdmin):
    list_display = (
        'id',
        'cliente_info',
        'estado_color',
        'metodo_pago',
        'pago_info',
        'status_gps',
        'acciones_mapa',
        'total_final',
        'fecha_creacion',
    )

    list_filter = (
        'estado',
        'metodo_pago',
        'fecha_creacion',
        'es_pedido_whatsapp',
        'pago_verificado',
    )

    search_fields = (
        'id',
        'cliente__nombre',
        'cliente__apellido',
        'cliente__telefono',
        'wompi_referencia',
        'wompi_id_transaccion',
    )

    inlines = [DetallePedidoInline]

    readonly_fields = (
        'total_productos',
        'comision_plataforma',
        'total_final',
        'latitud',
        'longitud',
        'wompi_referencia',
        'wompi_id_enlace',
        'wompi_url_enlace',
        'wompi_id_transaccion',
        'pago_verificado',
        'fecha_pago_verificado',
        'mapa_visual',
        'fecha_creacion',
        'actualizado_en',
    )

    def cliente_info(self, obj):
        nombre = f"{obj.cliente.nombre} {obj.cliente.apellido}".strip()
        return f"{nombre} ({obj.cliente.telefono})"

    cliente_info.short_description = "Cliente"

    def estado_color(self, obj):
        colores = {
            'PENDIENTE': '#f59e0b',
            'RECIBIDO': '#2563eb',
            'COCINA': '#dc2626',
            'RUTA': '#7c3aed',
            'ENTREGADO': '#16a34a',
            'PROBLEMA': '#ea580c',
            'CANCELADO': '#6b7280',
        }
        color = colores.get(obj.estado, '#374151')
        return format_html(
            '<span style="background:{}; color:white; padding:5px 10px; border-radius:999px; font-weight:700;">{}</span>',
            color,
            obj.get_estado_display()
        )

    estado_color.short_description = "Estado"

    def pago_info(self, obj):
        if obj.metodo_pago == 'EFECTIVO':
            return mark_safe('<span style="color:#16a34a; font-weight:700;">💵 Efectivo</span>')

        aprobado = obj.pago_verificado or obj.pagos_wompi.filter(estado='APROBADO').exists() or bool(obj.wompi_id_transaccion)

        if aprobado:
            return mark_safe('<span style="color:#16a34a; font-weight:700;">✅ Tarjeta verificada</span>')

        if obj.estado == 'PENDIENTE':
            return mark_safe('<span style="color:#f59e0b; font-weight:700;">⏳ Tarjeta pendiente</span>')

        return mark_safe('<span style="color:#ea580c; font-weight:700;">⚠️ Revisar pago</span>')

    pago_info.short_description = "Pago"

    def status_gps(self, obj):
        if obj.latitud and obj.longitud:
            return mark_safe('<span style="color:#16a34a; font-weight:bold;">📍 OK</span>')
        return mark_safe('<span style="color:#9ca3af;">Sin GPS</span>')

    status_gps.short_description = "GPS"

    def acciones_mapa(self, obj):
        if obj.latitud and obj.longitud:
            url_google = f"https://www.google.com/maps/search/?api=1&query={obj.latitud},{obj.longitud}"
            url_waze = f"https://waze.com/ul?ll={obj.latitud},{obj.longitud}&navigate=yes"

            return format_html(
                '<a href="{}" target="_blank" style="background:#4285F4; color:white; padding:5px 9px; border-radius:6px; text-decoration:none; margin-right:5px; font-weight:bold;">🗺️ Maps</a>'
                '<a href="{}" target="_blank" style="background:#FECC00; color:black; padding:5px 9px; border-radius:6px; text-decoration:none; font-weight:bold;">🚗 Waze</a>',
                url_google,
                url_waze
            )

        return "-"

    acciones_mapa.short_description = "Navegar"

    def mapa_visual(self, obj):
        if obj.latitud and obj.longitud:
            api_key = config('GOOGLE_MAPS_API_KEY', default='')

            if api_key:
                return format_html(
                    '<iframe width="100%" height="350" frameborder="0" style="border:1px solid #ddd; border-radius: 12px;" '
                    'src="https://www.google.com/maps/embed/v1/place?key={}&q={},{}&zoom=16"></iframe>',
                    api_key,
                    obj.latitud,
                    obj.longitud
                )

            return "⚠️ Falta configurar GOOGLE_MAPS_API_KEY en .env"

        return "No hay ubicación registrada"

    mapa_visual.short_description = "Ubicación exacta"


@admin.register(PagoWompi)
class PagoWompiAdmin(admin.ModelAdmin):
    list_display = (
        'id',
        'mostrar_tipo',
        'referencia',
        'estado_color',
        'mostrar_pedido',
        'monto',
        'id_transaccion',
        'fecha_creacion',
    )

    list_filter = ('tipo', 'estado', 'es_aprobada', 'fecha_creacion')
    search_fields = ('referencia', 'id_transaccion', 'pedido__id', 'pedido__cliente__telefono')
    readonly_fields = (
        'tipo',
        'pedido',
        'configuracion_negocio',
        'referencia',
        'id_enlace',
        'url_enlace',
        'id_transaccion',
        'monto',
        'estado',
        'es_aprobada',
        'raw_creacion',
        'raw_redirect',
        'raw_webhook',
        'ultimo_error',
        'fecha_creacion',
        'fecha_actualizacion',
        'fecha_aprobacion',
    )

    def mostrar_tipo(self, obj):
        if obj.tipo == 'SUSCRIPCION':
            return "💼 Suscripción"
        return "🍔 Pedido"

    mostrar_tipo.short_description = "Tipo"

    def estado_color(self, obj):
        colores = {
            'CREADO': '#6b7280',
            'PENDIENTE': '#f59e0b',
            'APROBADO': '#16a34a',
            'RECHAZADO': '#dc2626',
            'CANCELADO': '#6b7280',
            'ERROR': '#dc2626',
        }
        color = colores.get(obj.estado, '#374151')
        return format_html(
            '<span style="background:{}; color:white; padding:5px 10px; border-radius:999px; font-weight:700;">{}</span>',
            color,
            obj.get_estado_display()
        )

    estado_color.short_description = "Estado"

    def mostrar_pedido(self, obj):
        pedido = obj.pedido

        if not pedido and obj.referencia.startswith('ORDEN-'):
            try:
                pedido_id = int(obj.referencia.split('-')[1])
                pedido = Pedido.objects.filter(id=pedido_id).first()
            except Exception:
                pedido = None

        if pedido:
            return format_html(
                '<a href="/admin/pedidos/pedido/{}/change/">Pedido #{}</a>',
                pedido.id,
                pedido.id
            )

        return "-"

    mostrar_pedido.short_description = "Pedido"


@admin.register(ConfiguracionNegocio)
class ConfigAdmin(admin.ModelAdmin):
    list_display = (
        'nombre_negocio',
        'fecha_vencimiento',
        'hora_apertura',
        'hora_cierre',
    )

    def has_add_permission(self, request):
        if self.model.objects.exists():
            return False
        return super().has_add_permission(request)


@admin.register(DiaEspecial)
class DiaEspecialAdmin(admin.ModelAdmin):
    list_display = ('fecha', 'abierto', 'motivo')
    list_filter = ('abierto',)
    ordering = ('fecha',)


@admin.register(Categoria)
class CategoriaAdmin(admin.ModelAdmin):
    list_display = ('nombre', 'orden')
    list_editable = ('orden',)
    ordering = ('orden',)
    search_fields = ('nombre',)


@admin.register(Cliente)
class ClienteAdmin(admin.ModelAdmin):
    list_display = ('nombre', 'apellido', 'telefono', 'direccion_ultima')
    search_fields = ('nombre', 'apellido', 'telefono')
