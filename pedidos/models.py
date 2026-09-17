import uuid

from django.db import models
from django.db.models import Sum
from django.db.models.functions import (
    Lower,
    Trim,
)
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
        
        
class StaffIdentity(models.Model):
    """
    Identidad global de seguridad para usuarios internos
    de FoodBack: OWNER, MANAGER, DELIVERY y futuros
    usuarios administrativos.

    No pertenece a un Tenant porque debe poder localizarse
    durante recuperación de contraseña antes de resolver
    un contexto multi-tenant confiable.
    """

    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        related_name="foodback_staff_identity",
    )

    email = models.EmailField(
        max_length=254,
    )

    email_verified = models.BooleanField(
        default=False,
    )

    creado_en = models.DateTimeField(
        auto_now_add=True,
    )

    actualizado_en = models.DateTimeField(
        auto_now=True,
    )

    def save(
        self,
        *args,
        **kwargs,
    ):
        self.email = (
            (self.email or "")
            .strip()
            .lower()
        )

        super().save(
            *args,
            **kwargs,
        )

    def __str__(self):
        return (
            f"{self.user.username} - "
            f"{self.email}"
        )

    class Meta:
        verbose_name = (
            "Identidad de personal"
        )

        verbose_name_plural = (
            "Identidades de personal"
        )

        constraints = [
            models.UniqueConstraint(
                Lower(
                    Trim("email")
                ),
                name=(
                    "staff_identity_"
                    "email_ci_unique"
                ),
            ),
            models.CheckConstraint(
                check=~models.Q(
                    email__regex=r"^\s*$"
                ),
                name=(
                    "staff_identity_"
                    "email_not_empty"
                ),
            ),
        ]      



class PasswordResetChallenge(models.Model):
    """
    Desafío temporal para recuperar la contraseña
    de una identidad de personal.

    El código real nunca se persiste.
    Únicamente almacenamos un hash seguro.
    """

    public_id = models.UUIDField(
        default=uuid.uuid4,
        unique=True,
        editable=False,
        db_index=True,
    )

    identity = models.ForeignKey(
        StaffIdentity,
        on_delete=models.CASCADE,
        related_name="password_reset_challenges",
    )

    codigo_hash = models.CharField(
        max_length=128,
    )

    expira_en = models.DateTimeField()

    intentos = models.PositiveSmallIntegerField(
        default=0,
    )

    usado_en = models.DateTimeField(
        null=True,
        blank=True,
    )

    completado_en = models.DateTimeField(
        null=True,
        blank=True,
    )

    creado_en = models.DateTimeField(
        auto_now_add=True,
    )

    def __str__(self):
        return (
            f"Password reset "
            f"{self.identity.user.username} "
            f"{self.public_id}"
        )

    class Meta:
        verbose_name = (
            "Desafío de recuperación de contraseña"
        )

        verbose_name_plural = (
            "Desafíos de recuperación de contraseña"
        )

        indexes = [
            models.Index(
                fields=[
                    "identity",
                    "creado_en",
                ],
                name="pwdreset_identity_created_idx",
            ),
        ]

        constraints = [
            models.CheckConstraint(
                check=(
                    models.Q(
                        completado_en__isnull=True
                    )
                    | models.Q(
                        usado_en__isnull=False
                    )
                ),
                name=(
                    "pwdreset_complete_"
                    "requires_used"
                ),
            ),
        ]


