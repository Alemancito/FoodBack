import logging
import uuid
from datetime import timedelta

from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.core.mail import send_mail
from django.db import IntegrityError, transaction
from django.db.models import F
from django.utils import timezone

from .models import (
    AuditEvent,
    SecurityIncident,
)


logger = logging.getLogger("foodback.security_incidents")


_SUSPICIOUS_RESULTS = {
    AuditEvent.Resultado.FALLO,
    AuditEvent.Resultado.BLOQUEADO,
    AuditEvent.Resultado.DENEGADO,
    AuditEvent.Resultado.ERROR,
}

_SUSPICIOUS_CATEGORIES = {
    AuditEvent.Categoria.AUTENTICACION,
    AuditEvent.Categoria.AUTORIZACION,
    AuditEvent.Categoria.SEGURIDAD,
    AuditEvent.Categoria.CUENTA,
}

_ACTIVE_STATES = {
    SecurityIncident.Estado.ABIERTO,
    SecurityIncident.Estado.RECONOCIDO,
}

_SEVERITY_RANK = {
    AuditEvent.Severidad.INFO: 0,
    AuditEvent.Severidad.BAJA: 1,
    AuditEvent.Severidad.MEDIA: 2,
    AuditEvent.Severidad.ALTA: 3,
    AuditEvent.Severidad.CRITICA: 4,
}

_EVENT_TITLES = {
    "auth.login.failed": (
        "Intentos repetidos de inicio de sesión"
    ),
    "auth.login.rate_limited": (
        "Inicio de sesión bloqueado por actividad repetida"
    ),
    "authz.tenant_role.denied": (
        "Accesos denegados por rol o Membership"
    ),
    "authz.branch_access.denied": (
        "Accesos administrativos denegados a sucursal"
    ),
    "authz.delivery_assignment.denied": (
        "Accesos de reparto sin asignación válida"
    ),
    "account.password_reset.request_rate_limited": (
        "Recuperación de contraseña bloqueada por actividad repetida"
    ),
    "account.password_reset.verify_rate_limited": (
        "Verificación de recuperación bloqueada por actividad repetida"
    ),
    "account.password_reset.verify_failed": (
        "Intentos fallidos de verificación de recuperación"
    ),
    "security.csrf.rejected": (
        "Solicitudes rechazadas por protección CSRF"
    ),
    "security.suspicious_request": (
        "Solicitudes HTTP sospechosas bloqueadas"
    ),
    "security.rate_limit.checkout": (
        "Checkout bloqueado por actividad repetida"
    ),
    "security.rate_limit.payment_start": (
        "Inicio de pago bloqueado por actividad repetida"
    ),
    "security.rate_limit.payment_resume": (
        "Reanudación de pago bloqueada por actividad repetida"
    ),
    "security.rate_limit.subscription_payment": (
        "Pago de suscripción bloqueado por actividad repetida"
    ),
    "security.rate_limit.pending_order_action": (
        "Acciones sobre pedidos pendientes bloqueadas por actividad repetida"
    ),
    "security.webhook.payload_too_large": (
        "Webhook rechazado por tamaño de payload"
    ),
    "security.webhook.signature_invalid": (
        "Webhook rechazado por firma inválida"
    ),
    "security.webhook.tenant_context_invalid": (
        "Webhook rechazado por contexto Tenant inválido"
    ),
    "security.webhook.invalid_json": (
        "Webhooks con JSON inválido"
    ),
}


def _config_int(nombre, default, minimo=1):
    try:
        valor = int(
            getattr(
                settings,
                nombre,
                default,
            )
        )
    except (
        TypeError,
        ValueError,
    ):
        valor = default

    return max(
        valor,
        minimo,
    )


def _incident_window_seconds():
    return _config_int(
        "FOODBACK_SECURITY_INCIDENT_WINDOW_SECONDS",
        300,
        minimo=60,
    )


def _alert_cooldown_seconds():
    return _config_int(
        "FOODBACK_SECURITY_ALERT_COOLDOWN_SECONDS",
        300,
        minimo=60,
    )


