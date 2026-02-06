import hashlib
import json
import urllib.request
import requests 
from datetime import datetime, date, timedelta
from django.shortcuts import render, redirect, get_object_or_404
from .models import Categoria, Producto, Pedido, DetallePedido, Cliente, ConfiguracionNegocio, DiaEspecial, OpcionProducto, Extra
from django.db import transaction
from django.contrib import messages
from decouple import config
from django.contrib.auth.decorators import login_required, user_passes_test
from django.views.decorators.cache import never_cache
from django.contrib.auth.views import LoginView
from django.contrib.auth import logout
from django.http import JsonResponse 
from django.template.loader import render_to_string 
from django.db.models import Sum, Count, F, Q
from django.core.exceptions import PermissionDenied

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

def logout_view(request):
    logout(request)
    return redirect('login_custom')

def es_admin(user):
    return user.groups.filter(name='Administradores').exists() or user.is_superuser

def es_repartidor(user):
    return user.groups.filter(name='Repartidores').exists()

# --- EL GUARDIA DE SEGURIDAD (VALIDACIÓN ESTRICTA) ---
def suscripcion_activa():
    """
    Retorna True si está al día.
    Retorna False si venció.
    """
    config_negocio = ConfiguracionNegocio.objects.first()
    if not config_negocio: return True 
    
    hoy = date.today()
    vencimiento = config_negocio.fecha_vencimiento

    # --- DEBUG EN CONSOLA (MIRA ESTO EN TU TERMINAL) ---
    print(f"🔍 VERIFICANDO SUSCRIPCIÓN: Hoy={hoy} vs Vence={vencimiento}")

    # Si tiene fecha y la fecha de hoy es MAYOR o IGUAL al vencimiento, CORTAMOS.
    # Ejemplo: Si vence el 05/02 y hoy es 05/02 -> BLOQUEADO.
    if vencimiento and hoy >= vencimiento:
        print("❌ ESTADO: VENCIDO (BLOQUEANDO ACCESO)")
        return False
    
    print("✅ ESTADO: ACTIVO")
    return True

# --- CEREBRO DEL TIEMPO ---

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
            if excepcion.hora_apertura: apertura_efectiva = excepcion.hora_apertura
            if excepcion.hora_cierre: cierre_efectivo = excepcion.hora_cierre
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


# --- VISTAS PÚBLICAS (AQUÍ ESTÁ EL BLOQUEO VISUAL) ---

def menu_view(request):
    # 1. ¿PAGÓ? SI NO, AFUERA.
    if not suscripcion_activa():
        # Renderiza la pantalla de bloqueo EN LUGAR del menú
        return render(request, 'pedidos/suspendido.html')

    # Si pasa el filtro, carga todo normal
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

def cart_add(request, producto_id):
    # SEGURIDAD: Bloqueo de acciones
    if not suscripcion_activa():
        return render(request, 'pedidos/suspendido.html')

    cart = request.session.get('cart', {})
    producto = get_object_or_404(Producto, id=producto_id)
    opcion_id = request.POST.get('opcion_id')
    extras_ids = request.POST.getlist('extras') 
    
    key_parts = [str(producto_id)]
    key_parts.append(str(opcion_id) if opcion_id else "0")
    if extras_ids:
        extras_ids.sort()
        key_parts.append(",".join(extras_ids))
    else:
        key_parts.append("0")

    key = "-".join(key_parts)
    
    if key in cart:
        cart[key] += 1
    else:
        cart[key] = 1
    
    request.session['cart'] = cart
    request.session.modified = True
    
    nombre_mostrar = producto.nombre
    if opcion_id:
        try:
            opcion = OpcionProducto.objects.get(id=opcion_id)
            nombre_mostrar += f" ({opcion.nombre})"
        except: pass
    
    if extras_ids:
        nombre_mostrar += " + Extras"

    messages.success(request, f"¡{nombre_mostrar} agregado!")
    return redirect(request.META.get('HTTP_REFERER', 'menu'))

def cart_clear(request):
    request.session['cart'] = {}
    request.session.modified = True
    messages.info(request, "Tu carrito ha sido vaciado.")
    return redirect('menu')

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

