from functools import wraps

from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import (
    PermissionDenied,
    SuspiciousOperation,
)
from django.http import (
    Http404,
    JsonResponse,
)

from .audit import (
    obtener_request_id,
    registrar_error_runtime,
)


_API_ERROR_DEFAULTS = {
    400: (
        "invalid_request",
        "La solicitud no es válida.",
    ),
    401: (
        "authentication_required",
        "Debes iniciar sesión para continuar.",
    ),
    403: (
        "forbidden",
        "No tienes permiso para realizar esta acción.",
    ),
    404: (
        "not_found",
        "No se encontró el recurso solicitado.",
    ),
    405: (
        "method_not_allowed",
        "Método HTTP no permitido para este endpoint.",
    ),
    413: (
        "payload_too_large",
        "La solicitud supera el tamaño permitido.",
    ),
    415: (
        "unsupported_media_type",
        "El tipo de contenido enviado no es compatible.",
    ),
    429: (
        "rate_limited",
        "Demasiadas solicitudes. Inténtalo nuevamente más tarde.",
    ),
    500: (
        "internal_error",
        "Ocurrió un error interno. Inténtalo nuevamente más tarde.",
    ),
}


def is_api_request(
    request,
):
    if request is None:
        return False

    path = str(
        getattr(
            request,
            "path",
            "",
        )
        or ""
    )

    return path.startswith(
        "/api/"
    )


def api_error_response(
    request,
    *,
    status_code,
    code=None,
    message=None,
    source_response=None,
):
    default_code, default_message = (
        _API_ERROR_DEFAULTS.get(
            status_code,
            _API_ERROR_DEFAULTS[500],
        )
    )

    request_id = obtener_request_id(
        request
    )

    response = JsonResponse(
        {
            "ok": False,
            "error": {
                "code": (
                    code
                    or default_code
                ),
                "message": (
                    message
                    or default_message
                ),
                "request_id": str(
                    request_id
                ),
            },
        },
        status=status_code,
        json_dumps_params={
            "ensure_ascii": False,
        },
    )

    response.headers[
        "X-Request-ID"
    ] = str(
        request_id
    )

    response.headers[
        "Cache-Control"
    ] = "no-store"

    if source_response is not None:
        for header_name in (
            "Allow",
            "Retry-After",
        ):
            if (
                header_name
                in source_response.headers
            ):
                response.headers[
                    header_name
                ] = source_response.headers[
                    header_name
                ]

    return response


def ajax_error_response(
    request,
    *,
    status_code,
    code,
    message,
):
    """
    Error JSON para AJAX legacy del frontend.

    Mantiene `status` y `detail` por compatibilidad,
    pero añade el bloque `error` profesional y request_id.

    No debe usarse para webhooks de proveedores.
    """

    request_id = obtener_request_id(
        request
    )

    safe_message = str(
        message
        or "No pudimos completar la solicitud."
    )

    response = JsonResponse(
        {
            "status": "error",
            "detail": safe_message,
            "error": {
                "code": str(
                    code
                    or "request_error"
                ),
                "message": safe_message,
                "request_id": str(
                    request_id
                ),
            },
        },
        status=status_code,
        json_dumps_params={
            "ensure_ascii": False,
        },
    )

    response.headers[
        "X-Request-ID"
    ] = str(
        request_id
    )

    response.headers[
        "Cache-Control"
    ] = "no-store"

    return response


def professional_api_endpoint(
    view_func,
):
    @wraps(
        view_func
    )
    def wrapper(
        request,
        *args,
        **kwargs,
    ):
        try:
            response = view_func(
                request,
                *args,
                **kwargs,
            )

        except Http404:
            return api_error_response(
                request,
                status_code=404,
            )

        except PermissionDenied:
            return api_error_response(
                request,
                status_code=403,
            )

        except SuspiciousOperation as exc:
            registrar_error_runtime(
                request=request,
                exception=exc,
            )

            return api_error_response(
                request,
                status_code=400,
            )

        except Exception as exc:
            registrar_error_runtime(
                request=request,
                exception=exc,
            )

            return api_error_response(
                request,
                status_code=500,
            )

        status_code = int(
            getattr(
                response,
                "status_code",
                500,
            )
        )

        user = getattr(
            request,
            "user",
            None,
        )

        if (
            300 <= status_code < 400
            and (
                user is None
                or isinstance(
                    user,
                    AnonymousUser,
                )
                or not getattr(
                    user,
                    "is_authenticated",
                    False,
                )
            )
        ):
            return api_error_response(
                request,
                status_code=401,
                source_response=response,
            )

        if status_code >= 400:
            return api_error_response(
                request,
                status_code=status_code,
                source_response=response,
            )

        return response

    return wrapper
