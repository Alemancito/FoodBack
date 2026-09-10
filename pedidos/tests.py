import json
from unittest.mock import patch

import hashlib

from datetime import date, timedelta, time
from decimal import Decimal

from django.contrib.auth.models import Group, User
from django.test import Client, TestCase
from django.urls import reverse

from django.db import IntegrityError, transaction

from django.utils import timezone

from django.contrib.auth import get_user_model
from django.test import RequestFactory
from pedidos.models import Tenant, Membership, Sucursal

from django.contrib.sessions.backends.db import SessionStore

from django.contrib.auth.models import AnonymousUser

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
)

from pedidos.tenant_context import (
    resolver_tenant,
    resolver_sucursal,
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
            telefono = f"71{Cliente.objects.count():06d}"

        cliente = Cliente.objects.create(
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

    def test_repartidor_no_accede_dashboard_admin(self):
        self.client.force_login(self.delivery_1)

        response = self.client.get(
            reverse("dashboard_admin")
        )

        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            response["Location"].startswith(
                reverse("login_custom")
            )
        )

    def test_admin_no_accede_dashboard_delivery(self):
        self.client.force_login(self.admin_user)

        response = self.client.get(
            reverse("dashboard_delivery")
        )

        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            response["Location"].startswith(
                reverse("login_custom")
            )
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

        response = self.client.post(
            reverse("checkout"),
            {
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

        response = self.client.post(
            reverse("checkout"),
            {
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

        response = self.client.post(
            reverse("checkout"),
            {
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

        response = self.client.post(
            reverse("checkout"),
            {
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

    def crear_error_global(
        self,
        cliente_hash,
        numero,
        prefijo="GLOBAL",
    ):
        return EventoPagoWompi.objects.create(
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
                configuracion_negocio=
                    self.config
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
            configuracion_negocio=self.config,
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

        response = self.client.post(
            reverse("checkout"),
            {
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
            configuracion_negocio=self.config,
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
            configuracion_negocio=self.config,
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
            contexto_capturado["tenant"] = req.tenant
            contexto_capturado["sucursal"] = req.sucursal

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
            contexto_capturado["tenant"] = req.tenant
            contexto_capturado["sucursal"] = req.sucursal

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
