import hashlib
import json
import urllib.request
import requests 
from datetime import datetime, date, timedelta
from django.shortcuts import render, redirect, get_object_or_404
# AGREGADO: Importamos el modelo Extra
from .models import Categoria, Producto, Pedido, DetallePedido, Cliente, ConfiguracionNegocio, DiaEspecial, OpcionProducto, Extra
from django.db import transaction
from django.contrib import messages
from decouple import config
from django.contrib.auth.decorators import login_required, user_passes_test
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

# --- CEREBRO DEL TIEMPO (LÓGICA DE APERTURA) ---

def verificar_estado_negocio():
    """
    Devuelve (esta_abierto: bool, mensaje: str)
    Maneja cruce de medianoche y jerarquía de excepciones.
    """
    ahora = datetime.now()
    fecha_hoy = ahora.date()
    hora_actual = ahora.time()
    dia_semana = ahora.weekday() 

    # 1. Cargar Configuración Global
    config = ConfiguracionNegocio.objects.first()
    if not config:
        config = ConfiguracionNegocio.objects.create()

    # Valores por defecto (Globales)
    apertura_efectiva = config.hora_apertura
    cierre_efectivo = config.hora_cierre
    mensaje_base = config.mensaje_cierre
    
    # Determinamos si hoy debería estar abierto según GLOBAL
    dias_globales = [
        config.lunes_abierto, config.martes_abierto, config.miercoles_abierto,
        config.jueves_abierto, config.viernes_abierto, config.sabado_abierto,
        config.domingo_abierto
    ]
    esta_habilitado = dias_globales[dia_semana]

    # 2. ¿Hay una EXCEPCIÓN (Tarjeta Individual) para hoy?
    excepcion = DiaEspecial.objects.filter(fecha=fecha_hoy).first()
    
    if excepcion:
        # LA TARJETA MANDA (Sobrescribe todo lo global)
        if excepcion.abierto:
            esta_habilitado = True
            # Si la tarjeta tiene horas específicas, las usamos. Si no, quedan las globales.
            if excepcion.hora_apertura: apertura_efectiva = excepcion.hora_apertura
            if excepcion.hora_cierre: cierre_efectivo = excepcion.hora_cierre
        else:
            # Tarjeta dice cerrado explícitamente
            motivo = excepcion.motivo or ""
            return False, f"{mensaje_base} ({motivo})"

    # 3. SI EL DÍA ESTÁ HABILITADO, VERIFICAMOS LA HORA (Con lógica de Medianoche)
    if esta_habilitado:
        # Caso A: Horario Normal (Ej: 8am a 8pm)
        if apertura_efectiva < cierre_efectivo:
            if apertura_efectiva <= hora_actual <= cierre_efectivo:
                return True, ""
        
        # Caso B: Cruza Medianoche (Ej: 8am a 2am del día siguiente)
        # O cierra a las 00:00 exactas
        else:
            # Si cierra a las 00:00 o la hora de cierre es menor que apertura (madrugada)
            if hora_actual >= apertura_efectiva or hora_actual <= cierre_efectivo:
                return True, ""

        # Si falló la validación de hora:
        ap_str = apertura_efectiva.strftime('%I:%M %p').lower()
        ci_str = cierre_efectivo.strftime('%I:%M %p').lower()
        return False, f"{mensaje_base} (Hoy: {ap_str} - {ci_str})"
    
    # Si el día no está habilitado
    return False, mensaje_base


# --- VISTAS PÚBLICAS ---

def menu_view(request):
    categorias = Categoria.objects.all().order_by('orden')
    cart = request.session.get('cart', {})
    cantidad_total = sum(cart.values())
    
    # Consultamos al Cerebro
    abierto, mensaje_estado = verificar_estado_negocio()

    # --- LÓGICA DE MEMORIA (RADAR) ---
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
    # -------------------------

    return render(request, 'pedidos/menu.html', {
        'categorias': categorias, 
        'cantidad_carrito': cantidad_total,
        'abierto': abierto,
        'mensaje_estado': mensaje_estado,
        'ultimo_pedido_activo': ultimo_pedido_activo
    })

# --- LÓGICA DEL CARRITO (MODIFICADA PARA EXTRAS) ---

