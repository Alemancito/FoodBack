import hashlib

from django.db import migrations, models
from django.utils import timezone


TERMINOS = """# Términos del servicio

1. Alcance
FoodBack es una plataforma de software para apoyar la gestión de pedidos y operaciones de restaurantes. El acceso se presta como servicio; el cliente no adquiere el código fuente, la infraestructura ni la propiedad intelectual de la plataforma.

2. Cuenta y autoridad
El restaurante es responsable de mantener actualizados sus usuarios, roles y accesos. Las acciones administrativas deben realizarse por personas autorizadas. FoodBack puede aplicar controles de seguridad, límites de uso y medidas de protección cuando detecte actividad anómala o no autorizada.

3. Suscripción, precio e impuestos
Los planes, precios, beneficios, periodicidad y límites aplicables serán los mostrados o acordados al contratar. Los precios definitivos no se consideran fijados hasta su publicación comercial. Los impuestos, retenciones y documentos fiscales se manejarán conforme a la legislación aplicable y a la condición tributaria correspondiente.

4. Renovación, cancelación e impago
Salvo que el plan indique otra cosa, la intención comercial es cobrar al inicio del período. Una cancelación voluntaria detiene renovaciones futuras y puede mantener el acceso hasta finalizar el período ya pagado. El impago puede llevar a un período de gracia y posteriormente a suspensión; suspensión no significa eliminación inmediata de datos. Los plazos concretos se publicarán antes de ofrecer planes comerciales.

5. Disponibilidad y mantenimiento
FoodBack busca operar de forma continua, pero no promete disponibilidad absoluta. Pueden existir mantenimientos, fallas de infraestructura, proveedores externos, Internet u otros eventos fuera del control razonable de la plataforma. FoodBack gestionará los incidentes, comunicará los relevantes y aplicará los remedios comerciales que correspondan al caso y a la ley aplicable.

6. Datos y exportación
El restaurante conserva la responsabilidad sobre los datos que incorpora al servicio dentro de su operación. FoodBack aplica controles de aislamiento entre restaurantes, seguridad, retención y minimización de datos. Ante cancelación o terminación, la exportación y conservación se regirán por la política vigente, obligaciones legales y posibilidades técnicas razonables.

7. Uso aceptable
No se permite usar FoodBack para vulnerar sistemas, evadir controles de acceso, introducir contenido malicioso, interferir con otros clientes, procesar información ilícita o realizar actividades contrarias a la ley.

8. Proveedores externos
La plataforma puede apoyarse en servicios externos para alojamiento, pagos, mapas, medios, correo u otras funciones. FoodBack procura mantenerlos desacoplados cuando sea razonable y no traslada al cliente la responsabilidad de gestionar directamente incidentes de dichos proveedores.

9. Cambios de términos
Las modificaciones materiales se publicarán como una nueva versión. Cuando corresponda, FoodBack solicitará una nueva aceptación y conservará evidencia de la versión aceptada, fecha y actor autorizado.

10. Soporte y seguridad
Los incidentes técnicos pueden identificarse mediante referencias o request IDs para facilitar soporte. Nunca deben enviarse contraseñas, códigos de acceso, OTP, números completos de tarjeta, CVV, tokens ni otros secretos en solicitudes de soporte.
"""

