import re
import uuid
from unittest.mock import (
    Mock,
    patch,
)

import requests
from django.contrib.messages import get_messages
from django.test import SimpleTestCase
from django.urls import reverse

from .models import PagoWompi
from .tests import FoodBackTestBase
from .views import (
    WompiProviderError,
    _wompi_crear_enlace_pago,
    _wompi_error_metadata,
    _wompi_obtener_token,
)


class LegacyAjaxProfessionalErrorTests(
    FoodBackTestBase
):
    def test_eliminar_item_ajax_error_tiene_contrato_seguro(
        self,
    ):
        session = self.client.session

        # Sesión deliberadamente manipulada.
        session["cart"] = [
            "estructura-invalida"
        ]

        session.save()

        response = self.client.post(
            reverse(
                "eliminar_item",
                args=[
                    "no-existe"
                ],
            ),
            HTTP_X_REQUESTED_WITH=(
                "XMLHttpRequest"
            ),
            HTTP_ACCEPT=(
                "application/json"
            ),
        )

        self.assertEqual(
            response.status_code,
            400,
        )

        self.assertEqual(
            response.headers[
                "Cache-Control"
            ],
            "no-store",
        )

        data = response.json()

        self.assertEqual(
            data["status"],
            "error",
        )

        self.assertEqual(
            data["error"]["code"],
            "invalid_cart",
        )

        self.assertEqual(
            data["detail"],
            data["error"]["message"],
        )

        request_id = str(
            uuid.UUID(
                data[
                    "error"
                ][
                    "request_id"
                ]
            )
        )

        self.assertEqual(
            response.headers[
                "X-Request-ID"
            ],
            request_id,
        )

        body = response.content.decode(
            "utf-8"
        )

        self.assertNotIn(
            "estructura-invalida",
            body,
        )

        self.assertEqual(
            self.client.session.get(
                "cart"
            ),
            {},
        )

    def test_eliminar_item_ajax_exito_conserva_contrato(
        self,
    ):
        clave = (
            f"{self.producto.id}-0-0"
        )

        session = self.client.session

        session["cart"] = {
            clave: 1,
        }

        session.save()

        response = self.client.post(
            reverse(
                "eliminar_item",
                args=[
                    clave
                ],
            ),
            HTTP_X_REQUESTED_WITH=(
                "XMLHttpRequest"
            ),
            HTTP_ACCEPT=(
                "application/json"
            ),
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

        self.assertTrue(
            data["vacio"]
        )

        self.assertEqual(
            data["total"],
            0.0,
        )

        self.assertNotIn(
            "error",
            data,
        )

        self.assertEqual(
            response.headers[
                "Cache-Control"
            ],
            "no-store",
        )


class WompiProviderSanitizationTests(
    SimpleTestCase
):
    def _response(
        self,
        *,
        status_code=200,
        data=None,
        text="",
    ):
        response = Mock()

        response.status_code = (
            status_code
        )

        response.text = text

        if isinstance(
            data,
            Exception,
        ):
            response.json.side_effect = (
                data
            )

        else:
            response.json.return_value = (
                data
            )

        return response

    @patch(
        "pedidos.views._wompi_api_secret",
        return_value="secret-real",
    )
    @patch(
        "pedidos.views._wompi_app_id",
        return_value="client-id",
    )
    @patch(
        "pedidos.views.requests.post"
    )
    def test_auth_http_error_no_transporta_body_proveedor(
        self,
        mock_post,
        mock_app_id,
        mock_secret,
    ):
        secreto = (
            "ACCESS_TOKEN_PROVIDER="
            "NO-DEBE-FILTRARSE"
        )

        mock_post.return_value = (
            self._response(
                status_code=503,
                data={},
                text=secreto,
            )
        )

        with self.assertRaises(
            WompiProviderError
        ) as captured:
            _wompi_obtener_token(
                tipo_pago="PEDIDO",
            )

        error = captured.exception

        self.assertEqual(
            error.code,
            "WOMPI_AUTH_HTTP_ERROR",
        )

        self.assertEqual(
            error.provider_status,
            503,
        )

        self.assertNotIn(
            secreto,
            str(
                error
            ),
        )

        metadata = (
            _wompi_error_metadata(
                error
            )
        )

        self.assertEqual(
            metadata[
                "provider_code"
            ],
            "WOMPI_AUTH_HTTP_ERROR",
        )

        self.assertNotIn(
            secreto,
            str(
                metadata
            ),
        )

    @patch(
        "pedidos.views._wompi_api_secret",
        return_value="secret-real",
    )
    @patch(
        "pedidos.views._wompi_app_id",
        return_value="client-id",
    )
    @patch(
        "pedidos.views.requests.post",
        side_effect=requests.Timeout(
            "TOKEN_CRUDO_NO_FILTRAR"
        ),
    )
    def test_auth_timeout_no_transporta_mensaje_requests(
        self,
        mock_post,
        mock_app_id,
        mock_secret,
    ):
        with self.assertRaises(
            WompiProviderError
        ) as captured:
            _wompi_obtener_token(
                tipo_pago="PEDIDO",
            )

        error = captured.exception

        self.assertEqual(
            error.code,
            "WOMPI_TIMEOUT",
        )

        self.assertEqual(
            error.stage,
            "auth_request",
        )

        self.assertNotIn(
            "TOKEN_CRUDO_NO_FILTRAR",
            str(
                error
            ),
        )

    @patch(
        "pedidos.views._wompi_api_secret",
        return_value="secret-real",
    )
    @patch(
        "pedidos.views._wompi_app_id",
        return_value="client-id",
    )
    @patch(
        "pedidos.views.requests.post"
    )
    def test_auth_json_invalido_es_error_sanitizado(
        self,
        mock_post,
        mock_app_id,
        mock_secret,
    ):
        secreto = (
            "BODY_PROVEEDOR_SECRETO"
        )

        mock_post.return_value = (
            self._response(
                status_code=200,
                data=ValueError(
                    secreto
                ),
                text=secreto,
            )
        )

        with self.assertRaises(
            WompiProviderError
        ) as captured:
            _wompi_obtener_token(
                tipo_pago="PEDIDO",
            )

        error = captured.exception

        self.assertEqual(
            error.code,
            "WOMPI_AUTH_INVALID_RESPONSE",
        )

        self.assertNotIn(
            secreto,
            str(
                error
            ),
        )

    @patch(
        "pedidos.views._wompi_obtener_token",
        return_value="token-interno",
    )
    @patch(
        "pedidos.views.requests.post"
    )
    def test_enlace_http_error_no_transporta_body_proveedor(
        self,
        mock_post,
        mock_token,
    ):
        secreto = (
            "WOMPI_RESPONSE_SECRET="
            "NO-DEBE-FILTRARSE"
        )

        mock_post.return_value = (
            self._response(
                status_code=502,
                data={},
                text=secreto,
            )
        )

        with self.assertRaises(
            WompiProviderError
        ) as captured:
            _wompi_crear_enlace_pago(
                None,
                referencia=(
                    "ORDEN-T1-P1-"
                    "ABCDEF123456"
                ),
                monto="10.00",
                nombre_producto=(
                    "Pedido"
                ),
                redirect_url=(
                    "https://example.test/"
                    "retorno"
                ),
                webhook_url=(
                    "https://example.test/"
                    "webhook"
                ),
                tipo_pago="PEDIDO",
            )

        error = captured.exception

        self.assertEqual(
            error.code,
            "WOMPI_LINK_HTTP_ERROR",
        )

        self.assertEqual(
            error.provider_status,
            502,
        )

        self.assertNotIn(
            secreto,
            str(
                error
            ),
        )

    @patch(
        "pedidos.views._wompi_obtener_token",
        return_value="token-interno",
    )
    @patch(
        "pedidos.views.requests.post"
    )
    def test_enlace_json_invalido_es_error_sanitizado(
        self,
        mock_post,
        mock_token,
    ):
        secreto = (
            "JSON_ERROR_PROVIDER_SECRET"
        )

        mock_post.return_value = (
            self._response(
                status_code=200,
                data=ValueError(
                    secreto
                ),
                text=secreto,
            )
        )

        with self.assertRaises(
            WompiProviderError
        ) as captured:
            _wompi_crear_enlace_pago(
                None,
                referencia=(
                    "ORDEN-T1-P1-"
                    "ABCDEF123456"
                ),
                monto="10.00",
                nombre_producto=(
                    "Pedido"
                ),
                redirect_url=(
                    "https://example.test/"
                    "retorno"
                ),
                webhook_url=(
                    "https://example.test/"
                    "webhook"
                ),
                tipo_pago="PEDIDO",
            )

        self.assertEqual(
            captured.exception.code,
            "WOMPI_LINK_INVALID_RESPONSE",
        )

        self.assertNotIn(
            secreto,
            str(
                captured.exception
            ),
        )


class WompiPublicMessageTests(
    FoodBackTestBase
):
    def _mensajes(
        self,
        response,
    ):
        return [
            str(
                message
            )
            for message in get_messages(
                response.wsgi_request
            )
        ]

    def _assert_referencia_publica(
        self,
        response,
        mensajes,
    ):
        texto = "\n".join(
            mensajes
        )

        match = re.search(
            (
                r"Código de referencia: "
                r"([0-9a-fA-F-]{36})"
            ),
            texto,
        )

        self.assertIsNotNone(
            match
        )

        request_id = str(
            uuid.UUID(
                match.group(
                    1
                )
            )
        )

        self.assertEqual(
            response.headers[
                "X-Request-ID"
            ],
            request_id,
        )

    @patch(
        "pedidos.views._procesar_pago_wompi_aprobado",
        return_value=(
            False,
            (
                "SECRET_INTERNAL expected=10 "
                "received=999"
            ),
        ),
    )
    @patch(
        "pedidos.views._redirect_wompi_aprobado",
        return_value=True,
    )
    @patch(
        "pedidos.views._validar_hash_redirect_wompi",
        return_value=True,
    )
    def test_redirect_pedido_no_expone_motivo_interno(
        self,
        mock_hash,
        mock_aprobado,
        mock_procesar,
    ):
        pedido = self.crear_pedido(
            estado="PENDIENTE",
            telefono="79962001",
        )

        pedido.metodo_pago = (
            "TARJETA"
        )

        pedido.save(
            update_fields=[
                "metodo_pago",
            ]
        )

        pago = PagoWompi.objects.create(
            tenant=self.tenant,
            pedido=pedido,
            tipo="PEDIDO",
            referencia=(
                "ORDEN-T1-P1-"
                "ABCDEF123456"
            ),
            monto="10.00",
            estado="PENDIENTE",
        )

        response = self.client.get(
            reverse(
                "wompi_respuesta"
            ),
            {
                "ref":
                    pago.referencia,

                "idTransaccion":
                    "TX-REVISION",

                "monto":
                    "999",
            },
        )

        self.assertEqual(
            response.status_code,
            302,
        )

        mensajes = self._mensajes(
            response
        )

        texto = "\n".join(
            mensajes
        )

        self.assertNotIn(
            "SECRET_INTERNAL",
            texto,
        )

        self.assertNotIn(
            "expected=10",
            texto,
        )

        self.assertIn(
            "necesita revisión",
            texto,
        )

        self._assert_referencia_publica(
            response,
            mensajes,
        )

    @patch(
        "pedidos.views._procesar_pago_wompi_aprobado",
        return_value=(
            False,
            "SECRET_SUBSCRIPTION_INTERNAL",
        ),
    )
    @patch(
        "pedidos.views._redirect_wompi_aprobado",
        return_value=True,
    )
    @patch(
        "pedidos.views._validar_hash_redirect_wompi",
        return_value=True,
    )
    def test_redirect_suscripcion_no_expone_motivo_interno(
        self,
        mock_hash,
        mock_aprobado,
        mock_procesar,
    ):
        self.client.force_login(
            self.admin_user
        )

        pago = PagoWompi.objects.create(
            tenant=self.tenant,
            tipo="SUSCRIPCION",
            referencia=(
                "SUBS-T1-"
                "ABCDEF123456"
            ),
            monto="50.00",
            estado="PENDIENTE",
        )

        response = self.client.get(
            reverse(
                "wompi_suscripcion_respuesta"
            ),
            {
                "ref":
                    pago.referencia,

                "idTransaccion":
                    "TX-SUB-REVISION",

                "monto":
                    "999",
            },
        )

        self.assertEqual(
            response.status_code,
            302,
        )

        mensajes = self._mensajes(
            response
        )

        texto = "\n".join(
            mensajes
        )

        self.assertNotIn(
            "SECRET_SUBSCRIPTION_INTERNAL",
            texto,
        )

        self.assertIn(
            "necesita revisión",
            texto,
        )

        self._assert_referencia_publica(
            response,
            mensajes,
        )
