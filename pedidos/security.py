import ipaddress

from django.conf import settings

import hashlib

from datetime import timedelta

from django.db import (
    IntegrityError,
    transaction,
)

from django.utils import timezone

from .models import (
    PasswordResetChallenge,
    RateLimitBucket,
)

import uuid

import secrets

from django.contrib.auth.hashers import (
    check_password,
    make_password,
)


def _normalizar_ip(valor):
    """
    Valida y normaliza una dirección IPv4 o IPv6.

    Nunca devuelve contenido arbitrario recibido
    desde headers.
    """

    if not valor:
        return None

    valor = str(
        valor
    ).strip()

    try:
        return str(
            ipaddress.ip_address(
                valor
            )
        )

    except ValueError:
        return None


def obtener_ip_cliente(request):
    """
    Obtiene la IP que FoodBack utilizará para
    seguridad, rate limiting y auditoría.

    Local/desarrollo:
        REMOTE_ADDR

    Railway:
        X-Real-IP, pero únicamente si hemos
        habilitado explícitamente la confianza
        en ese header.

    Nunca utilizamos X-Forwarded-For directamente.
    """

    confiar_x_real_ip = getattr(
        settings,
        "FOODBACK_TRUST_X_REAL_IP",
        False,
    )

    if confiar_x_real_ip:

        railway_ip = _normalizar_ip(
            request.META.get(
                "HTTP_X_REAL_IP"
            )
        )

        if railway_ip:
            return railway_ip

    remote_addr = _normalizar_ip(
        request.META.get(
            "REMOTE_ADDR"
        )
    )

    if remote_addr:
        return remote_addr

    return "0.0.0.0"




PASSWORD_RESET_CODE_TTL_SECONDS = 600


def crear_password_reset_challenge(
    identity,
):
    """
    Genera un código numérico de 6 dígitos.

    El código en texto plano solo existe el tiempo
    necesario para enviarlo por correo.
    La BD recibe únicamente su hash.
    """

    codigo = (
        f"{secrets.randbelow(1_000_000):06d}"
    )

    challenge = (
        PasswordResetChallenge.objects.create(
            identity=identity,
            codigo_hash=make_password(
                codigo
            ),
            expira_en=(
                timezone.now()
                + timedelta(
                    seconds=(
                        PASSWORD_RESET_CODE_TTL_SECONDS
                    )
                )
            ),
        )
    )

    return challenge, codigo


def password_reset_codigo_coincide(
    challenge,
    codigo,
):
    """
    Solo comprueba criptográficamente el código.

    Todavía NO consume intentos ni marca el desafío
    como usado; eso lo añadiremos en el siguiente
    micro-sprint de validación transaccional.
    """

    if not codigo:
        return False

    return check_password(
        str(codigo),
        challenge.codigo_hash,
    )


def clave_ratelimit_ip(
    group,
    request,
):
    """
    Callable compatible con django-ratelimit.
    """

    return obtener_ip_cliente(
        request
    )


def clave_ratelimit_usuario_o_ip(
    group,
    request,
):
    """
    Para endpoints autenticados usamos el ID
    interno del usuario.

    Para anónimos usamos la IP validada.
    """

    user = getattr(
        request,
        "user",
        None,
    )

    if (
        user
        and user.is_authenticated
    ):
        return (
            f"user:{user.pk}"
        )

    return (
        f"ip:{obtener_ip_cliente(request)}"
    )


def clave_ratelimit_login(
    group,
    request,
):
    """
    Combina IP + username normalizado.

    Evita que cambiar únicamente el username
    permita saltarse el bucket por combinación.
    """

    ip = obtener_ip_cliente(
        request
    )

    username = (
        request.POST.get(
            "username",
            "",
        )
        .strip()
        .lower()
    )

    # No guardamos contraseñas ni ningún secreto
    # dentro de la clave.
    username = username[
        :150
    ]

    return (
        f"{ip}:{username}"
    )
    


def _hash_ratelimit_key(
    group,
    raw_key,
):
    contenido = (
        f"{group}|{raw_key}"
    ).encode(
        "utf-8"
    )

    return hashlib.sha256(
        contenido
    ).hexdigest()