class AuditEvent(models.Model):
    """
    Registro append-only de seguridad, auditoría y operación.

    Esta tabla es deliberadamente global/control-plane:
    el futuro dashboard Foundation/Superadmin debe poder
    investigar eventos de múltiples Tenant sin depender del
    contexto RLS de una sucursal concreta.

    No concede permisos por sí misma. Los campos de actor,
    Tenant y sucursal son snapshots para investigación.
    """

    class Categoria(models.TextChoices):
        AUTENTICACION = (
            "AUTENTICACION",
            "Autenticación",
        )
        AUTORIZACION = (
            "AUTORIZACION",
            "Autorización",
        )
        SEGURIDAD = (
            "SEGURIDAD",
            "Seguridad",
        )
        CUENTA = (
            "CUENTA",
            "Cuenta",
        )
        ADMINISTRACION = (
            "ADMINISTRACION",
            "Administración",
        )
        PAGOS = (
            "PAGOS",
            "Pagos",
        )
        SISTEMA = (
            "SISTEMA",
            "Sistema",
        )
        SOPORTE = (
            "SOPORTE",
            "Soporte",
        )

    class Severidad(models.TextChoices):
        INFO = "INFO", "Informativa"
        BAJA = "BAJA", "Baja"
        MEDIA = "MEDIA", "Media"
        ALTA = "ALTA", "Alta"
        CRITICA = "CRITICA", "Crítica"

    class Resultado(models.TextChoices):
        INFORMATIVO = (
            "INFORMATIVO",
            "Informativo",
        )
        EXITO = "EXITO", "Éxito"
        FALLO = "FALLO", "Fallo"
        BLOQUEADO = (
            "BLOQUEADO",
            "Bloqueado",
        )
        DENEGADO = (
            "DENEGADO",
            "Denegado",
        )
        ERROR = "ERROR", "Error"

    class Fuente(models.TextChoices):
        WEB = "WEB", "Web"
        SISTEMA = "SISTEMA", "Sistema"
        WEBHOOK = "WEBHOOK", "Webhook"
        TAREA = "TAREA", "Tarea"

    class ActorTipo(models.TextChoices):
        ANONIMO = "ANONIMO", "Anónimo"
        USUARIO = "USUARIO", "Usuario"
        SISTEMA = "SISTEMA", "Sistema"

    public_id = models.UUIDField(
        default=uuid.uuid4,
        unique=True,
        editable=False,
        db_index=True,
    )

    # Varios eventos generados dentro del mismo request
    # comparten request_id para facilitar investigación.
    request_id = models.UUIDField(
        null=True,
        blank=True,
        db_index=True,
    )

    evento = models.CharField(
        max_length=120,
    )

    categoria = models.CharField(
        max_length=24,
        choices=Categoria.choices,
    )

    severidad = models.CharField(
        max_length=16,
        choices=Severidad.choices,
        default=Severidad.INFO,
    )

    resultado = models.CharField(
        max_length=16,
        choices=Resultado.choices,
        default=Resultado.INFORMATIVO,
    )

    fuente = models.CharField(
        max_length=16,
        choices=Fuente.choices,
        default=Fuente.WEB,
    )

    descripcion = models.CharField(
        max_length=255,
        blank=True,
    )

    actor_tipo = models.CharField(
        max_length=16,
        choices=ActorTipo.choices,
        default=ActorTipo.ANONIMO,
    )

    actor_usuario = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="foodback_audit_events",
    )

    # Snapshot: permanece aunque luego cambie el usuario.
    actor_username = models.CharField(
        max_length=150,
        blank=True,
    )

    # Snapshot descriptivo. No se usa para autorizar.
    # Valores actuales esperados: OWNER, MANAGER,
    # DELIVERY, SUPERADMIN_FOODBACK, USUARIO.
    actor_role = models.CharField(
        max_length=40,
        blank=True,
        db_index=True,
    )

    # Snapshots globales para evitar joins RLS al consultar
    # auditoría desde Foundation/Superadmin.
    tenant_public_id = models.UUIDField(
        null=True,
        blank=True,
    )

    tenant_nombre = models.CharField(
        max_length=150,
        blank=True,
    )

    sucursal_public_id = models.UUIDField(
        null=True,
        blank=True,
    )

    sucursal_nombre = models.CharField(
        max_length=150,
        blank=True,
    )

    ip = models.GenericIPAddressField(
        null=True,
        blank=True,
    )

    metodo_http = models.CharField(
        max_length=10,
        blank=True,
    )

    # Solo path, nunca query string.
    ruta = models.CharField(
        max_length=500,
        blank=True,
    )

    status_code = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
    )

    user_agent = models.CharField(
        max_length=300,
        blank=True,
    )

    # Permite enlazar luego auditoría con objetos como
    # usuario, pedido, configuración, suscripción, etc.
    objeto_tipo = models.CharField(
        max_length=80,
        blank=True,
    )

    objeto_id = models.CharField(
        max_length=120,
        blank=True,
    )

    # Base para la futura capa de detección/incidentes:
    # eventos equivalentes generan la misma huella y podrán
    # agruparse sin enviar una alerta por cada request.
    fingerprint = models.CharField(
        max_length=64,
        blank=True,
    )

    metadata = models.JSONField(
        default=dict,
        blank=True,
    )

    creado_en = models.DateTimeField(
        auto_now_add=True,
        db_index=True,
    )

    def save(
        self,
        *args,
        **kwargs,
    ):
        if (
            self.pk is not None
            and not self._state.adding
        ):
            raise ValueError(
                "AuditEvent es append-only y no puede modificarse."
            )

        return super().save(
            *args,
            **kwargs,
        )

    def __str__(self):
        return (
            f"{self.creado_en} - "
            f"{self.evento} - "
            f"{self.severidad}"
        )

    class Meta:
        verbose_name = "Evento de auditoría"
        verbose_name_plural = "Eventos de auditoría"
        ordering = [
            "-creado_en",
        ]

        indexes = [
            models.Index(
                fields=[
                    "categoria",
                    "-creado_en",
                ],
                name="audit_category_created_idx",
            ),
            models.Index(
                fields=[
                    "severidad",
                    "-creado_en",
                ],
                name="audit_severity_created_idx",
            ),
            models.Index(
                fields=[
                    "tenant_public_id",
                    "-creado_en",
                ],
                name="audit_tenant_created_idx",
            ),
            models.Index(
                fields=[
                    "sucursal_public_id",
                    "-creado_en",
                ],
                name="audit_branch_created_idx",
            ),
            models.Index(
                fields=[
                    "ip",
                    "-creado_en",
                ],
                name="audit_ip_created_idx",
            ),
            models.Index(
                fields=[
                    "evento",
                    "-creado_en",
                ],
                name="audit_event_created_idx",
            ),
            models.Index(
                fields=[
                    "fingerprint",
                    "-creado_en",
                ],
                name="audit_fprint_created_idx",
            ),
        ]


