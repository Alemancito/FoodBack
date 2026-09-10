import uuid

from django.db import models
from django.db.models import Sum
from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver
from django.contrib.auth.models import User
from datetime import date, timedelta  # IMPORTANTE: Agregar esto
from django.utils import timezone
from decimal import Decimal



# ============================================================
# MULTI-TENANT FOUNDATION
# ============================================================


def fecha_vencimiento_por_defecto():
    return date.today() + timedelta(days=30)

class Tenant(models.Model):
    """
    Representa a una empresa/restaurante cliente de FoodBack.

    Un Tenant puede tener una o varias sucursales y varios
    usuarios asociados mediante Membership.
    """

    public_id = models.UUIDField(
        default=uuid.uuid4,
        unique=True,
        editable=False,
        db_index=True,
    )

    nombre = models.CharField(
        max_length=150,
    )

    slug = models.SlugField(
        max_length=100,
        unique=True,
        help_text=(
            "Identificador único del negocio. "
            "Más adelante podrá utilizarse para subdominios."
        ),
    )

    habilitado = models.BooleanField(
        default=True,
        help_text=(
            "Control administrativo de plataforma. "
            "No representa el estado de la suscripción."
        ),
    )

    fecha_creacion = models.DateTimeField(
        auto_now_add=True,
    )

    actualizado_en = models.DateTimeField(
        auto_now=True,
    )

    def __str__(self):
        return self.nombre

    class Meta:
        verbose_name = "Tenant"
        verbose_name_plural = "Tenants"
        ordering = ["nombre"]
        
        
class SuscripcionTenant(models.Model):

    class Estado(models.TextChoices):
        ACTIVA = "ACTIVA", "Activa"
        GRACIA = "GRACIA", "Período de gracia"
        SUSPENDIDA = "SUSPENDIDA", "Suspendida"
        CANCELADA = "CANCELADA", "Cancelada"

    tenant = models.OneToOneField(
        "Tenant",
        on_delete=models.PROTECT,
        related_name="suscripcion",
    )

    estado = models.CharField(
        max_length=20,
        choices=Estado.choices,
        default=Estado.ACTIVA,
        db_index=True,
    )

    fecha_vencimiento = models.DateField(
        default=fecha_vencimiento_por_defecto,
    )

    # Preparado para el período de gracia.
    # Todavía no activaremos automáticamente
    # esa lógica hasta diseñar el lifecycle completo.
    gracia_hasta = models.DateField(
        null=True,
        blank=True,
    )

    creado_en = models.DateTimeField(
        auto_now_add=True,
    )

    actualizado_en = models.DateTimeField(
        auto_now=True,
    )

    def __str__(self):
        return (
            f"{self.tenant.nombre} - "
            f"{self.estado} - "
            f"{self.fecha_vencimiento}"
        )

    class Meta:
        verbose_name = "Suscripción de Tenant"
        verbose_name_plural = "Suscripciones de Tenant"


class Sucursal(models.Model):
    """
    Una ubicación física perteneciente a un Tenant.

    Por ahora solo contiene su identidad básica.
    Horarios, coordenadas, cobertura, etc. se migrarán
    posteriormente desde ConfiguracionNegocio.
    """

    public_id = models.UUIDField(
        default=uuid.uuid4,
        unique=True,
        editable=False,
        db_index=True,
    )

    tenant = models.ForeignKey(
        Tenant,
        on_delete=models.PROTECT,
        related_name="sucursales",
    )

    nombre = models.CharField(
        max_length=150,
    )

    slug = models.SlugField(
        max_length=100,
    )

    class Estado(models.TextChoices):
        ACTIVA = "ACTIVA", "Activa"
        ARCHIVADA = "ARCHIVADA", "Archivada"


    estado = models.CharField(
        max_length=20,
        choices=Estado.choices,
        default=Estado.ACTIVA,
        db_index=True,
    )

    fecha_creacion = models.DateTimeField(
        auto_now_add=True,
    )

    actualizado_en = models.DateTimeField(
        auto_now=True,
    )

    def __str__(self):
        return f"{self.tenant.nombre} - {self.nombre}"

    class Meta:
        verbose_name = "Sucursal"
        verbose_name_plural = "Sucursales"

        constraints = [
            models.UniqueConstraint(
                fields=[
                    "tenant",
                    "slug",
                ],
                name="unique_sucursal_slug_por_tenant",
            ),
        ]

        ordering = [
            "tenant",
            "nombre",
        ]


