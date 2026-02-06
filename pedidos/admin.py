from django.contrib import admin
from django.utils.html import format_html
from django.utils.safestring import mark_safe
from .models import Categoria, Producto, Cliente, Pedido, DetallePedido, ConfiguracionNegocio, DiaEspecial, OpcionProducto, Extra
from decouple import config 

# --- NUEVO: CONFIGURACIÓN DE VARIANTES (INLINE) ---
class OpcionProductoInline(admin.TabularInline):
    model = OpcionProducto
    extra = 1 

# --- NUEVO: REGISTRO DE EXTRAS ---
@admin.register(Extra)
class ExtraAdmin(admin.ModelAdmin):
    list_display = ('nombre', 'precio', 'disponible')
    list_editable = ('precio', 'disponible')
    search_fields = ('nombre',)

# 1. Configuración de PRODUCTOS
@admin.register(Producto)
class ProductoAdmin(admin.ModelAdmin):
    list_display = ('mostrar_imagen', 'nombre', 'categoria', 'precio', 'disponible')
    list_filter = ('categoria', 'disponible')
    search_fields = ('nombre',)
    list_editable = ('precio', 'disponible')
    
    # CAJITA PARA SELECCIONAR EXTRAS
    filter_horizontal = ('extras',)
    
    inlines = [OpcionProductoInline] 
    
    def mostrar_imagen(self, obj):
        if obj.imagen:
            return format_html('<img src="{}" width="40" height="40" style="border-radius:5px;" />', obj.imagen.url)
        return "❌"
    mostrar_imagen.short_description = "Foto"

# 2. Configuración de INGREDIENTES (Detalles del Pedido)
class DetallePedidoInline(admin.TabularInline):
    model = DetallePedido
    extra = 0
    readonly_fields = ('subtotal',)
    # Los extras se pueden editar aquí si es necesario
    filter_horizontal = ('extras',)

# 3. LA TORRE DE CONTROL (Pedidos)
@admin.register(Pedido)
class PedidoAdmin(admin.ModelAdmin):
    list_display = ('id', 'cliente_info', 'estado_color', 'metodo_pago', 'status_gps', 'acciones_mapa', 'total_final', 'fecha_creacion')
    list_filter = ('estado', 'metodo_pago', 'fecha_creacion', 'es_pedido_whatsapp')
    search_fields = ('cliente__nombre', 'cliente__telefono', 'id')
    inlines = [DetallePedidoInline]
    readonly_fields = ('total_productos', 'comision_plataforma', 'total_final', 'latitud', 'longitud', 'mapa_visual')

    def cliente_info(self, obj): return f"{obj.cliente.nombre} ({obj.cliente.telefono})"
    cliente_info.short_description = "Cliente"

    def estado_color(self, obj):
        if obj.estado == 'RECIBIDO': return f"🔔 {obj.get_estado_display()}"
        elif obj.estado == 'COCINA': return f"🔥 {obj.get_estado_display()}"
        elif obj.estado == 'RUTA': return f"🏍️ {obj.get_estado_display()}" 
        elif obj.estado == 'ENTREGADO': return f"✅ {obj.get_estado_display()}"
        return obj.get_estado_display()
    estado_color.short_description = "Estado"

    def status_gps(self, obj):
        if obj.latitud and obj.longitud: return mark_safe('<span style="color:green; font-weight:bold;">📍 OK</span>')
        return mark_safe('<span style="color:#ccc;">Sin GPS</span>')
    status_gps.short_description = "GPS"

    def acciones_mapa(self, obj):
        if obj.latitud and obj.longitud:
            url_google = f"https://www.google.com/maps/search/?api=1&query={obj.latitud},{obj.longitud}"
            url_waze = f"https://waze.com/ul?ll={obj.latitud},{obj.longitud}&navigate=yes"
            return format_html(
                '<a href="{}" target="_blank" style="background:#4285F4; color:white; padding:4px 8px; border-radius:4px; text-decoration:none; margin-right:5px; font-weight:bold;">🗺️ Maps</a>'
                '<a href="{}" target="_blank" style="background:#FECC00; color:black; padding:4px 8px; border-radius:4px; text-decoration:none; font-weight:bold;">🚗 Waze</a>',
                url_google, url_waze
            )
        return "-"
    acciones_mapa.short_description = "Navegar"

    def mapa_visual(self, obj):
        if obj.latitud and obj.longitud:
            api_key = config('GOOGLE_MAPS_API_KEY', default='')
            if api_key:
                return format_html(
                    '<iframe width="100%" height="350" frameborder="0" style="border:1px solid #ddd; border-radius: 8px;" '
                    'src="https://www.google.com/maps/embed/v1/place?key={}&q={},{}&zoom=15"></iframe>',
                    api_key, obj.latitud, obj.longitud
                )
            else: return "⚠️ Falta configurar GOOGLE_MAPS_API_KEY en .env"
        return "No hay ubicación registrada"
    mapa_visual.short_description = "Ubicación Exacta"

admin.site.register(Categoria)
admin.site.register(Cliente)

# --- NUEVOS REGISTROS ---

@admin.register(ConfiguracionNegocio)
class ConfigAdmin(admin.ModelAdmin):
    # Agregamos 'fecha_vencimiento' para que puedas editarla y probar el bloqueo
    list_display = ('nombre_negocio', 'fecha_vencimiento', 'hora_apertura', 'hora_cierre')
    
    # Esto asegura que solo haya UN registro de configuración
    def has_add_permission(self, request):
        if self.model.objects.exists():
            return False
        return super().has_add_permission(request)

@admin.register(DiaEspecial)
class DiaEspecialAdmin(admin.ModelAdmin):
    list_display = ('fecha', 'abierto', 'motivo')
    list_filter = ('abierto',)
    ordering = ('fecha',)