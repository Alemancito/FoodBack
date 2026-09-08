import hashlib
import hmac
import json
import uuid
import urllib.request
import requests
import time  # Necesario para generar referencias únicas
from datetime import datetime, date, timedelta
from decimal import Decimal, InvalidOperation
from django.shortcuts import render, redirect, get_object_or_404
from .models import Categoria, Producto, Pedido, DetallePedido, Cliente, ConfiguracionNegocio, DiaEspecial, OpcionProducto, Extra, PagoWompi
from django.db import transaction
from django.contrib import messages
from decouple import config
from django.contrib.auth.decorators import login_required, user_passes_test
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST
from django.views.decorators.csrf import csrf_exempt  # IMPORTANTE PARA EL WEBHOOK
from django.contrib.auth.views import LoginView
from django.contrib.auth import logout
from django.http import JsonResponse, HttpResponseBadRequest
from django.template.loader import render_to_string
from django.db.models import Sum, Count, F, Q, Max, Prefetch
from django.core.exceptions import PermissionDenied
from django.utils.dateparse import parse_datetime
from django.utils import timezone

# --- LÓGICA DE LOGIN Y SEGURIDAD ---


class CustomLoginView(LoginView):
    template_name = 'registration/login.html'
    redirect_authenticated_user = True

    def get_success_url(self):
        user = self.request.user
        if user.groups.filter(name='Administradores').exists() or user.is_superuser:
            return '/dashboard/'
        elif user.groups.filter(name='Repartidores').exists():
            return '/reparto/'
        else:
            return '/'


@require_POST
def logout_view(request):
    logout(request)
    return redirect('login_custom')


def es_admin(user):
    return user.groups.filter(name='Administradores').exists() or user.is_superuser


def es_repartidor(user):
    return user.groups.filter(name='Repartidores').exists()

# --- VALIDACIÓN DE SUSCRIPCIÓN (EL GUARDIA DE SEGURIDAD) ---


def suscripcion_activa():
    """Retorna True si está al día, False si venció."""
    config_negocio = ConfiguracionNegocio.objects.first()
    if not config_negocio:
        return True

    hoy = date.today()
    vencimiento = config_negocio.fecha_vencimiento

    # Si tiene fecha y la fecha de hoy es MAYOR o IGUAL al vencimiento, CORTAMOS.
    if vencimiento and hoy >= vencimiento:
        return False
    return True

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


def verificar_estado_negocio():
    ahora = datetime.now()
    fecha_hoy = ahora.date()
    hora_actual = ahora.time()
    dia_semana = ahora.weekday()

    config = ConfiguracionNegocio.objects.first()
    if not config:
        config = ConfiguracionNegocio.objects.create()

    # KILL SWITCH INTERNO
    if not suscripcion_activa():
        return False, "Servicio en mantenimiento administrativo."

    apertura_efectiva = config.hora_apertura
    cierre_efectivo = config.hora_cierre
    mensaje_base = config.mensaje_cierre

    dias_globales = [
        config.lunes_abierto, config.martes_abierto, config.miercoles_abierto,
        config.jueves_abierto, config.viernes_abierto, config.sabado_abierto,
        config.domingo_abierto
    ]
    esta_habilitado = dias_globales[dia_semana]

    excepcion = DiaEspecial.objects.filter(fecha=fecha_hoy).first()

    if excepcion:
        if excepcion.abierto:
            esta_habilitado = True
            if excepcion.hora_apertura:
                apertura_efectiva = excepcion.hora_apertura
            if excepcion.hora_cierre:
                cierre_efectivo = excepcion.hora_cierre
        else:
            motivo = excepcion.motivo or ""
            return False, f"{mensaje_base} ({motivo})"

    if esta_habilitado:
        if apertura_efectiva < cierre_efectivo:
            if apertura_efectiva <= hora_actual <= cierre_efectivo:
                return True, ""
        else:
            if hora_actual >= apertura_efectiva or hora_actual <= cierre_efectivo:
                return True, ""

        ap_str = apertura_efectiva.strftime('%I:%M %p').lower()
        ci_str = cierre_efectivo.strftime('%I:%M %p').lower()
        return False, f"{mensaje_base} (Hoy: {ap_str} - {ci_str})"

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
):
    """
    Verifica que:

    - la opción exista;
    - pertenezca realmente al producto;
    - esté disponible;
    - los extras existan;
    - estén autorizados para ese producto;
    - estén disponibles.

    Devuelve objetos obtenidos exclusivamente desde la BD.
    """

    opcion = None

    if opcion_id not in [None, "", "0", 0]:
        opcion_id_limpio = _entero_positivo(
            opcion_id,
            "opcion_id",
        )

        opcion = (
            OpcionProducto.objects
            .filter(
                id=opcion_id_limpio,
                producto=producto,
                disponible=True,
            )
            .first()
        )

        if not opcion:
            raise CarritoInvalido(
                "La opción seleccionada no es válida para este producto."
            )

    extras_ids = extras_ids or []
    extras_ids_limpios = []

    for extra_id in extras_ids:
        extra_id_limpio = _entero_positivo(
            extra_id,
            "extra_id",
        )

        if extra_id_limpio not in extras_ids_limpios:
            extras_ids_limpios.append(extra_id_limpio)

    extras = []

    if extras_ids_limpios:
        extras = list(
            producto.extras.filter(
                id__in=extras_ids_limpios,
                disponible=True,
            ).order_by("id")
        )

        ids_encontrados = {
            extra.id
            for extra in extras
        }

        if ids_encontrados != set(extras_ids_limpios):
            raise CarritoInvalido(
                "Uno o más extras no son válidos para este producto."
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


def _validar_carrito(cart):
    """
    Reconstruye TODO el carrito desde la base de datos.

    No confía en precios, relaciones ni IDs almacenados
    previamente en la sesión.
    """

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
        ) = _descomponer_clave_carrito(key)

        producto = (
            Producto.objects
            .filter(
                id=producto_id,
                disponible=True,
            )
            .first()
        )

        if not producto:
            raise CarritoInvalido(
                "El producto ya no está disponible."
            )

        opcion, extras = _validar_seleccion_producto(
            producto=producto,
            opcion_id=opcion_id,
            extras_ids=extras_ids,
        )

        precio_item = producto.precio

        if opcion:
            precio_item += opcion.precio_extra

        for extra in extras:
            precio_item += extra.precio

        subtotal = precio_item * cantidad

        items_validados.append({
            "key": key,
            "producto": producto,
            "cantidad": cantidad,
            "opcion": opcion,
            "nombre_opcion": opcion.nombre if opcion else "",
            "lista_extras": extras,
            "precio_item": precio_item,
            "subtotal": subtotal,
        })

    return items_validados