class Membership(models.Model):
    """
    Relación segura entre un usuario de Django y un Tenant.

    No dependeremos de is_staff/is_superuser para decidir
    quién es dueño o gerente de un restaurante.
    """

    ROLE_OWNER = "OWNER"
    ROLE_MANAGER = "MANAGER"

    ROLE_CHOICES = [
        (
            ROLE_OWNER,
            "Dueño",
        ),
        (
            ROLE_MANAGER,
            "Gerente",
        ),
    ]

    tenant = models.ForeignKey(
        Tenant,
        on_delete=models.PROTECT,
        related_name="memberships",
    )

    usuario = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name="foodback_memberships",
    )

    rol = models.CharField(
        max_length=20,
        choices=ROLE_CHOICES,
    )

    activo = models.BooleanField(
        default=True,
    )

    fecha_creacion = models.DateTimeField(
        auto_now_add=True,
    )

    actualizado_en = models.DateTimeField(
        auto_now=True,
    )

    def __str__(self):
        return (
            f"{self.usuario.username} - "
            f"{self.tenant.nombre} ({self.rol})"
        )

    class Meta:
        verbose_name = "Membership"
        verbose_name_plural = "Memberships"

        constraints = [
            models.UniqueConstraint(
                fields=[
                    "tenant",
                    "usuario",
                ],
                name="unique_usuario_por_tenant",
            ),
        ]

        indexes = [
            models.Index(
                fields=[
                    "tenant",
                    "rol",
                ],
                name="membership_tenant_role_idx",
            ),
        ]


# --- NUEVO MODELO DE EXTRAS (Papas, Queso, Jalapeños...) ---
class Extra(models.Model):
    tenant = models.ForeignKey(
        "Tenant",
        on_delete=models.PROTECT,
        related_name="extras_catalogo",
        null=True,
        blank=True,
        db_index=True,
    )

    nombre = models.CharField(max_length=100)
    precio = models.DecimalField(
        max_digits=6,
        decimal_places=2,
    )
    disponible = models.BooleanField(
        default=True
    )

    def __str__(self):
        return f"{self.nombre} (+${self.precio})"


class Categoria(models.Model):
    tenant = models.ForeignKey(
        "Tenant",
        on_delete=models.PROTECT,
        related_name="categorias",
        null=True,
        blank=True,
        db_index=True,
    )

    nombre = models.CharField(
        max_length=100
    )

    orden = models.IntegerField(
        default=0
    )

    def __str__(self):
        return self.nombre

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
    
    sucursal = models.OneToOneField(
        "Sucursal",
        on_delete=models.PROTECT,
        related_name="configuracion",
        null=True,
        blank=True,
    )
    
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

    # LEGACY TEMPORAL:
    # La fuente de verdad de la suscripción ya es SuscripcionTenant.
    # Este campo se conserva durante la transición y se retirará
    # únicamente cuando las migraciones/tests legacy hayan sido actualizados.
    fecha_vencimiento = models.DateField(
        default=fecha_vencimiento_por_defecto,
        verbose_name="Vencimiento Suscripción",
    )

    def __str__(self): return f"Configuración de {self.nombre_negocio}"

    class Meta:
        verbose_name = "⚙️ Configuración del Negocio"