def _threshold_for_event(evento):
    """
    Umbral mínimo dentro de la ventana de correlación.

    ALTA/CRÍTICA representan señales que ya atravesaron otra defensa
    (por ejemplo un rate-limit), por lo que abren incidente de inmediato.
    MEDIA necesita repetición y BAJA necesita una repetición mayor.
    """

    return {
        AuditEvent.Severidad.CRITICA: 1,
        AuditEvent.Severidad.ALTA: 1,
        AuditEvent.Severidad.MEDIA: 3,
        AuditEvent.Severidad.BAJA: 5,
    }.get(
        evento.severidad,
        999999,
    )


def _max_severity(*severidades):
    disponibles = [
        valor
        for valor in severidades
        if valor in _SEVERITY_RANK
    ]

    if not disponibles:
        return AuditEvent.Severidad.INFO

    return max(
        disponibles,
        key=lambda valor: _SEVERITY_RANK[
            valor
        ],
    )


def _incident_severity_for_count(
    *,
    evento,
    threshold,
    count,
):
    """
    Una ráfaga de eventos BAJA/MEDIA escala el incidente.

    Ejemplo:
    - 5 fallos BAJA -> incidente MEDIA.
    - 10 fallos BAJA -> incidente ALTA.
    """

    if evento.severidad in {
        AuditEvent.Severidad.ALTA,
        AuditEvent.Severidad.CRITICA,
    }:
        return evento.severidad

    if count >= (
        threshold * 2
    ):
        return AuditEvent.Severidad.ALTA

    return AuditEvent.Severidad.MEDIA


def _title_for_event(evento):
    return _EVENT_TITLES.get(
        evento.evento,
        evento.descripcion
        or evento.evento,
    )[:180]


def _eligible_event(evento):
    if not evento:
        return False

    if not evento.fingerprint:
        return False

    if evento.resultado not in _SUSPICIOUS_RESULTS:
        return False

    if evento.categoria not in _SUSPICIOUS_CATEGORIES:
        return False

    if evento.severidad == AuditEvent.Severidad.INFO:
        return False

    return True


def _last_resolved_incident(fingerprint):
    return (
        SecurityIncident.objects
        .filter(
            fingerprint=fingerprint,
            estado=(
                SecurityIncident.Estado.RESUELTO
            ),
            resuelto_en__isnull=False,
        )
        .order_by(
            "-resuelto_en",
            "-pk",
        )
        .first()
    )


def _last_resolved_at(fingerprint):
    incidente = _last_resolved_incident(
        fingerprint
    )

    if incidente is None:
        return None

    return incidente.resuelto_en


def _recent_equivalent_events(
    *,
    evento,
    ahora,
):
    desde = (
        ahora
        - timedelta(
            seconds=(
                _incident_window_seconds()
            )
        )
    )

    ultimo_resuelto = (
        _last_resolved_incident(
            evento.fingerprint
        )
    )

    if (
        ultimo_resuelto is not None
        and
        ultimo_resuelto.resuelto_en
        and
        ultimo_resuelto.resuelto_en > desde
    ):
        desde = (
            ultimo_resuelto.resuelto_en
        )

    recientes = (
        AuditEvent.objects
        .filter(
            fingerprint=(
                evento.fingerprint
            ),
            creado_en__gte=desde,
            resultado__in=(
                _SUSPICIOUS_RESULTS
            ),
            categoria__in=(
                _SUSPICIOUS_CATEGORIES
            ),
        )
        .exclude(
            severidad=(
                AuditEvent.Severidad.INFO
            )
        )
    )

    # Resolver un incidente cierra esa oleada. Además del corte
    # temporal, usamos el último AuditEvent del incidente resuelto
    # como frontera determinista para impedir que el historial viejo
    # vuelva a contarse si existen timestamps iguales o muy cercanos.
    if (
        ultimo_resuelto is not None
        and
        ultimo_resuelto.evento_ultimo_id
    ):
        recientes = recientes.filter(
            pk__gt=(
                ultimo_resuelto.evento_ultimo_id
            )
        )

    return recientes.order_by(
        "creado_en",
        "pk",
    )


