from django.test import SimpleTestCase

from pedidos.views import _wompi_minimizar_payload


class WompiDataMinimizationTests(SimpleTestCase):
    def test_snapshot_conserva_solo_campos_operativos_permitidos(self):
        payload = {
            "identificadorEnlaceComercio": "ORDEN-123",
            "idTransaccion": "TX-ABC-123",
            "idEnlace": "LINK-77",
            "monto": "10.00",
            "esAprobada": True,
            "status": "APPROVED",
            "mensaje": "texto proveedor que no necesitamos persistir",
            "urlEnlace": "https://example.invalid/?token=secreto",
        }

        snapshot = _wompi_minimizar_payload(payload)

        self.assertEqual(snapshot["identificadorEnlaceComercio"], "ORDEN-123")
        self.assertEqual(snapshot["idTransaccion"], "TX-ABC-123")
        self.assertEqual(snapshot["idEnlace"], "LINK-77")
        self.assertEqual(snapshot["monto"], "10.00")
        self.assertTrue(snapshot["esAprobada"])
        self.assertEqual(snapshot["status"], "APPROVED")
        self.assertNotIn("mensaje", snapshot)
        self.assertNotIn("urlEnlace", snapshot)

    def test_snapshot_descarta_secretos_y_datos_personales_incluso_anidados(self):
        payload = {
            "authorization": "Bearer SUPER-SECRETO",
            "token": "TOKEN-SECRETO",
            "access_token": "ACCESS-SECRETO",
            "cardNumber": "4111111111111111",
            "cvv": "123",
            "email": "cliente@example.com",
            "phone": "70000000",
            "transaccion": {
                "idTransaccion": "TX-SEGURA",
                "monto": "12.50",
                "authorization": "AUTH-SECRETA",
                "card": "5555555555554444",
                "cvv": "999",
            },
        }

        snapshot = _wompi_minimizar_payload(payload)
        serializado = repr(snapshot)

        self.assertEqual(
            snapshot,
            {
                "transaccion": {
                    "idTransaccion": "TX-SEGURA",
                    "monto": "12.50",
                }
            },
        )
        for secreto in (
            "SUPER-SECRETO",
            "TOKEN-SECRETO",
            "ACCESS-SECRETO",
            "4111111111111111",
            "5555555555554444",
            "cliente@example.com",
            "70000000",
            "999",
        ):
            self.assertNotIn(secreto, serializado)

    def test_snapshot_es_fail_closed_ante_campos_nuevos(self):
        payload = {
            "campoNuevoWompi": "no guardar",
            "futureSecret": "no guardar tampoco",
            "data": {
                "estado": "APROBADO",
                "futureCustomerField": "dato inesperado",
            },
        }

        self.assertEqual(
            _wompi_minimizar_payload(payload),
            {"data": {"estado": "APROBADO"}},
        )

    def test_snapshot_no_persiste_payload_no_diccionario(self):
        self.assertEqual(_wompi_minimizar_payload(None), {})
        self.assertEqual(_wompi_minimizar_payload("token=secreto"), {})
        self.assertEqual(_wompi_minimizar_payload(["secreto"]), {})
