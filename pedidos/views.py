import hashlib
import hmac
import json
import uuid
import requests
from datetime import datetime, date, timedelta
from decimal import Decimal, InvalidOperation
from django.shortcuts import render, redirect, get_object_or_404
from .models import (
    Categoria,
    Producto,
    Pedido,
    DetallePedido,
    Cliente,
    ConfiguracionNegocio,
    DiaEspecial,
    OpcionProducto,
    Extra,
    PagoWompi,
    EventoPagoWompi,
    EstadoPasarelaPago,
    SuscripcionTenant,
    Membership,
)
from django.db import IntegrityError, transaction
from django.contrib import messages
from decouple import config
from django.contrib.auth.decorators import login_required
from .security import (
    consumir_rate_limit,
    obtener_id_seguridad_cliente,
    obtener_ip_cliente,
)
from django.utils import timezone
from django.conf import settings
from .authz import (
    obtener_asignacion_repartidor_activa,
    obtener_membership_activo,
    require_branch_access,
    require_delivery_assignment,
    require_tenant_roles,
)
from .tenant_context import (
    _sucursales_accesibles_usuario,
)
from django.urls import reverse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import (
    require_GET,
    require_POST,
    require_safe,
    require_http_methods,
)
from django.views.decorators.csrf import csrf_exempt  # IMPORTANTE PARA EL WEBHOOK
from django.contrib.auth.views import LoginView
from django.contrib.auth import logout
from django.http import JsonResponse, HttpResponseBadRequest, HttpResponseForbidden
from django.template.loader import render_to_string
from django.db.models import Sum, Count, F, Q, Max, Prefetch
from django.core.exceptions import PermissionDenied
from django.utils.dateparse import parse_datetime
# --- LÓGICA DE LOGIN Y SEGURIDAD ---


class CustomLoginView(LoginView):
    template_name = "registration/login.html"
    redirect_authenticated_user = True

    RATE_LIMIT_SESSION_UNTIL = (
        "foodback_login_rate_limit_until"
    )

    RATE_LIMIT_SESSION_USERNAME = (
        "foodback_login_rate_limit_username"
    )

    RATE_LIMIT_MESSAGE = (
        "Se realizaron demasiados intentos "
        "de inicio de sesión. "
        "Espera unos minutos e inténtalo nuevamente."
    )

    def _limpiar_estado_visual_rate_limit(
        self,
    ):
        self.request.session.pop(
            self.RATE_LIMIT_SESSION_UNTIL,
            None,
        )

        self.request.session.pop(
            self.RATE_LIMIT_SESSION_USERNAME,
            None,
        )

        self.request.session.modified = True

    def _guardar_estado_visual_rate_limit(
        self,
        request,
        retry_after,
    ):
        """
        Guarda únicamente información de UX.

        NO es la barrera de seguridad real.
        La autoridad continúa siendo
        RateLimitBucket en la base de datos.
        """

        retry_after = max(
            int(retry_after),
            1,
        )

        bloqueado_hasta = (
            timezone.now().timestamp()
            + retry_after
        )

        username = (
            request.POST.get(
                "username",
                "",
            )
            .strip()
        )

        request.session[
            self.RATE_LIMIT_SESSION_UNTIL
        ] = bloqueado_hasta

        request.session[
            self.RATE_LIMIT_SESSION_USERNAME
        ] = username[:150]

        request.session.modified = True

    def get_context_data(
        self,
        **kwargs,
    ):
        context = super().get_context_data(
            **kwargs
        )

        bloqueado_hasta = (
            self.request.session.get(
                self.RATE_LIMIT_SESSION_UNTIL
            )
        )

        if not bloqueado_hasta:
            return context

        try:
            segundos_restantes = int(
                float(
                    bloqueado_hasta
                )
                - timezone.now().timestamp()
            )

        except (
            TypeError,
            ValueError,
        ):
            self._limpiar_estado_visual_rate_limit()
            return context

        if segundos_restantes <= 0:
            self._limpiar_estado_visual_rate_limit()
            return context

        context[
            "rate_limit_error"
        ] = self.RATE_LIMIT_MESSAGE

        context[
            "rate_limit_retry_after"
        ] = max(
            segundos_restantes,
            1,
        )

        form = context.get(
            "form"
        )

        username_guardado = (
            self.request.session.get(
                self.RATE_LIMIT_SESSION_USERNAME,
                "",
            )
        )

        if (
            form
            and not form.is_bound
            and username_guardado
        ):
            form.initial[
                "username"
            ] = username_guardado

        return context

    def _respuesta_rate_limit(
        self,
        request,
        retry_after,
    ):
        """
        Respuesta HTTP 429 sin volver a ejecutar
        autenticación ni comprobación de contraseña.
        """

        retry_after = max(
            int(retry_after),
            1,
        )

        self._guardar_estado_visual_rate_limit(
            request,
            retry_after,
        )

        form_class = self.get_form_class()

        form = form_class(
            request=request,
            initial={
                "username": (
                    request.POST.get(
                        "username",
                        "",
                    )
                    .strip()
                ),
            },
        )

        context = self.get_context_data(
            form=form,
        )

        response = self.render_to_response(
            context,
            status=429,
        )

        response[
            "Retry-After"
        ] = str(
            retry_after
        )

        return response

    def post(
        self,
        request,
        *args,
        **kwargs,
    ):
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

        username = username[:150]

        # -------------------------------------------------
        # CAPA 1
        # Intentos totales desde la misma IP.
        # -------------------------------------------------

        limite_ip = consumir_rate_limit(
            group="login-ip",
            raw_key=ip,
            limite=(
                settings
                .FOODBACK_LOGIN_IP_LIMIT
            ),
            ventana_segundos=(
                settings
                .FOODBACK_LOGIN_WINDOW_SECONDS
            ),
            bloqueo_segundos=(
                settings
                .FOODBACK_LOGIN_BLOCK_SECONDS
            ),
        )

        if not limite_ip[
            "permitido"
        ]:
            return self._respuesta_rate_limit(
                request,
                limite_ip[
                    "retry_after"
                ],
            )

        # -------------------------------------------------
        # CAPA 2
        # Misma IP atacando un username concreto.
        # -------------------------------------------------

        clave_usuario = (
            f"{ip}:"
            f"{username or '<empty>'}"
        )

        limite_usuario = consumir_rate_limit(
            group="login-ip-username",
            raw_key=clave_usuario,
            limite=(
                settings
                .FOODBACK_LOGIN_USER_LIMIT
            ),
            ventana_segundos=(
                settings
                .FOODBACK_LOGIN_WINDOW_SECONDS
            ),
            bloqueo_segundos=(
                settings
                .FOODBACK_LOGIN_BLOCK_SECONDS
            ),
        )

        if not limite_usuario[
            "permitido"
        ]:
            return self._respuesta_rate_limit(
                request,
                limite_usuario[
                    "retry_after"
                ],
            )

        return super().post(
            request,
            *args,
            **kwargs,
        )

    def form_valid(
        self,
        form,
    ):
        """
        Si finalmente existe un login válido,
        eliminamos cualquier marca visual vieja.
        """

        self._limpiar_estado_visual_rate_limit()

        return super().form_valid(
            form
        )

    def get_success_url(self):
        membership = obtener_membership_activo(
            self.request,
            roles=[
                Membership.ROLE_OWNER,
                Membership.ROLE_MANAGER,
            ],
        )

        if membership:
            return reverse(
                "dashboard_admin"
            )

        asignacion_delivery = (
            obtener_asignacion_repartidor_activa(
                self.request
            )
        )

        if asignacion_delivery:
            return reverse(
                "dashboard_delivery"
            )

        return reverse(
            "menu"
        )



@require_POST
def logout_view(request):
    logout(request)
    return redirect('login_custom')

# --- VALIDACIÓN DE SUSCRIPCIÓN (EL GUARDIA DE SEGURIDAD) ---


def suscripcion_activa(tenant):
    """
    Indica si un Tenant puede operar.

    La suscripción ya NO depende de
    ConfiguracionNegocio.
    """

    if not tenant:
        return False

    if not tenant.habilitado:
        return False

    try:
        suscripcion = tenant.suscripcion

    except SuscripcionTenant.DoesNotExist:
        # Fail closed:
        # un Tenant sin suscripción configurada
        # no debe quedar activo accidentalmente.
        return False

    hoy = date.today()

    if suscripcion.estado in [
        SuscripcionTenant.Estado.SUSPENDIDA,
        SuscripcionTenant.Estado.CANCELADA,
    ]:
        return False

    if (
        suscripcion.estado
        == SuscripcionTenant.Estado.GRACIA
    ):
        return bool(
            suscripcion.gracia_hasta
            and
            hoy <= suscripcion.gracia_hasta
        )

    return bool(
        suscripcion.fecha_vencimiento
        and
        hoy < suscripcion.fecha_vencimiento
    )

# --- CEREBRO DEL TIEMPO ---


def _limpiar_pedidos_pendientes_vencidos():
    """
    TEMPORALMENTE DESACTIVADO.

    Los pedidos relacionados con un intento de pago no deben
    eliminarse físicamente por simplemente haber superado
    cierto tiempo.

    La expiración se implementará después mediante un proceso
    de mantenimiento que cambie el estado de forma lógica,
    conservando la evidencia financiera.
    """

    return 0


def verificar_estado_negocio(sucursal):
    """
    Determina si una sucursal específica está abierta.

    Esta función NO decide el estado de la suscripción.
    La suscripción pertenece conceptualmente al Tenant
    y se valida por separado.
    """

    if not sucursal:
        return False, "Sucursal no disponible."

    config = (
        ConfiguracionNegocio.objects
        .filter(sucursal=sucursal)
        .first()
    )

    if not config:
        return (
            False,
            "Sucursal sin configuración operativa."
        )

    ahora = datetime.now()
    fecha_hoy = ahora.date()
    hora_actual = ahora.time()
    dia_semana = ahora.weekday()

    apertura_efectiva = config.hora_apertura
    cierre_efectivo = config.hora_cierre
    mensaje_base = config.mensaje_cierre

    dias_globales = [
        config.lunes_abierto,
        config.martes_abierto,
        config.miercoles_abierto,
        config.jueves_abierto,
        config.viernes_abierto,
        config.sabado_abierto,
        config.domingo_abierto,
    ]

    esta_habilitado = dias_globales[
        dia_semana
    ]

    excepcion = (
        DiaEspecial.objects
        .filter(
            sucursal=sucursal,
            fecha=fecha_hoy,
        )
        .first()
    )

    if excepcion:
        if excepcion.abierto:
            esta_habilitado = True

            if excepcion.hora_apertura:
                apertura_efectiva = (
                    excepcion.hora_apertura
                )

            if excepcion.hora_cierre:
                cierre_efectivo = (
                    excepcion.hora_cierre
                )

        else:
            motivo = excepcion.motivo or ""

            if motivo:
                return (
                    False,
                    f"{mensaje_base} ({motivo})"
                )

            return False, mensaje_base

    if esta_habilitado:
        # Horario normal:
        # 10:00 -> 22:00
        if apertura_efectiva < cierre_efectivo:
            if (
                apertura_efectiva
                <= hora_actual
                <= cierre_efectivo
            ):
                return True, ""

        # Horario que cruza medianoche:
        # 18:00 -> 02:00
        else:
            if (
                hora_actual >= apertura_efectiva
                or hora_actual <= cierre_efectivo
            ):
                return True, ""

        ap_str = apertura_efectiva.strftime(
            "%I:%M %p"
        ).lower()

        ci_str = cierre_efectivo.strftime(
            "%I:%M %p"
        ).lower()

        return (
            False,
            f"{mensaje_base} "
            f"(Hoy: {ap_str} - {ci_str})"
        )

    return False, mensaje_base


# --- SEGURIDAD E INTEGRIDAD DEL CARRITO ---




class CarritoInvalido(ValueError):
    """
    Error interno controlado para cualquier dato de carrito
    que no coincida con la configuración real de la base de datos.
    """
    pass


def _entero_positivo(valor, nombre_campo):
    """
    Convierte IDs/cantidades recibidos desde cliente o sesión
    a enteros positivos.

    Nunca confiamos directamente en strings recibidos.
    """
    try:
        numero = int(str(valor))
    except (TypeError, ValueError):
        raise CarritoInvalido(
            f"{nombre_campo} contiene un identificador inválido."
        )

    if numero <= 0:
        raise CarritoInvalido(
            f"{nombre_campo} debe ser un entero positivo."
        )

    return numero


def _validar_seleccion_producto(
    producto,
    opcion_id=None,
    extras_ids=None,
    tenant=None,
):
    """
    Valida producto, opción y extras dentro del Tenant activo.

    Ningún ID enviado por navegador/sesión se considera
    autorizado simplemente porque exista en la BD.
    """

    if not tenant:
        raise CarritoInvalido(
            "No existe un restaurante activo."
        )

    if producto.categoria.tenant_id != tenant.id:
        raise CarritoInvalido(
            "El producto no pertenece al restaurante activo."
        )

    opcion = None

    if opcion_id not in [
        None,
        "",
        "0",
        0,
    ]:
        opcion_id_limpio = (
            _entero_positivo(
                opcion_id,
                "opcion_id",
            )
        )

        opcion = (
            OpcionProducto.objects
            .filter(
                id=opcion_id_limpio,
                producto=producto,
                producto__categoria__tenant=tenant,
                disponible=True,
            )
            .first()
        )

        if not opcion:
            raise CarritoInvalido(
                (
                    "La opción seleccionada "
                    "no es válida para este producto."
                )
            )

    extras_ids = extras_ids or []

    extras_ids_limpios = []

    for extra_id in extras_ids:
        extra_id_limpio = (
            _entero_positivo(
                extra_id,
                "extra_id",
            )
        )

        if (
            extra_id_limpio
            not in extras_ids_limpios
        ):
            extras_ids_limpios.append(
                extra_id_limpio
            )

    extras = []

    if extras_ids_limpios:
        extras = list(
            producto.extras.filter(
                id__in=extras_ids_limpios,
                tenant=tenant,
                disponible=True,
            ).order_by("id")
        )

        ids_encontrados = {
            extra.id
            for extra in extras
        }

        if (
            ids_encontrados
            != set(extras_ids_limpios)
        ):
            raise CarritoInvalido(
                (
                    "Uno o más extras "
                    "no son válidos para "
                    "este producto."
                )
            )

    return opcion, extras


def _descomponer_clave_carrito(key):
    """
    Formato esperado:

    producto-opcion-extras

    Ejemplo:
    5-8-2,4
    """

    partes = str(key).split("-", 2)

    if len(partes) != 3:
        raise CarritoInvalido(
            "La estructura del carrito es inválida."
        )

    producto_id = _entero_positivo(
        partes[0],
        "producto_id",
    )

    opcion_id = partes[1]

    extras_raw = partes[2]

    if extras_raw in ["", "0"]:
        extras_ids = []
    else:
        extras_ids = extras_raw.split(",")

    return producto_id, opcion_id, extras_ids


def _validar_carrito(
    cart,
    tenant,
):
    """
    Reconstruye el carrito desde la BD y lo limita
    estrictamente al Tenant activo.
    """

    if not tenant:
        raise CarritoInvalido(
            "No existe un restaurante activo."
        )

    if not isinstance(cart, dict):
        raise CarritoInvalido(
            "La estructura del carrito no es válida."
        )

    items_validados = []

    for key, cantidad_raw in cart.items():

        cantidad = _entero_positivo(
            cantidad_raw,
            "cantidad",
        )

        (
            producto_id,
            opcion_id,
            extras_ids,
        ) = _descomponer_clave_carrito(
            key
        )

        producto = (
            Producto.objects
            .select_related(
                "categoria"
            )
            .filter(
                id=producto_id,
                categoria__tenant=tenant,
                disponible=True,
            )
            .first()
        )

        if not producto:
            raise CarritoInvalido(
                (
                    "El producto ya no está "
                    "disponible para este restaurante."
                )
            )

        opcion, extras = (
            _validar_seleccion_producto(
                producto=producto,
                opcion_id=opcion_id,
                extras_ids=extras_ids,
                tenant=tenant,
            )
        )

        precio_item = producto.precio

        if opcion:
            precio_item += (
                opcion.precio_extra
            )

        for extra in extras:
            precio_item += extra.precio

        subtotal = (
            precio_item
            * cantidad
        )

        items_validados.append({
            "key": key,
            "producto": producto,
            "cantidad": cantidad,
            "opcion": opcion,
            "nombre_opcion": (
                opcion.nombre
                if opcion
                else ""
            ),
            "lista_extras": extras,
            "precio_item": precio_item,
            "subtotal": subtotal,
        })

    return items_validados