def _count_incident_events(
    incidente,
):
    eventos = (
        AuditEvent.objects
        .filter(
            fingerprint=(
                incidente.fingerprint
            ),
            creado_en__gte=(
                incidente.primero_visto_en
            ),
            resultado__in=(
                _SUSPICIOUS_RESULTS
            ),
            categoria__in=(
                _SUSPICIOUS_CATEGORIES
            ),
        )
        .exclude(
            severidad=(
                AuditEvent.Severidad.INFO
            )
        )
    )

    # Si dos oleadas comparten el mismo timestamp, el PK del evento
    # inicial impide que eventos del incidente anterior se mezclen
    # en el contador del incidente activo.
    if incidente.evento_inicial_id:
        eventos = eventos.filter(
            pk__gte=(
                incidente.evento_inicial_id
            )
        )

    return eventos.count()


def _notification_recipients():
    raw = str(
        getattr(
            settings,
            "FOODBACK_SECURITY_ALERT_EMAIL",
            "",
        )
        or ""
    ).strip()

    if not raw:
        return []

    return [
        item.strip()
        for item in raw.split(",")
        if item.strip()
    ]


def _alerts_enabled():
    return bool(
        getattr(
            settings,
            "FOODBACK_SECURITY_ALERTS_ENABLED",
            False,
        )
    )


def _should_reserve_notification(
    *,
    incidente,
    ahora,
):
    if not _alerts_enabled():
        return False

    if not _notification_recipients():
        return False

    if incidente.severidad not in {
        AuditEvent.Severidad.ALTA,
        AuditEvent.Severidad.CRITICA,
    }:
        return False

    if incidente.ultima_notificacion_en is None:
        return True

    return (
        ahora
        - incidente.ultima_notificacion_en
    ).total_seconds() >= (
        _alert_cooldown_seconds()
    )


def _build_alert_message(incidente):
    tenant = (
        incidente.tenant_nombre
        or "Sin tenant asociado"
    )

    sucursal = (
        incidente.sucursal_nombre
        or "Sin sucursal asociada"
    )

    ip = (
        str(incidente.ip)
        if incidente.ip
        else "No disponible"
    )

    ruta = (
        incidente.ruta
        or "No disponible"
    )

    return (
        "FoodBack detectó actividad de seguridad que requiere revisión.\n\n"
        f"Incidente: {incidente.public_id}\n"
        f"Severidad: {incidente.severidad}\n"
        f"Tipo: {incidente.evento_clave}\n"
        f"Eventos agrupados: {incidente.contador_eventos}\n"
        f"Primero visto: {incidente.primero_visto_en.isoformat()}\n"
        f"Último visto: {incidente.ultimo_visto_en.isoformat()}\n"
        f"IP: {ip}\n"
        f"Tenant: {tenant}\n"
        f"Sucursal: {sucursal}\n"
        f"Ruta: {ruta}\n\n"
        "FoodBack agrupa eventos equivalentes y aplica cooldown para "
        "evitar correos duplicados durante un mismo incidente."
    )


def _send_reserved_notification(
    *,
    incidente_id,
):
    """
    Se ejecuta fuera del lock de correlación.

    ultima_notificacion_en ya fue reservada dentro de la transacción,
    por lo que otra request concurrente no enviará el mismo correo.
    """

    incidente = (
        SecurityIncident.objects
        .filter(
            pk=incidente_id
        )
        .first()
    )

    if incidente is None:
        return False

    destinatarios = (
        _notification_recipients()
    )

    if not destinatarios:
        return False

    ahora = timezone.now()

    try:
        send_mail(
            subject=(
                "[FoodBack Seguridad] "
                f"{incidente.severidad} - "
                f"{incidente.titulo}"
            )[:200],
            message=(
                _build_alert_message(
                    incidente
                )
            ),
            from_email=getattr(
                settings,
                "DEFAULT_FROM_EMAIL",
                None,
            ),
            recipient_list=destinatarios,
            fail_silently=False,
        )

    except Exception as exc:
        # No persistimos el texto crudo de SMTP: algunos backends
        # pueden incluir detalles operativos que no pertenecen al log.
        error_seguro = (
            f"{exc.__class__.__name__}: "
            "fallo al enviar alerta de seguridad"
        )[:255]

        SecurityIncident.objects.filter(
            pk=incidente.pk
        ).update(
            fallos_notificacion=(
                F("fallos_notificacion")
                + 1
            ),
            ultimo_error_notificacion=(
                error_seguro
            ),
        )

        logger.exception(
            "No se pudo enviar alerta del incidente %s",
            incidente.public_id,
        )

        return False

    with transaction.atomic():
        locked = (
            SecurityIncident.objects
            .select_for_update()
            .filter(
                pk=incidente.pk
            )
            .first()
        )

        if locked is None:
            return False

        if (
            locked
            .primera_notificacion_exitosa_en
            is None
        ):
            locked.primera_notificacion_exitosa_en = ahora

        locked.ultima_notificacion_exitosa_en = ahora
        locked.notificaciones_enviadas += 1
        locked.ultimo_error_notificacion = ""

        locked.save(
            update_fields=[
                "primera_notificacion_exitosa_en",
                "ultima_notificacion_exitosa_en",
                "notificaciones_enviadas",
                "ultimo_error_notificacion",
                "actualizado_en",
            ]
        )

    return True