def cart_add(request, producto_id):
    cart = request.session.get('cart', {})
    producto = get_object_or_404(Producto, id=producto_id)
    
    # 1. Obtener Opción (Pollo/Res)
    opcion_id = request.POST.get('opcion_id')
    
    # 2. Obtener Extras (Lista de IDs del checkbox)
    extras_ids = request.POST.getlist('extras') 
    
    # 3. GENERAR LA LLAVE ÚNICA DEL CARRITO
    # Formato: "ProductoID-OpcionID-ExtrasString"
    key_parts = [str(producto_id)]
    
    # Parte Opcion
    key_parts.append(str(opcion_id) if opcion_id else "0")
    
    # Parte Extras
    if extras_ids:
        extras_ids.sort() # Ordenar para evitar duplicados visuales (1,2 es igual a 2,1)
        key_parts.append(",".join(extras_ids))
    else:
        key_parts.append("0")

    key = "-".join(key_parts)
    
    # 4. Guardar en sesión
    if key in cart:
        cart[key] += 1
    else:
        cart[key] = 1
    
    request.session['cart'] = cart
    request.session.modified = True
    
    # Mensaje bonito
    nombre_mostrar = producto.nombre
    if opcion_id:
        try:
            opcion = OpcionProducto.objects.get(id=opcion_id)
            nombre_mostrar += f" ({opcion.nombre})"
        except: pass
    
    if extras_ids:
        nombre_mostrar += " + Extras"

    messages.success(request, f"¡{nombre_mostrar} agregado!")
    
    # Redirigir a la misma página sin perder scroll
    return redirect(request.META.get('HTTP_REFERER', 'menu'))

def cart_clear(request):
    request.session['cart'] = {}
    request.session.modified = True
    messages.info(request, "Tu carrito ha sido vaciado.")
    return redirect('menu')

def eliminar_item_carrito(request, producto_id):
    cart = request.session.get('cart', {}) 
    # producto_id aquí es la KEY completa (ej: "5-2-8,9")
    key_to_delete = str(producto_id)

    if key_to_delete in cart:
        del cart[key_to_delete]
        request.session['cart'] = cart
        request.session.modified = True
    
    # Lógica AJAX Actualizada con soporte para Extras
    if request.headers.get('x-requested-with') == 'XMLHttpRequest':
        productos_en_carrito = []
        total_productos = 0
        
        for key, cantidad in cart.items():
            # Descomponer la llave: Prod-Opc-Extras
            parts = key.split('-')
            prod_id = parts[0]
            opc_id = parts[1] if len(parts) > 1 else "0"
            extras_str = parts[2] if len(parts) > 2 else "0"
            
            producto = get_object_or_404(Producto, id=prod_id)
            
            # Calcular Precio Unitario Real
            precio_item = producto.precio
            
            # Sumar Opción
            opcion = None
            if opc_id != "0":
                opcion = OpcionProducto.objects.filter(id=opc_id).first()
                if opcion:
                    precio_item += opcion.precio_extra
            
            # Sumar Extras
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

                # --- GUARDADO EN BD (CON EXTRAS) ---
                for key, cantidad in cart.items():
                    # Descomposición de la llave
                    parts = key.split('-')
                    prod_id = parts[0]
                    opc_id = parts[1] if len(parts) > 1 else "0"
                    extras_str = parts[2] if len(parts) > 2 else "0"

                    producto = get_object_or_404(Producto, id=prod_id)
                    
                    opcion = None
                    if opc_id != "0":
                        opcion = OpcionProducto.objects.filter(id=opc_id).first()
                    
                    # 1. Crear el detalle base
                    detalle = DetallePedido.objects.create(
                        pedido=pedido, 
                        producto=producto, 
                        cantidad=cantidad, 
                        precio_unitario=producto.precio,
                        opcion=opcion
                    )

                    # 2. Asignar Extras (Many-to-Many)
                    if extras_str != "0":
                        ids_ext = extras_str.split(',')
                        for eid in ids_ext:
                            extra_obj = Extra.objects.filter(id=eid).first()
                            if extra_obj:
                                detalle.extras.add(extra_obj)
                    
                    # 3. Guardar para que se actualicen los precios en el modelo
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

    # --- LÓGICA VISUAL PARA EL RESUMEN (CON EXTRAS) ---
    productos_en_carrito = []
    total_productos = 0
    
    for key, cantidad in cart.items():
        parts = key.split('-')
        prod_id = parts[0]
        opc_id = parts[1] if len(parts) > 1 else "0"
        extras_str = parts[2] if len(parts) > 2 else "0"
        
        producto = get_object_or_404(Producto, id=prod_id)
        
        # Precio Base
        precio_item = producto.precio
        nombre_opcion = ""
        
        # + Precio Opción
        opcion = None
        if opc_id != "0":
            opcion = OpcionProducto.objects.filter(id=opc_id).first()
            if opcion:
                precio_item += opcion.precio_extra
                nombre_opcion = opcion.nombre
        
        # + Precio Extras
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
            'producto': producto, 
            'cantidad': cantidad, 
            'subtotal': subtotal,
            'opcion': opcion,
            'nombre_opcion': nombre_opcion, # Para usar fácil en el template
            'lista_extras': lista_extras,   # Pasamos la lista de objetos Extra
            'key': key 
        })
    # ------------------------------------------------------
    
    context = {
        'items': productos_en_carrito,
        'total_productos': total_productos,
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
            'grant_type': 'client_credentials',
            'client_id': CLIENT_ID,
            'client_secret': CLIENT_SECRET,
            'audience': 'wompi_api'
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
                "PermitirTarjetaCreditoDebito": True,
                "PermitirTarjetaCreditoDebido": True, 
                "PermitirPagoConPuntoAgricola": True
            },
            "Configuracion": {
                "UrlRedirect": redirect_url,
                "EsMontoEditable": False,
                "EsCantidadEditable": False,
                "EmailsNotificacion": "marlini.aleman2014@gmail.com" 
            }
        }

        link_response = requests.post(API_URL, json=payment_payload, headers=headers_api)
        
        if link_response.status_code == 200:
            data = link_response.json()
            return redirect(data.get('urlEnlace'))
        else:
            print(f"Error Link Wompi (Status {link_response.status_code}): {link_response.text}")
            messages.error(request, "El banco rechazó la solicitud de enlace.")
            return redirect('menu')

    except Exception as e:
        print(f"Error Crítico Wompi: {e}")
        messages.error(request, "Error interno de conexión.")
        return redirect('menu')

