from django.contrib.auth.models import User
from django.db import IntegrityError, transaction
from django.test import TestCase

from .models import (
    AceptacionLegalTenant,
    DocumentoLegal,
    Tenant,
)


class LegalVersioningModelTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(
            nombre="Restaurante Legal",
            slug="restaurante-legal",
        )
        self.user = User.objects.create_user(
            username="owner_legal",
            password="test-only-password",
        )

    def _documento(self, *, version="1.0", vigente=True):
        return DocumentoLegal.objects.create(
            tipo=DocumentoLegal.Tipo.TERMINOS,
            version=version,
            titulo=f"Términos {version}",
            contenido_sha256=("a" if version == "1.0" else "b") * 64,
            vigente=vigente,
        )

    def test_tipo_y_version_no_se_pueden_duplicar(self):
        self._documento()

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                self._documento(vigente=False)

    def test_solo_una_version_vigente_por_tipo(self):
        self._documento(version="1.0", vigente=True)

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                self._documento(version="2.0", vigente=True)

    def test_aceptacion_conserva_snapshot_de_version_y_hash(self):
        documento = self._documento()

        aceptacion = AceptacionLegalTenant.objects.create(
            tenant=self.tenant,
            documento=documento,
            actor_usuario=self.user,
            actor_username=self.user.username,
            actor_role="OWNER",
            version_snapshot=documento.version,
            contenido_sha256_snapshot=documento.contenido_sha256,
        )

        self.assertEqual(aceptacion.version_snapshot, "1.0")
        self.assertEqual(
            aceptacion.contenido_sha256_snapshot,
            "a" * 64,
        )
        self.assertEqual(aceptacion.actor_username, "owner_legal")
        self.assertEqual(aceptacion.actor_role, "OWNER")

    def test_tenant_no_puede_aceptar_dos_veces_misma_version(self):
        documento = self._documento()
        payload = {
            "tenant": self.tenant,
            "documento": documento,
            "actor_usuario": self.user,
            "actor_username": self.user.username,
            "actor_role": "OWNER",
            "version_snapshot": documento.version,
            "contenido_sha256_snapshot": documento.contenido_sha256,
        }
        AceptacionLegalTenant.objects.create(**payload)

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                AceptacionLegalTenant.objects.create(**payload)
