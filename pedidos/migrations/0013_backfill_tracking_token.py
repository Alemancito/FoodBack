import uuid

from django.db import migrations


def generar_tracking_tokens(apps, schema_editor):
    Pedido = apps.get_model("pedidos", "Pedido")

    pedidos_sin_token = Pedido.objects.filter(
        tracking_token__isnull=True
    )

    for pedido in pedidos_sin_token.iterator():
        pedido.tracking_token = uuid.uuid4()
        pedido.save(update_fields=["tracking_token"])


class Migration(migrations.Migration):

    dependencies = [
        ("pedidos", "0012_add_tracking_token_nullable"),
    ]

    operations = [
        migrations.RunPython(
            generar_tracking_tokens,
            migrations.RunPython.noop,
        ),
    ]