def wompi_respuesta_view(request):
    pedido_ref = request.GET.get('pedido_ref')
    id_transaccion = request.GET.get('idTransaccion') 
    
    print(f"📡 Wompi Retorno -> Pedido: {pedido_ref}, Transaccion: {id_transaccion}")

    if not pedido_ref or not id_transaccion:
        messages.error(request, "Datos de pago incompletos.")
        return redirect('menu')

    pedido = get_object_or_404(Pedido, id=pedido_ref)

    # --- 1. MEMORIA DEL PERFIL ---
    request.session['ultimo_pedido_id'] = pedido.id
    historial = request.session.get('historial_pedidos', [])
    if pedido.id not in historial:
        historial.append(pedido.id)
    request.session['historial_pedidos'] = historial
    # -----------------------------

    # --- 2. VALIDACIÓN DE SEGURIDAD (SERVER-TO-SERVER) ---
    # No confiamos en la URL, preguntamos a Wompi directamente.
    
    CLIENT_ID = config('WOMPI_APP_ID')
    CLIENT_SECRET = config('WOMPI_API_SECRET')
    AUTH_URL = config('WOMPI_AUTH_URL', default='https://id.wompi.sv/connect/token')
    
    # URL para consultar la transacción específica (Ajusta si la doc de Wompi SV indica otra ruta)
    # Generalmente es: https://api.wompi.sv/Transacciones/{id}
    VALIDATION_URL = f"https://api.wompi.sv/Transacciones/{id_transaccion}"

    try:
        # A) Obtener Token de Acceso (Igual que al pagar)
        auth_payload = {
            'grant_type': 'client_credentials',
            'client_id': CLIENT_ID,
            'client_secret': CLIENT_SECRET,
            'audience': 'wompi_api'
        }
        auth_response = requests.post(AUTH_URL, data=auth_payload)
        
        if auth_response.status_code != 200:
            raise Exception("Error autenticando con Wompi para validación.")
            
        token = auth_response.json().get('access_token')
        
        # B) Consultar el estado REAL de la transacción
        headers = { 'Authorization': f'Bearer {token}' }
        validation_response = requests.get(VALIDATION_URL, headers=headers)
        
        if validation_response.status_code != 200:
            raise Exception("No se encontró la transacción en Wompi.")
            
        data_wompi = validation_response.json()
        
        # C) Verificar si Wompi dice que es verdadera y aprobada
        # Wompi suele devolver un campo 'esAprobada': true o false
        es_realmente_aprobada = data_wompi.get('esAprobada') == True
        
        if es_realmente_aprobada:
            if pedido.estado == 'PENDIENTE':
                pedido.estado = 'RECIBIDO'
                pedido.save()
            messages.success(request, f"¡Pago Verificado! Ref: {id_transaccion[:8]}")
            return redirect('order_tracker', pedido_id=pedido.id)
        else:
            messages.error(request, "El pago no fue aprobado por el banco.")
            return redirect('menu')

    except Exception as e:
        print(f"⚠️ Alerta de Seguridad o Error Wompi: {e}")
        messages.error(request, "No pudimos verificar el pago. Contacta soporte.")
        return redirect('menu')