# --- VISTAS PÚBLICAS ---
@require_safe
def menu_view(request):
    _limpiar_pedidos_pendientes_vencidos()

    if not suscripcion_activa(
        request.tenant
    ):
        return render(
            request,
            'pedidos/suspendido.html'
        )

    if request.tenant:
        categorias = (
            Categoria.objects
            .filter(
                tenant=request.tenant
            )
            .order_by("orden")
    )
    else:
        categorias = (
            Categoria.objects.none()
        )

    cart = request.session.get(
        'cart',
        {}
    )

    cantidad_total = sum(
        cart.values()
    )

    abierto, mensaje_estado = (
        verificar_estado_negocio(
            request.sucursal
        )
    )

    ids_historial = _pedidos_sesion(
        request
    )

    ids_ocultos = set(
        request.session.get(
            'pedidos_pendientes_ocultos',
            []
        )
    )

    if request.sucursal:
        candidatos = (
            Pedido.objects
            .filter(
                id__in=ids_historial,
                sucursal=request.sucursal,
            )
            .exclude(
                estado__in=[
                    "ENTREGADO",
                    "CANCELADO",
                ]
            )
            .order_by("-id")
    )
    else:
        candidatos = (
            Pedido.objects.none()
        )

    pedidos_activos = []

    for pedido in candidatos:

        oculto_temporalmente = (
            pedido.id in ids_ocultos
            and
            pedido.estado == 'PENDIENTE'
            and
            not pedido.pago_verificado
        )

        if oculto_temporalmente:
            continue

        pedidos_activos.append(
            pedido
        )

    ultimo_pedido_activo = (
        pedidos_activos[0]
        if pedidos_activos
        else None
    )

    if ultimo_pedido_activo:

        request.session[
            'ultimo_pedido_id'
        ] = ultimo_pedido_activo.id

    else:

        request.session.pop(
            'ultimo_pedido_id',
            None
        )

    request.session.modified = True

    return render(
        request,
        'pedidos/menu.html',
        {
            'categorias':
                categorias,

            'cantidad_carrito':
                cantidad_total,

            'abierto':
                abierto,

            'mensaje_estado':
                mensaje_estado,

            'ultimo_pedido_activo':
                ultimo_pedido_activo,

            # Nuevo: no perdemos los demás.
            'pedidos_activos':
                pedidos_activos,

            'cantidad_pedidos_activos':
                len(pedidos_activos),
        }
    )

@require_POST
def cart_add(request, producto_id):
    if not suscripcion_activa(
        request.tenant
    ):
        return render(
            request,
            'pedidos/suspendido.html'
        )

    producto = get_object_or_404(
        Producto,
        id=producto_id,
        categoria__tenant=request.tenant,
        disponible=True,
    )

    opcion_id = request.POST.get(
        'opcion_id'
    )

    extras_ids = request.POST.getlist(
        'extras'
    )

    try:
        opcion, extras = _validar_seleccion_producto(
            producto=producto,
            opcion_id=opcion_id,
            extras_ids=extras_ids,
            tenant=request.tenant,
        )

    except CarritoInvalido:
        return HttpResponseBadRequest(
            "La selección enviada no es válida."
        )

    cart = request.session.get(
        'cart',
        {}
    )

    # Construimos la clave usando únicamente IDs
    # que ya fueron validados contra la BD.
    opcion_key = (
        str(opcion.id)
        if opcion
        else "0"
    )

    extras_key = "0"

    if extras:
        extras_key = ",".join(
            str(extra.id)
            for extra in sorted(
                extras,
                key=lambda e: e.id
            )
        )

    key = (
        f"{producto.id}-"
        f"{opcion_key}-"
        f"{extras_key}"
    )

    if key in cart:
        cart[key] += 1
    else:
        cart[key] = 1

    request.session['cart'] = cart
    request.session.modified = True

    nombre_mostrar = producto.nombre

    if opcion:
        nombre_mostrar += (
            f" ({opcion.nombre})"
        )

    if extras:
        nombre_mostrar += " + Extras"

    messages.success(
        request,
        f"¡{nombre_mostrar} agregado!"
    )

    return redirect(
        request.META.get(
            'HTTP_REFERER',
            'menu'
        )
    )

@require_POST
def cart_clear(request):
    request.session['cart'] = {}
    request.session.modified = True
    messages.info(request, "Tu carrito ha sido vaciado.")
    return redirect('menu')


@require_POST
def eliminar_item_carrito(
    request,
    producto_id,
):
    cart = request.session.get(
        "cart",
        {},
    )

    key_to_delete = str(
        producto_id
    )

    if key_to_delete in cart:
        del cart[key_to_delete]

        request.session[
            "cart"
        ] = cart

        request.session.modified = True

    if (
        request.headers.get(
            "x-requested-with"
        )
        == "XMLHttpRequest"
    ):
        try:
            items = _validar_carrito(
                cart,
                request.tenant,
            )

        except CarritoInvalido:
            return JsonResponse(
                {
                    "status": "error",
                    "detail": (
                        "El carrito contiene "
                        "datos no válidos."
                    ),
                },
                status=400,
            )

        total_productos = sum(
            item["subtotal"]
            for item in items
        )

        html = render_to_string(
            "pedidos/partials/cart_summary.html",
            {
                "items": items,
                "total_productos": (
                    total_productos
                ),
            },
            request=request,
        )

        return JsonResponse({
            "status": "ok",
            "html": html,
            "total": float(
                total_productos
            ),
            "vacio": (
                len(cart) == 0
            ),
        })

    return redirect(
        "checkout"
    )


CHECKOUT_TOKEN_SESSION_KEY = (
    "foodback_checkout_token"
)


def _obtener_checkout_token(
    request,
):
    """
    Devuelve el token de envío actual del checkout.

    Si no existe o fue manipulado, genera uno nuevo.
    """

    valor = request.session.get(
        CHECKOUT_TOKEN_SESSION_KEY
    )

    if valor:
        try:
            return uuid.UUID(
                str(valor)
            )

        except (
            ValueError,
            TypeError,
            AttributeError,
        ):
            pass

    token = uuid.uuid4()

    request.session[
        CHECKOUT_TOKEN_SESSION_KEY
    ] = str(token)

    request.session.modified = True

    return token


def _checkout_token_post_valido(
    request,
):
    """
    El token recibido debe coincidir exactamente
    con el que emitió la sesión actual.
    """

    recibido = (
        request.POST.get(
            "checkout_token",
            "",
        )
        .strip()
    )

    esperado = request.session.get(
        CHECKOUT_TOKEN_SESSION_KEY,
        "",
    )

    if (
        not recibido
        or not esperado
        or recibido != esperado
    ):
        return None

    try:
        return uuid.UUID(
            recibido
        )

    except (
        ValueError,
        TypeError,
        AttributeError,
    ):
        return None
    
    

def _finalizar_checkout_en_sesion(
    request,
    pedido,
):
    """
    Consolida el estado de sesión después de que un Pedido
    quedó creado, tanto en el flujo normal como al recuperar
    un envío idempotente concurrente.
    """

    request.session[
        "ultimo_pedido_id"
    ] = pedido.id

    historial = request.session.get(
        "historial_pedidos",
        [],
    )

    if pedido.id not in historial:
        historial.append(
            pedido.id
        )

    request.session[
        "historial_pedidos"
    ] = historial

    request.session[
        "cart"
    ] = {}

    request.session.pop(
        CHECKOUT_TOKEN_SESSION_KEY,
        None,
    )

    request.session.modified = True


def _obtener_pedido_pendiente_recuperable(request):
    """
    Busca exclusivamente el último pedido que FoodBack guardó
    en esta sesión y que todavía espera pago con tarjeta.

    No recibe IDs arbitrarios desde la URL ni desde POST.
    """

    pedido_id = request.session.get(
        'ultimo_pedido_id'
    )

    if not pedido_id:
        return None

    return (
        Pedido.objects
        .filter(
            id=pedido_id,
            sucursal=request.sucursal,
            metodo_pago="TARJETA",
            estado="PENDIENTE",
            pago_verificado=False,
        )
        .select_related(
            'cliente'
        )
        .prefetch_related(
            'detalles__producto',
            'detalles__opcion',
            'detalles__extras',
        )
        .first()
    )


@require_http_methods([
    "GET",
    "POST",
])
def checkout_view(request):
    _limpiar_pedidos_pendientes_vencidos()
    
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
        return HttpResponseForbidden(
            "No hay una sucursal activa."
        )

    if not suscripcion_activa(
        request.tenant
    ):
        return render(
            request,
            'pedidos/suspendido.html'
        )

    abierto, mensaje = (
        verificar_estado_negocio(
            request.sucursal
        )
    )

    if not abierto:
        messages.error(
            request,
            f"⛔ El restaurante ha cerrado. {mensaje}"
        )
        return redirect('menu')
    
    
    # -------------------------------------------------
    # SEGURIDAD DEL POST
    #
    # El anti-flood se consume ANTES de validar token,
    # carrito o datos comerciales.
    #
    # Así un atacante no puede enviar POST inválidos
    # ilimitadamente evitando los buckets defensivos.
    # -------------------------------------------------

    checkout_token = None

    if request.method == 'POST':

        rate_limit_checkout = (
            _verificar_rate_limit_checkout(
                request,
                tenant,
            )
        )

        if rate_limit_checkout:
            response = HttpResponseBadRequest(
                "Se realizaron demasiados "
                "intentos de pedido. "
                "Espera unos minutos "
                "e inténtalo nuevamente."
            )

            response.status_code = 429

            response[
                "Retry-After"
            ] = str(
                max(
                    int(
                        rate_limit_checkout[
                            "retry_after"
                        ]
                    ),
                    1,
                )
            )

            return response

        checkout_token = (
            _checkout_token_post_valido(
                request
            )
        )

        if not checkout_token:
            return HttpResponseBadRequest(
                "El envío del checkout no es válido. "
                "Actualiza la página e inténtalo nuevamente."
            )

    cart = request.session.get(
        'cart',
        {}
    )
    
    pedido_pendiente = None

    if request.method == 'GET' and not cart:
        pedido_pendiente = (
            _obtener_pedido_pendiente_recuperable(
                request
            )
        )

    # -------------------------------------------------
    # DEFENSA EN PROFUNDIDAD
    #
    # Aunque cart_add ya haya validado el producto,
    # checkout vuelve a validar TODO desde la BD.
    # -------------------------------------------------

    items_validados = []

    if cart:
        try:
            items_validados = _validar_carrito(
                cart,
                request.tenant,
            )

        except CarritoInvalido:
            if request.method == 'POST':
                return HttpResponseBadRequest(
                    "No pudimos validar el carrito."
                )

            # Si llegamos mediante GET con una sesión
            # corrupta/manipulada, eliminamos el carrito.
            request.session['cart'] = {}
            request.session.modified = True

            messages.error(
                request,
                "Tu carrito contenía una selección que "
                "ya no es válida. Agrégala nuevamente."
            )

            return redirect('menu')

    if request.method == 'POST': 

        telefono = request.POST.get(
            'telefono'
        )

        nombre = request.POST.get(
            'nombre'
        )

        apellido = request.POST.get(
            'apellido'
        )

        direccion = request.POST.get(
            'direccion'
        )

        metodo_pago = request.POST.get(
            'metodo_pago'
        )

        lat = request.POST.get(
            'latitud'
        )

        lng = request.POST.get(
            'longitud'
        )
        
        metodos_pago_validos = {
            "EFECTIVO",
            "TARJETA",
        }

        if (
            metodo_pago
            not in metodos_pago_validos
        ):
            return HttpResponseBadRequest(
                "Método de pago no válido."
            )
        
        if metodo_pago == 'TARJETA':

            estado_global = (
                _estado_bloqueo_pasarela_tenant(
                    request.tenant
                )
            )

            if estado_global[
                'bloqueada'
            ]:
                messages.warning(
                    request,
                    (
                        'El pago con tarjeta está '
                        'temporalmente no disponible. '
                        'Puedes completar tu pedido '
                        'en efectivo.'
                    )
                )

                return redirect(
                    'checkout'
                )

            estado_bloqueo = (
                _estado_bloqueo_tarjeta_cliente(
                    request
                )
            )

            if estado_bloqueo[
                'bloqueada'
            ]:
                messages.warning(
                    request,
                    (
                        'Tarjeta temporalmente no disponible '
                        'debido a varios intentos rechazados. '
                        'Puedes usar efectivo o intentarlo '
                        'nuevamente más tarde.'
                    )
                )

                return redirect(
                    'checkout'
                )

        if not cart:
            messages.error(
                request,
                "El carrito está vacío."
            )
            return redirect('menu')

        if not telefono or len(telefono) < 8:
            messages.error(
                request,
                "Revisa tu teléfono."
            )
            return redirect('checkout')

        try:
            with transaction.atomic():

                cliente, created = (
                    Cliente.objects.get_or_create(
                        tenant=request.tenant,
                        telefono=telefono,
                        defaults={
                            "nombre": nombre,
                            "apellido": apellido,
                            "direccion_ultima": direccion,
                        },
                    )
                )

                if not created:
                    cliente.nombre = nombre
                    cliente.apellido = apellido
                    cliente.direccion_ultima = direccion
                    cliente.save()

                estado_inicial = (
                    'PENDIENTE'
                    if metodo_pago == 'TARJETA'
                    else 'RECIBIDO'
                )

                pedido = Pedido.objects.create(
                    sucursal=request.sucursal,
                    cliente=cliente,
                    direccion_entrega=direccion,
                    metodo_pago=metodo_pago,
                    latitud=lat,
                    longitud=lng,
                    es_pedido_whatsapp=False,
                    estado=estado_inicial,
                    checkout_token=checkout_token,
                )

                # -------------------------------------
                # SOLO usamos objetos validados.
                # No volvemos a confiar en IDs crudos.
                # -------------------------------------

                for item in items_validados:

                    producto = item[
                        'producto'
                    ]

                    opcion = item[
                        'opcion'
                    ]

                    extras = item[
                        'lista_extras'
                    ]

                    cantidad = item[
                        'cantidad'
                    ]

                    detalle = (
                        DetallePedido.objects.create(
                            pedido=pedido,
                            producto=producto,
                            cantidad=cantidad,
                            precio_unitario=producto.precio,
                            opcion=opcion,
                        )
                    )

                    for extra in extras:
                        detalle.extras.add(
                            extra
                        )

                    detalle.save()

                pedido.save()

                _finalizar_checkout_en_sesion(
                    request,
                    pedido,
                )

                if metodo_pago == 'TARJETA':
                    return _iniciar_pago_wompi_pedido(
                        request,
                        pedido
                    )

                return redirect(
                    'order_tracker',
                    tracking_token=(
                        pedido.tracking_token
                    )
                )
                
        except IntegrityError as e:
        # Puede ocurrir si dos POST legítimos llegan
        # casi simultáneamente con el mismo checkout_token.
        #
        # La restricción UNIQUE deja crear un solo Pedido.
        # El segundo request recupera ese mismo Pedido.

            pedido_existente = (
                Pedido.objects
                .filter(
                    sucursal=request.sucursal,
                    checkout_token=checkout_token,
                )
                .first()
            )

            if pedido_existente:
                _finalizar_checkout_en_sesion(
                    request,
                    pedido_existente,
                )

                if (
                    pedido_existente.metodo_pago
                    == 'TARJETA'
                ):
                    return _iniciar_pago_wompi_pedido(
                        request,
                        pedido_existente,
                    )

                return redirect(
                    'order_tracker',
                    tracking_token=(
                        pedido_existente.tracking_token
                    ),
                )

            # Si el IntegrityError fue provocado por otra
            # restricción, no lo tratamos falsamente como
            # una repetición idempotente.
            print(
                f"Error de integridad checkout: {e}"
            )

            messages.error(
                request,
                "No pudimos procesar tu pedido. "
                "Intenta nuevamente."
            )

            return redirect(
                'checkout'
            )
                

        except Exception as e:
            # El detalle técnico NO se envía al usuario.
            # De momento queda únicamente en consola.
            # Más adelante irá al sistema profesional
            # de logging/error reporting.
            print(
                f"Error interno checkout: {e}"
            )

            messages.error(
                request,
                "No pudimos procesar tu pedido. "
                "Intenta nuevamente."
            )

            return redirect('checkout')

    # -----------------------------------------
    # Render del checkout usando únicamente
    # datos que ya pasaron por el validador.
    # -----------------------------------------

    productos_en_carrito = []
    total_productos = Decimal(
        '0.00'
    )

    # -------------------------------------------------
    # RECUPERACIÓN DE UN PEDIDO DE TARJETA YA CREADO
    # -------------------------------------------------

    if pedido_pendiente:

        for detalle in pedido_pendiente.detalles.all():

            extras = list(
                detalle.extras.all()
            )

            productos_en_carrito.append({
                'producto':
                    detalle.producto,

                'cantidad':
                    detalle.cantidad,

                'subtotal':
                    detalle.subtotal,

                'opcion':
                    detalle.opcion,

                'nombre_opcion':
                    (
                        detalle.opcion.nombre
                        if detalle.opcion
                        else ''
                    ),

                'lista_extras':
                    extras,

                # No permitimos eliminar partes individuales
                # de un Pedido ya congelado en BD.
                'key':
                    None,
            })

        total_productos = (
            pedido_pendiente.total_productos
        )

    else:

        for item in items_validados:

            productos_en_carrito.append({
                'producto': item[
                    'producto'
                ],
                'cantidad': item[
                    'cantidad'
                ],
                'subtotal': item[
                    'subtotal'
                ],
                'opcion': item[
                    'opcion'
                ],
                'nombre_opcion': item[
                    'nombre_opcion'
                ],
                'lista_extras': item[
                    'lista_extras'
                ],
                'key': item[
                    'key'
                ],
            })

            total_productos += item[
                'subtotal'
            ]


    pago_pendiente = None

    if pedido_pendiente:
        pago_pendiente = (
            pedido_pendiente
            .pagos_wompi
            .order_by(
                '-fecha_creacion'
            )
            .first()
        )

    estado_tarjeta_cliente = (
        _estado_bloqueo_tarjeta_cliente(
            request
        )
    )
    
    estado_pasarela_global = (
        _estado_bloqueo_pasarela_tenant(
            request.tenant
        )
    )

    checkout_token = (
        _obtener_checkout_token(
            request
        )
    )

    context = {
        'checkout_token':
            str(checkout_token),

        'items':
            productos_en_carrito,

        'total_productos':
            total_productos,

        'total_wompi': (
            pedido_pendiente.total_productos
            if pedido_pendiente
            else total_productos
        ),

        'pedido_pendiente':
            pedido_pendiente,

        'pago_pendiente':
            pago_pendiente,

        'GOOGLE_MAPS_API_KEY':
            config(
                'GOOGLE_MAPS_API_KEY',
                default=''
            ),
                'tarjeta_cliente_bloqueada': (
            estado_tarjeta_cliente[
                'bloqueada'
            ]
        ),

        'tarjeta_cliente_bloqueada_hasta': (
            estado_tarjeta_cliente[
                'bloqueado_hasta'
            ]
        ),

        'tarjeta_cliente_fallos': (
            estado_tarjeta_cliente[
                'cantidad_fallos'
            ]
        ),
                'pasarela_global_bloqueada': (
            estado_pasarela_global[
                'bloqueada'
            ]
        ),

        'pasarela_bloqueo_manual': (
            estado_pasarela_global[
                'bloqueo_manual'
            ]
        ),

        'pasarela_bloqueada_hasta': (
            estado_pasarela_global[
                'bloqueado_hasta'
            ]
        ),

        'tarjeta_no_disponible': (
            estado_tarjeta_cliente[
                'bloqueada'
            ]
            or
            estado_pasarela_global[
                'bloqueada'
            ]
        ),
    }

    return render(
        request,
        'pedidos/checkout.html',
        context
    )
    
    
    