def checkout_view(request):
    # SEGURIDAD EXTREMA
    if not suscripcion_activa():
        return render(request, 'pedidos/suspendido.html')

    abierto, mensaje = verificar_estado_negocio()
    if not abierto:
        messages.error(request, f"⛔ El restaurante ha cerrado. {mensaje}")
        return redirect('menu')
    cart = request.session.get('cart', {})
    
    if request.method == 'POST':
        telefono = request.POST.get('telefono')
        nombre = request.POST.get('nombre')
        apellido = request.POST.get('apellido')
        direccion = request.POST.get('direccion')
        metodo_pago = request.POST.get('metodo_pago')
        lat = request.POST.get('latitud')
        lng = request.POST.get('longitud')

        if not cart:
             messages.error(request, "El carrito está vacío.")
             return redirect('menu')

        if not telefono or len(telefono) < 8:
            messages.error(request, "Revisa tu teléfono.")
            return redirect('checkout')

        try:
            with transaction.atomic():
                cliente, created = Cliente.objects.get_or_create(
                    telefono=telefono,
                    defaults={'nombre': nombre, 'apellido': apellido, 'direccion_ultima': direccion}
                )
                if not created:
                    cliente.nombre = nombre
                    cliente.apellido = apellido
                    cliente.direccion_ultima = direccion
                    cliente.save()

                estado_inicial = 'PENDIENTE' if metodo_pago == 'TARJETA' else 'RECIBIDO'

                pedido = Pedido.objects.create(
                    cliente=cliente, direccion_entrega=direccion, metodo_pago=metodo_pago,
                    latitud=lat, longitud=lng, es_pedido_whatsapp=False,
                    estado=estado_inicial
                )

                for key, cantidad in cart.items():
                    parts = key.split('-')
                    prod_id = parts[0]
                    opc_id = parts[1] if len(parts) > 1 else "0"
                    extras_str = parts[2] if len(parts) > 2 else "0"

                    producto = get_object_or_404(Producto, id=prod_id)
                    opcion = None
                    if opc_id != "0":
                        opcion = OpcionProducto.objects.filter(id=opc_id).first()
                    
                    detalle = DetallePedido.objects.create(
                        pedido=pedido, producto=producto, cantidad=cantidad, 
                        precio_unitario=producto.precio, opcion=opcion
                    )

                    if extras_str != "0":
                        ids_ext = extras_str.split(',')
                        for eid in ids_ext:
                            extra_obj = Extra.objects.filter(id=eid).first()
                            if extra_obj:
                                detalle.extras.add(extra_obj)
                    detalle.save()
                
                pedido.save()
                
                request.session['ultimo_pedido_id'] = pedido.id
                historial = request.session.get('historial_pedidos', [])
                if pedido.id not in historial:
                    historial.append(pedido.id)
                request.session['historial_pedidos'] = historial

                request.session['cart'] = {}
                request.session.modified = True
                
                if metodo_pago == 'TARJETA':
                    return redirect('pagar_wompi', pedido_id=pedido.id)
                else:
                    return redirect('order_tracker', pedido_id=pedido.id)
                
        except Exception as e:
            messages.error(request, f"Error procesando: {e}")
            return redirect('checkout')

    productos_en_carrito = []
    total_productos = 0
    for key, cantidad in cart.items():
        parts = key.split('-')
        prod_id = parts[0]
        opc_id = parts[1] if len(parts) > 1 else "0"
        extras_str = parts[2] if len(parts) > 2 else "0"
        
        producto = get_object_or_404(Producto, id=prod_id)
        precio_item = producto.precio
        nombre_opcion = ""
        opcion = None
        if opc_id != "0":
            opcion = OpcionProducto.objects.filter(id=opc_id).first()
            if opcion:
                precio_item += opcion.precio_extra
                nombre_opcion = opcion.nombre
        
        lista_extras = []
        if extras_str != "0":
            ids_ext = extras_str.split(',')
            extras_objs = Extra.objects.filter(id__in=ids_ext)
            for ex in extras_objs:
                precio_item += ex.precio
                lista_extras.append(ex)

        subtotal = precio_item * cantidad
        total_productos += subtotal
        productos_en_carrito.append({
            'producto': producto, 'cantidad': cantidad, 'subtotal': subtotal,
            'opcion': opcion, 'nombre_opcion': nombre_opcion, 'lista_extras': lista_extras, 'key': key 
        })
    
    context = {
        'items': productos_en_carrito, 'total_productos': total_productos,
        'total_wompi': float(total_productos) * 1.05,
        'GOOGLE_MAPS_API_KEY': config('GOOGLE_MAPS_API_KEY', default=''),
    }
    return render(request, 'pedidos/checkout.html', context)

