import uuid

from django.db import models
from django.db.models import Sum
from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver
from django.contrib.auth.models import User
from datetime import date, timedelta  # IMPORTANTE: Agregar esto
from django.utils import timezone


# --- NUEVO MODELO DE EXTRAS (Papas, Queso, Jalapeños...) ---
class Extra(models.Model):
    nombre = models.CharField(max_length=100)
    precio = models.DecimalField(max_digits=6, decimal_places=2)
    disponible = models.BooleanField(default=True)

    def __str__(self):
        return f"{self.nombre} (+${self.precio})"


class Categoria(models.Model):
    nombre = models.CharField(max_length=100)
    orden = models.IntegerField(default=0)
    def __str__(self): return self.nombre

    class Meta:
        verbose_name_plural = "Categorías"


class Producto(models.Model):
    categoria = models.ForeignKey(
        Categoria, related_name='productos', on_delete=models.CASCADE)
    nombre = models.CharField(max_length=200)
    descripcion = models.TextField(blank=True, null=True)
    precio = models.DecimalField(max_digits=10, decimal_places=2)
    imagen = models.ImageField(upload_to='productos/', blank=True, null=True)
    disponible = models.BooleanField(default=True)

    # NUEVO: Relación con los extras disponibles para este producto
    extras = models.ManyToManyField(
        Extra, blank=True, related_name='productos')

    def __str__(self): return f"{self.nombre} - ${self.precio}"

# --- MODELO DE VARIANTES ---


class OpcionProducto(models.Model):
    producto = models.ForeignKey(
        Producto, related_name='opciones', on_delete=models.CASCADE)
    nombre = models.CharField(max_length=100)  # Ej: "Carne de Res", "Pollo"
    precio_extra = models.DecimalField(
        max_digits=6, decimal_places=2, default=0.00)
    disponible = models.BooleanField(default=True)

    def __str__(self):
        signo = "+" if self.precio_extra > 0 else ""
        return f"{self.nombre} ({signo}${self.precio_extra})"


class Cliente(models.Model):
    telefono = models.CharField(max_length=15, unique=True)
    nombre = models.CharField(max_length=100)
    apellido = models.CharField(max_length=100)
    direccion_ultima = models.TextField(blank=True, null=True)
    def __str__(
        self): return f"{self.nombre} {self.apellido} ({self.telefono})"

# --- CEREBRO DEL TIEMPO ---


class ConfiguracionNegocio(models.Model):
    nombre_negocio = models.CharField(max_length=100, default="FoodBack")
    hora_apertura = models.TimeField(default="08:00")
    hora_cierre = models.TimeField(default="22:00")

    lunes_abierto = models.BooleanField(default=True)
    martes_abierto = models.BooleanField(default=True)
    miercoles_abierto = models.BooleanField(default=True)
    jueves_abierto = models.BooleanField(default=True)
    viernes_abierto = models.BooleanField(default=True)
    sabado_abierto = models.BooleanField(default=True)
    domingo_abierto = models.BooleanField(default=True)

    mensaje_cierre = models.TextField(
        default="Ups, la cocina descansa. 😴\nVolvemos mañana con las pilas cargadas.",
        help_text="Mensaje gracioso que verá el cliente cuando esté cerrado."
    )

    # --- NUEVO CAMPO DE SUSCRIPCIÓN ---
    # Por defecto damos 30 días de gracia al crear la BD
    fecha_vencimiento = models.DateField(default=date.today(
    ) + timedelta(days=30), verbose_name="Vencimiento Suscripción")

    def __str__(self): return f"Configuración de {self.nombre_negocio}"

    class Meta:
        verbose_name = "⚙️ Configuración del Negocio"


class DiaEspecial(models.Model):
    fecha = models.DateField(unique=True)
    abierto = models.BooleanField(default=False)
    hora_apertura = models.TimeField(blank=True, null=True)
    hora_cierre = models.TimeField(blank=True, null=True)
    motivo = models.CharField(max_length=100, blank=True, null=True)

    def __str__(self):
        estado = "ABIERTO" if self.abierto else "CERRADO"
        return f"{self.fecha} - {estado} ({self.motivo})"

    class Meta:
        verbose_name = "📅 Día Especial / Feriado"