def _verificar_rate_limit_checkout(
    request,
    tenant,
):
    """
    Protección anti-flood del checkout.

    Capa 1:
        navegador/sesión dentro del Tenant.

    Capa 2:
        IP dentro del Tenant.

    Devuelve None si está permitido.
    Devuelve resultado bloqueado si excede límite.
    """

    cliente_id = (
        obtener_id_seguridad_cliente(
            request
        )
    )

    ip = obtener_ip_cliente(
        request
    )

    tenant_id = (
        tenant.id
        if tenant
        else "none"
    )

    limite_cliente = consumir_rate_limit(
        group="checkout-client",
        raw_key=(
            f"{tenant_id}:"
            f"{cliente_id}"
        ),
        limite=(
            settings
            .FOODBACK_CHECKOUT_SESSION_LIMIT
        ),
        ventana_segundos=(
            settings
            .FOODBACK_CHECKOUT_WINDOW_SECONDS
        ),
        bloqueo_segundos=(
            settings
            .FOODBACK_CHECKOUT_BLOCK_SECONDS
        ),
    )

    if not limite_cliente[
        "permitido"
    ]:
        return limite_cliente

    limite_ip = consumir_rate_limit(
        group="checkout-ip",
        raw_key=(
            f"{tenant_id}:"
            f"{ip}"
        ),
        limite=(
            settings
            .FOODBACK_CHECKOUT_IP_LIMIT
        ),
        ventana_segundos=(
            settings
            .FOODBACK_CHECKOUT_WINDOW_SECONDS
        ),
        bloqueo_segundos=(
            settings
            .FOODBACK_CHECKOUT_BLOCK_SECONDS
        ),
    )

    if not limite_ip[
        "permitido"
    ]:
        return limite_ip

    return None



def _verificar_rate_limit_pagar_wompi(
    request,
    tenant,
):
    """
    Protección anti-flood al iniciar/reintentar
    un pago Wompi.

    Capa 1:
        navegador/sesión dentro del Tenant.

    Capa 2:
        IP dentro del Tenant.
    """

    cliente_id = obtener_id_seguridad_cliente(
        request
    )

    ip = obtener_ip_cliente(
        request
    )

    tenant_id = (
        tenant.id
        if tenant
        else "none"
    )

    limite_cliente = consumir_rate_limit(
        group="pagar-wompi-client",
        raw_key=(
            f"{tenant_id}:"
            f"{cliente_id}"
        ),
        limite=(
            settings
            .FOODBACK_WOMPI_START_SESSION_LIMIT
        ),
        ventana_segundos=(
            settings
            .FOODBACK_WOMPI_START_WINDOW_SECONDS
        ),
        bloqueo_segundos=(
            settings
            .FOODBACK_WOMPI_START_BLOCK_SECONDS
        ),
    )

    if not limite_cliente[
        "permitido"
    ]:
        return limite_cliente

    limite_ip = consumir_rate_limit(
        group="pagar-wompi-ip",
        raw_key=(
            f"{tenant_id}:"
            f"{ip}"
        ),
        limite=(
            settings
            .FOODBACK_WOMPI_START_IP_LIMIT
        ),
        ventana_segundos=(
            settings
            .FOODBACK_WOMPI_START_WINDOW_SECONDS
        ),
        bloqueo_segundos=(
            settings
            .FOODBACK_WOMPI_START_BLOCK_SECONDS
        ),
    )

    if not limite_ip[
        "permitido"
    ]:
        return limite_ip

    return None



def _verificar_rate_limit_retomar_pago(
    request,
    tenant,
):
    """
    Protección anti-flood al intentar retomar
    un pago pendiente.

    Capa 1:
        navegador/sesión dentro del Tenant.

    Capa 2:
        IP dentro del Tenant.
    """

    cliente_id = obtener_id_seguridad_cliente(
        request
    )

    ip = obtener_ip_cliente(
        request
    )

    tenant_id = (
        tenant.id
        if tenant
        else "none"
    )

    limite_cliente = consumir_rate_limit(
        group="retomar-pago-client",
        raw_key=(
            f"{tenant_id}:"
            f"{cliente_id}"
        ),
        limite=(
            settings
            .FOODBACK_PAYMENT_RESUME_SESSION_LIMIT
        ),
        ventana_segundos=(
            settings
            .FOODBACK_PAYMENT_RESUME_WINDOW_SECONDS
        ),
        bloqueo_segundos=(
            settings
            .FOODBACK_PAYMENT_RESUME_BLOCK_SECONDS
        ),
    )

    if not limite_cliente[
        "permitido"
    ]:
        return limite_cliente

    limite_ip = consumir_rate_limit(
        group="retomar-pago-ip",
        raw_key=(
            f"{tenant_id}:"
            f"{ip}"
        ),
        limite=(
            settings
            .FOODBACK_PAYMENT_RESUME_IP_LIMIT
        ),
        ventana_segundos=(
            settings
            .FOODBACK_PAYMENT_RESUME_WINDOW_SECONDS
        ),
        bloqueo_segundos=(
            settings
            .FOODBACK_PAYMENT_RESUME_BLOCK_SECONDS
        ),
    )

    if not limite_ip[
        "permitido"
    ]:
        return limite_ip

    return None



def _verificar_rate_limit_pago_suscripcion(
    request,
    tenant,
):
    """
    Protección anti-flood al iniciar
    el pago de una suscripción FoodBack.

    Capa 1:
        usuario autenticado dentro del Tenant.

    Capa 2:
        IP dentro del Tenant.
    """

    usuario_id = getattr(
        request.user,
        "pk",
        None,
    )

    ip = obtener_ip_cliente(
        request
    )

    tenant_id = (
        tenant.id
        if tenant
        else "none"
    )

    limite_usuario = consumir_rate_limit(
        group="pagar-suscripcion-user",
        raw_key=(
            f"{tenant_id}:"
            f"{usuario_id or 'unknown'}"
        ),
        limite=(
            settings
            .FOODBACK_SUBSCRIPTION_PAYMENT_USER_LIMIT
        ),
        ventana_segundos=(
            settings
            .FOODBACK_SUBSCRIPTION_PAYMENT_WINDOW_SECONDS
        ),
        bloqueo_segundos=(
            settings
            .FOODBACK_SUBSCRIPTION_PAYMENT_BLOCK_SECONDS
        ),
    )

    if not limite_usuario[
        "permitido"
    ]:
        return limite_usuario

    limite_ip = consumir_rate_limit(
        group="pagar-suscripcion-ip",
        raw_key=(
            f"{tenant_id}:"
            f"{ip}"
        ),
        limite=(
            settings
            .FOODBACK_SUBSCRIPTION_PAYMENT_IP_LIMIT
        ),
        ventana_segundos=(
            settings
            .FOODBACK_SUBSCRIPTION_PAYMENT_WINDOW_SECONDS
        ),
        bloqueo_segundos=(
            settings
            .FOODBACK_SUBSCRIPTION_PAYMENT_BLOCK_SECONDS
        ),
    )

    if not limite_ip[
        "permitido"
    ]:
        return limite_ip

    return None


def _verificar_rate_limit_accion_pedido_pendiente(
    request,
    tenant,
):
    """
    Anti-flood compartido para acciones sobre
    pedidos pendientes de pago.

    Protege cancelar/ocultar mediante:
    - navegador/sesión
    - IP
    """

    cliente_id = obtener_id_seguridad_cliente(
        request
    )

    ip = obtener_ip_cliente(
        request
    )

    tenant_id = (
        tenant.id
        if tenant
        else "none"
    )

    limite_cliente = consumir_rate_limit(
        group="pending-order-action-client",
        raw_key=(
            f"{tenant_id}:"
            f"{cliente_id}"
        ),
        limite=(
            settings
            .FOODBACK_PENDING_ORDER_ACTION_SESSION_LIMIT
        ),
        ventana_segundos=(
            settings
            .FOODBACK_PENDING_ORDER_ACTION_WINDOW_SECONDS
        ),
        bloqueo_segundos=(
            settings
            .FOODBACK_PENDING_ORDER_ACTION_BLOCK_SECONDS
        ),
    )

    if not limite_cliente[
        "permitido"
    ]:
        return limite_cliente

    limite_ip = consumir_rate_limit(
        group="pending-order-action-ip",
        raw_key=(
            f"{tenant_id}:"
            f"{ip}"
        ),
        limite=(
            settings
            .FOODBACK_PENDING_ORDER_ACTION_IP_LIMIT
        ),
        ventana_segundos=(
            settings
            .FOODBACK_PENDING_ORDER_ACTION_WINDOW_SECONDS
        ),
        bloqueo_segundos=(
            settings
            .FOODBACK_PENDING_ORDER_ACTION_BLOCK_SECONDS
        ),
    )

    if not limite_ip[
        "permitido"
    ]:
        return limite_ip

    return None


# --- VISTAS DE PAGO WOMPI (CLIENTES PAGANDO PEDIDOS) ---


def _cliente_pago_token_hash(request):
    """
    Identificador anónimo estable para este navegador.

    No usamos teléfono, IP, session_key ni datos de tarjeta.
    El navegador recibe solamente un UUID aleatorio guardado
    en su sesión; en la base únicamente almacenamos su hash.
    """

    token = request.session.get(
        'wompi_cliente_token'
    )

    if not token:
        token = uuid.uuid4().hex

        request.session[
            'wompi_cliente_token'
        ] = token

        request.session.modified = True

    return hashlib.sha256(
        token.encode('utf-8')
    ).hexdigest()


def _registrar_evento_pago(
    pago,
    *,
    categoria,
    origen,
    codigo='',
    mensaje='',
    cuenta_para_cliente=False,
    cuenta_para_global=False,
    clave_evento=None,
    metadata=None,
):
    """
    Registra telemetría financiera sanitizada.

    Si clave_evento ya existe, devolvemos el evento anterior
    y NO incrementamos futuros contadores otra vez.
    """
    
    tenant = None

    if pago:
        if pago.tenant_id:
            tenant = pago.tenant

        elif (
            pago.pedido
            and pago.pedido.sucursal
        ):
            tenant = (
                pago.pedido
                .sucursal
                .tenant
            )

        elif (
            pago.configuracion_negocio
            and
            pago.configuracion_negocio.sucursal
        ):
            tenant = (
                pago.configuracion_negocio
                .sucursal
                .tenant
            )

    defaults = {
        'pago':
            pago,

        'pedido':
            pago.pedido
            if pago
            else None,

        'configuracion_negocio':
            (
                pago.configuracion_negocio
                if pago
                else None
            ),

        'cliente_token_hash':
            (
                pago.cliente_token_hash
                if pago
                else ''
            ),

        'categoria':
            categoria,

        'origen':
            origen,

        'codigo':
            codigo,

        'mensaje':
            mensaje,

        'cuenta_para_cliente':
            cuenta_para_cliente,

        'cuenta_para_global':
            cuenta_para_global,

        'metadata':
            metadata or {},
        
        "tenant": tenant,
    }

    if clave_evento:
        evento, creado = (
            EventoPagoWompi.objects
            .get_or_create(
                clave_evento=clave_evento,
                defaults=defaults,
            )
        )

        return evento, creado

    evento = EventoPagoWompi.objects.create(
        **defaults
    )

    return evento, True

def _estado_bloqueo_tarjeta_cliente(request):
    """
    Determina si este navegador debe tener TARJETA
    temporalmente suspendida por demasiados rechazos.

    No usa teléfono, nombre, IP ni datos bancarios.

    Devuelve:
        {
            'bloqueada': bool,
            'cantidad_fallos': int,
            'limite': int,
            'bloqueado_hasta': datetime | None,
        }
    """

    try:
        limite = int(
            config(
                'WOMPI_USER_FAILURE_LIMIT',
                default=5,
            )
        )
    except (TypeError, ValueError):
        limite = 5

    try:
        ventana_minutos = int(
            config(
                'WOMPI_USER_FAILURE_WINDOW_MINUTES',
                default=30,
            )
        )
    except (TypeError, ValueError):
        ventana_minutos = 30

    try:
        cooldown_minutos = int(
            config(
                'WOMPI_USER_COOLDOWN_MINUTES',
                default=30,
            )
        )
    except (TypeError, ValueError):
        cooldown_minutos = 30

    limite = max(limite, 1)
    ventana_minutos = max(
        ventana_minutos,
        1,
    )
    cooldown_minutos = max(
        cooldown_minutos,
        1,
    )

    cliente_token_hash = (
        _cliente_pago_token_hash(
            request
        )
    )

    ahora = timezone.now()

    inicio_ventana = (
        ahora
        - timedelta(
            minutes=ventana_minutos
        )
    )

    eventos = list(
        EventoPagoWompi.objects
        .filter(
            tenant=request.tenant,
            cliente_token_hash=cliente_token_hash,
            cuenta_para_cliente=True,
            fecha__gte=inicio_ventana,
        )
        .order_by('-fecha')[
            :limite
        ]
    )

    cantidad_fallos = len(
        eventos
    )

    if cantidad_fallos < limite:
        return {
            'bloqueada': False,
            'cantidad_fallos':
                cantidad_fallos,
            'limite':
                limite,
            'bloqueado_hasta':
                None,
        }

    ultimo_fallo = eventos[0]

    bloqueado_hasta = (
        ultimo_fallo.fecha
        + timedelta(
            minutes=cooldown_minutos
        )
    )

    bloqueada = (
        bloqueado_hasta > ahora
    )

    return {
        'bloqueada':
            bloqueada,

        'cantidad_fallos':
            cantidad_fallos,

        'limite':
            limite,

        'bloqueado_hasta':
            (
                bloqueado_hasta
                if bloqueada
                else None
            ),
    }
    
    
