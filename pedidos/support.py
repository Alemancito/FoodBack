import uuid

from django.conf import settings
from django.contrib.auth.models import AnonymousUser
from django.core import signing
from django.db import transaction
from django.shortcuts import render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST

from .audit import (
    obtener_request_id,
    registrar_evento_auditoria,
    resolver_actor_role_auditoria,
)
from .models import (
    AuditEvent,
    SupportReport,
)
from .security import (
    consumir_rate_limit,
    obtener_id_seguridad_cliente,
    obtener_ip_cliente,
)


_SUPPORT_TOKEN_SALT = "foodback.support.error-report.v1"

_ALLOWED_HTTP_STATUS = {
    400,
    403,
    404,
    500,
}


def _setting_int(
    name,
    default,
    *,
    minimum=1,
):
    try:
        value = int(
            getattr(
                settings,
                name,
                default,
            )
        )
    except (
        TypeError,
        ValueError,
    ):
        value = default

    return max(
        value,
        minimum,
    )


def _support_token_max_age_seconds():
    return _setting_int(
        "FOODBACK_SUPPORT_REPORT_TOKEN_MAX_AGE_SECONDS",
        86400,
        minimum=300,
    )


def crear_token_reporte_error(
    *,
    request_id,
    status_code,
):
    """
    Firma el contexto mínimo que puede volver desde el navegador.

    Solo viajan:
    - request_id aleatorio;
    - status HTTP permitido.

    Tenant, sucursal, actor y demás contexto nunca se aceptan desde POST.
    """

    try:
        request_uuid = uuid.UUID(
            str(
                request_id
            )
        )
        status_code = int(
            status_code
        )
    except (
        TypeError,
        ValueError,
        AttributeError,
    ):
        return ""

    if (
        status_code
        not in _ALLOWED_HTTP_STATUS
    ):
        return ""

    return signing.dumps(
        {
            "v": 1,
            "request_id": str(
                request_uuid
            ),
            "status_code": status_code,
        },
        salt=_SUPPORT_TOKEN_SALT,
        compress=True,
    )


def _leer_token_reporte_error(
    token,
):
    try:
        payload = signing.loads(
            str(
                token or ""
            ),
            salt=_SUPPORT_TOKEN_SALT,
            max_age=(
                _support_token_max_age_seconds()
            ),
        )
    except signing.BadSignature:
        return None

    if not isinstance(
        payload,
        dict,
    ):
        return None

    if payload.get(
        "v"
    ) != 1:
        return None

    try:
        request_id = uuid.UUID(
            str(
                payload.get(
                    "request_id",
                    "",
                )
            )
        )

        status_code = int(
            payload.get(
                "status_code"
            )
        )
    except (
        TypeError,
        ValueError,
        AttributeError,
    ):
        return None

    if (
        status_code
        not in _ALLOWED_HTTP_STATUS
    ):
        return None

    return {
        "request_id": request_id,
        "status_code": status_code,
    }


def _normalizar_mensaje_reporte(
    value,
):
    # PostgreSQL no acepta NUL en text. Además acotamos explícitamente
    # el contenido voluntario para no convertir soporte en un dump.
    return (
        str(
            value or ""
        )
        .replace(
            "\x00",
            "",
        )
        .strip()
    )[:1500]


def _auditar_soporte(
    *,
    request,
    evento,
    resultado,
    descripcion,
    status_code,
    objeto_id="",
    metadata=None,
    severidad=AuditEvent.Severidad.INFO,
):
    registrar_evento_auditoria(
        request=request,
        evento=evento,
        categoria=(
            AuditEvent.Categoria.SOPORTE
        ),
        severidad=severidad,
        resultado=resultado,
        descripcion=descripcion,
        status_code=status_code,
        objeto_tipo=(
            "SupportReport"
            if objeto_id
            else ""
        ),
        objeto_id=objeto_id,
        metadata=metadata,
        fail_silently=True,
    )


def _support_rate_limit(
    request,
):
    tenant = getattr(
        request,
        "tenant",
        None,
    )

    tenant_id = (
        getattr(
            tenant,
            "public_id",
            None,
        )
        or "global"
    )

    client_id = (
        obtener_id_seguridad_cliente(
            request
        )
    )

    ip = (
        obtener_ip_cliente(
            request
        )
        or "unknown"
    )

    window_seconds = _setting_int(
        "FOODBACK_SUPPORT_REPORT_WINDOW_SECONDS",
        3600,
        minimum=60,
    )

    block_seconds = _setting_int(
        "FOODBACK_SUPPORT_REPORT_BLOCK_SECONDS",
        3600,
        minimum=60,
    )

    client_limit = consumir_rate_limit(
        group="support-report-client",
        raw_key=(
            f"{tenant_id}:"
            f"{client_id}"
        ),
        limite=_setting_int(
            "FOODBACK_SUPPORT_REPORT_SESSION_LIMIT",
            5,
        ),
        ventana_segundos=window_seconds,
        bloqueo_segundos=block_seconds,
    )

    if not client_limit[
        "permitido"
    ]:
        return (
            client_limit,
            "session",
        )

    ip_limit = consumir_rate_limit(
        group="support-report-ip",
        # IP global: evita repartir un flood entre muchos Tenant.
        raw_key=str(
            ip
        ),
        limite=_setting_int(
            "FOODBACK_SUPPORT_REPORT_IP_LIMIT",
            20,
        ),
        ventana_segundos=window_seconds,
        bloqueo_segundos=block_seconds,
    )

    if not ip_limit[
        "permitido"
    ]:
        return (
            ip_limit,
            "ip",
        )

    return (
        None,
        "",
    )


