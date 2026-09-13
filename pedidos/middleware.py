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

    1. Resolver Tenant usando únicamente las tablas
       bootstrap necesarias.

    2. Establecer inmediatamente foodback.tenant_id
       en PostgreSQL.

    3. Resolver Sucursal y asignaciones mientras el
       contexto Tenant ya está activo.

    4. Establecer también foodback.sucursal_id y
       ejecutar la vista.
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

        # =============================================
        # BOOTSTRAP MINIMO
        # =============================================
        #
        # Todavía no existe contexto RLS.
        #
        # Únicamente se permite resolver el Tenant.
        # En esta etapa intervienen Tenant/Membership.
        # =============================================

        tenant = resolver_tenant(
            request
        )

        request.tenant = tenant

        # =============================================
        # SIN TENANT
        # =============================================
        #
        # Seguimos fail-closed:
        # foodback.tenant_id = ""
        # foodback.sucursal_id = ""
        # =============================================

        if not tenant:
            with tenant_database_context():
                return self.get_response(
                    request
                )

        # =============================================
        # CONTEXTO TENANT
        # =============================================
        #
        # Desde este punto PostgreSQL ya conoce:
        #
        # foodback.tenant_id
        #
        # Por tanto resolver_sucursal() puede consultar
        # tablas protegidas mediante RLS.
        # =============================================

        with tenant_database_context(
            tenant=tenant,
        ):
            sucursal = resolver_sucursal(
                request,
                tenant,
            )

            request.sucursal = sucursal

            # =========================================
            # CONTEXTO TENANT + SUCURSAL
            # =========================================

            with tenant_database_context(
                tenant=tenant,
                sucursal=sucursal,
            ):
                response = self.get_response(
                    request
                )

        return response