class SecurityIncident(models.Model):
    """
    Incidente correlacionado a partir de uno o varios AuditEvent.

    A diferencia de AuditEvent, este modelo SÍ cambia con el tiempo:
    acumula eventos equivalentes, conserva el estado operativo y
    controla el cooldown de alertas. También es global/control-plane
    para que Foundation/Superadmin pueda investigar toda la plataforma.
    """

    class Estado(models.TextChoices):
        ABIERTO = "ABIERTO", "Abierto"
        RECONOCIDO = "RECONOCIDO", "Reconocido"
        RESUELTO = "RESUELTO", "Resuelto"

    public_id = models.UUIDField(
        default=uuid.uuid4,
        unique=True,
        editable=False,
        db_index=True,
    )

    # Mismo fingerprint que los AuditEvent equivalentes.
    fingerprint = models.CharField(
        max_length=64,
        db_index=True,
    )

    evento_clave = models.CharField(
        max_length=120,
    )

    categoria = models.CharField(
        max_length=24,
        choices=AuditEvent.Categoria.choices,
    )

    severidad = models.CharField(
        max_length=16,
        choices=AuditEvent.Severidad.choices,
        default=AuditEvent.Severidad.MEDIA,
        db_index=True,
    )

    estado = models.CharField(
        max_length=16,
        choices=Estado.choices,
        default=Estado.ABIERTO,
        db_index=True,
    )

    titulo = models.CharField(
        max_length=180,
    )

    descripcion = models.CharField(
        max_length=255,
        blank=True,
    )

    # Snapshots para pintar el incidente sin joins cross-tenant.
    actor_username = models.CharField(
        max_length=150,
        blank=True,
    )

    actor_role = models.CharField(
        max_length=40,
        blank=True,
    )

    tenant_public_id = models.UUIDField(
        null=True,
        blank=True,
    )

    tenant_nombre = models.CharField(
        max_length=150,
        blank=True,
    )

    sucursal_public_id = models.UUIDField(
        null=True,
        blank=True,
    )

    sucursal_nombre = models.CharField(
        max_length=150,
        blank=True,
    )

    ip = models.GenericIPAddressField(
        null=True,
        blank=True,
    )

    ruta = models.CharField(
        max_length=500,
        blank=True,
    )

    contador_eventos = models.PositiveIntegerField(
        default=1,
    )

    primero_visto_en = models.DateTimeField(
        db_index=True,
    )

    ultimo_visto_en = models.DateTimeField(
        db_index=True,
    )

    evento_inicial = models.ForeignKey(
        AuditEvent,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="incidentes_como_evento_inicial",
    )

    evento_ultimo = models.ForeignKey(
        AuditEvent,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="incidentes_como_evento_ultimo",
    )

    # ultima_notificacion_en funciona como reserva/cooldown de intento.
    # Se marca ANTES de hablar con SMTP para evitar correos duplicados
    # si llegan varios requests concurrentes.
    ultima_notificacion_en = models.DateTimeField(
        null=True,
        blank=True,
    )

    primera_notificacion_exitosa_en = models.DateTimeField(
        null=True,
        blank=True,
    )

    ultima_notificacion_exitosa_en = models.DateTimeField(
        null=True,
        blank=True,
    )

    notificaciones_enviadas = models.PositiveIntegerField(
        default=0,
    )

    fallos_notificacion = models.PositiveIntegerField(
        default=0,
    )

    ultimo_error_notificacion = models.CharField(
        max_length=255,
        blank=True,
    )

    reconocido_en = models.DateTimeField(
        null=True,
        blank=True,
    )

    resuelto_en = models.DateTimeField(
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
            f"{self.get_severidad_display()} - "
            f"{self.titulo} ({self.contador_eventos})"
        )

    class Meta:
        verbose_name = "Incidente de seguridad"
        verbose_name_plural = "Incidentes de seguridad"
        ordering = [
            "-ultimo_visto_en",
        ]

        constraints = [
            models.CheckConstraint(
                check=models.Q(
                    contador_eventos__gte=1,
                ),
                name="security_incident_count_gte_1",
            ),
            models.CheckConstraint(
                check=(
                    ~models.Q(
                        estado="RECONOCIDO",
                    )
                    | models.Q(
                        reconocido_en__isnull=False,
                    )
                ),
                name="secinc_ack_requires_timestamp",
            ),
            models.CheckConstraint(
                check=(
                    ~models.Q(
                        estado="RESUELTO",
                    )
                    | models.Q(
                        resuelto_en__isnull=False,
                    )
                ),
                name="secinc_resolved_requires_timestamp",
            ),
            models.CheckConstraint(
                check=(
                    models.Q(
                        estado="RESUELTO",
                    )
                    | models.Q(
                        resuelto_en__isnull=True,
                    )
                ),
                name="secinc_active_no_resolved_ts",
            ),
            # Solo puede existir un incidente activo por fingerprint.
            # Los incidentes resueltos permanecen como historial y una
            # nueva oleada puede abrir un incidente nuevo.
            models.UniqueConstraint(
                fields=[
                    "fingerprint",
                ],
                condition=models.Q(
                    estado__in=[
                        "ABIERTO",
                        "RECONOCIDO",
                    ],
                ),
                name="security_incident_active_fprint_uniq",
            ),
        ]

        indexes = [
            models.Index(
                fields=[
                    "estado",
                    "severidad",
                    "-ultimo_visto_en",
                ],
                name="secinc_state_sev_last_idx",
            ),
            models.Index(
                fields=[
                    "fingerprint",
                    "-ultimo_visto_en",
                ],
                name="secinc_fprint_last_idx",
            ),
            models.Index(
                fields=[
                    "tenant_public_id",
                    "-ultimo_visto_en",
                ],
                name="secinc_tenant_last_idx",
            ),
            models.Index(
                fields=[
                    "evento_clave",
                    "-ultimo_visto_en",
                ],
                name="secinc_event_last_idx",
            ),
        ]




