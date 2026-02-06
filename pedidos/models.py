from django.db import models
from django.db.models import Sum
from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver
from django.contrib.auth.models import User
from datetime import date, timedelta # IMPORTANTE: Agregar esto

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
    class Meta: verbose_name_plural = "Categorías"

class Producto(models.Model):
    categoria = models.ForeignKey(Categoria, related_name='productos', on_delete=models.CASCADE)
    nombre = models.CharField(max_length=200)
    descripcion = models.TextField(blank=True, null=True)
    precio = models.DecimalField(max_digits=10, decimal_places=2)
    imagen = models.ImageField(upload_to='productos/', blank=True, null=True)
    disponible = models.BooleanField(default=True)
    
    # NUEVO: Relación con los extras disponibles para este producto
    extras = models.ManyToManyField(Extra, blank=True, related_name='productos') 

    def __str__(self): return f"{self.nombre} - ${self.precio}"

# --- MODELO DE VARIANTES ---
class OpcionProducto(models.Model):
    producto = models.ForeignKey(Producto, related_name='opciones', on_delete=models.CASCADE)
    nombre = models.CharField(max_length=100) # Ej: "Carne de Res", "Pollo"
    precio_extra = models.DecimalField(max_digits=6, decimal_places=2, default=0.00) 
    disponible = models.BooleanField(default=True)

    def __str__(self):
        signo = "+" if self.precio_extra > 0 else ""
        return f"{self.nombre} ({signo}${self.precio_extra})"

class Cliente(models.Model):
    telefono = models.CharField(max_length=15, unique=True)
    nombre = models.CharField(max_length=100)
    apellido = models.CharField(max_length=100)
    direccion_ultima = models.TextField(blank=True, null=True)
    def __str__(self): return f"{self.nombre} {self.apellido} ({self.telefono})"

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
    fecha_vencimiento = models.DateField(default=date.today() + timedelta(days=30), verbose_name="Vencimiento Suscripción")

    def __str__(self): return f"Configuración de {self.nombre_negocio}"
    class Meta: verbose_name = "⚙️ Configuración del Negocio"

class DiaEspecial(models.Model):
    fecha = models.DateField(unique=True) 
    abierto = models.BooleanField(default=False)
    hora_apertura = models.TimeField(blank=True, null=True) 
    hora_cierre = models.TimeField(blank=True, null=True)
    motivo = models.CharField(max_length=100, blank=True, null=True) 

    def __str__(self):
        estado = "ABIERTO" if self.abierto else "CERRADO"
        return f"{self.fecha} - {estado} ({self.motivo})"
    class Meta: verbose_name = "📅 Día Especial / Feriado"

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
    METODOS_PAGO = [('EFECTIVO', 'Efectivo'), ('TARJETA', 'Tarjeta (Wompi)')]

    cliente = models.ForeignKey(Cliente, on_delete=models.PROTECT, related_name='pedidos')
    fecha_creacion = models.DateTimeField(auto_now_add=True)
    direccion_entrega = models.TextField(blank=True)
    latitud = models.CharField(max_length=50, blank=True, null=True)
    longitud = models.CharField(max_length=50, blank=True, null=True)
    metodo_pago = models.CharField(max_length=20, choices=METODOS_PAGO, default='EFECTIVO')
    
    # Campo clave para el rastreador
    estado = models.CharField(max_length=20, choices=ESTADOS, default='PENDIENTE')
    
    repartidor = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    
    total_productos = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    comision_plataforma = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    total_final = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    es_pedido_whatsapp = models.BooleanField(default=False, verbose_name="¿Es pedido manual/WhatsApp?")

    def save(self, *args, **kwargs):
        if self.metodo_pago == 'TARJETA':
            self.comision_plataforma = float(self.total_productos) * 0.05
        else:
            self.comision_plataforma = 0
            
        self.total_final = float(self.total_productos) + float(self.comision_plataforma)
        super().save(*args, **kwargs)

    def __str__(self): return f"Pedido #{self.id} - {self.cliente.nombre}"

class DetallePedido(models.Model):
    pedido = models.ForeignKey(Pedido, related_name='detalles', on_delete=models.CASCADE)
    producto = models.ForeignKey(Producto, on_delete=models.PROTECT)
    opcion = models.ForeignKey(OpcionProducto, on_delete=models.SET_NULL, null=True, blank=True)
    
    # NUEVO: Extras elegidos por el cliente
    extras = models.ManyToManyField(Extra, blank=True)
    
    cantidad = models.PositiveIntegerField(default=1)
    precio_unitario = models.DecimalField(max_digits=10, decimal_places=2, blank=True) 
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
    nuevo_total = pedido.detalles.aggregate(total=Sum('subtotal'))['total'] or 0
    pedido.total_productos = nuevo_total
    pedido.save()