def consumir_rate_limit(
    *,
    group,
    raw_key,
    limite,
    ventana_segundos,
    bloqueo_segundos=None,
):
    """
    Consume un intento dentro de un bucket
    persistido en BD.

    Retorna:
        {
            "permitido": bool,
            "restantes": int,
            "retry_after": int,
        }

    La clave sensible nunca se guarda cruda.
    Solo almacenamos SHA-256.
    """

    ahora = timezone.now()

    if bloqueo_segundos is None:
        bloqueo_segundos = (
            ventana_segundos
        )

    clave_hash = _hash_ratelimit_key(
        group,
        raw_key,
    )

    for intento in range(2):

        try:
            with transaction.atomic():

                bucket = (
                    RateLimitBucket.objects
                    .select_for_update()
                    .filter(
                        clave_hash=clave_hash
                    )
                    .first()
                )

                if bucket is None:
                    bucket = RateLimitBucket.objects.create(
                        grupo=group,
                        clave_hash=clave_hash,
                        ventana_inicio=ahora,
                        contador=1,
                    )

                    return {
                        "permitido": True,
                        "restantes":
                            max(
                                limite - 1,
                                0,
                            ),
                        "retry_after": 0,
                    }

                if (
                    bucket.bloqueado_hasta
                    and
                    bucket.bloqueado_hasta
                    > ahora
                ):
                    retry_after = int(
                        (
                            bucket.bloqueado_hasta
                            - ahora
                        ).total_seconds()
                    )

                    return {
                        "permitido": False,
                        "restantes": 0,
                        "retry_after":
                            max(
                                retry_after,
                                1,
                            ),
                    }

                fin_ventana = (
                    bucket.ventana_inicio
                    + timedelta(
                        seconds=ventana_segundos
                    )
                )

                if ahora >= fin_ventana:
                    bucket.ventana_inicio = (
                        ahora
                    )

                    bucket.contador = 1

                    bucket.bloqueado_hasta = (
                        None
                    )

                    bucket.grupo = (
                        group
                    )

                    bucket.save(
                        update_fields=[
                            "grupo",
                            "ventana_inicio",
                            "contador",
                            "bloqueado_hasta",
                            "actualizado_en",
                        ]
                    )

                    return {
                        "permitido": True,
                        "restantes":
                            max(
                                limite - 1,
                                0,
                            ),
                        "retry_after": 0,
                    }

                bucket.contador += 1

                if (
                    bucket.contador
                    > limite
                ):
                    bucket.bloqueado_hasta = (
                        ahora
                        + timedelta(
                            seconds=(
                                bloqueo_segundos
                            )
                        )
                    )

                    bucket.save(
                        update_fields=[
                            "contador",
                            "bloqueado_hasta",
                            "actualizado_en",
                        ]
                    )

                    return {
                        "permitido": False,
                        "restantes": 0,
                        "retry_after":
                            max(
                                bloqueo_segundos,
                                1,
                            ),
                    }

                bucket.save(
                    update_fields=[
                        "contador",
                        "actualizado_en",
                    ]
                )

                return {
                    "permitido": True,
                    "restantes":
                        max(
                            limite
                            - bucket.contador,
                            0,
                        ),
                    "retry_after": 0,
                }

        except IntegrityError:
            if intento == 0:
                continue

            raise
        

def obtener_id_seguridad_cliente(
    request,
):
    """
    Identificador aleatorio persistente por sesión/navegador.

    No contiene:
    - teléfono,
    - email,
    - username,
    - IP,
    - datos personales.

    Sirve únicamente para controles defensivos.
    """

    clave_sesion = (
        "foodback_security_client_id"
    )

    identificador = (
        request.session.get(
            clave_sesion
        )
    )

    if identificador:
        try:
            return str(
                uuid.UUID(
                    identificador
                )
            )

        except (
            ValueError,
            TypeError,
            AttributeError,
        ):
            pass

    identificador = str(
        uuid.uuid4()
    )

    request.session[
        clave_sesion
    ] = identificador

    request.session.modified = True

    return identificador