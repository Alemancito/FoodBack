from dataclasses import dataclass
from typing import Optional

from django.conf import settings

from .models import (
    Membership,
    MembershipSucursal,
    RepartidorSucursal,
    Sucursal,
    Tenant,
)


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
    Fallback explícito únicamente para desarrollo local.

    En producción jamás se selecciona automáticamente
    un Tenant por configuración.
    """

    if getattr(
        settings,
        "IS_PRODUCTION",
        False,
    ):
        return None

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


def _sucursales_accesibles_usuario(
    request,
    tenant,
):
    """
    Devuelve únicamente las sucursales que pueden
    utilizarse como contexto para el usuario actual.

    Los visitantes anónimos conservan acceso a las
    sucursales públicas activas.

    Para usuarios internos autenticados:
    - OWNER: todas las sucursales del Tenant.
    - MANAGER: solo sus asignaciones activas.
    - DELIVERY: solo sus asignaciones activas.
    - Sin asignación: ninguna sucursal.
    """

    sucursales = (
        Sucursal.objects
        .filter(
            tenant=tenant,
            estado=Sucursal.Estado.ACTIVA,
        )
    )

    user = getattr(
        request,
        "user",
        None,
    )

    # Cliente/visitante público.
    if (
        not user
        or not user.is_authenticated
    ):
        return sucursales

    membership = (
        Membership.objects
        .filter(
            usuario=user,
            tenant=tenant,
            activo=True,
        )
        .first()
    )

    if membership:

        if (
            membership.rol
            == Membership.ROLE_OWNER
        ):
            return sucursales

        if (
            membership.rol
            == Membership.ROLE_MANAGER
        ):
            return (
                sucursales
                .filter(
                    memberships_autorizados__membership=membership,
                    memberships_autorizados__activo=True,
                )
                .distinct()
            )

    # Si no tiene Membership administrativo,
    # comprobamos Delivery.
    return (
        sucursales
        .filter(
            repartidores_asignados__usuario=user,
            repartidores_asignados__activo=True,
        )
        .distinct()
    )


def resolver_sucursal(
    request,
    tenant,
):
    """
    Resuelve una sucursal activa y permitida
    para el contexto actual.

    Una selección guardada en sesión nunca se
    acepta sin volver a validar autorización.
    """

    if not tenant:
        return None

    sucursal_public_id = (
        request.session.get(
            "sucursal_activa_public_id"
        )
    )

    sucursales = (
        _sucursales_accesibles_usuario(
            request,
            tenant,
        )
    )

    if sucursal_public_id:

        sucursal = (
            sucursales
            .filter(
                public_id=sucursal_public_id
            )
            .first()
        )

        if sucursal:
            return sucursal

        # La sucursal guardada ya no existe,
        # está archivada o el usuario perdió acceso.
        request.session.pop(
            "sucursal_activa_public_id",
            None,
        )

        request.session.modified = True

    # Seleccionamos únicamente entre las sucursales
    # que este contexto tiene permitido utilizar.
    return (
        sucursales
        .order_by("id")
        .first()
    )


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