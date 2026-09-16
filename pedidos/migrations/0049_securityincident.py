# Generated for FoodBack Phase 7 incident correlation foundation.

import uuid

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        (
            "pedidos",
            "0048_auditevent",
        ),
    ]

    operations = [
        migrations.CreateModel(
            name="SecurityIncident",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "public_id",
                    models.UUIDField(
                        db_index=True,
                        default=uuid.uuid4,
                        editable=False,
                        unique=True,
                    ),
                ),
                (
                    "fingerprint",
                    models.CharField(
                        db_index=True,
                        max_length=64,
                    ),
                ),
                (
                    "evento_clave",
                    models.CharField(
                        max_length=120,
                    ),
                ),
                (
                    "categoria",
                    models.CharField(
                        choices=[
                            ("AUTENTICACION", "Autenticación"),
                            ("AUTORIZACION", "Autorización"),
                            ("SEGURIDAD", "Seguridad"),
                            ("CUENTA", "Cuenta"),
                            ("ADMINISTRACION", "Administración"),
                            ("PAGOS", "Pagos"),
                            ("SISTEMA", "Sistema"),
                            ("SOPORTE", "Soporte"),
                        ],
                        max_length=24,
                    ),
                ),
                (
                    "severidad",
                    models.CharField(
                        choices=[
                            ("INFO", "Informativa"),
                            ("BAJA", "Baja"),
                            ("MEDIA", "Media"),
                            ("ALTA", "Alta"),
                            ("CRITICA", "Crítica"),
                        ],
                        db_index=True,
                        default="MEDIA",
                        max_length=16,
                    ),
                ),
                (
                    "estado",
                    models.CharField(
                        choices=[
                            ("ABIERTO", "Abierto"),
                            ("RECONOCIDO", "Reconocido"),
                            ("RESUELTO", "Resuelto"),
                        ],
                        db_index=True,
                        default="ABIERTO",
                        max_length=16,
                    ),
                ),
                (
                    "titulo",
                    models.CharField(
                        max_length=180,
                    ),
                ),
                (
                    "descripcion",
                    models.CharField(
                        blank=True,
                        max_length=255,
                    ),
                ),
                (
                    "actor_username",
                    models.CharField(
                        blank=True,
                        max_length=150,
                    ),
                ),
                (
                    "actor_role",
                    models.CharField(
                        blank=True,
                        max_length=40,
                    ),
                ),
                (
                    "tenant_public_id",
                    models.UUIDField(
                        blank=True,
                        null=True,
                    ),
                ),
                (
                    "tenant_nombre",
                    models.CharField(
                        blank=True,
                        max_length=150,
                    ),
                ),
                (
                    "sucursal_public_id",
                    models.UUIDField(
                        blank=True,
                        null=True,
                    ),
                ),
                (
                    "sucursal_nombre",
                    models.CharField(
                        blank=True,
                        max_length=150,
                    ),
                ),
                (
                    "ip",
                    models.GenericIPAddressField(
                        blank=True,
                        null=True,
                    ),
                ),
                (
                    "ruta",
                    models.CharField(
                        blank=True,
                        max_length=500,
                    ),
                ),
                (
                    "contador_eventos",
                    models.PositiveIntegerField(
                        default=1,
                    ),
                ),
                (
                    "primero_visto_en",
                    models.DateTimeField(
                        db_index=True,
                    ),
                ),
                (
                    "ultimo_visto_en",
                    models.DateTimeField(
                        db_index=True,
                    ),
                ),
                (
                    "ultima_notificacion_en",
                    models.DateTimeField(
                        blank=True,
                        null=True,
                    ),
                ),
                (
                    "primera_notificacion_exitosa_en",
                    models.DateTimeField(
                        blank=True,
                        null=True,
                    ),
                ),
                (
                    "ultima_notificacion_exitosa_en",
                    models.DateTimeField(
                        blank=True,
                        null=True,
                    ),
                ),
                (
                    "notificaciones_enviadas",
                    models.PositiveIntegerField(
                        default=0,
                    ),
                ),
                (
                    "fallos_notificacion",
                    models.PositiveIntegerField(
                        default=0,
                    ),
                ),
                (
                    "ultimo_error_notificacion",
                    models.CharField(
                        blank=True,
                        max_length=255,
                    ),
                ),
                (
                    "reconocido_en",
                    models.DateTimeField(
                        blank=True,
                        null=True,
                    ),
                ),
                (
                    "resuelto_en",
                    models.DateTimeField(
                        blank=True,
                        null=True,
                    ),
                ),
                (
                    "creado_en",
                    models.DateTimeField(
                        auto_now_add=True,
                    ),
                ),
                (
                    "actualizado_en",
                    models.DateTimeField(
                        auto_now=True,
                    ),
                ),
                (
                    "evento_inicial",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=(
                            django.db.models.deletion.SET_NULL
                        ),
                        related_name=(
                            "incidentes_como_evento_inicial"
                        ),
                        to="pedidos.auditevent",
                    ),
                ),
                (
                    "evento_ultimo",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=(
                            django.db.models.deletion.SET_NULL
                        ),
                        related_name=(
                            "incidentes_como_evento_ultimo"
                        ),
                        to="pedidos.auditevent",
                    ),
                ),
            ],
            options={
                "verbose_name": "Incidente de seguridad",
                "verbose_name_plural": "Incidentes de seguridad",
                "ordering": [
                    "-ultimo_visto_en",
                ],
                "indexes": [
                    models.Index(
                        fields=[
                            "estado",
                            "severidad",
                            "-ultimo_visto_en",
                        ],
                        name="secinc_state_sev_last_idx",
                    ),
                    models.Index(
                        fields=[
                            "fingerprint",
                            "-ultimo_visto_en",
                        ],
                        name="secinc_fprint_last_idx",
                    ),
                    models.Index(
                        fields=[
                            "tenant_public_id",
                            "-ultimo_visto_en",
                        ],
                        name="secinc_tenant_last_idx",
                    ),
                    models.Index(
                        fields=[
                            "evento_clave",
                            "-ultimo_visto_en",
                        ],
                        name="secinc_event_last_idx",
                    ),
                ],
                "constraints": [
                    models.CheckConstraint(
                        check=models.Q(
                            ("contador_eventos__gte", 1)
                        ),
                        name="security_incident_count_gte_1",
                    ),
                    models.UniqueConstraint(
                        condition=models.Q(
                            ("estado__in", [
                                "ABIERTO",
                                "RECONOCIDO",
                            ])
                        ),
                        fields=("fingerprint",),
                        name=(
                            "security_incident_active_"
                            "fprint_uniq"
                        ),
                    ),
                ],
            },
        ),
    ]