class SupportReport(models.Model):
    """
    Ticket humano de soporte creado desde una pantalla de error.

    Es deliberadamente global/control-plane, igual que AuditEvent y
    SecurityIncident. No usa RLS de negocio y NO concede acceso a datos
    de un Tenant. Tenant/sucursal/actor se guardan como snapshots para
    que Foundation pueda investigar el reporte posteriormente.

    El request_id original se firma en servidor antes de llegar al
    navegador; nunca se aceptan Tenant, sucursal ni actor desde POST.
    """

    class Estado(models.TextChoices):
        ABIERTO = "ABIERTO", "Abierto"
        EN_REVISION = "EN_REVISION", "En revisión"
        RESUELTO = "RESUELTO", "Resuelto"

    class Origen(models.TextChoices):
        ERROR_HTTP = "ERROR_HTTP", "Pantalla de error HTTP"

    public_id = models.UUIDField(
        default=uuid.uuid4,
        unique=True,
        editable=False,
        db_index=True,
    )

    # Referencia técnica que el usuario vio en la pantalla de error.
    # Es única para que reenviar el mismo formulario sea idempotente.
    source_request_id = models.UUIDField(
        unique=True,
        editable=False,
    )

    # Request que efectivamente creó el ticket.
    submission_request_id = models.UUIDField(
        null=True,
        blank=True,
        db_index=True,
    )

    origen = models.CharField(
        max_length=24,
        choices=Origen.choices,
        default=Origen.ERROR_HTTP,
    )

    http_status = models.PositiveSmallIntegerField()

    estado = models.CharField(
        max_length=20,
        choices=Estado.choices,
        default=Estado.ABIERTO,
        db_index=True,
    )

    # Texto voluntario del usuario. No copiamos request.POST, headers,
    # cookies, tracebacks ni mensajes técnicos dentro del ticket.
    mensaje = models.TextField(
        max_length=1500,
        blank=True,
    )

    actor_usuario = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="foodback_support_reports",
    )

    actor_username = models.CharField(
        max_length=150,
        blank=True,
    )

    actor_role = models.CharField(
        max_length=40,
        blank=True,
    )

    tenant_public_id = models.UUIDField(
        null=True,
        blank=True,
    )

    tenant_nombre = models.CharField(
        max_length=150,
        blank=True,
    )

    sucursal_public_id = models.UUIDField(
        null=True,
        blank=True,
    )

    sucursal_nombre = models.CharField(
        max_length=150,
        blank=True,
    )

    creado_en = models.DateTimeField(
        auto_now_add=True,
        db_index=True,
    )

    actualizado_en = models.DateTimeField(
        auto_now=True,
    )

    def __str__(self):
        return (
            f"Soporte {self.public_id} - "
            f"HTTP {self.http_status} - "
            f"{self.estado}"
        )

    class Meta:
        verbose_name = "Reporte de soporte"
        verbose_name_plural = "Reportes de soporte"
        ordering = [
            "-creado_en",
        ]

        constraints = [
            models.CheckConstraint(
                check=models.Q(
                    http_status__in=[
                        400,
                        403,
                        404,
                        500,
                    ],
                ),
                name="support_http_status_allowed",
            ),
        ]

        indexes = [
            models.Index(
                fields=[
                    "estado",
                    "-creado_en",
                ],
                name="support_state_created_idx",
            ),
            models.Index(
                fields=[
                    "tenant_public_id",
                    "-creado_en",
                ],
                name="support_tenant_created_idx",
            ),
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



class MembershipSucursal(models.Model):
    """
    Define qué sucursales puede administrar
    un Membership.

    OWNER no necesita registros aquí:
    por definición puede administrar todas
    las sucursales de su Tenant.

    Para MANAGER, estas asignaciones determinan
    las sucursales permitidas.
    """

    membership = models.ForeignKey(
        Membership,
        on_delete=models.PROTECT,
        related_name="sucursales_permitidas",
    )

    sucursal = models.ForeignKey(
        Sucursal,
        on_delete=models.PROTECT,
        related_name="memberships_autorizados",
    )

    activo = models.BooleanField(
        default=True,
        db_index=True,
    )

    fecha_creacion = models.DateTimeField(
        auto_now_add=True,
    )

    actualizado_en = models.DateTimeField(
        auto_now=True,
    )

    def __str__(self):
        return (
            f"{self.membership.usuario.username} - "
            f"{self.sucursal.nombre}"
        )

    class Meta:
        verbose_name = "Acceso administrativo a sucursal"
        verbose_name_plural = (
            "Accesos administrativos a sucursales"
        )

        constraints = [
            models.UniqueConstraint(
                fields=[
                    "membership",
                    "sucursal",
                ],
                name=(
                    "unique_membership_"
                    "por_sucursal"
                ),
            ),
        ]

        indexes = [
            models.Index(
                fields=[
                    "membership",
                    "activo",
                ],
                name="member_branch_active_idx",
            ),
            models.Index(
                fields=[
                    "sucursal",
                    "activo",
                ],
                name="branch_member_active_idx",
            ),
        ]


class RepartidorSucursal(models.Model):
    """
    Autoriza a un usuario para trabajar como repartidor
    en una sucursal específica.

    Un mismo usuario puede estar habilitado en varias
    sucursales sin convertirse en MANAGER del Tenant.
    """

    usuario = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name="foodback_delivery_assignments",
    )

    sucursal = models.ForeignKey(
        "Sucursal",
        on_delete=models.PROTECT,
        related_name="repartidores_asignados",
    )

    activo = models.BooleanField(
        default=True,
        db_index=True,
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
            f"{self.sucursal}"
        )

    class Meta:
        verbose_name = "Asignación de repartidor"
        verbose_name_plural = "Asignaciones de repartidores"

        constraints = [
            models.UniqueConstraint(
                fields=[
                    "usuario",
                    "sucursal",
                ],
                name=(
                    "unique_repartidor_"
                    "por_sucursal"
                ),
            ),
        ]

        indexes = [
            models.Index(
                fields=[
                    "usuario",
                    "activo",
                ],
                name="delivery_user_active_idx",
            ),
            models.Index(
                fields=[
                    "sucursal",
                    "activo",
                ],
                name="delivery_branch_active_idx",
            ),
        ]


