import json
import uuid
from unittest.mock import patch

from django.contrib.auth.models import AnonymousUser
from django.http import HttpResponse
from django.test import RequestFactory, SimpleTestCase, override_settings
from django.urls import reverse

from .api_errors import professional_api_endpoint
from .models import AuditEvent
from .tests import FoodBackTestBase


class ApiErrorContractUnitTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def test_429_conserva_retry_after(self):
        request = self.factory.get('/api/test/')
        request.user = AnonymousUser()

        def view(_request):
            response = HttpResponse('detalle-interno-no-copiar', status=429)
            response.headers['Retry-After'] = '120'
            return response

        response = professional_api_endpoint(view)(request)
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.headers['Retry-After'], '120')
        self.assertEqual(response.headers['Cache-Control'], 'no-store')
        data = json.loads(
            response.content.decode(
                "utf-8"
            )
        )
        self.assertFalse(data['ok'])
        self.assertEqual(data['error']['code'], 'rate_limited')
        self.assertNotIn('detalle-interno-no-copiar', response.content.decode('utf-8'))

    def test_405_conserva_allow(self):
        request = self.factory.post('/api/test/')
        request.user = AnonymousUser()

        def view(_request):
            response = HttpResponse(status=405)
            response.headers['Allow'] = 'GET, HEAD'
            return response

        response = professional_api_endpoint(view)(request)
        self.assertEqual(response.status_code, 405)
        self.assertEqual(response.headers['Allow'], 'GET, HEAD')
        data = json.loads(response.content.decode("utf-8"))
        self.assertEqual(data['error']['code'], 'method_not_allowed')


class ApiErrorContractIntegrationTests(FoodBackTestBase):
    def _assert_error_contract(self, response, *, status_code, code):
        self.assertEqual(response.status_code, status_code)
        self.assertTrue(response.headers['Content-Type'].startswith('application/json'))
        self.assertEqual(response.headers['Cache-Control'], 'no-store')
        data = response.json()
        self.assertFalse(data['ok'])
        self.assertEqual(data['error']['code'], code)
        request_id = uuid.UUID(data['error']['request_id'])
        self.assertEqual(str(request_id), response.headers['X-Request-ID'])
        self.assertNotIn('Traceback', response.content.decode('utf-8'))

    def test_order_status_error_400_normalizado(self):
        pedido = self.crear_pedido(estado='RECIBIDO', telefono='79961001')
        response = self.client.get(
            reverse('api_order_status', args=[pedido.tracking_token]),
            {'last_update': 'fecha-totalmente-invalida'},
        )
        self._assert_error_contract(response, status_code=400, code='invalid_request')

    def test_order_status_404_normalizado(self):
        response = self.client.get(reverse('api_order_status', args=[uuid.uuid4()]))
        self._assert_error_contract(response, status_code=404, code='not_found')

    @override_settings(DEBUG=False)
    def test_404_de_resolucion_api_tambien_es_json(self):
        response = self.client.get('/api/pedido/1/status/')
        self._assert_error_contract(response, status_code=404, code='not_found')

    def test_post_a_api_get_only_es_405_json(self):
        response = self.client.post(reverse('api_order_status', args=[uuid.uuid4()]))
        self._assert_error_contract(response, status_code=405, code='method_not_allowed')
        self.assertIn('GET', response.headers['Allow'])

    def test_admin_sync_anonimo_es_401_no_redirect(self):
        response = self.client.get(reverse('api_dashboard_admin_sync'))
        self._assert_error_contract(response, status_code=401, code='authentication_required')
        self.assertNotIn('Location', response.headers)

    def test_delivery_sync_anonimo_es_401_no_redirect(self):
        response = self.client.get(reverse('api_delivery_sync'))
        self._assert_error_contract(response, status_code=401, code='authentication_required')

    def test_admin_sync_error_400_normalizado(self):
        self.client.force_login(self.admin_user)
        response = self.client.get(
            reverse('api_dashboard_admin_sync'),
            {'last_update': 'fecha-invalida'},
        )
        self._assert_error_contract(response, status_code=400, code='invalid_request')

    def test_delivery_sync_error_400_normalizado(self):
        self.client.force_login(self.delivery_1)
        response = self.client.get(
            reverse('api_delivery_sync'),
            {'last_update': 'fecha-invalida'},
        )
        self._assert_error_contract(response, status_code=400, code='invalid_request')

    def test_success_order_status_no_cambia_contrato(self):
        pedido = self.crear_pedido(estado='RECIBIDO', telefono='79961002')
        response = self.client.get(reverse('api_order_status', args=[pedido.tracking_token]))
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['status'], 'ok')
        self.assertTrue(data['changed'])
        self.assertNotIn('ok', data)

    @patch(
        'pedidos.views._parse_last_update',
        side_effect=RuntimeError('SECRET_DB_URL=no-debe-filtrarse'),
    )
    def test_excepcion_inesperada_api_es_500_seguro_y_auditado(self, mock_parse):
        pedido = self.crear_pedido(estado='RECIBIDO', telefono='79961003')
        response = self.client.get(reverse('api_order_status', args=[pedido.tracking_token]))
        self._assert_error_contract(response, status_code=500, code='internal_error')
        body = response.content.decode('utf-8')
        self.assertNotIn('SECRET_DB_URL', body)
        evento = AuditEvent.objects.filter(evento='system.unhandled_exception').latest('creado_en')
        self.assertEqual(str(evento.request_id), response.headers['X-Request-ID'])
        self.assertEqual(evento.metadata.get('exception_type'), 'RuntimeError')
        self.assertNotIn('SECRET_DB_URL', str(evento.metadata))