PRIVACIDAD = """# Política de privacidad

1. Qué datos trata FoodBack
Según la función utilizada, FoodBack puede tratar datos de clientes finales, personal del restaurante y responsables de cuenta, incluyendo nombres, teléfonos, direcciones de entrega, coordenadas asociadas al pedido, pedidos, estados de pago, usuarios, roles, registros de seguridad, auditoría y soporte.

2. Para qué se utilizan
Los datos se usan para prestar el servicio, procesar y dar seguimiento a pedidos, administrar accesos, operar pagos, prevenir abuso, investigar incidentes, brindar soporte, mantener evidencia contractual y cumplir obligaciones aplicables.

3. Minimización
FoodBack procura conservar únicamente la información necesaria para cada finalidad. Las integraciones externas no se almacenan de forma indiscriminada: los datos de proveedor que se conserven deben limitarse a campos operativos necesarios para conciliación, soporte, auditoría o cumplimiento.

4. Aislamiento y acceso
FoodBack utiliza separación por restaurante y sucursal, controles de roles y medidas adicionales de base de datos. El acceso interno debe corresponder a una necesidad operativa o de soporte autorizada.

5. Proveedores
Algunas funciones pueden depender de proveedores de alojamiento, pagos, mapas, almacenamiento de medios, correo u otros servicios. Se procura limitar los datos compartidos a lo necesario para la función correspondiente y mantener la posibilidad de migrar de proveedor cuando sea razonable.

6. Conservación
Los períodos de conservación dependen de la categoría de datos y su finalidad. La suspensión de una cuenta no implica borrado inmediato. Algunas categorías pueden conservarse durante más tiempo por obligaciones fiscales, contractuales, de seguridad, auditoría, resolución de disputas o respaldo. FoodBack mantiene una política interna de ciclo de vida y revisa los plazos antes de automatizar purgas.

7. Derechos y solicitudes
Las personas pueden solicitar información, corrección, actualización, oposición, eliminación u otras actuaciones que correspondan bajo la normativa aplicable. El canal formal de privacidad se publicará con la identidad y dominio definitivos de la plataforma. Mientras el producto esté en etapa previa a comercialización, estas solicitudes se gestionan mediante el canal contractual acordado con el restaurante.

8. Seguridad
FoodBack aplica medidas técnicas y organizativas orientadas a proteger confidencialidad, integridad y disponibilidad. Ningún sistema puede garantizar riesgo cero; por ello también se mantienen registros, detección de incidentes, recuperación y procedimientos de soporte.

9. Cambios
Las modificaciones materiales se publicarán como una nueva versión de esta política. Cuando corresponda, se conservará evidencia de la versión aceptada por el responsable autorizado del restaurante.
"""

COOKIES = """# Política de cookies

1. Cookies técnicas necesarias
FoodBack utiliza cookies o mecanismos equivalentes necesarios para funciones como sesión, autenticación, seguridad CSRF, continuidad de navegación y preferencias técnicas. Estas funciones son necesarias para operar de forma segura.

2. Analítica y marketing
La versión actual no define como parte del producto cookies publicitarias o de marketing. Si en el futuro se incorporan analítica no esencial, publicidad u otras categorías similares, esta política y la experiencia de consentimiento se actualizarán antes de activarlas cuando corresponda.

3. Duración
La duración depende del mecanismo utilizado. Algunas cookies existen únicamente durante la sesión y otras pueden persistir durante el tiempo necesario para una función de seguridad o preferencia.

4. Control del navegador
El navegador permite borrar o bloquear cookies. Bloquear cookies técnicas necesarias puede impedir iniciar sesión, conservar una sesión o completar algunas funciones de FoodBack.

5. Cambios
Los cambios materiales de esta política se publicarán como una nueva versión.
"""


def _hash(texto):
    return hashlib.sha256(texto.encode("utf-8")).hexdigest()


def seed(apps, schema_editor):
    DocumentoLegal = apps.get_model("pedidos", "DocumentoLegal")
    ahora = timezone.now()
    docs = [
        ("TERMINOS", "Términos del servicio", TERMINOS),
        ("PRIVACIDAD", "Política de privacidad", PRIVACIDAD),
        ("COOKIES", "Política de cookies", COOKIES),
    ]
    for tipo, titulo, contenido in docs:
        DocumentoLegal.objects.filter(tipo=tipo, vigente=True).update(vigente=False)
        DocumentoLegal.objects.update_or_create(
            tipo=tipo,
            version="1.0",
            defaults={
                "titulo": titulo,
                "contenido": contenido,
                "contenido_sha256": _hash(contenido),
                "vigente": True,
                "publicado_en": ahora,
            },
        )


def unseed(apps, schema_editor):
    DocumentoLegal = apps.get_model("pedidos", "DocumentoLegal")
    DocumentoLegal.objects.filter(version="1.0").delete()


class Migration(migrations.Migration):
    dependencies = [("pedidos", "0052_documentolegal_aceptacionlegaltenant")]

    operations = [
        migrations.AddField(
            model_name="documentolegal",
            name="contenido",
            field=models.TextField(
                default="",
                help_text=(
                    "Contenido público exacto de esta versión. "
                    "Su SHA-256 debe coincidir con contenido_sha256."
                ),
            ),
            preserve_default=False,
        ),
        migrations.RunPython(seed, unseed),
    ]
