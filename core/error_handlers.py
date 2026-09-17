import logging

from django.conf import settings
from django.http import (
    Http404,
    HttpResponse,
)
from django.shortcuts import render
from django.urls import reverse

from pedidos.api_errors import (
    api_error_response,
    is_api_request,
)
from pedidos.audit import obtener_request_id
from pedidos.support import (
    crear_token_reporte_error,
)


logger = logging.getLogger(
    "foodback.error_handlers"
)


_ERROR_COPY = {
    400: {
        "eyebrow": "Solicitud no válida",
        "title": "No pudimos completar esa solicitud",
        "message": (
            "La solicitud recibida no es válida o no pudo verificarse. "
            "Actualiza la página e inténtalo de nuevo."
        ),
        "rail": "Solicitud detenida antes de entrar al flujo.",
    },
    403: {
        "eyebrow": "Acceso restringido",
        "title": "Este acceso no está disponible para tu sesión",
        "message": (
            "Tu sesión no tiene acceso a este recurso o la solicitud "
            "no superó una validación de seguridad."
        ),
        "rail": "La operación fue detenida por una regla de acceso.",
    },
    404: {
        "eyebrow": "Ruta fuera del menú",
        "title": "Esta página no está en el menú",
        "message": (
            "La dirección puede haber cambiado, el recurso puede no "
            "existir o no estar disponible para este contexto."
        ),
        "rail": "La ruta solicitada no forma parte del flujo disponible.",
    },
    429: {
        "eyebrow": "Demasiadas solicitudes",
        "title": "Necesitamos esperar antes de continuar",
        "message": (
            "Se realizaron varias solicitudes en poco tiempo. "
            "Espera un momento e inténtalo nuevamente."
        ),
        "rail": "El flujo fue pausado temporalmente para proteger el servicio.",
    },
    500: {
        "eyebrow": "Incidente interno",
        "title": "La operación se detuvo antes de completarse",
        "message": (
            "El problema fue registrado para poder investigarlo. "
            "Puedes volver al inicio e intentarlo nuevamente más tarde."
        ),
        "rail": "FoodBack conservó una referencia técnica para investigación.",
    },
}


def _fallback_response(
    *,
    status_code,
    request_id,
):
    body = (
        "No pudimos completar la solicitud.\n"
        f"Código de referencia: {request_id}\n"
    )

    response = HttpResponse(
        body,
        status=status_code,
        content_type=(
            "text/plain; charset=utf-8"
        ),
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


def _support_context(
    *,
    request,
    request_id,
    status_code,
    allow_report,
):
    if (
        not allow_report
        or status_code not in {
            400,
            403,
            404,
            500,
        }
    ):
        return {
            "support_report_enabled": False,
            "support_report_token": "",
            "support_report_url": "",
        }

    try:
        report_url = reverse(
            "support_report_error"
        )

        # Evita un bucle UX: un error del propio endpoint de soporte
        # nunca ofrece volver a reportar el reporte.
        if (
            str(
                getattr(
                    request,
                    "path",
                    "",
                )
                or ""
            )
            == report_url
        ):
            return {
                "support_report_enabled": False,
                "support_report_token": "",
                "support_report_url": "",
            }

        token = crear_token_reporte_error(
            request_id=request_id,
            status_code=status_code,
        )

        if not token:
            return {
                "support_report_enabled": False,
                "support_report_token": "",
                "support_report_url": "",
            }

        return {
            "support_report_enabled": True,
            "support_report_token": token,
            "support_report_url": report_url,
        }

    except Exception as exc:
        # La página de error debe seguir funcionando aunque el componente
        # opcional de soporte falle. Nunca registramos mensaje/traceback.
        logger.error(
            (
                "No se pudo preparar reporte de soporte. "
                "tipo=%s request_id=%s"
            ),
            exc.__class__.__name__,
            request_id,
        )

        return {
            "support_report_enabled": False,
            "support_report_token": "",
            "support_report_url": "",
        }


def render_error_response(
    request,
    *,
    status_code,
    title=None,
    message=None,
    allow_report=True,
):
    """
    Render seguro y resiliente para errores HTTP.

    Las rutas /api/ nunca reciben HTML: usan el contrato JSON profesional.

    Las rutas web nunca reciben exception.message, traceback, SQL, rutas
    internas, credenciales ni payloads. Si el template falla, cae a una
    respuesta mínima de texto para evitar un segundo error durante un 500.
    """

    if is_api_request(
        request
    ):
        return api_error_response(
            request,
            status_code=status_code,
        )

    request_id = obtener_request_id(
        request
    )

    defaults = _ERROR_COPY.get(
        status_code,
        _ERROR_COPY[500],
    )

    context = {
        "status_code": status_code,
        "eyebrow": defaults["eyebrow"],
        "error_title": (
            title
            or defaults["title"]
        ),
        "error_message": (
            message
            or defaults["message"]
        ),
        "rail_message": defaults[
            "rail"
        ],
        "request_id": str(
            request_id
        ),
    }

    context.update(
        _support_context(
            request=request,
            request_id=request_id,
            status_code=status_code,
            allow_report=allow_report,
        )
    )

    try:
        response = render(
            request,
            "errors/error.html",
            context,
            status=status_code,
        )

    except Exception as exc:
        # Este logger NO usa logger.exception() ni exc_info=True.
        # Una excepción de template puede incluir datos sensibles en
        # su mensaje/traceback. Conservamos únicamente tipo, status e ID.
        logger.error(
            (
                "Falló el template de error HTTP %s; "
                "usando fallback seguro. "
                "tipo=%s request_id=%s"
            ),
            status_code,
            exc.__class__.__name__,
            request_id,
        )

        return _fallback_response(
            status_code=status_code,
            request_id=request_id,
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


def error_400(
    request,
    exception,
):
    return render_error_response(
        request,
        status_code=400,
    )


def error_403(
    request,
    exception,
):
    return render_error_response(
        request,
        status_code=403,
    )


def error_404(
    request,
    exception,
):
    return render_error_response(
        request,
        status_code=404,
    )


def error_500(
    request,
):
    return render_error_response(
        request,
        status_code=500,
    )


def preview_error(
    request,
    status_code,
):
    """
    Preview visual exclusivamente de desarrollo.

    Hay dos capas:
    1. la URL solo se registra cuando DEBUG=True;
    2. la vista vuelve a comprobar DEBUG por defensa en profundidad.
    """

    if not settings.DEBUG:
        raise Http404

    if status_code not in {
        400,
        403,
        404,
        500,
    }:
        status_code = 404

    return render_error_response(
        request,
        status_code=status_code,
        allow_report=False,
    )