class Pedido(models.Model):
    # --- ESTADOS PARA EL TRACKING ---
    ESTADOS = [
        ('PENDIENTE', '⏳ Pendiente de Pago'),
        ('RECIBIDO', '🔔 Recibido (Confirmado)'),
        ('COCINA', '🔥 En Cocina'),
        ('RUTA', '🏍️ En Ruta'),
        ('ENTREGADO', '✅ Entregado'),
        ('PROBLEMA', '⚠️ Problema / No Recibido'),
        ('CANCELADO', '❌ Cancelado'),
    ]

    METODOS_PAGO = [
        ('EFECTIVO', 'Efectivo'),
        ('TARJETA', 'Tarjeta (Wompi)'),
    ]

    cliente = models.ForeignKey(
        Cliente, on_delete=models.PROTECT, related_name='pedidos')

    fecha_creacion = models.DateTimeField(auto_now_add=True)

    tracking_token = models.UUIDField(
        default=uuid.uuid4,
        unique=True,
        editable=False,
    )

    # NUEVO:
    # Este campo se actualiza automáticamente cada vez que el pedido cambia.
    # Nos sirve para saber si el polling debe actualizar la pantalla o no.
    actualizado_en = models.DateTimeField(auto_now=True)

    direccion_entrega = models.TextField(blank=True)
    latitud = models.CharField(max_length=50, blank=True, null=True)
    longitud = models.CharField(max_length=50, blank=True, null=True)

    metodo_pago = models.CharField(
        max_length=20, choices=METODOS_PAGO, default='EFECTIVO')

    # Campo clave para el rastreador
    estado = models.CharField(
        max_length=20, choices=ESTADOS, default='PENDIENTE')

    repartidor = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True)

    total_productos = models.DecimalField(
        max_digits=10, decimal_places=2, default=0)
    comision_plataforma = models.DecimalField(
        max_digits=10, decimal_places=2, default=0)
    total_final = models.DecimalField(
        max_digits=10, decimal_places=2, default=0)

    es_pedido_whatsapp = models.BooleanField(
        default=False,
        verbose_name="¿Es pedido manual/WhatsApp?"
    )

    # --- DATOS DE PAGO WOMPI ---
    # La URL de regreso NO debe ser la prueba de pago.
    # Estos campos nos ayudan a enlazar el pedido con una referencia segura
    # generada por el servidor y confirmada por webhook/hash válido.
    wompi_referencia = models.CharField(
        max_length=150, unique=True, null=True, blank=True, db_index=True)
    wompi_id_enlace = models.CharField(max_length=100, null=True, blank=True)
    wompi_url_enlace = models.URLField(max_length=700, null=True, blank=True)
    wompi_id_transaccion = models.CharField(
        max_length=150, null=True, blank=True, db_index=True)
    pago_verificado = models.BooleanField(default=False)
    fecha_pago_verificado = models.DateTimeField(null=True, blank=True)

    def save(self, *args, **kwargs):
        if self.metodo_pago == 'TARJETA':
            self.comision_plataforma = float(self.total_productos) * 0.05
        else:
            self.comision_plataforma = 0

        self.total_final = float(self.total_productos) + \
            float(self.comision_plataforma)
        super().save(*args, **kwargs)

    def __str__(self):
        return f"Pedido #{self.id} - {self.cliente.nombre}"