def procesar_evento_auditoria_para_incidentes(
    evento,
):
    """
    Correlaciona un AuditEvent sospechoso con un SecurityIncident.

    No sustituye al rate-limit ni a la autorización. Su trabajo es
    convertir señales ya registradas en incidentes investigables y
    deduplicados para Foundation/Superadmin.
    """

    if not _eligible_event(
        evento
    ):
        return None

    ahora = timezone.now()
    threshold = _threshold_for_event(
        evento
    )

    recientes = _recent_equivalent_events(
        evento=evento,
        ahora=ahora,
    )

    recent_count = recientes.count()

    incidente_id_para_notificar = None

    with transaction.atomic():
        incidente = (
            SecurityIncident.objects
            .select_for_update()
            .filter(
                fingerprint=(
                    evento.fingerprint
                ),
                estado__in=(
                    _ACTIVE_STATES
                ),
            )
            .first()
        )

        if incidente is None:
            if recent_count < threshold:
                return None

            primero = recientes.first()

            severidad_incidente = (
                _incident_severity_for_count(
                    evento=evento,
                    threshold=threshold,
                    count=recent_count,
                )
            )

            try:
                # Savepoint interno: si otra request gana la carrera
                # contra la constraint parcial, la transacción exterior
                # sigue utilizable y podremos cargar el incidente ganador.
                with transaction.atomic():
                    incidente = (
                        SecurityIncident.objects
                        .create(
                            fingerprint=(
                                evento.fingerprint
                            ),
                            evento_clave=(
                                evento.evento
                            ),
                            categoria=(
                                evento.categoria
                            ),
                            severidad=(
                                severidad_incidente
                            ),
                            estado=(
                                SecurityIncident
                                .Estado
                                .ABIERTO
                            ),
                            titulo=(
                                _title_for_event(
                                    evento
                                )
                            ),
                            descripcion=(
                                evento.descripcion
                            )[:255],
                            actor_username=(
                                evento.actor_username
                            ),
                            actor_role=(
                                evento.actor_role
                            ),
                            tenant_public_id=(
                                evento.tenant_public_id
                            ),
                            tenant_nombre=(
                                evento.tenant_nombre
                            ),
                            sucursal_public_id=(
                                evento.sucursal_public_id
                            ),
                            sucursal_nombre=(
                                evento.sucursal_nombre
                            ),
                            ip=evento.ip,
                            ruta=evento.ruta,
                            contador_eventos=(
                                recent_count
                            ),
                            primero_visto_en=(
                                primero.creado_en
                                if primero
                                else evento.creado_en
                            ),
                            ultimo_visto_en=(
                                evento.creado_en
                            ),
                            evento_inicial=(
                                primero or evento
                            ),
                            evento_ultimo=evento,
                        )
                    )

            except IntegrityError:
                incidente = (
                    SecurityIncident.objects
                    .select_for_update()
                    .get(
                        fingerprint=(
                            evento.fingerprint
                        ),
                        estado__in=(
                            _ACTIVE_STATES
                        ),
                    )
                )

        if incidente.evento_ultimo_id != evento.pk:
            total = _count_incident_events(
                incidente
            )

            severidad_nueva = (
                _incident_severity_for_count(
                    evento=evento,
                    threshold=threshold,
                    count=total,
                )
            )

            incidente.contador_eventos = total
            incidente.severidad = _max_severity(
                incidente.severidad,
                severidad_nueva,
                evento.severidad,
            )

            # En carreras concurrentes puede terminar procesándose después
            # un AuditEvent cronológicamente anterior.
            #
            # Orden determinista:
            #   1. mayor creado_en;
            #   2. ante empate exacto, mayor PK.
            #
            # El desempate por PK evita que dos eventos creados con el mismo
            # timestamp puedan hacer retroceder evento_ultimo según qué worker
            # termine de procesarse al final.
            evento_es_mas_reciente = (
                evento.creado_en
                > incidente.ultimo_visto_en
            )

            if (
                evento.creado_en
                == incidente.ultimo_visto_en
            ):
                evento_es_mas_reciente = (
                    incidente.evento_ultimo_id is None
                    or evento.pk
                    > incidente.evento_ultimo_id
                )

            if evento_es_mas_reciente:
                incidente.ultimo_visto_en = evento.creado_en
                incidente.evento_ultimo = evento

                # Conservamos snapshots recientes útiles para investigación,
                # pero nunca reducimos el contexto de Tenant/sucursal si la
                # señal nueva carece de él.
                if evento.actor_username:
                    incidente.actor_username = evento.actor_username

                if evento.actor_role:
                    incidente.actor_role = evento.actor_role

                if evento.tenant_public_id:
                    incidente.tenant_public_id = evento.tenant_public_id
                    incidente.tenant_nombre = evento.tenant_nombre

                if evento.sucursal_public_id:
                    incidente.sucursal_public_id = evento.sucursal_public_id
                    incidente.sucursal_nombre = evento.sucursal_nombre

                if evento.ip:
                    incidente.ip = evento.ip

                if evento.ruta:
                    incidente.ruta = evento.ruta

        if _should_reserve_notification(
            incidente=incidente,
            ahora=ahora,
        ):
            incidente.ultima_notificacion_en = ahora
            incidente_id_para_notificar = incidente.pk

        incidente.save(
            update_fields=[
                "contador_eventos",
                "ultimo_visto_en",
                "evento_ultimo",
                "severidad",
                "actor_username",
                "actor_role",
                "tenant_public_id",
                "tenant_nombre",
                "sucursal_public_id",
                "sucursal_nombre",
                "ip",
                "ruta",
                "ultima_notificacion_en",
                "actualizado_en",
            ]
        )

    if incidente_id_para_notificar:
        # Si el evento/incidente nació dentro de una transacción mayor,
        # el correo solo debe salir cuando la operación realmente haya
        # quedado confirmada en PostgreSQL. Así evitamos alertas sobre
        # eventos que después terminaron en rollback.
        transaction.on_commit(
            lambda incidente_id=incidente_id_para_notificar: (
                _send_reserved_notification(
                    incidente_id=incidente_id
                )
            )
        )

    return incidente


