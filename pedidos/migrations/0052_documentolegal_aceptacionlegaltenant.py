import uuid

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("pedidos", "0051_supportreport"),
    ]

    operations = [
        migrations.CreateModel(
            name="DocumentoLegal",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("public_id", models.UUIDField(db_index=True, default=uuid.uuid4, editable=False, unique=True)),
                ("tipo", models.CharField(choices=[("TERMINOS", "Términos del servicio"), ("PRIVACIDAD", "Política de privacidad"), ("COOKIES", "Política de cookies")], db_index=True, max_length=20)),
                ("version", models.CharField(help_text="Versión visible, por ejemplo 1.0.", max_length=32)),
                ("titulo", models.CharField(max_length=160)),
                ("contenido_sha256", models.CharField(help_text="SHA-256 hexadecimal del contenido exacto publicado. No debe contener datos personales.", max_length=64)),
                ("vigente", models.BooleanField(db_index=True, default=False)),
                ("publicado_en", models.DateTimeField(blank=True, null=True)),
                ("creado_en", models.DateTimeField(auto_now_add=True)),
                ("actualizado_en", models.DateTimeField(auto_now=True)),
            ],
            options={
                "verbose_name": "Documento legal",
                "verbose_name_plural": "Documentos legales",
                "ordering": ["tipo", "-creado_en"],
            },
        ),
        migrations.CreateModel(
            name="AceptacionLegalTenant",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("public_id", models.UUIDField(db_index=True, default=uuid.uuid4, editable=False, unique=True)),
                ("actor_username", models.CharField(max_length=150)),
                ("actor_role", models.CharField(max_length=40)),
                ("version_snapshot", models.CharField(max_length=32)),
                ("contenido_sha256_snapshot", models.CharField(max_length=64)),
                ("request_id", models.UUIDField(blank=True, db_index=True, null=True)),
                ("aceptado_en", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("actor_usuario", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="foodback_legal_acceptances", to=settings.AUTH_USER_MODEL)),
                ("documento", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="aceptaciones_tenant", to="pedidos.documentolegal")),
                ("tenant", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="aceptaciones_legales", to="pedidos.tenant")),
            ],
            options={
                "verbose_name": "Aceptación legal de Tenant",
                "verbose_name_plural": "Aceptaciones legales de Tenant",
                "ordering": ["-aceptado_en"],
            },
        ),
        migrations.AddConstraint(
            model_name="documentolegal",
            constraint=models.UniqueConstraint(fields=("tipo", "version"), name="legal_doc_type_version_uniq"),
        ),
        migrations.AddConstraint(
            model_name="documentolegal",
            constraint=models.UniqueConstraint(condition=models.Q(("vigente", True)), fields=("tipo",), name="legal_doc_one_current_type"),
        ),
        migrations.AddConstraint(
            model_name="aceptacionlegaltenant",
            constraint=models.UniqueConstraint(fields=("tenant", "documento"), name="legal_accept_tenant_doc_uniq"),
        ),
        migrations.AddIndex(
            model_name="aceptacionlegaltenant",
            index=models.Index(fields=["tenant", "-aceptado_en"], name="legal_accept_tenant_dt_idx"),
        ),
        migrations.RunSQL(
            sql=r"""
                ALTER TABLE pedidos_aceptacionlegaltenant
                    ENABLE ROW LEVEL SECURITY;

                DROP POLICY IF EXISTS foodback_tenant_isolation
                    ON pedidos_aceptacionlegaltenant;

                CREATE POLICY foodback_tenant_isolation
                    ON pedidos_aceptacionlegaltenant
                    USING (
                        tenant_id = NULLIF(
                            current_setting('foodback.tenant_id', true),
                            ''
                        )::bigint
                    )
                    WITH CHECK (
                        tenant_id = NULLIF(
                            current_setting('foodback.tenant_id', true),
                            ''
                        )::bigint
                    );
            """,
            reverse_sql=r"""
                DROP POLICY IF EXISTS foodback_tenant_isolation
                    ON pedidos_aceptacionlegaltenant;
                ALTER TABLE pedidos_aceptacionlegaltenant
                    DISABLE ROW LEVEL SECURITY;
            """,
        ),
    ]
