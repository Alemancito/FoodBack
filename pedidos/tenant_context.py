from dataclasses import dataclass
from typing import Optional

from django.conf import settings

from .models import Membership, Sucursal, Tenant


@dataclass(frozen=True)
class TenantContext:
    tenant: Tenant
    sucursal: Optional[Sucursal]


def _resolver_tenant_usuario(request):
    """
    Resuelve el Tenant de un usuario autenticado.

    Seguridad:
    - nunca confía directamente en un tenant_id enviado por navegador;
    - cualquier Tenant guardado en sesión debe estar respaldado por
      un Membership activo.
    """

    user = request.user

    if not user.is_authenticated:
        return None

    memberships = (
        Membership.objects
        .select_related("tenant")
        .filter(
            usuario=user,
            activo=True,
            tenant__habilitado=True,
        )
    )

    # Futuro: el OWNER podría administrar varios Tenant.
    tenant_public_id = request.session.get(
        "tenant_activo_public_id"
    )

    if tenant_public_id:
        membership = memberships.filter(
            tenant__public_id=tenant_public_id
        ).first()

        if membership:
            return membership.tenant

        # La sesión tenía un Tenant al que el usuario ya no tiene acceso.
        request.session.pop(
            "tenant_activo_public_id",
            None,
        )
        request.session.modified = True

    # Si solo pertenece a un Tenant, no necesitamos selector.
    membership = memberships.first()

    if membership:
        return membership.tenant

    return None


def _resolver_tenant_host(request):
    """
    Resuelve Tenant por subdominio.

    Ejemplo futuro:
        rancheritos.foodbacksv.com
            -> slug = rancheritos
    """

    host = request.get_host().split(":")[0].lower()

    base_domain = getattr(
        settings,
        "FOODBACK_BASE_DOMAIN",
        "",
    ).strip().lower()

    if base_domain and host.endswith(
        f".{base_domain}"
    ):
        subdomain = host[
            : -(len(base_domain) + 1)
        ]

        # De momento solo aceptamos un nivel:
        # rancheritos.foodbacksv.com
        if (
            subdomain
            and "." not in subdomain
            and subdomain != "www"
        ):
            return (
                Tenant.objects
                .filter(
                    slug=subdomain,
                    habilitado=True,
                )
                .first()
            )

    return None


def _resolver_tenant_desarrollo():
    """
    Fallback explícito para localhost/ngrok durante desarrollo.

    NO usamos Tenant.objects.first().
    """

    slug = getattr(
        settings,
        "FOODBACK_DEFAULT_TENANT_SLUG",
        "",
    ).strip()

    if not slug:
        return None

    return (
        Tenant.objects
        .filter(
            slug=slug,
            habilitado=True,
        )
        .first()
    )


def resolver_tenant(request):
    """
    Orden de resolución:

    1. Usuario autenticado + Membership.
    2. Subdominio.
    3. Tenant explícito de desarrollo.
    """

    tenant = _resolver_tenant_usuario(
        request
    )

    if tenant:
        return tenant

    tenant = _resolver_tenant_host(
        request
    )

    if tenant:
        return tenant

    return _resolver_tenant_desarrollo()


def resolver_sucursal(
    request,
    tenant,
):
    """
    Obtiene una sucursal válida del Tenant.

    Si existe una selección guardada en sesión,
    se valida SIEMPRE contra el Tenant actual.
    """

    if not tenant:
        return None

    sucursal_public_id = request.session.get(
        "sucursal_activa_public_id"
    )

    sucursales = Sucursal.objects.filter(
        tenant=tenant,
        estado=Sucursal.Estado.ACTIVA,
    )

    if sucursal_public_id:
        sucursal = sucursales.filter(
            public_id=sucursal_public_id
        ).first()

        if sucursal:
            return sucursal

        request.session.pop(
            "sucursal_activa_public_id",
            None,
        )
        request.session.modified = True

    # Mientras solo exista una sucursal activa,
    # esta será la sucursal natural.
    return sucursales.order_by(
        "id"
    ).first()


def obtener_tenant_context(request):
    tenant = resolver_tenant(
        request
    )

    if not tenant:
        return None

    sucursal = resolver_sucursal(
        request,
        tenant,
    )

    return TenantContext(
        tenant=tenant,
        sucursal=sucursal,
    )