# --- VISTAS DE PAGO WOMPI ---

def pagar_wompi_view(request, pedido_id):
    pedido = get_object_or_404(Pedido, id=pedido_id)
    CLIENT_ID = config('WOMPI_APP_ID')
    CLIENT_SECRET = config('WOMPI_API_SECRET')
    AUTH_URL = config('WOMPI_AUTH_URL', default='https://id.wompi.sv/connect/token')
    API_URL = config('WOMPI_API_URL', default='https://api.wompi.sv/EnlacePago')

    headers_seguridad = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
        'Accept': 'application/json'
    }

    try:
        auth_payload = {
            'grant_type': 'client_credentials', 'client_id': CLIENT_ID,
            'client_secret': CLIENT_SECRET, 'audience': 'wompi_api'
        }
        auth_response = requests.post(AUTH_URL, data=auth_payload, headers=headers_seguridad)
        if auth_response.status_code != 200:
            messages.error(request, "Error de comunicación con el Banco (Auth).")
            return redirect('menu')
            
        access_token = auth_response.json().get('access_token')
        headers_api = {
            'Authorization': f'Bearer {access_token}',
            'Content-Type': 'application/json',
            'User-Agent': headers_seguridad['User-Agent']
        }
        base_url = request.build_absolute_uri('/')[:-1] 
        redirect_url = f"{base_url}/wompi-respuesta/?pedido_ref={pedido.id}"
        
        payment_payload = {
            "IdentificadorEnlaceComercio": f"ORDEN-{pedido.id}",
            "Monto": float(pedido.total_final),
            "NombreProducto": f"FoodBack Pedido #{pedido.id}",
            "FormaPago": {
                "PermitirTarjetaCreditoDebito": True, "PermitirTarjetaCreditoDebido": True, "PermitirPagoConPuntoAgricola": True
            },
            "Configuracion": {
                "UrlRedirect": redirect_url, "EsMontoEditable": False, "EsCantidadEditable": False,
                "EmailsNotificacion": "marlini.aleman2014@gmail.com" 
            }
        }
        link_response = requests.post(API_URL, json=payment_payload, headers=headers_api)
        if link_response.status_code == 200:
            data = link_response.json()
            return redirect(data.get('urlEnlace'))
        else:
            messages.error(request, "El banco rechazó la solicitud de enlace.")
            return redirect('menu')
    except Exception as e:
        messages.error(request, "Error interno de conexión.")
        return redirect('menu')

def wompi_respuesta_view(request):
    pedido_ref = request.GET.get('pedido_ref')
    id_transaccion = request.GET.get('idTransaccion') 
    if not pedido_ref:
        messages.error(request, "Referencia de pedido perdida.")
        return redirect('menu')
    pedido = get_object_or_404(Pedido, id=pedido_ref)
    
    request.session['ultimo_pedido_id'] = pedido.id
    historial = request.session.get('historial_pedidos', [])
    if pedido.id not in historial: historial.append(pedido.id)
    request.session['historial_pedidos'] = historial

    if id_transaccion:
        if pedido.estado == 'PENDIENTE':
            pedido.estado = 'RECIBIDO'
            pedido.save()
        messages.success(request, f"¡Pago Confirmado! Ref: {id_transaccion[:8]}")
        return redirect('order_tracker', pedido_id=pedido.id)
    else:
        messages.error(request, "No se recibió ID de transacción.")
        return redirect('menu')

def pedido_exito_view(request, pedido_id):
    return redirect('order_tracker', pedido_id=pedido_id)

# --- DASHBOARDS PROTEGIDOS ---

@never_cache
@login_required(login_url='login_custom')
@user_passes_test(es_admin, login_url='login_custom') 
def dashboard_admin_view(request):
    # VERIFICACIÓN DE SUSCRIPCIÓN ESTRICTA
    config_negocio = ConfiguracionNegocio.objects.first()
    dias_restantes = 30
    bloqueado = False
    
    if config_negocio and config_negocio.fecha_vencimiento:
        # Aquí usamos lógica < 0 para el overlay del admin (que permite ver pero no tocar)
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
        
    pedidos = Pedido.objects.exclude(estado__in=['ENTREGADO', 'CANCELADO']).order_by('-id')
    return render(request, 'pedidos/dashboard_admin.html', {
        'pedidos': pedidos,
        'dias_restantes': dias_restantes 
    })

