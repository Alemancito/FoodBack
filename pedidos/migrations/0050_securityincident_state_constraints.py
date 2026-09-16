from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        (
            "pedidos",
            "0049_securityincident",
        ),
    ]

    operations = [
        migrations.AddConstraint(
            model_name="securityincident",
            constraint=models.CheckConstraint(
                check=(
                    ~models.Q(
                        estado="RECONOCIDO",
                    )
                    | models.Q(
                        reconocido_en__isnull=False,
                    )
                ),
                name="secinc_ack_requires_timestamp",
            ),
        ),
        migrations.AddConstraint(
            model_name="securityincident",
            constraint=models.CheckConstraint(
                check=(
                    ~models.Q(
                        estado="RESUELTO",
                    )
                    | models.Q(
                        resuelto_en__isnull=False,
                    )
                ),
                name="secinc_resolved_requires_timestamp",
            ),
        ),
        migrations.AddConstraint(
            model_name="securityincident",
            constraint=models.CheckConstraint(
                check=(
                    models.Q(
                        estado="RESUELTO",
                    )
                    | models.Q(
                        resuelto_en__isnull=True,
                    )
                ),
                name="secinc_active_no_resolved_ts",
            ),
        ),
    ]
