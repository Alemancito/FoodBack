from contextlib import contextmanager

from django.db import connection, transaction


TENANT_SETTING = "foodback.tenant_id"
SUCURSAL_SETTING = "foodback.sucursal_id"


def _leer_contexto_actual():
    """
    Lee el contexto PostgreSQL que existía antes de entrar.

    Esto permite restaurarlo correctamente incluso cuando
    estamos dentro de otra transacción, como ocurre con
    Django TestCase.
    """

    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT
                current_setting(%s, true),
                current_setting(%s, true)
            """,
            [
                TENANT_SETTING,
                SUCURSAL_SETTING,
            ],
        )

        tenant_id, sucursal_id = cursor.fetchone()

    return (
        tenant_id or "",
        sucursal_id or "",
    )


def _establecer_contexto_local(
    tenant_id,
    sucursal_id,
):
    """
    SET LOCAL mediante set_config(..., true).

    El tercer argumento True hace que el valor sea local
    a la transacción PostgreSQL actual.
    """

    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT
                set_config(%s, %s, true),
                set_config(%s, %s, true)
            """,
            [
                TENANT_SETTING,
                tenant_id,
                SUCURSAL_SETTING,
                sucursal_id,
            ],
        )


@contextmanager
def tenant_database_context(
    tenant=None,
    sucursal=None,
):
    """
    Expone tenant/sucursal a PostgreSQL únicamente mientras
    se procesa el request actual.

    Si no hay tenant:
        foodback.tenant_id = ''

    Las futuras políticas RLS tratarán '' como ausencia de
    contexto y por tanto bloquearán acceso tenantizado.
    """

    # Protección defensiva por si en algún entorno se usa
    # otro motor temporalmente.
    if connection.vendor != "postgresql":
        yield
        return

    tenant_id = (
        str(tenant.pk)
        if tenant is not None
        else ""
    )

    sucursal_id = (
        str(sucursal.pk)
        if sucursal is not None
        else ""
    )

    tenant_anterior, sucursal_anterior = (
        _leer_contexto_actual()
    )

    with transaction.atomic():
        _establecer_contexto_local(
            tenant_id,
            sucursal_id,
        )

        try:
            yield

        finally:
            # Si la transacción quedó marcada para rollback por
            # un error de BD, PostgreSQL restaurará el SET LOCAL
            # al hacer rollback del savepoint/transacción.
            if not connection.needs_rollback:
                _establecer_contexto_local(
                    tenant_anterior,
                    sucursal_anterior,
                )