@never_cache
@login_required(login_url='login_custom')
@user_passes_test(es_admin, login_url='login_custom')
def admin_settings_view(request):
    # SEGURIDAD: SI VENCIÓ, FUERA DE AQUÍ
    if not suscripcion_activa():
        messages.error(request, "⛔ Acceso denegado a Configuración. Suscripción vencida.")
        return redirect('dashboard_admin') 

    DiaEspecial.objects.filter(fecha__lt=date.today()).delete()
    config_negocio = ConfiguracionNegocio.objects.first()
    if not config_negocio:
        config_negocio = ConfiguracionNegocio.objects.create()

    if request.method == 'POST':
        tipo_accion = request.POST.get('tipo_accion')

        if tipo_accion == 'global':
            config_negocio.hora_apertura = request.POST.get('hora_apertura')
            config_negocio.hora_cierre = request.POST.get('hora_cierre')
            config_negocio.mensaje_cierre = request.POST.get('mensaje_cierre')
            
            dias_map = ['lunes', 'martes', 'miercoles', 'jueves', 'viernes', 'sabado', 'domingo']
            for d in dias_map:
                valor = request.POST.get(f'{d}_abierto') == 'on'
                setattr(config_negocio, f'{d}_abierto', valor)
            
            config_negocio.save()
            messages.success(request, "Configuración global actualizada (Excepciones mantenidas) ⚙️")
            return redirect('admin_settings')

        elif tipo_accion == 'dia_especifico':
            fecha_str = request.POST.get('fecha_target') 
            if not fecha_str:
                 messages.error(request, "Error: No se recibió la fecha.")
                 return redirect('admin_settings')

            fecha_dt = datetime.strptime(fecha_str, '%Y-%m-%d').date()
            excepcion, created = DiaEspecial.objects.get_or_create(fecha=fecha_dt)
            excepcion.abierto = request.POST.get('estado_dia') == 'on'
            h_ap = request.POST.get('hora_apertura_dia')
            h_ci = request.POST.get('hora_cierre_dia')
            excepcion.hora_apertura = h_ap if h_ap else None
            excepcion.hora_cierre = h_ci if h_ci else None
            excepcion.motivo = request.POST.get('motivo')
            excepcion.save()
            messages.success(request, f"Horario para {fecha_str} actualizado ✅")
            return redirect('admin_settings')

    agenda = []
    hoy = date.today()
    nombres_dias = ['Lunes', 'Martes', 'Miércoles', 'Jueves', 'Viernes', 'Sábado', 'Domingo']
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
            if excepcion.hora_apertura: h_inicio = excepcion.hora_apertura
            if excepcion.hora_cierre: h_fin = excepcion.hora_cierre
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
def eliminar_excepcion_view(request, excepcion_id):
    if not suscripcion_activa(): 
        messages.error(request, "Acción denegada. Suscripción vencida.")
        return redirect('dashboard_admin')
        
    excepcion = get_object_or_404(DiaEspecial, id=excepcion_id)
    excepcion.delete()
    messages.info(request, "Excepción eliminada 🗑️")
    return redirect('admin_settings')

@login_required(login_url='login_custom')
@user_passes_test(es_repartidor, login_url='login_custom')
def dashboard_delivery_view(request):
    disponibles = Pedido.objects.filter(estado='RUTA', repartidor=None).order_by('id')
    mis_pedidos = Pedido.objects.filter(estado='RUTA', repartidor=request.user).order_by('id')
    if request.method == 'POST':
        pedido = get_object_or_404(Pedido, id=request.POST.get('pedido_id'))
        accion = request.POST.get('accion')
        if accion == 'tomar':
            pedido.repartidor = request.user
            pedido.save()
        elif accion == 'entregado':
            pedido.estado = 'ENTREGADO'
            pedido.save()
        elif accion == 'problema':
            pedido.estado = 'PROBLEMA'
            pedido.repartidor = None
            pedido.save()
        return redirect('dashboard_delivery')
    return render(request, 'pedidos/dashboard_delivery.html', {'disponibles': disponibles, 'mis_pedidos': mis_pedidos, 'GOOGLE_MAPS_API_KEY': config('GOOGLE_MAPS_API_KEY', default='')})