# --- VISTAS PÚBLICAS ---

def menu_view(request):
    _limpiar_pedidos_pendientes_vencidos()

    if not suscripcion_activa():
        return render(request, 'pedidos/suspendido.html')

    categorias = Categoria.objects.all().order_by('orden')
    cart = request.session.get('cart', {})
    cantidad_total = sum(cart.values())

    abierto, mensaje_estado = verificar_estado_negocio()

    ultimo_pedido_id = request.session.get('ultimo_pedido_id')
    ultimo_pedido_activo = None

    if ultimo_pedido_id:
        try:
            ped = Pedido.objects.get(id=ultimo_pedido_id)
            if ped.estado not in ['ENTREGADO', 'CANCELADO']:
                ultimo_pedido_activo = ped
            else:
                if 'ultimo_pedido_id' in request.session:
                    del request.session['ultimo_pedido_id']
        except Pedido.DoesNotExist:
            if 'ultimo_pedido_id' in request.session:
                del request.session['ultimo_pedido_id']

    return render(request, 'pedidos/menu.html', {
        'categorias': categorias,
        'cantidad_carrito': cantidad_total,
        'abierto': abierto,
        'mensaje_estado': mensaje_estado,
        'ultimo_pedido_activo': ultimo_pedido_activo
    })


@require_POST
def cart_add(request, producto_id):
    if not suscripcion_activa():
        return render(
            request,
            'pedidos/suspendido.html'
        )

    producto = get_object_or_404(
        Producto,
        id=producto_id,
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
def eliminar_item_carrito(request, producto_id):
    cart = request.session.get('cart', {})
    key_to_delete = str(producto_id)

    if key_to_delete in cart:
        del cart[key_to_delete]
        request.session['cart'] = cart
        request.session.modified = True

    if request.headers.get('x-requested-with') == 'XMLHttpRequest':
        productos_en_carrito = []
        total_productos = 0

        for key, cantidad in cart.items():
            parts = key.split('-')
            prod_id = parts[0]
            opc_id = parts[1] if len(parts) > 1 else "0"
            extras_str = parts[2] if len(parts) > 2 else "0"

            producto = get_object_or_404(Producto, id=prod_id)
            precio_item = producto.precio

            opcion = None
            if opc_id != "0":
                opcion = OpcionProducto.objects.filter(id=opc_id).first()
                if opcion:
                    precio_item += opcion.precio_extra

            if extras_str != "0":
                ids_ext = extras_str.split(',')
                extras_objs = Extra.objects.filter(id__in=ids_ext)
                for ex in extras_objs:
                    precio_item += ex.precio

            subtotal = precio_item * cantidad
            total_productos += subtotal

            productos_en_carrito.append({
                'producto': producto,
                'cantidad': cantidad,
                'subtotal': subtotal,
                'opcion': opcion,
                'key': key
            })

        html = render_to_string('pedidos/partials/cart_summary.html', {
            'items': productos_en_carrito, 'total_productos': total_productos
        })
        return JsonResponse({'status': 'ok', 'html': html, 'total': float(total_productos), 'vacio': len(cart) == 0})

    return redirect('checkout')


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
            metodo_pago='TARJETA',
            estado='PENDIENTE',
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


def checkout_view(request):
    _limpiar_pedidos_pendientes_vencidos()

    if not suscripcion_activa():
        return render(
            request,
            'pedidos/suspendido.html'
        )

    abierto, mensaje = verificar_estado_negocio()

    if not abierto:
        messages.error(
            request,
            f"⛔ El restaurante ha cerrado. {mensaje}"
        )
        return redirect('menu')

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
                cart
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
                        telefono=telefono,
                        defaults={
                            'nombre': nombre,
                            'apellido': apellido,
                            'direccion_ultima': direccion,
                        }
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
                    cliente=cliente,
                    direccion_entrega=direccion,
                    metodo_pago=metodo_pago,
                    latitud=lat,
                    longitud=lng,
                    es_pedido_whatsapp=False,
                    estado=estado_inicial,
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

                request.session[
                    'ultimo_pedido_id'
                ] = pedido.id

                historial = request.session.get(
                    'historial_pedidos',
                    []
                )

                if pedido.id not in historial:
                    historial.append(
                        pedido.id
                    )

                request.session[
                    'historial_pedidos'
                ] = historial

                request.session[
                    'cart'
                ] = {}

                request.session.modified = True

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


    context = {
        'items':
            productos_en_carrito,

        'total_productos':
            total_productos,

        'total_wompi':
            (
                pedido_pendiente.total_final
                if pedido_pendiente
                else float(total_productos) * 1.05
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
    }

    return render(
        request,
        'pedidos/checkout.html',
        context
    )

# --- VISTAS DE PAGO WOMPI (CLIENTES PAGANDO PEDIDOS) ---


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
    return str(value).strip().lower() in ['true', '1', 'si', 'sí', 'aprobada', 'approved']


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


def _renovar_suscripcion_30_dias(config_negocio=None):
    if not config_negocio:
        config_negocio = ConfiguracionNegocio.objects.first(
        ) or ConfiguracionNegocio.objects.create()

    hoy = date.today()
    base_fecha = config_negocio.fecha_vencimiento
    if not base_fecha or base_fecha < hoy:
        base_fecha = hoy

    config_negocio.fecha_vencimiento = base_fecha + timedelta(days=30)
    config_negocio.save()
    return config_negocio


@transaction.atomic
def _procesar_pago_wompi_aprobado(
    referencia,
    *,
    id_transaccion=None,
    monto=None,
    raw_payload=None,
    origen='WEBHOOK'
):
    """
    Confirma un pago Wompi solamente si:

    - existe el PagoWompi local;
    - existe idTransaccion;
    - el monto coincide exactamente;
    - esa transacción no fue usada por otro pago;
    - un replay del mismo pago es idempotente.

    Esta función es la barrera central para cualquier
    confirmación financiera de FoodBack.
    """

    pago = (
        PagoWompi.objects
        .select_for_update()
        .filter(
            referencia=referencia
        )
        .first()
    )

    # Compatibilidad temporal con referencias antiguas.
    if (
        not pago
        and referencia
        and referencia.startswith('ORDEN-')
    ):
        try:
            pedido_id = int(
                referencia.split('-')[1]
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
                .order_by('-fecha_creacion')
                .first()
            )

        except Exception:
            pago = None

    if (
        not pago
        and referencia
        and referencia.startswith('SUBS-')
    ):
        pago = (
            PagoWompi.objects
            .select_for_update()
            .filter(
                referencia=referencia
            )
            .order_by('-fecha_creacion')
            .first()
        )

    if not pago:
        return (
            False,
            'No existe registro local para esa referencia.'
        )

    # -------------------------------------------------
    # ID DE TRANSACCIÓN OBLIGATORIO
    # -------------------------------------------------

    id_transaccion = str(
        id_transaccion or ''
    ).strip()

    if not id_transaccion:
        if pago.estado != 'APROBADO':
            pago.ultimo_error = (
                'Wompi indicó aprobación sin '
                'idTransaccion.'
            )

            if raw_payload is not None:
                if origen == 'REDIRECT':
                    pago.raw_redirect = raw_payload
                else:
                    pago.raw_webhook = raw_payload

            pago.save()

        return (
            False,
            'La transacción no contiene '
            'un identificador válido.'
        )

    # -------------------------------------------------
    # REPLAY DEL MISMO PAGO
    # -------------------------------------------------

    if pago.estado == 'APROBADO':

        # Mismo pago + misma transacción =
        # replay válido e idempotente.
        if (
            pago.id_transaccion
            and str(pago.id_transaccion)
            == id_transaccion
        ):
            return (
                True,
                'Pago ya estaba aprobado.'
            )

        # Una referencia ya aprobada jamás debe
        # cambiar posteriormente de transacción.
        return (
            False,
            'El pago ya fue aprobado con '
            'otra transacción.'
        )

    # -------------------------------------------------
    # MONTO OBLIGATORIO Y EXACTO
    # -------------------------------------------------

    if not _monto_coincide(
        pago.monto,
        monto
    ):
        pago.estado = 'ERROR'

        pago.ultimo_error = (
            f'Monto no coincide. '
            f'Esperado {pago.monto}, '
            f'recibido {monto}'
        )

        if raw_payload is not None:
            if origen == 'REDIRECT':
                pago.raw_redirect = raw_payload
            else:
                pago.raw_webhook = raw_payload

        pago.save()

        return (
            False,
            pago.ultimo_error
        )

    # -------------------------------------------------
    # UNA TRANSACCIÓN NO PUEDE PAGAR DOS COSAS
    # -------------------------------------------------

    transaccion_usada = (
        PagoWompi.objects
        .select_for_update()
        .filter(
            id_transaccion=id_transaccion
        )
        .exclude(
            pk=pago.pk
        )
        .exists()
    )

    if transaccion_usada:
        pago.ultimo_error = (
            'El idTransaccion recibido ya fue '
            'utilizado por otro pago.'
        )

        if raw_payload is not None:
            if origen == 'REDIRECT':
                pago.raw_redirect = raw_payload
            else:
                pago.raw_webhook = raw_payload

        pago.save()

        return (
            False,
            pago.ultimo_error
        )

    # Defensa adicional mientras todavía existen
    # campos históricos en Pedido.
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
            'La transacción ya está asociada '
            'a otro pedido.'
        )

        pago.save()

        return (
            False,
            pago.ultimo_error
        )

    # -------------------------------------------------
    # APROBACIÓN
    # -------------------------------------------------

    pago.estado = 'APROBADO'
    pago.es_aprobada = True
    pago.id_transaccion = id_transaccion

    if raw_payload is not None:
        if origen == 'REDIRECT':
            pago.raw_redirect = raw_payload
        else:
            pago.raw_webhook = raw_payload

    pago.fecha_aprobacion = timezone.now()
    pago.ultimo_error = ''
    pago.save()

    if (
        pago.tipo == 'PEDIDO'
        and pago.pedido
    ):
        pedido = pago.pedido

        pedido.estado = 'RECIBIDO'
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
            f'Pedido #{pedido.id} confirmado.'
        )

    if pago.tipo == 'SUSCRIPCION':
        _renovar_suscripcion_30_dias(
            pago.configuracion_negocio
        )

        return (
            True,
            'Suscripción renovada.'
        )

    return (
        True,
        'Pago aprobado.'
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

    try:
        base = _base_url(request)
        webhook_url = f"{base}/wompi-webhook/"

        # Buscamos primero un intento reutilizable.
        pago = (
            PagoWompi.objects
            .filter(
                pedido=pedido,
                estado__in=['CREADO', 'PENDIENTE']
            )
            .order_by('-fecha_creacion')
            .first()
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
                pedido=pedido,
                referencia=referencia,
                monto=pedido.total_final,
                estado='PENDIENTE',
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
def pagar_wompi_view(request, tracking_token):
    pedido = get_object_or_404(
        Pedido,
        tracking_token=tracking_token
    )

    return _iniciar_pago_wompi_pedido(
        request,
        pedido
    )


def wompi_respuesta_view(request):
    referencia = request.GET.get('ref') or request.GET.get('pedido_ref')
    id_transaccion = request.GET.get('idTransaccion', '').strip()

    if not referencia:
        messages.error(request, 'No se recibió la referencia del pago.')
        return redirect('menu')

    pago = (
        PagoWompi.objects
        .filter(referencia=referencia)
        .select_related('pedido')
        .first()
    )

    pedido = pago.pedido if pago else None

    if not pago or not pedido:
        messages.error(
            request, 'No encontramos el pedido relacionado al pago.')
        return redirect('menu')

    pago.raw_redirect = dict(request.GET.items())
    pago.save()

    request.session['ultimo_pedido_id'] = pedido.id
    historial = request.session.get('historial_pedidos', [])
    if pedido.id not in historial:
        historial.append(pedido.id)
    request.session['historial_pedidos'] = historial
    request.session.modified = True

    # Si Wompi envía hash en el redirect, podemos confirmar inmediatamente.
    # Si no viene hash, NO confiamos en la URL: dejamos que el webhook confirme.
    if _validar_hash_redirect_wompi(request.GET, referencia=pago.referencia, tipo_pago=pago.tipo) and _redirect_wompi_aprobado(request.GET):
        ok, msg = _procesar_pago_wompi_aprobado(
            pago.referencia,
            id_transaccion=id_transaccion,
            monto=request.GET.get('monto'),
            raw_payload=dict(request.GET.items()),
            origen='REDIRECT'
        )
        if ok:
            messages.success(
                request, 'Pago confirmado. Tu pedido fue recibido.')
        else:
            messages.warning(request, f'Pago en revisión: {msg}')
    elif pago.estado == 'APROBADO' or pedido.pago_verificado:
        messages.success(request, 'Pago confirmado. Tu pedido fue recibido.')
    else:
        messages.info(
            request, 'Estamos verificando tu pago. Tu pedido se activará automáticamente al confirmarse.')

    return redirect(
        'order_tracker',
        tracking_token=pedido.tracking_token
    )


def pedido_exito_view(request, tracking_token):
    pedido = get_object_or_404(
        Pedido,
        tracking_token=tracking_token
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


def _ultimo_cambio_pedidos():
    """
    Devuelve la última fecha en que cambió cualquier pedido.
    Esto evita renderizar HTML completo si nada cambió.
    """
    return Pedido.objects.aggregate(ultimo=Max('actualizado_en'))['ultimo']


def _query_detalles_optimizada():
    return DetallePedido.objects.select_related(
        'producto',
        'opcion'
    ).prefetch_related(
        'extras'
    )


def _query_pedidos_admin():
    return Pedido.objects.exclude(
        estado__in=['ENTREGADO', 'CANCELADO']
    ).select_related(
        'cliente',
        'repartidor'
    ).prefetch_related(
        Prefetch('detalles', queryset=_query_detalles_optimizada())
    ).order_by('-id')


def _contexto_admin_pedidos():
    pedidos = list(_query_pedidos_admin())

    pedidos_nuevos = [
        p for p in pedidos
        if p.estado == 'RECIBIDO' or not p.estado
    ]

    pedidos_cocina = [
        p for p in pedidos
        if p.estado == 'COCINA'
    ]

    pedidos_ruta = [
        p for p in pedidos
        if p.estado == 'RUTA'
    ]

    pedidos_problema = [
        p for p in pedidos
        if p.estado == 'PROBLEMA'
    ]

    return {
        'pedidos': pedidos,
        'pedidos_nuevos': pedidos_nuevos,
        'pedidos_cocina': pedidos_cocina,
        'pedidos_ruta': pedidos_ruta,
        'pedidos_problema': pedidos_problema,
    }


def _query_pedidos_delivery_base():
    return Pedido.objects.select_related(
        'cliente',
        'repartidor'
    ).prefetch_related(
        Prefetch('detalles', queryset=_query_detalles_optimizada())
    )


def _contexto_delivery_pedidos(user):
    disponibles = list(
        _query_pedidos_delivery_base().filter(
            estado='RUTA',
            repartidor=None
        ).order_by('id')
    )

    mis_pedidos = list(
        _query_pedidos_delivery_base().filter(
            estado='RUTA',
            repartidor=user
        ).order_by('id')
    )

    return {
        'disponibles': disponibles,
        'mis_pedidos': mis_pedidos,
    }

# --- DASHBOARDS PROTEGIDOS ---


@never_cache
@login_required(login_url='login_custom')
@user_passes_test(es_admin, login_url='login_custom')
def dashboard_admin_view(request):
    _limpiar_pedidos_pendientes_vencidos()

    config_negocio = ConfiguracionNegocio.objects.first()
    dias_restantes = 30
    bloqueado = False

    if config_negocio and config_negocio.fecha_vencimiento:
        dias_restantes = (config_negocio.fecha_vencimiento - date.today()).days

        if dias_restantes < 0:
            bloqueado = True

    if bloqueado and request.method == 'POST':
        messages.error(request, "⛔ Acción denegada. Suscripción vencida.")

    elif request.method == 'POST':
        pedido = get_object_or_404(Pedido, id=request.POST.get('pedido_id'))
        accion = request.POST.get('accion')

        if accion == 'cocina':
            pedido.estado = 'COCINA'
            messages.success(request, f"Orden #{pedido.id} enviada a Cocina 🔥")

        elif accion == 'ruta':
            pedido.estado = 'RUTA'
            messages.success(request, f"Orden #{pedido.id} lista para Ruta 🛵")

        elif accion == 'reintentar':
            pedido.estado = 'RUTA'
            messages.info(request, f"Reintentando Orden #{pedido.id} 🔄")

        elif accion == 'cancelar':
            pedido.estado = 'CANCELADO'
            messages.error(request, f"Orden #{pedido.id} cancelada ❌")

        pedido.save()
        return redirect('dashboard_admin')

    context = _contexto_admin_pedidos()
    context.update({
        'dias_restantes': dias_restantes,
        'last_update': _iso_datetime(_ultimo_cambio_pedidos()),
    })

    return render(request, 'pedidos/dashboard_admin.html', context)


@never_cache
@login_required(login_url='login_custom')
@user_passes_test(es_admin, login_url='login_custom')
def api_dashboard_admin_sync(request):
    ultimo_servidor = _ultimo_cambio_pedidos()
    ultimo_cliente_raw = request.GET.get('last_update', 'none')
    ultimo_cliente = _parse_last_update(ultimo_cliente_raw)

    if ultimo_servidor is None and ultimo_cliente_raw == "none":
        return JsonResponse({
            'changed': False,
            'last_update': "none",
        })

    if ultimo_servidor and ultimo_cliente and ultimo_servidor <= ultimo_cliente:
        return JsonResponse({
            'changed': False,
            'last_update': _iso_datetime(ultimo_servidor),
        })

    context = _contexto_admin_pedidos()

    html_nuevos = render_to_string(
        'pedidos/partials/admin_nuevos.html',
        context,
        request=request
    )

    html_cocina = render_to_string(
        'pedidos/partials/admin_cocina.html',
        context,
        request=request
    )

    html_ruta = render_to_string(
        'pedidos/partials/admin_ruta.html',
        context,
        request=request
    )

    html_problema = render_to_string(
        'pedidos/partials/admin_problema.html',
        context,
        request=request
    )

    return JsonResponse({
        'changed': True,
        'last_update': _iso_datetime(ultimo_servidor),
        'nuevos_count': len(context['pedidos_nuevos']),
        'html': {
            'pills-nuevos': html_nuevos,
            'pills-cocina': html_cocina,
            'pills-ruta': html_ruta,
            'pills-problema': html_problema,
        }
    })


@never_cache
@login_required(login_url='login_custom')
@user_passes_test(es_admin, login_url='login_custom')
def admin_settings_view(request):
    if not suscripcion_activa():
        messages.error(
            request, "⛔ Acceso denegado a Configuración. Suscripción vencida.")
        return redirect('dashboard_admin')

    config_negocio = ConfiguracionNegocio.objects.first()
    if not config_negocio:
        config_negocio = ConfiguracionNegocio.objects.create()

    if request.method == 'POST':
        tipo_accion = request.POST.get('tipo_accion')

        if tipo_accion == 'global':
            config_negocio.hora_apertura = request.POST.get('hora_apertura')
            config_negocio.hora_cierre = request.POST.get('hora_cierre')
            config_negocio.mensaje_cierre = request.POST.get('mensaje_cierre')

            dias_map = ['lunes', 'martes', 'miercoles',
                        'jueves', 'viernes', 'sabado', 'domingo']
            for d in dias_map:
                valor = request.POST.get(f'{d}_abierto') == 'on'
                setattr(config_negocio, f'{d}_abierto', valor)

            config_negocio.save()
            messages.success(
                request, "Configuración global actualizada (Excepciones mantenidas) ⚙️")
            return redirect('admin_settings')

        elif tipo_accion == 'dia_especifico':
            fecha_str = request.POST.get('fecha_target')
            if not fecha_str:
                messages.error(request, "Error: No se recibió la fecha.")
                return redirect('admin_settings')

            fecha_dt = datetime.strptime(fecha_str, '%Y-%m-%d').date()
            excepcion, created = DiaEspecial.objects.get_or_create(
                fecha=fecha_dt)
            excepcion.abierto = request.POST.get('estado_dia') == 'on'
            h_ap = request.POST.get('hora_apertura_dia')
            h_ci = request.POST.get('hora_cierre_dia')
            excepcion.hora_apertura = h_ap if h_ap else None
            excepcion.hora_cierre = h_ci if h_ci else None
            excepcion.motivo = request.POST.get('motivo')
            excepcion.save()
            messages.success(
                request, f"Horario para {fecha_str} actualizado ✅")
            return redirect('admin_settings')

    agenda = []
    hoy = date.today()
    nombres_dias = ['Lunes', 'Martes', 'Miércoles',
                    'Jueves', 'Viernes', 'Sábado', 'Domingo']
    defaults_globales = [
        config_negocio.lunes_abierto, config_negocio.martes_abierto,
        config_negocio.miercoles_abierto, config_negocio.jueves_abierto,
        config_negocio.viernes_abierto, config_negocio.sabado_abierto,
        config_negocio.domingo_abierto
    ]

    for i in range(7):
        fecha_iter = hoy + timedelta(days=i)
        idx = fecha_iter.weekday()
        es_abierto = defaults_globales[idx]
        h_inicio = config_negocio.hora_apertura
        h_fin = config_negocio.hora_cierre
        motivo = ""
        es_excepcion = False
        excepcion = DiaEspecial.objects.filter(fecha=fecha_iter).first()
        id_db = None
        if excepcion:
            es_abierto = excepcion.abierto
            motivo = excepcion.motivo
            if excepcion.hora_apertura:
                h_inicio = excepcion.hora_apertura
            if excepcion.hora_cierre:
                h_fin = excepcion.hora_cierre
            es_excepcion = True
            id_db = excepcion.id

        agenda.append({
            'fecha_str': fecha_iter.strftime("%Y-%m-%d"),
            'nombre_dia': "HOY" if i == 0 else ("MAÑANA" if i == 1 else nombres_dias[idx]),
            'fecha_fmt': fecha_iter.strftime("%d/%m"),
            'abierto': es_abierto,
            'hora_ini': h_inicio,
            'hora_fin': h_fin,
            'motivo': motivo,
            'id_db': id_db,
            'es_excepcion': es_excepcion
        })

    return render(request, 'pedidos/admin_settings.html', {'config': config_negocio, 'agenda': agenda})


@login_required(login_url='login_custom')
@user_passes_test(es_admin, login_url='login_custom')
@require_POST
def eliminar_excepcion_view(request, excepcion_id):
    if not suscripcion_activa():
        messages.error(
            request,
            "Acción denegada. Suscripción vencida."
        )
        return redirect('dashboard_admin')

    excepcion = get_object_or_404(
        DiaEspecial,
        id=excepcion_id
    )
    excepcion.delete()

    messages.info(
        request,
        "Excepción eliminada 🗑️"
    )

    return redirect('admin_settings')


@never_cache
@login_required(login_url='login_custom')
@user_passes_test(es_repartidor, login_url='login_custom')
def dashboard_delivery_view(request):
    _limpiar_pedidos_pendientes_vencidos()

    if not suscripcion_activa():
        return render(
            request,
            'pedidos/suspendido.html'
        )

    if request.method == 'POST':
        pedido = get_object_or_404(
            Pedido,
            id=request.POST.get('pedido_id')
        )
        accion = request.POST.get('accion')

        if accion == 'tomar':
            if pedido.estado == 'RUTA' and pedido.repartidor is None:
                pedido.repartidor = request.user
                pedido.save()
                messages.success(
                    request,
                    f"Pedido #{pedido.id} tomado 🛵"
                )
            else:
                messages.warning(
                    request,
                    "Ese pedido ya fue tomado por otro repartidor."
                )

        elif accion == 'entregado':
            if pedido.repartidor == request.user:
                pedido.estado = 'ENTREGADO'
                pedido.save()
                messages.success(
                    request,
                    f"Pedido #{pedido.id} entregado ✅"
                )
            else:
                messages.error(
                    request,
                    "No puedes entregar un pedido que no está en tu mochila."
                )

        elif accion == 'soltar':
            if pedido.estado == 'RUTA' and pedido.repartidor == request.user:
                pedido.repartidor = None
                pedido.save()
                messages.info(
                    request,
                    f"Pedido #{pedido.id} devuelto a disponibles 🔄"
                )
            else:
                messages.error(
                    request,
                    "No puedes quitar de tu mochila un pedido que no tienes asignado."
                )

        elif accion == 'problema':
            if pedido.repartidor == request.user:
                pedido.estado = 'PROBLEMA'
                pedido.repartidor = None
                pedido.save()
                messages.warning(
                    request,
                    f"Problema reportado en pedido #{pedido.id}"
                )
            else:
                messages.error(
                    request,
                    "No puedes reportar un pedido que no está en tu mochila."
                )

        return redirect('dashboard_delivery')

    context = _contexto_delivery_pedidos(request.user)
    context.update({
        'GOOGLE_MAPS_API_KEY': config('GOOGLE_MAPS_API_KEY', default=''),
        'last_update': _iso_datetime(_ultimo_cambio_pedidos()),
    })

    return render(
        request,
        'pedidos/dashboard_delivery.html',
        context
    )


@never_cache
@login_required(login_url='login_custom')
@user_passes_test(es_repartidor, login_url='login_custom')
def api_delivery_sync(request):
    ultimo_servidor = _ultimo_cambio_pedidos()
    ultimo_cliente_raw = request.GET.get('last_update', 'none')
    ultimo_cliente = _parse_last_update(ultimo_cliente_raw)

    if ultimo_servidor is None and ultimo_cliente_raw == "none":
        return JsonResponse({
            'changed': False,
            'last_update': "none",
        })

    if ultimo_servidor and ultimo_cliente and ultimo_servidor <= ultimo_cliente:
        return JsonResponse({
            'changed': False,
            'last_update': _iso_datetime(ultimo_servidor),
        })

    context = _contexto_delivery_pedidos(request.user)

    html_mochila = render_to_string(
        'pedidos/partials/delivery_mochila.html',
        context,
        request=request
    )

    html_pool = render_to_string(
        'pedidos/partials/delivery_pool.html',
        context,
        request=request
    )

    return JsonResponse({
        'changed': True,
        'last_update': _iso_datetime(ultimo_servidor),
        'pool_count': len(context['disponibles']),
        'html': {
            'zona-mochila': html_mochila,
            'zona-pool': html_pool,
        }
    })


def obtener_ubicacion_ip(request):
    x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    if x_forwarded_for:
        ip = x_forwarded_for.split(',')[0]
    else:
        ip = request.META.get('REMOTE_ADDR')
    if ip == '127.0.0.1':
        pass
    try:
        with urllib.request.urlopen(f"http://ip-api.com/json/{ip}") as url:
            data = json.loads(url.read().decode())
            if data.get('status') == 'success' and data.get('countryCode') == 'SV':
                return JsonResponse({'status': 'ok', 'lat': data['lat'], 'lng': data['lon'], 'city': data['city']})
    except:
        pass
    return JsonResponse({'status': 'error', 'lat': 13.6929, 'lng': -89.2182})


def order_tracker_view(request, tracking_token):
    pedido = get_object_or_404(
        Pedido,
        tracking_token=tracking_token
    )

    return render(
        request,
        'pedidos/order_tracker.html',
        {'pedido': pedido}
    )


def api_order_status(request, tracking_token):
    try:
        pedido = Pedido.objects.only(
            'id',
            'estado',
            'actualizado_en'
        ).get(
            tracking_token=tracking_token
        )

        return JsonResponse({
            'status': 'ok',
            'estado_codigo': pedido.estado,
            'estado_texto': pedido.get_estado_display(),
            'last_update': _iso_datetime(
                pedido.actualizado_en
            ),
        })

    except Pedido.DoesNotExist:
        return JsonResponse({
            'status': 'error',
            'msg': 'Pedido no encontrado'
        }, status=404)


@never_cache
@login_required(login_url='login_custom')
@user_passes_test(es_admin, login_url='login_custom')
def dashboard_metrics_view(request):
    if not suscripcion_activa():
        messages.error(
            request, "⛔ Acceso denegado a Finanzas. Suscripción vencida.")
        return redirect('dashboard_admin')

    hoy = datetime.now().date()
    pedidos_validos_hoy = Pedido.objects.filter(
        fecha_creacion__date=hoy).exclude(estado__in=['CANCELADO', 'PENDIENTE'])

    resumen = pedidos_validos_hoy.aggregate(
        total_general=Sum('total_final'),
        total_efectivo=Sum('total_final', filter=Q(metodo_pago='EFECTIVO')),
        total_wompi=Sum('total_final', filter=Q(metodo_pago='TARJETA'))
    )
    total_ventas_hoy = resumen['total_general'] or 0
    dinero_en_caja = resumen['total_efectivo'] or 0
    dinero_banco = resumen['total_wompi'] or 0
    cantidad_pedidos_hoy = pedidos_validos_hoy.count()
    ticket_promedio = total_ventas_hoy / \
        cantidad_pedidos_hoy if cantidad_pedidos_hoy > 0 else 0

    fechas_grafica, montos_grafica = [], []
    for i in range(6, -1, -1):
        fecha = hoy - timedelta(days=i)
        venta_dia = Pedido.objects.filter(fecha_creacion__date=fecha).exclude(estado__in=[
            'CANCELADO', 'PENDIENTE']).aggregate(Sum('total_final'))['total_final__sum'] or 0
        nombres_dias = ['Lun', 'Mar', 'Mié', 'Jue', 'Vie', 'Sáb', 'Dom']
        fechas_grafica.append(f"{nombres_dias[fecha.weekday()]} {fecha.day}")
        montos_grafica.append(float(venta_dia))

    top_productos = DetallePedido.objects.filter(pedido__estado__in=['RECIBIDO', 'COCINA', 'RUTA', 'ENTREGADO']).values(
        'producto__nombre').annotate(total_vendido=Sum('cantidad'), dinero_generado=Sum('subtotal')).order_by('-total_vendido')[:5]

    context = {
        'total_ventas_hoy': total_ventas_hoy, 'dinero_en_caja': dinero_en_caja, 'dinero_banco': dinero_banco,
        'cantidad_pedidos_hoy': cantidad_pedidos_hoy, 'ticket_promedio': ticket_promedio,
        'fechas_grafica': json.dumps(fechas_grafica), 'montos_grafica': json.dumps(montos_grafica),
        'top_productos': top_productos,
    }
    return render(request, 'pedidos/dashboard_metrics.html', context)


def perfil_usuario_view(request):
    if not suscripcion_activa():
        return render(request, 'pedidos/suspendido.html')

    ids_historial = request.session.get('historial_pedidos', [])
    mis_pedidos = Pedido.objects.filter(id__in=ids_historial).order_by('-id')
    activos = mis_pedidos.exclude(estado__in=['ENTREGADO', 'CANCELADO'])
    historial = mis_pedidos.filter(estado__in=['ENTREGADO', 'CANCELADO'])
    return render(request, 'pedidos/perfil.html', {'activos': activos, 'historial': historial})

# --- PAGO DE SUSCRIPCIÓN (TU DINERO - EL CLIENTE TE PAGA A TI) ---


@login_required(login_url='login_custom')
@user_passes_test(es_admin, login_url='login_custom')
@require_POST
def pagar_suscripcion_view(request):
    try:
        precio_mensual = _decimal_monto(
            config('FOODBACK_SUBSCRIPTION_PRICE', default='50.00'))
        config_negocio = ConfiguracionNegocio.objects.first(
        ) or ConfiguracionNegocio.objects.create()
        referencia = _wompi_crear_referencia('SUBS', config_negocio.id)
        base = _base_url(request)
        redirect_url = f"{base}/wompi-suscripcion-respuesta/?ref={referencia}"
        webhook_url = f"{base}/wompi-webhook/"

        pago = PagoWompi.objects.create(
            tipo='SUSCRIPCION',
            configuracion_negocio=config_negocio,
            referencia=referencia,
            monto=precio_mensual,
            estado='PENDIENTE',
        )

        data, raw_payload = _wompi_crear_enlace_pago(
            request,
            tipo_pago='SUSCRIPCION',
            referencia=referencia,
            monto=precio_mensual,
            nombre_producto='Suscripción mensual FoodBack',
            redirect_url=redirect_url,
            webhook_url=webhook_url,
        )

        url_enlace = data.get('urlEnlace') or data.get('UrlEnlace')
        id_enlace = data.get('idEnlace') or data.get('IdEnlace')

        if not url_enlace:
            pago.estado = 'ERROR'
            pago.ultimo_error = 'Wompi no devolvió urlEnlace para suscripción.'
            pago.raw_creacion = {'request': raw_payload, 'response': data}
            pago.save()
            messages.error(request, 'No se pudo generar el enlace de pago.')
            return redirect('dashboard_admin')

        pago.url_enlace = url_enlace
        pago.id_enlace = str(id_enlace or '')
        pago.raw_creacion = {'request': raw_payload, 'response': data}
        pago.save()

        return redirect(url_enlace)

    except Exception as e:
        print(f'Error Wompi suscripción: {e}')
        messages.error(
            request, 'No pudimos conectar con la pasarela de pago. Intenta nuevamente.')
        return redirect('dashboard_admin')


@login_required(login_url='login_custom')
def wompi_suscripcion_respuesta_view(request):
    referencia = request.GET.get('ref') or request.GET.get('referencia')
    id_transaccion = request.GET.get('idTransaccion', '').strip()

    if not referencia:
        messages.error(request, 'No se recibió la referencia del pago.')
        return redirect('dashboard_admin')

    pago = PagoWompi.objects.filter(
        referencia=referencia, tipo='SUSCRIPCION').first()
    if not pago:
        messages.error(request, 'No encontramos el pago de suscripción.')
        return redirect('dashboard_admin')

    pago.raw_redirect = dict(request.GET.items())
    pago.save()

    if _validar_hash_redirect_wompi(request.GET, referencia=pago.referencia, tipo_pago=pago.tipo) and _redirect_wompi_aprobado(request.GET):
        ok, msg = _procesar_pago_wompi_aprobado(
            pago.referencia,
            id_transaccion=id_transaccion,
            monto=request.GET.get('monto'),
            raw_payload=dict(request.GET.items()),
            origen='REDIRECT'
        )
        if ok:
            return render(request, 'pedidos/pago_exitoso_suscripcion.html')
        messages.warning(
            request, f'Pago recibido, pero quedó en revisión: {msg}')
        return redirect('dashboard_admin')

    if pago.estado == 'APROBADO':
        return render(request, 'pedidos/pago_exitoso_suscripcion.html')

    return render(request, 'pedidos/pago_verificando_suscripcion.html', {'pago': pago})


@csrf_exempt
@never_cache
def wompi_webhook_view(request):
    if request.method != 'POST':
        return JsonResponse({'status': 'error', 'msg': 'Método no permitido'}, status=405)

    raw_body = request.body

    try:
        data = json.loads(raw_body.decode('utf-8'))
        transaccion = _extraer_transaccion_wompi(data)

        referencia = _get_any(
            transaccion,
            'identificadorEnlaceComercio', 'IdentificadorEnlaceComercio',
            'referencia', 'Referencia'
        ) or _get_any(data, 'identificadorEnlaceComercio', 'IdentificadorEnlaceComercio')

        tipo_pago = _wompi_tipo_desde_referencia(referencia)

        if not _validar_hash_webhook_wompi(
            request,
            referencia=referencia,
            tipo_pago=tipo_pago,
            raw_body=raw_body
        ):
            return JsonResponse({'status': 'error', 'msg': 'Webhook no autorizado'}, status=403)

        es_aprobada = _valor_bool_wompi(
            _get_any(transaccion, 'esAprobada', 'EsAprobada', 'approved', 'status'))
        id_transaccion = _get_any(
            transaccion, 'idTransaccion', 'IdTransaccion', 'id', 'Id')
        monto = _get_any(transaccion, 'monto', 'Monto') or _get_any(
            data, 'monto', 'Monto')

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
            pago.ultimo_error = 'Webhook recibido, pero la transacción no venía aprobada.'
            pago.save()

        return JsonResponse({'status': 'ok', 'msg': 'Webhook recibido'})

    except Exception as e:
        print(f'Error webhook Wompi: {e}')
        return JsonResponse({'status': 'error', 'msg': 'Error interno'}, status=500)
