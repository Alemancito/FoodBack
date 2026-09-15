import json
from unittest.mock import patch

import hashlib

from django.conf import settings

from concurrent.futures import (
    ThreadPoolExecutor,
)
from threading import Barrier

from datetime import date, timedelta, time
from decimal import Decimal

from django.contrib.auth.models import Group, User
from django.test import (
    Client,
    TestCase,
    TransactionTestCase,
)
from django.urls import reverse

from django.test import override_settings, RequestFactory

from django.core import mail

from pedidos.security import (
    obtener_ip_cliente,
    crear_password_reset_challenge,
    password_reset_codigo_coincide,
    consumir_password_reset_challenge,
    solicitar_password_reset,
)


from pedidos.security import (
    consumir_rate_limit,
)

from django.db import connection

from django.db import (
    DatabaseError,
    IntegrityError,
    transaction,
    connection,
    close_old_connections,
)

from django.utils import timezone

from django.contrib.auth import get_user_model
from django.test import RequestFactory
from pedidos.models import Tenant, Membership, Sucursal

import uuid

from django.contrib.sessions.backends.db import SessionStore

from django.contrib.auth.models import AnonymousUser

from django.core.exceptions import PermissionDenied

from django.test import override_settings

from django.http import HttpResponse

from pedidos.middleware import TenantContextMiddleware

from pedidos.views import (
    verificar_estado_negocio,
    _validar_carrito,
    _validar_seleccion_producto,
    CarritoInvalido,
    suscripcion_activa,
    _renovar_suscripcion_30_dias,
    _procesar_pago_wompi_aprobado,
    _estado_bloqueo_pasarela_tenant,
    CART_MAX_ITEM_QUANTITY,
    CART_MAX_LINES,
    _wompi_crear_referencia,
    _wompi_tenant_id_desde_referencia,
    PASSWORD_RESET_FLOW_SESSION_KEY,
    PASSWORD_RESET_GRANT_SESSION_KEY,
)

from pedidos.tenant_context import (
    resolver_tenant,
    resolver_sucursal,
    _resolver_tenant_desarrollo,
)

from pedidos.db_tenant_context import (
    tenant_database_context,
)

from .models import (
    Categoria,
    Cliente,
    ConfiguracionNegocio,
    DiaEspecial,
    Extra,
    OpcionProducto,
    Pedido,
    Producto,
    PagoWompi,
    DetallePedido,
    EventoPagoWompi,
    EstadoPasarelaPago,
    SuscripcionTenant,
    RepartidorSucursal,
    MembershipSucursal,
    StaffIdentity,
    PasswordResetChallenge,
)


class StaffIdentityTests(TestCase):

    def test_email_se_normaliza_al_guardar(
        self,
    ):
        user = User.objects.create_user(
            username="staff_email_normalizado",
            password="PasswordSeguro123!",
        )

        identity = StaffIdentity.objects.create(
            user=user,
            email="  Staff.Test@Correo.COM  ",
        )

        identity.refresh_from_db()

        self.assertEqual(
            identity.email,
            "staff.test@correo.com",
        )

        self.assertFalse(
            identity.email_verified
        )

    def test_email_es_unico_sin_importar_mayusculas(
        self,
    ):
        user_1 = User.objects.create_user(
            username="staff_email_1",
            password="PasswordSeguro123!",
        )

        user_2 = User.objects.create_user(
            username="staff_email_2",
            password="PasswordSeguro123!",
        )

        StaffIdentity.objects.create(
            user=user_1,
            email="usuario@correo.com",
        )

        # bulk_create() se usa intencionalmente:
        # evita StaffIdentity.save() y demuestra
        # que PostgreSQL también protege la
        # unicidad case-insensitive por sí solo.
        with self.assertRaises(
            IntegrityError
        ):
            with transaction.atomic():
                StaffIdentity.objects.bulk_create(
                    [
                        StaffIdentity(
                            user=user_2,
                            email="  USUARIO@CORREO.COM  ",
                        ),
                    ]
                )


class FoodBackTestBase(TestCase):
    """
    Datos mínimos reutilizables para las pruebas del baseline.
    """

    @classmethod
    def setUpTestData(cls):
        # ========================================================
        # CONTEXTO MULTI-TENANT BASE
        # ========================================================

        # Debe coincidir con FOODBACK_DEFAULT_TENANT_SLUG
        # para que las requests públicas de los tests puedan
        # resolver el Tenant igual que localhost/desarrollo.
        cls.tenant = Tenant.objects.create(
            nombre="FoodBack Test",
            slug="rancheritos",
            habilitado=True,
        )

        cls.suscripcion = SuscripcionTenant.objects.create(
            tenant=cls.tenant,
            estado=SuscripcionTenant.Estado.ACTIVA,
            fecha_vencimiento=(
                date.today()
                + timedelta(days=365)
            ),
        )

        cls.sucursal = Sucursal.objects.create(
            tenant=cls.tenant,
            nombre="Sucursal Test",
            slug="principal",
            estado=Sucursal.Estado.ACTIVA,
        )

        # ========================================================
        # CONFIGURACION DE LA SUCURSAL
        # ========================================================

        cls.config = ConfiguracionNegocio.objects.create(
            sucursal=cls.sucursal,
            nombre_negocio="FoodBack Test",
            fecha_vencimiento=(
                date.today()
                + timedelta(days=365)
            ),
            hora_apertura="00:00",
            hora_cierre="23:59",
            lunes_abierto=True,
            martes_abierto=True,
            miercoles_abierto=True,
            jueves_abierto=True,
            viernes_abierto=True,
            sabado_abierto=True,
            domingo_abierto=True,
        )

        # ========================================================
        # CATALOGO
        # ========================================================

        cls.categoria = Categoria.objects.create(
            tenant=cls.tenant,
            nombre="Hamburguesas",
            orden=1,
        )

        cls.producto = Producto.objects.create(
            categoria=cls.categoria,
            nombre="Hamburguesa de prueba",
            descripcion=(
                "Producto utilizado en "
                "las pruebas automáticas."
            ),
            precio=Decimal("5.00"),
            disponible=True,
        )

        # ========================================================
        # CLIENTE
        # ========================================================

        cls.cliente_pedido = Cliente.objects.create(
            tenant=cls.tenant,
            telefono="70000001",
            nombre="Cliente",
            apellido="Prueba",
            direccion_ultima="San Miguel",
        )

        # ========================================================
        # GRUPOS LEGACY
        # ========================================================

        cls.grupo_admin = Group.objects.create(
            name="Administradores"
        )

        cls.grupo_delivery = Group.objects.create(
            name="Repartidores"
        )

        # ========================================================
        # ADMIN
        # ========================================================

        cls.admin_user = User.objects.create_user(
            username="admin_test",
            password="PasswordSeguro123!",
        )

        cls.admin_user.groups.add(
            cls.grupo_admin
        )

        Membership.objects.create(
            tenant=cls.tenant,
            usuario=cls.admin_user,
            rol=Membership.ROLE_OWNER,
            activo=True,
        )

        # ========================================================
        # DELIVERY
        # ========================================================

        cls.delivery_1 = User.objects.create_user(
            username="delivery_test_1",
            password="PasswordSeguro123!",
        )

        cls.delivery_1.groups.add(
            cls.grupo_delivery
        )

        cls.delivery_2 = User.objects.create_user(
            username="delivery_test_2",
            password="PasswordSeguro123!",
        )

        cls.delivery_2.groups.add(
            cls.grupo_delivery
        )
        
        RepartidorSucursal.objects.create(
            usuario=cls.delivery_1,
            sucursal=cls.sucursal,
            activo=True,
        )

        RepartidorSucursal.objects.create(
            usuario=cls.delivery_2,
            sucursal=cls.sucursal,
            activo=True,
        )
        
    
    def _crear_checkout_token_test(
        self,
    ):
        """
        Emite un token legítimo de checkout
        para tests que NO están probando
        manipulación/idempotencia del token.
        """

        token = str(
            uuid.uuid4()
        )

        session = self.client.session

        session[
            "foodback_checkout_token"
        ] = token

        session.save()

        return token    
    

    def crear_pedido(
        self,
        estado="RECIBIDO",
        repartidor=None,
        telefono=None,
    ):
        """
        Crea un cliente/pedido independiente para evitar conflictos
        con el campo telefono unique.
        """

        if telefono is None:
            telefono = (
                f"71"
                f"{Cliente.objects.filter(tenant=self.tenant).count():06d}"
            )

        cliente = Cliente.objects.create(
            tenant=self.tenant,
            telefono=telefono,
            nombre="Cliente",
            apellido="Pedido",
            direccion_ultima="San Miguel",
        )

        return Pedido.objects.create(
            sucursal=self.sucursal,
            cliente=cliente,
            direccion_entrega="Dirección de prueba",
            latitud="13.4800",
            longitud="-88.1800",
            metodo_pago="EFECTIVO",
            estado=estado,
            repartidor=repartidor,
            total_productos=Decimal("10.00"),
        )


class PublicBaselineTests(FoodBackTestBase):

    def test_menu_es_publico_y_carga(self):
        response = self.client.get(reverse("menu"))

        self.assertEqual(response.status_code, 200)

        self.assertContains(
            response,
            "Hamburguesa de prueba",
        )

    def test_agregar_producto_guarda_carrito_en_sesion(self):
        response = self.client.post(
            reverse(
                "add_to_cart",
                args=[self.producto.id],
            )
        )

        self.assertEqual(response.status_code, 302)

        session = self.client.session
        cart = session.get("cart", {})

        clave_esperada = f"{self.producto.id}-0-0"

        self.assertIn(clave_esperada, cart)
        self.assertEqual(cart[clave_esperada], 1)

    def test_agregar_producto_no_redirige_a_referer_externo(
        self,
    ):
        response = self.client.post(
            reverse(
                "add_to_cart",
                args=[self.producto.id],
            ),
            HTTP_REFERER=(
                "https://evil.example/phishing"
            ),
        )

        self.assertEqual(
            response.status_code,
            302,
        )

        self.assertEqual(
            response.url,
            reverse("menu"),
        )

        cart = self.client.session.get(
            "cart",
            {},
        )

        clave = (
            f"{self.producto.id}-0-0"
        )

        self.assertEqual(
            cart[clave],
            1,
        )

    def test_carrito_no_permite_superar_cantidad_maxima(
        self,
    ):
        clave = (
            f"{self.producto.id}-0-0"
        )

        session = self.client.session
        session["cart"] = {
            clave: CART_MAX_ITEM_QUANTITY,
        }
        session.save()

        response = self.client.post(
            reverse(
                "add_to_cart",
                args=[self.producto.id],
            )
        )

        self.assertEqual(
            response.status_code,
            400,
        )

        cart = self.client.session.get(
            "cart",
            {},
        )

        self.assertEqual(
            cart[clave],
            CART_MAX_ITEM_QUANTITY,
        )
    
    def test_menu_descarta_carrito_de_sesion_malformado(
        self,
    ):
        session = self.client.session
        session["cart"] = [
            "estructura-invalida"
        ]
        session.save()

        response = self.client.get(
            reverse("menu")
        )

        self.assertEqual(
            response.status_code,
            200,
        )

        self.assertEqual(
            response.context[
                "cantidad_carrito"
            ],
            0,
        )

        self.assertEqual(
            self.client.session.get(
                "cart"
            ),
            {},
        )

    def test_producto_simple_menu_usa_post_y_no_get(self):
        response = self.client.get(
            reverse("menu")
        )

        self.assertEqual(
            response.status_code,
            200,
        )

        add_url = reverse(
            "add_to_cart",
            args=[self.producto.id],
        )

        html = response.content.decode()

        # Debe existir un formulario POST para el producto simple.
        self.assertIn(
            f'action="{add_url}"',
            html,
        )

        self.assertIn(
            'method="POST"',
            html,
        )

        self.assertIn(
            'name="csrfmiddlewaretoken"',
            html,
        )

        # Nunca debe volver a agregarse mediante un enlace GET.
        self.assertNotIn(
            f'href="{add_url}"',
            html,
        )

        # Tampoco mediante window.location.href.
        self.assertNotIn(
            f"window.location.href='{add_url}'",
            html,
        )


class AuthenticationBaselineTests(FoodBackTestBase):

    def test_dashboard_admin_exige_autenticacion(self):
        response = self.client.get(
            reverse("dashboard_admin")
        )

        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            response["Location"].startswith(
                reverse("login_custom")
            )
        )

    def test_dashboard_delivery_exige_autenticacion(self):
        response = self.client.get(
            reverse("dashboard_delivery")
        )

        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            response["Location"].startswith(
                reverse("login_custom")
            )
        )

    def test_repartidor_no_accede_dashboard_admin(
        self,
    ):
        self.client.force_login(
            self.delivery_1
        )

        response = self.client.get(
            reverse(
                "dashboard_admin"
            )
        )

        self.assertEqual(
            response.status_code,
            403,
        )

    def test_admin_no_accede_dashboard_delivery(
        self,
    ):
        self.client.force_login(
            self.admin_user
        )

        response = self.client.get(
            reverse(
                "dashboard_delivery"
            )
        )

        self.assertEqual(
            response.status_code,
            403,
        )


class AdminBaselineTests(FoodBackTestBase):

    def test_admin_puede_mover_pedido_a_cocina(self):
        pedido = self.crear_pedido(
            estado="RECIBIDO",
            telefono="72000001",
        )

        self.client.force_login(self.admin_user)

        response = self.client.post(
            reverse("dashboard_admin"),
            {
                "pedido_id": pedido.id,
                "accion": "cocina",
            },
        )

        self.assertEqual(response.status_code, 302)

        pedido.refresh_from_db()

        self.assertEqual(
            pedido.estado,
            "COCINA",
        )
        
    def test_admin_rechaza_accion_y_pedido_id_invalidos(
        self,
    ):
        pedido = self.crear_pedido(
            estado="RECIBIDO",
            telefono="72000010",
        )

        self.client.force_login(
            self.admin_user
        )

        response = self.client.post(
            reverse(
                "dashboard_admin"
            ),
            {
                "pedido_id":
                    pedido.id,

                "accion":
                    "accion-inventada",
            },
        )

        self.assertEqual(
            response.status_code,
            400,
        )

        response = self.client.post(
            reverse(
                "dashboard_admin"
            ),
            {
                "pedido_id":
                    "-999",

                "accion":
                    "cocina",
            },
        )

        self.assertEqual(
            response.status_code,
            400,
        )

        pedido.refresh_from_db()

        self.assertEqual(
            pedido.estado,
            "RECIBIDO",
        )
        
        
    def test_admin_no_puede_revivir_pedido_entregado(
        self,
    ):
        pedido = self.crear_pedido(
            estado="ENTREGADO",
            telefono="72000011",
        )

        self.client.force_login(
            self.admin_user
        )

        response = self.client.post(
            reverse(
                "dashboard_admin"
            ),
            {
                "pedido_id":
                    pedido.id,

                "accion":
                    "cocina",
            },
        )

        self.assertEqual(
            response.status_code,
            302,
        )

        pedido.refresh_from_db()

        self.assertEqual(
            pedido.estado,
            "ENTREGADO",
        )


class DeliveryBaselineTests(FoodBackTestBase):

    def test_delivery_puede_llevar_multiples_pedidos(self):
        pedido_1 = self.crear_pedido(
            estado="RUTA",
            telefono="73000001",
        )

        pedido_2 = self.crear_pedido(
            estado="RUTA",
            telefono="73000002",
        )

        self.client.force_login(self.delivery_1)

        self.client.post(
            reverse("dashboard_delivery"),
            {
                "pedido_id": pedido_1.id,
                "accion": "tomar",
            },
        )

        self.client.post(
            reverse("dashboard_delivery"),
            {
                "pedido_id": pedido_2.id,
                "accion": "tomar",
            },
        )

        pedido_1.refresh_from_db()
        pedido_2.refresh_from_db()

        self.assertEqual(
            pedido_1.repartidor,
            self.delivery_1,
        )

        self.assertEqual(
            pedido_2.repartidor,
            self.delivery_1,
        )

        pedidos_asignados = Pedido.objects.filter(
            repartidor=self.delivery_1,
            estado="RUTA",
        ).count()

        self.assertEqual(
            pedidos_asignados,
            2,
        )

    def test_delivery_no_puede_entregar_pedido_de_otro_delivery(self):
        pedido = self.crear_pedido(
            estado="RUTA",
            repartidor=self.delivery_2,
            telefono="74000001",
        )

        self.client.force_login(self.delivery_1)

        response = self.client.post(
            reverse("dashboard_delivery"),
            {
                "pedido_id": pedido.id,
                "accion": "entregado",
            },
        )

        self.assertEqual(response.status_code, 302)

        pedido.refresh_from_db()

        self.assertEqual(
            pedido.estado,
            "RUTA",
        )

        self.assertEqual(
            pedido.repartidor,
            self.delivery_2,
        )
        
    def test_delivery_rechaza_accion_y_pedido_id_invalidos(
        self,
    ):
        pedido = self.crear_pedido(
            estado="RUTA",
            telefono="74000010",
        )

        self.client.force_login(
            self.delivery_1
        )

        # -----------------------------------------
        # Acción manipulada.
        # -----------------------------------------

        response = self.client.post(
            reverse(
                "dashboard_delivery"
            ),
            {
                "pedido_id":
                    pedido.id,

                "accion":
                    "accion-inventada",
            },
        )

        self.assertEqual(
            response.status_code,
            400,
        )

        # -----------------------------------------
        # ID manipulado.
        # -----------------------------------------

        response = self.client.post(
            reverse(
                "dashboard_delivery"
            ),
            {
                "pedido_id":
                    "-999",

                "accion":
                    "tomar",
            },
        )

        self.assertEqual(
            response.status_code,
            400,
        )

        # Ninguno de los intentos debe modificar
        # el Pedido.
        pedido.refresh_from_db()

        self.assertEqual(
            pedido.estado,
            "RUTA",
        )

        self.assertIsNone(
            pedido.repartidor
        )


    def test_delivery_no_puede_robar_pedido_ya_tomado(
        self,
    ):
        pedido = self.crear_pedido(
            estado="RUTA",
            telefono="74000011",
        )

        # Delivery 1 toma legítimamente el Pedido.
        self.client.force_login(
            self.delivery_1
        )

        response = self.client.post(
            reverse(
                "dashboard_delivery"
            ),
            {
                "pedido_id":
                    pedido.id,

                "accion":
                    "tomar",
            },
        )

        self.assertEqual(
            response.status_code,
            302,
        )

        pedido.refresh_from_db()

        self.assertEqual(
            pedido.repartidor,
            self.delivery_1,
        )

        # -----------------------------------------
        # Delivery 2 también pertenece a ESTA
        # sucursal, por lo que supera autorización.
        #
        # Debe fallar por el estado REAL del Pedido,
        # no por permisos.
        # -----------------------------------------

        self.client.force_login(
            self.delivery_2
        )

        response = self.client.post(
            reverse(
                "dashboard_delivery"
            ),
            {
                "pedido_id":
                    pedido.id,

                "accion":
                    "tomar",
            },
        )

        self.assertEqual(
            response.status_code,
            302,
        )

        pedido.refresh_from_db()

        # Delivery 2 NO debe haber podido
        # apropiarse del Pedido.
        self.assertEqual(
            pedido.repartidor,
            self.delivery_1,
        )

        self.assertEqual(
            pedido.estado,
            "RUTA",
        )
        
class PublicTrackingSecurityTests(FoodBackTestBase):
    """
    FB-SEC-001:
    Un ID interno secuencial nunca debe bastar para consultar
    públicamente un pedido.
    """

    def test_id_numerico_no_debe_exponer_tracker_publico(self):
        pedido = self.crear_pedido(
            estado="RECIBIDO",
            telefono="75000001",
        )

        response = self.client.get(
            f"/pedido/{pedido.id}/rastrear/"
        )

        self.assertEqual(response.status_code, 404)

    def test_id_numerico_no_debe_exponer_api_estado(self):
        pedido = self.crear_pedido(
            estado="RECIBIDO",
            telefono="75000002",
        )

        response = self.client.get(
            f"/api/pedido/{pedido.id}/status/"
        )

        self.assertEqual(response.status_code, 404)

    def test_tracking_token_valido_muestra_tracker(self):
        pedido = self.crear_pedido(
            estado="RECIBIDO",
            telefono="75000003",
        )

        response = self.client.get(
            reverse(
                "order_tracker",
                args=[pedido.tracking_token],
            )
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.context["pedido"].id,
            pedido.id,
        )

    def test_tracking_token_valido_permite_consultar_estado(self):
        pedido = self.crear_pedido(
            estado="RECIBIDO",
            telefono="75000004",
        )

        response = self.client.get(
            reverse(
                "api_order_status",
                args=[pedido.tracking_token],
            )
        )

        self.assertEqual(response.status_code, 200)

        data = response.json()

        self.assertEqual(data["status"], "ok")
        self.assertEqual(
            data["estado_codigo"],
            pedido.estado,
        )
        
    
    def test_api_estado_sin_cambios_responde_ligero(
        self,
    ):
        pedido = self.crear_pedido(
            estado="RECIBIDO",
            telefono="75000040",
        )

        pedido.refresh_from_db()

        response = self.client.get(
            reverse(
                "api_order_status",
                args=[
                    pedido.tracking_token
                ],
            ),
            {
                "last_update": (
                    pedido
                    .actualizado_en
                    .isoformat()
                )
            },
        )

        self.assertEqual(
            response.status_code,
            200,
        )

        data = response.json()

        self.assertEqual(
            data["status"],
            "ok",
        )

        self.assertFalse(
            data["changed"]
        )

        self.assertNotIn(
            "estado_codigo",
            data,
        )

        self.assertNotIn(
            "estado_texto",
            data,
        )


    def test_api_estado_rechaza_last_update_invalido(
        self,
    ):
        pedido = self.crear_pedido(
            estado="RECIBIDO",
            telefono="75000041",
        )

        response = self.client.get(
            reverse(
                "api_order_status",
                args=[
                    pedido.tracking_token
                ],
            ),
            {
                "last_update":
                    "esto-no-es-una-fecha"
            },
        )

        self.assertEqual(
            response.status_code,
            400,
        )

    def test_id_numerico_no_debe_abrir_pago(self):
        pedido = self.crear_pedido(
            estado="PENDIENTE",
            telefono="75000005",
        )

        response = self.client.get(
            f"/pagar/{pedido.id}/"
        )

        self.assertEqual(response.status_code, 404)

    def test_id_numerico_no_debe_abrir_exito(self):
        pedido = self.crear_pedido(
            estado="RECIBIDO",
            telefono="75000006",
        )

        response = self.client.get(
            f"/exito/{pedido.id}/"
        )

        self.assertEqual(response.status_code, 404)
    