def obtener_ubicacion_ip(request):
    x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    if x_forwarded_for: ip = x_forwarded_for.split(',')[0]
    else: ip = request.META.get('REMOTE_ADDR')
    if ip == '127.0.0.1': pass 
    try:
        with urllib.request.urlopen(f"http://ip-api.com/json/{ip}") as url:
            data = json.loads(url.read().decode())
            if data.get('status') == 'success' and data.get('countryCode') == 'SV':
                return JsonResponse({'status': 'ok', 'lat': data['lat'], 'lng': data['lon'], 'city': data['city']})
    except: pass
    return JsonResponse({'status': 'error', 'lat': 13.6929, 'lng': -89.2182})

def order_tracker_view(request, pedido_id):
    pedido = get_object_or_404(Pedido, id=pedido_id)
    return render(request, 'pedidos/order_tracker.html', {'pedido': pedido})

def api_order_status(request, pedido_id):
    try:
        pedido = Pedido.objects.get(id=pedido_id)
        return JsonResponse({'status': 'ok', 'estado_codigo': pedido.estado, 'estado_texto': pedido.get_estado_display()})
    except Pedido.DoesNotExist:
        return JsonResponse({'status': 'error', 'msg': 'Pedido no encontrado'}, status=404)

@never_cache 
@login_required(login_url='login_custom')
@user_passes_test(es_admin, login_url='login_custom')
def dashboard_metrics_view(request):
    if not suscripcion_activa():
        messages.error(request, "⛔ Acceso denegado a Finanzas. Suscripción vencida.")
        return redirect('dashboard_admin')

    hoy = datetime.now().date()
    pedidos_validos_hoy = Pedido.objects.filter(fecha_creacion__date=hoy).exclude(estado__in=['CANCELADO', 'PENDIENTE'])
    
    resumen = pedidos_validos_hoy.aggregate(
        total_general=Sum('total_final'),
        total_efectivo=Sum('total_final', filter=Q(metodo_pago='EFECTIVO')),
        total_wompi=Sum('total_final', filter=Q(metodo_pago='TARJETA'))
    )
    total_ventas_hoy = resumen['total_general'] or 0
    dinero_en_caja = resumen['total_efectivo'] or 0
    dinero_banco = resumen['total_wompi'] or 0
    cantidad_pedidos_hoy = pedidos_validos_hoy.count()
    ticket_promedio = total_ventas_hoy / cantidad_pedidos_hoy if cantidad_pedidos_hoy > 0 else 0

    fechas_grafica, montos_grafica = [], []
    for i in range(6, -1, -1): 
        fecha = hoy - timedelta(days=i)
        venta_dia = Pedido.objects.filter(fecha_creacion__date=fecha).exclude(estado__in=['CANCELADO', 'PENDIENTE']).aggregate(Sum('total_final'))['total_final__sum'] or 0
        nombres_dias = ['Lun', 'Mar', 'Mié', 'Jue', 'Vie', 'Sáb', 'Dom']
        fechas_grafica.append(f"{nombres_dias[fecha.weekday()]} {fecha.day}")
        montos_grafica.append(float(venta_dia))

    top_productos = DetallePedido.objects.filter(pedido__estado__in=['RECIBIDO', 'COCINA', 'RUTA', 'ENTREGADO']).values('producto__nombre').annotate(total_vendido=Sum('cantidad'), dinero_generado=Sum('subtotal')).order_by('-total_vendido')[:5]

    context = {
        'total_ventas_hoy': total_ventas_hoy, 'dinero_en_caja': dinero_en_caja, 'dinero_banco': dinero_banco,
        'cantidad_pedidos_hoy': cantidad_pedidos_hoy, 'ticket_promedio': ticket_promedio,
        'fechas_grafica': json.dumps(fechas_grafica), 'montos_grafica': json.dumps(montos_grafica),
        'top_productos': top_productos,
    }
    return render(request, 'pedidos/dashboard_metrics.html', context)

def perfil_usuario_view(request):
    ids_historial = request.session.get('historial_pedidos', [])
    mis_pedidos = Pedido.objects.filter(id__in=ids_historial).order_by('-id')
    activos = mis_pedidos.exclude(estado__in=['ENTREGADO', 'CANCELADO'])
    historial = mis_pedidos.filter(estado__in=['ENTREGADO', 'CANCELADO'])
    return render(request, 'pedidos/perfil.html', {'activos': activos, 'historial': historial})