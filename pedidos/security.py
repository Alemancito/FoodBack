import ipaddress

from django.conf import settings

import hashlib

from django.core.mail import send_mail

from datetime import timedelta

from django.db import (
    IntegrityError,
    transaction,
)

from django.utils import timezone

from .models import (
    PasswordResetChallenge,
    RateLimitBucket,
    StaffIdentity,
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
                        settings
                        .FOODBACK_PASSWORD_RESET_CODE_TTL_SECONDS
                    )
                )
            ),
        )
    )

    return challenge, codigo



def solicitar_password_reset(
    *,
    request,
    email,
):
    """
    Solicita recuperación de contraseña sin revelar
    si el correo pertenece o no a una cuenta.

    Protecciones:
    - rate limit por IP;
    - rate limit por correo;
    - únicamente usuarios activos;
    - únicamente correos verificados;
    - invalida challenges anteriores;
    - nunca retorna el código;
    - nunca revela existencia de cuenta.
    """

    email_normalizado = (
        str(
            email or ""
        )
        .strip()
        .lower()
    )[:254]

    ip = obtener_ip_cliente(
        request
    )

    limite_ip = consumir_rate_limit(
        group="password-reset-request-ip",
        raw_key=ip,
        limite=(
            settings
            .FOODBACK_PASSWORD_RESET_REQUEST_IP_LIMIT
        ),
        ventana_segundos=(
            settings
            .FOODBACK_PASSWORD_RESET_REQUEST_WINDOW_SECONDS
        ),
        bloqueo_segundos=(
            settings
            .FOODBACK_PASSWORD_RESET_REQUEST_BLOCK_SECONDS
        ),
    )

    limite_email = consumir_rate_limit(
        group="password-reset-request-email",
        raw_key=(
            email_normalizado
            or "<empty>"
        ),
        limite=(
            settings
            .FOODBACK_PASSWORD_RESET_REQUEST_EMAIL_LIMIT
        ),
        ventana_segundos=(
            settings
            .FOODBACK_PASSWORD_RESET_REQUEST_WINDOW_SECONDS
        ),
        bloqueo_segundos=(
            settings
            .FOODBACK_PASSWORD_RESET_REQUEST_BLOCK_SECONDS
        ),
    )

    if (
        not limite_ip["permitido"]
        or
        not limite_email["permitido"]
    ):
        return {
            "permitido": False,
            "retry_after": max(
                limite_ip["retry_after"],
                limite_email["retry_after"],
            ),
        }

    identity = (
        StaffIdentity.objects
        .select_related(
            "user"
        )
        .filter(
            email=email_normalizado,
            email_verified=True,
            user__is_active=True,
        )
        .first()
    )

    # MUY IMPORTANTE:
    # que no exista una cuenta produce exactamente
    # la misma respuesta pública.
    if identity is None:
        return {
            "permitido": True,
            "retry_after": 0,
            "flow_id": uuid.uuid4(),
        }

    ahora = timezone.now()

    with transaction.atomic():
        identity = (
            StaffIdentity.objects
            .select_for_update()
            .select_related(
                "user"
            )
            .get(
                pk=identity.pk
            )
        )

        # Invalida códigos anteriores todavía utilizables.
        PasswordResetChallenge.objects.filter(
            identity=identity,
            usado_en__isnull=True,
        ).update(
            usado_en=ahora
        )

        challenge, codigo = (
            crear_password_reset_challenge(
                identity
            )
        )

    # No propagamos al usuario diferencias entre
    # "correo inexistente" y "SMTP falló".
    #
    # Phase 7 registrará estos fallos internamente.
    send_mail(
        subject=(
            "Código de recuperación de FoodBack"
        ),
        message=(
            "Tu código para recuperar tu "
            "contraseña de FoodBack es:\n\n"
            f"{codigo}\n\n"
            "Este código vence en 10 minutos.\n"
            "Si no solicitaste este cambio, "
            "ignora este mensaje."
        ),
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[
            identity.email,
        ],
        fail_silently=True,
    )

    return {
        "permitido": True,
        "retry_after": 0,
        "flow_id": challenge.public_id,
    }



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
    
    
def consumir_password_reset_challenge(
    *,
    public_id,
    codigo,
):
    """
    Verifica y consume un desafío de recuperación
    dentro de una transacción.

    Garantías:
    - un código vencido nunca valida;
    - un código usado nunca valida otra vez;
    - los intentos incorrectos se contabilizan;
    - al alcanzar el máximo queda bloqueado;
    - dos requests concurrentes no pueden consumir
      exitosamente el mismo desafío.
    """

    try:
        challenge_public_id = uuid.UUID(
            str(public_id)
        )

    except (
        TypeError,
        ValueError,
        AttributeError,
    ):
        return {
            "valido": False,
            "estado": "NO_ENCONTRADO",
            "challenge": None,
        }

    ahora = timezone.now()

    with transaction.atomic():
        challenge = (
            PasswordResetChallenge.objects
            .select_for_update()
            .select_related(
                "identity",
                "identity__user",
            )
            .filter(
                public_id=challenge_public_id
            )
            .first()
        )

        if challenge is None:
            return {
                "valido": False,
                "estado": "NO_ENCONTRADO",
                "challenge": None,
            }

        if challenge.usado_en is not None:
            return {
                "valido": False,
                "estado": "USADO",
                "challenge": challenge,
            }

        if challenge.expira_en <= ahora:
            return {
                "valido": False,
                "estado": "EXPIRADO",
                "challenge": challenge,
            }

        max_intentos = (
            settings
            .FOODBACK_PASSWORD_RESET_MAX_ATTEMPTS
        )

        if challenge.intentos >= max_intentos:
            return {
                "valido": False,
                "estado": "BLOQUEADO",
                "challenge": challenge,
            }

        codigo_valido = (
            codigo
            and
            check_password(
                str(codigo),
                challenge.codigo_hash,
            )
        )

        if not codigo_valido:
            challenge.intentos += 1

            challenge.save(
                update_fields=[
                    "intentos",
                ]
            )

            estado = (
                "BLOQUEADO"
                if challenge.intentos >= max_intentos
                else "INVALIDO"
            )

            return {
                "valido": False,
                "estado": estado,
                "challenge": challenge,
            }

        challenge.usado_en = ahora

        challenge.save(
            update_fields=[
                "usado_en",
            ]
        )

        return {
            "valido": True,
            "estado": "VALIDO",
            "challenge": challenge,
        }


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