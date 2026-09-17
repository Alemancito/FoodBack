from unittest.mock import patch
import uuid

from django.test import (
    RequestFactory,
    SimpleTestCase,
)

from core.error_handlers import error_500


class ErrorHandlerSafeLoggingTests(
    SimpleTestCase
):
    def setUp(self):
        self.factory = RequestFactory()

    def test_fallo_de_template_no_filtra_mensaje_ni_traceback_en_log(
        self,
    ):
        request = self.factory.get(
            "/error-log-test/"
        )

        secreto = (
            "SECRET_KEY=valor-super-secreto"
        )

        with patch(
            "core.error_handlers.render",
            side_effect=RuntimeError(
                secreto
            ),
        ):
            with self.assertLogs(
                "foodback.error_handlers",
                level="ERROR",
            ) as captured:
                response = error_500(
                    request
                )

        self.assertEqual(
            response.status_code,
            500,
        )

        request_id = response.headers[
            "X-Request-ID"
        ]

        uuid.UUID(
            request_id
        )

        logs = "\n".join(
            captured.output
        )

        self.assertIn(
            "RuntimeError",
            logs,
        )

        self.assertIn(
            request_id,
            logs,
        )

        self.assertNotIn(
            secreto,
            logs,
        )

        self.assertNotIn(
            "Traceback",
            logs,
        )

        body = response.content.decode(
            "utf-8"
        )

        self.assertNotIn(
            secreto,
            body,
        )

        self.assertIn(
            request_id,
            body,
        )
