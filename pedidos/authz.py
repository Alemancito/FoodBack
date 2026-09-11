from functools import wraps

from django.core.exceptions import PermissionDenied

from .models import Membership


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