def _actor_user_from_request(
    request,
):
    user = getattr(
        request,
        "user",
        None,
    )

    if (
        not user
        or isinstance(
            user,
            AnonymousUser,
        )
        or not getattr(
            user,
            "is_authenticated",
            False,
        )
    ):
        return None

    return user


def _support_snapshot(
    request,
):
    actor_user = (
        _actor_user_from_request(
            request
        )
    )

    tenant = getattr(
        request,
        "tenant",
        None,
    )

    sucursal = getattr(
        request,
        "sucursal",
        None,
    )

    actor_username = ""

    if actor_user is not None:
        actor_username = str(
            getattr(
                actor_user,
                "username",
                "",
            )
            or ""
        )[:150]

    return {
        "actor_usuario": actor_user,
        "actor_username": actor_username,
        "actor_role": (
            resolver_actor_role_auditoria(
                request,
                actor_user=actor_user,
            )
        )[:40],
        "tenant_public_id": getattr(
            tenant,
            "public_id",
            None,
        ),
        "tenant_nombre": str(
            getattr(
                tenant,
                "nombre",
                "",
            )
            or ""
        )[:150],
        "sucursal_public_id": getattr(
            sucursal,
            "public_id",
            None,
        ),
        "sucursal_nombre": str(
            getattr(
                sucursal,
                "nombre",
                "",
            )
            or ""
        )[:150],
    }


@never_cache
@require_POST
def support_report_error_view(
    request,
):
    """
    Recibe un reporte humano originado en una pantalla de error.

    El navegador solo aporta:
    - token firmado por FoodBack;
    - descripción voluntaria.

    Ningún actor/Tenant/sucursal enviado por el cliente se consulta.
    """

    from core.error_handlers import (
        render_error_response,
    )

    token_data = (
        _leer_token_reporte_error(
            request.POST.get(
                "report_token",
                "",
            )
        )
    )

    if token_data is None:
        _auditar_soporte(
            request=request,
            evento=(
                "support.report.invalid_token"
            ),
            resultado=(
                AuditEvent.Resultado.FALLO
            ),
            descripcion=(
                "Reporte de soporte rechazado por "
                "contexto firmado inválido o vencido."
            ),
            status_code=400,
            severidad=(
                AuditEvent.Severidad.BAJA
            ),
        )

        return render_error_response(
            request,
            status_code=400,
            title=(
                "No pudimos validar el reporte"
            ),
            message=(
                "La referencia del error venció o no es válida. "
                "Vuelve a la pantalla anterior e inténtalo nuevamente."
            ),
            allow_report=False,
        )

    rate_limit, scope = (
        _support_rate_limit(
            request
        )
    )

    if rate_limit is not None:
        retry_after = max(
            int(
                rate_limit.get(
                    "retry_after",
                    60,
                )
                or 60
            ),
            1,
        )

        _auditar_soporte(
            request=request,
            evento=(
                "support.report.rate_limited"
            ),
            resultado=(
                AuditEvent.Resultado.BLOQUEADO
            ),
            descripcion=(
                "Reporte de soporte bloqueado por "
                "actividad repetida."
            ),
            status_code=429,
            severidad=(
                AuditEvent.Severidad.BAJA
            ),
            metadata={
                "scope": scope,
            },
        )

        response = render_error_response(
            request,
            status_code=429,
            title=(
                "Ya recibimos varios reportes"
            ),
            message=(
                "Espera un momento antes de enviar otro. "
                "Los reportes anteriores permanecen guardados."
            ),
            allow_report=False,
        )

        response.headers[
            "Retry-After"
        ] = str(
            retry_after
        )

        return response

    source_request_id = (
        token_data[
            "request_id"
        ]
    )

    http_status = (
        token_data[
            "status_code"
        ]
    )

    mensaje = (
        _normalizar_mensaje_reporte(
            request.POST.get(
                "message",
                "",
            )
        )
    )

    submission_request_id = (
        obtener_request_id(
            request
        )
    )

    snapshot = _support_snapshot(
        request
    )

    # source_request_id es UNIQUE: get_or_create vuelve el envío
    # idempotente incluso ante doble clic o reintento del navegador.
    with transaction.atomic():
        report, created = (
            SupportReport.objects
            .get_or_create(
                source_request_id=(
                    source_request_id
                ),
                defaults={
                    "submission_request_id": (
                        submission_request_id
                    ),
                    "http_status": (
                        http_status
                    ),
                    "mensaje": mensaje,
                    **snapshot,
                },
            )
        )

    if created:
        _auditar_soporte(
            request=request,
            evento="support.report.created",
            resultado=(
                AuditEvent.Resultado.EXITO
            ),
            descripcion=(
                "Usuario envió un reporte de soporte "
                "desde una pantalla de error."
            ),
            status_code=200,
            objeto_id=str(
                report.public_id
            ),
            metadata={
                "source_request_id": str(
                    source_request_id
                ),
                "http_status": http_status,
                "message_present": bool(
                    mensaje
                ),
                "message_length": len(
                    mensaje
                ),
            },
        )

    response = render(
        request,
        "support/report_done.html",
        {
            "report": report,
            "created": created,
            "source_request_id": str(
                source_request_id
            ),
        },
        status=200,
    )

    response.headers[
        "X-Request-ID"
    ] = str(
        submission_request_id
    )

    # never_cache ya añade una política fuerte; explicitamos no-store
    # porque esta página contiene referencias de soporte.
    response.headers[
        "Cache-Control"
    ] = "no-store"

    return response
