from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth.models import Group, User
from django.test import TestCase
from django.urls import reverse

from .models import (
    Categoria,
    Cliente,
    ConfiguracionNegocio,
    Extra,
    OpcionProducto,
    Pedido,
    Producto,
)


class FoodBackTestBase(TestCase):
    """
    Datos mínimos reutilizables para las pruebas del baseline.
    """

    @classmethod
    def setUpTestData(cls):
        # Mantener la suscripción activa durante las pruebas.
        cls.config = ConfiguracionNegocio.objects.create(
            nombre_negocio="FoodBack Test",
            fecha_vencimiento=date.today() + timedelta(days=365),
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

        cls.categoria = Categoria.objects.create(
            nombre="Hamburguesas",
            orden=1,
        )

        cls.producto = Producto.objects.create(
            categoria=cls.categoria,
            nombre="Hamburguesa de prueba",
            descripcion="Producto utilizado en las pruebas automáticas.",
            precio=Decimal("5.00"),
            disponible=True,
        )

        cls.cliente_pedido = Cliente.objects.create(
            telefono="70000001",
            nombre="Cliente",
            apellido="Prueba",
            direccion_ultima="San Miguel",
        )

        cls.grupo_admin = Group.objects.create(
            name="Administradores"
        )

        cls.grupo_delivery = Group.objects.create(
            name="Repartidores"
        )

        cls.admin_user = User.objects.create_user(
            username="admin_test",
            password="PasswordSeguro123!",
        )
        cls.admin_user.groups.add(cls.grupo_admin)

        cls.delivery_1 = User.objects.create_user(
            username="delivery_test_1",
            password="PasswordSeguro123!",
        )
        cls.delivery_1.groups.add(cls.grupo_delivery)

        cls.delivery_2 = User.objects.create_user(
            username="delivery_test_2",
            password="PasswordSeguro123!",
        )
        cls.delivery_2.groups.add(cls.grupo_delivery)

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
            nombre="Queso extra",
            precio=Decimal("1.00"),
            disponible=True,
        )
        cls.producto.extras.add(cls.extra_valido)

        # Extra válido, pero solamente para producto B.
        cls.extra_otro_producto = Extra.objects.create(
            nombre="Extra exclusivo pizza",
            precio=Decimal("2.00"),
            disponible=True,
        )
        cls.producto_b.extras.add(cls.extra_otro_producto)

        # Extra perteneciente al producto, pero deshabilitado.
        cls.extra_no_disponible = Extra.objects.create(
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