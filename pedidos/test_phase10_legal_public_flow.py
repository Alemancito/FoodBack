import hashlib

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from .models import (
    AceptacionLegalTenant,
    AuditEvent,
    DocumentoLegal,
    Membership,
    Tenant,
)


class Phase10LegalPublicFlowTests(TestCase):
    def setUp(self):
        DocumentoLegal.objects.update(vigente=False)
        self.tenant = Tenant.objects.create(nombre="Legal Flow", slug="legal-flow")
        self.owner = User.objects.create_user(username="owner_flow", password="test-only-password")
        Membership.objects.create(
            tenant=self.tenant,
            usuario=self.owner,
            rol=Membership.ROLE_OWNER,
            activo=True,
        )
        self.manager = User.objects.create_user(username="manager_flow", password="test-only-password")
        Membership.objects.create(
            tenant=self.tenant,
            usuario=self.manager,
            rol=Membership.ROLE_MANAGER,
            activo=True,
        )
        for tipo, titulo, contenido in (
            (DocumentoLegal.Tipo.TERMINOS, "Términos", "Términos de prueba"),
            (DocumentoLegal.Tipo.PRIVACIDAD, "Privacidad", "Privacidad de prueba"),
            (DocumentoLegal.Tipo.COOKIES, "Cookies", "Cookies de prueba"),
        ):
            DocumentoLegal.objects.create(
                tipo=tipo,
                version="9.9-test",
                titulo=titulo,
                contenido=contenido,
                contenido_sha256=hashlib.sha256(contenido.encode("utf-8")).hexdigest(),
                vigente=True,
            )

    def test_centro_legal_es_publico(self):
        response = self.client.get(reverse("legal_center"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Términos del servicio")
        self.assertContains(response, "Privacidad")
        self.assertContains(response, "Cookies")

    def test_documento_publicado_muestra_version_y_contenido(self):
        response = self.client.get(reverse("legal_document", args=["privacidad"]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "9.9-test")
        self.assertContains(response, "Privacidad de prueba")

    def test_manager_no_puede_registrar_aceptacion_contractual(self):
        self.client.force_login(self.manager)
        response = self.client.get(reverse("legal_acceptance"))
        self.assertEqual(response.status_code, 403)

    def test_owner_debe_confirmar_ambos_documentos(self):
        self.client.force_login(self.owner)
        response = self.client.post(
            reverse("legal_acceptance"),
            {"acepta_terminos": "on"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(AceptacionLegalTenant.objects.filter(tenant=self.tenant).count(), 0)

    def test_owner_registra_terminos_y_privacidad(self):
        self.client.force_login(self.owner)
        response = self.client.post(
            reverse("legal_acceptance"),
            {"acepta_terminos": "on", "acepta_privacidad": "on"},
            follow=False,
        )
        self.assertEqual(response.status_code, 302)
        aceptaciones = AceptacionLegalTenant.objects.filter(tenant=self.tenant)
        self.assertEqual(aceptaciones.count(), 2)
        self.assertTrue(aceptaciones.filter(documento__tipo=DocumentoLegal.Tipo.TERMINOS).exists())
        self.assertTrue(aceptaciones.filter(documento__tipo=DocumentoLegal.Tipo.PRIVACIDAD).exists())
        self.assertTrue(AuditEvent.objects.filter(evento="legal.documents.accepted").exists())

    def test_post_repetido_es_idempotente(self):
        self.client.force_login(self.owner)
        payload = {"acepta_terminos": "on", "acepta_privacidad": "on"}
        self.client.post(reverse("legal_acceptance"), payload)
        self.client.post(reverse("legal_acceptance"), payload)
        self.assertEqual(AceptacionLegalTenant.objects.filter(tenant=self.tenant).count(), 2)

    def test_hash_del_documento_coincide_con_contenido(self):
        documento = DocumentoLegal.objects.get(tipo=DocumentoLegal.Tipo.TERMINOS, vigente=True)
        esperado = hashlib.sha256(documento.contenido.encode("utf-8")).hexdigest()
        self.assertEqual(documento.contenido_sha256, esperado)