def pedido_exito_view(request, pedido_id):
    return redirect('order_tracker', pedido_id=pedido_id)

# --- DASHBOARDS PROTEGIDOS ---

@login_required(login_url='login_custom')
@user_passes_test(es_admin, login_url='login_custom') 
def dashboard_admin_view(request):
    pedidos = Pedido.objects.exclude(estado__in=['ENTREGADO', 'CANCELADO']).order_by('-id')
    
    if request.method == 'POST':
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
        
    return render(request, 'pedidos/dashboard_admin.html', {'pedidos': pedidos})

# --- VISTA DE CONFIGURACIÓN (EL PANEL DE CONTROL DEL TIEMPO) ---

@login_required(login_url='login_custom')
@user_passes_test(es_admin, login_url='login_custom')
def admin_settings_view(request):
    config_negocio = ConfiguracionNegocio.objects.first()
    if not config_negocio:
        config_negocio = ConfiguracionNegocio.objects.create()

    if request.method == 'POST':
        tipo_accion = request.POST.get('tipo_accion')

        # 1. Configuración Global
        if tipo_accion == 'global':
            config_negocio.hora_apertura = request.POST.get('hora_apertura')
            config_negocio.hora_cierre = request.POST.get('hora_cierre')
            config_negocio.mensaje_cierre = request.POST.get('mensaje_cierre')
            
            dias_map = ['lunes', 'martes', 'miercoles', 'jueves', 'viernes', 'sabado', 'domingo']
            for d in dias_map:
                valor = request.POST.get(f'{d}_abierto') == 'on'
                setattr(config_negocio, f'{d}_abierto', valor)
            
            config_negocio.save()

            # --- SINCRONIZACIÓN ELIMINADA (Para respetar excepciones) ---
            # Ya no sobrescribimos DiaEspecial aquí.
            
            messages.success(request, "Configuración global actualizada (Excepciones mantenidas) ⚙️")
            return redirect('admin_settings')

        # 2. Configuración Individual (Tarjetas)
        elif tipo_accion == 'dia_especifico':
            fecha_str = request.POST.get('fecha_target') 
            
            if not fecha_str:
                 messages.error(request, "Error: No se recibió la fecha.")
                 return redirect('admin_settings')

            fecha_dt = datetime.strptime(fecha_str, '%Y-%m-%d').date()
            
            excepcion, created = DiaEspecial.objects.get_or_create(fecha=fecha_dt)
            
            # El checkbox en tu HTML se llama 'estado_dia'
            excepcion.abierto = request.POST.get('estado_dia') == 'on'
            
            # Los inputs de hora se llaman 'hora_apertura_dia' y 'hora_cierre_dia'
            h_ap = request.POST.get('hora_apertura_dia')
            h_ci = request.POST.get('hora_cierre_dia')
            
            excepcion.hora_apertura = h_ap if h_ap else None
            excepcion.hora_cierre = h_ci if h_ci else None
            
            excepcion.motivo = request.POST.get('motivo')
            
            excepcion.save()
            messages.success(request, f"Horario para {fecha_str} actualizado ✅")
            return redirect('admin_settings')

    # --- PREPARAR DATOS PARA EL HTML ---
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

    return render(request, 'pedidos/admin_settings.html', {
        'config': config_negocio,
        'agenda': agenda
    })

# -------------------------------------------------------------

@login_required(login_url='login_custom')
@user_passes_test(es_admin, login_url='login_custom')
def eliminar_excepcion_view(request, excepcion_id):
    excepcion = get_object_or_404(DiaEspecial, id=excepcion_id)
    excepcion.delete()
    messages.info(request, "Excepción eliminada 🗑️")
    return redirect('admin_settings')

