import uuid
from unittest.mock import patch

from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import PermissionDenied
from django.http import Http404, HttpResponse
from django.test import (
    RequestFactory,
    SimpleTestCase,
    TestCase,
    override_settings,
)

from core.error_handlers import (
    error_400,
    error_403,
    error_404,
    error_500,
)
from pedidos.audit import (
    csrf_failure_view,
    registrar_evento_auditoria,
)
from pedidos.middleware import (
    AuditExceptionMiddleware,
)
from pedidos.models import AuditEvent


class RequestIdMiddlewareTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def test_agrega_request_id_a_respuesta(self):
        request = self.factory.get("/health-test/")
        middleware = AuditExceptionMiddleware(lambda req: HttpResponse("OK"))
        response = middleware(request)
        request_id = uuid.UUID(response.headers["X-Request-ID"])
        self.assertEqual(request_id, request.foodback_audit_request_id)

    def test_reutiliza_request_id_existente(self):
        request = self.factory.get("/health-test/")
        esperado = uuid.uuid4()
        request.foodback_audit_request_id = esperado
        middleware = AuditExceptionMiddleware(lambda req: HttpResponse("OK"))
        response = middleware(request)
        self.assertEqual(response.headers["X-Request-ID"], str(esperado))


class ErrorHandlerTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def _assert_handler_seguro(self, *, response, status_code, secreto):
        self.assertEqual(response.status_code, status_code)
        body = response.content.decode("utf-8")
        self.assertIn(str(status_code), body)
        self.assertIn("Código de referencia", body)
        self.assertNotIn(secreto, body)
        uuid.UUID(response.headers["X-Request-ID"])

    def test_400_no_expone_excepcion(self):
        secreto = "SQL password=NO-MOSTRAR-400"
        response = error_400(self.factory.get("/bad/"), Exception(secreto))
        self._assert_handler_seguro(response=response, status_code=400, secreto=secreto)

    def test_403_no_expone_excepcion(self):
        secreto = "TOKEN-INTERNO-NO-MOSTRAR-403"
        response = error_403(self.factory.get("/forbidden/"), PermissionDenied(secreto))
        self._assert_handler_seguro(response=response, status_code=403, secreto=secreto)

    def test_404_no_expone_excepcion(self):
        secreto = "RUTA-INTERNA-NO-MOSTRAR-404"
        response = error_404(self.factory.get("/missing/"), Http404(secreto))
        self._assert_handler_seguro(response=response, status_code=404, secreto=secreto)

    def test_500_no_expone_detalles_internos(self):
        response = error_500(self.factory.get("/boom/"))
        self._assert_handler_seguro(response=response, status_code=500, secreto="Traceback")

    @patch("core.error_handlers.render", side_effect=RuntimeError("template roto con SECRET_KEY"))
    def test_fallback_seguro_si_falla_template(self, mock_render):
        response = error_500(self.factory.get("/boom/"))
        self.assertEqual(response.status_code, 500)
        body = response.content.decode("utf-8")
        self.assertIn("Código de referencia", body)
        self.assertNotIn("SECRET_KEY", body)
        self.assertNotIn("Traceback", body)


class ProfessionalErrorIntegrationTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()

    @override_settings(DEBUG=False)
    def test_404_real_usa_handler_profesional(self):
        response = self.client.get("/ruta-que-no-existe-fase9/")
        self.assertEqual(response.status_code, 404)
        self.assertContains(response, "Código de referencia", status_code=404)
        uuid.UUID(response.headers["X-Request-ID"])

    def test_csrf_failure_profesional_no_expone_reason(self):
        reason_secreto = "CSRF token abc123-no-debe-salir"
        request = self.factory.post("/operacion-sensible/")
        request.user = AnonymousUser()
        response = csrf_failure_view(request, reason=reason_secreto)
        self.assertEqual(response.status_code, 403)
        body = response.content.decode("utf-8")
        self.assertIn("Código de referencia", body)
        self.assertNotIn(reason_secreto, body)
        evento = AuditEvent.objects.get(evento="security.csrf.rejected")
        self.assertEqual(str(evento.request_id), response.headers["X-Request-ID"])

    def test_request_id_http_coincide_con_auditoria(self):
        request = self.factory.get("/operacion-auditada/")
        request.user = AnonymousUser()

        def vista(req):
            registrar_evento_auditoria(
                request=req,
                evento="system.request_id.test",
                categoria=AuditEvent.Categoria.SISTEMA,
                severidad=AuditEvent.Severidad.INFO,
                resultado=AuditEvent.Resultado.INFORMATIVO,
                descripcion="Prueba de correlación Fase 9.",
                fail_silently=False,
            )
            return HttpResponse("OK")

        middleware = AuditExceptionMiddleware(vista)
        response = middleware(request)
        evento = AuditEvent.objects.get(evento="system.request_id.test")
        self.assertEqual(str(evento.request_id), response.headers["X-Request-ID"])
