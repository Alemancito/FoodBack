from django.contrib.auth import get_user_model
from django.contrib.sessions.backends.db import SessionStore
from django.test import RequestFactory, TestCase, override_settings

from .models import Membership, MembershipSucursal, Sucursal, Tenant
from .tenant_context import resolver_sucursal, resolver_tenant


@override_settings(
    FOODBACK_DEFAULT_TENANT_SLUG="",
    FOODBACK_BASE_DOMAIN="foodbacksv.com",
)
class Phase11TenantBootstrapAdversarialTests(TestCase):
    """Security Gate: sesiones antiguas/manipuladas deben fallar cerrado."""

    def setUp(self):
        self.factory = RequestFactory()
        User = get_user_model()

        self.user = User.objects.create_user(
            username="phase11_owner",
            password="test-only-password",
        )
        self.tenant_a = Tenant.objects.create(
            nombre="Tenant A",
            slug="phase11-a",
            habilitado=True,
        )
        self.tenant_b = Tenant.objects.create(
            nombre="Tenant B",
            slug="phase11-b",
            habilitado=True,
        )
        self.owner_a = Membership.objects.create(
            tenant=self.tenant_a,
            usuario=self.user,
            rol=Membership.ROLE_OWNER,
            activo=True,
        )
        self.sucursal_a = Sucursal.objects.create(
            tenant=self.tenant_a,
            nombre="A Central",
            slug="central-a",
            estado=Sucursal.Estado.ACTIVA,
        )
        self.sucursal_b = Sucursal.objects.create(
            tenant=self.tenant_b,
            nombre="B Central",
            slug="central-b",
            estado=Sucursal.Estado.ACTIVA,
        )

    def _request(self, user=None):
        request = self.factory.get(
            "/dashboard/",
            HTTP_HOST="127.0.0.1:8000",
        )
        request.user = user or self.user
        request.session = SessionStore()
        return request

    def test_tenant_ajeno_en_sesion_se_limpia_y_no_se_acepta(self):
        request = self._request()
        request.session["tenant_activo_public_id"] = str(
            self.tenant_b.public_id
        )

        tenant = resolver_tenant(request)

        self.assertEqual(tenant, self.tenant_a)
        self.assertNotIn("tenant_activo_public_id", request.session)

    def test_membership_desactivada_invalida_tenant_guardado(self):
        request = self._request()
        request.session["tenant_activo_public_id"] = str(
            self.tenant_a.public_id
        )
        self.owner_a.activo = False
        self.owner_a.save(update_fields=["activo"])

        tenant = resolver_tenant(request)

        self.assertIsNone(tenant)
        self.assertNotIn("tenant_activo_public_id", request.session)

    def test_tenant_deshabilitado_invalida_tenant_guardado(self):
        request = self._request()
        request.session["tenant_activo_public_id"] = str(
            self.tenant_a.public_id
        )
        self.tenant_a.habilitado = False
        self.tenant_a.save(update_fields=["habilitado"])

        tenant = resolver_tenant(request)

        self.assertIsNone(tenant)
        self.assertNotIn("tenant_activo_public_id", request.session)

    def test_sucursal_de_otro_tenant_en_sesion_se_limpia(self):
        request = self._request()
        request.session["sucursal_activa_public_id"] = str(
            self.sucursal_b.public_id
        )

        sucursal = resolver_sucursal(request, self.tenant_a)

        self.assertEqual(sucursal, self.sucursal_a)
        self.assertNotIn("sucursal_activa_public_id", request.session)

    def test_manager_que_pierde_asignacion_no_conserva_sucursal(self):
        manager_user = get_user_model().objects.create_user(
            username="phase11_manager",
            password="test-only-password",
        )
        manager = Membership.objects.create(
            tenant=self.tenant_a,
            usuario=manager_user,
            rol=Membership.ROLE_MANAGER,
            activo=True,
        )
        asignacion = MembershipSucursal.objects.create(
            membership=manager,
            sucursal=self.sucursal_a,
            activo=True,
        )
        request = self._request(user=manager_user)
        request.session["sucursal_activa_public_id"] = str(
            self.sucursal_a.public_id
        )

        self.assertEqual(
            resolver_sucursal(request, self.tenant_a),
            self.sucursal_a,
        )

        asignacion.activo = False
        asignacion.save(update_fields=["activo"])
        request.session["sucursal_activa_public_id"] = str(
            self.sucursal_a.public_id
        )

        sucursal = resolver_sucursal(request, self.tenant_a)

        self.assertIsNone(sucursal)
        self.assertNotIn("sucursal_activa_public_id", request.session)

    def test_usuario_autenticado_sin_membership_no_obtiene_tenant(self):
        outsider = get_user_model().objects.create_user(
            username="phase11_outsider",
            password="test-only-password",
        )
        request = self._request(user=outsider)

        self.assertIsNone(resolver_tenant(request))