def _estado_bloqueo_pasarela_tenant(
    tenant,
):
    """
    Circuit breaker Wompi aislado por Tenant.

    Los errores de un restaurante pueden bloquear
    temporalmente tarjeta para todas sus sucursales,
    pero nunca afectan a otro Tenant.
    """

    estado_vacio = {
        "bloqueada": False,
        "bloqueo_manual": False,
        "bloqueado_hasta": None,
        "codigo_motivo": "",
        "errores_recientes": 0,
        "clientes_distintos": 0,
    }

    if not tenant:
        return {
            **estado_vacio,
            "bloqueada": True,
            "codigo_motivo": "TENANT_NO_RESUELTO",
        }

    try:
        limite_errores = int(
            config(
                "WOMPI_GLOBAL_FAILURE_LIMIT",
                default=5,
            )
        )
    except (TypeError, ValueError):
        limite_errores = 5

    try:
        minimo_clientes = int(
            config(
                "WOMPI_GLOBAL_MIN_DISTINCT_CLIENTS",
                default=3,
            )
        )
    except (TypeError, ValueError):
        minimo_clientes = 3

    try:
        ventana_minutos = int(
            config(
                "WOMPI_GLOBAL_FAILURE_WINDOW_MINUTES",
                default=10,
            )
        )
    except (TypeError, ValueError):
        ventana_minutos = 10

    try:
        cooldown_minutos = int(
            config(
                "WOMPI_GLOBAL_COOLDOWN_MINUTES",
                default=15,
            )
        )
    except (TypeError, ValueError):
        cooldown_minutos = 15

    limite_errores = max(
        limite_errores,
        1,
    )

    minimo_clientes = max(
        minimo_clientes,
        1,
    )

    ventana_minutos = max(
        ventana_minutos,
        1,
    )

    cooldown_minutos = max(
        cooldown_minutos,
        1,
    )

    estado = (
        EstadoPasarelaPago.objects
        .filter(
            tenant=tenant
        )
        .first()
    )

    if (
        estado
        and estado.bloqueo_manual
    ):
        return {
            "bloqueada": True,
            "bloqueo_manual": True,
            "bloqueado_hasta":
                estado.bloqueado_hasta,
            "codigo_motivo":
                estado.codigo_motivo,
            "errores_recientes": 0,
            "clientes_distintos": 0,
        }

    ahora = timezone.now()

    if (
        estado
        and estado.bloqueado_hasta
        and estado.bloqueado_hasta > ahora
    ):
        return {
            "bloqueada": True,
            "bloqueo_manual": False,
            "bloqueado_hasta":
                estado.bloqueado_hasta,
            "codigo_motivo":
                estado.codigo_motivo,
            "errores_recientes": 0,
            "clientes_distintos": 0,
        }

    inicio_ventana = (
        ahora
        - timedelta(
            minutes=ventana_minutos
        )
    )

    errores = (
        EventoPagoWompi.objects
        .filter(
            tenant=tenant,
            cuenta_para_global=True,
            categoria="ERROR_TECNICO",
            fecha__gte=inicio_ventana,
        )
    )

    cantidad_errores = (
        errores.count()
    )

    clientes_distintos = (
        errores
        .exclude(
            cliente_token_hash=""
        )
        .values(
            "cliente_token_hash"
        )
        .distinct()
        .count()
    )

    debe_bloquear = (
        cantidad_errores
        >= limite_errores
        and
        clientes_distintos
        >= minimo_clientes
    )

    if debe_bloquear:
        bloqueado_hasta = (
            ahora
            + timedelta(
                minutes=cooldown_minutos
            )
        )

        estado, _ = (
            EstadoPasarelaPago.objects
            .update_or_create(
                tenant=tenant,
                defaults={
                    "bloqueo_manual":
                        False,

                    "bloqueado_hasta":
                        bloqueado_hasta,

                    "codigo_motivo":
                        "WOMPI_TECHNICAL_FAILURES",

                    "motivo": (
                        f"{cantidad_errores} errores "
                        f"técnicos recientes de "
                        f"{clientes_distintos} clientes."
                    ),
                },
            )
        )

        return {
            "bloqueada": True,
            "bloqueo_manual": False,
            "bloqueado_hasta":
                bloqueado_hasta,
            "codigo_motivo":
                estado.codigo_motivo,
            "errores_recientes":
                cantidad_errores,
            "clientes_distintos":
                clientes_distintos,
        }

    return {
        "bloqueada": False,
        "bloqueo_manual": False,
        "bloqueado_hasta": None,
        "codigo_motivo": "",
        "errores_recientes":
            cantidad_errores,
        "clientes_distintos":
            clientes_distintos,
    }


def _decimal_monto(value, default='0.00'):
    try:
        return Decimal(str(value)).quantize(Decimal('0.01'))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal(default)


def _wompi_headers_seguridad():
    return {
        'User-Agent': 'FoodBack/1.0 (+https://foodbacksv.com)',
        'Accept': 'application/json'
    }


def _wompi_tipo_desde_referencia(referencia):
    ref = str(referencia or '').upper()
    if ref.startswith('SUBS-'):
        return 'SUSCRIPCION'
    if ref.startswith('ORDEN-'):
        return 'PEDIDO'
    return None


def _wompi_app_id(tipo_pago=None, referencia=None):
    tipo = (tipo_pago or _wompi_tipo_desde_referencia(referencia) or '').upper()

    if tipo == 'SUSCRIPCION':
        return config(
            'WOMPI_PLATFORM_APP_ID',
            default=config('WOMPI_APP_ID', default='')
        )

    if tipo == 'PEDIDO':
        return config(
            'WOMPI_RESTAURANT_APP_ID',
            default=config('WOMPI_APP_ID', default='')
        )

    return config(
        'WOMPI_RESTAURANT_APP_ID',
        default=config('WOMPI_PLATFORM_APP_ID',
                       default=config('WOMPI_APP_ID', default=''))
    )


def _wompi_api_secret(tipo_pago=None, referencia=None):
    tipo = (tipo_pago or _wompi_tipo_desde_referencia(referencia) or '').upper()

    if tipo == 'SUSCRIPCION':
        return config(
            'WOMPI_PLATFORM_API_SECRET',
            default=config('WOMPI_API_SECRET', default='')
        )

    if tipo == 'PEDIDO':
        return config(
            'WOMPI_RESTAURANT_API_SECRET',
            default=config('WOMPI_API_SECRET', default='')
        )

    return config(
        'WOMPI_RESTAURANT_API_SECRET',
        default=config('WOMPI_PLATFORM_API_SECRET',
                       default=config('WOMPI_API_SECRET', default=''))
    )


def _wompi_posibles_secrets(referencia=None, tipo_pago=None):
    """
    Si no sabemos de qué cuenta vino un webhook, probamos solo los secrets
    configurados. Esto no aprueba nada por sí solo: también debe existir la
    referencia local y la transacción debe venir aprobada.
    """
    secrets = []

    preferido = _wompi_api_secret(tipo_pago=tipo_pago, referencia=referencia)
    if preferido:
        secrets.append(preferido)

    for key in ['WOMPI_RESTAURANT_API_SECRET', 'WOMPI_PLATFORM_API_SECRET', 'WOMPI_API_SECRET']:
        value = config(key, default='')
        if value and value not in secrets:
            secrets.append(value)

    return secrets


def _wompi_obtener_token(tipo_pago=None, referencia=None):
    client_id = _wompi_app_id(tipo_pago=tipo_pago, referencia=referencia)
    client_secret = _wompi_api_secret(
        tipo_pago=tipo_pago, referencia=referencia)
    auth_url = config('WOMPI_AUTH_URL',
                      default='https://id.wompi.sv/connect/token')

    if not client_id or not client_secret:
        raise Exception(
            'Faltan credenciales Wompi. Revisa WOMPI_RESTAURANT_* para pedidos '
            'y WOMPI_PLATFORM_* para suscripción.'
        )

    payload = {
        'grant_type': 'client_credentials',
        'client_id': client_id,
        'client_secret': client_secret,
        'audience': 'wompi_api'
    }

    response = requests.post(auth_url, data=payload,
                             headers=_wompi_headers_seguridad(), timeout=25)
    if response.status_code != 200:
        raise Exception(
            f'Wompi Auth error {response.status_code}: {response.text[:300]}')

    token = response.json().get('access_token')
    if not token:
        raise Exception('Wompi no devolvió access_token.')

    return token


def _wompi_crear_referencia(prefijo, objeto_id):
    return f"{prefijo}-{objeto_id}-{uuid.uuid4().hex[:12].upper()}"


def _base_url(request):
    return request.build_absolute_uri('/')[:-1]


def _wompi_crear_enlace_pago(request, *, referencia, monto, nombre_producto, redirect_url, webhook_url, tipo_pago='PEDIDO'):
    access_token = _wompi_obtener_token(
        tipo_pago=tipo_pago, referencia=referencia)
    api_url = config(
        'WOMPI_API_URL', default='https://api.wompi.sv/EnlacePago')

    headers = {
        'Authorization': f'Bearer {access_token}',
        'Content-Type': 'application/json',
        **_wompi_headers_seguridad(),
    }

    # Correo de notificación separado:
    # - pedidos: normalmente restaurante
    # - suscripción: plataforma/FoodBack
    if str(tipo_pago).upper() == 'SUSCRIPCION' or str(referencia).upper().startswith('SUBS-'):
        email_notificacion = config(
            'WOMPI_PLATFORM_NOTIFICATION_EMAIL',
            default=config('WOMPI_NOTIFICATION_EMAIL', default='')
        )
    else:
        email_notificacion = config(
            'WOMPI_RESTAURANT_NOTIFICATION_EMAIL',
            default=config('WOMPI_NOTIFICATION_EMAIL', default='')
        )

    payload = {
        'identificadorEnlaceComercio': referencia,
        'monto': float(_decimal_monto(monto)),
        'nombreProducto': nombre_producto,
        'formaPago': {
            'permitirTarjetaCreditoDebido': True,
            'permitirPagoConPuntoAgricola': True,
            'permitirPagoEnCuotasAgricola': False,
        },
        'configuracion': {
            'urlRedirect': redirect_url,
            'urlRetorno': redirect_url,
            'urlWebhook': webhook_url,
            'esMontoEditable': False,
            'esCantidadEditable': False,
            'emailsNotificacion': email_notificacion,
            'notificarTransaccionCliente': True,
        },
        'limitesDeUso': {
            'cantidadMaximaPagosExitosos': 1,
            'cantidadMaximaPagosFallidos': 5,
        }
    }

    response = requests.post(api_url, json=payload,
                             headers=headers, timeout=30)
    if response.status_code != 200:
        raise Exception(
            f'Wompi EnlacePago error {response.status_code}: {response.text[:500]}')

    return response.json(), payload


def _valor_bool_wompi(value):
    if isinstance(value, bool):
        return value

    if value is None:
        return False

    return str(value).strip().lower() in [
        'true',
        '1',
        'si',
        'sí',
        'aprobada',
        'approved',
        'exitosaaprobada',
    ]


def _redirect_wompi_aprobado(params):
    """
    En Enlace de Pago Wompi puede regresar sin esAprobada.
    Si el hash ya fue validado y existe idTransaccion, lo tomamos como
    confirmación aprobada, salvo que Wompi envíe explícitamente esAprobada=false.
    """
    estado_explicito = (
        params.get('esAprobada') or
        params.get('EsAprobada') or
        params.get('aprobada') or
        params.get('approved')
    )

    if estado_explicito not in [None, '']:
        return _valor_bool_wompi(estado_explicito)

    id_transaccion = params.get('idTransaccion') or params.get('IdTransaccion')
    return bool(str(id_transaccion or '').strip())


def _extraer_transaccion_wompi(data):
    if not isinstance(data, dict):
        return {}
    return data.get('transaccion') or data.get('transaction') or data.get('data') or data


def _get_any(dic, *keys, default=None):
    if not isinstance(dic, dict):
        return default
    for key in keys:
        if key in dic and dic.get(key) not in [None, '']:
            return dic.get(key)
    return default


def _calcular_hmac_sha256(texto_o_bytes, *, tipo_pago=None, referencia=None, secret=None):
    secret = secret or _wompi_api_secret(
        tipo_pago=tipo_pago, referencia=referencia)
    if not secret:
        return ''

    if isinstance(texto_o_bytes, bytes):
        msg = texto_o_bytes
    else:
        msg = str(texto_o_bytes).encode('utf-8')

    return hmac.new(secret.encode('utf-8'), msg, hashlib.sha256).hexdigest()


def _hash_wompi_coincide(texto_o_bytes, hash_recibido, *, referencia=None, tipo_pago=None):
    if not hash_recibido:
        return False

    recibido = str(hash_recibido).lower()

    for secret in _wompi_posibles_secrets(referencia=referencia, tipo_pago=tipo_pago):
        calculado = _calcular_hmac_sha256(texto_o_bytes, secret=secret)
        if calculado and hmac.compare_digest(calculado.lower(), recibido):
            return True

    return False


def _validar_hash_redirect_wompi(params, *, referencia=None, tipo_pago=None):
    """
    Valida la URL de retorno de Wompi.

    Para Enlace de Pago, Wompi valida concatenando:
    identificadorEnlaceComercio + idTransaccion + idEnlace + monto

    Si no viene hash, NO confirmamos pago por URL; esperamos webhook.
    """
    hash_recibido = params.get('hash') or params.get(
        'wompi_hash') or params.get('Hash')
    if not hash_recibido:
        return False

    identificador = (
        params.get('identificadorEnlaceComercio') or
        params.get('IdentificadorEnlaceComercio') or
        params.get('referencia') or
        referencia or
        ''
    )

    cadena_enlace_pago = ''.join([
        str(identificador),
        str(params.get('idTransaccion', '') or params.get('IdTransaccion', '')),
        str(params.get('idEnlace', '') or params.get('IdEnlace', '')),
        str(params.get('monto', '') or params.get('Monto', '')),
    ])

    if _hash_wompi_coincide(
        cadena_enlace_pago,
        hash_recibido,
        referencia=referencia or identificador,
        tipo_pago=tipo_pago
    ):
        return True

    # Respaldo para otros flujos de transacción si Wompi envía el formato viejo.
    cadena_transaccion = ''.join([
        str(params.get('idTransaccion', '') or params.get('IdTransaccion', '')),
        str(params.get('monto', '') or params.get('Monto', '')),
        str(params.get('esReal', '') or params.get('EsReal', '')),
        str(params.get('formaPago', '') or params.get('FormaPago', '')),
        str(params.get('esAprobada', '') or params.get('EsAprobada', '')),
        str(params.get('codigoAutorizacion', '')
            or params.get('CodigoAutorizacion', '')),
        str(params.get('mensaje', '') or params.get('Mensaje', '')),
    ])

    return _hash_wompi_coincide(
        cadena_transaccion,
        hash_recibido,
        referencia=referencia or identificador,
        tipo_pago=tipo_pago
    )


def _validar_hash_webhook_wompi(request, *, referencia=None, tipo_pago=None, raw_body=None):
    hash_recibido = (
        request.headers.get('wompi_hash') or
        request.headers.get('Wompi-Hash') or
        request.headers.get('Wompi_Hash') or
        request.META.get('HTTP_WOMPI_HASH')
    )

    if not hash_recibido:
        return False

    body = raw_body if raw_body is not None else request.body
    return _hash_wompi_coincide(body, hash_recibido, referencia=referencia, tipo_pago=tipo_pago)


def _monto_coincide(monto_esperado, monto_recibido):
    """
    Un pago solamente puede confirmarse si Wompi informa
    explícitamente el monto y este coincide exactamente
    con el registrado localmente.
    """

    if monto_recibido in [None, '']:
        return False

    return (
        _decimal_monto(monto_esperado)
        == _decimal_monto(monto_recibido)
    )


def _renovar_suscripcion_30_dias(
    suscripcion,
):
    if not suscripcion:
        raise ValueError(
            "No existe una suscripción para renovar."
        )

    hoy = date.today()

    base_fecha = (
        suscripcion.fecha_vencimiento
    )

    if (
        not base_fecha
        or base_fecha < hoy
    ):
        base_fecha = hoy

    suscripcion.fecha_vencimiento = (
        base_fecha
        + timedelta(days=30)
    )

    suscripcion.estado = (
        SuscripcionTenant.Estado.ACTIVA
    )

    suscripcion.gracia_hasta = None

    suscripcion.save(
        update_fields=[
            "fecha_vencimiento",
            "estado",
            "gracia_hasta",
            "actualizado_en",
        ]
    )

    return suscripcion


def _tenant_pago_suscripcion(
    pago,
):
    """
    Resuelve el Tenant de una suscripción.

    PagoWompi.tenant es la fuente nueva.

    configuracion_negocio queda solamente
    como compatibilidad con registros legacy.
    """

    return pago.tenant


