from functools import wraps

from django.core.exceptions import PermissionDenied

from .models import (
    Membership,
    MembershipSucursal,
    RepartidorSucursal,
)


def obtener_membership_activo(
    request,
    roles=None,
):
    """
    Obtiene el Membership activo del usuario
    dentro del Tenant resuelto para este request.

    Nunca autoriza usando is_staff,
    is_superuser ni Django Groups.
    """

    user = getattr(
        request,
        "user",
        None,
    )

    tenant = getattr(
        request,
        "tenant",
        None,
    )

    if (
        not user
        or not user.is_authenticated
        or not tenant
    ):
        return None

    queryset = (
        Membership.objects
        .filter(
            usuario=user,
            tenant=tenant,
            activo=True,
        )
        .select_related(
            "tenant"
        )
    )

    if roles:
        queryset = queryset.filter(
            rol__in=roles
        )

    return queryset.first()


def require_tenant_roles(
    *roles,
):
    """
    Autoriza una vista únicamente si el usuario
    posee un Membership activo en el Tenant
    resuelto y uno de los roles permitidos.

    Debe usarse junto a @login_required.
    """

    def decorator(view_func):

        @wraps(view_func)
        def wrapper(
            request,
            *args,
            **kwargs,
        ):
            membership = (
                obtener_membership_activo(
                    request,
                    roles=roles,
                )
            )

            if not membership:
                raise PermissionDenied(
                    (
                        "No tienes permisos "
                        "para acceder a este "
                        "restaurante."
                    )
                )

            # Lo dejamos disponible para
            # futuras decisiones de permisos.
            request.membership = (
                membership
            )

            return view_func(
                request,
                *args,
                **kwargs,
            )

        return wrapper

    return decorator


def obtener_asignacion_repartidor_activa(
    request,
):
    """
    Comprueba que el usuario esté autorizado
    específicamente para la sucursal activa.
    """

    user = getattr(
        request,
        "user",
        None,
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

    if (
        not user
        or not user.is_authenticated
        or not tenant
        or not sucursal
    ):
        return None

    return (
        RepartidorSucursal.objects
        .filter(
            usuario=user,
            sucursal=sucursal,
            sucursal__tenant=tenant,
            activo=True,
        )
        .select_related(
            "sucursal",
            "sucursal__tenant",
        )
        .first()
    )


def require_delivery_assignment(
    view_func,
):
    @wraps(view_func)
    def wrapper(
        request,
        *args,
        **kwargs,
    ):
        asignacion = (
            obtener_asignacion_repartidor_activa(
                request
            )
        )

        if not asignacion:
            raise PermissionDenied(
                (
                    "No tienes autorización "
                    "para repartir en esta sucursal."
                )
            )

        request.delivery_assignment = (
            asignacion
        )

        return view_func(
            request,
            *args,
            **kwargs,
        )

    return wrapper


def obtener_acceso_admin_sucursal(
    request,
):
    """
    Resuelve autorización administrativa
    sobre la sucursal activa.

    OWNER:
        todas las sucursales de su Tenant.

    MANAGER:
        únicamente las sucursales asignadas
        mediante MembershipSucursal.
    """

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

    if not tenant or not sucursal:
        return None

    # Defensa adicional: jamás aceptamos una
    # sucursal perteneciente a otro Tenant.
    if sucursal.tenant_id != tenant.id:
        return None

    membership = obtener_membership_activo(
        request,
        roles=[
            Membership.ROLE_OWNER,
            Membership.ROLE_MANAGER,
        ],
    )

    if not membership:
        return None

    # OWNER controla todas las sucursales
    # de su propio Tenant.
    if (
        membership.rol
        == Membership.ROLE_OWNER
    ):
        return membership

    # MANAGER necesita asignación explícita.
    autorizado = (
        MembershipSucursal.objects
        .filter(
            membership=membership,
            sucursal=sucursal,
            activo=True,
        )
        .exists()
    )

    if not autorizado:
        return None

    return membership


def require_branch_access(
    view_func,
):
    """
    Requiere autoridad administrativa sobre
    la sucursal activa.
    """

    @wraps(view_func)
    def wrapper(
        request,
        *args,
        **kwargs,
    ):
        membership = (
            obtener_acceso_admin_sucursal(
                request
            )
        )

        if not membership:
            raise PermissionDenied(
                (
                    "No tienes permisos "
                    "para administrar esta "
                    "sucursal."
                )
            )

        request.membership = membership

        return view_func(
            request,
            *args,
            **kwargs,
        )

    return wrapper