import hashlib
import hmac
import json
import logging
import uuid

from django.conf import settings
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import (
    PermissionDenied,
    SuspiciousOperation,
)
from django.http import Http404
from django.views.csrf import csrf_failure as django_csrf_failure

from .models import (
    AuditEvent,
    Membership,
    RepartidorSucursal,
)


logger = logging.getLogger("foodback.audit")


_REDACTED = "[REDACTED]"

_SENSITIVE_KEY_PARTS = (
    "password",
    "passwd",
    "contrasena",
    "contraseña",
    "secret",
    "token",
    "authorization",
    "cookie",
    "csrf",
    "session",
    "api_key",
    "apikey",
    "private_key",
    "access_key",
    "codigo",
    "código",
    "otp",
    "wompi_token",
)


def hash_valor_auditoria(valor):
    """
    Pseudonimiza un valor sensible de baja entropía
    (por ejemplo username/email) usando HMAC.

    Permite correlacionar eventos sin persistir el valor
    original en el log de auditoría.
    """

    normalizado = str(
        valor or ""
    ).strip().lower()

    if not normalizado:
        return ""

    return hmac.new(
        key=settings.SECRET_KEY.encode(
            "utf-8"
        ),
        msg=normalizado.encode(
            "utf-8"
        ),
        digestmod=hashlib.sha256,
    ).hexdigest()


def _clave_es_sensible(clave):
    clave_normalizada = str(
        clave or ""
    ).strip().lower()

    return any(
        parte in clave_normalizada
        for parte in _SENSITIVE_KEY_PARTS
    )


def _sanitizar_valor(
    valor,
    *,
    profundidad=0,
):
    """
    Convierte metadata de auditoría a un subconjunto seguro
    y acotado de JSON.

    Nunca debe utilizarse para volcar request.POST/headers
    completos. La auditoría recibe metadata explícita y esta
    función aporta una segunda capa defensiva.
    """

    if profundidad >= 4:
        return "[MAX_DEPTH]"

    if valor is None or isinstance(
        valor,
        (
            bool,
            int,
            float,
        ),
    ):
        return valor

    if isinstance(
        valor,
        str,
    ):
        return valor[:500]

    if isinstance(
        valor,
        uuid.UUID,
    ):
        return str(
            valor
        )

    if isinstance(
        valor,
        dict,
    ):
        resultado = {}

        for indice, (
            clave,
            item,
        ) in enumerate(
            valor.items()
        ):
            if indice >= 50:
                resultado[
                    "_truncated"
                ] = True
                break

            clave_texto = str(
                clave
            )[:120]

            if _clave_es_sensible(
                clave_texto
            ):
                resultado[
                    clave_texto
                ] = _REDACTED
                continue

            resultado[
                clave_texto
            ] = _sanitizar_valor(
                item,
                profundidad=(
                    profundidad + 1
                ),
            )

        return resultado

    if isinstance(
        valor,
        (
            list,
            tuple,
            set,
        ),
    ):
        return [
            _sanitizar_valor(
                item,
                profundidad=(
                    profundidad + 1
                ),
            )
            for item in list(
                valor
            )[:20]
        ]

    return str(
        valor
    )[:500]


def sanitizar_metadata_auditoria(
    metadata,
):
    if metadata is None:
        return {}

    if not isinstance(
        metadata,
        dict,
    ):
        metadata = {
            "valor": metadata,
        }

    return _sanitizar_valor(
        metadata
    )


def _obtener_request_id(
    request,
):
    if request is None:
        return None

    existente = getattr(
        request,
        "foodback_audit_request_id",
        None,
    )

    if existente:
        try:
            return uuid.UUID(
                str(
                    existente
                )
            )

        except (
            TypeError,
            ValueError,
            AttributeError,
        ):
            pass

    request_id = uuid.uuid4()

    setattr(
        request,
        "foodback_audit_request_id",
        request_id,
    )

    return request_id


def _obtener_ip_request(
    request,
):
    if request is None:
        return None

    # Import local para mantener audit.py desacoplado del
    # módulo security.py y evitar ciclos de importación.
    from .security import obtener_ip_cliente

    return obtener_ip_cliente(
        request
    )