def _exigir_foundation_superadmin(request):
    """
    Frontera de autorización para acciones operativas sobre incidentes.

    OWNER sigue significando dueño del restaurante cliente. Estas
    operaciones pertenecen exclusivamente al control-plane interno de
    FoodBack y por ahora se representan con is_superuser.
    """

    user = getattr(
        request,
        "user",
        None,
    )

    if not (
        user
        and getattr(
            user,
            "is_authenticated",
            False,
        )
        and getattr(
            user,
            "is_active",
            False,
        )
        and getattr(
            user,
            "is_superuser",
            False,
        )
    ):
        raise PermissionDenied(
            "Esta acción requiere Foundation/Superadmin FoodBack."
        )

    return user


def _normalizar_incident_public_id(public_id):
    try:
        return uuid.UUID(
            str(public_id)
        )
    except (
        TypeError,
        ValueError,
        AttributeError,
    ):
        return None


def _auditar_transicion_incidente(
    *,
    request,
    incidente,
    evento,
    descripcion,
    estado_anterior,
):
    # Import local para evitar dependencia circular:
    # audit.py importa este módulo cuando correlaciona eventos.
    from .audit import registrar_evento_auditoria

    registrar_evento_auditoria(
        request=request,
        evento=evento,
        categoria=AuditEvent.Categoria.SEGURIDAD,
        severidad=AuditEvent.Severidad.INFO,
        resultado=AuditEvent.Resultado.EXITO,
        descripcion=descripcion,
        fuente=AuditEvent.Fuente.WEB,
        objeto_tipo="SecurityIncident",
        objeto_id=str(
            incidente.public_id
        ),
        metadata={
            "incident_public_id": str(
                incidente.public_id
            ),
            "previous_state": estado_anterior,
            "new_state": incidente.estado,
            "severity": incidente.severidad,
            "event_count": incidente.contador_eventos,
        },
        fail_silently=True,
    )


