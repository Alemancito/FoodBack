from .db_tenant_context import (
    tenant_database_context,
)
from .tenant_context import (
    resolver_sucursal,
    resolver_tenant,
)


class TenantContextMiddleware:
    """
    Añade contexto multi-tenant a cada request.

    request.tenant
    request.sucursal

    Orden de seguridad:
    1. Resolver Tenant usando únicamente las tablas bootstrap necesarias.
    2. Establecer inmediatamente foodback.tenant_id en PostgreSQL.
    3. Resolver Sucursal y asignaciones con el contexto Tenant activo.
    4. Establecer foodback.sucursal_id y ejecutar la vista.
    """

    def __init__(
        self,
        get_response,
    ):
        self.get_response = get_response

    def __call__(
        self,
        request,
    ):
        request.tenant = None
        request.sucursal = None

        tenant = resolver_tenant(
            request
        )
        request.tenant = tenant

        if not tenant:
            with tenant_database_context():
                return self.get_response(
                    request
                )

        with tenant_database_context(
            tenant=tenant,
        ):
            sucursal = resolver_sucursal(
                request,
                tenant,
            )
            request.sucursal = sucursal

            with tenant_database_context(
                tenant=tenant,
                sucursal=sucursal,
            ):
                response = self.get_response(
                    request
                )

        return response


class AuditExceptionMiddleware:
    """
    Proporciona un request ID seguro a toda request y audita excepciones
    no controladas sin sustituir el manejo normal de Django.

    El mismo UUID aparece:
    - en AuditEvent.request_id;
    - en la respuesta HTTP X-Request-ID;
    - en las páginas profesionales de error.

    Así soporte puede correlacionar lo que vio el usuario con el evento
    interno sin mostrar tracebacks, SQL, secretos ni mensajes crudos.
    """

    def __init__(
        self,
        get_response,
    ):
        self.get_response = get_response

    def __call__(
        self,
        request,
    ):
        from .audit import obtener_request_id

        request_id = obtener_request_id(
            request
        )

        response = self.get_response(
            request
        )

        if request_id is not None:
            response.headers[
                "X-Request-ID"
            ] = str(
                request_id
            )

        return response

    def process_exception(
        self,
        request,
        exception,
    ):
        from .audit import registrar_error_runtime

        registrar_error_runtime(
            request=request,
            exception=exception,
        )

        return None