class PagoWompi(models.Model):
    TIPO_CHOICES = [
        ('PEDIDO', 'Pedido de cliente'),
        ('SUSCRIPCION', 'Suscripción SaaS'),
    ]

    ESTADO_CHOICES = [
        ('CREADO', 'Enlace creado'),
        ('PENDIENTE', 'Pendiente de confirmación'),
        ('APROBADO', 'Pago aprobado'),
        ('RECHAZADO', 'Pago rechazado'),
        ('CANCELADO', 'Pago cancelado'),
        ('ERROR', 'Error de validación'),
    ]

    tipo = models.CharField(max_length=20, choices=TIPO_CHOICES)
    pedido = models.ForeignKey(
        Pedido,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='pagos_wompi'
    )
    configuracion_negocio = models.ForeignKey(
        ConfiguracionNegocio,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='pagos_suscripcion'
    )

    referencia = models.CharField(max_length=150, unique=True, db_index=True)
    id_enlace = models.CharField(
        max_length=100, null=True, blank=True, db_index=True)
    url_enlace = models.URLField(max_length=700, null=True, blank=True)
    id_transaccion = models.CharField(
        max_length=150,
        null=True,
        blank=True,
        unique=True,
    )

    monto = models.DecimalField(max_digits=10, decimal_places=2)
    estado = models.CharField(
        max_length=20, choices=ESTADO_CHOICES, default='CREADO')
    es_aprobada = models.BooleanField(default=False)

    raw_creacion = models.JSONField(default=dict, blank=True)
    raw_redirect = models.JSONField(default=dict, blank=True)
    raw_webhook = models.JSONField(default=dict, blank=True)
    ultimo_error = models.TextField(blank=True, null=True)

    fecha_creacion = models.DateTimeField(auto_now_add=True)
    fecha_actualizacion = models.DateTimeField(auto_now=True)
    fecha_aprobacion = models.DateTimeField(null=True, blank=True)

    def marcar_aprobado(self, id_transaccion=None, raw_payload=None):
        self.estado = 'APROBADO'
        self.es_aprobada = True
        if id_transaccion:
            self.id_transaccion = id_transaccion
        if raw_payload is not None:
            self.raw_webhook = raw_payload
        self.fecha_aprobacion = timezone.now()
        self.save()

    def __str__(self):
        return f"{self.tipo} | {self.referencia} | {self.estado}"

    class Meta:
        verbose_name = "Pago Wompi"
        verbose_name_plural = "Pagos Wompi"
        ordering = ['-fecha_creacion']


class DetallePedido(models.Model):
    pedido = models.ForeignKey(
        Pedido, related_name='detalles', on_delete=models.CASCADE)
    producto = models.ForeignKey(Producto, on_delete=models.PROTECT)
    opcion = models.ForeignKey(
        OpcionProducto, on_delete=models.SET_NULL, null=True, blank=True)

    # NUEVO: Extras elegidos por el cliente
    extras = models.ManyToManyField(Extra, blank=True)

    cantidad = models.PositiveIntegerField(default=1)
    precio_unitario = models.DecimalField(
        max_digits=10, decimal_places=2, blank=True)
    subtotal = models.DecimalField(max_digits=10, decimal_places=2, blank=True)

    def save(self, *args, **kwargs):
        # Si no hay precio unitario definido, tomamos el del producto base
        if not self.precio_unitario:
            self.precio_unitario = self.producto.precio

        # Calcular el precio real (Base + Extra de la opción + Extras checkbox)
        extra_opcion = self.opcion.precio_extra if self.opcion else 0

        # SUMAR EXTRAS (Solo si el objeto ya existe, para evitar error M2M)
        extra_extras = 0
        if self.pk:
            extra_extras = sum([e.precio for e in self.extras.all()])

        precio_final = self.precio_unitario + extra_opcion + extra_extras

        # Calcular subtotal
        self.subtotal = self.cantidad * precio_final
        super().save(*args, **kwargs)

    def __str__(self):
        variante_str = f" ({self.opcion.nombre})" if self.opcion else ""
        return f"{self.cantidad}x {self.producto.nombre}{variante_str}"


@receiver(post_save, sender=DetallePedido)
@receiver(post_delete, sender=DetallePedido)
def actualizar_total_pedido(sender, instance, **kwargs):
    pedido = instance.pedido
    nuevo_total = pedido.detalles.aggregate(
        total=Sum('subtotal'))['total'] or 0
    pedido.total_productos = nuevo_total
    pedido.save()