# -------------------------------------------------------------

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
    if x_forwarded_for:
        ip = x_forwarded_for.split(',')[0]
    else:
        ip = request.META.get('REMOTE_ADDR')
    
    if ip == '127.0.0.1': 
        pass 

    try:
        with urllib.request.urlopen(f"http://ip-api.com/json/{ip}") as url:
            data = json.loads(url.read().decode())
            if data.get('status') == 'success':
                if data.get('countryCode') == 'SV':
                    return JsonResponse({
                        'status': 'ok',
                        'lat': data['lat'],
                        'lng': data['lon'],
                        'city': data['city']
                    })
    except Exception as e:
        print(f"Error GeoIP: {e}")

    return JsonResponse({'status': 'error', 'lat': 13.6929, 'lng': -89.2182})

def order_tracker_view(request, pedido_id):
    pedido = get_object_or_404(Pedido, id=pedido_id)
    return render(request, 'pedidos/order_tracker.html', {'pedido': pedido})

def api_order_status(request, pedido_id):
    try:
        pedido = Pedido.objects.get(id=pedido_id)
        return JsonResponse({
            'status': 'ok',
            'estado_codigo': pedido.estado,
            'estado_texto': pedido.get_estado_display(),
        })
    except Pedido.DoesNotExist:
        return JsonResponse({'status': 'error', 'msg': 'Pedido no encontrado'}, status=404)

# --- CEREBRO FINANCIERO (METRICS) MEJORADO ---

@login_required(login_url='login_custom')
@user_passes_test(es_admin, login_url='login_custom')
def dashboard_metrics_view(request):
    hoy = datetime.now().date()
    
    # Solo pedidos de HOY que NO sean Cancelados NI Pendientes (Dinero real)
    pedidos_validos_hoy = Pedido.objects.filter(
        fecha_creacion__date=hoy
    ).exclude(estado__in=['CANCELADO', 'PENDIENTE'])
    
    # CÁLCULO DE TOTALES
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

    # DATOS PARA LA GRÁFICA (Últimos 7 días)
    fechas_grafica = []
    montos_grafica = []
    
    for i in range(6, -1, -1): 
        fecha = hoy - timedelta(days=i)
        venta_dia = Pedido.objects.filter(
            fecha_creacion__date=fecha
        ).exclude(estado__in=['CANCELADO', 'PENDIENTE']).aggregate(Sum('total_final'))['total_final__sum'] or 0
        
        nombres_dias = ['Lun', 'Mar', 'Mié', 'Jue', 'Vie', 'Sáb', 'Dom']
        nombre_dia = nombres_dias[fecha.weekday()]
        fechas_grafica.append(f"{nombre_dia} {fecha.day}")
        montos_grafica.append(float(venta_dia))

    # TOP PRODUCTOS
    top_productos = DetallePedido.objects.filter(
        pedido__estado__in=['RECIBIDO', 'COCINA', 'RUTA', 'ENTREGADO']
    ).values('producto__nombre').annotate(
        total_vendido=Sum('cantidad'),
        dinero_generado=Sum('subtotal')
    ).order_by('-total_vendido')[:5]

    context = {
        'total_ventas_hoy': total_ventas_hoy,
        'dinero_en_caja': dinero_en_caja,
        'dinero_banco': dinero_banco,
        'cantidad_pedidos_hoy': cantidad_pedidos_hoy,
        'ticket_promedio': ticket_promedio,
        'fechas_grafica': json.dumps(fechas_grafica),
        'montos_grafica': json.dumps(montos_grafica),
        'top_productos': top_productos,
    }
    return render(request, 'pedidos/dashboard_metrics.html', context)

# --- PERFIL DE USUARIO (MIS PEDIDOS) ---

def perfil_usuario_view(request):
    # Recuperar lista de IDs de la sesión
    ids_historial = request.session.get('historial_pedidos', [])
    
    # Buscar pedidos en BD
    mis_pedidos = Pedido.objects.filter(id__in=ids_historial).order_by('-id')
    
    activos = mis_pedidos.exclude(estado__in=['ENTREGADO', 'CANCELADO'])
    historial = mis_pedidos.filter(estado__in=['ENTREGADO', 'CANCELADO'])

    return render(request, 'pedidos/perfil.html', {
        'activos': activos,
        'historial': historial
    })