# --- NUEVO MODELO DE EXTRAS (Papas, Queso, Jalapeños...) ---
class Extra(models.Model):
    tenant = models.ForeignKey(
        "Tenant",
        on_delete=models.PROTECT,
        related_name="extras_catalogo",
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
    tenant = models.ForeignKey(
        "Tenant",
        on_delete=models.PROTECT,
        related_name="clientes",
        db_index=True,
    )

    telefono = models.CharField(
        max_length=15,
    )

    nombre = models.CharField(
        max_length=100,
    )

    apellido = models.CharField(
        max_length=100,
    )

    direccion_ultima = models.TextField(
        blank=True,
        null=True,
    )

    def __str__(self):
        return (
            f"{self.nombre} "
            f"{self.apellido} "
            f"({self.telefono})"
        )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=[
                    "tenant",
                    "telefono",
                ],
                name=(
                    "unique_cliente_telefono_"
                    "por_tenant"
                ),
            ),
        ]

# --- CEREBRO DEL TIEMPO ---


class ConfiguracionNegocio(models.Model):
    
    sucursal = models.OneToOneField(
        "Sucursal",
        on_delete=models.PROTECT,
        related_name="configuracion",
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
        db_index=True,
    )

    cliente = models.ForeignKey(
        Cliente,
        on_delete=models.PROTECT,
        related_name="pedidos",
    )
    
    checkout_token = models.UUIDField(
        null=True,
        blank=True,
        unique=True,
        editable=False,
        db_index=True,
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
    
    
    class Meta:
        indexes = [
            models.Index(
                fields=[
                    "sucursal",
                    "actualizado_en",
                ],
                name="pedido_suc_actual_idx",
            ),
        ]

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
    
    tenant = models.ForeignKey(
        "Tenant",
        on_delete=models.PROTECT,
        related_name="eventos_pago_wompi",
        db_index=True,
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
    Circuit breaker Wompi a nivel Tenant.

    Todas las sucursales del mismo Tenant comparten
    el estado de su pasarela, pero un Tenant jamás
    afecta a otro.
    """

    tenant = models.OneToOneField(
        "Tenant",
        on_delete=models.PROTECT,
        related_name="estado_pasarela_wompi",
    )

    # LEGACY TEMPORAL.
    # Se conserva mientras migramos registros históricos.
    configuracion_negocio = models.OneToOneField(
        ConfiguracionNegocio,
        on_delete=models.SET_NULL,
        related_name="estado_pasarela",
        null=True,
        blank=True,
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

        tenant = (
            self.tenant.nombre
            if self.tenant
            else "SIN TENANT"
        )

        return (
            f"Pasarela {tenant}: "
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
    
class RateLimitBucket(models.Model):
    grupo = models.CharField(
        max_length=80,
        db_index=True,
    )

    clave_hash = models.CharField(
        max_length=64,
        unique=True,
    )

    ventana_inicio = models.DateTimeField()

    contador = models.PositiveIntegerField(
        default=0,
    )

    bloqueado_hasta = models.DateTimeField(
        null=True,
        blank=True,
    )

    actualizado_en = models.DateTimeField(
        auto_now=True,
    )

    class Meta:
        indexes = [
            models.Index(
                fields=[
                    "grupo",
                    "actualizado_en",
                ],
                name="ratelimit_group_updated_idx",
            ),
        ]

    def __str__(self):
        return (
            f"{self.grupo} "
            f"({self.contador})"
        )