def _resolver_actor_role(
    *,
    request,
    actor_user,
    actor_role,
):
    if actor_role:
        return str(
            actor_role
        )[:40]

    if (
        not actor_user
        or isinstance(
            actor_user,
            AnonymousUser,
        )
        or not getattr(
            actor_user,
            "is_authenticated",
            False,
        )
    ):
        return ""

    # Solo describe el actor para auditoría. NO concede
    # autorización de restaurante ni sustituye los roles
    # FoodBack de authz.py.
    if getattr(
        actor_user,
        "is_superuser",
        False,
    ):
        return "SUPERADMIN_FOODBACK"

    membership = getattr(
        request,
        "membership",
        None,
    ) if request is not None else None

    if (
        membership
        and getattr(
            membership,
            "usuario_id",
            None,
        ) == actor_user.pk
    ):
        return str(
            membership.rol
        )[:40]

    tenant = getattr(
        request,
        "tenant",
        None,
    ) if request is not None else None

    if tenant is not None:
        membership = (
            Membership.objects
            .filter(
                tenant=tenant,
                usuario=actor_user,
                activo=True,
            )
            .only(
                "rol",
            )
            .first()
        )

        if membership:
            return str(
                membership.rol
            )[:40]

    asignacion = getattr(
        request,
        "delivery_assignment",
        None,
    ) if request is not None else None

    if asignacion is not None:
        return "DELIVERY"

    sucursal = getattr(
        request,
        "sucursal",
        None,
    ) if request is not None else None

    if sucursal is not None:
        try:
            if (
                RepartidorSucursal.objects
                .filter(
                    usuario=actor_user,
                    sucursal=sucursal,
                    activo=True,
                )
                .exists()
            ):
                return "DELIVERY"
        except Exception:
            # La resolución de rol es enriquecimiento.
            # No debe romper la persistencia del evento.
            pass

    return "USUARIO"


def _crear_fingerprint(
    *,
    evento,
    ip,
    actor_user_id,
    tenant_public_id,
    ruta,
    objeto_tipo,
    objeto_id,
):
    contenido = {
        "evento": evento or "",
        "ip": ip or "",
        "actor_user_id": (
            actor_user_id or ""
        ),
        "tenant_public_id": str(
            tenant_public_id or ""
        ),
        "ruta": ruta or "",
        "objeto_tipo": objeto_tipo or "",
        "objeto_id": objeto_id or "",
    }

    serializado = json.dumps(
        contenido,
        sort_keys=True,
        separators=(
            ",",
            ":",
        ),
        ensure_ascii=False,
    )

    return hmac.new(
        key=settings.SECRET_KEY.encode(
            "utf-8"
        ),
        msg=serializado.encode(
            "utf-8"
        ),
        digestmod=hashlib.sha256,
    ).hexdigest()