@transaction.atomic
def _procesar_pago_wompi_aprobado(
    referencia,
    *,
    id_transaccion=None,
    monto=None,
    raw_payload=None,
    origen="WEBHOOK",
):
    """
    Confirma un pago Wompi únicamente si:

    - existe el PagoWompi local;
    - existe idTransaccion;
    - el monto coincide exactamente;
    - la transacción no fue usada por otro pago;
    - los replays son idempotentes;
    - una suscripción pertenece a un Tenant válido.
    """

    pago = (
        PagoWompi.objects
        .select_for_update()
        .filter(referencia=referencia)
        .first()
    )

    # Compatibilidad temporal con referencias antiguas de pedidos.
    if (
        not pago
        and referencia
        and referencia.startswith("ORDEN-")
    ):
        try:
            pedido_id = int(
                referencia.split("-")[1]
            )

            pedido = (
                Pedido.objects
                .select_for_update()
                .get(id=pedido_id)
            )

            pago = (
                PagoWompi.objects
                .select_for_update()
                .filter(pedido=pedido)
                .order_by("-fecha_creacion")
                .first()
            )
        except Exception:
            pago = None

    # Compatibilidad temporal con referencias antiguas de suscripción.
    if (
        not pago
        and referencia
        and referencia.startswith("SUBS-")
    ):
        pago = (
            PagoWompi.objects
            .select_for_update()
            .filter(referencia=referencia)
            .order_by("-fecha_creacion")
            .first()
        )

    if not pago:
        return (
            False,
            "No existe registro local para esa referencia.",
        )

    # -------------------------------------------------
    # ID DE TRANSACCIÓN OBLIGATORIO
    # -------------------------------------------------

    id_transaccion = str(
        id_transaccion or ""
    ).strip()

    if not id_transaccion:
        if pago.estado != "APROBADO":
            pago.ultimo_error = (
                "Wompi indicó aprobación sin "
                "idTransaccion."
            )

            if raw_payload is not None:
                if origen == "REDIRECT":
                    pago.raw_redirect = raw_payload
                else:
                    pago.raw_webhook = raw_payload

            pago.save()

        return (
            False,
            "La transacción no contiene "
            "un identificador válido.",
        )

    # -------------------------------------------------
    # REPLAY DEL MISMO PAGO
    # -------------------------------------------------

    if pago.estado == "APROBADO":
        if (
            pago.id_transaccion
            and str(pago.id_transaccion) == id_transaccion
        ):
            return (
                True,
                "Pago ya estaba aprobado.",
            )

        return (
            False,
            "El pago ya fue aprobado con otra transacción.",
        )

    # -------------------------------------------------
    # SUSCRIPCIÓN: RESOLVER Y BLOQUEAR EL TENANT CORRECTO
    # -------------------------------------------------

    suscripcion_pago = None

    if pago.tipo == "SUSCRIPCION":
        tenant_pago = _tenant_pago_suscripcion(
            pago
        )

        if not tenant_pago:
            return (
                False,
                "El pago de suscripción no tiene un Tenant válido.",
            )

        suscripcion_pago = (
            SuscripcionTenant.objects
            .select_for_update()
            .filter(tenant=tenant_pago)
            .first()
        )

        if not suscripcion_pago:
            return (
                False,
                "No existe una suscripción para ese Tenant.",
            )

    # -------------------------------------------------
    # MONTO OBLIGATORIO Y EXACTO
    # -------------------------------------------------

    if not _monto_coincide(
        pago.monto,
        monto,
    ):
        pago.estado = "ERROR"
        pago.ultimo_error = (
            f"Monto no coincide. "
            f"Esperado {pago.monto}, "
            f"recibido {monto}"
        )

        if raw_payload is not None:
            if origen == "REDIRECT":
                pago.raw_redirect = raw_payload
            else:
                pago.raw_webhook = raw_payload

        pago.save()

        return (
            False,
            pago.ultimo_error,
        )

    # -------------------------------------------------
    # UNA TRANSACCIÓN NO PUEDE PAGAR DOS COSAS
    # -------------------------------------------------

    transaccion_usada = (
        PagoWompi.objects
        .select_for_update()
        .filter(id_transaccion=id_transaccion)
        .exclude(pk=pago.pk)
        .exists()
    )

    if transaccion_usada:
        pago.ultimo_error = (
            "El idTransaccion recibido ya fue "
            "utilizado por otro pago."
        )

        if raw_payload is not None:
            if origen == "REDIRECT":
                pago.raw_redirect = raw_payload
            else:
                pago.raw_webhook = raw_payload

        pago.save()

        return (
            False,
            pago.ultimo_error,
        )

    # Defensa adicional mientras Pedido conserva
    # wompi_id_transaccion por compatibilidad histórica.
    pedido_con_misma_transaccion = (
        Pedido.objects
        .filter(
            wompi_id_transaccion=id_transaccion
        )
    )

    if pago.pedido_id:
        pedido_con_misma_transaccion = (
            pedido_con_misma_transaccion.exclude(
                id=pago.pedido_id
            )
        )

    if pedido_con_misma_transaccion.exists():
        pago.ultimo_error = (
            "La transacción ya está asociada "
            "a otro pedido."
        )
        pago.save()

        return (
            False,
            pago.ultimo_error,
        )

    # -------------------------------------------------
    # APROBACIÓN
    # -------------------------------------------------

    pago.estado = "APROBADO"
    pago.es_aprobada = True
    pago.id_transaccion = id_transaccion

    if raw_payload is not None:
        if origen == "REDIRECT":
            pago.raw_redirect = raw_payload
        else:
            pago.raw_webhook = raw_payload

    pago.fecha_aprobacion = timezone.now()
    pago.ultimo_error = ""
    pago.save()

    if (
        pago.tipo == "PEDIDO"
        and pago.pedido
    ):
        pedido = pago.pedido
        pedido.estado = "RECIBIDO"
        pedido.pago_verificado = True
        pedido.wompi_id_transaccion = (
            id_transaccion
        )
        pedido.fecha_pago_verificado = (
            timezone.now()
        )
        pedido.save()

        return (
            True,
            f"Pedido #{pedido.id} confirmado.",
        )

    if pago.tipo == "SUSCRIPCION":
        _renovar_suscripcion_30_dias(
            suscripcion_pago
        )

        return (
            True,
            "Suscripción renovada.",
        )

    return (
        True,
        "Pago aprobado.",
    )



def _iniciar_pago_wompi_pedido(request, pedido):
    """
    Crea o reutiliza de forma idempotente el intento de pago
    correspondiente a un pedido.

    Nunca crea un segundo PagoWompi si ya existe uno pendiente.
    """

    if pedido.metodo_pago != 'TARJETA':
        return redirect(
            'order_tracker',
            tracking_token=pedido.tracking_token
        )

    if pedido.pago_verificado or pedido.estado != 'PENDIENTE':
        return redirect(
            'order_tracker',
            tracking_token=pedido.tracking_token
        )
    
        # FB-COMP-001:
    # Normaliza pedidos pendientes antiguos que
    # todavía pudieran conservar el recargo histórico.
    total_sin_recargo = Decimal(
        str(
            pedido.total_productos
            or '0.00'
        )
    ).quantize(
        Decimal('0.01')
    )

    if (
        pedido.comision_plataforma
        != Decimal('0.00')
        or
        pedido.total_final
        != total_sin_recargo
    ):
        pedido.comision_plataforma = (
            Decimal('0.00')
        )

        pedido.total_final = (
            total_sin_recargo
        )

        pedido.save(
            update_fields=[
                'comision_plataforma',
                'total_final',
            ]
        )
    
    estado_global = (
        _estado_bloqueo_pasarela_tenant(
            request.tenant
        )
    )

    if estado_global[
        'bloqueada'
    ]:
        messages.warning(
            request,
            (
                'El pago con tarjeta está '
                'temporalmente no disponible. '
                'Puedes pagar en efectivo '
                'mientras solucionamos el servicio.'
            )
        )

        return redirect(
            'checkout'
        )
        
    estado_bloqueo = (
    _estado_bloqueo_tarjeta_cliente(
            request
        )   
    )

    if estado_bloqueo[
        'bloqueada'
    ]:
        messages.warning(
            request,
            (
                'Tarjeta temporalmente no disponible '
                'debido a varios intentos rechazados. '
                'Puedes pagar en efectivo o intentar '
                'nuevamente más tarde.'
            )
        )

        return redirect(
            'checkout'
        )    

    try:
        base = _base_url(request)
        webhook_url = f"{base}/wompi-webhook/"

        tenant_pago = (
            pedido.sucursal.tenant
            if pedido.sucursal_id
            else getattr(request, "tenant", None)
        )

        if not tenant_pago:
            messages.error(
                request,
                "No fue posible determinar el restaurante del pedido.",
            )
            return redirect("checkout")

        # Buscamos primero un intento reutilizable
        # del mismo pedido y del mismo Tenant.
        pago = (
            PagoWompi.objects
            .filter(
                pedido=pedido,
                tenant=tenant_pago,
                estado__in=[
                    "CREADO",
                    "PENDIENTE",
                ],
            )
            .order_by(
                "-fecha_creacion"
            )
            .first()
        )
        
        if (
            pago
            and not pago.cliente_token_hash
        ):
            pago.cliente_token_hash = (
                _cliente_pago_token_hash(
                    request
                )
            )

            pago.save(
                update_fields=[
                    'cliente_token_hash'
                ]
            )

        # Si ya tiene enlace, no contactamos Wompi otra vez.
        if pago and pago.url_enlace:
            return redirect(
                pago.url_enlace
            )

        # Si existe un PagoWompi pendiente sin URL,
        # reutilizamos su referencia y su registro.
        if pago:
            referencia = pago.referencia

            if pedido.wompi_referencia != referencia:
                pedido.wompi_referencia = referencia
                pedido.save(
                    update_fields=[
                        'wompi_referencia'
                    ]
                )

        else:
            # Si no existe un intento activo reutilizable,
            # SIEMPRE generamos una referencia nueva.
            #
            # Esto cubre:
            # - RECHAZADO
            # - CANCELADO
            # - ERROR
            # - cualquier intento anterior terminado

            referencia = (
                _wompi_crear_referencia(
                    'ORDEN',
                    pedido.id
                )
            )

            pedido.wompi_referencia = referencia

            pedido.save(
                update_fields=[
                    'wompi_referencia'
                ]
            )

            pago = PagoWompi.objects.create(
                tipo='PEDIDO',
                tenant=tenant_pago,
                pedido=pedido,
                referencia=referencia,
                monto=pedido.total_final,
                estado='PENDIENTE',
                cliente_token_hash=(
                    _cliente_pago_token_hash(
                        request
                    )
                ),
            )

        redirect_url = (
            f"{base}/wompi-respuesta/"
            f"?ref={referencia}"
        )

        data, raw_payload = (
            _wompi_crear_enlace_pago(
                request,
                tipo_pago='PEDIDO',
                referencia=referencia,
                monto=pedido.total_final,
                nombre_producto=(
                    f"Pedido #{pedido.id}"
                ),
                redirect_url=redirect_url,
                webhook_url=webhook_url,
            )
        )

        url_enlace = (
            data.get('urlEnlace')
            or data.get('UrlEnlace')
        )

        id_enlace = (
            data.get('idEnlace')
            or data.get('IdEnlace')
        )

        if not url_enlace:
            pago.estado = 'ERROR'
            pago.ultimo_error = (
                'Wompi no devolvió urlEnlace.'
            )
            pago.raw_creacion = {
                'request': raw_payload,
                'response': data,
            }
            pago.save()

            messages.error(
                request,
                'No se pudo generar el enlace de pago.'
            )

            return redirect('checkout')

        pago.url_enlace = url_enlace
        pago.id_enlace = str(
            id_enlace or ''
        )
        pago.raw_creacion = {
            'request': raw_payload,
            'response': data,
        }
        pago.save()

        pedido.wompi_id_enlace = str(
            id_enlace or ''
        )
        pedido.wompi_url_enlace = url_enlace

        pedido.save(
            update_fields=[
                'wompi_id_enlace',
                'wompi_url_enlace',
            ]
        )

        return redirect(
            url_enlace
        )

    except Exception as e:
        if 'pago' in locals() and pago:

            if pago.estado != 'APROBADO':
                pago.estado = 'ERROR'
                pago.es_aprobada = False
                pago.ultimo_error = (
                    'No fue posible iniciar '
                    'el enlace de pago con Wompi.'
                )

                pago.save(
                    update_fields=[
                        'estado',
                        'es_aprobada',
                        'ultimo_error',
                    ]
                )
            
            
            _registrar_evento_pago(
                pago,
                categoria='ERROR_TECNICO',
                origen='INICIO',
                codigo='WOMPI_CONNECTION_ERROR',
                mensaje=(
                    'No fue posible iniciar '
                    'el enlace de pago.'
                ),
                cuenta_para_cliente=False,
                cuenta_para_global=True,
                clave_evento=(
                    f"ERROR-INICIO:"
                    f"{pago.id}:"
                    f"{uuid.uuid4().hex}"
                ),
            )
        print(
            f'Error Wompi pedido: {e}'
        )

        messages.error(
            request,
            'No pudimos conectar con la pasarela '
            'de pago. Intenta de nuevo o elige efectivo.'
        )

        return redirect('checkout')


@require_POST
@require_POST
def pagar_wompi_view(request, tracking_token):
    tenant = getattr(
        request,
        "tenant",
        None,
    )

    rate_limit_pago = (
        _verificar_rate_limit_pagar_wompi(
            request,
            tenant,
        )
    )

    if rate_limit_pago:
        response = HttpResponseBadRequest(
            "Se realizaron demasiados "
            "intentos de iniciar el pago. "
            "Espera unos minutos "
            "e inténtalo nuevamente."
        )

        response.status_code = 429

        response[
            "Retry-After"
        ] = str(
            max(
                int(
                    rate_limit_pago[
                        "retry_after"
                    ]
                ),
                1,
            )
        )

        return response

    pedido = get_object_or_404(
        Pedido,
        tracking_token=tracking_token,
        sucursal=request.sucursal,
    )

    return _iniciar_pago_wompi_pedido(
        request,
        pedido
    )


