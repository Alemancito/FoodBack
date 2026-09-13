from .db_tenant_context import (
    tenant_database_context,
)
from .tenant_context import (
    obtener_tenant_context,
)


class TenantContextMiddleware:
    """
    Añade contexto multi-tenant a cada request.

    request.tenant
    request.sucursal

    El Tenant/Sucursal se resuelven primero mediante las
    reglas de autorización existentes.

    Después se exponen a PostgreSQL únicamente durante
    el procesamiento del request para que RLS pueda
    utilizarlos.
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

        # -------------------------------------------------
        # BOOTSTRAP
        # -------------------------------------------------
        # Estas consultas ocurren antes del contexto RLS.
        #
        # Por eso Tenant, Membership, Sucursal y las tablas
        # de asignaciones todavía NO recibirán RLS.
        # -------------------------------------------------

        context = obtener_tenant_context(
            request
        )

        if context:
            request.tenant = (
                context.tenant
            )

            request.sucursal = (
                context.sucursal
            )

        # -------------------------------------------------
        # CONTEXTO POSTGRESQL
        # -------------------------------------------------
        #
        # Todo lo que ocurra desde aquí dentro puede usar:
        #
        # current_setting(
        #     'foodback.tenant_id',
        #     true,
        # )
        #
        # y
        #
        # current_setting(
        #     'foodback.sucursal_id',
        #     true,
        # )
        # -------------------------------------------------

        with tenant_database_context(
            tenant=request.tenant,
            sucursal=request.sucursal,
        ):
            response = self.get_response(
                request
            )

        return response