def reconocer_incidente_seguridad(
    *,
    request,
    public_id,
):
    """
    Marca un incidente ABIERTO como RECONOCIDO.

    La operación es idempotente: repetirla sobre un incidente ya
    reconocido no genera otro AuditEvent ni cambia timestamps.
    Un incidente resuelto no puede retroceder de estado.
    """

    _exigir_foundation_superadmin(
        request
    )

    public_uuid = (
        _normalizar_incident_public_id(
            public_id
        )
    )

    if public_uuid is None:
        return None

    ahora = timezone.now()
    cambio = False
    estado_anterior = ""

    with transaction.atomic():
        incidente = (
            SecurityIncident.objects
            .select_for_update()
            .filter(
                public_id=public_uuid
            )
            .first()
        )

        if incidente is None:
            return None

        if (
            incidente.estado
            == SecurityIncident.Estado.RESUELTO
        ):
            return incidente

        if (
            incidente.estado
            == SecurityIncident.Estado.ABIERTO
        ):
            estado_anterior = (
                incidente.estado
            )

            incidente.estado = (
                SecurityIncident
                .Estado
                .RECONOCIDO
            )

            if incidente.reconocido_en is None:
                incidente.reconocido_en = ahora

            incidente.save(
                update_fields=[
                    "estado",
                    "reconocido_en",
                    "actualizado_en",
                ]
            )

            cambio = True

    if cambio:
        _auditar_transicion_incidente(
            request=request,
            incidente=incidente,
            evento=(
                "security.incident.acknowledged"
            ),
            descripcion=(
                "Incidente de seguridad reconocido por "
                "Foundation/Superadmin."
            ),
            estado_anterior=estado_anterior,
        )

    return incidente


def resolver_incidente_seguridad(
    *,
    request,
    public_id,
):
    """
    Marca un incidente activo como RESUELTO.

    Resolverlo libera la constraint parcial del fingerprint para que una
    futura oleada equivalente pueda abrir un incidente nuevo conservando
    el historial anterior. La operación también es idempotente.
    """

    _exigir_foundation_superadmin(
        request
    )

    public_uuid = (
        _normalizar_incident_public_id(
            public_id
        )
    )

    if public_uuid is None:
        return None

    ahora = timezone.now()
    cambio = False
    estado_anterior = ""

    with transaction.atomic():
        incidente = (
            SecurityIncident.objects
            .select_for_update()
            .filter(
                public_id=public_uuid
            )
            .first()
        )

        if incidente is None:
            return None

        if (
            incidente.estado
            == SecurityIncident.Estado.RESUELTO
        ):
            return incidente

        estado_anterior = (
            incidente.estado
        )

        incidente.estado = (
            SecurityIncident.Estado.RESUELTO
        )
        incidente.resuelto_en = ahora

        incidente.save(
            update_fields=[
                "estado",
                "resuelto_en",
                "actualizado_en",
            ]
        )

        cambio = True

    if cambio:
        _auditar_transicion_incidente(
            request=request,
            incidente=incidente,
            evento=(
                "security.incident.resolved"
            ),
            descripcion=(
                "Incidente de seguridad resuelto por "
                "Foundation/Superadmin."
            ),
            estado_anterior=estado_anterior,
        )

    return incidente