class CartIntegritySecurityTests(FoodBackTestBase):
    """
    FB-SEC-002:
    El servidor no debe confiar en producto/opción/extras
    enviados por el navegador.
    """

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()

        # Segundo producto para intentar mezclar relaciones.
        cls.producto_b = Producto.objects.create(
            categoria=cls.categoria,
            nombre="Pizza de prueba",
            descripcion="Segundo producto para ataques de integridad.",
            precio=Decimal("8.00"),
            disponible=True,
        )

        # Variante válida del producto principal.
        cls.opcion_valida = OpcionProducto.objects.create(
            producto=cls.producto,
            nombre="Doble carne",
            precio_extra=Decimal("2.00"),
            disponible=True,
        )

        # Variante que pertenece a OTRO producto.
        cls.opcion_otro_producto = OpcionProducto.objects.create(
            producto=cls.producto_b,
            nombre="Borde relleno",
            precio_extra=Decimal("3.00"),
            disponible=True,
        )

        # Variante del producto correcto, pero deshabilitada.
        cls.opcion_no_disponible = OpcionProducto.objects.create(
            producto=cls.producto,
            nombre="Variante desactivada",
            precio_extra=Decimal("1.00"),
            disponible=False,
        )

        # Extra válido del producto principal.
        cls.extra_valido = Extra.objects.create(
            tenant=cls.tenant,
            nombre="Queso extra",
            precio=Decimal("1.00"),
            disponible=True,
        )
        cls.producto.extras.add(cls.extra_valido)

        # Extra válido, pero solamente para producto B.
        cls.extra_otro_producto = Extra.objects.create(
            tenant=cls.tenant,
            nombre="Extra exclusivo pizza",
            precio=Decimal("2.00"),
            disponible=True,
        )
        cls.producto_b.extras.add(cls.extra_otro_producto)

        # Extra perteneciente al producto, pero deshabilitado.
        cls.extra_no_disponible = Extra.objects.create(
            tenant=cls.tenant,
            nombre="Extra desactivado",
            precio=Decimal("1.50"),
            disponible=False,
        )
        cls.producto.extras.add(cls.extra_no_disponible)

        # Producto deshabilitado.
        cls.producto_no_disponible = Producto.objects.create(
            categoria=cls.categoria,
            nombre="Producto desactivado",
            descripcion="No debe poder agregarse.",
            precio=Decimal("6.00"),
            disponible=False,
        )

    def assert_cart_vacio(self):
        cart = self.client.session.get("cart", {})
        self.assertEqual(cart, {})

    def test_rechaza_opcion_de_otro_producto(self):
        response = self.client.post(
            reverse(
                "add_to_cart",
                args=[self.producto.id],
            ),
            {
                "opcion_id": str(
                    self.opcion_otro_producto.id
                ),
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assert_cart_vacio()

    def test_rechaza_extra_de_otro_producto(self):
        response = self.client.post(
            reverse(
                "add_to_cart",
                args=[self.producto.id],
            ),
            {
                "extras": [
                    str(self.extra_otro_producto.id)
                ],
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assert_cart_vacio()

    def test_rechaza_opcion_no_disponible(self):
        response = self.client.post(
            reverse(
                "add_to_cart",
                args=[self.producto.id],
            ),
            {
                "opcion_id": str(
                    self.opcion_no_disponible.id
                ),
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assert_cart_vacio()

    def test_rechaza_extra_no_disponible(self):
        response = self.client.post(
            reverse(
                "add_to_cart",
                args=[self.producto.id],
            ),
            {
                "extras": [
                    str(self.extra_no_disponible.id)
                ],
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assert_cart_vacio()

    def test_rechaza_producto_no_disponible(self):
        response = self.client.post(
            reverse(
                "add_to_cart",
                args=[self.producto_no_disponible.id],
            )
        )

        self.assertEqual(response.status_code, 404)
        self.assert_cart_vacio()

    def test_rechaza_identificador_de_opcion_malformado(self):
        response = self.client.post(
            reverse(
                "add_to_cart",
                args=[self.producto.id],
            ),
            {
                "opcion_id": "ESTO-NO-ES-UN-ID",
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assert_cart_vacio()

    def test_combinacion_legitima_si_se_acepta(self):
        response = self.client.post(
            reverse(
                "add_to_cart",
                args=[self.producto.id],
            ),
            {
                "opcion_id": str(self.opcion_valida.id),
                "extras": [
                    str(self.extra_valido.id)
                ],
            },
        )

        self.assertEqual(response.status_code, 302)

        cart = self.client.session.get("cart", {})

        clave = (
            f"{self.producto.id}-"
            f"{self.opcion_valida.id}-"
            f"{self.extra_valido.id}"
        )

        self.assertIn(clave, cart)
        self.assertEqual(cart[clave], 1)

    def test_validador_rechaza_cantidad_excesiva_en_sesion(
        self,
    ):
        cart = {
            f"{self.producto.id}-0-0": (
                CART_MAX_ITEM_QUANTITY + 1
            ),
        }

        with self.assertRaises(
            CarritoInvalido
        ):
            _validar_carrito(
                cart,
                self.tenant,
            )

    def test_validador_rechaza_demasiadas_lineas(
        self,
    ):
        cart = {
            f"{numero}-0-0": 1
            for numero in range(
                1,
                CART_MAX_LINES + 2,
            )
        }

        with self.assertRaises(
            CarritoInvalido
        ):
            _validar_carrito(
                cart,
                self.tenant,
            )

    def test_checkout_revalida_carrito_manipulado(self):
        """
        Aunque alguien lograra introducir una combinación inválida
        directamente en la sesión/carrito, checkout debe volver
        a comprobarla antes de crear el pedido.
        """

        session = self.client.session

        session["cart"] = {
            (
                f"{self.producto.id}-"
                f"{self.opcion_otro_producto.id}-0"
            ): 1
        }

        session.save()
        
        checkout_token = (
            self._crear_checkout_token_test()
        )

        response = self.client.post(
            reverse("checkout"),
            {
                "checkout_token": checkout_token,
                "telefono": "76000001",
                "nombre": "Ataque",
                "apellido": "Prueba",
                "direccion": "San Miguel",
                "metodo_pago": "EFECTIVO",
                "latitud": "13.4800",
                "longitud": "-88.1800",
            },
        )

        self.assertEqual(response.status_code, 400)

        self.assertEqual(
            Pedido.objects.filter(
                cliente__telefono="76000001"
            ).count(),
            0,
        )
        
class HttpMethodSecurityTests(FoodBackTestBase):
    """
    FB-SEC-003:
    Las operaciones que cambian estado no deben ejecutarse
    mediante peticiones GET.
    """

    def test_get_no_debe_vaciar_carrito(self):
        session = self.client.session
        clave = f"{self.producto.id}-0-0"

        session["cart"] = {
            clave: 1,
        }
        session.save()

        response = self.client.get(
            reverse("clean_cart")
        )

        self.assertEqual(
            response.status_code,
            405,
        )

        cart = self.client.session.get(
            "cart",
            {},
        )

        self.assertIn(
            clave,
            cart,
        )

    def test_get_no_debe_eliminar_item_carrito(self):
        session = self.client.session
        clave = f"{self.producto.id}-0-0"

        session["cart"] = {
            clave: 1,
        }
        session.save()

        response = self.client.get(
            reverse(
                "eliminar_item",
                args=[clave],
            )
        )

        self.assertEqual(
            response.status_code,
            405,
        )

        cart = self.client.session.get(
            "cart",
            {},
        )

        self.assertIn(
            clave,
            cart,
        )

    def test_get_no_debe_eliminar_excepcion_admin(self):
        excepcion = DiaEspecial.objects.create(
            sucursal=self.sucursal,
            fecha=date.today() + timedelta(days=5),
            abierto=False,
            motivo="Prueba de seguridad",
        )

        self.client.force_login(
            self.admin_user
        )

        response = self.client.get(
            reverse(
                "eliminar_excepcion",
                args=[excepcion.id],
            )
        )

        self.assertEqual(
            response.status_code,
            405,
        )

        self.assertTrue(
            DiaEspecial.objects.filter(
                id=excepcion.id
            ).exists()
        )

    def test_get_no_debe_cerrar_sesion(self):
        self.client.force_login(
            self.admin_user
        )

        response = self.client.get(
            reverse("logout")
        )

        self.assertEqual(
            response.status_code,
            405,
        )

        self.assertEqual(
            self.client.session.get(
                "_auth_user_id"
            ),
            str(self.admin_user.id),
        )

    def test_get_admin_settings_no_debe_borrar_datos(self):
        """
        Simplemente visualizar Configuración no debería
        eliminar registros de la base de datos.
        """

        excepcion_pasada = (
            DiaEspecial.objects.create(
                sucursal=self.sucursal,
                fecha=date.today()
                - timedelta(days=1),
                abierto=False,
                motivo="Histórico",
            )
        )

        self.client.force_login(
            self.admin_user
        )

        response = self.client.get(
            reverse("admin_settings")
        )

        self.assertEqual(
            response.status_code,
            200,
        )

        self.assertTrue(
            DiaEspecial.objects.filter(
                id=excepcion_pasada.id
            ).exists()
        )
        
    def test_get_no_debe_agregar_producto_al_carrito(self):
        response = self.client.get(
        reverse(
            "add_to_cart",
            args=[self.producto.id],
        )
    )

        self.assertEqual(
            response.status_code,
            405,
        )

        cart = self.client.session.get(
            "cart",
            {},
        )

        self.assertEqual(
            cart,
            {},
        )
        
    def test_post_si_debe_vaciar_carrito(self):
        session = self.client.session
        clave = f"{self.producto.id}-0-0"

        session["cart"] = {
            clave: 2,
        }
        session.save()

        response = self.client.post(
            reverse("clean_cart")
        )

        self.assertEqual(
            response.status_code,
            302,
        )

        cart = self.client.session.get(
            "cart",
            {},
        )

        self.assertEqual(
            cart,
            {},
        )

    def test_post_key_inventada_no_borra_otro_item_del_carrito(
        self,
    ):
        session = self.client.session
        clave = f"{self.producto.id}-0-0"

        session["cart"] = {
            clave: 1,
        }
        session.save()

        response = self.client.post(
            reverse(
                "eliminar_item",
                args=[
                    "999999-0-0"
                ],
            )
        )

        self.assertEqual(
            response.status_code,
            302,
        )

        cart = self.client.session.get(
            "cart",
            {},
        )

        self.assertEqual(
            cart,
            {clave: 1},
        )

    def test_post_si_debe_eliminar_item_carrito(self):
        session = self.client.session
        clave = f"{self.producto.id}-0-0"

        session["cart"] = {
            clave: 1,
        }
        session.save()

        response = self.client.post(
            reverse(
                "eliminar_item",
                args=[clave],
            )
        )

        self.assertEqual(
            response.status_code,
            302,
        )

        cart = self.client.session.get(
            "cart",
            {},
        )

        self.assertNotIn(
            clave,
            cart,
        )

    def test_post_si_debe_eliminar_excepcion_admin(self):
        excepcion = DiaEspecial.objects.create(
            sucursal=self.sucursal,
            fecha=date.today() + timedelta(days=5),
            abierto=False,
            motivo="Eliminar mediante POST",
        )

        self.client.force_login(
            self.admin_user
        )

        response = self.client.post(
            reverse(
                "eliminar_excepcion",
                args=[excepcion.id],
            )
        )

        self.assertEqual(
            response.status_code,
            302,
        )

        self.assertFalse(
            DiaEspecial.objects.filter(
                id=excepcion.id
            ).exists()
        )

    def test_post_si_debe_cerrar_sesion(self):
        self.client.force_login(
            self.admin_user
        )

        response = self.client.post(
            reverse("logout")
        )

        self.assertEqual(
            response.status_code,
            302,
        )

        self.assertIsNone(
            self.client.session.get(
                "_auth_user_id"
            )
        )
    
    
    def test_put_no_debe_operar_dashboard_admin(self):
        pedido = self.crear_pedido(
            estado="RECIBIDO",
            telefono="72100020",
        )

        self.client.force_login(
            self.admin_user
        )

        response = self.client.put(
            reverse("dashboard_admin"),
            data={
                "pedido_id": pedido.id,
                "accion": "cocina",
            },
            content_type="application/json",
        )

        self.assertEqual(
            response.status_code,
            405,
        )

        pedido.refresh_from_db()

        self.assertEqual(
            pedido.estado,
            "RECIBIDO",
        )

    def test_put_no_debe_operar_admin_settings(self):
        self.client.force_login(
            self.admin_user
        )

        response = self.client.put(
            reverse("admin_settings"),
            data={
                "tipo_accion": "global",
            },
            content_type="application/json",
        )

        self.assertEqual(
            response.status_code,
            405,
        )

    def test_put_no_debe_operar_dashboard_delivery(self):
        self.client.force_login(
            self.delivery_1
        )

        response = self.client.put(
            reverse("dashboard_delivery"),
            data={
                "pedido_id": 1,
                "accion": "tomar",
            },
            content_type="application/json",
        )

        self.assertEqual(
            response.status_code,
            405,
        )


class CsrfSecurityTests(FoodBackTestBase):
    """
    FB-SEC-003A:
    Las operaciones POST sensibles deben estar protegidas
    también por CSRF.
    """

    def setUp(self):
        self.csrf_client = Client(
            enforce_csrf_checks=True
        )

    def test_vaciar_carrito_sin_csrf_es_rechazado(self):
        session = self.csrf_client.session

        session["cart"] = {
            f"{self.producto.id}-0-0": 1
        }

        session.save()

        response = self.csrf_client.post(
            reverse("clean_cart")
        )

        self.assertEqual(
            response.status_code,
            403,
        )

    def test_logout_sin_csrf_es_rechazado(self):
        self.csrf_client.force_login(
            self.admin_user
        )

        response = self.csrf_client.post(
            reverse("logout")
        )

        self.assertEqual(
            response.status_code,
            403,
        )
        

class PaymentHttpSecurityTests(FoodBackTestBase):
    """
    FB-SEC-003B:
    Seguridad del inicio y confirmación de pagos Wompi.

    Todas las llamadas externas están mockeadas.
    Ninguna prueba contacta Wompi real.
    """

    WOMPI_URL_FAKE = "https://wompi.test/enlace-seguro"
    
    @patch(
    "pedidos.views."
    "_validar_hash_webhook_wompi"
    )
    def test_webhook_rechaza_content_type_no_json(
        self,
        mock_validar_hash,
    ):
        response = self.client.post(
            reverse(
                "wompi_webhook"
            ),
            data="contenido arbitrario",
            content_type="text/plain",
        )

        self.assertEqual(
            response.status_code,
            415,
        )

        mock_validar_hash.assert_not_called()
    
    @patch(
    "pedidos.views."
    "_validar_hash_webhook_wompi"
    )
    def test_webhook_json_malformado_responde_400(
        self,
        mock_validar_hash,
    ):
        response = self.client.post(
            reverse(
                "wompi_webhook"
            ),
            data='{"IdCuenta": 123,',
            content_type="application/json",
        )

        self.assertEqual(
            response.status_code,
            400,
        )

        mock_validar_hash.assert_not_called()
    
    @override_settings(
        FOODBACK_WOMPI_WEBHOOK_MAX_BYTES=128,
    )
    @patch(
        "pedidos.views."
        "_validar_hash_webhook_wompi"
    )
    def test_webhook_rechaza_payload_demasiado_grande(
        self,
        mock_validar_hash,
    ):
        body = {
            "padding": "X" * 500,
        }

        response = self.client.post(
            reverse(
                "wompi_webhook"
            ),
            data=json.dumps(
                body
            ),
            content_type="application/json",
        )

        self.assertEqual(
            response.status_code,
            413,
        )

        mock_validar_hash.assert_not_called()
    
    @patch(
    "pedidos.views._validar_hash_webhook_wompi",
    return_value=True,
    )
    def test_webhook_formato_real_wompi_aprueba_pedido(
        self,
        mock_hash,
    ):
        pedido = self.crear_pedido_tarjeta(
            "79000009"
        )

        pago = self.crear_pago_pendiente(
            pedido
        )

        body = {
            "IdCuenta": 123,
            "FechaTransaccion": "2026-09-08T00:00:00",
            "Monto": str(pago.monto),

            "IdTransaccion":
                "TX-WOMPI-FORMATO-REAL",

            "ResultadoTransaccion":
                "ExitosaAprobada",

            "EsProductiva": False,

            "EnlacePago": {
                "Id": 999999,

                "IdentificadorEnlaceComercio":
                    pago.referencia,

                "NombreProducto":
                    f"Pedido #{pedido.id}",

                "DescripcionProducto":
                    "",
            },
        }

        response = self.client.post(
            reverse("wompi_webhook"),
            data=json.dumps(body),
            content_type="application/json",
        )

        self.assertEqual(
            response.status_code,
            200,
        )

        pedido.refresh_from_db()
        pago.refresh_from_db()

        self.assertTrue(
            pedido.pago_verificado
        )

        self.assertEqual(
            pedido.estado,
            "RECIBIDO",
        )

        self.assertEqual(
            pago.estado,
            "APROBADO",
        )

        self.assertTrue(
            pago.es_aprobada
        )

        self.assertEqual(
            pago.id_transaccion,
            "TX-WOMPI-FORMATO-REAL",
        )

        self.assertTrue(
            bool(pago.raw_webhook)
        )
    
    @patch(
        "pedidos.views._wompi_crear_enlace_pago"
    )
    def test_repetir_inicio_suscripcion_reutiliza_mismo_pago(
        self,
        mock_crear_enlace,
    ):
        mock_crear_enlace.return_value = (
            self.respuesta_wompi_fake()
        )

        self.client.force_login(
            self.admin_user
        )

        url = reverse(
            "pagar_suscripcion"
        )

        response_1 = self.client.post(
            url
        )

        response_2 = self.client.post(
            url
        )

        self.assertEqual(
            response_1.status_code,
            302,
        )

        self.assertEqual(
            response_2.status_code,
            302,
        )

        self.assertEqual(
            response_1["Location"],
            self.WOMPI_URL_FAKE,
        )

        self.assertEqual(
            response_2["Location"],
            self.WOMPI_URL_FAKE,
        )

        pagos = (
            PagoWompi.objects
            .filter(
                tipo="SUSCRIPCION"
            )
        )

        self.assertEqual(
            pagos.count(),
            1,
        )

        self.assertEqual(
            mock_crear_enlace.call_count,
            1,
        )


    @patch(
        "pedidos.views._wompi_crear_enlace_pago"
    )
    def test_suscripcion_pendiente_sin_url_reutiliza_registro(
        self,
        mock_crear_enlace,
    ):
        mock_crear_enlace.return_value = (
            self.respuesta_wompi_fake()
        )

        self.client.force_login(
            self.admin_user
        )

        pago_original = (
            PagoWompi.objects.create(
                tipo="SUSCRIPCION",
                tenant=self.tenant,
                referencia=(
                    "SUBS-TEST-PENDIENTE"
                ),
                monto=Decimal("50.00"),
                estado="PENDIENTE",
            )
        )

        response = self.client.post(
            reverse(
                "pagar_suscripcion"
            )
        )

        self.assertEqual(
            response.status_code,
            302,
        )

        self.assertEqual(
            response["Location"],
            self.WOMPI_URL_FAKE,
        )

        pagos = (
            PagoWompi.objects
            .filter(
                tipo="SUSCRIPCION"
            )
        )

        self.assertEqual(
            pagos.count(),
            1,
        )

        pago_original.refresh_from_db()

        self.assertEqual(
            pago_original.url_enlace,
            self.WOMPI_URL_FAKE,
        )

        self.assertEqual(
            mock_crear_enlace.call_count,
            1,
        )

    def crear_pedido_tarjeta(self, telefono):
        pedido = self.crear_pedido(
            estado="PENDIENTE",
            telefono=telefono,
        )

        pedido.metodo_pago = "TARJETA"
        pedido.estado = "PENDIENTE"
        pedido.save()

        pedido.refresh_from_db()

        return pedido

    def crear_pago_pendiente(self, pedido):
        referencia = (
            pedido.wompi_referencia
            or f"ORDEN-{pedido.id}-TESTSEC"
        )

        pedido.wompi_referencia = referencia
        pedido.save()

        pago = PagoWompi.objects.create(
            tipo="PEDIDO",
            tenant=pedido.sucursal.tenant,
            pedido=pedido,
            referencia=referencia,
            monto=pedido.total_final,
            estado="PENDIENTE",
        )

        return pago

    def respuesta_wompi_fake(self):
        return (
            {
                "urlEnlace": self.WOMPI_URL_FAKE,
                "idEnlace": "LINK-SECURITY-TEST",
            },
            {
                "mock": True,
            },
        )

    @patch("pedidos.views._wompi_crear_enlace_pago")
    def test_get_no_debe_iniciar_pago_de_pedido(
        self,
        mock_crear_enlace,
    ):
        mock_crear_enlace.return_value = (
            self.respuesta_wompi_fake()
        )

        pedido = self.crear_pedido_tarjeta(
            "79000001"
        )

        response = self.client.get(
            reverse(
                "pagar_wompi",
                args=[pedido.tracking_token],
            )
        )

        self.assertEqual(
            response.status_code,
            405,
        )

        self.assertEqual(
            PagoWompi.objects.filter(
                pedido=pedido
            ).count(),
            0,
        )

        mock_crear_enlace.assert_not_called()

    @patch("pedidos.views._wompi_crear_enlace_pago")
    def test_get_no_debe_iniciar_pago_de_suscripcion(
        self,
        mock_crear_enlace,
    ):
        mock_crear_enlace.return_value = (
            self.respuesta_wompi_fake()
        )

        self.client.force_login(
            self.admin_user
        )

        response = self.client.get(
            reverse("pagar_suscripcion")
        )

        self.assertEqual(
            response.status_code,
            405,
        )

        self.assertFalse(
            PagoWompi.objects.filter(
                tipo="SUSCRIPCION"
            ).exists()
        )

        mock_crear_enlace.assert_not_called()

    @patch("pedidos.views._wompi_crear_enlace_pago")
    def test_post_si_puede_iniciar_pago_de_pedido(
        self,
        mock_crear_enlace,
    ):
        mock_crear_enlace.return_value = (
            self.respuesta_wompi_fake()
        )

        pedido = self.crear_pedido_tarjeta(
            "79000002"
        )

        response = self.client.post(
            reverse(
                "pagar_wompi",
                args=[pedido.tracking_token],
            )
        )

        self.assertEqual(
            response.status_code,
            302,
        )

        self.assertEqual(
            response["Location"],
            self.WOMPI_URL_FAKE,
        )

        self.assertEqual(
            PagoWompi.objects.filter(
                pedido=pedido
            ).count(),
            1,
        )

    @patch("pedidos.views._wompi_crear_enlace_pago")
    def test_repetir_inicio_pago_no_crea_dos_registros(
        self,
        mock_crear_enlace,
    ):
        mock_crear_enlace.return_value = (
            self.respuesta_wompi_fake()
        )

        pedido = self.crear_pedido_tarjeta(
            "79000003"
        )

        url = reverse(
            "pagar_wompi",
            args=[pedido.tracking_token],
        )

        self.client.post(url)
        self.client.post(url)

        self.assertEqual(
            PagoWompi.objects.filter(
                pedido=pedido
            ).count(),
            1,
        )

        self.assertEqual(
            mock_crear_enlace.call_count,
            1,
        )

    @patch("pedidos.views._wompi_crear_enlace_pago")
    def test_pago_pendiente_sin_url_se_reutiliza(
        self,
        mock_crear_enlace,
    ):
        mock_crear_enlace.return_value = (
            self.respuesta_wompi_fake()
        )

        pedido = self.crear_pedido_tarjeta(
            "79000004"
        )

        pago_original = (
            self.crear_pago_pendiente(
                pedido
            )
        )

        response = self.client.post(
            reverse(
                "pagar_wompi",
                args=[pedido.tracking_token],
            )
        )

        self.assertEqual(
            response.status_code,
            302,
        )

        self.assertEqual(
            response["Location"],
            self.WOMPI_URL_FAKE,
        )

        self.assertEqual(
            PagoWompi.objects.filter(
                pedido=pedido
            ).count(),
            1,
        )

        pago_original.refresh_from_db()

        self.assertEqual(
            pago_original.url_enlace,
            self.WOMPI_URL_FAKE,
        )

    def test_webhook_get_es_rechazado(self):
        response = self.client.get(
            reverse("wompi_webhook")
        )

        self.assertEqual(
            response.status_code,
            405,
        )

    def test_webhook_sin_firma_es_rechazado(self):
        pedido = self.crear_pedido_tarjeta(
            "79000005"
        )

        pago = self.crear_pago_pendiente(
            pedido
        )

        body = {
            "transaccion": {
                "identificadorEnlaceComercio":
                    pago.referencia,
                "esAprobada": True,
                "idTransaccion":
                    "TX-SIN-FIRMA",
                "monto":
                    str(pago.monto),
            }
        }

        response = self.client.post(
            reverse("wompi_webhook"),
            data=json.dumps(body),
            content_type="application/json",
        )

        self.assertEqual(
            response.status_code,
            403,
        )

        pedido.refresh_from_db()
        pago.refresh_from_db()

        self.assertFalse(
            pedido.pago_verificado
        )

        self.assertNotEqual(
            pago.estado,
            "APROBADO",
        )

    @patch(
        "pedidos.views._validar_hash_webhook_wompi",
        return_value=True,
    )
    def test_webhook_aprobado_con_monto_incorrecto_no_confirma(
        self,
        mock_hash,
    ):
        pedido = self.crear_pedido_tarjeta(
            "79000006"
        )

        pago = self.crear_pago_pendiente(
            pedido
        )

        body = {
            "transaccion": {
                "identificadorEnlaceComercio":
                    pago.referencia,
                "esAprobada": True,
                "idTransaccion":
                    "TX-MONTO-MALO",
                "monto":
                    "0.01",
            }
        }

        self.client.post(
            reverse("wompi_webhook"),
            data=json.dumps(body),
            content_type="application/json",
        )

        pedido.refresh_from_db()
        pago.refresh_from_db()

        self.assertFalse(
            pedido.pago_verificado
        )

        self.assertNotEqual(
            pago.estado,
            "APROBADO",
        )

    @patch(
        "pedidos.views._validar_hash_webhook_wompi",
        return_value=True,
    )
    def test_webhook_aprobado_sin_monto_no_confirma(
        self,
        mock_hash,
    ):
        pedido = self.crear_pedido_tarjeta(
            "79000007"
        )

        pago = self.crear_pago_pendiente(
            pedido
        )

        body = {
            "transaccion": {
                "identificadorEnlaceComercio":
                    pago.referencia,
                "esAprobada": True,
                "idTransaccion":
                    "TX-SIN-MONTO",
            }
        }

        self.client.post(
            reverse("wompi_webhook"),
            data=json.dumps(body),
            content_type="application/json",
        )

        pedido.refresh_from_db()
        pago.refresh_from_db()

        self.assertFalse(
            pedido.pago_verificado
        )

        self.assertNotEqual(
            pago.estado,
            "APROBADO",
        )
        
    @patch(
        "pedidos.views._validar_hash_webhook_wompi",
        return_value=True,
    )
    def test_replay_mismo_webhook_pedido_no_duplica_efecto(
        self,
        mock_hash,
    ):
        pedido = self.crear_pedido_tarjeta(
        "79000008"
        )

        pago = self.crear_pago_pendiente(
            pedido
        )

        body = {
            "transaccion": {
                "identificadorEnlaceComercio":
                    pago.referencia,
                "esAprobada": True,
                "idTransaccion":
                    "TX-REPLAY-PEDIDO",
                "monto":
                    str(pago.monto),
            }
        }

        url = reverse("wompi_webhook")

        self.client.post(
            url,
            data=json.dumps(body),
            content_type="application/json",
        )

        pedido.refresh_from_db()
        pago.refresh_from_db()

        fecha_pago_1 = (
            pedido.fecha_pago_verificado
        )

        fecha_aprobacion_1 = (
            pago.fecha_aprobacion
        )

        self.client.post(
            url,
            data=json.dumps(body),
            content_type="application/json",
        )

        pedido.refresh_from_db()
        pago.refresh_from_db()

        self.assertTrue(
            pedido.pago_verificado
        )

        self.assertEqual(
            pedido.fecha_pago_verificado,
            fecha_pago_1,
        )

        self.assertEqual(
            pago.fecha_aprobacion,
            fecha_aprobacion_1,
        )

    @patch(
        "pedidos.views._validar_hash_webhook_wompi",
        return_value=True,
    )
    def test_replay_suscripcion_no_agrega_60_dias(
        self,
        mock_hash,
    ):
        fecha_inicial = (
            date.today()
            + timedelta(days=10)
        )

        self.suscripcion.fecha_vencimiento = (
            fecha_inicial
        )
        self.suscripcion.estado = (
            SuscripcionTenant.Estado.ACTIVA
        )
        self.suscripcion.save()

        pago = PagoWompi.objects.create(
            tipo="SUSCRIPCION",
            tenant=self.tenant,
            referencia="SUBS-TEST-REPLAY",
            monto=Decimal("50.00"),
            estado="PENDIENTE",
        )

        body = {
            "transaccion": {
                "identificadorEnlaceComercio":
                    pago.referencia,
                "esAprobada": True,
                "idTransaccion":
                    "TX-SUBS-REPLAY",
                "monto":
                    str(pago.monto),
            }
        }

        url = reverse("wompi_webhook")

        self.client.post(
            url,
            data=json.dumps(body),
            content_type="application/json",
        )

        self.client.post(
            url,
            data=json.dumps(body),
            content_type="application/json",
        )

        self.suscripcion.refresh_from_db()

        self.assertEqual(
            self.suscripcion.fecha_vencimiento,
            fecha_inicial
            + timedelta(days=30),
        )


    @patch(
        "pedidos.views._validar_hash_webhook_wompi",
        return_value=True,
    )
    def test_misma_transaccion_no_puede_aprobar_dos_pagos(
        self,
        mock_hash,
    ):
        pedido_1 = self.crear_pedido_tarjeta(
            "79000009"
        )

        pedido_2 = self.crear_pedido_tarjeta(
            "79000010"
        )

        pago_1 = self.crear_pago_pendiente(
            pedido_1
        )

        pago_2 = self.crear_pago_pendiente(
            pedido_2
        )

        tx_repetida = "TX-GLOBAL-DUPLICADA"

        body_1 = {
            "transaccion": {
                "identificadorEnlaceComercio":
                    pago_1.referencia,
                "esAprobada": True,
                "idTransaccion":
                    tx_repetida,
                "monto":
                    str(pago_1.monto),
            }
        }

        body_2 = {
            "transaccion": {
                "identificadorEnlaceComercio":
                    pago_2.referencia,
                "esAprobada": True,
                "idTransaccion":
                    tx_repetida,
                "monto":
                    str(pago_2.monto),
            }
        }

        url = reverse("wompi_webhook")

        self.client.post(
            url,
            data=json.dumps(body_1),
            content_type="application/json",
        )

        self.client.post(
            url,
            data=json.dumps(body_2),
            content_type="application/json",
        )

        pedido_1.refresh_from_db()
        pedido_2.refresh_from_db()

        self.assertTrue(
            pedido_1.pago_verificado
        )

        # Una transacción Wompi real
        # jamás debe poder pagar dos pedidos.
        self.assertFalse(
            pedido_2.pago_verificado
        )

    @patch(
        "pedidos.views._validar_hash_webhook_wompi",
        return_value=True,
    )
    def test_webhook_aprobado_sin_id_transaccion_no_confirma(
        self,
        mock_hash,
    ):
        pedido = self.crear_pedido_tarjeta(
            "79000011"
        )

        pago = self.crear_pago_pendiente(
            pedido
        )

        body = {
            "transaccion": {
                "identificadorEnlaceComercio":
                    pago.referencia,
                "esAprobada": True,
                "monto":
                    str(pago.monto),
            }
        }

        self.client.post(
            reverse("wompi_webhook"),
            data=json.dumps(body),
            content_type="application/json",
        )

        pedido.refresh_from_db()
        pago.refresh_from_db()

        self.assertFalse(
            pedido.pago_verificado
        )

        self.assertNotEqual(
            pago.estado,
            "APROBADO",
        )

    def test_redirect_suscripcion_sin_hash_no_renueva(
        self,
    ):
        self.client.force_login(
            self.admin_user
        )

        vencimiento_inicial = (
            self.suscripcion.fecha_vencimiento
        )

        pago = PagoWompi.objects.create(
            tipo="SUSCRIPCION",
            tenant=self.tenant,
            referencia=(
                "SUBS-REDIRECT-SIN-HASH"
            ),
            monto=Decimal("50.00"),
            estado="PENDIENTE",
        )

        response = self.client.get(
            reverse(
                "wompi_suscripcion_respuesta"
            ),
            {
                "ref": pago.referencia,
                "idTransaccion": (
                    "TX-SUB-SIN-HASH"
                ),
                "monto": str(
                    pago.monto
                ),
            },
        )

        self.assertEqual(
            response.status_code,
            200,
        )

        self.suscripcion.refresh_from_db()
        pago.refresh_from_db()

        self.assertEqual(
            self.suscripcion.fecha_vencimiento,
            vencimiento_inicial,
        )

        self.assertNotEqual(
            pago.estado,
            "APROBADO",
        )

    def test_redirect_sin_hash_no_confirma_pago(self):
        pedido = self.crear_pedido_tarjeta(
            "79000012"
        )

        pago = self.crear_pago_pendiente(
            pedido
        )

        response = self.client.get(
            "/wompi-respuesta/",
            {
                "ref": pago.referencia,
                "idTransaccion":
                    "TX-REDIRECT-SIN-HASH",
                "monto":
                    str(pago.monto),
            },
        )

        self.assertEqual(
            response.status_code,
            302,
        )

        pedido.refresh_from_db()
        pago.refresh_from_db()

        self.assertFalse(
            pedido.pago_verificado
        )

        self.assertNotEqual(
            pago.estado,
            "APROBADO",
        )

    def test_iniciar_pago_pedido_sin_csrf_es_rechazado(
        self
    ):
        csrf_client = Client(
            enforce_csrf_checks=True
        )

        pedido = self.crear_pedido_tarjeta(
            "79000013"
        )

        response = csrf_client.post(
            reverse(
                "pagar_wompi",
                args=[
                    pedido.tracking_token
                ],
            )
        )

        self.assertEqual(
            response.status_code,
            403,
        )

        self.assertFalse(
            PagoWompi.objects.filter(
                pedido=pedido
            ).exists()
        )

    def test_iniciar_suscripcion_sin_csrf_es_rechazado(
        self
    ):
        csrf_client = Client(
            enforce_csrf_checks=True
        )

        csrf_client.force_login(
            self.admin_user
        )

        response = csrf_client.post(
            reverse("pagar_suscripcion")
        )

        self.assertEqual(
            response.status_code,
            403,
        )

        self.assertFalse(
            PagoWompi.objects.filter(
                tipo="SUSCRIPCION"
            ).exists()
        )
        
    def test_base_datos_impide_id_transaccion_duplicado(self):
        pedido_1 = self.crear_pedido_tarjeta(
            "79000014"
        )

        pedido_2 = self.crear_pedido_tarjeta(
            "79000015"
        )

        pago_1 = self.crear_pago_pendiente(
            pedido_1
        )

        pago_2 = self.crear_pago_pendiente(
            pedido_2
        )

        pago_1.id_transaccion = (
            "TX-UNIQUE-DB-TEST"
        )
        pago_1.save()

        pago_2.id_transaccion = (
            "TX-UNIQUE-DB-TEST"
        )

        with self.assertRaises(
            IntegrityError
        ):
            with transaction.atomic():
                pago_2.save()
                
                
                
class WompiTenantReferenceTests(
    TestCase
):

    def test_referencia_pedido_incluye_tenant(
        self,
    ):
        referencia = (
            _wompi_crear_referencia(
                "ORDEN",
                17,
                250,
            )
        )

        self.assertTrue(
            referencia.startswith(
                "ORDEN-T17-P250-"
            )
        )

        self.assertEqual(
            _wompi_tenant_id_desde_referencia(
                referencia
            ),
            17,
        )

    def test_referencia_suscripcion_incluye_tenant(
        self,
    ):
        referencia = (
            _wompi_crear_referencia(
                "SUBS",
                23,
            )
        )

        self.assertTrue(
            referencia.startswith(
                "SUBS-T23-"
            )
        )

        self.assertEqual(
            _wompi_tenant_id_desde_referencia(
                referencia
            ),
            23,
        )

    def test_referencia_legacy_o_manipulada_no_inventa_tenant(
        self,
    ):
        referencias_invalidas = [
            "ORDEN-123-ABCDEF123456",
            "SUBS-5-ABCDEF123456",
            "ORDEN-T0-P1-ABCDEF123456",
            "ORDEN-T5-P0-ABCDEF123456",
            "ORDEN-T5-P10-TOKENINVALIDO",
            "cualquier-cosa",
            "",
        ]

        for referencia in referencias_invalidas:
            with self.subTest(
                referencia=referencia
            ):
                self.assertIsNone(
                    _wompi_tenant_id_desde_referencia(
                        referencia
                    )
                )
             
                
class PaymentRecoverySecurityTests(FoodBackTestBase):
    """
    FB-SEC-003B2:
    Recuperación segura cuando Wompi falla,
    rechaza el pago o el cliente abandona el flujo.
    """

    WOMPI_URL_FAKE = (
        "https://wompi.test/"
        "payment-recovery"
    )

    def crear_pedido_tarjeta_con_detalle(
        self,
        telefono,
    ):
        pedido = self.crear_pedido(
            estado="PENDIENTE",
            telefono=telefono,
        )

        pedido.metodo_pago = "TARJETA"
        pedido.estado = "PENDIENTE"
        pedido.save()

        detalle = DetallePedido.objects.create(
            pedido=pedido,
            producto=self.producto,
            cantidad=2,
            precio_unitario=self.producto.precio,
        )

        detalle.save()

        pedido.refresh_from_db()

        return pedido, detalle

    def crear_pago_pendiente(
        self,
        pedido,
        referencia=None,
    ):
        referencia = (
            referencia
            or f"ORDEN-{pedido.id}-RECOVERY"
        )

        pedido.wompi_referencia = referencia
        pedido.save(
            update_fields=[
                "wompi_referencia"
            ]
        )

        return PagoWompi.objects.create(
            tipo="PEDIDO",
            tenant=pedido.sucursal.tenant,
            pedido=pedido,
            referencia=referencia,
            monto=pedido.total_final,
            estado="PENDIENTE",
        )

    @patch(
        "pedidos.views._validar_hash_webhook_wompi",
        return_value=True,
    )
    def test_pago_rechazado_conserva_pedido_y_detalles(
        self,
        mock_hash,
    ):
        pedido, detalle = (
            self.crear_pedido_tarjeta_con_detalle(
                "79100001"
            )
        )

        pago = self.crear_pago_pendiente(
            pedido
        )

        body = {
            "transaccion": {
                "identificadorEnlaceComercio":
                    pago.referencia,
                "esAprobada": False,
                "idTransaccion":
                    "TX-RECHAZADA-RECOVERY",
                "monto":
                    str(pago.monto),
            }
        }

        response = self.client.post(
            reverse("wompi_webhook"),
            data=json.dumps(body),
            content_type="application/json",
        )

        self.assertEqual(
            response.status_code,
            200,
        )

        pago.refresh_from_db()

        self.assertEqual(
            pago.estado,
            "RECHAZADO",
        )

        self.assertTrue(
            Pedido.objects.filter(
                pk=pedido.pk
            ).exists()
        )

        self.assertTrue(
            DetallePedido.objects.filter(
                pk=detalle.pk
            ).exists()
        )

        pedido.refresh_from_db()

        self.assertEqual(
            pedido.estado,
            "PENDIENTE",
        )

        self.assertFalse(
            pedido.pago_verificado
        )

    def test_pedido_pendiente_antiguo_no_se_borra_fisicamente(
        self
    ):
        pedido, detalle = (
            self.crear_pedido_tarjeta_con_detalle(
                "79100002"
            )
        )

        self.crear_pago_pendiente(
            pedido
        )

        Pedido.objects.filter(
            pk=pedido.pk
        ).update(
            fecha_creacion=(
                timezone.now()
                - timedelta(hours=2)
            )
        )

        self.client.get(
            reverse("menu")
        )

        self.assertTrue(
            Pedido.objects.filter(
                pk=pedido.pk
            ).exists()
        )

        self.assertTrue(
            DetallePedido.objects.filter(
                pk=detalle.pk
            ).exists()
        )

    def test_checkout_recupera_pedido_pendiente(
        self
    ):
        pedido, detalle = (
            self.crear_pedido_tarjeta_con_detalle(
                "79100003"
            )
        )

        pago = self.crear_pago_pendiente(
            pedido
        )

        pago.url_enlace = (
            self.WOMPI_URL_FAKE
        )
        pago.save()

        session = self.client.session

        session["ultimo_pedido_id"] = (
            pedido.id
        )

        session["cart"] = {}

        session.save()

        response = self.client.get(
            reverse("checkout")
        )

        self.assertEqual(
            response.status_code,
            200,
        )

        # El checkout de recuperación debe
        # mostrar el pedido existente.
        self.assertContains(
            response,
            self.producto.nombre,
        )

        self.assertContains(
            response,
            "Pago pendiente",
        )

    @patch(
        "pedidos.views._wompi_crear_enlace_pago"
    )
    def test_reintento_tras_rechazo_crea_nueva_referencia(
        self,
        mock_crear_enlace,
    ):
        mock_crear_enlace.return_value = (
            {
                "urlEnlace":
                    self.WOMPI_URL_FAKE,
                "idEnlace":
                    "LINK-RECOVERY-2",
            },
            {
                "mock": True,
            },
        )

        pedido, detalle = (
            self.crear_pedido_tarjeta_con_detalle(
                "79100004"
            )
        )

        pago_anterior = (
            self.crear_pago_pendiente(
                pedido
            )
        )

        referencia_anterior = (
            pago_anterior.referencia
        )

        pago_anterior.estado = (
            "RECHAZADO"
        )

        pago_anterior.save()

        response = self.client.post(
            reverse(
                "pagar_wompi",
                args=[
                    pedido.tracking_token
                ],
            )
        )

        self.assertEqual(
            response.status_code,
            302,
        )

        self.assertEqual(
            response["Location"],
            self.WOMPI_URL_FAKE,
        )

        pagos = list(
            PagoWompi.objects
            .filter(
                pedido=pedido
            )
            .order_by(
                "fecha_creacion"
            )
        )

        self.assertEqual(
            len(pagos),
            2,
        )

        self.assertNotEqual(
            pagos[0].referencia,
            pagos[1].referencia,
        )

        pedido.refresh_from_db()

        self.assertNotEqual(
            pedido.wompi_referencia,
            referencia_anterior,
        )

        # El reintento NO crea otro pedido.
        self.assertEqual(
            Pedido.objects.filter(
                pk=pedido.pk
            ).count(),
            1,
        )

    @patch(
        "pedidos.views._validar_hash_redirect_wompi",
        return_value=True,
    )
    def test_pago_exitoso_muestra_confirmacion_inmediatamente(
        self,
        mock_hash,
    ):
        pedido, detalle = (
            self.crear_pedido_tarjeta_con_detalle(
                "79100005"
            )
        )

        pago = self.crear_pago_pendiente(
            pedido
        )

        response = self.client.get(
            reverse("wompi_respuesta"),
            {
                "ref":
                    pago.referencia,
                "idTransaccion":
                    "TX-RECOVERY-OK",
                "monto":
                    str(pago.monto),
                "esAprobada":
                    "true",
            },
            follow=True,
        )

        self.assertEqual(
            response.status_code,
            200,
        )

        pedido.refresh_from_db()

        self.assertTrue(
            pedido.pago_verificado
        )

        self.assertEqual(
            pedido.estado,
            "RECIBIDO",
        )

        # El mensaje debe verse en la
        # misma pantalla del tracker.
        self.assertContains(
            response,
            "Pago confirmado. "
            "Tu pedido fue recibido.",
        )

    @patch(
        "pedidos.views._wompi_crear_enlace_pago"
    )
    def test_pedido_ya_pagado_no_puede_iniciar_otro_pago(
        self,
        mock_crear_enlace,
    ):
        pedido, detalle = (
            self.crear_pedido_tarjeta_con_detalle(
                "79100006"
            )
        )

        pago = self.crear_pago_pendiente(
            pedido
        )

        pago.estado = "APROBADO"
        pago.es_aprobada = True
        pago.id_transaccion = (
            "TX-YA-PAGADA"
        )
        pago.save()

        pedido.estado = "RECIBIDO"
        pedido.pago_verificado = True
        pedido.wompi_id_transaccion = (
            pago.id_transaccion
        )
        pedido.save()

        response = self.client.post(
            reverse(
                "pagar_wompi",
                args=[
                    pedido.tracking_token
                ],
            )
        )

        self.assertEqual(
            response.status_code,
            302,
        )

        self.assertEqual(
            response["Location"],
            reverse(
                "order_tracker",
                args=[
                    pedido.tracking_token
                ],
            ),
        )

        self.assertEqual(
            PagoWompi.objects.filter(
                pedido=pedido
            ).count(),
            1,
        )

        mock_crear_enlace.assert_not_called()

    @patch(
        "pedidos.views._wompi_crear_enlace_pago"
    )
    def test_error_wompi_no_deja_cliente_sin_sus_datos(
        self,
        mock_crear_enlace,
    ):
        mock_crear_enlace.side_effect = (
            Exception(
                "Timeout Wompi simulado"
            )
        )

        session = self.client.session

        session["cart"] = {
            f"{self.producto.id}-0-0": 2
        }

        session.save()
        
        checkout_token = (
            self._crear_checkout_token_test()
        )

        response = self.client.post(
            reverse("checkout"),
            {
                "checkout_token": checkout_token,
                "telefono":
                    "79100007",
                "nombre":
                    "Cliente",
                "apellido":
                    "Recovery",
                "direccion":
                    "Dirección de prueba",
                "metodo_pago":
                    "TARJETA",
                "latitud":
                    "13.4800",
                "longitud":
                    "-88.1800",
            },
            follow=True,
        )

        self.assertEqual(
            response.status_code,
            200,
        )

        pedido = (
            Pedido.objects
            .filter(
                cliente__telefono=
                    "79100007"
            )
            .first()
        )

        self.assertIsNotNone(
            pedido
        )

        self.assertTrue(
            pedido.detalles.exists()
        )

        # Aunque Wompi haya fallado,
        # el usuario debe poder continuar
        # viendo/recuperando su pedido.
        self.assertContains(
            response,
            self.producto.nombre,
        )

        self.assertContains(
            response,
            "Pago pendiente",
        )
        
class PaymentResilienceLoggingTests(
    FoodBackTestBase
):
    """
    FB-SEC-003B3-A:
    Los fallos financieros deben generar
    telemetría segura e idempotente.
    """

    def crear_pedido_tarjeta(
        self,
        telefono,
    ):
        pedido = self.crear_pedido(
            estado="PENDIENTE",
            telefono=telefono,
        )

        pedido.metodo_pago = "TARJETA"
        pedido.save()

        return pedido
    

    def crear_pago(
        self,
        pedido,
    ):
        referencia = (
            f"ORDEN-{pedido.id}-LOGTEST"
        )

        pedido.wompi_referencia = referencia
        pedido.save(
            update_fields=[
                "wompi_referencia"
            ]
        )

        return PagoWompi.objects.create(
            tipo="PEDIDO",
            tenant=pedido.sucursal.tenant,
            pedido=pedido,
            referencia=referencia,
            monto=pedido.total_final,
            estado="PENDIENTE",
            cliente_token_hash=(
                "a" * 64
            ),
        )

    @patch(
        "pedidos.views."
        "_validar_hash_webhook_wompi",
        return_value=True,
    )
    def test_rechazo_genera_evento_para_cliente(
        self,
        mock_hash,
    ):
        pedido = self.crear_pedido_tarjeta(
            "79200001"
        )

        pago = self.crear_pago(
            pedido
        )

        body = {
            "transaccion": {
                "identificadorEnlaceComercio":
                    pago.referencia,
                "esAprobada":
                    False,
                "idTransaccion":
                    "TX-RECHAZO-LOG",
                "monto":
                    str(pago.monto),
            }
        }

        response = self.client.post(
            reverse("wompi_webhook"),
            data=json.dumps(body),
            content_type="application/json",
        )

        self.assertEqual(
            response.status_code,
            200,
        )

        evento = (
            EventoPagoWompi.objects
            .get(
                pago=pago
            )
        )

        self.assertEqual(
            evento.categoria,
            "RECHAZO_CLIENTE",
        )

        self.assertTrue(
            evento.cuenta_para_cliente
        )

        self.assertFalse(
            evento.cuenta_para_global
        )

        self.assertEqual(
            evento.cliente_token_hash,
            pago.cliente_token_hash,
        )

    @patch(
        "pedidos.views."
        "_validar_hash_webhook_wompi",
        return_value=True,
    )
    def test_replay_rechazo_no_duplica_eventos(
        self,
        mock_hash,
    ):
        pedido = self.crear_pedido_tarjeta(
            "79200002"
        )

        pago = self.crear_pago(
            pedido
        )

        body = {
            "transaccion": {
                "identificadorEnlaceComercio":
                    pago.referencia,
                "esAprobada":
                    False,
                "idTransaccion":
                    "TX-REPLAY-RECHAZO",
                "monto":
                    str(pago.monto),
            }
        }

        url = reverse(
            "wompi_webhook"
        )

        for _ in range(5):
            self.client.post(
                url,
                data=json.dumps(body),
                content_type=(
                    "application/json"
                ),
            )

        self.assertEqual(
            EventoPagoWompi.objects.filter(
                pago=pago,
                categoria="RECHAZO_CLIENTE",
            ).count(),
            1,
        )

    @patch(
        "pedidos.views."
        "_wompi_crear_enlace_pago"
    )
    def test_error_tecnico_genera_evento_global(
        self,
        mock_wompi,
    ):
        mock_wompi.side_effect = Exception(
            "Timeout simulado"
        )

        pedido = self.crear_pedido_tarjeta(
            "79200003"
        )

        response = self.client.post(
            reverse(
                "pagar_wompi",
                args=[
                    pedido.tracking_token
                ],
            )
        )

        self.assertEqual(
            response.status_code,
            302,
        )

        pago = (
            PagoWompi.objects
            .filter(
                pedido=pedido
            )
            .latest(
                "fecha_creacion"
            )
        )
        
        pago.refresh_from_db()
        pedido.refresh_from_db()

        self.assertEqual(
            pago.estado,
            "ERROR",
        )

        self.assertFalse(
            pago.es_aprobada
        )

        self.assertEqual(
            pedido.estado,
            "PENDIENTE",
        )

        self.assertFalse(
            pedido.pago_verificado
        )

        evento = (
            EventoPagoWompi.objects
            .get(
                pago=pago,
                categoria="ERROR_TECNICO",
            )
        )

        self.assertFalse(
            evento.cuenta_para_cliente
        )

        self.assertTrue(
            evento.cuenta_para_global
        )

        self.assertEqual(
            len(
                pago.cliente_token_hash
            ),
            64,
        )
    
    @override_settings(
    FOODBACK_DEFAULT_TENANT_SLUG="",
    FOODBACK_BASE_DOMAIN="foodbacksv.com",
    )
    @patch(
        "pedidos.views._validar_hash_webhook_wompi",
        return_value=True,
    )
    def test_webhook_firmado_establece_tenant_db_sin_contexto_http(
        self,
        mock_hash,
    ):
        pedido = self.crear_pedido_tarjeta(
            "79990001"
        )

        pago = self.crear_pago(
            pedido
        )

        referencia = (
            _wompi_crear_referencia(
                "ORDEN",
                pago.tenant_id,
                pedido.id,
            )
        )

        pago.referencia = referencia
        pago.save(
            update_fields=[
                "referencia",
            ]
        )

        pedido.wompi_referencia = referencia
        pedido.save(
            update_fields=[
                "wompi_referencia",
            ]
        )

        contexto_capturado = {}

        def procesar_fake(
            referencia_recibida,
            **kwargs,
        ):
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT current_setting(
                        'foodback.tenant_id',
                        true
                    )
                    """
                )

                contexto_capturado[
                    "tenant_id"
                ] = cursor.fetchone()[0]

            return (
                True,
                "Pago procesado.",
            )

        body = {
            "transaccion": {
                "identificadorEnlaceComercio":
                    referencia,

                "esAprobada":
                    True,

                "idTransaccion":
                    "TX-TENANT-CONTEXT",

                "monto":
                    str(pago.monto),
            }
        }

        with patch(
            "pedidos.views."
            "_procesar_pago_wompi_aprobado",
            side_effect=procesar_fake,
        ):
            response = self.client.post(
                reverse(
                    "wompi_webhook"
                ),
                data=json.dumps(
                    body
                ),
                content_type=(
                    "application/json"
                ),
            )

        self.assertEqual(
            response.status_code,
            200,
        )

        # El middleware no pudo resolver Tenant
        # por sesión/host.
        self.assertIsNone(
            response.wsgi_request.tenant
        )

        # Pero el webhook firmado sí estableció
        # correctamente el Tenant en PostgreSQL.
        self.assertEqual(
            contexto_capturado[
                "tenant_id"
            ],
            str(
                pago.tenant_id
            ),
        )
        
class PaymentUserCooldownSecurityTests(
    FoodBackTestBase
):
    """
    FB-SEC-003B3-B/C

    Un número elevado de rechazos atribuibles al cliente
    debe suspender temporalmente TARJETA para ese navegador,
    sin afectar efectivo ni otros clientes.
    """

    WOMPI_URL_FAKE = (
        "https://wompi.test/"
        "user-cooldown"
    )

    def fijar_token_cliente(
        self,
        client=None,
        token="cliente-test-cooldown",
    ):
        client = client or self.client

        session = client.session

        session[
            "wompi_cliente_token"
        ] = token

        session.save()

        return hashlib.sha256(
            token.encode("utf-8")
        ).hexdigest()

    def crear_rechazos(
        self,
        cliente_token_hash,
        cantidad,
        prefijo="COOLDOWN",
    ):
        eventos = []

        for numero in range(cantidad):

            evento = (
                EventoPagoWompi.objects.create(
                    tenant=self.tenant,
                    cliente_token_hash=(
                        cliente_token_hash
                    ),
                    categoria=(
                        "RECHAZO_CLIENTE"
                    ),
                    origen="WEBHOOK",
                    codigo="WOMPI_RECHAZADO",
                    mensaje=(
                        "Rechazo de prueba."
                    ),
                    cuenta_para_cliente=True,
                    cuenta_para_global=False,
                    clave_evento=(
                        f"{prefijo}:"
                        f"{cliente_token_hash}:"
                        f"{numero}"
                    ),
                )
            )

            eventos.append(
                evento
            )

        return eventos

    def crear_pedido_tarjeta(
        self,
        telefono,
    ):
        pedido = self.crear_pedido(
            estado="PENDIENTE",
            telefono=telefono,
        )

        pedido.metodo_pago = (
            "TARJETA"
        )

        pedido.estado = (
            "PENDIENTE"
        )

        pedido.save()

        return pedido

    def respuesta_wompi_fake(self):
        return (
            {
                "urlEnlace":
                    self.WOMPI_URL_FAKE,

                "idEnlace":
                    "LINK-COOLDOWN",
            },
            {
                "mock": True,
            },
        )

    @patch(
        "pedidos.views."
        "_wompi_crear_enlace_pago"
    )
    def test_cuatro_rechazos_aun_permiten_tarjeta(
        self,
        mock_wompi,
    ):
        mock_wompi.return_value = (
            self.respuesta_wompi_fake()
        )

        token_hash = (
            self.fijar_token_cliente()
        )

        self.crear_rechazos(
            token_hash,
            4,
        )

        pedido = (
            self.crear_pedido_tarjeta(
                "79300001"
            )
        )

        response = self.client.post(
            reverse(
                "pagar_wompi",
                args=[
                    pedido.tracking_token
                ],
            )
        )

        self.assertEqual(
            response.status_code,
            302,
        )

        self.assertEqual(
            response["Location"],
            self.WOMPI_URL_FAKE,
        )

        mock_wompi.assert_called_once()

    @patch(
        "pedidos.views."
        "_wompi_crear_enlace_pago"
    )
    def test_cinco_rechazos_bloquean_tarjeta(
        self,
        mock_wompi,
    ):
        mock_wompi.return_value = (
            self.respuesta_wompi_fake()
        )

        token_hash = (
            self.fijar_token_cliente()
        )

        self.crear_rechazos(
            token_hash,
            5,
        )

        pedido = (
            self.crear_pedido_tarjeta(
                "79300002"
            )
        )

        response = self.client.post(
            reverse(
                "pagar_wompi",
                args=[
                    pedido.tracking_token
                ],
            )
        )

        self.assertEqual(
            response.status_code,
            302,
        )

        self.assertEqual(
            response["Location"],
            reverse("checkout"),
        )

        self.assertFalse(
            PagoWompi.objects.filter(
                pedido=pedido
            ).exists()
        )

        mock_wompi.assert_not_called()

    @patch(
        "pedidos.views."
        "_wompi_crear_enlace_pago"
    )
    def test_bloqueo_tambien_impide_reusar_enlace_pendiente(
        self,
        mock_wompi,
    ):
        token_hash = (
            self.fijar_token_cliente()
        )

        self.crear_rechazos(
            token_hash,
            5,
        )

        pedido = (
            self.crear_pedido_tarjeta(
                "79300003"
            )
        )

        referencia = (
            f"ORDEN-{pedido.id}-"
            f"COOLDOWN-PENDIENTE"
        )

        pedido.wompi_referencia = (
            referencia
        )

        pedido.save(
            update_fields=[
                "wompi_referencia"
            ]
        )

        pago = (
            PagoWompi.objects.create(
                tipo="PEDIDO",
                tenant=pedido.sucursal.tenant,
                pedido=pedido,
                referencia=referencia,
                monto=pedido.total_final,
                estado="PENDIENTE",
                url_enlace=(
                    self.WOMPI_URL_FAKE
                ),
                cliente_token_hash=(
                    token_hash
                ),
            )
        )

        response = self.client.post(
            reverse(
                "pagar_wompi",
                args=[
                    pedido.tracking_token
                ],
            )
        )

        self.assertEqual(
            response.status_code,
            302,
        )

        self.assertEqual(
            response["Location"],
            reverse("checkout"),
        )

        # No debe redirigir al enlace viejo.
        self.assertNotEqual(
            response["Location"],
            pago.url_enlace,
        )

        mock_wompi.assert_not_called()

    @patch(
        "pedidos.views."
        "_wompi_crear_enlace_pago"
    )
    def test_checkout_tarjeta_bloqueada_no_crea_pedido(
        self,
        mock_wompi,
    ):
        mock_wompi.return_value = (
            self.respuesta_wompi_fake()
        )

        token_hash = (
            self.fijar_token_cliente()
        )

        self.crear_rechazos(
            token_hash,
            5,
        )

        session = self.client.session

        session["cart"] = {
            f"{self.producto.id}-0-0":
                1
        }

        session.save()
        
        checkout_token = (
            self._crear_checkout_token_test()
        )

        response = self.client.post(
            reverse("checkout"),
            {
                "checkout_token": checkout_token,
                "telefono":
                    "79300004",

                "nombre":
                    "Cliente",

                "apellido":
                    "Bloqueado",

                "direccion":
                    "Dirección de prueba",

                "metodo_pago":
                    "TARJETA",

                "latitud":
                    "13.4800",

                "longitud":
                    "-88.1800",
            },
        )

        self.assertEqual(
            response.status_code,
            302,
        )

        self.assertEqual(
            response["Location"],
            reverse("checkout"),
        )

        self.assertFalse(
            Pedido.objects.filter(
                cliente__telefono=
                    "79300004"
            ).exists()
        )

        mock_wompi.assert_not_called()

    def test_efectivo_sigue_disponible_durante_bloqueo(
        self
    ):
        token_hash = (
            self.fijar_token_cliente()
        )

        self.crear_rechazos(
            token_hash,
            5,
        )

        session = self.client.session

        session["cart"] = {
            f"{self.producto.id}-0-0":
                1
        }

        session.save()
        
        checkout_token = (
            self._crear_checkout_token_test()
        )

        response = self.client.post(
            reverse("checkout"),
            {
                "checkout_token": checkout_token,
                "telefono":
                    "79300005",

                "nombre":
                    "Cliente",

                "apellido":
                    "Efectivo",

                "direccion":
                    "Dirección de prueba",

                "metodo_pago":
                    "EFECTIVO",

                "latitud":
                    "13.4800",

                "longitud":
                    "-88.1800",
            },
        )

        self.assertEqual(
            response.status_code,
            302,
        )

        pedido = (
            Pedido.objects.get(
                cliente__telefono=
                    "79300005"
            )
        )

        self.assertEqual(
            pedido.metodo_pago,
            "EFECTIVO",
        )

        self.assertEqual(
            pedido.estado,
            "RECIBIDO",
        )

    @patch(
        "pedidos.views."
        "_wompi_crear_enlace_pago"
    )
    def test_otro_navegador_no_hereda_bloqueo(
        self,
        mock_wompi,
    ):
        mock_wompi.return_value = (
            self.respuesta_wompi_fake()
        )

        hash_bloqueado = (
            self.fijar_token_cliente(
                token=(
                    "navegador-bloqueado"
                )
            )
        )

        self.crear_rechazos(
            hash_bloqueado,
            5,
        )

        otro_cliente = Client()

        self.fijar_token_cliente(
            client=otro_cliente,
            token=(
                "navegador-distinto"
            ),
        )

        pedido = (
            self.crear_pedido_tarjeta(
                "79300006"
            )
        )

        response = otro_cliente.post(
            reverse(
                "pagar_wompi",
                args=[
                    pedido.tracking_token
                ],
            )
        )

        self.assertEqual(
            response.status_code,
            302,
        )

        self.assertEqual(
            response["Location"],
            self.WOMPI_URL_FAKE,
        )

        mock_wompi.assert_called_once()

    @patch(
        "pedidos.views."
        "_wompi_crear_enlace_pago"
    )
    def test_cooldown_expirado_reactiva_tarjeta(
        self,
        mock_wompi,
    ):
        mock_wompi.return_value = (
            self.respuesta_wompi_fake()
        )

        token_hash = (
            self.fijar_token_cliente()
        )

        eventos = self.crear_rechazos(
            token_hash,
            5,
            prefijo="COOLDOWN-OLD",
        )

        fecha_antigua = (
            timezone.now()
            - timedelta(minutes=31)
        )

        EventoPagoWompi.objects.filter(
            id__in=[
                evento.id
                for evento in eventos
            ]
        ).update(
            fecha=fecha_antigua
        )

        pedido = (
            self.crear_pedido_tarjeta(
                "79300007"
            )
        )

        response = self.client.post(
            reverse(
                "pagar_wompi",
                args=[
                    pedido.tracking_token
                ],
            )
        )

        self.assertEqual(
            response.status_code,
            302,
        )

        self.assertEqual(
            response["Location"],
            self.WOMPI_URL_FAKE,
        )

        mock_wompi.assert_called_once()

    def test_checkout_muestra_tarjeta_temporalmente_bloqueada(
        self
    ):
        token_hash = (
            self.fijar_token_cliente()
        )

        self.crear_rechazos(
            token_hash,
            5,
        )

        session = self.client.session

        session["cart"] = {
            f"{self.producto.id}-0-0":
                1
        }

        session.save()

        response = self.client.get(
            reverse("checkout")
        )

        self.assertEqual(
            response.status_code,
            200,
        )

        self.assertTrue(
            response.context[
                "tarjeta_cliente_bloqueada"
            ]
        )

        self.assertContains(
            response,
            (
                "Tarjeta temporalmente "
                "no disponible"
            ),
        )
        

class PaymentGlobalCircuitBreakerTests(
    FoodBackTestBase
):
    """
    FB-SEC-003B3-D/E/F

    El breaker global solamente debe activarse
    ante una concentración de errores técnicos
    provenientes de varios clientes distintos.
    """

    WOMPI_URL_FAKE = (
        "https://wompi.test/"
        "global-breaker"
    )
    
    def test_bd_impide_evento_wompi_sin_tenant(
        self,
    ):
        with self.assertRaises(
            IntegrityError
        ):
            with transaction.atomic():
                EventoPagoWompi.objects.create(
                    cliente_token_hash=(
                        "9" * 64
                    ),
                    categoria="ERROR_TECNICO",
                    origen="INICIO",
                    codigo="TEST_SIN_TENANT",
                    mensaje="Prueba.",
                    cuenta_para_cliente=False,
                    cuenta_para_global=True,
                    clave_evento=(
                        "TEST:SIN-TENANT:"
                        "EVENTO"
                    ),
                )


    def test_bd_impide_estado_pasarela_sin_tenant(
        self,
    ):
        with self.assertRaises(
            IntegrityError
        ):
            with transaction.atomic():
                EstadoPasarelaPago.objects.create()

    def crear_error_global(
        self,
        cliente_hash,
        numero,
        prefijo="GLOBAL",
        tenant=None,
    ):
        if tenant is None:
            tenant = self.tenant

        return EventoPagoWompi.objects.create(
            tenant=tenant,
            cliente_token_hash=cliente_hash,
            categoria="ERROR_TECNICO",
            origen="INICIO",
            codigo="WOMPI_CONNECTION_ERROR",
            mensaje="Error técnico simulado.",
            cuenta_para_cliente=False,
            cuenta_para_global=True,
            clave_evento=(
                f"{prefijo}:"
                f"{cliente_hash}:"
                f"{numero}"
            ),
        )
        
    def test_errores_de_otro_tenant_no_bloquean_este_tenant(
        self,
    ):
        tenant_b = Tenant.objects.create(
            nombre="Restaurante B Breaker",
            slug="restaurante-b-breaker",
        )

        for numero in range(5):
            EventoPagoWompi.objects.create(
                tenant=tenant_b,
                cliente_token_hash=(
                    f"{numero + 100:064x}"
                ),
                categoria="ERROR_TECNICO",
                origen="INICIO",
                codigo="WOMPI_CONNECTION_ERROR",
                mensaje="Error Tenant B",
                cuenta_para_cliente=False,
                cuenta_para_global=True,
                clave_evento=(
                    f"TENANT-B-BREAKER:"
                    f"{numero}"
                ),
            )

        estado_a = (
            _estado_bloqueo_pasarela_tenant(
                self.tenant
            )
        )

        estado_b = (
            _estado_bloqueo_pasarela_tenant(
                tenant_b
            )
        )

        self.assertFalse(
            estado_a["bloqueada"]
        )

        self.assertTrue(
            estado_b["bloqueada"]
        )

        self.assertFalse(
            EstadoPasarelaPago.objects
            .filter(
                tenant=self.tenant
            )
            .exists()
        )

        self.assertTrue(
            EstadoPasarelaPago.objects
            .filter(
                tenant=tenant_b
            )
            .exists()
        )

    def crear_errores_distintos(
        self,
        cantidad,
    ):
        eventos = []

        for numero in range(cantidad):
            cliente_hash = (
                f"{numero + 1:064x}"
            )

            eventos.append(
                self.crear_error_global(
                    cliente_hash,
                    numero,
                )
            )

        return eventos

    def crear_pedido_tarjeta(
        self,
        telefono,
    ):
        pedido = self.crear_pedido(
            estado="PENDIENTE",
            telefono=telefono,
        )

        pedido.metodo_pago = "TARJETA"
        pedido.estado = "PENDIENTE"
        pedido.save()

        return pedido

    def respuesta_wompi_fake(self):
        return (
            {
                "urlEnlace":
                    self.WOMPI_URL_FAKE,
                "idEnlace":
                    "LINK-GLOBAL-BREAKER",
            },
            {
                "mock": True,
            },
        )

    @patch(
        "pedidos.views."
        "_wompi_crear_enlace_pago"
    )
    def test_cuatro_errores_globales_no_bloquean(
        self,
        mock_wompi,
    ):
        mock_wompi.return_value = (
            self.respuesta_wompi_fake()
        )

        self.crear_errores_distintos(
            4
        )

        pedido = self.crear_pedido_tarjeta(
            "79400001"
        )

        response = self.client.post(
            reverse(
                "pagar_wompi",
                args=[
                    pedido.tracking_token
                ],
            )
        )

        self.assertEqual(
            response.status_code,
            302,
        )

        self.assertEqual(
            response["Location"],
            self.WOMPI_URL_FAKE,
        )

        mock_wompi.assert_called_once()

    @patch(
        "pedidos.views."
        "_wompi_crear_enlace_pago"
    )
    def test_cinco_errores_de_varios_clientes_bloquean_global(
        self,
        mock_wompi,
    ):
        mock_wompi.return_value = (
            self.respuesta_wompi_fake()
        )

        self.crear_errores_distintos(
            5
        )

        pedido = self.crear_pedido_tarjeta(
            "79400002"
        )

        response = self.client.post(
            reverse(
                "pagar_wompi",
                args=[
                    pedido.tracking_token
                ],
            )
        )

        self.assertEqual(
            response.status_code,
            302,
        )

        self.assertEqual(
            response["Location"],
            reverse("checkout"),
        )

        mock_wompi.assert_not_called()

        estado = (
            EstadoPasarelaPago.objects
            .get(
                tenant=self.tenant
            )
        )

        self.assertTrue(
            estado.bloqueada
        )

    @patch(
        "pedidos.views."
        "_wompi_crear_enlace_pago"
    )
    def test_cinco_errores_del_mismo_cliente_no_bloquean_global(
        self,
        mock_wompi,
    ):
        mock_wompi.return_value = (
            self.respuesta_wompi_fake()
        )

        cliente_hash = (
            "b" * 64
        )

        for numero in range(5):
            self.crear_error_global(
                cliente_hash,
                numero,
                prefijo="MISMO",
            )

        pedido = self.crear_pedido_tarjeta(
            "79400003"
        )

        response = self.client.post(
            reverse(
                "pagar_wompi",
                args=[
                    pedido.tracking_token
                ],
            )
        )

        self.assertEqual(
            response["Location"],
            self.WOMPI_URL_FAKE,
        )

        mock_wompi.assert_called_once()

    @patch(
        "pedidos.views."
        "_wompi_crear_enlace_pago"
    )
    def test_rechazos_cliente_no_activan_breaker_global(
        self,
        mock_wompi,
    ):
        mock_wompi.return_value = (
            self.respuesta_wompi_fake()
        )

        for numero in range(8):
            EventoPagoWompi.objects.create(
                cliente_token_hash=(
                    f"{numero + 50:064x}"
                ),
                categoria="RECHAZO_CLIENTE",
                origen="WEBHOOK",
                codigo="WOMPI_RECHAZADO",
                mensaje="Tarjeta rechazada.",
                cuenta_para_cliente=True,
                cuenta_para_global=False,
                clave_evento=(
                    f"RECHAZO-GLOBAL:"
                    f"{numero}"
                ),
                tenant=self.tenant,
            )

        pedido = self.crear_pedido_tarjeta(
            "79400004"
        )

        response = self.client.post(
            reverse(
                "pagar_wompi",
                args=[
                    pedido.tracking_token
                ],
            )
        )

        self.assertEqual(
            response["Location"],
            self.WOMPI_URL_FAKE,
        )

        mock_wompi.assert_called_once()

    def test_efectivo_funciona_con_breaker_global_activo(
        self
    ):
        EstadoPasarelaPago.objects.create(
            tenant=self.tenant,
            bloqueado_hasta=(
                timezone.now()
                + timedelta(minutes=15)
            ),
            codigo_motivo=(
                "WOMPI_TECHNICAL_FAILURES"
            ),
            motivo=(
                "Breaker global de prueba"
            ),
        )

        session = self.client.session

        session["cart"] = {
            f"{self.producto.id}-0-0": 1
        }

        session.save()
        
        checkout_token = (
            self._crear_checkout_token_test()
        )

        response = self.client.post(
            reverse("checkout"),
            {
                "checkout_token": checkout_token,
                "telefono":
                    "79400005",
                "nombre":
                    "Cliente",
                "apellido":
                    "Efectivo",
                "direccion":
                    "Dirección prueba",
                "metodo_pago":
                    "EFECTIVO",
                "latitud":
                    "13.4800",
                "longitud":
                    "-88.1800",
            },
        )

        self.assertEqual(
            response.status_code,
            302,
        )

        pedido = Pedido.objects.get(
            cliente__telefono=
                "79400005"
        )

        self.assertEqual(
            pedido.metodo_pago,
            "EFECTIVO",
        )

        self.assertEqual(
            pedido.estado,
            "RECIBIDO",
        )

    @patch(
        "pedidos.views."
        "_wompi_crear_enlace_pago"
    )
    def test_breaker_global_afecta_otro_navegador(
        self,
        mock_wompi,
    ):
        EstadoPasarelaPago.objects.create(
            tenant=self.tenant,
            bloqueado_hasta=(
                timezone.now()
                + timedelta(minutes=15)
            ),
            codigo_motivo=(
                "WOMPI_TECHNICAL_FAILURES"
            ),
        )

        otro_cliente = Client()

        pedido = self.crear_pedido_tarjeta(
            "79400006"
        )

        response = otro_cliente.post(
            reverse(
                "pagar_wompi",
                args=[
                    pedido.tracking_token
                ],
            )
        )

        self.assertEqual(
            response["Location"],
            reverse("checkout"),
        )

        mock_wompi.assert_not_called()

    @patch(
        "pedidos.views."
        "_wompi_crear_enlace_pago"
    )
    def test_breaker_expirado_reactiva_tarjeta(
        self,
        mock_wompi,
    ):
        mock_wompi.return_value = (
            self.respuesta_wompi_fake()
        )

        estado = (
            EstadoPasarelaPago.objects.create(
                configuracion_negocio=
                    self.config,

                bloqueado_hasta=(
                    timezone.now()
                    - timedelta(minutes=1)
                ),

                codigo_motivo=(
                    "WOMPI_TECHNICAL_FAILURES"
                ),
                tenant=self.tenant,
            )
        )

        pedido = self.crear_pedido_tarjeta(
            "79400007"
        )

        response = self.client.post(
            reverse(
                "pagar_wompi",
                args=[
                    pedido.tracking_token
                ],
            )
        )

        self.assertEqual(
            response["Location"],
            self.WOMPI_URL_FAKE,
        )

        mock_wompi.assert_called_once()

        estado.refresh_from_db()

        self.assertFalse(
            estado.bloqueada
        )

    @patch(
        "pedidos.views."
        "_wompi_crear_enlace_pago"
    )
    def test_bloqueo_manual_owner_no_expira_solo(
        self,
        mock_wompi,
    ):
        EstadoPasarelaPago.objects.create(
            tenant=self.tenant,
            bloqueo_manual=True,
            bloqueado_hasta=None,
            codigo_motivo="OWNER_MANUAL",
            motivo=(
                "Bloqueado manualmente"
            ),
        )

        pedido = self.crear_pedido_tarjeta(
            "79400008"
        )

        response = self.client.post(
            reverse(
                "pagar_wompi",
                args=[
                    pedido.tracking_token
                ],
            )
        )

        self.assertEqual(
            response["Location"],
            reverse("checkout"),
        )

        mock_wompi.assert_not_called()
        
class PaymentMethodPriceParityTests(
    FoodBackTestBase
):
    """
    FB-COMP-001

    El precio final publicado no puede cambiar
    solamente por elegir tarjeta en lugar de efectivo.
    """

    def test_tarjeta_no_agrega_recargo_al_pedido(
        self
    ):
        pedido = self.crear_pedido(
            estado="PENDIENTE",
            telefono="79500001",
        )

        pedido.metodo_pago = "TARJETA"
        pedido.total_productos = Decimal(
            "10.00"
        )
        pedido.save()

        pedido.refresh_from_db()

        self.assertEqual(
            pedido.comision_plataforma,
            Decimal("0.00"),
        )

        self.assertEqual(
            pedido.total_final,
            Decimal("10.00"),
        )

    def test_efectivo_y_tarjeta_tienen_mismo_total(
        self
    ):
        efectivo = self.crear_pedido(
            estado="RECIBIDO",
            telefono="79500002",
        )

        efectivo.metodo_pago = "EFECTIVO"
        efectivo.total_productos = Decimal(
            "17.50"
        )
        efectivo.save()

        tarjeta = self.crear_pedido(
            estado="PENDIENTE",
            telefono="79500003",
        )

        tarjeta.metodo_pago = "TARJETA"
        tarjeta.total_productos = Decimal(
            "17.50"
        )
        tarjeta.save()

        efectivo.refresh_from_db()
        tarjeta.refresh_from_db()

        self.assertEqual(
            efectivo.total_final,
            tarjeta.total_final,
        )

        self.assertEqual(
            tarjeta.total_final,
            Decimal("17.50"),
        )

    def test_checkout_no_anuncia_recargo_por_tarjeta(
        self
    ):
        session = self.client.session

        session["cart"] = {
            f"{self.producto.id}-0-0": 1
        }

        session.save()

        response = self.client.get(
            reverse("checkout")
        )

        self.assertEqual(
            response.status_code,
            200,
        )

        self.assertNotContains(
            response,
            "+5% Servicio Digital",
        )

        self.assertNotContains(
            response,
            "Incluye cargo por servicio digital",
        )

    def test_total_wompi_checkout_igual_total_productos(
        self
    ):
        session = self.client.session

        session["cart"] = {
            f"{self.producto.id}-0-0": 1
        }

        session.save()

        response = self.client.get(
            reverse("checkout")
        )

        self.assertEqual(
            response.status_code,
            200,
        )

        self.assertEqual(
            Decimal(
                str(
                    response.context[
                        "total_wompi"
                    ]
                )
            ).quantize(
                Decimal("0.01")
            ),
            Decimal(
                str(
                    response.context[
                        "total_productos"
                    ]
                )
            ).quantize(
                Decimal("0.01")
            ),
        )
        
class PaymentTrackerRecoveryTests(
    FoodBackTestBase
):
    """
    Un pedido pendiente de tarjeta debe poder
    retomarse desde el tracker únicamente desde
    la sesión que creó/conserva ese pedido.
    """

    def crear_pedido_pendiente_tarjeta(
        self,
        telefono,
    ):
        pedido = self.crear_pedido(
            estado="PENDIENTE",
            telefono=telefono,
        )

        pedido.metodo_pago = "TARJETA"
        pedido.estado = "PENDIENTE"
        pedido.pago_verificado = False
        pedido.save()

        DetallePedido.objects.create(
            pedido=pedido,
            producto=self.producto,
            cantidad=1,
            precio_unitario=self.producto.precio,
        )

        return pedido

    def asociar_pedido_a_sesion(
        self,
        pedido,
        client=None,
    ):
        client = client or self.client

        session = client.session
        session["ultimo_pedido_id"] = pedido.id
        session["historial_pedidos"] = [
            pedido.id
        ]
        session.save()

    def test_tracker_muestra_retomar_pago(
        self
    ):
        pedido = (
            self.crear_pedido_pendiente_tarjeta(
                "79600001"
            )
        )

        self.asociar_pedido_a_sesion(
            pedido
        )

        response = self.client.get(
            reverse(
                "order_tracker",
                args=[
                    pedido.tracking_token
                ],
            )
        )

        self.assertEqual(
            response.status_code,
            200,
        )

        self.assertContains(
            response,
            "Retomar pago",
        )
        
    @override_settings(
        FOODBACK_PAYMENT_RESUME_SESSION_LIMIT=2,
        FOODBACK_PAYMENT_RESUME_IP_LIMIT=50,
        FOODBACK_PAYMENT_RESUME_WINDOW_SECONDS=600,
        FOODBACK_PAYMENT_RESUME_BLOCK_SECONDS=900,
    )
    def test_retomar_pago_bloquea_flood_del_mismo_navegador(
        self
    ):
        pedido = (
            self.crear_pedido_pendiente_tarjeta(
                "79600020"
            )
        )

        self.asociar_pedido_a_sesion(
            pedido
        )

        url = reverse(
            "retomar_pago",
            args=[
                pedido.tracking_token
            ],
        )

        for _ in range(2):
            response = self.client.post(
                url,
                REMOTE_ADDR="192.0.2.220",
            )

            self.assertEqual(
                response.status_code,
                302,
            )

        response = self.client.post(
            url,
            REMOTE_ADDR="192.0.2.220",
        )

        self.assertEqual(
            response.status_code,
            429,
        )

        self.assertIn(
            "Retry-After",
            response.headers,
        )


    @override_settings(
        FOODBACK_PAYMENT_RESUME_SESSION_LIMIT=50,
        FOODBACK_PAYMENT_RESUME_IP_LIMIT=2,
        FOODBACK_PAYMENT_RESUME_WINDOW_SECONDS=600,
        FOODBACK_PAYMENT_RESUME_BLOCK_SECONDS=900,
    )
    def test_retomar_pago_tokens_falsos_consumen_limite_ip(
        self
    ):
        for _ in range(2):
            response = self.client.post(
                reverse(
                    "retomar_pago",
                    args=[
                        uuid.uuid4()
                    ],
                ),
                REMOTE_ADDR="192.0.2.221",
            )

            self.assertEqual(
                response.status_code,
                404,
            )

        response = self.client.post(
            reverse(
                "retomar_pago",
                args=[
                    uuid.uuid4()
                ],
            ),
            REMOTE_ADDR="192.0.2.221",
        )

        self.assertEqual(
            response.status_code,
            429,
        )

        self.assertIn(
            "Retry-After",
            response.headers,
        )
    

    def test_retomar_pago_regresa_al_checkout(
        self
    ):
        pedido = (
            self.crear_pedido_pendiente_tarjeta(
                "79600002"
            )
        )

        self.asociar_pedido_a_sesion(
            pedido
        )

        response = self.client.post(
            reverse(
                "retomar_pago",
                args=[
                    pedido.tracking_token
                ],
            )
        )

        self.assertEqual(
            response.status_code,
            302,
        )

        self.assertEqual(
            response["Location"],
            reverse("checkout"),
        )

        session = self.client.session

        self.assertEqual(
            session["ultimo_pedido_id"],
            pedido.id,
        )

    def test_otra_sesion_no_puede_retomar_pago(
        self
    ):
        pedido = (
            self.crear_pedido_pendiente_tarjeta(
                "79600003"
            )
        )

        otro_cliente = Client()

        response = otro_cliente.post(
            reverse(
                "retomar_pago",
                args=[
                    pedido.tracking_token
                ],
            )
        )

        self.assertEqual(
            response.status_code,
            403,
        )

    def test_pedido_pagado_no_muestra_retomar_pago(
        self
    ):
        pedido = (
            self.crear_pedido_pendiente_tarjeta(
                "79600004"
            )
        )

        self.asociar_pedido_a_sesion(
            pedido
        )

        pedido.estado = "RECIBIDO"
        pedido.pago_verificado = True
        pedido.save()

        response = self.client.get(
            reverse(
                "order_tracker",
                args=[
                    pedido.tracking_token
                ],
            )
        )

        self.assertNotContains(
            response,
            "Retomar pago",
        )
        

class PaymentPendingCancellationTests(
    FoodBackTestBase
):
    """
    Un cliente puede cancelar su pedido pendiente
    únicamente desde su propia sesión y cuando
    no existe un enlace Wompi potencialmente activo.
    """

    def crear_pedido_pendiente(
        self,
        telefono,
    ):
        pedido = self.crear_pedido(
            estado="PENDIENTE",
            telefono=telefono,
        )

        pedido.metodo_pago = "TARJETA"
        pedido.pago_verificado = False
        pedido.save()

        DetallePedido.objects.create(
            pedido=pedido,
            producto=self.producto,
            cantidad=1,
            precio_unitario=self.producto.precio,
        )

        return pedido

    def asociar_a_sesion(
        self,
        pedido,
        client=None,
    ):
        client = client or self.client

        session = client.session
        session["ultimo_pedido_id"] = pedido.id
        session["historial_pedidos"] = [
            pedido.id
        ]
        session.save()

    def test_error_sin_enlace_puede_cancelarse(
        self
    ):
        pedido = self.crear_pedido_pendiente(
            "79700001"
        )

        self.asociar_a_sesion(
            pedido
        )

        PagoWompi.objects.create(
            tipo="PEDIDO",
            tenant=pedido.sucursal.tenant,
            pedido=pedido,
            referencia=(
                f"ORDEN-{pedido.id}-CANCELTEST"
            ),
            monto=pedido.total_final,
            estado="ERROR",
        )

        response = self.client.post(
            reverse(
                "cancelar_pedido_pendiente",
                args=[
                    pedido.tracking_token
                ],
            )
        )

        self.assertEqual(
            response.status_code,
            302,
        )

        pedido.refresh_from_db()

        self.assertEqual(
            pedido.estado,
            "CANCELADO",
        )

        self.assertFalse(
            pedido.pago_verificado
        )

        session = self.client.session

        self.assertNotEqual(
            session.get(
                "ultimo_pedido_id"
            ),
            pedido.id,
        )

        self.assertIn(
            pedido.id,
            session[
                "historial_pedidos"
            ],
        )

    def test_enlace_wompi_activo_impide_cancelacion_local(
        self
    ):
        pedido = self.crear_pedido_pendiente(
            "79700002"
        )

        self.asociar_a_sesion(
            pedido
        )

        PagoWompi.objects.create(
            tipo="PEDIDO",
            tenant=pedido.sucursal.tenant,
            pedido=pedido,
            referencia=(
                f"ORDEN-{pedido.id}-ACTIVO"
            ),
            monto=pedido.total_final,
            estado="PENDIENTE",
            id_enlace="LINK-ACTIVO",
            url_enlace=(
                "https://wompi.test/"
                "pago-activo"
            ),
        )

        response = self.client.post(
            reverse(
                "cancelar_pedido_pendiente",
                args=[
                    pedido.tracking_token
                ],
            )
        )

        self.assertEqual(
            response.status_code,
            302,
        )

        pedido.refresh_from_db()

        self.assertEqual(
            pedido.estado,
            "PENDIENTE",
        )

    def test_otra_sesion_no_puede_cancelar(
        self
    ):
        pedido = self.crear_pedido_pendiente(
            "79700003"
        )

        self.asociar_a_sesion(
            pedido
        )

        otro_cliente = Client()

        response = otro_cliente.post(
            reverse(
                "cancelar_pedido_pendiente",
                args=[
                    pedido.tracking_token
                ],
            )
        )

        self.assertEqual(
            response.status_code,
            403,
        )

        pedido.refresh_from_db()

        self.assertEqual(
            pedido.estado,
            "PENDIENTE",
        )

    def test_pedido_pagado_no_puede_cancelarse(
        self
    ):
        pedido = self.crear_pedido_pendiente(
            "79700004"
        )

        self.asociar_a_sesion(
            pedido
        )

        pedido.estado = "RECIBIDO"
        pedido.pago_verificado = True
        pedido.save()

        response = self.client.post(
            reverse(
                "cancelar_pedido_pendiente",
                args=[
                    pedido.tracking_token
                ],
            )
        )

        self.assertEqual(
            response.status_code,
            404,
        )

        pedido.refresh_from_db()

        self.assertEqual(
            pedido.estado,
            "RECIBIDO",
        )
        
    @override_settings(
        FOODBACK_PENDING_ORDER_ACTION_SESSION_LIMIT=2,
        FOODBACK_PENDING_ORDER_ACTION_IP_LIMIT=50,
        FOODBACK_PENDING_ORDER_ACTION_WINDOW_SECONDS=600,
        FOODBACK_PENDING_ORDER_ACTION_BLOCK_SECONDS=900,
    )
    def test_cancelar_pendiente_bloquea_flood_de_sesion(
        self,
    ):
        for _ in range(2):
            response = self.client.post(
                reverse(
                    "cancelar_pedido_pendiente",
                    args=[
                        uuid.uuid4()
                    ],
                ),
                REMOTE_ADDR="192.0.2.240",
            )

            self.assertEqual(
                response.status_code,
                404,
            )

        response = self.client.post(
            reverse(
                "cancelar_pedido_pendiente",
                args=[
                    uuid.uuid4()
                ],
            ),
            REMOTE_ADDR="192.0.2.240",
        )

        self.assertEqual(
            response.status_code,
            429,
        )

        self.assertIn(
            "Retry-After",
            response.headers,
        )
        
class PaymentPendingHideTests(
    FoodBackTestBase
):
    """
    Un enlace Wompi potencialmente activo
    puede ocultarse de la UX sin alterar
    su estado financiero.
    """

    def crear_pedido_con_enlace(
        self,
        telefono,
    ):
        pedido = self.crear_pedido(
            estado="PENDIENTE",
            telefono=telefono,
        )

        pedido.metodo_pago = "TARJETA"
        pedido.pago_verificado = False
        pedido.save()

        pago = PagoWompi.objects.create(
            tipo="PEDIDO",
            tenant=pedido.sucursal.tenant,
            pedido=pedido,
            referencia=(
                f"ORDEN-{pedido.id}-HIDE"
            ),
            monto=pedido.total_final,
            estado="PENDIENTE",
            id_enlace="LINK-HIDE",
            url_enlace=(
                "https://wompi.test/"
                "link-activo"
            ),
        )

        session = self.client.session

        session[
            "historial_pedidos"
        ] = [
            pedido.id
        ]

        session[
            "ultimo_pedido_id"
        ] = pedido.id

        session.save()

        return pedido, pago

    def test_ocultar_no_cancela_pedido_ni_pago(
        self
    ):
        pedido, pago = (
            self.crear_pedido_con_enlace(
                "79800001"
            )
        )

        response = self.client.post(
            reverse(
                "ocultar_pedido_pendiente",
                args=[
                    pedido.tracking_token
                ],
            )
        )

        self.assertEqual(
            response.status_code,
            302,
        )

        pedido.refresh_from_db()
        pago.refresh_from_db()

        self.assertEqual(
            pedido.estado,
            "PENDIENTE",
        )

        self.assertFalse(
            pedido.pago_verificado
        )

        self.assertEqual(
            pago.estado,
            "PENDIENTE",
        )

        self.assertTrue(
            pago.url_enlace
        )

    def test_oculto_desaparece_del_menu(
        self
    ):
        pedido, _ = (
            self.crear_pedido_con_enlace(
                "79800002"
            )
        )

        self.client.post(
            reverse(
                "ocultar_pedido_pendiente",
                args=[
                    pedido.tracking_token
                ],
            )
        )

        response = self.client.get(
            reverse("menu")
        )

        self.assertIsNone(
            response.context[
                "ultimo_pedido_activo"
            ]
        )

    def test_oculto_desaparece_de_mis_pedidos_activos(
        self
    ):
        pedido, _ = (
            self.crear_pedido_con_enlace(
                "79800003"
            )
        )

        self.client.post(
            reverse(
                "ocultar_pedido_pendiente",
                args=[
                    pedido.tracking_token
                ],
            )
        )

        response = self.client.get(
            reverse("perfil_usuario")
        )

        activos = list(
            response.context[
                "activos"
            ]
        )

        self.assertNotIn(
            pedido,
            activos,
        )

    def test_otra_sesion_no_puede_ocultarlo(
        self
    ):
        pedido, _ = (
            self.crear_pedido_con_enlace(
                "79800004"
            )
        )

        otro_cliente = Client()

        response = otro_cliente.post(
            reverse(
                "ocultar_pedido_pendiente",
                args=[
                    pedido.tracking_token
                ],
            )
        )

        self.assertEqual(
            response.status_code,
            403,
        )

    def test_si_luego_se_paga_vuelve_a_aparecer(
        self
    ):
        pedido, _ = (
            self.crear_pedido_con_enlace(
                "79800005"
            )
        )

        self.client.post(
            reverse(
                "ocultar_pedido_pendiente",
                args=[
                    pedido.tracking_token
                ],
            )
        )

        pedido.estado = "RECIBIDO"
        pedido.pago_verificado = True
        pedido.save()

        response = self.client.get(
            reverse("menu")
        )

        self.assertIsNotNone(
            response.context[
                "ultimo_pedido_activo"
            ]
        )

        self.assertEqual(
            response.context[
                "ultimo_pedido_activo"
            ].id,
            pedido.id,
        )
        

    @override_settings(
        FOODBACK_PENDING_ORDER_ACTION_SESSION_LIMIT=50,
        FOODBACK_PENDING_ORDER_ACTION_IP_LIMIT=2,
        FOODBACK_PENDING_ORDER_ACTION_WINDOW_SECONDS=600,
        FOODBACK_PENDING_ORDER_ACTION_BLOCK_SECONDS=900,
    )
    def test_cancelar_y_ocultar_comparten_limite_ip(
        self,
    ):
        ip = "192.0.2.241"

        response = self.client.post(
            reverse(
                "cancelar_pedido_pendiente",
                args=[
                    uuid.uuid4()
                ],
            ),
            REMOTE_ADDR=ip,
        )

        self.assertEqual(
            response.status_code,
            404,
        )

        response = self.client.post(
            reverse(
                "ocultar_pedido_pendiente",
                args=[
                    uuid.uuid4()
                ],
            ),
            REMOTE_ADDR=ip,
        )

        self.assertEqual(
            response.status_code,
            404,
        )

        response = self.client.post(
            reverse(
                "cancelar_pedido_pendiente",
                args=[
                    uuid.uuid4()
                ],
            ),
            REMOTE_ADDR=ip,
        )

        self.assertEqual(
            response.status_code,
            429,
        )
        
class MultipleActiveOrdersVisibilityTests(
    FoodBackTestBase
):

    def test_pagar_pedido_oculto_no_hace_desaparecer_otro_activo(
        self
    ):
        # Pedido anterior que ya estaba confirmado.
        pedido_anterior = self.crear_pedido(
            estado="RECIBIDO",
            telefono="79910001",
        )

        pedido_anterior.metodo_pago = "TARJETA"
        pedido_anterior.pago_verificado = True
        pedido_anterior.save()

        # Segundo pedido: estuvo oculto mientras era pendiente,
        # pero posteriormente terminó pagándose.
        pedido_nuevo = self.crear_pedido(
            estado="RECIBIDO",
            telefono="79910002",
        )

        pedido_nuevo.metodo_pago = "TARJETA"
        pedido_nuevo.pago_verificado = True
        pedido_nuevo.save()

        session = self.client.session

        session[
            "historial_pedidos"
        ] = [
            pedido_anterior.id,
            pedido_nuevo.id,
        ]

        # Simula que el segundo pedido había sido ocultado
        # cuando todavía estaba pendiente.
        session[
            "pedidos_pendientes_ocultos"
        ] = [
            pedido_nuevo.id,
        ]

        session[
            "ultimo_pedido_id"
        ] = pedido_nuevo.id

        session.save()

        response = self.client.get(
            reverse("perfil_usuario")
        )

        self.assertEqual(
            response.status_code,
            200,
        )

        activos = list(
            response.context["activos"]
        )

        self.assertIn(
            pedido_anterior,
            activos,
        )

        self.assertIn(
            pedido_nuevo,
            activos,
        )

        self.assertEqual(
            len(activos),
            2,
        )
        
    def test_ocultar_segundo_y_luego_pagarlo_conserva_ambos_activos(
    self
    ):
        pedido_38 = self.crear_pedido(
            estado="RECIBIDO",
            telefono="79920001",
        )

        pedido_38.metodo_pago = "TARJETA"
        pedido_38.pago_verificado = True
        pedido_38.save()

        pedido_39 = self.crear_pedido(
            estado="PENDIENTE",
            telefono="79920002",
        )

        pedido_39.metodo_pago = "TARJETA"
        pedido_39.pago_verificado = False
        pedido_39.save()

        PagoWompi.objects.create(
            tipo="PEDIDO",
            tenant=pedido_39.sucursal.tenant,
            pedido=pedido_39,
            referencia=(
                f"ORDEN-{pedido_39.id}-MULTI"
            ),
            monto=pedido_39.total_final,
            estado="PENDIENTE",
            id_enlace="MULTI-LINK",
            url_enlace=(
                "https://wompi.test/multi"
            ),
        )

        session = self.client.session

        session[
            "historial_pedidos"
        ] = [
            pedido_38.id,
            pedido_39.id,
        ]

        session[
            "ultimo_pedido_id"
        ] = pedido_39.id

        session.save()

        # El usuario oculta #39.
        response = self.client.post(
            reverse(
                "ocultar_pedido_pendiente",
                args=[
                    pedido_39.tracking_token
                ],
            )
        )

        self.assertEqual(
            response.status_code,
            302,
        )

        # #38 debe seguir visible.
        response = self.client.get(
            reverse("perfil_usuario")
        )

        activos = list(
            response.context[
                "activos"
            ]
        )

        self.assertIn(
            pedido_38,
            activos,
        )

        self.assertNotIn(
            pedido_39,
            activos,
        )

        # Simulamos confirmación posterior de Wompi.
        pedido_39.estado = "RECIBIDO"
        pedido_39.pago_verificado = True

        pedido_39.save(
            update_fields=[
                "estado",
                "pago_verificado",
            ]
        )

        response = self.client.get(
            reverse("perfil_usuario")
        )

        activos = list(
            response.context[
                "activos"
            ]
        )

        self.assertIn(
            pedido_38,
            activos,
        )

        self.assertIn(
            pedido_39,
            activos,
        )

        self.assertEqual(
            len(activos),
            2,
        )
        
class TenantContextResolverTests(TestCase):

    def setUp(self):
        self.factory = RequestFactory()

        User = get_user_model()

        self.user = User.objects.create_user(
            username="owner_tenant_test",
            password="test12345",
        )

        self.tenant = Tenant.objects.create(
            nombre="Restaurante Test",
            slug="restaurante-test",
            habilitado=True,
        )

        Membership.objects.create(
            tenant=self.tenant,
            usuario=self.user,
            rol=Membership.ROLE_OWNER,
            activo=True,
        )

    def test_usuario_autenticado_resuelve_tenant_por_membership(self):
        request = self.factory.get(
            "/dashboard/"
        )

        request.user = self.user
        request.session = {}

        tenant = resolver_tenant(
            request
        )

        self.assertEqual(
            tenant,
            self.tenant,
        )
        
    def test_usuario_no_puede_forzar_otro_tenant_desde_sesion(self):
        tenant_ajeno = Tenant.objects.create(
            nombre="Restaurante Ajeno",
            slug="restaurante-ajeno",
            habilitado=True,
        )

        request = self.factory.get(
            "/dashboard/"
        )

        request.user = self.user
        request.session = SessionStore()

        # Simulamos que alguien manipuló su sesión
        # para intentar seleccionar otro Tenant.
        request.session[
            "tenant_activo_public_id"
        ] = str(tenant_ajeno.public_id)

        tenant = resolver_tenant(
            request
        )

        # Debe ignorar el Tenant ajeno y volver
        # al único Tenant autorizado del usuario.
        self.assertEqual(
            tenant,
            self.tenant,
        )

        # Además debe limpiar la selección inválida.
        self.assertNotIn(
            "tenant_activo_public_id",
            request.session,
        )
        
    def test_usuario_anonimo_resuelve_tenant_por_subdominio(self):
        request = self.factory.get(
            "/",
            HTTP_HOST="restaurante-test.foodbacksv.com",
        )

        request.user = AnonymousUser()
        request.session = {}

        tenant = resolver_tenant(
            request
        )

        self.assertEqual(
            tenant,
            self.tenant,
        )
        
    def test_usuario_no_puede_forzar_sucursal_de_otro_tenant(self):
        sucursal_valida = Sucursal.objects.create(
            tenant=self.tenant,
            nombre="Sucursal Principal",
            slug="principal",
            estado=Sucursal.Estado.ACTIVA,
        )

        tenant_ajeno = Tenant.objects.create(
            nombre="Tenant Ajeno",
            slug="tenant-ajeno",
            habilitado=True,
        )

        sucursal_ajena = Sucursal.objects.create(
            tenant=tenant_ajeno,
            nombre="Sucursal Ajena",
            slug="ajena",
            estado=Sucursal.Estado.ACTIVA,
        )

        request = self.factory.get(
            "/dashboard/"
        )

        request.user = self.user
        request.session = SessionStore()

        request.session[
            "sucursal_activa_public_id"
        ] = str(
            sucursal_ajena.public_id
        )

        sucursal = resolver_sucursal(
            request,
            self.tenant,
        )

        self.assertEqual(
            sucursal,
            sucursal_valida,
        )

        self.assertNotIn(
            "sucursal_activa_public_id",
            request.session,
        )
        
    def test_sucursal_archivada_no_puede_quedar_activa_en_sesion(self):
        sucursal_activa = Sucursal.objects.create(
            tenant=self.tenant,
            nombre="Sucursal Activa",
            slug="activa",
            estado=Sucursal.Estado.ACTIVA,
        )

        sucursal_archivada = Sucursal.objects.create(
            tenant=self.tenant,
            nombre="Sucursal Archivada",
            slug="archivada",
            estado=Sucursal.Estado.ARCHIVADA,
        )

        request = self.factory.get(
            "/dashboard/"
        )

        request.user = self.user
        request.session = SessionStore()

        # Simulamos una selección antigua o manipulada.
        request.session[
            "sucursal_activa_public_id"
        ] = str(
            sucursal_archivada.public_id
        )

        sucursal = resolver_sucursal(
            request,
            self.tenant,
        )

        # FoodBack debe ignorar la archivada.
        self.assertEqual(
            sucursal,
            sucursal_activa,
        )

        # Y limpiar la selección inválida de sesión.
        self.assertNotIn(
            "sucursal_activa_public_id",
            request.session,
        )
        
    def test_usuario_puede_seleccionar_sucursal_activa_de_su_tenant(self):
        sucursal_principal = Sucursal.objects.create(
            tenant=self.tenant,
            nombre="Sucursal Principal",
            slug="principal",
            estado=Sucursal.Estado.ACTIVA,
        )

        sucursal_secundaria = Sucursal.objects.create(
            tenant=self.tenant,
            nombre="Sucursal Secundaria",
            slug="secundaria",
            estado=Sucursal.Estado.ACTIVA,
        )

        request = self.factory.get(
            "/dashboard/"
        )

        request.user = self.user
        request.session = SessionStore()

        request.session[
            "sucursal_activa_public_id"
        ] = str(
            sucursal_secundaria.public_id
        )

        sucursal = resolver_sucursal(
            request,
            self.tenant,
        )

        self.assertEqual(
            sucursal,
            sucursal_secundaria,
        )

        self.assertNotEqual(
            sucursal,
            sucursal_principal,
        )

        self.assertEqual(
            request.session[
                "sucursal_activa_public_id"
            ],
            str(
                sucursal_secundaria.public_id
            ),
        )
        
    @override_settings(
        FOODBACK_DEFAULT_TENANT_SLUG="restaurante-test",
    )
    def test_desarrollo_resuelve_tenant_por_slug_configurado(self):
        request = self.factory.get(
            "/",
            HTTP_HOST="127.0.0.1:8000",
        )

        request.user = AnonymousUser()
        request.session = SessionStore()

        tenant = resolver_tenant(
            request
        )

        self.assertEqual(
            tenant,
            self.tenant,
        )
        
    def test_middleware_agrega_tenant_y_sucursal_al_request(self):
        sucursal = Sucursal.objects.create(
            tenant=self.tenant,
            nombre="Sucursal Principal",
            slug="principal",
            estado=Sucursal.Estado.ACTIVA,
        )

        request = self.factory.get(
            "/dashboard/"
        )

        request.user = self.user
        request.session = SessionStore()

        contexto_capturado = {}

        def vista_falsa(req):
            contexto_capturado["tenant"] = (
                req.tenant
            )

            contexto_capturado["sucursal"] = (
                req.sucursal
            )

            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT
                        current_setting(
                            'foodback.tenant_id',
                            true
                        ),
                        current_setting(
                            'foodback.sucursal_id',
                            true
                        )
                    """
                )

                (
                    contexto_capturado["db_tenant"],
                    contexto_capturado["db_sucursal"],
                ) = cursor.fetchone()

            return HttpResponse("OK")

        middleware = TenantContextMiddleware(
            vista_falsa
        )

        response = middleware(
            request
        )

        self.assertEqual(
            response.status_code,
            200,
        )

        self.assertEqual(
            contexto_capturado["tenant"],
            self.tenant,
        )

        self.assertEqual(
            contexto_capturado["sucursal"],
            sucursal,
        )
        
        self.assertEqual(
            contexto_capturado["db_tenant"],
            str(self.tenant.pk),
        )

        self.assertEqual(
            contexto_capturado["db_sucursal"],
            str(sucursal.pk),
        )
        
    @patch(
    "pedidos.middleware.resolver_sucursal"
)
    def test_middleware_establece_tenant_db_antes_de_resolver_sucursal(
        self,
        mock_resolver_sucursal,
    ):
        sucursal = Sucursal.objects.create(
            tenant=self.tenant,
            nombre="Sucursal Contexto DB",
            slug="contexto-db",
            estado=Sucursal.Estado.ACTIVA,
        )

        request = self.factory.get(
            "/dashboard/"
        )

        request.user = self.user
        request.session = SessionStore()

        contexto_capturado = {}

        def resolver_sucursal_fake(
            request_recibido,
            tenant_recibido,
        ):
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT current_setting(
                        'foodback.tenant_id',
                        true
                    )
                    """
                )

                contexto_capturado[
                    "tenant_durante_resolucion"
                ] = cursor.fetchone()[0]

            contexto_capturado[
                "tenant_argumento"
            ] = tenant_recibido

            return sucursal

        mock_resolver_sucursal.side_effect = (
            resolver_sucursal_fake
        )

        def vista_falsa(req):
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT
                        current_setting(
                            'foodback.tenant_id',
                            true
                        ),
                        current_setting(
                            'foodback.sucursal_id',
                            true
                        )
                    """
                )

                (
                    tenant_db,
                    sucursal_db,
                ) = cursor.fetchone()

            contexto_capturado[
                "tenant_vista"
            ] = tenant_db

            contexto_capturado[
                "sucursal_vista"
            ] = sucursal_db

            return HttpResponse(
                "OK"
            )

        middleware = TenantContextMiddleware(
            vista_falsa
        )

        response = middleware(
            request
        )

        self.assertEqual(
            response.status_code,
            200,
        )

        # CRÍTICO:
        # resolver_sucursal ya se ejecutó dentro
        # del contexto Tenant de PostgreSQL.
        self.assertEqual(
            contexto_capturado[
                "tenant_durante_resolucion"
            ],
            str(
                self.tenant.id
            ),
        )

        self.assertEqual(
            contexto_capturado[
                "tenant_argumento"
            ],
            self.tenant,
        )

        # Y la vista obtiene ambos contextos.
        self.assertEqual(
            contexto_capturado[
                "tenant_vista"
            ],
            str(
                self.tenant.id
            ),
        )

        self.assertEqual(
            contexto_capturado[
                "sucursal_vista"
            ],
            str(
                sucursal.id
            ),
        )
        
    @override_settings(
    FOODBACK_DEFAULT_TENANT_SLUG=""
)
    def test_middleware_deja_contexto_none_si_no_hay_tenant_valido(self):
        request = self.factory.get(
            "/",
            HTTP_HOST="127.0.0.1:8000",
        )

        request.user = AnonymousUser()
        request.session = SessionStore()

        contexto_capturado = {}

        def vista_falsa(req):
            contexto_capturado["tenant"] = (
                req.tenant
            )

            contexto_capturado["sucursal"] = (
                req.sucursal
            )

            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT
                        current_setting(
                            'foodback.tenant_id',
                            true
                        ),
                        current_setting(
                            'foodback.sucursal_id',
                            true
                        )
                    """
                )

                (
                    contexto_capturado["db_tenant"],
                    contexto_capturado["db_sucursal"],
                ) = cursor.fetchone()

            return HttpResponse("OK")

        middleware = TenantContextMiddleware(
            vista_falsa
        )

        response = middleware(
            request
        )

        self.assertEqual(
            response.status_code,
            200,
        )

        self.assertIsNone(
            contexto_capturado["tenant"]
        )

        self.assertIsNone(
            contexto_capturado["sucursal"]
        )
        
        self.assertEqual(
            contexto_capturado["db_tenant"],
            "",
        )

        self.assertEqual(
            contexto_capturado["db_sucursal"],
            "",
        )
        
    @override_settings(
        IS_PRODUCTION=True,
        FOODBACK_DEFAULT_TENANT_SLUG="rancheritos",
    )
    def test_produccion_no_usa_tenant_default_de_desarrollo(
        self,
    ):
        Tenant.objects.create(
            nombre="Rancheritos",
            slug="rancheritos",
            habilitado=True,
        )

        tenant = (
            _resolver_tenant_desarrollo()
        )

        self.assertIsNone(
            tenant
        )
        
    @override_settings(
        IS_PRODUCTION=False,
        FOODBACK_DEFAULT_TENANT_SLUG="tenant-dev",
    )
    def test_desarrollo_si_permite_tenant_default_explicito(
        self,
    ):
        tenant_esperado = (
            Tenant.objects.create(
                nombre="Tenant Dev",
                slug="tenant-dev",
                habilitado=True,
            )
        )

        tenant = (
            _resolver_tenant_desarrollo()
        )

        self.assertEqual(
            tenant,
            tenant_esperado,
        )
        
        
        
class PostgreSQLRowLevelSecurityTests(
    TransactionTestCase
):
    """
    Verifica RLS real de PostgreSQL.

    IMPORTANTE:
    El usuario foodback_test es propietario de las tablas
    dentro de la BD temporal creada por Django.

    PostgreSQL permite normalmente al owner omitir RLS.

    Por eso estos tests activan FORCE RLS únicamente
    dentro de la BD descartable de pruebas.
    """

    RLS_TABLES = (
    "pedidos_categoria",
    "pedidos_extra",
    "pedidos_cliente",
    "pedidos_producto",
    "pedidos_opcionproducto",
    "pedidos_producto_extras",
    "pedidos_configuracionnegocio",
    "pedidos_diaespecial",
    "pedidos_pedido",
    "pedidos_detallepedido",
    "pedidos_detallepedido_extras",
    "pedidos_suscripciontenant",
    "pedidos_estadopasarelapago",
    "pedidos_pagowompi",
    "pedidos_eventopagowompi",
    "pedidos_sucursal",
    "pedidos_membershipsucursal",
    "pedidos_repartidorsucursal",
    )

    def setUp(self):
        super().setUp()

        if connection.vendor != "postgresql":
            self.skipTest(
                "RLS requiere PostgreSQL."
            )

        with connection.cursor() as cursor:
            for table in self.RLS_TABLES:
                cursor.execute(
                    f"""
                    ALTER TABLE {table}
                    FORCE ROW LEVEL SECURITY
                    """
                )
                

        self.tenant_a = Tenant.objects.create(
            nombre="RLS Tenant A",
            slug="rls-tenant-a",
            habilitado=True,
        )

        self.tenant_b = Tenant.objects.create(
            nombre="RLS Tenant B",
            slug="rls-tenant-b",
            habilitado=True,
        )
        
        self.usuario_a = User.objects.create(
            username="rls-user-a",
        )

        self.usuario_b = User.objects.create(
            username="rls-user-b",
        )

        self.delivery_a = User.objects.create(
            username="rls-delivery-a",
        )

        self.delivery_b = User.objects.create(
            username="rls-delivery-b",
        )

        self.membership_a = Membership.objects.create(
            tenant=self.tenant_a,
            usuario=self.usuario_a,
            rol=Membership.ROLE_MANAGER,
            activo=True,
        )

        self.membership_b = Membership.objects.create(
            tenant=self.tenant_b,
            usuario=self.usuario_b,
            rol=Membership.ROLE_MANAGER,
            activo=True,
        )
        

        with tenant_database_context(
            tenant=self.tenant_a
        ):
            
            self.sucursal_a = Sucursal.objects.create(
                tenant=self.tenant_a,
                nombre="RLS Sucursal A",
                slug="rls-sucursal-a",
                estado=Sucursal.Estado.ACTIVA,
            )
            
            self.membership_sucursal_a = (
                MembershipSucursal.objects.create(
                    membership=self.membership_a,
                    sucursal=self.sucursal_a,
                    activo=True,
                )
            )

            self.repartidor_sucursal_a = (
                RepartidorSucursal.objects.create(
                    usuario=self.delivery_a,
                    sucursal=self.sucursal_a,
                    activo=True,
                )
            )
            
            self.categoria_a = (
                Categoria.objects.create(
                    tenant=self.tenant_a,
                    nombre="Categoria A",
                    orden=1,
                )
            )

            self.extra_a = (
                Extra.objects.create(
                    tenant=self.tenant_a,
                    nombre="Extra A",
                    precio=Decimal("1.00"),
                    disponible=True,
                )
            )

            self.cliente_a = (
                Cliente.objects.create(
                    tenant=self.tenant_a,
                    telefono="78000001",
                    nombre="Cliente",
                    apellido="A",
                )
            )
            
            self.producto_a = (
                Producto.objects.create(
                    categoria=self.categoria_a,
                    nombre="Producto A",
                    precio=Decimal("10.00"),
                    disponible=True,
                )
            )

            self.opcion_a = (
                OpcionProducto.objects.create(
                    producto=self.producto_a,
                    nombre="Opcion A",
                    precio_extra=Decimal("1.00"),
                    disponible=True,
                )
            )
            
            self.producto_a.extras.add(
                self.extra_a
            )
            
            self.config_a = (
                ConfiguracionNegocio.objects.create(
                    sucursal=self.sucursal_a,
                )
            )

            self.dia_a = (
                DiaEspecial.objects.create(
                    sucursal=self.sucursal_a,
                    fecha=date.today(),
                    abierto=False,
                    motivo="Tenant A",
                )
            )
            
            self.pedido_a = (
                Pedido.objects.create(
                    sucursal=self.sucursal_a,
                    cliente=self.cliente_a,
                    direccion_entrega="Direccion Tenant A",
                )
            )
            
            self.detalle_a = (
                DetallePedido.objects.create(
                    pedido=self.pedido_a,
                    producto=self.producto_a,
                    opcion=self.opcion_a,
                    cantidad=1,
                    precio_unitario=Decimal("11.00"),
                    subtotal=Decimal("11.00"),
                )
            )

            self.detalle_a.extras.add(
                self.extra_a
            )
            
            self.suscripcion_a = (
                SuscripcionTenant.objects.create(
                    tenant=self.tenant_a,
                    estado=SuscripcionTenant.Estado.ACTIVA,
                    fecha_vencimiento=(
                        date.today()
                        + timedelta(days=30)
                    ),
                )
            )

            self.estado_pasarela_a = (
                EstadoPasarelaPago.objects.create(
                    tenant=self.tenant_a,
                    configuracion_negocio=self.config_a,
                )
            )
            
            self.pago_a = PagoWompi.objects.create(
                tipo="PEDIDO",
                tenant=self.tenant_a,
                pedido=self.pedido_a,
                configuracion_negocio=self.config_a,
                referencia="RLS-WOMPI-A",
                cliente_token_hash="a" * 64,
                monto=Decimal("11.00"),
                estado="PENDIENTE",
            )

            self.evento_a = EventoPagoWompi.objects.create(
                pago=self.pago_a,
                pedido=self.pedido_a,
                tenant=self.tenant_a,
                configuracion_negocio=self.config_a,
                cliente_token_hash="a" * 64,
                categoria="INFO",
                origen="SISTEMA",
                codigo="RLS_A",
                mensaje="Evento Tenant A",
                clave_evento="RLS-EVENT-A",
            )

        with tenant_database_context(
            tenant=self.tenant_b
        ):
            
            self.sucursal_b = Sucursal.objects.create(
                tenant=self.tenant_b,
                nombre="RLS Sucursal B",
                slug="rls-sucursal-b",
                estado=Sucursal.Estado.ACTIVA,
            )
            
            
            self.membership_sucursal_b = (
                MembershipSucursal.objects.create(
                    membership=self.membership_b,
                    sucursal=self.sucursal_b,
                    activo=True,
                )
            )

            self.repartidor_sucursal_b = (
                RepartidorSucursal.objects.create(
                    usuario=self.delivery_b,
                    sucursal=self.sucursal_b,
                    activo=True,
                )
            )
            
            
            self.sucursal_b_extra = (
                Sucursal.objects.create(
                    tenant=self.tenant_b,
                    nombre="RLS Sucursal B Extra",
                    slug="rls-sucursal-b-extra",
                    estado=Sucursal.Estado.ACTIVA,
                )
            )
            
            
            self.categoria_b = (
                Categoria.objects.create(
                    tenant=self.tenant_b,
                    nombre="Categoria B",
                    orden=1,
                )
            )

            self.extra_b = (
                Extra.objects.create(
                    tenant=self.tenant_b,
                    nombre="Extra B",
                    precio=Decimal("2.00"),
                    disponible=True,
                )
            )

            self.cliente_b = (
                Cliente.objects.create(
                    tenant=self.tenant_b,
                    telefono="78000002",
                    nombre="Cliente",
                    apellido="B",
                )
            )
            self.producto_b = (
                Producto.objects.create(
                    categoria=self.categoria_b,
                    nombre="Producto B",
                    precio=Decimal("20.00"),
                    disponible=True,
                )
            )

            self.opcion_b = (
                OpcionProducto.objects.create(
                    producto=self.producto_b,
                    nombre="Opcion B",
                    precio_extra=Decimal("2.00"),
                    disponible=True,
                )
            )
            
            self.producto_b.extras.add(
                self.extra_b
            )
            
            self.config_b = (
                ConfiguracionNegocio.objects.create(
                    sucursal=self.sucursal_b,
                )
            )

            self.dia_b = (
                DiaEspecial.objects.create(
                    sucursal=self.sucursal_b,
                    fecha=date.today(),
                    abierto=False,
                    motivo="Tenant B",
                )
            )
            
            self.pedido_b = (
                Pedido.objects.create(
                    sucursal=self.sucursal_b,
                    cliente=self.cliente_b,
                    direccion_entrega="Direccion Tenant B",
                )
            )
            
            self.detalle_b = (
                DetallePedido.objects.create(
                    pedido=self.pedido_b,
                    producto=self.producto_b,
                    opcion=self.opcion_b,
                    cantidad=1,
                    precio_unitario=Decimal("22.00"),
                    subtotal=Decimal("22.00"),
                )
            )

            self.detalle_b.extras.add(
                self.extra_b
            )
            
            self.suscripcion_b = (
                SuscripcionTenant.objects.create(
                    tenant=self.tenant_b,
                    estado=SuscripcionTenant.Estado.ACTIVA,
                    fecha_vencimiento=(
                        date.today()
                        + timedelta(days=30)
                    ),
                )
            )

            self.estado_pasarela_b = (
                EstadoPasarelaPago.objects.create(
                    tenant=self.tenant_b,
                    configuracion_negocio=self.config_b,
                )
            )
            
            self.config_b_extra = (
                ConfiguracionNegocio.objects.create(
                    sucursal=self.sucursal_b_extra,
                )
            )
            
            self.pago_b = PagoWompi.objects.create(
                tipo="PEDIDO",
                tenant=self.tenant_b,
                pedido=self.pedido_b,
                configuracion_negocio=self.config_b,
                referencia="RLS-WOMPI-B",
                cliente_token_hash="b" * 64,
                monto=Decimal("22.00"),
                estado="PENDIENTE",
            )

            self.evento_b = EventoPagoWompi.objects.create(
                pago=self.pago_b,
                pedido=self.pedido_b,
                tenant=self.tenant_b,
                configuracion_negocio=self.config_b,
                cliente_token_hash="b" * 64,
                categoria="INFO",
                origen="SISTEMA",
                codigo="RLS_B",
                mensaje="Evento Tenant B",
                clave_evento="RLS-EVENT-B",
            )

    def tearDown(self):
        # Quitamos FORCE antes de que Django haga
        # la limpieza normal de su BD temporal.
        with connection.cursor() as cursor:
            for table in self.RLS_TABLES:
                cursor.execute(
                    f"""
                    ALTER TABLE {table}
                    NO FORCE ROW LEVEL SECURITY
                    """
                )

        super().tearDown()

    def test_rls_select_solo_ve_tenant_activo(
        self,
    ):
        with tenant_database_context(
            tenant=self.tenant_a
        ):
            categorias = set(
                Categoria.objects.values_list(
                    "id",
                    flat=True,
                )
            )

            extras = set(
                Extra.objects.values_list(
                    "id",
                    flat=True,
                )
            )

            clientes = set(
                Cliente.objects.values_list(
                    "id",
                    flat=True,
                )
            )

        self.assertEqual(
            categorias,
            {self.categoria_a.id},
        )

        self.assertEqual(
            extras,
            {self.extra_a.id},
        )

        self.assertEqual(
            clientes,
            {self.cliente_a.id},
        )

    def test_rls_sin_tenant_no_ve_filas(
        self,
    ):
        with tenant_database_context():
            self.assertEqual(
                Categoria.objects.count(),
                0,
            )

            self.assertEqual(
                Extra.objects.count(),
                0,
            )

            self.assertEqual(
                Cliente.objects.count(),
                0,
            )

    def test_rls_impide_insertar_otro_tenant(
        self,
    ):
        with self.assertRaises(
            DatabaseError
        ):
            with tenant_database_context(
                tenant=self.tenant_a
            ):
                Categoria.objects.create(
                    tenant=self.tenant_b,
                    nombre="Categoria infiltrada",
                    orden=99,
                )

        with self.assertRaises(
            DatabaseError
        ):
            with tenant_database_context(
                tenant=self.tenant_a
            ):
                Extra.objects.create(
                    tenant=self.tenant_b,
                    nombre="Extra infiltrado",
                    precio=Decimal("99.00"),
                    disponible=True,
                )

        with self.assertRaises(
            DatabaseError
        ):
            with tenant_database_context(
                tenant=self.tenant_a
            ):
                Cliente.objects.create(
                    tenant=self.tenant_b,
                    telefono="78009999",
                    nombre="Intruso",
                    apellido="RLS",
                )

    def test_rls_impide_mover_fila_a_otro_tenant(
        self,
    ):
        with self.assertRaises(
            DatabaseError
        ):
            with tenant_database_context(
                tenant=self.tenant_a
            ):
                (
                    Categoria.objects
                    .filter(
                        pk=self.categoria_a.pk
                    )
                    .update(
                        tenant=self.tenant_b
                    )
                )

    def test_rls_no_elimina_fila_de_otro_tenant(
        self,
    ):
        with tenant_database_context(
            tenant=self.tenant_a
        ):
            eliminados, _ = (
                Categoria.objects
                .filter(
                    pk=self.categoria_b.pk
                )
                .delete()
            )

        self.assertEqual(
            eliminados,
            0,
        )

        with tenant_database_context(
            tenant=self.tenant_b
        ):
            self.assertTrue(
                Categoria.objects.filter(
                    pk=self.categoria_b.pk
                ).exists()
            )   
            
    def test_rls_producto_y_opcion_solo_ven_tenant_activo(
        self,
    ):
        with tenant_database_context(
            tenant=self.tenant_a
        ):
            productos = set(
                Producto.objects.values_list(
                    "id",
                    flat=True,
                )
            )

            opciones = set(
                OpcionProducto.objects.values_list(
                    "id",
                    flat=True,
                )
            )

        self.assertEqual(
            productos,
            {self.producto_a.id},
        )

        self.assertEqual(
            opciones,
            {self.opcion_a.id},
        )


    def test_rls_impide_producto_con_categoria_ajena(
        self,
    ):
        with self.assertRaises(
            DatabaseError
        ):
            with tenant_database_context(
                tenant=self.tenant_a
            ):
                Producto.objects.create(
                    categoria=self.categoria_b,
                    nombre="Producto infiltrado",
                    precio=Decimal("99.00"),
                    disponible=True,
                )


    def test_rls_impide_opcion_con_producto_ajeno(
        self,
    ):
        with self.assertRaises(
            DatabaseError
        ):
            with tenant_database_context(
                tenant=self.tenant_a
            ):
                OpcionProducto.objects.create(
                    producto=self.producto_b,
                    nombre="Opcion infiltrada",
                    precio_extra=Decimal("99.00"),
                    disponible=True,
                )


    def test_rls_impide_mover_producto_y_opcion_a_otro_tenant(
        self,
    ):
        with self.assertRaises(
            DatabaseError
        ):
            with tenant_database_context(
                tenant=self.tenant_a
            ):
                (
                    Producto.objects
                    .filter(
                        pk=self.producto_a.pk
                    )
                    .update(
                        categoria=self.categoria_b
                    )
                )

        with self.assertRaises(
            DatabaseError
        ):
            with tenant_database_context(
                tenant=self.tenant_a
            ):
                (
                    OpcionProducto.objects
                    .filter(
                        pk=self.opcion_a.pk
                    )
                    .update(
                        producto=self.producto_b
                    )
                )    
                
    def test_rls_producto_extras_solo_ve_tenant_activo(
        self,
    ):
        through = Producto.extras.through

        with tenant_database_context(
            tenant=self.tenant_a
        ):
            relaciones = set(
                through.objects.values_list(
                    "producto_id",
                    "extra_id",
                )
            )

        self.assertEqual(
            relaciones,
            {
                (
                    self.producto_a.id,
                    self.extra_a.id,
                )
            },
        )


    def test_rls_producto_no_puede_recibir_extra_de_otro_tenant(
        self,
    ):
        with self.assertRaises(
            DatabaseError
        ):
            with tenant_database_context(
                tenant=self.tenant_a
            ):
                self.producto_a.extras.add(
                    self.extra_b
                )


    def test_rls_producto_extras_sin_tenant_no_ve_relaciones(
        self,
    ):
        through = Producto.extras.through

        with tenant_database_context():
            self.assertEqual(
                through.objects.count(),
                0,
            ) 
            
    
    def test_rls_configuracion_y_dia_solo_ven_tenant_activo(
        self,
    ):
        with tenant_database_context(
            tenant=self.tenant_a
        ):
            configuraciones = set(
                ConfiguracionNegocio.objects.values_list(
                    "id",
                    flat=True,
                )
            )

            dias = set(
                DiaEspecial.objects.values_list(
                    "id",
                    flat=True,
                )
            )

        self.assertEqual(
            configuraciones,
            {self.config_a.id},
        )

        self.assertEqual(
            dias,
            {self.dia_a.id},
        )


    def test_rls_impide_configuracion_y_dia_en_sucursal_ajena(
        self,
    ):
        with self.assertRaises(
            DatabaseError
        ):
            with tenant_database_context(
                tenant=self.tenant_a
            ):
                DiaEspecial.objects.create(
                    sucursal=self.sucursal_b,
                    fecha=(
                        date.today()
                        + timedelta(days=1)
                    ),
                    abierto=False,
                    motivo="Infiltrado",
                )

        with self.assertRaises(
            DatabaseError
        ):
            with tenant_database_context(
                tenant=self.tenant_a
            ):
                (
                    ConfiguracionNegocio.objects
                    .filter(
                        pk=self.config_a.pk
                    )
                    .update(
                        sucursal=self.sucursal_b
                    )
                )


    def test_rls_configuracion_y_dia_sin_tenant_no_ve_filas(
        self,
    ):
        with tenant_database_context():
            self.assertEqual(
                ConfiguracionNegocio.objects.count(),
                0,
            )

            self.assertEqual(
                DiaEspecial.objects.count(),
                0,
            )
            
            
    def test_rls_pedido_solo_ve_tenant_activo(
        self,
    ):
        with tenant_database_context(
            tenant=self.tenant_a
        ):
            pedidos = set(
                Pedido.objects.values_list(
                    "id",
                    flat=True,
                )
            )

        self.assertEqual(
            pedidos,
            {self.pedido_a.id},
        )


    def test_rls_pedido_sin_tenant_no_ve_filas(
        self,
    ):
        with tenant_database_context():
            self.assertEqual(
                Pedido.objects.count(),
                0,
            )


    def test_rls_impide_pedido_hibrido_entre_tenants(
        self,
    ):
        with self.assertRaises(
            DatabaseError
        ):
            with tenant_database_context(
                tenant=self.tenant_a
            ):
                Pedido.objects.create(
                    sucursal=self.sucursal_a,
                    cliente=self.cliente_b,
                    direccion_entrega=(
                        "Cliente B infiltrado"
                    ),
                )

        with self.assertRaises(
            DatabaseError
        ):
            with tenant_database_context(
                tenant=self.tenant_a
            ):
                Pedido.objects.create(
                    sucursal=self.sucursal_b,
                    cliente=self.cliente_a,
                    direccion_entrega=(
                        "Sucursal B infiltrada"
                    ),
                )


    def test_rls_impide_mover_pedido_a_otro_tenant(
        self,
    ):
        with self.assertRaises(
            DatabaseError
        ):
            with tenant_database_context(
                tenant=self.tenant_a
            ):
                (
                    Pedido.objects
                    .filter(
                        pk=self.pedido_a.pk
                    )
                    .update(
                        sucursal=self.sucursal_b
                    )
                )

        with self.assertRaises(
            DatabaseError
        ):
            with tenant_database_context(
                tenant=self.tenant_a
            ):
                (
                    Pedido.objects
                    .filter(
                        pk=self.pedido_a.pk
                    )
                    .update(
                        cliente=self.cliente_b
                    )
            )
                
    def test_rls_detalle_y_extras_solo_ven_tenant_activo(
        self,
    ):
        through = DetallePedido.extras.through

        with tenant_database_context(
            tenant=self.tenant_a
        ):
            detalles = set(
                DetallePedido.objects.values_list(
                    "id",
                    flat=True,
                )
            )

            extras_detalle = set(
                through.objects.values_list(
                    "detallepedido_id",
                    "extra_id",
                )
            )

        self.assertEqual(
            detalles,
            {self.detalle_a.id},
        )

        self.assertEqual(
            extras_detalle,
            {
                (
                    self.detalle_a.id,
                    self.extra_a.id,
                )
            },
        )


    def test_rls_detalle_y_extras_sin_tenant_no_ven_filas(
        self,
    ):
        through = DetallePedido.extras.through

        with tenant_database_context():
            self.assertEqual(
                DetallePedido.objects.count(),
                0,
            )

            self.assertEqual(
                through.objects.count(),
                0,
            )


    def test_rls_impide_detalle_con_pedido_o_producto_ajeno(
        self,
    ):
        with self.assertRaises(
            DatabaseError
        ):
            with tenant_database_context(
                tenant=self.tenant_a
            ):
                DetallePedido.objects.create(
                    pedido=self.pedido_a,
                    producto=self.producto_b,
                    cantidad=1,
                    precio_unitario=Decimal("99.00"),
                    subtotal=Decimal("99.00"),
                )

        with self.assertRaises(
            DatabaseError
        ):
            with tenant_database_context(
                tenant=self.tenant_a
            ):
                DetallePedido.objects.create(
                    pedido=self.pedido_b,
                    producto=self.producto_a,
                    cantidad=1,
                    precio_unitario=Decimal("99.00"),
                    subtotal=Decimal("99.00"),
                )


    def test_rls_impide_opcion_que_no_corresponde_al_producto(
        self,
    ):
        with tenant_database_context(
            tenant=self.tenant_a
        ):
            producto_2 = Producto.objects.create(
                categoria=self.categoria_a,
                nombre="Producto A2",
                precio=Decimal("15.00"),
                disponible=True,
            )

            opcion_2 = OpcionProducto.objects.create(
                producto=producto_2,
                nombre="Opcion A2",
                precio_extra=Decimal("1.00"),
                disponible=True,
            )

        with self.assertRaises(
            DatabaseError
        ):
            with tenant_database_context(
                tenant=self.tenant_a
            ):
                DetallePedido.objects.create(
                    pedido=self.pedido_a,
                    producto=self.producto_a,
                    opcion=opcion_2,
                    cantidad=1,
                    precio_unitario=Decimal("11.00"),
                    subtotal=Decimal("11.00"),
                )


    def test_rls_impide_extra_de_otro_tenant_en_detalle(
        self,
    ):
        with self.assertRaises(
            DatabaseError
        ):
            with tenant_database_context(
                tenant=self.tenant_a
            ):
                self.detalle_a.extras.add(
                    self.extra_b
                )
                
                
    def test_rls_suscripcion_y_estado_solo_ven_tenant_activo(
        self,
    ):
        with tenant_database_context(
            tenant=self.tenant_a
        ):
            suscripciones = set(
                SuscripcionTenant.objects.values_list(
                    "id",
                    flat=True,
                )
            )

            estados = set(
                EstadoPasarelaPago.objects.values_list(
                    "id",
                    flat=True,
                )
            )

        self.assertEqual(
            suscripciones,
            {self.suscripcion_a.id},
        )

        self.assertEqual(
            estados,
            {self.estado_pasarela_a.id},
        )


    def test_rls_suscripcion_y_estado_sin_tenant_no_ven_filas(
        self,
    ):
        with tenant_database_context():
            self.assertEqual(
                SuscripcionTenant.objects.count(),
                0,
            )

            self.assertEqual(
                EstadoPasarelaPago.objects.count(),
                0,
            )


    def test_rls_impide_suscripcion_para_otro_tenant(
        self,
    ):
        tenant_c = Tenant.objects.create(
            nombre="RLS Tenant C",
            slug="rls-tenant-c",
            habilitado=True,
        )

        with self.assertRaises(
            DatabaseError
        ):
            with tenant_database_context(
                tenant=self.tenant_a
            ):
                SuscripcionTenant.objects.create(
                    tenant=tenant_c,
                    estado=SuscripcionTenant.Estado.ACTIVA,
                    fecha_vencimiento=(
                        date.today()
                        + timedelta(days=30)
                    ),
                )


    def test_rls_estado_pasarela_no_acepta_configuracion_de_otro_tenant(
        self,
    ):
        with self.assertRaises(
            DatabaseError
        ):
            with tenant_database_context(
                tenant=self.tenant_a
            ):
                (
                    EstadoPasarelaPago.objects
                    .filter(
                        pk=self.estado_pasarela_a.pk
                    )
                    .update(
                        configuracion_negocio=
                            self.config_b_extra
                    )
                )
                
    def test_rls_pago_y_evento_wompi_solo_ven_tenant_activo(
        self,
    ):
        with tenant_database_context(
            tenant=self.tenant_a
        ):
            pagos = set(
                PagoWompi.objects.values_list(
                    "id",
                    flat=True,
                )
            )

            eventos = set(
                EventoPagoWompi.objects.values_list(
                    "id",
                    flat=True,
                )
            )

        self.assertEqual(
            pagos,
            {self.pago_a.id},
        )

        self.assertEqual(
            eventos,
            {self.evento_a.id},
        )


    def test_rls_pago_y_evento_wompi_sin_tenant_no_ven_filas(
        self,
    ):
        with tenant_database_context():
            self.assertEqual(
                PagoWompi.objects.count(),
                0,
            )

            self.assertEqual(
                EventoPagoWompi.objects.count(),
                0,
            )


    def test_rls_pago_wompi_no_acepta_pedido_de_otro_tenant(
        self,
    ):
        with self.assertRaises(
            DatabaseError
        ):
            with tenant_database_context(
                tenant=self.tenant_a
            ):
                PagoWompi.objects.create(
                    tipo="PEDIDO",
                    tenant=self.tenant_a,
                    pedido=self.pedido_b,
                    referencia="RLS-CROSS-PEDIDO",
                    cliente_token_hash="c" * 64,
                    monto=Decimal("50.00"),
                    estado="PENDIENTE",
                )


    def test_rls_pago_wompi_no_acepta_configuracion_de_otro_tenant(
        self,
    ):
        with self.assertRaises(
            DatabaseError
        ):
            with tenant_database_context(
                tenant=self.tenant_a
            ):
                PagoWompi.objects.create(
                    tipo="SUSCRIPCION",
                    tenant=self.tenant_a,
                    configuracion_negocio=self.config_b,
                    referencia="RLS-CROSS-CONFIG",
                    cliente_token_hash="d" * 64,
                    monto=Decimal("60.00"),
                    estado="PENDIENTE",
                )


    def test_rls_evento_wompi_no_acepta_relaciones_de_otro_tenant(
        self,
    ):
        with self.assertRaises(
            DatabaseError
        ):
            with tenant_database_context(
                tenant=self.tenant_a
            ):
                EventoPagoWompi.objects.create(
                    pago=self.pago_b,
                    pedido=self.pedido_b,
                    tenant=self.tenant_a,
                    configuracion_negocio=self.config_b,
                    cliente_token_hash="e" * 64,
                    categoria="SEGURIDAD",
                    origen="SISTEMA",
                    codigo="RLS_CROSS",
                    mensaje="Debe bloquearse",
                    clave_evento="RLS-EVENT-CROSS",
                )
                
                
    def test_rls_sucursal_y_asignaciones_solo_ven_tenant_activo(
        self,
    ):
        with tenant_database_context(
            tenant=self.tenant_a
        ):
            sucursales = set(
                Sucursal.objects.values_list(
                    "id",
                    flat=True,
                )
            )

            memberships = set(
                MembershipSucursal.objects.values_list(
                    "id",
                    flat=True,
                )
            )

            repartidores = set(
                RepartidorSucursal.objects.values_list(
                    "id",
                    flat=True,
                )
            )

        self.assertEqual(
            sucursales,
            {self.sucursal_a.id},
        )

        self.assertEqual(
            memberships,
            {self.membership_sucursal_a.id},
        )

        self.assertEqual(
            repartidores,
            {self.repartidor_sucursal_a.id},
        )


    def test_rls_sucursal_y_asignaciones_sin_tenant_no_ven_filas(
        self,
    ):
        with tenant_database_context():
            self.assertEqual(
                Sucursal.objects.count(),
                0,
            )

            self.assertEqual(
                MembershipSucursal.objects.count(),
                0,
            )

            self.assertEqual(
                RepartidorSucursal.objects.count(),
                0,
            )


    def test_rls_membership_sucursal_impide_cruce_de_tenants(
        self,
    ):
        with self.assertRaises(
            DatabaseError
        ):
            with tenant_database_context(
                tenant=self.tenant_a
            ):
                MembershipSucursal.objects.create(
                    membership=self.membership_a,
                    sucursal=self.sucursal_b,
                    activo=True,
                )

        with self.assertRaises(
            DatabaseError
        ):
            with tenant_database_context(
                tenant=self.tenant_a
            ):
                MembershipSucursal.objects.create(
                    membership=self.membership_b,
                    sucursal=self.sucursal_a,
                    activo=True,
                )


    def test_rls_repartidor_sucursal_impide_sucursal_ajena(
        self,
    ):
        with self.assertRaises(
            DatabaseError
        ):
            with tenant_database_context(
                tenant=self.tenant_a
            ):
                RepartidorSucursal.objects.create(
                    usuario=self.delivery_a,
                    sucursal=self.sucursal_b,
                    activo=True,
                )
            
            
class SucursalBusinessStateIsolationTests(TestCase):

    def setUp(self):
        self.tenant = Tenant.objects.create(
            nombre="Restaurante Multi",
            slug="restaurante-multi",
        )

        self.sucursal_a = Sucursal.objects.create(
            tenant=self.tenant,
            nombre="Sucursal A",
            slug="sucursal-a",
            estado=Sucursal.Estado.ACTIVA,
        )

        self.sucursal_b = Sucursal.objects.create(
            tenant=self.tenant,
            nombre="Sucursal B",
            slug="sucursal-b",
            estado=Sucursal.Estado.ACTIVA,
        )

        datos_config = {
            "hora_apertura": time(0, 0),
            "hora_cierre": time(23, 59),
            "lunes_abierto": True,
            "martes_abierto": True,
            "miercoles_abierto": True,
            "jueves_abierto": True,
            "viernes_abierto": True,
            "sabado_abierto": True,
            "domingo_abierto": True,
        }

        ConfiguracionNegocio.objects.create(
            sucursal=self.sucursal_a,
            **datos_config,
        )

        ConfiguracionNegocio.objects.create(
            sucursal=self.sucursal_b,
            **datos_config,
        )

    def test_dia_especial_de_una_sucursal_no_afecta_otra(self):
        DiaEspecial.objects.create(
            sucursal=self.sucursal_a,
            fecha=date.today(),
            abierto=False,
            motivo="Evento privado",
        )

        abierto_a, _ = verificar_estado_negocio(
            self.sucursal_a
        )

        abierto_b, _ = verificar_estado_negocio(
            self.sucursal_b
        )

        self.assertFalse(
            abierto_a
        )

        self.assertTrue(
            abierto_b
        )

    def test_misma_fecha_puede_existir_en_dos_sucursales(self):
        DiaEspecial.objects.create(
            sucursal=self.sucursal_a,
            fecha=date.today(),
            abierto=False,
        )

        DiaEspecial.objects.create(
            sucursal=self.sucursal_b,
            fecha=date.today(),
            abierto=True,
            hora_apertura=time(0, 0),
            hora_cierre=time(23, 59),
        )

        self.assertEqual(
            DiaEspecial.objects.filter(
                fecha=date.today()
            ).count(),
            2,
        )
        

class AdminSettingsSucursalIsolationTests(
    TestCase
):

    def setUp(self):
        User = get_user_model()

        self.admin = (
            User.objects.create_superuser(
                username="admin-settings-test",
                password="test12345",
                email="admin@test.com",
            )
        )

        self.tenant = Tenant.objects.create(
            nombre="Restaurante Settings",
            slug="restaurante-settings",
        )

        Membership.objects.create(
            tenant=self.tenant,
            usuario=self.admin,
            rol=Membership.ROLE_OWNER,
            activo=True,
        )

        self.sucursal_a = (
            Sucursal.objects.create(
                tenant=self.tenant,
                nombre="Sucursal A",
                slug="sucursal-a-settings",
                estado=Sucursal.Estado.ACTIVA,
            )
        )

        self.sucursal_b = (
            Sucursal.objects.create(
                tenant=self.tenant,
                nombre="Sucursal B",
                slug="sucursal-b-settings",
                estado=Sucursal.Estado.ACTIVA,
            )
        )

        self.config_a = (
            ConfiguracionNegocio.objects.create(
                sucursal=self.sucursal_a,
            )
        )

        self.config_b = (
            ConfiguracionNegocio.objects.create(
                sucursal=self.sucursal_b,
            )
        )

        self.client.force_login(
            self.admin
        )

        session = self.client.session

        session[
            "sucursal_activa_public_id"
        ] = str(
            self.sucursal_a.public_id
        )

        session.save()

    @patch(
        "pedidos.views.suscripcion_activa",
        return_value=True,
    )
    def test_configuracion_global_solo_modifica_sucursal_activa(
        self,
        mock_suscripcion,
    ):
        response = self.client.post(
            reverse("admin_settings"),
            {
                "tipo_accion": "global",
                "hora_apertura": "08:00",
                "hora_cierre": "21:00",
                "mensaje_cierre": (
                    "Sucursal A cerrada"
                ),
                "lunes_abierto": "on",
                "martes_abierto": "on",
            },
        )

        self.assertEqual(
            response.status_code,
            302,
        )

        self.config_a.refresh_from_db()
        self.config_b.refresh_from_db()

        self.assertEqual(
            self.config_a.mensaje_cierre,
            "Sucursal A cerrada",
        )

        self.assertNotEqual(
            self.config_b.mensaje_cierre,
            "Sucursal A cerrada",
        )

    @patch(
        "pedidos.views.suscripcion_activa",
        return_value=True,
    )
    def test_excepcion_se_crea_solo_en_sucursal_activa(
        self,
        mock_suscripcion,
    ):
        fecha = (
            date.today()
            + timedelta(days=2)
        )

        response = self.client.post(
            reverse("admin_settings"),
            {
                "tipo_accion":
                    "dia_especifico",
                "fecha_target":
                    fecha.strftime(
                        "%Y-%m-%d"
                    ),
                "motivo":
                    "Evento sucursal A",
            },
        )

        self.assertEqual(
            response.status_code,
            302,
        )

        self.assertTrue(
            DiaEspecial.objects.filter(
                sucursal=self.sucursal_a,
                fecha=fecha,
            ).exists()
        )

        self.assertFalse(
            DiaEspecial.objects.filter(
                sucursal=self.sucursal_b,
                fecha=fecha,
            ).exists()
        )

    @patch(
        "pedidos.views.suscripcion_activa",
        return_value=True,
    )
    def test_no_puede_eliminar_excepcion_de_otra_sucursal(
        self,
        mock_suscripcion,
    ):
        excepcion_b = (
            DiaEspecial.objects.create(
                sucursal=self.sucursal_b,
                fecha=(
                    date.today()
                    + timedelta(days=3)
                ),
                abierto=False,
                motivo="Solo B",
            )
        )

        response = self.client.post(
            reverse(
                "eliminar_excepcion",
                args=[
                    excepcion_b.id
                ],
            )
        )

        self.assertEqual(
            response.status_code,
            404,
        )

        self.assertTrue(
            DiaEspecial.objects.filter(
                id=excepcion_b.id
            ).exists()
        )
        
        
class AdminDashboardSucursalIsolationTests(
    FoodBackTestBase
):

    def setUp(self):
        self.client.force_login(
            self.admin_user
        )

        session = self.client.session

        session[
            "sucursal_activa_public_id"
        ] = str(
            self.sucursal.public_id
        )

        session.save()

        self.sucursal_b = (
            Sucursal.objects.create(
                tenant=self.tenant,
                nombre="Sucursal B",
                slug="sucursal-b-dashboard",
                estado=(
                    Sucursal.Estado.ACTIVA
                ),
            )
        )

        self.config_b = (
            ConfiguracionNegocio.objects.create(
                sucursal=self.sucursal_b,
                fecha_vencimiento=(
                    date.today()
                    + timedelta(days=365)
                ),
            )
        )

        self.pedido_a = (
            Pedido.objects.create(
                sucursal=self.sucursal,
                cliente=self.cliente_pedido,
                estado="RECIBIDO",
                metodo_pago="EFECTIVO",
            )
        )

        self.pedido_b = (
            Pedido.objects.create(
                sucursal=self.sucursal_b,
                cliente=self.cliente_pedido,
                estado="RECIBIDO",
                metodo_pago="EFECTIVO",
            )
        )

    def test_dashboard_solo_muestra_pedidos_de_sucursal_activa(
        self,
    ):
        response = self.client.get(
            reverse(
                "dashboard_admin"
            )
        )

        self.assertEqual(
            response.status_code,
            200,
        )

        pedidos_ids = {
            pedido.id
            for pedido
            in response.context[
                "pedidos"
            ]
        }

        self.assertIn(
            self.pedido_a.id,
            pedidos_ids,
        )

        self.assertNotIn(
            self.pedido_b.id,
            pedidos_ids,
        )

    def test_admin_no_puede_modificar_pedido_de_otra_sucursal(
        self,
    ):
        response = self.client.post(
            reverse(
                "dashboard_admin"
            ),
            {
                "pedido_id": (
                    self.pedido_b.id
                ),
                "accion": "cocina",
            },
        )

        self.assertEqual(
            response.status_code,
            404,
        )

        self.pedido_b.refresh_from_db()

        self.assertEqual(
            self.pedido_b.estado,
            "RECIBIDO",
        )

    def test_polling_admin_cuenta_solo_sucursal_activa(
        self,
    ):
        response = self.client.get(
            reverse(
                "api_dashboard_admin_sync"
            )
        )

        self.assertEqual(
            response.status_code,
            200,
        )

        data = response.json()

        self.assertTrue(
            data["changed"]
        )

        self.assertEqual(
            data["nuevos_count"],
            1,
        )
        
    def test_polling_admin_rechaza_last_update_invalido(
        self,
    ):
        response = self.client.get(
            reverse(
                "api_dashboard_admin_sync"
            ),
            {
                "last_update":
                    "esto-no-es-una-fecha",
            },
        )

        self.assertEqual(
            response.status_code,
            400,
        )
        
        
class DeliverySucursalIsolationTests(
    FoodBackTestBase
):

    def setUp(self):
        self.client.force_login(
            self.delivery_1
        )

        session = self.client.session

        session[
            "sucursal_activa_public_id"
        ] = str(
            self.sucursal.public_id
        )

        session.save()

        self.sucursal_b = (
            Sucursal.objects.create(
                tenant=self.tenant,
                nombre="Sucursal B Delivery",
                slug="sucursal-b-delivery",
                estado=(
                    Sucursal.Estado.ACTIVA
                ),
            )
        )

        self.pedido_a = (
            Pedido.objects.create(
                sucursal=self.sucursal,
                cliente=self.cliente_pedido,
                estado="RUTA",
                metodo_pago="EFECTIVO",
            )
        )

        self.pedido_b = (
            Pedido.objects.create(
                sucursal=self.sucursal_b,
                cliente=self.cliente_pedido,
                estado="RUTA",
                metodo_pago="EFECTIVO",
            )
        )

    @patch(
        "pedidos.views.suscripcion_activa",
        return_value=True,
    )
    def test_delivery_solo_ve_pedidos_de_sucursal_activa(
        self,
        mock_suscripcion,
    ):
        response = self.client.get(
            reverse(
                "dashboard_delivery"
            )
        )

        self.assertEqual(
            response.status_code,
            200,
        )

        disponibles_ids = {
            pedido.id
            for pedido
            in response.context[
                "disponibles"
            ]
        }

        self.assertIn(
            self.pedido_a.id,
            disponibles_ids,
        )

        self.assertNotIn(
            self.pedido_b.id,
            disponibles_ids,
        )

    @patch(
        "pedidos.views.suscripcion_activa",
        return_value=True,
    )
    def test_delivery_no_puede_tomar_pedido_de_otra_sucursal(
        self,
        mock_suscripcion,
    ):
        response = self.client.post(
            reverse(
                "dashboard_delivery"
            ),
            {
                "pedido_id": (
                    self.pedido_b.id
                ),
                "accion": "tomar",
            },
        )

        self.assertEqual(
            response.status_code,
            404,
        )

        self.pedido_b.refresh_from_db()

        self.assertIsNone(
            self.pedido_b.repartidor
        )

    def test_polling_delivery_solo_cuenta_sucursal_activa(
        self,
    ):
        response = self.client.get(
            reverse(
                "api_delivery_sync"
            )
        )

        self.assertEqual(
            response.status_code,
            200,
        )

        data = response.json()

        self.assertTrue(
            data["changed"]
        )

        self.assertEqual(
            data["pool_count"],
            1,
        )
        
    def test_polling_delivery_rechaza_last_update_invalido(
        self,
    ):
        response = self.client.get(
            reverse(
                "api_delivery_sync"
            ),
            {
                "last_update":
                    "esto-no-es-una-fecha",
            },
        )

        self.assertEqual(
            response.status_code,
            400,
        )
        
class CatalogTenantIsolationTests(
    FoodBackTestBase
):

    def setUp(self):
        self.tenant_b = (
            Tenant.objects.create(
                nombre="Restaurante B",
                slug="restaurante-b",
            )
        )

        self.categoria_b = (
            Categoria.objects.create(
                tenant=self.tenant_b,
                nombre="Categoria B",
                orden=1,
            )
        )

        self.producto_b = (
            Producto.objects.create(
                categoria=self.categoria_b,
                nombre="Producto B",
                descripcion="Otro Tenant",
                precio=Decimal("9.00"),
                disponible=True,
            )
        )

        self.opcion_b = (
            OpcionProducto.objects.create(
                producto=self.producto_b,
                nombre="Opcion B",
                precio_extra=Decimal(
                    "1.00"
                ),
                disponible=True,
            )
        )

        self.extra_b = (
            Extra.objects.create(
                tenant=self.tenant_b,
                nombre="Extra B",
                precio=Decimal("2.00"),
                disponible=True,
            )
        )

        self.producto_b.extras.add(
            self.extra_b
        )

    def test_menu_no_muestra_catalogo_de_otro_tenant(
        self,
    ):
        response = self.client.get(
            reverse("menu")
        )

        self.assertEqual(
            response.status_code,
            200,
        )

        ids = {
            categoria.id
            for categoria
            in response.context[
                "categorias"
            ]
        }

        self.assertIn(
            self.categoria.id,
            ids,
        )

        self.assertNotIn(
            self.categoria_b.id,
            ids,
        )

    def test_carrito_rechaza_producto_de_otro_tenant(
        self,
    ):
        cart = {
            str(
                self.producto_b.id
            ): 1,
        }

        with self.assertRaises(
            CarritoInvalido
        ):
            _validar_carrito(
                cart,
                self.tenant,
            )

    def test_opcion_de_otro_tenant_no_puede_inyectarse(
        self,
    ):
        with self.assertRaises(
            CarritoInvalido
        ):
            _validar_seleccion_producto(
                producto=self.producto,
                opcion_id=self.opcion_b.id,
                extras_ids=[],
                tenant=self.tenant,
            )

    def test_extra_de_otro_tenant_no_puede_inyectarse(
        self,
    ):
        # Simulamos incluso una relación M2M corrupta/mal creada.
        self.producto.extras.add(
            self.extra_b
        )

        with self.assertRaises(
            CarritoInvalido
        ):
            _validar_seleccion_producto(
                producto=self.producto,
                opcion_id=None,
                extras_ids=[
                    self.extra_b.id
                ],
                tenant=self.tenant,
            )
                



class TenantSubscriptionIsolationTests(
    FoodBackTestBase
):

    def setUp(self):
        self.tenant_b = Tenant.objects.create(
            nombre="Restaurante B Suscripción",
            slug="restaurante-b-suscripcion",
            habilitado=True,
        )

        self.suscripcion_b = (
            SuscripcionTenant.objects.create(
                tenant=self.tenant_b,
                estado=(
                    SuscripcionTenant.Estado.ACTIVA
                ),
                fecha_vencimiento=(
                    date.today()
                    + timedelta(days=20)
                ),
            )
        )

    def test_tenant_vencido_no_bloquea_otro_tenant(
        self,
    ):
        self.suscripcion.fecha_vencimiento = (
            date.today()
            - timedelta(days=1)
        )
        self.suscripcion.save()

        self.assertFalse(
            suscripcion_activa(
                self.tenant
            )
        )

        self.assertTrue(
            suscripcion_activa(
                self.tenant_b
            )
        )

    def test_renovar_tenant_a_no_modifica_tenant_b(
        self,
    ):
        vencimiento_b = (
            self.suscripcion_b.fecha_vencimiento
        )

        _renovar_suscripcion_30_dias(
            self.suscripcion
        )

        self.suscripcion_b.refresh_from_db()

        self.assertEqual(
            self.suscripcion_b.fecha_vencimiento,
            vencimiento_b,
        )

    def test_pago_suscripcion_a_no_renueva_b(
        self,
    ):
        vencimiento_a = (
            self.suscripcion.fecha_vencimiento
        )

        vencimiento_b = (
            self.suscripcion_b.fecha_vencimiento
        )

        pago = PagoWompi.objects.create(
            tenant=self.tenant,
            tipo="SUSCRIPCION",
            referencia="SUBS-TENANT-A-TEST",
            monto=Decimal("50.00"),
            estado="PENDIENTE",
        )

        ok, _ = _procesar_pago_wompi_aprobado(
            pago.referencia,
            id_transaccion="TX-SUB-TENANT-A",
            monto="50.00",
        )

        self.assertTrue(ok)

        self.suscripcion.refresh_from_db()
        self.suscripcion_b.refresh_from_db()

        self.assertEqual(
            self.suscripcion.fecha_vencimiento,
            vencimiento_a
            + timedelta(days=30),
        )

        self.assertEqual(
            self.suscripcion_b.fecha_vencimiento,
            vencimiento_b,
        )
        

class MetricsAndProfileSucursalIsolationTests(
    FoodBackTestBase
):

    def setUp(self):
        self.sucursal_b = (
            Sucursal.objects.create(
                tenant=self.tenant,
                nombre="Sucursal B Métricas",
                slug="sucursal-b-metricas",
                estado=(
                    Sucursal.Estado.ACTIVA
                ),
            )
        )

        self.pedido_a = (
            self.crear_pedido(
                estado="RECIBIDO",
                telefono="79990001",
            )
        )

        self.pedido_a.total_productos = (
            Decimal("10.00")
        )

        self.pedido_a.save()

        cliente_b = Cliente.objects.create(
            tenant=self.tenant,
            telefono="79990002",
            nombre="Cliente",
            apellido="Sucursal B",
            direccion_ultima="San Miguel",
        )

        self.pedido_b = (
            Pedido.objects.create(
                sucursal=self.sucursal_b,
                cliente=cliente_b,
                direccion_entrega="Sucursal B",
                metodo_pago="EFECTIVO",
                estado="RECIBIDO",
                total_productos=(
                    Decimal("100.00")
                ),
            )
        )

    def test_metricas_solo_incluyen_sucursal_activa(
        self,
    ):
        self.client.force_login(
            self.admin_user
        )

        response = self.client.get(
            reverse(
                "dashboard_metrics"
            )
        )

        self.assertEqual(
            response.status_code,
            200,
        )

        self.assertEqual(
            Decimal(
                str(
                    response.context[
                        "total_ventas_hoy"
                    ]
                )
            ),
            Decimal("10.00"),
        )

        self.assertEqual(
            response.context[
                "cantidad_pedidos_hoy"
            ],
            1,
        )

    def test_perfil_no_muestra_pedido_de_otra_sucursal(
        self,
    ):
        session = self.client.session

        session[
            "historial_pedidos"
        ] = [
            self.pedido_a.id,
            self.pedido_b.id,
        ]

        session.save()

        response = self.client.get(
            reverse(
                "perfil_usuario"
            )
        )

        self.assertEqual(
            response.status_code,
            200,
        )

        visibles = (
            list(
                response.context[
                    "activos"
                ]
            )
            +
            list(
                response.context[
                    "historial"
                ]
            )
        )

        ids_visibles = {
            pedido.id
            for pedido in visibles
        }

        self.assertIn(
            self.pedido_a.id,
            ids_visibles,
        )

        self.assertNotIn(
            self.pedido_b.id,
            ids_visibles,
        )



class ClienteTenantIsolationTests(
    FoodBackTestBase
):

    def setUp(self):
        self.tenant_b = Tenant.objects.create(
            nombre="Restaurante B Clientes",
            slug="restaurante-b-clientes",
        )

    def test_mismo_telefono_puede_existir_en_dos_tenants(
        self,
    ):
        telefono = "79998881"

        cliente_a = Cliente.objects.create(
            tenant=self.tenant,
            telefono=telefono,
            nombre="Cliente A",
            apellido="Prueba",
        )

        cliente_b = Cliente.objects.create(
            tenant=self.tenant_b,
            telefono=telefono,
            nombre="Cliente B",
            apellido="Prueba",
        )

        self.assertNotEqual(
            cliente_a.id,
            cliente_b.id,
        )

        self.assertEqual(
            Cliente.objects.filter(
                telefono=telefono
            ).count(),
            2,
        )
        
    def test_base_datos_impide_cliente_sin_tenant(
        self,
    ):
        with self.assertRaises(
            IntegrityError
        ):
            with transaction.atomic():
                Cliente.objects.create(
                    telefono="79998884",
                    nombre="Sin",
                    apellido="Tenant",
                )

    def test_mismo_telefono_no_puede_duplicarse_en_mismo_tenant(
        self,
    ):
        telefono = "79998882"

        Cliente.objects.create(
            tenant=self.tenant,
            telefono=telefono,
            nombre="Primero",
            apellido="Cliente",
        )

        with self.assertRaises(
            IntegrityError
        ):
            with transaction.atomic():
                Cliente.objects.create(
                    tenant=self.tenant,
                    telefono=telefono,
                    nombre="Segundo",
                    apellido="Cliente",
                )

    def test_checkout_no_modifica_cliente_de_otro_tenant(
        self,
    ):
        telefono = "79998883"

        cliente_b = Cliente.objects.create(
            tenant=self.tenant_b,
            telefono=telefono,
            nombre="NO TOCAR",
            apellido="Tenant B",
            direccion_ultima=(
                "Dirección Tenant B"
            ),
        )

        session = self.client.session

        session["cart"] = {
            f"{self.producto.id}-0-0": 1
        }

        session.save()
        
        checkout_token = (
            self._crear_checkout_token_test()
        )

        response = self.client.post(
            reverse("checkout"),
            {
                "checkout_token": checkout_token,
                
                "telefono":
                    telefono,

                "nombre":
                    "Cliente Rancheritos",

                "apellido":
                    "Tenant A",

                "direccion":
                    "Dirección Tenant A",

                "metodo_pago":
                    "EFECTIVO",

                "latitud":
                    "13.4800",

                "longitud":
                    "-88.1800",
            },
        )

        self.assertEqual(
            response.status_code,
            302,
        )

        cliente_b.refresh_from_db()

        self.assertEqual(
            cliente_b.nombre,
            "NO TOCAR",
        )

        self.assertEqual(
            cliente_b.direccion_ultima,
            "Dirección Tenant B",
        )

        cliente_a = Cliente.objects.get(
            tenant=self.tenant,
            telefono=telefono,
        )

        self.assertNotEqual(
            cliente_a.id,
            cliente_b.id,
        )

        self.assertEqual(
            cliente_a.nombre,
            "Cliente Rancheritos",
        )
        
class WompiRedirectTenantIsolationTests(
    FoodBackTestBase
):

    def test_redirect_de_otro_tenant_no_toca_pago_ni_sesion(
        self,
    ):
        tenant_b = Tenant.objects.create(
            nombre="Restaurante B Redirect",
            slug="restaurante-b-redirect",
        )

        sucursal_b = Sucursal.objects.create(
            tenant=tenant_b,
            nombre="Sucursal B",
            slug="principal-b",
            estado=Sucursal.Estado.ACTIVA,
        )

        cliente_b = Cliente.objects.create(
            tenant=tenant_b,
            telefono="78889991",
            nombre="Cliente",
            apellido="Tenant B",
        )

        pedido_b = Pedido.objects.create(
            sucursal=sucursal_b,
            cliente=cliente_b,
            direccion_entrega="Tenant B",
            metodo_pago="TARJETA",
            estado="PENDIENTE",
            total_productos=Decimal("10.00"),
        )

        pago_b = PagoWompi.objects.create(
            tipo="PEDIDO",
            tenant=tenant_b,
            pedido=pedido_b,
            referencia=(
                "ORDEN-TENANT-B-REDIRECT"
            ),
            monto=Decimal("10.00"),
            estado="PENDIENTE",
        )

        response = self.client.get(
            reverse(
                "wompi_respuesta"
            ),
            {
                "ref":
                    pago_b.referencia,

                "idTransaccion":
                    "TX-AJENA",

                "monto":
                    "10.00",
            },
        )

        self.assertEqual(
            response.status_code,
            302,
        )

        pago_b.refresh_from_db()

        self.assertEqual(
            pago_b.raw_redirect,
            {},
        )

        session = self.client.session

        self.assertNotEqual(
            session.get(
                "ultimo_pedido_id"
            ),
            pedido_b.id,
        )

        self.assertNotIn(
            pedido_b.id,
            session.get(
                "historial_pedidos",
                [],
            ),
        )
        

class TenantMembershipAuthorizationTests(
    FoodBackTestBase
):

    def test_owner_membership_puede_entrar_dashboard(
        self,
    ):
        self.client.force_login(
            self.admin_user
        )

        response = self.client.get(
            reverse(
                "dashboard_admin"
            )
        )

        self.assertEqual(
            response.status_code,
            200,
        )


    def test_manager_membership_puede_entrar_dashboard(
        self,
    ):
        manager = User.objects.create_user(
            username="manager_authz",
            password="test12345",
        )

        membership = Membership.objects.create(
            tenant=self.tenant,
            usuario=manager,
            rol=Membership.ROLE_MANAGER,
            activo=True,
        )

        MembershipSucursal.objects.create(
            membership=membership,
            sucursal=self.sucursal,
            activo=True,
        )

        self.client.force_login(
            manager
        )

        response = self.client.get(
            reverse(
                "dashboard_admin"
            )
        )

        self.assertEqual(
            response.status_code,
            200,
        )


    def test_usuario_solo_con_group_admin_no_es_autorizado(
        self,
    ):
        legacy_admin = (
            User.objects.create_user(
                username="legacy_admin",
                password="test12345",
            )
        )

        legacy_admin.groups.add(
            self.grupo_admin
        )

        self.client.force_login(
            legacy_admin
        )

        response = self.client.get(
            reverse(
                "dashboard_admin"
            )
        )

        self.assertEqual(
            response.status_code,
            403,
        )
        
class LoginMembershipRoutingTests(
    FoodBackTestBase
):

    def test_owner_sin_group_admin_va_dashboard(
        self,
    ):
        owner = User.objects.create_user(
            username="owner_login",
            password="PasswordSeguro123!",
        )

        Membership.objects.create(
            tenant=self.tenant,
            usuario=owner,
            rol=Membership.ROLE_OWNER,
            activo=True,
        )

        response = self.client.post(
            reverse("login_custom"),
            {
                "username": "owner_login",
                "password": "PasswordSeguro123!",
            },
        )

        self.assertRedirects(
            response,
            reverse("dashboard_admin"),
            fetch_redirect_response=False,
        )


    def test_manager_sin_group_admin_va_dashboard(
        self,
    ):
        manager = User.objects.create_user(
            username="manager_login",
            password="PasswordSeguro123!",
        )

        Membership.objects.create(
            tenant=self.tenant,
            usuario=manager,
            rol=Membership.ROLE_MANAGER,
            activo=True,
        )

        response = self.client.post(
            reverse("login_custom"),
            {
                "username": "manager_login",
                "password": "PasswordSeguro123!",
            },
        )

        self.assertRedirects(
            response,
            reverse("dashboard_admin"),
            fetch_redirect_response=False,
        )


    def test_group_admin_sin_membership_no_va_dashboard(
        self,
    ):
        legacy = User.objects.create_user(
            username="legacy_login",
            password="PasswordSeguro123!",
        )

        legacy.groups.add(
            self.grupo_admin
        )

        response = self.client.post(
            reverse("login_custom"),
            {
                "username": "legacy_login",
                "password": "PasswordSeguro123!",
            },
        )

        self.assertRedirects(
            response,
            reverse("menu"),
            fetch_redirect_response=False,
        )


    def test_superuser_sin_membership_no_va_dashboard_restaurante(
        self,
    ):
        superuser = User.objects.create_superuser(
            username="platform_superuser",
            password="PasswordSeguro123!",
            email="platform@test.com",
        )

        response = self.client.post(
            reverse("login_custom"),
            {
                "username":
                    "platform_superuser",

                "password":
                    "PasswordSeguro123!",
            },
        )

        self.assertRedirects(
            response,
            reverse("menu"),
            fetch_redirect_response=False,
        )


    def test_repartidor_sin_group_va_delivery(
        self,
    ):
        repartidor = User.objects.create_user(
            username="delivery_login_real",
            password="PasswordSeguro123!",
        )

        RepartidorSucursal.objects.create(
            usuario=repartidor,
            sucursal=self.sucursal,
            activo=True,
        )

        response = self.client.post(
            reverse("login_custom"),
            {
                "username":
                    "delivery_login_real",

                "password":
                    "PasswordSeguro123!",
            },
        )

        self.assertRedirects(
            response,
            reverse("dashboard_delivery"),
            fetch_redirect_response=False,
        )
        
    
    def test_group_repartidores_sin_asignacion_no_va_delivery(
        self,
    ):
        legacy = User.objects.create_user(
            username="delivery_group_only",
            password="PasswordSeguro123!",
        )

        legacy.groups.add(
            self.grupo_delivery
        )

        response = self.client.post(
            reverse("login_custom"),
            {
                "username":
                    "delivery_group_only",

                "password":
                    "PasswordSeguro123!",
            },
        )

        self.assertRedirects(
            response,
            reverse("menu"),
            fetch_redirect_response=False,
        )

class DeliveryAssignmentAuthorizationTests(
    FoodBackTestBase
):

    def test_repartidor_asignado_puede_entrar(
        self,
    ):
        self.client.force_login(
            self.delivery_1
        )

        response = self.client.get(
            reverse(
                "dashboard_delivery"
            )
        )

        self.assertEqual(
            response.status_code,
            200,
        )


    def test_group_repartidores_sin_asignacion_no_autoriza(
        self,
    ):
        usuario = User.objects.create_user(
            username="delivery_legacy_only",
            password="PasswordSeguro123!",
        )

        usuario.groups.add(
            self.grupo_delivery
        )

        self.client.force_login(
            usuario
        )

        response = self.client.get(
            reverse(
                "dashboard_delivery"
            )
        )

        self.assertEqual(
            response.status_code,
            403,
        )


    def test_repartidor_resuelve_su_sucursal_asignada(
        self,
    ):
        sucursal_b = Sucursal.objects.create(
            tenant=self.tenant,
            nombre="Sucursal B Delivery",
            slug="sucursal-b-delivery",
            estado=Sucursal.Estado.ACTIVA,
        )

        usuario = User.objects.create_user(
            username="delivery_sucursal_b",
            password="PasswordSeguro123!",
        )

        RepartidorSucursal.objects.create(
            usuario=usuario,
            sucursal=sucursal_b,
            activo=True,
        )

        self.client.force_login(
            usuario
        )

        response = self.client.get(
            reverse(
                "dashboard_delivery"
            )
        )

        self.assertEqual(
            response.status_code,
            200,
        )

        self.assertEqual(
            response.wsgi_request.sucursal,
            sucursal_b,
        )


    def test_manager_no_es_repartidor_automaticamente(
        self,
    ):
        manager = User.objects.create_user(
            username="manager_not_delivery",
            password="PasswordSeguro123!",
        )

        Membership.objects.create(
            tenant=self.tenant,
            usuario=manager,
            rol=Membership.ROLE_MANAGER,
            activo=True,
        )

        self.client.force_login(
            manager
        )

        response = self.client.get(
            reverse(
                "dashboard_delivery"
            )
        )

        self.assertEqual(
            response.status_code,
            403,
        )
        
        
class ManagerBranchAuthorizationTests(
    FoodBackTestBase
):

    def setUp(self):
        self.manager = User.objects.create_user(
            username="manager_branch_auth",
            password="PasswordSeguro123!",
        )

        self.membership = (
            Membership.objects.create(
                tenant=self.tenant,
                usuario=self.manager,
                rol=Membership.ROLE_MANAGER,
                activo=True,
            )
        )

        self.sucursal_b = Sucursal.objects.create(
            tenant=self.tenant,
            nombre="Sucursal B Auth",
            slug="sucursal-b-auth",
            estado=Sucursal.Estado.ACTIVA,
        )


    def test_manager_asignado_puede_administrar_sucursal(
        self,
    ):
        MembershipSucursal.objects.create(
            membership=self.membership,
            sucursal=self.sucursal,
            activo=True,
        )

        from pedidos.authz import (
            obtener_acceso_admin_sucursal,
        )

        request = type(
            "Request",
            (),
            {},
        )()

        request.user = self.manager
        request.tenant = self.tenant
        request.sucursal = self.sucursal

        acceso = (
            obtener_acceso_admin_sucursal(
                request
            )
        )

        self.assertEqual(
            acceso,
            self.membership,
        )


    def test_manager_no_asignado_no_administra_otra_sucursal(
        self,
    ):
        MembershipSucursal.objects.create(
            membership=self.membership,
            sucursal=self.sucursal,
            activo=True,
        )

        from pedidos.authz import (
            obtener_acceso_admin_sucursal,
        )

        request = type(
            "Request",
            (),
            {},
        )()

        request.user = self.manager
        request.tenant = self.tenant
        request.sucursal = self.sucursal_b

        self.assertIsNone(
            obtener_acceso_admin_sucursal(
                request
            )
        )


    def test_owner_administra_todas_las_sucursales_sin_asignacion(
        self,
    ):
        from pedidos.authz import (
            obtener_acceso_admin_sucursal,
        )

        request = type(
            "Request",
            (),
            {},
        )()

        request.user = self.admin_user
        request.tenant = self.tenant
        request.sucursal = self.sucursal_b

        acceso = (
            obtener_acceso_admin_sucursal(
                request
            )
        )

        self.assertIsNotNone(
            acceso
        )

        self.assertEqual(
            acceso.rol,
            Membership.ROLE_OWNER,
        )


    def test_sucursal_de_otro_tenant_nunca_autoriza(
        self,
    ):
        tenant_b = Tenant.objects.create(
            nombre="Tenant B Branch Auth",
            slug="tenant-b-branch-auth",
        )

        sucursal_otro_tenant = (
            Sucursal.objects.create(
                tenant=tenant_b,
                nombre="Sucursal Ajena",
                slug="sucursal-ajena",
                estado=Sucursal.Estado.ACTIVA,
            )
        )

        # Incluso si alguien consiguiera introducir
        # una relación inconsistente en la BD...
        MembershipSucursal.objects.create(
            membership=self.membership,
            sucursal=sucursal_otro_tenant,
            activo=True,
        )

        from pedidos.authz import (
            obtener_acceso_admin_sucursal,
        )

        request = type(
            "Request",
            (),
            {},
        )()

        request.user = self.manager
        request.tenant = self.tenant
        request.sucursal = sucursal_otro_tenant

        self.assertIsNone(
            obtener_acceso_admin_sucursal(
                request
            )
        )
        
class SucursalContextAuthorizationTests(
    FoodBackTestBase
):

    def setUp(self):
        self.sucursal_b = Sucursal.objects.create(
            tenant=self.tenant,
            nombre="Sucursal B Context",
            slug="sucursal-b-context",
            estado=Sucursal.Estado.ACTIVA,
        )

    def test_manager_resuelve_solo_sucursal_asignada(
        self,
    ):
        manager = User.objects.create_user(
            username="manager_context_b",
            password="PasswordSeguro123!",
        )

        membership = Membership.objects.create(
            tenant=self.tenant,
            usuario=manager,
            rol=Membership.ROLE_MANAGER,
            activo=True,
        )

        MembershipSucursal.objects.create(
            membership=membership,
            sucursal=self.sucursal_b,
            activo=True,
        )

        self.client.force_login(
            manager
        )

        response = self.client.get(
            reverse("dashboard_admin")
        )

        self.assertEqual(
            response.status_code,
            200,
        )

        self.assertEqual(
            response.wsgi_request.sucursal,
            self.sucursal_b,
        )


    def test_session_no_permite_manager_saltar_a_sucursal_no_asignada(
        self,
    ):
        manager = User.objects.create_user(
            username="manager_context_session",
            password="PasswordSeguro123!",
        )

        membership = Membership.objects.create(
            tenant=self.tenant,
            usuario=manager,
            rol=Membership.ROLE_MANAGER,
            activo=True,
        )

        MembershipSucursal.objects.create(
            membership=membership,
            sucursal=self.sucursal_b,
            activo=True,
        )

        self.client.force_login(
            manager
        )

        session = self.client.session

        session[
            "sucursal_activa_public_id"
        ] = str(
            self.sucursal.public_id
        )

        session.save()

        response = self.client.get(
            reverse("dashboard_admin")
        )

        self.assertEqual(
            response.status_code,
            200,
        )

        self.assertEqual(
            response.wsgi_request.sucursal,
            self.sucursal_b,
        )


    def test_owner_puede_resolver_segunda_sucursal(
        self,
    ):
        self.client.force_login(
            self.admin_user
        )

        session = self.client.session

        session[
            "sucursal_activa_public_id"
        ] = str(
            self.sucursal_b.public_id
        )

        session.save()

        response = self.client.get(
            reverse("dashboard_admin")
        )

        self.assertEqual(
            response.status_code,
            200,
        )

        self.assertEqual(
            response.wsgi_request.sucursal,
            self.sucursal_b,
        )


    def test_repartidor_resuelve_solo_sucursal_asignada(
        self,
    ):
        repartidor = User.objects.create_user(
            username="delivery_context_b",
            password="PasswordSeguro123!",
        )

        RepartidorSucursal.objects.create(
            usuario=repartidor,
            sucursal=self.sucursal_b,
            activo=True,
        )

        self.client.force_login(
            repartidor
        )

        response = self.client.get(
            reverse("dashboard_delivery")
        )

        self.assertEqual(
            response.status_code,
            200,
        )

        self.assertEqual(
            response.wsgi_request.sucursal,
            self.sucursal_b,
        )
        
        
class BranchSwitchAuthorizationTests(
    FoodBackTestBase
):

    def setUp(self):
        self.sucursal_b = Sucursal.objects.create(
            tenant=self.tenant,
            nombre="Sucursal B Switch",
            slug="sucursal-b-switch",
            estado=Sucursal.Estado.ACTIVA,
        )


    def test_owner_puede_cambiar_a_otra_sucursal(
        self,
    ):
        self.client.force_login(
            self.admin_user
        )

        response = self.client.post(
            reverse(
                "cambiar_sucursal"
            ),
            {
                "sucursal_public_id":
                    str(
                        self.sucursal_b.public_id
                    ),
            },
        )

        self.assertEqual(
            response.status_code,
            302,
        )

        session = self.client.session

        self.assertEqual(
            session[
                "sucursal_activa_public_id"
            ],
            str(
                self.sucursal_b.public_id
            ),
        )

    def test_owner_dashboard_muestra_todas_sus_sucursales(
        self,
    ):
        self.client.force_login(
            self.admin_user
        )

        response = self.client.get(
            reverse(
                "dashboard_admin"
            )
        )

        sucursales = list(
            response.context[
                "sucursales_disponibles"
            ]
        )

        self.assertIn(
            self.sucursal,
            sucursales,
        )

        self.assertIn(
            self.sucursal_b,
            sucursales,
        )


    def test_manager_dashboard_no_expone_sucursal_no_asignada(
        self,
    ):
        manager = User.objects.create_user(
            username="manager_branch_visibility",
            password="PasswordSeguro123!",
        )

        membership = Membership.objects.create(
            tenant=self.tenant,
            usuario=manager,
            rol=Membership.ROLE_MANAGER,
            activo=True,
        )

        MembershipSucursal.objects.create(
            membership=membership,
            sucursal=self.sucursal,
            activo=True,
        )

        self.client.force_login(
            manager
        )

        response = self.client.get(
            reverse(
                "dashboard_admin"
            )
        )

        sucursales = list(
            response.context[
                "sucursales_disponibles"
            ]
        )

        self.assertIn(
            self.sucursal,
            sucursales,
        )

        self.assertNotIn(
            self.sucursal_b,
            sucursales,
        )

    def test_manager_puede_cambiar_a_sucursal_asignada(
        self,
    ):
        manager = User.objects.create_user(
            username="manager_switch",
            password="PasswordSeguro123!",
        )

        membership = Membership.objects.create(
            tenant=self.tenant,
            usuario=manager,
            rol=Membership.ROLE_MANAGER,
            activo=True,
        )

        MembershipSucursal.objects.create(
            membership=membership,
            sucursal=self.sucursal_b,
            activo=True,
        )

        self.client.force_login(
            manager
        )

        response = self.client.post(
            reverse(
                "cambiar_sucursal"
            ),
            {
                "sucursal_public_id":
                    str(
                        self.sucursal_b.public_id
                    ),
            },
        )

        self.assertEqual(
            response.status_code,
            302,
        )

        self.assertEqual(
            self.client.session[
                "sucursal_activa_public_id"
            ],
            str(
                self.sucursal_b.public_id
            ),
        )


    def test_manager_no_puede_forzar_sucursal_no_asignada(
        self,
    ):
        manager = User.objects.create_user(
            username="manager_switch_denied",
            password="PasswordSeguro123!",
        )

        membership = Membership.objects.create(
            tenant=self.tenant,
            usuario=manager,
            rol=Membership.ROLE_MANAGER,
            activo=True,
        )

        MembershipSucursal.objects.create(
            membership=membership,
            sucursal=self.sucursal_b,
            activo=True,
        )

        self.client.force_login(
            manager
        )

        response = self.client.post(
            reverse(
                "cambiar_sucursal"
            ),
            {
                "sucursal_public_id":
                    str(
                        self.sucursal.public_id
                    ),
            },
        )

        self.assertEqual(
            response.status_code,
            403,
        )

        self.assertNotEqual(
            self.client.session.get(
                "sucursal_activa_public_id"
            ),
            str(
                self.sucursal.public_id
            ),
        )


    def test_no_se_puede_cambiar_a_sucursal_de_otro_tenant(
        self,
    ):
        tenant_b = Tenant.objects.create(
            nombre="Tenant B Switch",
            slug="tenant-b-switch",
        )

        sucursal_ajena = Sucursal.objects.create(
            tenant=tenant_b,
            nombre="Sucursal Ajena",
            slug="sucursal-ajena-switch",
            estado=Sucursal.Estado.ACTIVA,
        )

        self.client.force_login(
            self.admin_user
        )

        response = self.client.post(
            reverse(
                "cambiar_sucursal"
            ),
            {
                "sucursal_public_id":
                    str(
                        sucursal_ajena.public_id
                    ),
            },
        )

        self.assertEqual(
            response.status_code,
            403,
        )
        

class EndpointMethodSecurityTests(
    FoodBackTestBase
):

    def test_endpoints_de_lectura_rechazan_post(
        self,
    ):
        token_inexistente = uuid.uuid4()

        urls = [
            reverse(
                "menu"
            ),
            reverse(
                "pedido_exito",
                args=[
                    token_inexistente,
                ],
            ),
            reverse(
                "order_tracker",
                args=[
                    token_inexistente,
                ],
            ),
            reverse(
                "api_order_status",
                args=[
                    token_inexistente,
                ],
            ),
            reverse(
                "api_dashboard_admin_sync"
            ),
            reverse(
                "api_delivery_sync"
            ),
            reverse(
                "dashboard_metrics"
            ),
            reverse(
                "wompi_respuesta"
            ),
            reverse(
                "wompi_suscripcion_respuesta"
            ),
        ]

        for url in urls:

            with self.subTest(
                url=url
            ):
                response = self.client.post(
                    url,
                    {}
                )

                self.assertEqual(
                    response.status_code,
                    405,
                    msg=(
                        f"{url} aceptó POST "
                        "cuando debería ser "
                        "solo lectura."
                    ),
                )


    def test_webhook_wompi_rechaza_get(
        self,
    ):
        response = self.client.get(
            reverse(
                "wompi_webhook"
            )
        )

        self.assertEqual(
            response.status_code,
            405,
        )
        
    def test_checkout_rechaza_put(
        self,
    ):
        response = self.client.put(
            reverse(
                "checkout"
            ),
            data={},
            content_type="application/json",
        )

        self.assertEqual(
            response.status_code,
            405,
        )


    def test_perfil_cliente_rechaza_post(
        self,
    ):
        response = self.client.post(
            reverse(
                "perfil_usuario"
            ),
            {},
        )

        self.assertEqual(
            response.status_code,
            405,
        )


    def test_redirect_wompi_rechaza_head(
        self,
    ):
        response = self.client.head(
            reverse(
                "wompi_respuesta"
            )
        )

        self.assertEqual(
            response.status_code,
            405,
        )


    def test_redirect_suscripcion_wompi_rechaza_head(
        self,
    ):
        response = self.client.head(
            reverse(
                "wompi_suscripcion_respuesta"
            )
        )

        self.assertEqual(
            response.status_code,
            405,
        )
        
        
class ClientIPSecurityTests(
    FoodBackTestBase
):

    def test_no_confia_en_x_forwarded_for(
        self,
    ):
        request = type(
            "Request",
            (),
            {},
        )()

        request.META = {
            "REMOTE_ADDR":
                "192.0.2.10",

            "HTTP_X_FORWARDED_FOR":
                "203.0.113.250",
        }

        self.assertEqual(
            obtener_ip_cliente(
                request
            ),
            "192.0.2.10",
        )


    @override_settings(
        FOODBACK_TRUST_X_REAL_IP=True
    )
    def test_railway_x_real_ip_valida(
        self,
    ):
        request = type(
            "Request",
            (),
            {},
        )()

        request.META = {
            "REMOTE_ADDR":
                "10.0.0.10",

            "HTTP_X_REAL_IP":
                "203.0.113.25",
        }

        self.assertEqual(
            obtener_ip_cliente(
                request
            ),
            "203.0.113.25",
        )


    @override_settings(
        FOODBACK_TRUST_X_REAL_IP=True
    )
    def test_x_real_ip_invalida_cae_a_remote_addr(
        self,
    ):
        request = type(
            "Request",
            (),
            {},
        )()

        request.META = {
            "REMOTE_ADDR":
                "192.0.2.50",

            "HTTP_X_REAL_IP":
                "soy-un-hacker-jajaja",
        }

        self.assertEqual(
            obtener_ip_cliente(
                request
            ),
            "192.0.2.50",
        )
        
        
class DatabaseRateLimitTests(
    FoodBackTestBase
):

    def test_permite_hasta_el_limite(
        self,
    ):
        for _ in range(3):
            resultado = consumir_rate_limit(
                group="test-login",
                raw_key="192.0.2.10:user",
                limite=3,
                ventana_segundos=60,
                bloqueo_segundos=120,
            )

            self.assertTrue(
                resultado[
                    "permitido"
                ]
            )


    def test_bloquea_al_superar_limite(
        self,
    ):
        for _ in range(3):
            consumir_rate_limit(
                group="test-login-bloqueo",
                raw_key="192.0.2.20:user",
                limite=3,
                ventana_segundos=60,
                bloqueo_segundos=120,
            )

        resultado = consumir_rate_limit(
            group="test-login-bloqueo",
            raw_key="192.0.2.20:user",
            limite=3,
            ventana_segundos=60,
            bloqueo_segundos=120,
        )

        self.assertFalse(
            resultado[
                "permitido"
            ]
        )

        self.assertGreater(
            resultado[
                "retry_after"
            ],
            0,
        )


    def test_clave_cruda_no_se_guarda(
        self,
    ):
        from pedidos.models import (
            RateLimitBucket,
        )

        raw_key = (
            "203.0.113.55:"
            "owner@example.com"
        )

        consumir_rate_limit(
            group="test-privacy",
            raw_key=raw_key,
            limite=5,
            ventana_segundos=60,
        )

        bucket = (
            RateLimitBucket.objects
            .get(
                grupo="test-privacy"
            )
        )

        self.assertNotIn(
            raw_key,
            bucket.clave_hash,
        )

        self.assertEqual(
            len(
                bucket.clave_hash
            ),
            64,
        )
        
class LoginRateLimitTests(
    FoodBackTestBase
):

    @override_settings(
        FOODBACK_LOGIN_IP_LIMIT=50,
        FOODBACK_LOGIN_USER_LIMIT=2,
        FOODBACK_LOGIN_WINDOW_SECONDS=600,
        FOODBACK_LOGIN_BLOCK_SECONDS=900,
    )
    def test_bloquea_multiples_intentos_misma_cuenta(
        self,
    ):
        url = reverse(
            "login_custom"
        )

        datos = {
            "username":
                "victima_login",

            "password":
                "incorrecta",
        }

        for _ in range(2):
            response = self.client.post(
                url,
                datos,
                REMOTE_ADDR="192.0.2.10",
            )

            self.assertNotEqual(
                response.status_code,
                429,
            )

        response = self.client.post(
            url,
            datos,
            REMOTE_ADDR="192.0.2.10",
        )

        self.assertEqual(
            response.status_code,
            429,
        )

        self.assertIn(
            "Retry-After",
            response.headers,
        )


    @override_settings(
        FOODBACK_LOGIN_IP_LIMIT=2,
        FOODBACK_LOGIN_USER_LIMIT=50,
        FOODBACK_LOGIN_WINDOW_SECONDS=600,
        FOODBACK_LOGIN_BLOCK_SECONDS=900,
    )
    def test_bloquea_ip_aunque_cambie_username(
        self,
    ):
        url = reverse(
            "login_custom"
        )

        for numero in range(2):
            response = self.client.post(
                url,
                {
                    "username":
                        f"usuario{numero}",

                    "password":
                        "incorrecta",
                },
                REMOTE_ADDR="192.0.2.20",
            )

            self.assertNotEqual(
                response.status_code,
                429,
            )

        response = self.client.post(
            url,
            {
                "username":
                    "otro_usuario",

                "password":
                    "incorrecta",
            },
            REMOTE_ADDR="192.0.2.20",
        )

        self.assertEqual(
            response.status_code,
            429,
        )


    @override_settings(
        FOODBACK_LOGIN_IP_LIMIT=2,
        FOODBACK_LOGIN_USER_LIMIT=50,
        FOODBACK_LOGIN_WINDOW_SECONDS=600,
        FOODBACK_LOGIN_BLOCK_SECONDS=900,
        FOODBACK_TRUST_X_REAL_IP=False,
    )
    def test_x_forwarded_for_no_permite_evadir_limite(
        self,
    ):
        url = reverse(
            "login_custom"
        )

        for numero in range(2):
            response = self.client.post(
                url,
                {
                    "username":
                        f"usuario{numero}",

                    "password":
                        "incorrecta",
                },
                REMOTE_ADDR="192.0.2.30",
                HTTP_X_FORWARDED_FOR=(
                    f"203.0.113.{numero + 1}"
                ),
            )

            self.assertNotEqual(
                response.status_code,
                429,
            )

        response = self.client.post(
            url,
            {
                "username":
                    "tercer_usuario",

                "password":
                    "incorrecta",
            },
            REMOTE_ADDR="192.0.2.30",
            HTTP_X_FORWARDED_FOR=(
                "198.51.100.200"
            ),
        )

        self.assertEqual(
            response.status_code,
            429,
        )
    
    def test_bloqueo_visual_persiste_al_salir_y_volver_al_login(
        self,
    ):
        url = reverse(
            "login_custom"
        )

        with self.settings(
            FOODBACK_LOGIN_IP_LIMIT=50,
            FOODBACK_LOGIN_USER_LIMIT=1,
            FOODBACK_LOGIN_WINDOW_SECONDS=600,
            FOODBACK_LOGIN_BLOCK_SECONDS=900,
        ):
            datos = {
                "username": "ataque-persistente",
                "password": "incorrecta",
            }

            primer_intento = self.client.post(
                url,
                datos,
                REMOTE_ADDR="192.0.2.80",
            )

            self.assertNotEqual(
                primer_intento.status_code,
                429,
            )

            bloqueo = self.client.post(
                url,
                datos,
                REMOTE_ADDR="192.0.2.80",
            )

            self.assertEqual(
                bloqueo.status_code,
                429,
            )

            # Simulamos que el usuario sale al menú.
            self.client.get(
                reverse("menu"),
                REMOTE_ADDR="192.0.2.80",
            )

            # Y posteriormente vuelve al login.
            regreso = self.client.get(
                url,
                REMOTE_ADDR="192.0.2.80",
            )

            self.assertEqual(
                regreso.status_code,
                200,
            )

            self.assertContains(
                regreso,
                "Acceso temporalmente limitado",
            )

            self.assertTrue(
                regreso.context[
                    "rate_limit_retry_after"
                ] > 0
            )


    def test_borrar_estado_visual_no_elimina_bloqueo_real(
        self,
    ):
        url = reverse(
            "login_custom"
        )

        with self.settings(
            FOODBACK_LOGIN_IP_LIMIT=50,
            FOODBACK_LOGIN_USER_LIMIT=1,
            FOODBACK_LOGIN_WINDOW_SECONDS=600,
            FOODBACK_LOGIN_BLOCK_SECONDS=900,
        ):
            datos = {
                "username": "ataque-backend",
                "password": "incorrecta",
            }

            self.client.post(
                url,
                datos,
                REMOTE_ADDR="192.0.2.81",
            )

            bloqueo = self.client.post(
                url,
                datos,
                REMOTE_ADDR="192.0.2.81",
            )

            self.assertEqual(
                bloqueo.status_code,
                429,
            )

            # El atacante manipula su propia sesión
            # e intenta eliminar únicamente la marca UX.
            session = self.client.session

            session.pop(
                "foodback_login_rate_limit_until",
                None,
            )

            session.pop(
                "foodback_login_rate_limit_username",
                None,
            )

            session.save()

            # PostgreSQL sigue teniendo la autoridad.
            intento_manipulado = self.client.post(
                url,
                datos,
                REMOTE_ADDR="192.0.2.81",
            )

            self.assertEqual(
                intento_manipulado.status_code,
                429,
            )

            self.assertIn(
                "Retry-After",
                intento_manipulado.headers,
            )
            
class CheckoutIdempotencyTests(
    FoodBackTestBase
):

    def test_checkout_post_sin_token_es_rechazado(
        self,
    ):
        response = self.client.post(
            reverse(
                "checkout"
            ),
            {
                "nombre": "Cliente",
                "apellido": "Prueba",
                "telefono": "77777777",
                "direccion": "San Miguel",
                "metodo_pago": "EFECTIVO",
                "latitud": "13.48",
                "longitud": "-88.18",
            },
        )

        self.assertEqual(
            response.status_code,
            400,
        )


    def test_checkout_token_manipulado_es_rechazado(
        self,
    ):
        # GET crea el token legítimo.
        self.client.get(
            reverse(
                "checkout"
            )
        )

        response = self.client.post(
            reverse(
                "checkout"
            ),
            {
                "checkout_token":
                    str(
                        uuid.uuid4()
                    ),

                "nombre":
                    "Cliente",

                "apellido":
                    "Prueba",

                "telefono":
                    "77777777",

                "direccion":
                    "San Miguel",

                "metodo_pago":
                    "EFECTIVO",

                "latitud":
                    "13.48",

                "longitud":
                    "-88.18",
            },
        )

        self.assertEqual(
            response.status_code,
            400,
        )
        
    def test_checkout_repetido_recupera_mismo_pedido(
        self,
    ):
        checkout_token = (
            self._crear_checkout_token_test()
        )

        pedido_existente = self.crear_pedido(
            telefono="79770002",
        )

        pedido_existente.checkout_token = (
            checkout_token
        )

        pedido_existente.save(
            update_fields=[
                "checkout_token"
            ]
        )

        session = self.client.session

        session["cart"] = {
            f"{self.producto.id}-0-0": 1
        }

        session.save()

        cantidad_antes = (
            Pedido.objects.count()
        )

        response = self.client.post(
            reverse(
                "checkout"
            ),
            {
                "checkout_token":
                    checkout_token,

                "nombre":
                    "Cliente",

                "apellido":
                    "Repetido",

                "telefono":
                    "79770002",

                "direccion":
                    "San Miguel",

                "metodo_pago":
                    "EFECTIVO",

                "latitud":
                    "13.4800",

                "longitud":
                    "-88.1800",
            },
        )

        self.assertEqual(
            response.status_code,
            302,
        )

        self.assertEqual(
            Pedido.objects.count(),
            cantidad_antes,
        )

        self.assertEqual(
            Pedido.objects.filter(
                checkout_token=checkout_token,
            ).count(),
            1,
        )

        self.assertEqual(
            response["Location"],
            reverse(
                "order_tracker",
                args=[
                    pedido_existente.tracking_token
                ],
            ),
        )
        
    #--
    def test_checkout_legitimo_guarda_y_consume_token(
        self,
    ):
        checkout_token = (
            self._crear_checkout_token_test()
        )

        session = self.client.session

        session["cart"] = {
            f"{self.producto.id}-0-0": 1
        }

        session.save()

        response = self.client.post(
            reverse(
                "checkout"
            ),
            {
                "checkout_token":
                    checkout_token,

                "nombre":
                    "Cliente",

                "apellido":
                    "Idempotencia",

                "telefono":
                    "79770003",

                "direccion":
                    "San Miguel",

                "metodo_pago":
                    "EFECTIVO",

                "latitud":
                    "13.4800",

                "longitud":
                    "-88.1800",
            },
        )

        self.assertEqual(
            response.status_code,
            302,
        )

        pedido = Pedido.objects.get(
            checkout_token=checkout_token,
        )

        self.assertEqual(
            str(
                pedido.checkout_token
            ),
            checkout_token,
        )

        session = self.client.session

        self.assertNotIn(
            "foodback_checkout_token",
            session,
        )

        self.assertEqual(
            session.get(
                "cart"
            ),
            {},
        )

        self.assertEqual(
            session.get(
                "ultimo_pedido_id"
            ),
            pedido.id,
        )

        self.assertIn(
            pedido.id,
            session.get(
                "historial_pedidos",
                [],
            ),
        )
        
        
class CheckoutRateLimitTests(
    FoodBackTestBase
):

    @override_settings(
        FOODBACK_CHECKOUT_SESSION_LIMIT=2,
        FOODBACK_CHECKOUT_IP_LIMIT=50,
        FOODBACK_CHECKOUT_WINDOW_SECONDS=600,
        FOODBACK_CHECKOUT_BLOCK_SECONDS=900,
    )
    def test_checkout_bloquea_flood_del_mismo_navegador(
        self,
    ):
        url = reverse(
            "checkout"
        )

        # No necesitamos un checkout válido.
        # El rate limit ocurre antes de procesar
        # los datos comerciales.
        for _ in range(2):
            response = self.client.post(
                url,
                {},
                REMOTE_ADDR="192.0.2.100",
            )

            self.assertNotEqual(
                response.status_code,
                429,
            )

        response = self.client.post(
            url,
            {},
            REMOTE_ADDR="192.0.2.100",
        )

        self.assertEqual(
            response.status_code,
            429,
        )

        self.assertIn(
            "Retry-After",
            response.headers,
        )


    @override_settings(
        FOODBACK_CHECKOUT_SESSION_LIMIT=50,
        FOODBACK_CHECKOUT_IP_LIMIT=2,
        FOODBACK_CHECKOUT_WINDOW_SECONDS=600,
        FOODBACK_CHECKOUT_BLOCK_SECONDS=900,
    )
    def test_checkout_bloquea_flood_por_ip(
        self,
    ):
        url = reverse(
            "checkout"
        )

        for _ in range(2):
            response = self.client.post(
                url,
                {},
                REMOTE_ADDR="192.0.2.110",
            )

            self.assertNotEqual(
                response.status_code,
                429,
            )

        response = self.client.post(
            url,
            {},
            REMOTE_ADDR="192.0.2.110",
        )

        self.assertEqual(
            response.status_code,
            429,
        )


    @override_settings(
        FOODBACK_CHECKOUT_SESSION_LIMIT=50,
        FOODBACK_CHECKOUT_IP_LIMIT=2,
        FOODBACK_CHECKOUT_WINDOW_SECONDS=600,
        FOODBACK_CHECKOUT_BLOCK_SECONDS=900,
        FOODBACK_TRUST_X_REAL_IP=False,
    )
    def test_checkout_no_confia_en_x_forwarded_for(
        self,
    ):
        url = reverse(
            "checkout"
        )

        for numero in range(2):
            self.client.post(
                url,
                {},
                REMOTE_ADDR="192.0.2.120",
                HTTP_X_FORWARDED_FOR=(
                    f"203.0.113.{numero + 1}"
                ),
            )

        response = self.client.post(
            url,
            {},
            REMOTE_ADDR="192.0.2.120",
            HTTP_X_FORWARDED_FOR=(
                "198.51.100.250"
            ),
        )

        self.assertEqual(
            response.status_code,
            429,
        )
        
        
class WompiStartRateLimitTests(
    FoodBackTestBase
):

    def _crear_pedido_tarjeta_pendiente(
        self,
        telefono,
    ):
        pedido = self.crear_pedido(
            estado="PENDIENTE",
            telefono=telefono,
        )

        pedido.metodo_pago = "TARJETA"

        pedido.save(
            update_fields=[
                "metodo_pago"
            ]
        )

        return pedido


    @override_settings(
        FOODBACK_WOMPI_START_SESSION_LIMIT=2,
        FOODBACK_WOMPI_START_IP_LIMIT=50,
        FOODBACK_WOMPI_START_WINDOW_SECONDS=600,
        FOODBACK_WOMPI_START_BLOCK_SECONDS=900,
    )
    @patch(
        "pedidos.views."
        "_iniciar_pago_wompi_pedido"
    )
    def test_pagar_wompi_bloquea_flood_del_mismo_navegador(
        self,
        mock_iniciar_pago,
    ):
        mock_iniciar_pago.return_value = (
            HttpResponse(
                "ok",
                status=200,
            )
        )

        pedido = (
            self._crear_pedido_tarjeta_pendiente(
                "79770101"
            )
        )

        url = reverse(
            "pagar_wompi",
            args=[
                pedido.tracking_token
            ],
        )

        for _ in range(2):
            response = self.client.post(
                url,
                REMOTE_ADDR="192.0.2.201",
            )

            self.assertEqual(
                response.status_code,
                200,
            )

        response = self.client.post(
            url,
            REMOTE_ADDR="192.0.2.201",
        )

        self.assertEqual(
            response.status_code,
            429,
        )

        self.assertIn(
            "Retry-After",
            response.headers,
        )

        self.assertEqual(
            mock_iniciar_pago.call_count,
            2,
        )


    @override_settings(
        FOODBACK_WOMPI_START_SESSION_LIMIT=50,
        FOODBACK_WOMPI_START_IP_LIMIT=2,
        FOODBACK_WOMPI_START_WINDOW_SECONDS=600,
        FOODBACK_WOMPI_START_BLOCK_SECONDS=900,
    )
    @patch(
        "pedidos.views."
        "_iniciar_pago_wompi_pedido"
    )
    def test_pagar_wompi_bloquea_flood_por_ip(
        self,
        mock_iniciar_pago,
    ):
        mock_iniciar_pago.return_value = (
            HttpResponse(
                "ok",
                status=200,
            )
        )

        pedido = (
            self._crear_pedido_tarjeta_pendiente(
                "79770102"
            )
        )

        url = reverse(
            "pagar_wompi",
            args=[
                pedido.tracking_token
            ],
        )

        for _ in range(2):
            response = self.client.post(
                url,
                REMOTE_ADDR="192.0.2.202",
            )

            self.assertEqual(
                response.status_code,
                200,
            )

        response = self.client.post(
            url,
            REMOTE_ADDR="192.0.2.202",
        )

        self.assertEqual(
            response.status_code,
            429,
        )

        self.assertEqual(
            mock_iniciar_pago.call_count,
            2,
        )
        

class SubscriptionPaymentRateLimitTests(
    FoodBackTestBase
):

    @override_settings(
        FOODBACK_SUBSCRIPTION_PAYMENT_USER_LIMIT=2,
        FOODBACK_SUBSCRIPTION_PAYMENT_IP_LIMIT=50,
        FOODBACK_SUBSCRIPTION_PAYMENT_WINDOW_SECONDS=600,
        FOODBACK_SUBSCRIPTION_PAYMENT_BLOCK_SECONDS=900,
    )
    @patch(
        "pedidos.views."
        "_wompi_crear_enlace_pago"
    )
    def test_pago_suscripcion_bloquea_flood_del_owner(
        self,
        mock_wompi,
    ):
        mock_wompi.return_value = (
            {
                "urlEnlace":
                    "https://wompi.test/subscription",

                "idEnlace":
                    "SUBS-RATE-LIMIT",
            },
            {
                "mock": True,
            },
        )

        self.client.force_login(
            self.admin_user
        )

        url = reverse(
            "pagar_suscripcion"
        )

        for _ in range(2):
            response = self.client.post(
                url,
                REMOTE_ADDR="192.0.2.230",
            )

            self.assertEqual(
                response.status_code,
                302,
            )

        response = self.client.post(
            url,
            REMOTE_ADDR="192.0.2.230",
        )

        self.assertEqual(
            response.status_code,
            429,
        )

        self.assertIn(
            "Retry-After",
            response.headers,
        )

        # Gracias a la idempotencia existente,
        # Wompi solo necesitó crear un enlace.
        self.assertEqual(
            mock_wompi.call_count,
            1,
        )


    @override_settings(
        FOODBACK_SUBSCRIPTION_PAYMENT_USER_LIMIT=50,
        FOODBACK_SUBSCRIPTION_PAYMENT_IP_LIMIT=2,
        FOODBACK_SUBSCRIPTION_PAYMENT_WINDOW_SECONDS=600,
        FOODBACK_SUBSCRIPTION_PAYMENT_BLOCK_SECONDS=900,
    )
    @patch(
        "pedidos.views."
        "_wompi_crear_enlace_pago"
    )
    def test_pago_suscripcion_bloquea_flood_por_ip(
        self,
        mock_wompi,
    ):
        mock_wompi.return_value = (
            {
                "urlEnlace":
                    "https://wompi.test/subscription-ip",

                "idEnlace":
                    "SUBS-RATE-LIMIT-IP",
            },
            {
                "mock": True,
            },
        )

        self.client.force_login(
            self.admin_user
        )

        url = reverse(
            "pagar_suscripcion"
        )

        for _ in range(2):
            response = self.client.post(
                url,
                REMOTE_ADDR="192.0.2.231",
            )

            self.assertEqual(
                response.status_code,
                302,
            )

        response = self.client.post(
            url,
            REMOTE_ADDR="192.0.2.231",
        )

        self.assertEqual(
            response.status_code,
            429,
        )

        self.assertIn(
            "Retry-After",
            response.headers,
        )
        
class GeoIpRetirementSecurityTests(
    FoodBackTestBase
):

    def test_endpoint_geo_ip_ya_no_esta_expuesto(
        self,
    ):
        response = self.client.get(
            "/api/geo-ip/"
        )

        self.assertEqual(
            response.status_code,
            404,
        )
        
        
class AdminSettingsValidationTests(
    FoodBackTestBase
):

    def test_tipo_accion_inventado_es_rechazado(
        self,
    ):
        self.client.force_login(
            self.admin_user
        )

        self.config.refresh_from_db()

        apertura_original = (
            self.config.hora_apertura
        )

        cierre_original = (
            self.config.hora_cierre
        )

        response = self.client.post(
            reverse(
                "admin_settings"
            ),
            {
                "tipo_accion":
                    "accion-inventada",

                "hora_apertura":
                    "01:00",

                "hora_cierre":
                    "02:00",
            },
        )

        self.assertEqual(
            response.status_code,
            400,
        )

        self.config.refresh_from_db()

        self.assertEqual(
            self.config.hora_apertura,
            apertura_original,
        )

        self.assertEqual(
            self.config.hora_cierre,
            cierre_original,
        )

    def test_horario_global_invalido_no_modifica_configuracion(
        self,
    ):
        self.client.force_login(
            self.admin_user
        )

        self.config.refresh_from_db()

        apertura_original = (
            self.config.hora_apertura
        )

        cierre_original = (
            self.config.hora_cierre
        )

        response = self.client.post(
            reverse(
                "admin_settings"
            ),
            {
                "tipo_accion":
                    "global",

                "hora_apertura":
                    "esto-no-es-hora",

                "hora_cierre":
                    "22:00",

                "mensaje_cierre":
                    "Mensaje válido",
            },
        )

        self.assertEqual(
            response.status_code,
            302,
        )

        self.config.refresh_from_db()

        self.assertEqual(
            self.config.hora_apertura,
            apertura_original,
        )

        self.assertEqual(
            self.config.hora_cierre,
            cierre_original,
        )


    def test_horario_especial_invalido_no_crea_excepcion(
        self,
    ):
        self.client.force_login(
            self.admin_user
        )

        fecha_objetivo = (
            date.today()
            + timedelta(days=3)
        )

        response = self.client.post(
            reverse(
                "admin_settings"
            ),
            {
                "tipo_accion":
                    "dia_especifico",

                "fecha_target":
                    fecha_objetivo.isoformat(),

                "estado_dia":
                    "on",

                "hora_apertura_dia":
                    "hora-falsa",

                "hora_cierre_dia":
                    "18:00",

                "motivo":
                    "Horario especial",
            },
        )

        self.assertEqual(
            response.status_code,
            302,
        )

        self.assertFalse(
            DiaEspecial.objects.filter(
                sucursal=self.sucursal,
                fecha=fecha_objetivo,
            ).exists()
        )


    def test_motivo_demasiado_largo_no_crea_excepcion(
        self,
    ):
        self.client.force_login(
            self.admin_user
        )

        fecha_objetivo = (
            date.today()
            + timedelta(days=4)
        )

        response = self.client.post(
            reverse(
                "admin_settings"
            ),
            {
                "tipo_accion":
                    "dia_especifico",

                "fecha_target":
                    fecha_objetivo.isoformat(),

                "estado_dia":
                    "on",

                "hora_apertura_dia":
                    "08:00",

                "hora_cierre_dia":
                    "18:00",

                "motivo":
                    "X" * 101,
            },
        )

        self.assertEqual(
            response.status_code,
            302,
        )

        self.assertFalse(
            DiaEspecial.objects.filter(
                sucursal=self.sucursal,
                fecha=fecha_objetivo,
            ).exists()
        )
        
        
class PostgreSQLConcurrencyTests(
    TransactionTestCase
):
    """
    Pruebas reales de concurrencia PostgreSQL.

    TransactionTestCase es obligatorio porque necesitamos
    transacciones independientes y conexiones distintas.
    """

    def setUp(self):
        super().setUp()

        if connection.vendor != "postgresql":
            self.skipTest(
                "Estas pruebas requieren PostgreSQL."
            )

        self.tenant = Tenant.objects.create(
            nombre="Concurrency Tenant",
            slug="concurrency-tenant",
            habilitado=True,
        )

        fecha_inicial = (
            date.today()
            + timedelta(days=10)
        )

        self.fecha_inicial = fecha_inicial

        with tenant_database_context(
            tenant=self.tenant
        ):
            self.suscripcion = (
                SuscripcionTenant.objects.create(
                    tenant=self.tenant,
                    estado=(
                        SuscripcionTenant
                        .Estado
                        .ACTIVA
                    ),
                    fecha_vencimiento=(
                        fecha_inicial
                    ),
                )
            )

            self.pago = (
                PagoWompi.objects.create(
                    tipo="SUSCRIPCION",
                    tenant=self.tenant,
                    referencia=(
                        "SUBS-CONCURRENCY-TEST"
                    ),
                    monto=Decimal("50.00"),
                    estado="PENDIENTE",
                )
            )

    def test_dos_aprobaciones_simultaneas_renuevan_solo_una_vez(
        self,
    ):
        barrera = Barrier(2)

        def aprobar_pago():
            # Cada hilo debe obtener su propia conexión
            # PostgreSQL.
            close_old_connections()

            try:
                with tenant_database_context(
                    tenant=self.tenant
                ):
                    # Ambos hilos llegan hasta aquí antes
                    # de intentar bloquear PagoWompi.
                    barrera.wait(
                        timeout=10
                    )

                    return (
                        _procesar_pago_wompi_aprobado(
                            self.pago.referencia,
                            id_transaccion=(
                                "TX-CONCURRENCY-001"
                            ),
                            monto="50.00",
                            raw_payload={
                                "test":
                                    "concurrency",
                            },
                            origen="WEBHOOK",
                        )
                    )

            finally:
                close_old_connections()

        with ThreadPoolExecutor(
            max_workers=2
        ) as executor:
            futuros = [
                executor.submit(
                    aprobar_pago
                )
                for _ in range(2)
            ]

            resultados = [
                futuro.result(
                    timeout=20
                )
                for futuro in futuros
            ]

        self.pago.refresh_from_db()
        self.suscripcion.refresh_from_db()

        # Las dos llamadas son legítimas:
        #
        # una aprueba;
        # la otra descubre el replay después
        # de esperar el row lock.
        self.assertTrue(
            all(
                resultado[0]
                for resultado in resultados
            )
        )

        self.assertEqual(
            self.pago.estado,
            "APROBADO",
        )

        self.assertTrue(
            self.pago.es_aprobada
        )

        self.assertEqual(
            self.pago.id_transaccion,
            "TX-CONCURRENCY-001",
        )

        # CRÍTICO:
        # dos webhooks simultáneos NO deben sumar
        # 60 días.
        self.assertEqual(
            self.suscripcion.fecha_vencimiento,
            (
                self.fecha_inicial
                + timedelta(days=30)
            ),
        )
        
    def test_misma_transaccion_concurrente_no_aprueba_dos_pagos(
        self,
    ):
        with tenant_database_context(
            tenant=self.tenant
        ):
            sucursal = Sucursal.objects.create(
                tenant=self.tenant,
                nombre="Sucursal Concurrency",
                slug="sucursal-concurrency",
                estado=Sucursal.Estado.ACTIVA,
            )

            cliente_1 = Cliente.objects.create(
                tenant=self.tenant,
                telefono="70000001",
                nombre="Cliente",
                apellido="Uno",
            )

            cliente_2 = Cliente.objects.create(
                tenant=self.tenant,
                telefono="70000002",
                nombre="Cliente",
                apellido="Dos",
            )

            pedido_1 = Pedido.objects.create(
                sucursal=sucursal,
                cliente=cliente_1,
                direccion_entrega=(
                    "Direccion concurrency 1"
                ),
                metodo_pago="TARJETA",
                estado="PENDIENTE",
                total_final=Decimal("50.00"),
            )

            pedido_2 = Pedido.objects.create(
                sucursal=sucursal,
                cliente=cliente_2,
                direccion_entrega=(
                    "Direccion concurrency 2"
                ),
                metodo_pago="TARJETA",
                estado="PENDIENTE",
                total_final=Decimal("50.00"),
            )

            pago_1 = PagoWompi.objects.create(
                tipo="PEDIDO",
                tenant=self.tenant,
                pedido=pedido_1,
                referencia=(
                    "ORDEN-CONCURRENCY-1"
                ),
                monto=Decimal("50.00"),
                estado="PENDIENTE",
            )

            pago_2 = PagoWompi.objects.create(
                tipo="PEDIDO",
                tenant=self.tenant,
                pedido=pedido_2,
                referencia=(
                    "ORDEN-CONCURRENCY-2"
                ),
                monto=Decimal("50.00"),
                estado="PENDIENTE",
            )

        barrera = Barrier(2)

        def aprobar_pago(
            referencia,
        ):
            close_old_connections()

            try:
                with tenant_database_context(
                    tenant=self.tenant
                ):
                    barrera.wait(
                        timeout=10
                    )

                    try:
                        resultado = (
                            _procesar_pago_wompi_aprobado(
                                referencia,
                                id_transaccion=(
                                    "TX-COMPARTIDA-001"
                                ),
                                monto="50.00",
                                raw_payload={
                                    "test":
                                        "concurrency-shared-tx",
                                },
                                origen="WEBHOOK",
                            )
                        )

                        return (
                            "resultado",
                            resultado,
                        )

                    except IntegrityError:
                        # Esta también es una defensa válida:
                        # id_transaccion tiene UNIQUE en
                        # PostgreSQL.
                        return (
                            "integrity_error",
                            None,
                        )

            finally:
                close_old_connections()

        with ThreadPoolExecutor(
            max_workers=2
        ) as executor:
            futuros = [
                executor.submit(
                    aprobar_pago,
                    pago_1.referencia,
                ),
                executor.submit(
                    aprobar_pago,
                    pago_2.referencia,
                ),
            ]

            resultados = [
                futuro.result(
                    timeout=20
                )
                for futuro in futuros
            ]

        with tenant_database_context(
            tenant=self.tenant
        ):
            pago_1.refresh_from_db()
            pago_2.refresh_from_db()

            pedido_1.refresh_from_db()
            pedido_2.refresh_from_db()

        pagos_aprobados = [
            pago
            for pago in (
                pago_1,
                pago_2,
            )
            if pago.estado == "APROBADO"
        ]

        self.assertEqual(
            len(pagos_aprobados),
            1,
        )

        self.assertEqual(
            pagos_aprobados[0].id_transaccion,
            "TX-COMPARTIDA-001",
        )

        pedidos_pagados = [
            pedido
            for pedido in (
                pedido_1,
                pedido_2,
            )
            if pedido.pago_verificado
        ]

        self.assertEqual(
            len(pedidos_pagados),
            1,
        )

        # La base de datos jamás debe terminar con
        # dos pagos usando la misma transacción Wompi.
        with tenant_database_context(
            tenant=self.tenant
        ):
            cantidad = (
                PagoWompi.objects
                .filter(
                    id_transaccion=(
                        "TX-COMPARTIDA-001"
                    )
                )
                .count()
            )

        self.assertEqual(
            cantidad,
            1,
        )

        # Ambas ejecuciones terminaron de manera
        # controlada: una puede haber sido rechazada
        # por nuestra lógica, o por la constraint UNIQUE
        # si la carrera fue extremadamente cerrada.
        self.assertEqual(
            len(resultados),
            2,
        )
        
    def test_checkout_token_concurrente_crea_un_solo_pedido(
        self,
    ):
        with tenant_database_context(
            tenant=self.tenant
        ):
            sucursal = Sucursal.objects.create(
                tenant=self.tenant,
                nombre="Sucursal Checkout Concurrente",
                slug="checkout-concurrente",
                estado=Sucursal.Estado.ACTIVA,
            )

            cliente = Cliente.objects.create(
                tenant=self.tenant,
                telefono="70000999",
                nombre="Cliente",
                apellido="Concurrente",
            )

        checkout_token = uuid.uuid4()

        barrera = Barrier(2)

        def crear_pedido():
            close_old_connections()

            try:
                with tenant_database_context(
                    tenant=self.tenant
                ):
                    barrera.wait(
                        timeout=10
                    )

                    try:
                        with transaction.atomic():
                            pedido = (
                                Pedido.objects.create(
                                    sucursal_id=sucursal.id,
                                    cliente_id=cliente.id,
                                    direccion_entrega=(
                                        "Direccion concurrente"
                                    ),
                                    metodo_pago="EFECTIVO",
                                    estado="RECIBIDO",
                                    checkout_token=(
                                        checkout_token
                                    ),
                                )
                            )

                        return (
                            "creado",
                            pedido.id,
                        )

                    except IntegrityError:
                        return (
                            "duplicado",
                            None,
                        )

            finally:
                close_old_connections()

        with ThreadPoolExecutor(
            max_workers=2
        ) as executor:
            futuros = [
                executor.submit(
                    crear_pedido
                )
                for _ in range(2)
            ]

            resultados = [
                futuro.result(
                    timeout=20
                )
                for futuro in futuros
            ]

        with tenant_database_context(
            tenant=self.tenant
        ):
            pedidos = list(
                Pedido.objects.filter(
                    checkout_token=checkout_token
                )
            )

        # PostgreSQL permitió exactamente uno.
        self.assertEqual(
            len(pedidos),
            1,
        )

        resultados_creados = [
            resultado
            for resultado in resultados
            if resultado[0] == "creado"
        ]

        resultados_duplicados = [
            resultado
            for resultado in resultados
            if resultado[0] == "duplicado"
        ]

        self.assertEqual(
            len(resultados_creados),
            1,
        )

        self.assertEqual(
            len(resultados_duplicados),
            1,
        )

        self.assertEqual(
            resultados_creados[0][1],
            pedidos[0].id,
        )
        
        
class SessionSecurityTests(
    FoodBackTestBase
):
    """
    Seguridad de sesiones autenticadas.

    Verifica que una sesión conocida antes del login
    no pueda reutilizarse después de autenticarse
    y que logout invalide la sesión autenticada.
    """

    PASSWORD = "PasswordSeguro123!"

    def _crear_owner(
        self,
        username,
    ):
        user = User.objects.create_user(
            username=username,
            password=self.PASSWORD,
        )

        Membership.objects.create(
            tenant=self.tenant,
            usuario=user,
            rol=Membership.ROLE_OWNER,
            activo=True,
        )

        return user
    
    def _crear_manager(
        self,
        username,
    ):
        user = User.objects.create_user(
            username=username,
            password=self.PASSWORD,
        )

        membership = Membership.objects.create(
            tenant=self.tenant,
            usuario=user,
            rol=Membership.ROLE_MANAGER,
            activo=True,
        )

        MembershipSucursal.objects.create(
            membership=membership,
            sucursal=self.sucursal,
            activo=True,
        )

        return user, membership

    def test_login_rota_session_key(
        self,
    ):
        """
        Protección contra session fixation.

        El identificador conocido antes del login
        debe dejar de ser el identificador usado
        por la sesión autenticada.
        """

        owner = self._crear_owner(
            "session_fixation_owner"
        )

        session = self.client.session

        session[
            "prelogin_marker"
        ] = "valor-anonimo"

        session.save()

        session_key_antes = (
            session.session_key
        )

        self.assertIsNotNone(
            session_key_antes
        )

        response = self.client.post(
            reverse("login_custom"),
            {
                "username":
                    owner.username,

                "password":
                    self.PASSWORD,
            },
        )

        self.assertEqual(
            response.status_code,
            302,
        )

        session_key_despues = (
            self.client.session.session_key
        )

        self.assertIsNotNone(
            session_key_despues
        )

        self.assertNotEqual(
            session_key_antes,
            session_key_despues,
        )

        self.assertEqual(
            self.client.session.get(
                "_auth_user_id"
            ),
            str(owner.id),
        )

        # Incluso si alguien conocía la cookie
        # anterior, esa sesión no debe darle
        # acceso al dashboard autenticado.
        atacante = Client()

        atacante.cookies[
            settings.SESSION_COOKIE_NAME
        ] = session_key_antes

        response_atacante = (
            atacante.get(
                reverse(
                    "dashboard_admin"
                )
            )
        )

        self.assertEqual(
            response_atacante.status_code,
            302,
        )

        self.assertTrue(
            response_atacante[
                "Location"
            ].startswith(
                reverse(
                    "login_custom"
                )
            )
        )

    def test_logout_invalida_session_autenticada(
        self,
    ):
        """
        La cookie conocida antes del logout
        no debe continuar autenticando después.
        """

        owner = self._crear_owner(
            "logout_session_owner"
        )

        response_login = (
            self.client.post(
                reverse(
                    "login_custom"
                ),
                {
                    "username":
                        owner.username,

                    "password":
                        self.PASSWORD,
                },
            )
        )

        self.assertEqual(
            response_login.status_code,
            302,
        )

        session_key_autenticada = (
            self.client.session.session_key
        )

        self.assertIsNotNone(
            session_key_autenticada
        )

        response_logout = (
            self.client.post(
                reverse("logout")
            )
        )

        self.assertEqual(
            response_logout.status_code,
            302,
        )

        self.assertIsNone(
            self.client.session.get(
                "_auth_user_id"
            )
        )

        # Simulamos que alguien intenta reutilizar
        # exactamente la cookie de sesión anterior.
        atacante = Client()

        atacante.cookies[
            settings.SESSION_COOKIE_NAME
        ] = session_key_autenticada

        response_atacante = (
            atacante.get(
                reverse(
                    "dashboard_admin"
                )
            )
        )

        self.assertEqual(
            response_atacante.status_code,
            302,
        )

        self.assertTrue(
            response_atacante[
                "Location"
            ].startswith(
                reverse(
                    "login_custom"
                )
            )
        )
        
    def test_login_elimina_estado_anonimo_previo(
        self,
    ):
        """
        Un login administrativo exitoso establece
        una frontera entre la sesión pública de cliente
        y la sesión autenticada del personal.

        Estado de carrito, pedidos y pagos anónimos
        no debe cruzar esa frontera.
        """

        owner = self._crear_owner(
            "session_boundary_owner"
        )

        session = self.client.session

        estado_anonimo = {
            "cart": {
                "producto-demo": 1,
            },
            "foodback_checkout_token":
                "11111111-1111-1111-1111-111111111111",
            "ultimo_pedido_id": 999,
            "historial_pedidos": [
                998,
                999,
            ],
            "pedidos_pendientes_ocultos": [
                997,
            ],
            "wompi_cliente_token":
                "token-anonimo-prueba",
            "sucursal_activa_public_id":
                "22222222-2222-2222-2222-222222222222",
        }

        for clave, valor in (
            estado_anonimo.items()
        ):
            session[
                clave
            ] = valor

        session.save()

        response = self.client.post(
            reverse("login_custom"),
            {
                "username":
                    owner.username,

                "password":
                    self.PASSWORD,
            },
        )

        self.assertEqual(
            response.status_code,
            302,
        )

        session_autenticada = (
            self.client.session
        )

        self.assertEqual(
            session_autenticada.get(
                "_auth_user_id"
            ),
            str(owner.id),
        )

        for clave in estado_anonimo:
            with self.subTest(
                clave=clave
            ):
                self.assertNotIn(
                    clave,
                    session_autenticada,
                )
                
                
    def test_session_autenticada_tiene_expiracion_absoluta(
        self,
    ):
        owner = self._crear_owner(
            "session_expiry_owner"
        )

        response = self.client.post(
            reverse("login_custom"),
            {
                "username":
                    owner.username,

                "password":
                    self.PASSWORD,
            },
        )

        self.assertEqual(
            response.status_code,
            302,
        )

        session = self.client.session

        expiry_marker_antes = (
            session.get(
                "_session_expiry"
            )
        )

        self.assertIsNotNone(
            expiry_marker_antes
        )

        expiry_antes = (
            session.get_expiry_date()
        )

        segundos_restantes = (
            expiry_antes
            - timezone.now()
        ).total_seconds()

        self.assertGreater(
            segundos_restantes,
            (
                settings
                .FOODBACK_STAFF_SESSION_MAX_AGE
                - 10
            ),
        )

        self.assertLessEqual(
            segundos_restantes,
            settings
            .FOODBACK_STAFF_SESSION_MAX_AGE,
        )

        # Una modificación posterior de la sesión
        # NO debe renovar el turno por otras 15 horas.
        session[
            "prueba_modificacion"
        ] = True

        session.save()

        session_despues = (
            self.client.session
        )

        expiry_marker_despues = (
            session_despues.get(
                "_session_expiry"
            )
        )

        expiry_despues = (
            session_despues
            .get_expiry_date()
        )

        self.assertEqual(
            expiry_marker_despues,
            expiry_marker_antes,
        )

        self.assertEqual(
            expiry_despues,
            expiry_antes,
        )


    def test_desactivar_membership_bloquea_sesion_existente(
        self,
    ):
        manager, membership = (
            self._crear_manager(
                "manager_revocado"
            )
        )

        response_login = self.client.post(
            reverse("login_custom"),
            {
                "username":
                    manager.username,

                "password":
                    self.PASSWORD,
            },
        )

        self.assertEqual(
            response_login.status_code,
            302,
        )

        response_antes = self.client.get(
            reverse("dashboard_admin")
        )

        self.assertEqual(
            response_antes.status_code,
            200,
        )

        membership.activo = False
        membership.save(
            update_fields=[
                "activo",
            ]
        )

        response_despues = self.client.get(
            reverse("dashboard_admin")
        )

        self.assertEqual(
            response_despues.status_code,
            403,
        )


    def test_desactivar_usuario_bloquea_sesion_existente(
        self,
    ):
        owner = self._crear_owner(
            "owner_desactivado"
        )

        response_login = self.client.post(
            reverse("login_custom"),
            {
                "username":
                    owner.username,

                "password":
                    self.PASSWORD,
            },
        )

        self.assertEqual(
            response_login.status_code,
            302,
        )

        response_antes = self.client.get(
            reverse("dashboard_admin")
        )

        self.assertEqual(
            response_antes.status_code,
            200,
        )

        owner.is_active = False
        owner.save(
            update_fields=[
                "is_active",
            ]
        )

        response_despues = self.client.get(
            reverse("dashboard_admin")
        )

        self.assertEqual(
            response_despues.status_code,
            302,
        )

        self.assertTrue(
            response_despues[
                "Location"
            ].startswith(
                reverse(
                    "login_custom"
                )
            )
        )
        
        
class PasswordResetChallengeTests(TestCase):

    def setUp(self):
        self.user = User.objects.create_user(
            username="password_reset_user",
            password="PasswordSeguro123!",
        )

        self.identity = (
            StaffIdentity.objects.create(
                user=self.user,
                email="reset@foodback.test",
                email_verified=True,
            )
        )

    def test_codigo_generado_no_se_guarda_en_texto_plano(
        self,
    ):
        challenge, codigo = (
            crear_password_reset_challenge(
                self.identity
            )
        )

        challenge.refresh_from_db()

        self.assertEqual(
            len(codigo),
            6,
        )

        self.assertTrue(
            codigo.isdigit()
        )

        self.assertNotEqual(
            challenge.codigo_hash,
            codigo,
        )

        self.assertNotIn(
            codigo,
            challenge.codigo_hash,
        )

        self.assertTrue(
            password_reset_codigo_coincide(
                challenge,
                codigo,
            )
        )

    def test_codigo_incorrecto_no_valida(
        self,
    ):
        challenge, codigo = (
            crear_password_reset_challenge(
                self.identity
            )
        )

        codigo_incorrecto = (
            "000000"
            if codigo != "000000"
            else "999999"
        )

        self.assertFalse(
            password_reset_codigo_coincide(
                challenge,
                codigo_incorrecto,
            )
        )

        self.assertEqual(
            challenge.intentos,
            0,
        )

        self.assertIsNone(
            challenge.usado_en
        )
        
    
    @override_settings(
    FOODBACK_PASSWORD_RESET_MAX_ATTEMPTS=3,
    )
    def test_codigo_incorrecto_incrementa_intentos_y_bloquea(
        self,
    ):
        challenge, codigo = (
            crear_password_reset_challenge(
                self.identity
            )
        )

        incorrecto = (
            "000000"
            if codigo != "000000"
            else "999999"
        )

        primer_resultado = (
            consumir_password_reset_challenge(
                public_id=challenge.public_id,
                codigo=incorrecto,
            )
        )

        self.assertEqual(
            primer_resultado["estado"],
            "INVALIDO",
        )

        segundo_resultado = (
            consumir_password_reset_challenge(
                public_id=challenge.public_id,
                codigo=incorrecto,
            )
        )

        self.assertEqual(
            segundo_resultado["estado"],
            "INVALIDO",
        )

        tercer_resultado = (
            consumir_password_reset_challenge(
                public_id=challenge.public_id,
                codigo=incorrecto,
            )
        )

        self.assertEqual(
            tercer_resultado["estado"],
            "BLOQUEADO",
        )

        challenge.refresh_from_db()

        self.assertEqual(
            challenge.intentos,
            3,
        )

        # Incluso el código correcto ya no sirve.
        resultado_correcto = (
            consumir_password_reset_challenge(
                public_id=challenge.public_id,
                codigo=codigo,
            )
        )

        self.assertFalse(
            resultado_correcto["valido"]
        )

        self.assertEqual(
            resultado_correcto["estado"],
            "BLOQUEADO",
        )


    def test_codigo_expirado_no_valida(
        self,
    ):
        challenge, codigo = (
            crear_password_reset_challenge(
                self.identity
            )
        )

        challenge.expira_en = (
            timezone.now()
            - timedelta(
                seconds=1
            )
        )

        challenge.save(
            update_fields=[
                "expira_en",
            ]
        )

        resultado = (
            consumir_password_reset_challenge(
                public_id=challenge.public_id,
                codigo=codigo,
            )
        )

        self.assertFalse(
            resultado["valido"]
        )

        self.assertEqual(
            resultado["estado"],
            "EXPIRADO",
        )

        challenge.refresh_from_db()

        self.assertIsNone(
            challenge.usado_en
        )


    def test_codigo_correcto_se_consume_una_sola_vez(
        self,
    ):
        challenge, codigo = (
            crear_password_reset_challenge(
                self.identity
            )
        )

        primer_resultado = (
            consumir_password_reset_challenge(
                public_id=challenge.public_id,
                codigo=codigo,
            )
        )

        self.assertTrue(
            primer_resultado["valido"]
        )

        self.assertEqual(
            primer_resultado["estado"],
            "VALIDO",
        )

        challenge.refresh_from_db()

        self.assertIsNotNone(
            challenge.usado_en
        )

        segundo_resultado = (
            consumir_password_reset_challenge(
                public_id=challenge.public_id,
                codigo=codigo,
            )
        )

        self.assertFalse(
            segundo_resultado["valido"]
        )

        self.assertEqual(
            segundo_resultado["estado"],
            "USADO",
        )


    def test_public_id_invalido_no_rompe_validacion(
        self,
    ):
        resultado = (
            consumir_password_reset_challenge(
                public_id="esto-no-es-un-uuid",
                codigo="123456",
            )
        )

        self.assertFalse(
            resultado["valido"]
        )

        self.assertEqual(
            resultado["estado"],
            "NO_ENCONTRADO",
        )
        
        
    @override_settings(
        EMAIL_BACKEND=(
            "django.core.mail.backends.locmem.EmailBackend"
        ),
        DEFAULT_FROM_EMAIL="no-reply@foodback.test",
        FOODBACK_PASSWORD_RESET_REQUEST_IP_LIMIT=50,
        FOODBACK_PASSWORD_RESET_REQUEST_EMAIL_LIMIT=50,
    )
    def test_solicitud_valida_envia_codigo_sin_guardarlo_plano(
        self,
    ):
        request = RequestFactory().post(
            "/password-reset/",
            REMOTE_ADDR="192.0.2.100",
        )

        resultado = solicitar_password_reset(
            request=request,
            email="  RESET@FOODBACK.TEST ",
        )

        self.assertTrue(
            resultado["permitido"]
        )

        self.assertEqual(
            len(mail.outbox),
            1,
        )

        challenge = (
            PasswordResetChallenge.objects
            .get(
                identity=self.identity
            )
        )

        cuerpo = mail.outbox[0].body

        import re

        coincidencia = re.search(
            r"\b\d{6}\b",
            cuerpo,
        )

        self.assertIsNotNone(
            coincidencia
        )

        codigo = coincidencia.group(0)

        self.assertNotEqual(
            challenge.codigo_hash,
            codigo,
        )

        self.assertTrue(
            password_reset_codigo_coincide(
                challenge,
                codigo,
            )
        )


    @override_settings(
        EMAIL_BACKEND=(
            "django.core.mail.backends.locmem.EmailBackend"
        ),
        FOODBACK_PASSWORD_RESET_REQUEST_IP_LIMIT=50,
        FOODBACK_PASSWORD_RESET_REQUEST_EMAIL_LIMIT=50,
    )
    def test_correo_inexistente_da_respuesta_generica(
        self,
    ):
        request = RequestFactory().post(
            "/password-reset/",
            REMOTE_ADDR="192.0.2.101",
        )

        resultado = solicitar_password_reset(
            request=request,
            email="no-existe@foodback.test",
        )

        self.assertTrue(
            resultado["permitido"]
        )

        self.assertEqual(
            len(mail.outbox),
            0,
        )

        self.assertFalse(
            PasswordResetChallenge.objects.exists()
        )


    @override_settings(
        EMAIL_BACKEND=(
            "django.core.mail.backends.locmem.EmailBackend"
        ),
        FOODBACK_PASSWORD_RESET_REQUEST_IP_LIMIT=50,
        FOODBACK_PASSWORD_RESET_REQUEST_EMAIL_LIMIT=50,
    )
    def test_nueva_solicitud_invalida_codigo_anterior(
        self,
    ):
        request = RequestFactory().post(
            "/password-reset/",
            REMOTE_ADDR="192.0.2.102",
        )

        solicitar_password_reset(
            request=request,
            email=self.identity.email,
        )

        primero = (
            PasswordResetChallenge.objects
            .get(
                identity=self.identity
            )
        )

        self.assertIsNone(
            primero.usado_en
        )

        solicitar_password_reset(
            request=request,
            email=self.identity.email,
        )

        primero.refresh_from_db()

        self.assertIsNotNone(
            primero.usado_en
        )

        self.assertEqual(
            PasswordResetChallenge.objects.filter(
                identity=self.identity,
            ).count(),
            2,
        )


    @override_settings(
        EMAIL_BACKEND=(
            "django.core.mail.backends.locmem.EmailBackend"
        ),
        FOODBACK_PASSWORD_RESET_REQUEST_IP_LIMIT=50,
        FOODBACK_PASSWORD_RESET_REQUEST_EMAIL_LIMIT=1,
        FOODBACK_PASSWORD_RESET_REQUEST_WINDOW_SECONDS=600,
        FOODBACK_PASSWORD_RESET_REQUEST_BLOCK_SECONDS=600,
    )
    def test_solicitud_repetida_por_correo_es_limitada(
        self,
    ):
        request = RequestFactory().post(
            "/password-reset/",
            REMOTE_ADDR="192.0.2.103",
        )

        primero = solicitar_password_reset(
            request=request,
            email=self.identity.email,
        )

        segundo = solicitar_password_reset(
            request=request,
            email=self.identity.email,
        )

        self.assertTrue(
            primero["permitido"]
        )

        self.assertFalse(
            segundo["permitido"]
        )

        self.assertGreater(
            segundo["retry_after"],
            0,
        )
        
        
    
    @override_settings(
    EMAIL_BACKEND=(
        "django.core.mail.backends.locmem.EmailBackend"
    ),
        FOODBACK_PASSWORD_RESET_REQUEST_IP_LIMIT=50,
        FOODBACK_PASSWORD_RESET_REQUEST_EMAIL_LIMIT=50,
    )
    def test_vista_solicitud_redirige_a_verificacion(
        self,
    ):
        response = self.client.post(
            reverse(
                "password_reset_request"
            ),
            {
                "email":
                    self.identity.email,
            },
            REMOTE_ADDR="192.0.2.120",
        )

        self.assertRedirects(
            response,
            reverse(
                "password_reset_verify"
            ),
            fetch_redirect_response=False,
        )

        self.assertIn(
            PASSWORD_RESET_FLOW_SESSION_KEY,
            self.client.session,
        )

        self.assertEqual(
            len(mail.outbox),
            1,
        )


    @override_settings(
        EMAIL_BACKEND=(
            "django.core.mail.backends.locmem.EmailBackend"
        ),
        FOODBACK_PASSWORD_RESET_REQUEST_IP_LIMIT=50,
        FOODBACK_PASSWORD_RESET_REQUEST_EMAIL_LIMIT=50,
    )
    def test_vista_correo_inexistente_redirige_igual(
        self,
    ):
        response = self.client.post(
            reverse(
                "password_reset_request"
            ),
            {
                "email":
                    "nadie@foodback.test",
            },
            REMOTE_ADDR="192.0.2.121",
        )

        self.assertRedirects(
            response,
            reverse(
                "password_reset_verify"
            ),
            fetch_redirect_response=False,
        )

        self.assertIn(
            PASSWORD_RESET_FLOW_SESSION_KEY,
            self.client.session,
        )

        self.assertEqual(
            len(mail.outbox),
            0,
        )


    @override_settings(
        EMAIL_BACKEND=(
            "django.core.mail.backends.locmem.EmailBackend"
        ),
        FOODBACK_PASSWORD_RESET_REQUEST_IP_LIMIT=50,
        FOODBACK_PASSWORD_RESET_REQUEST_EMAIL_LIMIT=50,
    )
    def test_codigo_valido_crea_grant_y_rota_sesion(
        self,
    ):
        self.client.post(
            reverse(
                "password_reset_request"
            ),
            {
                "email":
                    self.identity.email,
            },
            REMOTE_ADDR="192.0.2.122",
        )

        session_key_antes = (
            self.client.session.session_key
        )

        import re

        codigo = re.search(
            r"\b\d{6}\b",
            mail.outbox[0].body,
        ).group(0)

        response = self.client.post(
            reverse(
                "password_reset_verify"
            ),
            {
                "codigo":
                    codigo,
            },
        )

        self.assertEqual(
            response.status_code,
            200,
        )

        self.assertContains(
            response,
            "Código verificado correctamente",
        )

        session_despues = (
            self.client.session
        )

        self.assertNotEqual(
            session_key_antes,
            session_despues.session_key,
        )

        self.assertNotIn(
            PASSWORD_RESET_FLOW_SESSION_KEY,
            session_despues,
        )

        self.assertIn(
            PASSWORD_RESET_GRANT_SESSION_KEY,
            session_despues,
        )

        grant = session_despues[
            PASSWORD_RESET_GRANT_SESSION_KEY
        ]

        self.assertEqual(
            grant["user_id"],
            self.user.id,
        )


    def test_codigo_invalido_no_crea_grant(
        self,
    ):
        session = self.client.session

        session[
            PASSWORD_RESET_FLOW_SESSION_KEY
        ] = str(
            uuid.uuid4()
        )

        session.save()

        response = self.client.post(
            reverse(
                "password_reset_verify"
            ),
            {
                "codigo":
                    "123456",
            },
        )

        self.assertEqual(
            response.status_code,
            200,
        )

        self.assertContains(
            response,
            (
                "El código no es válido, "
                "venció o ya fue utilizado."
            ),
        )

        self.assertNotIn(
            PASSWORD_RESET_GRANT_SESSION_KEY,
            self.client.session,
        )
