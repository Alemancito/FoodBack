from .tenant_context import obtener_tenant_context


class TenantContextMiddleware:
    """
    Añade contexto multi-tenant a cada request.

    request.tenant
    request.sucursal

    Si no puede resolver un Tenant válido, ambos quedan en None.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.tenant = None
        request.sucursal = None

        context = obtener_tenant_context(
            request
        )

        if context:
            request.tenant = context.tenant
            request.sucursal = context.sucursal

        response = self.get_response(
            request
        )

        return response