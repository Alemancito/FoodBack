import re
import uuid
from unittest.mock import patch

from django.http import Http404
from django.test import (
    Client,
    RequestFactory,
    TestCase,
    override_settings,
)
from django.urls import reverse
from django.utils import timezone

from core.error_handlers import preview_error
from pedidos.audit import (
    registrar_evento_auditoria,
)
from pedidos.incidents import (
    _send_reserved_notification,
)
from pedidos.models import (
    AuditEvent,
    SecurityIncident,
    SupportReport,
)
from pedidos.support import (
    crear_token_reporte_error,
)
from pedidos.tests import FoodBackTestBase


class Phase9SupportReportTests(
    FoodBackTestBase
):
    def _token(
        self,
        *,
        status_code=500,
        request_id=None,
    ):
        request_id = (
            request_id
            or uuid.uuid4()
        )

        return (
            request_id,
            crear_token_reporte_error(
                request_id=request_id,
                status_code=status_code,
            ),
        )

    @staticmethod
    def _token_from_html(
        html,
    ):
        match = re.search(
            r'name="report_token"\s+value="([^"]+)"',
            html,
        )

        if match is None:
            return ""

        return match.group(
            1
        )

    @override_settings(
        DEBUG=False
    )
    def test_404_produccion_ofrece_reporte_firmado_y_no_cache(
        self,
    ):
        response = self.client.get(
            "/ruta-que-no-existe-fase9/"
        )

        self.assertEqual(
            response.status_code,
            404,
        )

        body = response.content.decode(
            "utf-8"
        )

        self.assertIn(
            "Reportar este problema",
            body,
        )

        self.assertTrue(
            self._token_from_html(
                body
            )
        )

        self.assertIn(
            "no-store",
            response.headers.get(
                "Cache-Control",
                "",
            ),
        )

        uuid.UUID(
            response.headers[
                "X-Request-ID"
            ]
        )

    def test_reporte_anonimo_crea_ticket_correlacionado(
        self,
    ):
        source_request_id, token = (
            self._token(
                status_code=500
            )
        )

        response = self.client.post(
            reverse(
                "support_report_error"
            ),
            {
                "report_token": token,
                "message": (
                    "Estaba intentando confirmar "
                    "un pedido."
                ),
            },
        )

        self.assertEqual(
            response.status_code,
            200,
        )

        report = (
            SupportReport.objects.get()
        )

        self.assertEqual(
            report.source_request_id,
            source_request_id,
        )

        self.assertEqual(
            report.http_status,
            500,
        )

        self.assertEqual(
            report.tenant_public_id,
            self.tenant.public_id,
        )

        self.assertIsNone(
            report.actor_usuario_id
        )

        self.assertEqual(
            report.actor_username,
            "",
        )

        self.assertIsNotNone(
            report.submission_request_id
        )

        self.assertContains(
            response,
            str(
                report.public_id
            ),
        )

    def test_contexto_actor_tenant_se_toma_del_servidor_y_no_del_post(
        self,
    ):
        self.client.force_login(
            self.admin_user
        )

        _, token = self._token(
            status_code=403
        )

        fake_tenant = uuid.uuid4()
        fake_branch = uuid.uuid4()

        response = self.client.post(
            reverse(
                "support_report_error"
            ),
            {
                "report_token": token,
                "message": "Acceso inesperado.",
                # Intentos deliberados de falsificación.
                "actor_username": "atacante",
                "actor_role": "SUPERADMIN_FOODBACK",
                "tenant_public_id": str(
                    fake_tenant
                ),
                "sucursal_public_id": str(
                    fake_branch
                ),
                "tenant_nombre": "Tenant falso",
                "sucursal_nombre": "Sucursal falsa",
            },
        )

        self.assertEqual(
            response.status_code,
            200,
        )

        report = (
            SupportReport.objects.get()
        )

        self.assertEqual(
            report.actor_usuario_id,
            self.admin_user.pk,
        )

        self.assertEqual(
            report.actor_username,
            self.admin_user.username,
        )

        self.assertEqual(
            report.actor_role,
            "OWNER",
        )

        self.assertEqual(
            report.tenant_public_id,
            self.tenant.public_id,
        )

        self.assertNotEqual(
            report.tenant_public_id,
            fake_tenant,
        )

        self.assertNotEqual(
            report.sucursal_public_id,
            fake_branch,
        )

        self.assertNotEqual(
            report.tenant_nombre,
            "Tenant falso",
        )

        self.assertNotEqual(
            report.sucursal_nombre,
            "Sucursal falsa",
        )

    def test_token_manipulado_se_rechaza_sin_crear_ticket(
        self,
    ):
        _, token = self._token()

        response = self.client.post(
            reverse(
                "support_report_error"
            ),
            {
                "report_token": (
                    token
                    + "alterado"
                ),
                "message": "No importa.",
            },
        )

        self.assertEqual(
            response.status_code,
            400,
        )

        self.assertFalse(
            SupportReport.objects.exists()
        )

        self.assertNotContains(
            response,
            "Reportar este problema",
            status_code=400,
        )

    def test_reenvio_mismo_token_es_idempotente(
        self,
    ):
        _, token = self._token()

        url = reverse(
            "support_report_error"
        )

        first = self.client.post(
            url,
            {
                "report_token": token,
                "message": "Primer envío",
            },
        )

        second = self.client.post(
            url,
            {
                "report_token": token,
                "message": "Segundo envío",
            },
        )

        self.assertEqual(
            first.status_code,
            200,
        )

        self.assertEqual(
            second.status_code,
            200,
        )

        self.assertEqual(
            SupportReport.objects.count(),
            1,
        )

        self.assertEqual(
            AuditEvent.objects.filter(
                evento="support.report.created"
            ).count(),
            1,
        )

        self.assertContains(
            second,
            "ya estaba guardado",
        )

    @override_settings(
        FOODBACK_SUPPORT_REPORT_SESSION_LIMIT=1,
        FOODBACK_SUPPORT_REPORT_IP_LIMIT=100,
        FOODBACK_SUPPORT_REPORT_WINDOW_SECONDS=3600,
        FOODBACK_SUPPORT_REPORT_BLOCK_SECONDS=120,
    )
    def test_rate_limit_bloquea_reporte_repetido_y_expone_retry_after(
        self,
    ):
        _, token_1 = self._token()
        _, token_2 = self._token()

        url = reverse(
            "support_report_error"
        )

        first = self.client.post(
            url,
            {
                "report_token": token_1,
            },
        )

        second = self.client.post(
            url,
            {
                "report_token": token_2,
            },
        )

        self.assertEqual(
            first.status_code,
            200,
        )

        self.assertEqual(
            second.status_code,
            429,
        )

        self.assertGreaterEqual(
            int(
                second.headers[
                    "Retry-After"
                ]
            ),
            1,
        )

        self.assertEqual(
            SupportReport.objects.count(),
            1,
        )

        self.assertTrue(
            AuditEvent.objects.filter(
                evento=(
                    "support.report.rate_limited"
                )
            ).exists()
        )

    def test_mensaje_se_acota_y_remueve_nul(
        self,
    ):
        _, token = self._token()

        response = self.client.post(
            reverse(
                "support_report_error"
            ),
            {
                "report_token": token,
                "message": (
                    ("A" * 1700)
                    + "\x00"
                    + ("B" * 200)
                ),
            },
        )

        self.assertEqual(
            response.status_code,
            200,
        )

        report = (
            SupportReport.objects.get()
        )

        self.assertEqual(
            len(
                report.mensaje
            ),
            1500,
        )

        self.assertNotIn(
            "\x00",
            report.mensaje,
        )

    def test_auditoria_de_soporte_no_duplica_texto_del_usuario(
        self,
    ):
        secreto = (
            "TOKEN-USUARIO-NO-DEBE-COPIARSE"
        )

        _, token = self._token()

        response = self.client.post(
            reverse(
                "support_report_error"
            ),
            {
                "report_token": token,
                "message": secreto,
            },
        )

        self.assertEqual(
            response.status_code,
            200,
        )

        event = (
            AuditEvent.objects.get(
                evento="support.report.created"
            )
        )

        self.assertNotIn(
            secreto,
            event.descripcion,
        )

        self.assertNotIn(
            secreto,
            str(
                event.metadata
            ),
        )

        self.assertEqual(
            event.objeto_tipo,
            "SupportReport",
        )

    def test_endpoint_exige_csrf_real(
        self,
    ):
        _, token = self._token()

        csrf_client = Client(
            enforce_csrf_checks=True
        )

        response = csrf_client.post(
            reverse(
                "support_report_error"
            ),
            {
                "report_token": token,
                "message": "Sin CSRF",
            },
        )

        self.assertEqual(
            response.status_code,
            403,
        )

        self.assertFalse(
            SupportReport.objects.exists()
        )

        self.assertNotContains(
            response,
            "Reportar este problema",
            status_code=403,
        )

    @override_settings(
        DEBUG=False
    )
    def test_preview_error_tiene_defensa_interna_en_produccion(
        self,
    ):
        request = RequestFactory().get(
            "/__dev__/error/500/"
        )

        with self.assertRaises(
            Http404
        ):
            preview_error(
                request,
                500,
            )