class DiaEspecial(models.Model):
    sucursal = models.ForeignKey(
        "Sucursal",
        on_delete=models.PROTECT,
        related_name="dias_especiales",
        null=True,
        blank=True,
        help_text=(
            "Sucursal a la que pertenece esta excepción "
            "de horario."
        ),
    )

    fecha = models.DateField()

    abierto = models.BooleanField(
        default=False
    )

    hora_apertura = models.TimeField(
        blank=True,
        null=True
    )

    hora_cierre = models.TimeField(
        blank=True,
        null=True
    )

    motivo = models.CharField(
        max_length=100,
        blank=True,
        null=True
    )

    def __str__(self):
        estado = (
            "ABIERTO"
            if self.abierto
            else "CERRADO"
        )

        sucursal = (
            self.sucursal.nombre
            if self.sucursal
            else "Sin sucursal"
        )

        return (
            f"{sucursal} - "
            f"{self.fecha} - "
            f"{estado} "
            f"({self.motivo or ''})"
        )

    class Meta:
        verbose_name = (
            "📅 Día Especial / Feriado"
        )

        verbose_name_plural = (
            "📅 Días Especiales / Feriados"
        )

        constraints = [
            models.UniqueConstraint(
                fields=[
                    "sucursal",
                    "fecha",
                ],
                name=(
                    "unique_dia_especial_"
                    "por_sucursal"
                ),
            ),
        ]

        indexes = [
            models.Index(
                fields=[
                    "sucursal",
                    "fecha",
                ],
                name="diaesp_sucursal_fecha_idx",
            ),
        ]


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
    
    sucursal = models.ForeignKey(
        "Sucursal",
        on_delete=models.PROTECT,
        related_name="pedidos",
        null=True,
        blank=True,
        db_index=True,
    )

    cliente = models.ForeignKey(
        Cliente,
        on_delete=models.PROTECT,
        related_name="pedidos",
    )

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
        total_productos = Decimal(
            str(
                self.total_productos
                or '0.00'
            )
        ).quantize(
            Decimal('0.01')
        )

        self.total_productos = total_productos

        # FB-COMP-001:
        # El precio final NO cambia por elegir tarjeta.
        self.comision_plataforma = Decimal(
            '0.00'
        )

        self.total_final = total_productos
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
    tenant = models.ForeignKey(
        "Tenant",
        on_delete=models.PROTECT,
        related_name="pagos_wompi",
        null=True,
        blank=True,
        db_index=True,
    )
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
    
    cliente_token_hash = models.CharField(
        max_length=64,
        blank=True,
        db_index=True,
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


    def __str__(self):
        return f"{self.tipo} | {self.referencia} | {self.estado}"

    class Meta:
        verbose_name = "Pago Wompi"
        verbose_name_plural = "Pagos Wompi"
        ordering = ['-fecha_creacion']
        
        
        
class EventoPagoWompi(models.Model):
    CATEGORIA_CHOICES = [
        (
            'RECHAZO_CLIENTE',
            'Rechazo bancario / cliente'
        ),
        (
            'ERROR_TECNICO',
            'Error técnico'
        ),
        (
            'SEGURIDAD',
            'Evento de seguridad'
        ),
        (
            'INFO',
            'Información'
        ),
    ]

    ORIGEN_CHOICES = [
        ('INICIO', 'Inicio de pago'),
        ('WEBHOOK', 'Webhook'),
        ('REDIRECT', 'Redirect'),
        ('SISTEMA', 'Sistema'),
    ]

    pago = models.ForeignKey(
        PagoWompi,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='eventos',
    )

    pedido = models.ForeignKey(
        Pedido,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='eventos_pago_wompi',
    )

    configuracion_negocio = models.ForeignKey(
        ConfiguracionNegocio,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='eventos_pago_wompi',
    )

    cliente_token_hash = models.CharField(
        max_length=64,
        blank=True,
        db_index=True,
    )

    categoria = models.CharField(
        max_length=30,
        choices=CATEGORIA_CHOICES,
        db_index=True,
    )

    origen = models.CharField(
        max_length=20,
        choices=ORIGEN_CHOICES,
    )

    codigo = models.CharField(
        max_length=80,
        blank=True,
    )

    mensaje = models.TextField(
        blank=True,
    )

    # Permite que los contadores futuros sean explícitos.
    cuenta_para_cliente = models.BooleanField(
        default=False
    )

    cuenta_para_global = models.BooleanField(
        default=False
    )

    # Evita que un replay del mismo webhook
    # incremente contadores varias veces.
    clave_evento = models.CharField(
        max_length=128,
        null=True,
        blank=True,
        unique=True,
    )

    # Solo metadata sanitizada.
    # Nunca tarjeta, CVV, secrets ni Authorization.
    metadata = models.JSONField(
        default=dict,
        blank=True,
    )

    fecha = models.DateTimeField(
        auto_now_add=True,
        db_index=True,
    )

    def __str__(self):
        return (
            f"{self.categoria} | "
            f"{self.codigo or 'SIN-CODIGO'}"
        )

    class Meta:
        verbose_name = "Evento de pago Wompi"
        verbose_name_plural = "Eventos de pago Wompi"
        ordering = ['-fecha']


class EstadoPasarelaPago(models.Model):
    """
    Estado del circuit breaker.

    En el futuro este estado quedará naturalmente
    asociado a cada tenant/sucursal.
    """

    configuracion_negocio = models.OneToOneField(
        ConfiguracionNegocio,
        on_delete=models.CASCADE,
        related_name='estado_pasarela',
    )

    bloqueo_manual = models.BooleanField(
        default=False
    )

    bloqueado_hasta = models.DateTimeField(
        null=True,
        blank=True,
    )

    codigo_motivo = models.CharField(
        max_length=80,
        blank=True,
    )

    motivo = models.TextField(
        blank=True,
    )

    actualizado_en = models.DateTimeField(
        auto_now=True
    )

    @property
    def bloqueada(self):
        if self.bloqueo_manual:
            return True

        if not self.bloqueado_hasta:
            return False

        return (
            self.bloqueado_hasta
            > timezone.now()
        )

    def __str__(self):
        estado = (
            "BLOQUEADA"
            if self.bloqueada
            else "ACTIVA"
        )

        return (
            f"Pasarela "
            f"{self.configuracion_negocio_id}: "
            f"{estado}"
        )

    class Meta:
        verbose_name = "Estado de pasarela"
        verbose_name_plural = "Estados de pasarela"        



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