def registrar_evento_auditoria(
    *,
    evento,
    categoria,
    severidad=AuditEvent.Severidad.INFO,
    resultado=AuditEvent.Resultado.INFORMATIVO,
    descripcion="",
    request=None,
    actor_user=None,
    actor_role="",
    tenant=None,
    sucursal=None,
    status_code=None,
    fuente=AuditEvent.Fuente.WEB,
    objeto_tipo="",
    objeto_id="",
    metadata=None,
    fail_silently=True,
):
    """
    Punto único para persistir auditoría de FoodBack.

    Principios:
    - append-only;
    - no guardar cuerpos HTTP, contraseñas, tokens ni OTP;
    - snapshots de actor/tenant/sucursal para investigación;
    - fingerprint estable para futura deduplicación/detección;
    - un fallo del subsistema de auditoría no debe tumbar el
      flujo principal de la aplicación.
    """

    try:
        if actor_user is None and request is not None:
            request_user = getattr(
                request,
                "user",
                None,
            )

            if (
                request_user
                and getattr(
                    request_user,
                    "is_authenticated",
                    False,
                )
            ):
                actor_user = request_user

        if (
            actor_user is not None
            and not getattr(
                actor_user,
                "is_authenticated",
                False,
            )
        ):
            actor_user = None

        if tenant is None and request is not None:
            tenant = getattr(
                request,
                "tenant",
                None,
            )

        if sucursal is None and request is not None:
            sucursal = getattr(
                request,
                "sucursal",
                None,
            )

        if actor_user is not None:
            actor_tipo = (
                AuditEvent.ActorTipo.USUARIO
            )
        elif (
            fuente
            == AuditEvent.Fuente.SISTEMA
        ):
            actor_tipo = (
                AuditEvent.ActorTipo.SISTEMA
            )
        else:
            actor_tipo = (
                AuditEvent.ActorTipo.ANONIMO
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

        actor_role_resuelto = (
            _resolver_actor_role(
                request=request,
                actor_user=actor_user,
                actor_role=actor_role,
            )
        )

        tenant_public_id = getattr(
            tenant,
            "public_id",
            None,
        )

        tenant_nombre = str(
            getattr(
                tenant,
                "nombre",
                "",
            )
            or ""
        )[:150]

        sucursal_public_id = getattr(
            sucursal,
            "public_id",
            None,
        )

        sucursal_nombre = str(
            getattr(
                sucursal,
                "nombre",
                "",
            )
            or ""
        )[:150]

        ip = _obtener_ip_request(
            request
        )

        metodo_http = ""
        ruta = ""
        user_agent = ""

        if request is not None:
            metodo_http = str(
                getattr(
                    request,
                    "method",
                    "",
                )
                or ""
            )[:10]

            # request.path nunca incluye el query string.
            # Esto evita registrar accidentalmente tokens
            # enviados por URL.
            ruta = str(
                getattr(
                    request,
                    "path",
                    "",
                )
                or ""
            )[:500]

            user_agent = str(
                request.META.get(
                    "HTTP_USER_AGENT",
                    "",
                )
                or ""
            )[:300]

        metadata_segura = (
            sanitizar_metadata_auditoria(
                metadata
            )
        )

        fingerprint = (
            _crear_fingerprint(
                evento=evento,
                ip=ip,
                actor_user_id=(
                    getattr(
                        actor_user,
                        "pk",
                        None,
                    )
                ),
                tenant_public_id=(
                    tenant_public_id
                ),
                ruta=ruta,
                objeto_tipo=objeto_tipo,
                objeto_id=objeto_id,
            )
        )

        evento_creado = AuditEvent.objects.create(
            request_id=(
                _obtener_request_id(
                    request
                )
            ),
            evento=str(
                evento
            )[:120],
            categoria=categoria,
            severidad=severidad,
            resultado=resultado,
            fuente=fuente,
            descripcion=str(
                descripcion or ""
            )[:255],
            actor_tipo=actor_tipo,
            actor_usuario=actor_user,
            actor_username=(
                actor_username
            ),
            actor_role=(
                actor_role_resuelto
            ),
            tenant_public_id=(
                tenant_public_id
            ),
            tenant_nombre=tenant_nombre,
            sucursal_public_id=(
                sucursal_public_id
            ),
            sucursal_nombre=(
                sucursal_nombre
            ),
            ip=ip,
            metodo_http=metodo_http,
            ruta=ruta,
            status_code=status_code,
            user_agent=user_agent,
            objeto_tipo=str(
                objeto_tipo or ""
            )[:80],
            objeto_id=str(
                objeto_id or ""
            )[:120],
            fingerprint=fingerprint,
            metadata=metadata_segura,
        )

        # Correlación de incidentes desacoplada del flujo principal.
        # Un fallo de detección/alerta jamás debe convertir en fallo una
        # operación válida ni ocultar que AuditEvent sí fue persistido.
        try:
            from .incidents import (
                procesar_evento_auditoria_para_incidentes,
            )

            procesar_evento_auditoria_para_incidentes(
                evento_creado
            )

        except Exception:
            logger.exception(
                "No se pudo correlacionar incidente para AuditEvent %s",
                evento_creado.public_id,
            )

        return evento_creado

    except Exception:
        logger.exception(
            "No se pudo persistir evento de auditoría %s",
            str(
                evento
            )[:120],
        )

        if not fail_silently:
            raise

        return None


def registrar_error_runtime(
    *,
    request,
    exception,
):
    """
    Registra errores que escaparon de una vista sin persistir
    el mensaje crudo de la excepción ni un traceback en BD.

    PermissionDenied/404 son respuestas esperadas y se excluyen aquí
    porque las fronteras de autorización sensibles ya se auditan donde
    se toman. SuspiciousOperation sí se considera señal de seguridad.
    """

    if isinstance(
        exception,
        (
            PermissionDenied,
            Http404,
        ),
    ):
        return None

    if isinstance(
        exception,
        SuspiciousOperation,
    ):
        return registrar_evento_auditoria(
            request=request,
            evento="security.suspicious_request",
            categoria=AuditEvent.Categoria.SEGURIDAD,
            severidad=AuditEvent.Severidad.ALTA,
            resultado=AuditEvent.Resultado.BLOQUEADO,
            descripcion=(
                "Solicitud HTTP sospechosa bloqueada por Django."
            ),
            status_code=400,
            metadata={
                "exception_type": (
                    exception.__class__.__name__
                ),
            },
            fail_silently=True,
        )

    return registrar_evento_auditoria(
        request=request,
        evento="system.unhandled_exception",
        categoria=AuditEvent.Categoria.SISTEMA,
        severidad=AuditEvent.Severidad.ALTA,
        resultado=AuditEvent.Resultado.ERROR,
        descripcion=(
            "Una vista produjo una excepción no controlada."
        ),
        status_code=500,
        metadata={
            # Guardamos únicamente el tipo. El mensaje podría contener
            # datos sensibles aportados por usuario/proveedor.
            "exception_type": (
                exception.__class__.__name__
            ),
        },
        fail_silently=True,
    )


def csrf_failure_view(
    request,
    reason="",
):
    """
    Failure view CSRF de FoodBack.

    Registra la denegación sin almacenar el reason crudo, token CSRF,
    cookies ni cuerpo de la petición, y delega la respuesta 403 al
    handler estándar de Django.
    """

    registrar_evento_auditoria(
        request=request,
        evento="security.csrf.rejected",
        categoria=AuditEvent.Categoria.SEGURIDAD,
        severidad=AuditEvent.Severidad.MEDIA,
        resultado=AuditEvent.Resultado.BLOQUEADO,
        descripcion=(
            "Solicitud rechazada por la protección CSRF."
        ),
        status_code=403,
        metadata={
            "reason_class": "csrf_validation_failed",
        },
        fail_silently=True,
    )

    return django_csrf_failure(
        request,
        reason=reason,
    )