class Phase9SafeInternalLoggingTests(
    TestCase
):
    @patch(
        (
            "pedidos.incidents."
            "procesar_evento_auditoria_para_incidentes"
        ),
        side_effect=RuntimeError(
            "SECRET_CORRELATION_DETAIL"
        ),
    )
    def test_fallo_correlacion_incidente_no_filtra_exception_message(
        self,
        mock_process,
    ):
        with self.assertLogs(
            "foodback.audit",
            level="ERROR",
        ) as captured:
            event = registrar_evento_auditoria(
                evento="support.logging.test",
                categoria=(
                    AuditEvent.Categoria.SOPORTE
                ),
                descripcion=(
                    "Prueba de logging seguro."
                ),
                fail_silently=False,
            )

        self.assertIsNotNone(
            event
        )

        logs = "\n".join(
            captured.output
        )

        self.assertIn(
            "RuntimeError",
            logs,
        )

        self.assertNotIn(
            "SECRET_CORRELATION_DETAIL",
            logs,
        )

        self.assertNotIn(
            "Traceback",
            logs,
        )

    @patch(
        "pedidos.audit.AuditEvent.objects.create",
        side_effect=RuntimeError(
            "SECRET_AUDIT_DB_DETAIL"
        ),
    )
    def test_fallo_persistencia_auditoria_no_filtra_exception_message(
        self,
        mock_create,
    ):
        with self.assertLogs(
            "foodback.audit",
            level="ERROR",
        ) as captured:
            event = registrar_evento_auditoria(
                evento="support.persist.test",
                categoria=(
                    AuditEvent.Categoria.SOPORTE
                ),
                fail_silently=True,
            )

        self.assertIsNone(
            event
        )

        logs = "\n".join(
            captured.output
        )

        self.assertIn(
            "RuntimeError",
            logs,
        )

        self.assertNotIn(
            "SECRET_AUDIT_DB_DETAIL",
            logs,
        )

        self.assertNotIn(
            "Traceback",
            logs,
        )

    @override_settings(
        FOODBACK_SECURITY_ALERT_EMAIL=(
            "security@example.com"
        ),
    )
    @patch(
        "pedidos.incidents.send_mail",
        side_effect=RuntimeError(
            "SECRET_SMTP_PASSWORD"
        ),
    )
    def test_fallo_smtp_no_filtra_exception_message_en_log(
        self,
        mock_send,
    ):
        now = timezone.now()

        incident = (
            SecurityIncident.objects.create(
                fingerprint=(
                    "f" * 64
                ),
                evento_clave=(
                    "security.phase9.logging"
                ),
                categoria=(
                    AuditEvent.Categoria.SEGURIDAD
                ),
                severidad=(
                    AuditEvent.Severidad.ALTA
                ),
                estado=(
                    SecurityIncident.Estado.ABIERTO
                ),
                titulo=(
                    "Prueba SMTP"
                ),
                contador_eventos=1,
                primero_visto_en=now,
                ultimo_visto_en=now,
            )
        )

        with self.assertLogs(
            "foodback.security_incidents",
            level="ERROR",
        ) as captured:
            result = (
                _send_reserved_notification(
                    incidente_id=(
                        incident.pk
                    )
                )
            )

        self.assertFalse(
            result
        )

        incident.refresh_from_db()

        self.assertEqual(
            incident.fallos_notificacion,
            1,
        )

        self.assertNotIn(
            "SECRET_SMTP_PASSWORD",
            incident.ultimo_error_notificacion,
        )

        logs = "\n".join(
            captured.output
        )

        self.assertIn(
            "RuntimeError",
            logs,
        )

        self.assertNotIn(
            "SECRET_SMTP_PASSWORD",
            logs,
        )

        self.assertNotIn(
            "Traceback",
            logs,
        )