@require_GET
def wompi_respuesta_view(request):
    referencia = (
        request.GET.get("ref")
        or request.GET.get("pedido_ref")
    )

    id_transaccion = (
        request.GET.get(
            "idTransaccion",
            "",
        )
        .strip()
    )

    if not referencia:
        messages.error(
            request,
            "No se recibió la referencia del pago.",
        )

        return redirect(
            "menu"
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

    if not tenant or not sucursal:
        return HttpResponseForbidden(
            "No hay un restaurante activo."
        )

    pago = (
        PagoWompi.objects
        .filter(
            referencia=referencia,
            tipo="PEDIDO",
            tenant=tenant,
            pedido__sucursal=sucursal,
        )
        .select_related(
            "pedido"
        )
        .first()
    )

    pedido = (
        pago.pedido
        if pago
        else None
    )

    if not pago or not pedido:
        messages.error(
            request,
            (
                "No encontramos el pedido "
                "relacionado al pago."
            ),
        )

        return redirect(
            "menu"
        )

    pago.raw_redirect = dict(
        request.GET.items()
    )

    pago.save(
        update_fields=[
            "raw_redirect",
            "fecha_actualizacion",
        ]
    )

    request.session[
        "ultimo_pedido_id"
    ] = pedido.id

    historial = (
        request.session.get(
            "historial_pedidos",
            [],
        )
    )

    if pedido.id not in historial:
        historial.append(
            pedido.id
        )

    request.session[
        "historial_pedidos"
    ] = historial

    request.session.modified = True

    if (
        _validar_hash_redirect_wompi(
            request.GET,
            referencia=pago.referencia,
            tipo_pago=pago.tipo,
        )
        and
        _redirect_wompi_aprobado(
            request.GET
        )
    ):
        ok, msg = (
            _procesar_pago_wompi_aprobado(
                pago.referencia,
                id_transaccion=(
                    id_transaccion
                ),
                monto=request.GET.get(
                    "monto"
                ),
                raw_payload=dict(
                    request.GET.items()
                ),
                origen="REDIRECT",
            )
        )

        if ok:
            messages.success(
                request,
                (
                    "Pago confirmado. "
                    "Tu pedido fue recibido."
                ),
            )
        else:
            messages.warning(
                request,
                f"Pago en revisión: {msg}",
            )

    elif (
        pago.estado == "APROBADO"
        or pedido.pago_verificado
    ):
        messages.success(
            request,
            (
                "Pago confirmado. "
                "Tu pedido fue recibido."
            ),
        )

    else:
        messages.info(
            request,
            (
                "Estamos verificando tu pago. "
                "Tu pedido se activará "
                "automáticamente al confirmarse."
            ),
        )

    return redirect(
        "order_tracker",
        tracking_token=(
            pedido.tracking_token
        ),
    )

@require_safe
def pedido_exito_view(request, tracking_token):
    pedido = get_object_or_404(
        Pedido,
        tracking_token=tracking_token,
        sucursal=request.sucursal
    )

    return redirect(
        'order_tracker',
        tracking_token=pedido.tracking_token
    )

# --- HELPERS PARA POLLING OPTIMIZADO ---


def _iso_datetime(dt):
    if not dt:
        return "none"
    return dt.isoformat()


def _parse_last_update(value):
    if not value or value == "none":
        return None

    dt = parse_datetime(value)

    if not dt:
        return None

    if timezone.is_naive(dt):
        dt = timezone.make_aware(dt)

    return dt


def _ultimo_cambio_pedidos(
    sucursal,
):
    """
    Devuelve la última modificación de pedidos
    exclusivamente para una sucursal.

    No existe fallback global.
    """

    if not sucursal:
        return None

    return (
        Pedido.objects
        .filter(
            sucursal=sucursal
        )
        .aggregate(
            ultimo=Max(
                "actualizado_en"
            )
        )["ultimo"]
    )


def _query_detalles_optimizada():
    return DetallePedido.objects.select_related(
        'producto',
        'opcion'
    ).prefetch_related(
        'extras'
    )


def _query_pedidos_admin(sucursal):
    """
    Pedidos operativos visibles para una sucursal.

    Fail closed: sin sucursal no devuelve ningún pedido.
    """

    if not sucursal:
        return Pedido.objects.none()

    return (
        Pedido.objects
        .filter(
            sucursal=sucursal
        )
        .exclude(
            estado__in=[
                "ENTREGADO",
                "CANCELADO",
            ]
        )
        .select_related(
            "cliente",
            "repartidor",
        )
        .prefetch_related(
            Prefetch(
                "detalles",
                queryset=(
                    _query_detalles_optimizada()
                ),
            )
        )
        .order_by("-id")
    )


def _contexto_admin_pedidos(sucursal):
    pedidos = list(
        _query_pedidos_admin(
            sucursal
        )
    )

    pedidos_nuevos = [
        pedido
        for pedido in pedidos
        if (
            pedido.estado == "RECIBIDO"
            or not pedido.estado
        )
    ]

    pedidos_cocina = [
        pedido
        for pedido in pedidos
        if pedido.estado == "COCINA"
    ]

    pedidos_ruta = [
        pedido
        for pedido in pedidos
        if pedido.estado == "RUTA"
    ]

    pedidos_problema = [
        pedido
        for pedido in pedidos
        if pedido.estado == "PROBLEMA"
    ]

    return {
        "pedidos": pedidos,
        "pedidos_nuevos": (
            pedidos_nuevos
        ),
        "pedidos_cocina": (
            pedidos_cocina
        ),
        "pedidos_ruta": pedidos_ruta,
        "pedidos_problema": (
            pedidos_problema
        ),
    }


def _query_pedidos_delivery_base(sucursal):
    """
    Query base para Delivery limitada a una sucursal.

    Fail closed:
    si no existe una sucursal válida, no devuelve pedidos.
    """

    if not sucursal:
        return Pedido.objects.none()

    return (
        Pedido.objects
        .filter(
            sucursal=sucursal
        )
        .select_related(
            "cliente",
            "repartidor",
        )
        .prefetch_related(
            Prefetch(
                "detalles",
                queryset=(
                    _query_detalles_optimizada()
                ),
            )
        )
    )


def _contexto_delivery_pedidos(
    user,
    sucursal,
):
    queryset = (
        _query_pedidos_delivery_base(
            sucursal
        )
    )

    disponibles = list(
        queryset.filter(
            estado="RUTA",
            repartidor=None,
        ).order_by("id")
    )

    mis_pedidos = list(
        queryset.filter(
            estado="RUTA",
            repartidor=user,
        ).order_by("id")
    )

    return {
        "disponibles": disponibles,
        "mis_pedidos": mis_pedidos,
    }
    
def _pedidos_sesion(request):
    ids = request.session.get(
        'historial_pedidos',
        []
    )

    # Limpieza defensiva: solo enteros,
    # sin duplicados y conservando orden.
    ids_limpios = []

    for pedido_id in ids:
        try:
            pedido_id = int(pedido_id)
        except (TypeError, ValueError):
            continue

        if pedido_id not in ids_limpios:
            ids_limpios.append(pedido_id)

    if ids_limpios != ids:
        request.session[
            'historial_pedidos'
        ] = ids_limpios

        request.session.modified = True

    return ids_limpios

# --- DASHBOARDS PROTEGIDOS ---



@require_POST
@login_required(
    login_url="login_custom"
)
@require_tenant_roles(
    Membership.ROLE_OWNER,
    Membership.ROLE_MANAGER,
)
def cambiar_sucursal_view(request):
    tenant = getattr(
        request,
        "tenant",
        None,
    )

    if not tenant:
        return HttpResponseForbidden(
            "No hay un Tenant activo."
        )

    public_id = request.POST.get(
        "sucursal_public_id",
        ""
    ).strip()

    if not public_id:
        return HttpResponseBadRequest(
            "Sucursal no especificada."
        )

    sucursales_permitidas = (
        _sucursales_accesibles_usuario(
            request,
            tenant,
        )
    )

    sucursal = (
        sucursales_permitidas
        .filter(
            public_id=public_id
        )
        .first()
    )

    if not sucursal:
        raise PermissionDenied(
            (
                "No tienes autorización "
                "para utilizar esa sucursal."
            )
        )

    request.session[
        "sucursal_activa_public_id"
    ] = str(
        sucursal.public_id
    )

    request.session.modified = True

    return redirect(
        "dashboard_admin"
    )

@never_cache
@login_required(login_url="login_custom")
@require_branch_access
def dashboard_admin_view(request):
    _limpiar_pedidos_pendientes_vencidos()

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

    if not tenant:
        return HttpResponseForbidden(
            "No hay un Tenant activo."
        )

    # Seguridad: el dashboard operativo nunca debe caer
    # accidentalmente a consultas globales.
    if not sucursal:
        return HttpResponseForbidden(
            "No hay una sucursal activa."
        )

    suscripcion = (
        SuscripcionTenant.objects
        .filter(tenant=tenant)
        .first()
    )

    dias_restantes = 0

    if (
        suscripcion
        and suscripcion.fecha_vencimiento
    ):
        fecha_limite = (
            suscripcion.gracia_hasta
            if (
                suscripcion.estado
                == SuscripcionTenant.Estado.GRACIA
                and suscripcion.gracia_hasta
            )
            else suscripcion.fecha_vencimiento
        )

        dias_restantes = (
            fecha_limite
            - date.today()
        ).days

    bloqueado = not suscripcion_activa(
        tenant
    )

    if (
        bloqueado
        and request.method == "POST"
    ):
        messages.error(
            request,
            "⛔ Acción denegada. Suscripción vencida.",
        )

    elif request.method == "POST":
        pedido = get_object_or_404(
            Pedido,
            id=request.POST.get("pedido_id"),
            sucursal=sucursal,
        )

        accion = request.POST.get(
            "accion"
        )

        if accion == "cocina":
            pedido.estado = "COCINA"
            messages.success(
                request,
                f"Orden #{pedido.id} enviada a Cocina 🔥",
            )

        elif accion == "ruta":
            pedido.estado = "RUTA"
            messages.success(
                request,
                f"Orden #{pedido.id} lista para Ruta 🛵",
            )

        elif accion == "reintentar":
            pedido.estado = "RUTA"
            messages.info(
                request,
                f"Reintentando Orden #{pedido.id} 🔄",
            )

        elif accion == "cancelar":
            pedido.estado = "CANCELADO"
            messages.error(
                request,
                f"Orden #{pedido.id} cancelada ❌",
            )

        else:
            messages.error(
                request,
                "Acción no válida.",
            )
            return redirect(
                "dashboard_admin"
            )

        pedido.save()
        return redirect(
            "dashboard_admin"
        )

    context = _contexto_admin_pedidos(
        sucursal
    )
    
    #---
    sucursales_disponibles = list(
        _sucursales_accesibles_usuario(
            request,
            tenant,
        ).order_by(
            "nombre"
        )
    )

    context.update({
        "dias_restantes": dias_restantes,
        "last_update": _iso_datetime(
            _ultimo_cambio_pedidos(
                sucursal
            )
        ),
        "tenant": tenant,
        "sucursal": sucursal,
        "sucursales_disponibles":
            sucursales_disponibles,
        "suscripcion": suscripcion,
    })

    return render(
        request,
        "pedidos/dashboard_admin.html",
        context,
    )


@require_safe
@never_cache
@login_required(login_url='login_custom')
@require_branch_access
def api_dashboard_admin_sync(request):
    sucursal = getattr(
        request,
        "sucursal",
        None,
    )

    if not sucursal:
        return JsonResponse(
            {
                "detail": (
                    "No hay una "
                    "sucursal activa."
                )
            },
            status=403,
        )

    ultimo_servidor = (
        _ultimo_cambio_pedidos(
            sucursal
        )
    )

    ultimo_cliente_raw = (
        request.GET.get(
            "last_update",
            "none",
        )
    )

    ultimo_cliente = (
        _parse_last_update(
            ultimo_cliente_raw
        )
    )
    
    if (
        ultimo_cliente_raw != "none"
        and ultimo_cliente is None
    ):
        return JsonResponse(
            {
                "detail": (
                    "last_update inválido."
                )
            },
            status=400,
        )

    if (
        ultimo_servidor is None
        and ultimo_cliente_raw == "none"
    ):
        return JsonResponse({
            "changed": False,
            "last_update": "none",
        })

    if (
        ultimo_servidor
        and ultimo_cliente
        and (
            ultimo_servidor
            <= ultimo_cliente
        )
    ):
        return JsonResponse({
            "changed": False,
            "last_update": (
                _iso_datetime(
                    ultimo_servidor
                )
            ),
        })

    context = (
        _contexto_admin_pedidos(
            sucursal
        )
    )

    html_nuevos = render_to_string(
        "pedidos/partials/admin_nuevos.html",
        context,
        request=request,
    )

    html_cocina = render_to_string(
        "pedidos/partials/admin_cocina.html",
        context,
        request=request,
    )

    html_ruta = render_to_string(
        "pedidos/partials/admin_ruta.html",
        context,
        request=request,
    )

    html_problema = render_to_string(
        "pedidos/partials/admin_problema.html",
        context,
        request=request,
    )

    return JsonResponse({
        "changed": True,
        "last_update": (
            _iso_datetime(
                ultimo_servidor
            )
        ),
        "nuevos_count": len(
            context[
                "pedidos_nuevos"
            ]
        ),
        "html": {
            "pills-nuevos": (
                html_nuevos
            ),
            "pills-cocina": (
                html_cocina
            ),
            "pills-ruta": (
                html_ruta
            ),
            "pills-problema": (
                html_problema
            ),
        },
    })
    
    
    
def _parse_hora_configuracion(
    valor,
    *,
    requerida=False,
):
    """
    Convierte una hora enviada por formulario
    en un objeto time válido.

    Solo acepta el formato HH:MM que utiliza
    el input type="time" de FoodBack.
    """

    valor = (
        valor or ""
    ).strip()

    if not valor:
        if requerida:
            raise ValueError(
                "Hora requerida."
            )

        return None

    try:
        return datetime.strptime(
            valor,
            "%H:%M",
        ).time()

    except ValueError as exc:
        raise ValueError(
            "Hora inválida."
        ) from exc


@never_cache
@login_required(login_url='login_custom')
@require_branch_access
def admin_settings_view(request):
    if not suscripcion_activa(
        request.tenant
    ):
        messages.error(
            request,
            "⛔ Acceso denegado a Configuración. Suscripción vencida."
        )
        return redirect("dashboard_admin")

    sucursal = getattr(
        request,
        "sucursal",
        None,
    )

    if not sucursal:
        messages.error(
            request,
            "No fue posible determinar la sucursal activa."
        )
        return redirect("dashboard_admin")

    config_negocio, _ = (
        ConfiguracionNegocio.objects
        .get_or_create(
            sucursal=sucursal
        )
    )

    if request.method == "POST":
        tipo_accion = request.POST.get(
            "tipo_accion"
        )

        if tipo_accion == "global":
            try:
                hora_apertura = (
                    _parse_hora_configuracion(
                        request.POST.get(
                            "hora_apertura"
                        ),
                        requerida=True,
                    )
                )

                hora_cierre = (
                    _parse_hora_configuracion(
                        request.POST.get(
                            "hora_cierre"
                        ),
                        requerida=True,
                    )
                )

            except ValueError:
                messages.error(
                    request,
                    (
                        "Las horas de apertura "
                        "y cierre no son válidas."
                    ),
                )

                return redirect(
                    "admin_settings"
                )

            config_negocio.hora_apertura = (
                hora_apertura
            )

            config_negocio.hora_cierre = (
                hora_cierre
            )

            config_negocio.mensaje_cierre = (
                request.POST.get(
                    "mensaje_cierre",
                    ""
                )
            )

            dias_map = [
                "lunes",
                "martes",
                "miercoles",
                "jueves",
                "viernes",
                "sabado",
                "domingo",
            ]

            for dia in dias_map:
                valor = (
                    request.POST.get(
                        f"{dia}_abierto"
                    )
                    == "on"
                )

                setattr(
                    config_negocio,
                    f"{dia}_abierto",
                    valor,
                )

            config_negocio.save()

            messages.success(
                request,
                "Configuración de la sucursal actualizada ⚙️"
            )

            return redirect(
                "admin_settings"
            )

        elif tipo_accion == "dia_especifico":
            fecha_str = request.POST.get(
                "fecha_target"
            )

            if not fecha_str:
                messages.error(
                    request,
                    "Error: No se recibió la fecha."
                )

                return redirect(
                    "admin_settings"
                )

            try:
                fecha_dt = datetime.strptime(
                    fecha_str,
                    "%Y-%m-%d",
                ).date()

            except ValueError:
                messages.error(
                    request,
                    "La fecha recibida no es válida."
                )

                return redirect(
                    "admin_settings"
                )

            abierto = (
                request.POST.get(
                    "estado_dia"
                )
                == "on"
            )

            h_ap_raw = (
                request.POST.get(
                    "hora_apertura_dia"
                )
            )

            h_ci_raw = (
                request.POST.get(
                    "hora_cierre_dia"
                )
            )

            motivo = (
                request.POST.get(
                    "motivo",
                    ""
                )
                or ""
            ).strip()

            # DiaEspecial.motivo tiene
            # max_length=100 en el modelo.
            if len(motivo) > 100:
                messages.error(
                    request,
                    (
                        "El motivo no puede "
                        "superar 100 caracteres."
                    ),
                )

                return redirect(
                    "admin_settings"
                )

            try:
                hora_apertura = (
                    _parse_hora_configuracion(
                        h_ap_raw
                    )
                )

                hora_cierre = (
                    _parse_hora_configuracion(
                        h_ci_raw
                    )
                )

            except ValueError:
                messages.error(
                    request,
                    (
                        "El horario especial "
                        "no es válido."
                    ),
                )

                return redirect(
                    "admin_settings"
                )

            # IMPORTANTE:
            # solo después de validar TODO
            # tocamos la base de datos.
            excepcion, _ = (
                DiaEspecial.objects
                .get_or_create(
                    sucursal=sucursal,
                    fecha=fecha_dt,
                )
            )

            excepcion.abierto = (
                abierto
            )

            excepcion.hora_apertura = (
                hora_apertura
            )

            excepcion.hora_cierre = (
                hora_cierre
            )

            excepcion.motivo = (
                motivo or None
            )

            excepcion.save()

            messages.success(
                request,
                (
                    f"Horario para "
                    f"{fecha_str} "
                    f"actualizado ✅"
                ),
            )

            return redirect(
                "admin_settings"
            )

    agenda = []

    hoy = date.today()

    nombres_dias = [
        "Lunes",
        "Martes",
        "Miércoles",
        "Jueves",
        "Viernes",
        "Sábado",
        "Domingo",
    ]

    defaults_globales = [
        config_negocio.lunes_abierto,
        config_negocio.martes_abierto,
        config_negocio.miercoles_abierto,
        config_negocio.jueves_abierto,
        config_negocio.viernes_abierto,
        config_negocio.sabado_abierto,
        config_negocio.domingo_abierto,
    ]

    excepciones = {
        excepcion.fecha: excepcion
        for excepcion in (
            DiaEspecial.objects
            .filter(
                sucursal=sucursal,
                fecha__range=(
                    hoy,
                    hoy + timedelta(days=6),
                ),
            )
        )
    }

    for i in range(7):
        fecha_iter = (
            hoy
            + timedelta(days=i)
        )

        idx = fecha_iter.weekday()

        es_abierto = (
            defaults_globales[idx]
        )

        h_inicio = (
            config_negocio.hora_apertura
        )

        h_fin = (
            config_negocio.hora_cierre
        )

        motivo = ""
        es_excepcion = False
        id_db = None

        excepcion = (
            excepciones.get(
                fecha_iter
            )
        )

        if excepcion:
            es_abierto = (
                excepcion.abierto
            )

            motivo = (
                excepcion.motivo
                or ""
            )

            if excepcion.hora_apertura:
                h_inicio = (
                    excepcion.hora_apertura
                )

            if excepcion.hora_cierre:
                h_fin = (
                    excepcion.hora_cierre
                )

            es_excepcion = True
            id_db = excepcion.id

        agenda.append({
            "fecha_str": (
                fecha_iter.strftime(
                    "%Y-%m-%d"
                )
            ),
            "nombre_dia": (
                "HOY"
                if i == 0
                else (
                    "MAÑANA"
                    if i == 1
                    else nombres_dias[idx]
                )
            ),
            "fecha_fmt": (
                fecha_iter.strftime(
                    "%d/%m"
                )
            ),
            "abierto": es_abierto,
            "hora_ini": h_inicio,
            "hora_fin": h_fin,
            "motivo": motivo,
            "id_db": id_db,
            "es_excepcion": (
                es_excepcion
            ),
        })

    return render(
        request,
        "pedidos/admin_settings.html",
        {
            "config": config_negocio,
            "agenda": agenda,
            "sucursal": sucursal,
        },
    )


@login_required(login_url='login_custom')
@require_branch_access
@require_POST
def eliminar_excepcion_view(
    request,
    excepcion_id,
):
    if not suscripcion_activa(
        request.tenant
    ):
        messages.error(
            request,
            "Acción denegada. Suscripción vencida."
        )

        return redirect(
            "dashboard_admin"
        )

    sucursal = getattr(
        request,
        "sucursal",
        None,
    )

    if not sucursal:
        messages.error(
            request,
            "No fue posible determinar la sucursal activa."
        )

        return redirect(
            "dashboard_admin"
        )

    excepcion = get_object_or_404(
        DiaEspecial,
        id=excepcion_id,
        sucursal=sucursal,
    )

    excepcion.delete()

    messages.info(
        request,
        "Excepción eliminada 🗑️"
    )

    return redirect(
        "admin_settings"
    )


@never_cache
@login_required(login_url='login_custom')
@require_delivery_assignment
def dashboard_delivery_view(request):
    _limpiar_pedidos_pendientes_vencidos()

    sucursal = getattr(
        request,
        "sucursal",
        None,
    )

    if not sucursal:
        return HttpResponseForbidden(
            "No hay una sucursal activa."
        )

    if not suscripcion_activa(
        request.tenant
    ):
        return render(
            request,
            "pedidos/suspendido.html",
        )

    if request.method == "POST":
        accion = request.POST.get(
            "accion"
        )

        acciones_validas = {
            "tomar",
            "entregado",
            "soltar",
            "problema",
        }

        if accion not in acciones_validas:
            return HttpResponseBadRequest(
                "Acción no válida."
            )

        pedido_id_raw = request.POST.get(
            "pedido_id"
        )

        try:
            pedido_id = int(
                pedido_id_raw
            )

            if pedido_id <= 0:
                raise ValueError

        except (
            TypeError,
            ValueError,
        ):
            return HttpResponseBadRequest(
                "Pedido no válido."
            )

        with transaction.atomic():
            pedido = get_object_or_404(
                Pedido.objects.select_for_update(),
                id=pedido_id,
                sucursal=sucursal,
            )

            if accion == "tomar":
                if (
                    pedido.estado == "RUTA"
                    and pedido.repartidor is None
                ):
                    pedido.repartidor = (
                        request.user
                    )

                    pedido.save()

                    messages.success(
                        request,
                        (
                            f"Pedido #{pedido.id} "
                            "tomado 🛵"
                        ),
                    )

                else:
                    messages.warning(
                        request,
                        (
                            "Ese pedido ya fue "
                            "tomado por otro "
                            "repartidor."
                        ),
                    )

            elif accion == "entregado":
                if (
                    pedido.repartidor
                    == request.user
                ):
                    pedido.estado = (
                        "ENTREGADO"
                    )

                    pedido.save()

                    messages.success(
                        request,
                        (
                            f"Pedido #{pedido.id} "
                            "entregado ✅"
                        ),
                    )

                else:
                    messages.error(
                        request,
                        (
                            "No puedes entregar "
                            "un pedido que no está "
                            "en tu mochila."
                        ),
                    )

            elif accion == "soltar":
                if (
                    pedido.estado == "RUTA"
                    and pedido.repartidor
                    == request.user
                ):
                    pedido.repartidor = None

                    pedido.save()

                    messages.info(
                        request,
                        (
                            f"Pedido #{pedido.id} "
                            "devuelto a disponibles 🔄"
                        ),
                    )

                else:
                    messages.error(
                        request,
                        (
                            "No puedes quitar de "
                            "tu mochila un pedido "
                            "que no tienes asignado."
                        ),
                    )

            elif accion == "problema":
                if (
                    pedido.repartidor
                    == request.user
                ):
                    pedido.estado = (
                        "PROBLEMA"
                    )

                    pedido.repartidor = None

                    pedido.save()

                    messages.warning(
                        request,
                        (
                            "Problema reportado "
                            f"en pedido #{pedido.id}"
                        ),
                    )

                else:
                    messages.error(
                        request,
                        (
                            "No puedes reportar "
                            "un pedido que no está "
                            "en tu mochila."
                        ),
                    )

        return redirect(
            "dashboard_delivery"
        )

    context = (
        _contexto_delivery_pedidos(
            request.user,
            sucursal,
        )
    )

    context.update({
        "GOOGLE_MAPS_API_KEY": config(
            "GOOGLE_MAPS_API_KEY",
            default="",
        ),
        "last_update": _iso_datetime(
            _ultimo_cambio_pedidos(
                sucursal
            )
        ),
        "sucursal": sucursal,
    })

    return render(
        request,
        "pedidos/dashboard_delivery.html",
        context,
    )

@require_safe
@never_cache
@login_required(login_url='login_custom')
@require_delivery_assignment
def api_delivery_sync(request):
    sucursal = getattr(
        request,
        "sucursal",
        None,
    )

    if not sucursal:
        return JsonResponse(
            {
                "detail": (
                    "No hay una "
                    "sucursal activa."
                )
            },
            status=403,
        )

    ultimo_servidor = (
        _ultimo_cambio_pedidos(
            sucursal
        )
    )

    ultimo_cliente_raw = (
        request.GET.get(
            "last_update",
            "none",
        )
    )

    ultimo_cliente = (
        _parse_last_update(
            ultimo_cliente_raw
        )
    )
    
    if (
        ultimo_cliente_raw != "none"
        and ultimo_cliente is None
    ):
        return JsonResponse(
            {
                "detail": (
                    "last_update inválido."
                )
            },
            status=400,
        )

    if (
        ultimo_servidor is None
        and ultimo_cliente_raw == "none"
    ):
        return JsonResponse({
            "changed": False,
            "last_update": "none",
        })

    if (
        ultimo_servidor
        and ultimo_cliente
        and (
            ultimo_servidor
            <= ultimo_cliente
        )
    ):
        return JsonResponse({
            "changed": False,
            "last_update": (
                _iso_datetime(
                    ultimo_servidor
                )
            ),
        })

    context = (
        _contexto_delivery_pedidos(
            request.user,
            sucursal,
        )
    )

    html_mochila = render_to_string(
        "pedidos/partials/delivery_mochila.html",
        context,
        request=request,
    )

    html_pool = render_to_string(
        "pedidos/partials/delivery_pool.html",
        context,
        request=request,
    )

    return JsonResponse({
        "changed": True,
        "last_update": (
            _iso_datetime(
                ultimo_servidor
            )
        ),
        "pool_count": len(
            context["disponibles"]
        ),
        "html": {
            "zona-mochila": (
                html_mochila
            ),
            "zona-pool": (
                html_pool
            ),
        },
    })


@require_safe
def order_tracker_view(
    request,
    tracking_token
):
    pedido = get_object_or_404(
        Pedido,
        tracking_token=tracking_token,
        sucursal=request.sucursal
    )

    historial = request.session.get(
        'historial_pedidos',
        []
    )

    ultimo_pedido_id = (
        request.session.get(
            'ultimo_pedido_id'
        )
    )

    pertenece_a_sesion = (
        pedido.id == ultimo_pedido_id
        or
        pedido.id in historial
    )

    pago_actual = None

    if pedido.metodo_pago == 'TARJETA':
        pago_actual = (
            pedido.pagos_wompi
            .order_by(
                '-fecha_creacion'
            )
            .first()
        )

    puede_retomar_pago = (
        pertenece_a_sesion
        and
        pedido.metodo_pago == 'TARJETA'
        and
        pedido.estado == 'PENDIENTE'
        and
        not pedido.pago_verificado
    )
    
    pago_tiene_enlace = (
        pago_actual
        and (
            bool(pago_actual.url_enlace)
            or
            bool(pago_actual.id_enlace)
        )
    )

    puede_cancelar_pedido = (
        puede_retomar_pago
        and
        not pago_tiene_enlace
    )
    
    puede_ocultar_pedido = (
        puede_retomar_pago
        and
        bool(pago_tiene_enlace)
    )

    return render(
        request,
        'pedidos/order_tracker.html',
        {
            'pedido':
                pedido,

            'pago_actual':
                pago_actual,

            'puede_retomar_pago':
                puede_retomar_pago,
            
            'puede_cancelar_pedido':
                puede_cancelar_pedido,
            
            'puede_ocultar_pedido':
                puede_ocultar_pedido,
        }
    )


@require_POST
def retomar_pago_view(
    request,
    tracking_token
):
    tenant = getattr(
        request,
        "tenant",
        None,
    )

    rate_limit_pago = (
        _verificar_rate_limit_retomar_pago(
            request,
            tenant,
        )
    )

    if rate_limit_pago:
        response = HttpResponseBadRequest(
            "Se realizaron demasiados "
            "intentos de retomar el pago. "
            "Espera unos minutos "
            "e inténtalo nuevamente."
        )

        response.status_code = 429

        response[
            "Retry-After"
        ] = str(
            max(
                int(
                    rate_limit_pago[
                        "retry_after"
                    ]
                ),
                1,
            )
        )

        return response

    pedido = get_object_or_404(
        Pedido,
        tracking_token=tracking_token,
        metodo_pago='TARJETA',
        estado='PENDIENTE',
        pago_verificado=False,
        sucursal=request.sucursal,
    )

    historial = request.session.get(
        'historial_pedidos',
        []
    )

    ultimo_pedido_id = (
        request.session.get(
            'ultimo_pedido_id'
        )
    )

    pertenece_a_sesion = (
        pedido.id == ultimo_pedido_id
        or
        pedido.id in historial
    )

    if not pertenece_a_sesion:
        raise PermissionDenied(
            "No puedes retomar este pedido."
        )

    request.session[
        'ultimo_pedido_id'
    ] = pedido.id

    if pedido.id not in historial:
        historial.append(
            pedido.id
        )

    request.session[
        'historial_pedidos'
    ] = historial

    request.session.modified = True

    return redirect(
        'checkout'
    )
    

@require_POST
def cancelar_pedido_pendiente_view(
    request,
    tracking_token
):
    
    tenant = getattr(
        request,
        "tenant",
        None,
    )

    rate_limit_accion = (
        _verificar_rate_limit_accion_pedido_pendiente(
            request,
            tenant,
        )
    )

    if rate_limit_accion:
        response = HttpResponseBadRequest(
            "Se realizaron demasiados "
            "intentos sobre pedidos pendientes. "
            "Espera unos minutos "
            "e inténtalo nuevamente."
        )

        response.status_code = 429

        response[
            "Retry-After"
        ] = str(
            max(
                int(
                    rate_limit_accion[
                        "retry_after"
                    ]
                ),
                1,
            )
        )

        return response
    with transaction.atomic():
    
        pedido = get_object_or_404(
            Pedido.objects.select_for_update(),
            tracking_token=tracking_token,
            metodo_pago='TARJETA',
            estado='PENDIENTE',
            pago_verificado=False,
            sucursal=request.sucursal,
        )

        historial = request.session.get(
            'historial_pedidos',
            []
        )

        ultimo_pedido_id = (
            request.session.get(
                'ultimo_pedido_id'
            )
        )

        pertenece_a_sesion = (
            pedido.id == ultimo_pedido_id
            or
            pedido.id in historial
        )

        if not pertenece_a_sesion:
            raise PermissionDenied(
                "No puedes cancelar este pedido."
            )

        pago_actual = (
            PagoWompi.objects
            .select_for_update()
            .filter(
                pedido=pedido
            )
            .order_by(
                '-fecha_creacion'
            )
            .first()
        )

        # Si Wompi llegó a generar un enlace,
        # asumimos conservadoramente que aún
        # podría ser pagable.
        enlace_potencialmente_activo = (
            pago_actual
            and (
                bool(pago_actual.url_enlace)
                or
                bool(pago_actual.id_enlace)
            )
        )

        if enlace_potencialmente_activo:
            messages.warning(
                request,
                (
                    'Este pedido todavía tiene un enlace '
                    'de pago que podría estar activo. '
                    'Por seguridad no podemos cancelarlo '
                    'automáticamente todavía.'
                )
            )

            return redirect(
                'order_tracker',
                tracking_token=(
                    pedido.tracking_token
                )
            )

        pedido.estado = 'CANCELADO'

        pedido.save(
            update_fields=[
                'estado',
            ]
        )

        if pago_actual:
            _registrar_evento_pago(
                pago_actual,
                categoria='INFO',
                origen='SISTEMA',
                codigo='CLIENT_ORDER_CANCELLED',
                mensaje=(
                    'El cliente canceló un pedido '
                    'pendiente sin enlace Wompi activo.'
                ),
                cuenta_para_cliente=False,
                cuenta_para_global=False,
                clave_evento=(
                    f"CANCELACION-PEDIDO:"
                    f"{pedido.id}"
                ),
            )

        # Conservamos historial para que aparezca
        # como pedido pasado.
        if pedido.id not in historial:
            historial.append(
                pedido.id
            )

        request.session[
            'historial_pedidos'
        ] = historial

        if (
            request.session.get(
                'ultimo_pedido_id'
            )
            == pedido.id
        ):
            del request.session[
                'ultimo_pedido_id'
            ]

        request.session.modified = True

        messages.info(
            request,
            'El pedido pendiente fue cancelado.'
        )

        return redirect(
            'menu'
        )


@require_POST
def ocultar_pedido_pendiente_view(
    request,
    tracking_token
):
    """
    Oculta de la UX un pedido pendiente cuyo
    enlace Wompi podría seguir activo.

    NO cancela el Pedido.
    NO cancela PagoWompi.
    NO modifica el enlace financiero.
    """
    
    tenant = getattr(
        request,
        "tenant",
        None,
    )

    rate_limit_accion = (
        _verificar_rate_limit_accion_pedido_pendiente(
            request,
            tenant,
        )
    )

    if rate_limit_accion:
        response = HttpResponseBadRequest(
            "Se realizaron demasiados "
            "intentos sobre pedidos pendientes. "
            "Espera unos minutos "
            "e inténtalo nuevamente."
        )

        response.status_code = 429

        response[
            "Retry-After"
        ] = str(
            max(
                int(
                    rate_limit_accion[
                        "retry_after"
                    ]
                ),
                1,
            )
        )

        return response
    with transaction.atomic():
    

        pedido = get_object_or_404(
            Pedido.objects.select_for_update(),
            tracking_token=tracking_token,
            metodo_pago='TARJETA',
            estado='PENDIENTE',
            pago_verificado=False,
            sucursal=request.sucursal,
        )

        historial = request.session.get(
            'historial_pedidos',
            []
        )

        ultimo_pedido_id = (
            request.session.get(
                'ultimo_pedido_id'
            )
        )

        pertenece_a_sesion = (
            pedido.id == ultimo_pedido_id
            or
            pedido.id in historial
        )

        if not pertenece_a_sesion:
            raise PermissionDenied(
                "No puedes ocultar este pedido."
            )

        pago_actual = (
            PagoWompi.objects
            .select_for_update()
            .filter(
                pedido=pedido
            )
            .order_by(
                '-fecha_creacion'
            )
            .first()
        )

        enlace_potencialmente_activo = (
            pago_actual
            and (
                bool(pago_actual.url_enlace)
                or
                bool(pago_actual.id_enlace)
            )
        )

        if not enlace_potencialmente_activo:
            messages.info(
                request,
                (
                    'Este pedido no tiene un enlace '
                    'Wompi activo. Puedes cancelarlo '
                    'normalmente.'
                )
            )

            return redirect(
                'order_tracker',
                tracking_token=(
                    pedido.tracking_token
                )
            )

        ocultos = request.session.get(
            'pedidos_pendientes_ocultos',
            []
        )

        if pedido.id not in ocultos:
            ocultos.append(
                pedido.id
            )

        request.session[
            'pedidos_pendientes_ocultos'
        ] = ocultos

        if (
            request.session.get(
                'ultimo_pedido_id'
            )
            == pedido.id
        ):
            del request.session[
                'ultimo_pedido_id'
            ]

        request.session.modified = True

        _registrar_evento_pago(
            pago_actual,
            categoria='INFO',
            origen='SISTEMA',
            codigo='CLIENT_PENDING_ORDER_HIDDEN',
            mensaje=(
                'El cliente ocultó de su interfaz '
                'un pedido pendiente con enlace '
                'Wompi potencialmente activo.'
            ),
            cuenta_para_cliente=False,
            cuenta_para_global=False,
            clave_evento=(
                f"OCULTAR-PENDIENTE:"
                f"{pedido.id}"
            ),
        )

        messages.info(
            request,
            (
                'El pedido dejó de mostrarse como '
                'pedido en curso.'
            )
        )

        return redirect(
            'menu'
        )

@require_safe
def api_order_status(
    request,
    tracking_token
):
    ultimo_cliente_raw = (
        request.GET.get(
            "last_update",
            "none",
        )
    )

    ultimo_cliente = (
        _parse_last_update(
            ultimo_cliente_raw
        )
    )

    # Un last_update manipulado o malformado
    # se rechaza antes incluso de buscar el Pedido.
    if (
        ultimo_cliente_raw != "none"
        and ultimo_cliente is None
    ):
        return JsonResponse(
            {
                "status": "error",
                "msg": (
                    "last_update inválido"
                ),
            },
            status=400,
        )

    try:
        pedido = (
            Pedido.objects
            .only(
                "id",
                "estado",
                "actualizado_en",
            )
            .get(
                tracking_token=(
                    tracking_token
                ),
                sucursal=request.sucursal,
            )
        )

    except Pedido.DoesNotExist:
        return JsonResponse(
            {
                "status": "error",
                "msg": (
                    "Pedido no encontrado"
                ),
            },
            status=404,
        )

    ultimo_servidor = (
        pedido.actualizado_en
    )

    # El cliente ya posee esta versión.
    # No reenviamos información que no cambió.
    if (
        ultimo_cliente
        and ultimo_servidor
        <= ultimo_cliente
    ):
        return JsonResponse(
            {
                "status": "ok",
                "changed": False,
                "last_update": (
                    _iso_datetime(
                        ultimo_servidor
                    )
                ),
            }
        )

    return JsonResponse(
        {
            "status": "ok",
            "changed": True,
            "estado_codigo":
                pedido.estado,
            "estado_texto":
                pedido.get_estado_display(),
            "last_update": (
                _iso_datetime(
                    ultimo_servidor
                )
            ),
        }
    )


@require_safe
@never_cache
@login_required(login_url='login_custom')
@require_branch_access
def dashboard_metrics_view(request):
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
        return HttpResponseForbidden(
            "No hay una sucursal activa."
        )

    if not suscripcion_activa(
        tenant
    ):
        messages.error(
            request,
            (
                "⛔ Acceso denegado a Finanzas. "
                "Suscripción vencida."
            ),
        )

        return redirect(
            "dashboard_admin"
        )

    hoy = datetime.now().date()

    pedidos_validos_hoy = (
        Pedido.objects
        .filter(
            sucursal=sucursal,
            fecha_creacion__date=hoy,
        )
        .exclude(
            estado__in=[
                "CANCELADO",
                "PENDIENTE",
            ]
        )
    )

    resumen = (
        pedidos_validos_hoy
        .aggregate(
            total_general=Sum(
                "total_final"
            ),
            total_efectivo=Sum(
                "total_final",
                filter=Q(
                    metodo_pago="EFECTIVO"
                ),
            ),
            total_wompi=Sum(
                "total_final",
                filter=Q(
                    metodo_pago="TARJETA"
                ),
            ),
        )
    )

    total_ventas_hoy = (
        resumen["total_general"]
        or 0
    )

    dinero_en_caja = (
        resumen["total_efectivo"]
        or 0
    )

    dinero_banco = (
        resumen["total_wompi"]
        or 0
    )

    cantidad_pedidos_hoy = (
        pedidos_validos_hoy.count()
    )

    ticket_promedio = (
        total_ventas_hoy
        / cantidad_pedidos_hoy
        if cantidad_pedidos_hoy > 0
        else 0
    )

    fechas_grafica = []
    montos_grafica = []

    nombres_dias = [
        "Lun",
        "Mar",
        "Mié",
        "Jue",
        "Vie",
        "Sáb",
        "Dom",
    ]

    for i in range(
        6,
        -1,
        -1,
    ):
        fecha = (
            hoy
            - timedelta(days=i)
        )

        venta_dia = (
            Pedido.objects
            .filter(
                sucursal=sucursal,
                fecha_creacion__date=fecha,
            )
            .exclude(
                estado__in=[
                    "CANCELADO",
                    "PENDIENTE",
                ]
            )
            .aggregate(
                total=Sum(
                    "total_final"
                )
            )["total"]
            or 0
        )

        fechas_grafica.append(
            (
                f"{nombres_dias[fecha.weekday()]} "
                f"{fecha.day}"
            )
        )

        montos_grafica.append(
            float(
                venta_dia
            )
        )

    top_productos = (
        DetallePedido.objects
        .filter(
            pedido__sucursal=sucursal,
            pedido__estado__in=[
                "RECIBIDO",
                "COCINA",
                "RUTA",
                "ENTREGADO",
            ],
        )
        .values(
            "producto__nombre"
        )
        .annotate(
            total_vendido=Sum(
                "cantidad"
            ),
            dinero_generado=Sum(
                "subtotal"
            ),
        )
        .order_by(
            "-total_vendido"
        )[:5]
    )

    context = {
        "total_ventas_hoy":
            total_ventas_hoy,

        "dinero_en_caja":
            dinero_en_caja,

        "dinero_banco":
            dinero_banco,

        "cantidad_pedidos_hoy":
            cantidad_pedidos_hoy,

        "ticket_promedio":
            ticket_promedio,

        "fechas_grafica":
            json.dumps(
                fechas_grafica
            ),

        "montos_grafica":
            json.dumps(
                montos_grafica
            ),

        "top_productos":
            top_productos,

        "sucursal":
            sucursal,
    }

    return render(
        request,
        "pedidos/dashboard_metrics.html",
        context,
    )


@require_safe
def perfil_usuario_view(request):
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
        return HttpResponseForbidden(
            "No hay una sucursal activa."
        )

    if not suscripcion_activa(
        tenant
    ):
        return render(
            request,
            "pedidos/suspendido.html",
        )

    ids_historial = (
        _pedidos_sesion(
            request
        )
    )

    ids_ocultos = set(
        request.session.get(
            "pedidos_pendientes_ocultos",
            [],
        )
    )

    mis_pedidos = (
        Pedido.objects
        .filter(
            id__in=ids_historial,
            sucursal=sucursal,
        )
        .order_by(
            "-id"
        )
    )

    activos = []
    historial = []

    for pedido in mis_pedidos:

        if pedido.estado in [
            "ENTREGADO",
            "CANCELADO",
        ]:
            historial.append(
                pedido
            )

            continue

        oculto_temporalmente = (
            pedido.id
            in ids_ocultos
            and
            pedido.estado
            == "PENDIENTE"
            and
            not pedido.pago_verificado
        )

        if oculto_temporalmente:
            continue

        activos.append(
            pedido
        )

    return render(
        request,
        "pedidos/perfil.html",
        {
            "activos":
                activos,

            "historial":
                historial,

            "sucursal":
                sucursal,
        },
    )

# --- PAGO DE SUSCRIPCIÓN (TU DINERO - EL CLIENTE TE PAGA A TI) ---


@login_required(login_url='login_custom')
@require_tenant_roles(
    Membership.ROLE_OWNER,
)
@require_POST
def pagar_suscripcion_view(request):
    """
    Inicia o reutiliza de forma idempotente
    un pago pendiente de suscripción.

    Mientras exista un intento CREADO/PENDIENTE:
    - no crea otra referencia;
    - no crea otro PagoWompi;
    - si ya tiene URL, reutiliza el mismo enlace.
    """
    
    tenant = getattr(
        request,
        "tenant",
        None,
    )

    rate_limit_pago = (
        _verificar_rate_limit_pago_suscripcion(
            request,
            tenant,
        )
    )

    if rate_limit_pago:
        response = HttpResponseBadRequest(
            "Se realizaron demasiados "
            "intentos de iniciar el pago "
            "de la suscripción. "
            "Espera unos minutos "
            "e inténtalo nuevamente."
        )

        response.status_code = 429

        response[
            "Retry-After"
        ] = str(
            max(
                int(
                    rate_limit_pago[
                        "retry_after"
                    ]
                ),
                1,
            )
        )

        return response

    try:
        precio_mensual = _decimal_monto(
            config(
                'FOODBACK_SUBSCRIPTION_PRICE',
                default='50.00',
            )
        )

        base = _base_url(request)

        webhook_url = (
            f"{base}/wompi-webhook/"
        )

        with transaction.atomic():

            tenant = getattr(
                request,
                "tenant",
                None,
            )

            if not tenant:
                messages.error(
                    request,
                    (
                        "No fue posible determinar "
                        "el restaurante activo."
                    ),
                )

                return redirect(
                    "dashboard_admin"
                )

            suscripcion = (
                SuscripcionTenant.objects
                .select_for_update()
                .filter(
                    tenant=tenant
                )
                .first()
            )

            if not suscripcion:
                messages.error(
                    request,
                    (
                        "No existe una suscripción "
                        "configurada para este restaurante."
                    ),
                )

                return redirect(
                    "dashboard_admin"
                )

            # -----------------------------------------
            # INTENTO YA EXISTENTE
            # -----------------------------------------

            pago = (
                PagoWompi.objects
                .select_for_update()
                .filter(
                    tipo="SUSCRIPCION",
                    tenant=tenant,
                    estado__in=[
                        "CREADO",
                        "PENDIENTE",
                    ],
                )
                .order_by(
                    "-fecha_creacion"
                )
                .first()
            )

            # Si ya existe un enlace todavía pendiente,
            # simplemente lo reutilizamos.
            if pago and pago.url_enlace:
                return redirect(
                    pago.url_enlace
                )

            # -----------------------------------------
            # REUTILIZAR O CREAR INTENTO
            # -----------------------------------------

            if pago:
                referencia = (
                    pago.referencia
                )

                # El precio queda congelado en el
                # intento original.
                monto_intento = (
                    pago.monto
                )

            else:
                referencia = (
                    _wompi_crear_referencia(
                        "SUBS",
                        tenant.id,
                    )
                )

                monto_intento = (
                    precio_mensual
                )

                pago = (
                    PagoWompi.objects.create(
                        tipo="SUSCRIPCION",
                        tenant=tenant,
                        referencia=referencia,
                        monto=monto_intento,
                        estado='PENDIENTE',
                    )
                )

            redirect_url = (
                f"{base}/"
                f"wompi-suscripcion-respuesta/"
                f"?ref={referencia}"
            )

            # -----------------------------------------
            # CREAR ENLACE WOMPI
            # -----------------------------------------

            try:
                data, raw_payload = (
                    _wompi_crear_enlace_pago(
                        request,
                        tipo_pago='SUSCRIPCION',
                        referencia=referencia,
                        monto=monto_intento,
                        nombre_producto=(
                            'Suscripción mensual '
                            'FoodBack'
                        ),
                        redirect_url=(
                            redirect_url
                        ),
                        webhook_url=(
                            webhook_url
                        ),
                    )
                )

            except Exception as e:
                print(
                    f'Error Wompi '
                    f'suscripción: {e}'
                )

                pago.estado = 'ERROR'
                pago.es_aprobada = False
                pago.ultimo_error = (
                    'No fue posible iniciar '
                    'el enlace de pago '
                    'de la suscripción.'
                )

                pago.save(
                    update_fields=[
                        'estado',
                        'es_aprobada',
                        'ultimo_error',
                    ]
                )

                messages.error(
                    request,
                    'No pudimos conectar con '
                    'la pasarela de pago. '
                    'Intenta nuevamente.'
                )

                return redirect(
                    'dashboard_admin'
                )

            url_enlace = (
                data.get('urlEnlace')
                or data.get('UrlEnlace')
            )

            id_enlace = (
                data.get('idEnlace')
                or data.get('IdEnlace')
            )

            if not url_enlace:
                pago.estado = 'ERROR'

                pago.ultimo_error = (
                    'Wompi no devolvió '
                    'urlEnlace para '
                    'suscripción.'
                )

                pago.raw_creacion = {
                    'request':
                        raw_payload,

                    'response':
                        data,
                }

                pago.save()

                messages.error(
                    request,
                    'No se pudo generar '
                    'el enlace de pago.'
                )

                return redirect(
                    'dashboard_admin'
                )

            pago.url_enlace = (
                url_enlace
            )

            pago.id_enlace = str(
                id_enlace or ''
            )

            pago.estado = 'PENDIENTE'

            pago.raw_creacion = {
                'request':
                    raw_payload,

                'response':
                    data,
            }

            pago.ultimo_error = ''

            pago.save()

            return redirect(
                url_enlace
            )

    except Exception as e:
        print(
            f'Error interno '
            f'suscripción Wompi: {e}'
        )

        messages.error(
            request,
            'No pudimos iniciar '
            'el pago de la suscripción.'
        )

        return redirect(
            'dashboard_admin'
        )


@require_GET
@login_required(login_url="login_custom")
@require_tenant_roles(
    Membership.ROLE_OWNER,
)
def wompi_suscripcion_respuesta_view(request):
    referencia = (
        request.GET.get("ref")
        or request.GET.get("referencia")
    )

    id_transaccion = request.GET.get(
        "idTransaccion",
        "",
    ).strip()

    if not referencia:
        messages.error(
            request,
            "No se recibió la referencia del pago.",
        )
        return redirect(
            "dashboard_admin"
        )

    tenant = getattr(
        request,
        "tenant",
        None,
    )

    if not tenant:
        return HttpResponseForbidden(
            "No hay un Tenant activo."
        )

    pago = (
        PagoWompi.objects
        .filter(
            referencia=referencia,
            tipo="SUSCRIPCION",
            tenant=tenant,
        )
        .first()
    )

    if not pago:
        messages.error(
            request,
            "No encontramos el pago de suscripción.",
        )
        return redirect(
            "dashboard_admin"
        )

    pago.raw_redirect = dict(
        request.GET.items()
    )
    pago.save(
        update_fields=[
            "raw_redirect",
            "fecha_actualizacion",
        ]
    )

    hash_valido = _validar_hash_redirect_wompi(
        request.GET,
        referencia=pago.referencia,
        tipo_pago=pago.tipo,
    )

    if (
        hash_valido
        and _redirect_wompi_aprobado(
            request.GET
        )
    ):
        ok, msg = _procesar_pago_wompi_aprobado(
            pago.referencia,
            id_transaccion=id_transaccion,
            monto=request.GET.get("monto"),
            raw_payload=dict(
                request.GET.items()
            ),
            origen="REDIRECT",
        )

        if ok:
            return render(
                request,
                "pedidos/pago_exitoso_suscripcion.html",
            )

        messages.warning(
            request,
            f"Pago recibido, pero quedó en revisión: {msg}",
        )
        return redirect(
            "dashboard_admin"
        )

    if pago.estado == "APROBADO":
        return render(
            request,
            "pedidos/pago_exitoso_suscripcion.html",
        )

    return render(
        request,
        "pedidos/pago_verificando_suscripcion.html",
        {"pago": pago},
    )



@csrf_exempt
@never_cache
@require_POST
def wompi_webhook_view(request):
    
    if request.content_type != "application/json":
        return JsonResponse(
            {
                "status": "error",
                "msg": "Content-Type no soportado",
            },
            status=415,
    )

    limite_bytes = (
        settings
        .FOODBACK_WOMPI_WEBHOOK_MAX_BYTES
    )

    content_length = request.META.get(
        "CONTENT_LENGTH"
    )

    if content_length:
        try:
            if int(content_length) > limite_bytes:
                return JsonResponse(
                    {
                        "status": "error",
                        "msg": (
                            "Payload demasiado grande"
                        ),
                    },
                    status=413,
                )

        except (
            TypeError,
            ValueError,
        ):
            pass

    raw_body = request.body

    if len(raw_body) > limite_bytes:
        return JsonResponse(
            {
                "status": "error",
                "msg": (
                    "Payload demasiado grande"
                ),
            },
            status=413,
        )

    try:
        data = json.loads(raw_body.decode('utf-8'))
        transaccion = _extraer_transaccion_wompi(data)

        enlace_pago = _get_any(
            data,
            'EnlacePago',
            'enlacePago',
            default={},
        ) or {}

        referencia = (
            _get_any(
                transaccion,
                'identificadorEnlaceComercio',
                'IdentificadorEnlaceComercio',
                'referencia',
                'Referencia',
            )
            or
            _get_any(
                data,
                'identificadorEnlaceComercio',
                'IdentificadorEnlaceComercio',
            )
            or
            _get_any(
                enlace_pago,
                'identificadorEnlaceComercio',
                'IdentificadorEnlaceComercio',
            )
        )

        tipo_pago = _wompi_tipo_desde_referencia(
            referencia
        )

        if not _validar_hash_webhook_wompi(
            request,
            referencia=referencia,
            tipo_pago=tipo_pago,
            raw_body=raw_body
        ):
            return JsonResponse(
                {
                    'status': 'error',
                    'msg': 'Webhook no autorizado'
                },
                status=403
            )

        es_aprobada = _valor_bool_wompi(
            _get_any(
                transaccion,
                'esAprobada',
                'EsAprobada',
                'approved',
                'status',
                'ResultadoTransaccion',
                'resultadoTransaccion',
            )
        )

        id_transaccion = _get_any(
            transaccion,
            'idTransaccion',
            'IdTransaccion',
            'id',
            'Id',
        )

        monto = (
            _get_any(
                transaccion,
                'monto',
                'Monto',
            )
            or
            _get_any(
                data,
                'monto',
                'Monto',
            )
        )

        if not referencia:
            return JsonResponse({'status': 'ok', 'msg': 'Webhook recibido sin referencia'})

        pago = PagoWompi.objects.filter(referencia=referencia).first()
        if pago:
            pago.raw_webhook = data
            pago.save()

        if es_aprobada:
            ok, msg = _procesar_pago_wompi_aprobado(
                referencia,
                id_transaccion=id_transaccion,
                monto=monto,
                raw_payload=data,
                origen='WEBHOOK'
            )
            return JsonResponse({'status': 'ok' if ok else 'warning', 'msg': msg})

        if pago and pago.estado != 'APROBADO':

            pago.estado = 'RECHAZADO'
            pago.es_aprobada = False

            pago.ultimo_error = (
                'Webhook recibido, pero la '
                'transacción no venía aprobada.'
            )

            pago.save()

            cuerpo_hash = hashlib.sha256(
                raw_body
            ).hexdigest()

            identificador_evento = (
                str(id_transaccion).strip()
                if id_transaccion
                else cuerpo_hash
            )

            _registrar_evento_pago(
                pago,
                categoria='RECHAZO_CLIENTE',
                origen='WEBHOOK',
                codigo='WOMPI_RECHAZADO',
                mensaje=(
                    'Wompi informó que la '
                    'transacción no fue aprobada.'
                ),
                cuenta_para_cliente=True,
                cuenta_para_global=False,
                clave_evento=(
                    f"RECHAZO:"
                    f"{pago.id}:"
                    f"{identificador_evento}"
                ),
            )

        return JsonResponse({'status': 'ok', 'msg': 'Webhook recibido'})
    
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
    ):
        return JsonResponse(
            {
                'status': 'error',
                'msg': 'JSON inválido',
            },
            status=400,
        )

    except Exception as e:
        print(f'Error webhook Wompi: {e}')
        return JsonResponse({'status': 'error', 'msg': 'Error interno'}, status=500)
