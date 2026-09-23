# FoodBack — Política interna de ciclo de vida y retención de datos

**Fase:** 10 — Legal, privacidad y ciclo de vida de datos
**Estado:** propuesta técnica interna v0.1
**Alcance:** define el comportamiento objetivo que después deberá reflejarse en código, tests y documentos públicos.

> Los plazos marcados como **POR DEFINIR** no deben publicarse ni hardcodearse hasta validar su fundamento operativo, fiscal, contractual y legal.

## 1. Objetivo

Evitar dos errores opuestos:

1. borrar información que FoodBack necesita legítimamente para operar, facturar, defender una transacción, investigar seguridad o cumplir una obligación;
2. conservar indefinidamente datos personales o técnicos que ya no tienen finalidad.

La retención se decide por **categoría de dato y finalidad**, no con un único “borrar todo al cancelar”.

## 2. Lifecycle del Tenant

FoodBack trabajará conceptualmente con los siguientes estados de ciclo de vida:

### ALTA / PRUEBA

- Se crea Tenant, OWNER y configuración mínima.
- Solo se solicitan datos necesarios para habilitar la prueba/contratación.
- La prueba no debe convertir automáticamente datos temporales en conservación indefinida.
- Duración comercial de prueba: **POR DEFINIR**.

### ACTIVO

- Servicio habilitado normalmente.
- Se generan datos operativos, pedidos, pagos, auditoría y soporte según uso.
- Aplican los plazos normales de cada categoría.

### GRACIA

- Estado ya soportado por `SuscripcionTenant`.
- Existe para resolver impagos/renovaciones fallidas sin apagar inmediatamente un restaurante por un error puntual.
- Duración: **POR DEFINIR**.
- No implica borrado ni pérdida de datos.
- Debe restringirse únicamente lo necesario conforme a la política comercial final.

### SUSPENDIDO

- El servicio operativo puede quedar limitado por impago u otra causa contractual válida.
- Los datos no se borran por el simple hecho de suspender.
- Debe mantenerse acceso suficiente para acciones que legal/contractualmente correspondan, como regularización, soporte o exportación cuando aplique.
- Duración máxima antes de pasar a cancelación/retención: **POR DEFINIR**.

### CANCELADO

- No se generan nuevas renovaciones.
- Si existe un período ya pagado y la política/ley permite conservar acceso hasta su vencimiento, la cancelación comercial y el fin del acceso efectivo pueden ser fechas distintas.
- Se congela el crecimiento de datos salvo soporte, facturación, seguridad o exportación necesarios.
- Comienza la ventana de exportación/retención post-cancelación: **POR DEFINIR**.

### RETENCIÓN POST-CANCELACIÓN

- Se separan los datos que pueden eliminarse pronto de los que deben conservarse por razones fiscales, contractuales, antifraude, seguridad o resolución de disputas.
- El Tenant no debe reaparecer como activo por mantener evidencia histórica.
- Las credenciales de personal deben quedar deshabilitadas cuando ya no sean necesarias.

### PURGA / ANONIMIZACIÓN

- Se eliminan datos sin finalidad vigente.
- Cuando un registro deba permanecer por integridad, estadísticas o evidencia, se evalúa anonimización/minimización en lugar de conservar identificadores personales innecesarios.
- La purga debe ser reproducible, auditable y probada.
- No se ejecutará automáticamente hasta contar con implementación y tests específicos.

## 3. Matriz de retención

| Categoría | Mientras Tenant está activo | Después de cancelación | Política actual/objetivo |
|---|---|---|---|
| Tenant/sucursal/configuración | Necesario | Ventana de cierre + evidencia mínima | POR DEFINIR |
| Identidad de staff/memberships | Necesario mientras exista relación | Deshabilitar; posteriormente minimizar/purgar | POR DEFINIR |
| Recuperación de contraseña | Solo seguridad temporal | No necesita conservación indefinida | TTL funcional existe; limpieza histórica POR DEFINIR |
| Cliente final | Para pedidos/recompra según finalidad | Minimizar/anonymizar cuando deje de ser necesario | POR DEFINIR |
| Dirección última del cliente | Conveniencia de entrega | Mayor prioridad de minimización por ser ubicación | POR DEFINIR |
| Pedido | Operación, soporte, reportes y evidencia | Conservar lo requerido; luego anonimizar/purgar identificadores cuando proceda | POR DEFINIR |
| Coordenadas de entrega | Solo para operación/soporte razonable | No conservar indefinidamente por defecto | POR DEFINIR — prioridad alta |
| Pago Wompi normalizado | Conciliación/soporte/evidencia | Conservar según necesidad fiscal/contractual | POR DEFINIR |
| Payloads `raw_*` Wompi | Actualmente se guardan | Deben minimizarse | **REVISIÓN OBLIGATORIA F10** |
| EventoPagoWompi sanitizado | Diagnóstico/seguridad | Conservar período razonable | POR DEFINIR |
| AuditEvent | Seguridad/auditoría | Igual | **180 días por defecto** |
| SecurityIncident activo | Hasta resolución | No purgar si sigue activo | Ya implementado |
| SecurityIncident resuelto | Investigación histórica | Igual | **365 días por defecto** |
| SupportReport abierto/en revisión | Resolver el caso | Mantener mientras sea necesario | POR DEFINIR |
| SupportReport resuelto | Evidencia de soporte | Retención limitada | POR DEFINIR |
| Rate-limit buckets | Solo ventana antiabuso | Expirar/purgar | Conforme a ventanas técnicas; política de limpieza a revisar |
| Backup PostgreSQL local | DR | Ventana limitada | **14 días** según diseño DR |
| Media Cloudinary | Operación | Seguir lifecycle del contenido/Tenant | POR DEFINIR + DR pendiente |

## 4. Regla para datos fiscales y contractuales

FoodBack no debe usar el derecho de eliminación como excusa para borrar evidencia que exista obligación legítima de conservar. A la vez, la existencia de una obligación sobre una factura o transacción no justifica conservar para siempre todos los datos personales relacionados.

Ejemplo de diseño:

- conservar referencia de transacción, importe, fecha, documento/evidencia necesaria;
- eliminar o anonimizar datos de ubicación u otros identificadores que ya no sean necesarios para esa obligación;
- documentar la razón concreta de cada excepción a la purga.

El plazo fiscal/legal exacto se fijará con fuente oficial y/o contador antes de automatizarlo.

## 5. Exportación al cancelar

Antes de producción comercial debe existir una política definida para exportación. Como mínimo se evaluará exportar información del propio restaurante en formatos reutilizables (por ejemplo CSV/XLSX según el módulo), sin incluir:

- secretos de FoodBack;
- datos de otros Tenants;
- información interna de detección de seguridad que facilite abuso;
- datos que el restaurante no tenga derecho a recibir.

La exportación debe estar autorizada server-side por el OWNER o por un flujo Foundation controlado y quedar auditada.

**Ventana de exportación post-cancelación:** POR DEFINIR.

## 6. Borrado vs anonimización

### Borrado

Usar cuando el registro ya no tiene finalidad y puede eliminarse sin romper obligaciones o integridad necesaria.

### Anonimización/minimización

Preferible cuando FoodBack necesita conservar agregados o evidencia operacional pero ya no necesita identificar a una persona.

Ejemplos candidatos futuros:

- retirar nombre/teléfono/dirección/coordenadas de pedidos antiguos cuando legal y técnicamente proceda;
- conservar importe, fecha, productos y sucursal para estadísticas agregadas;
- conservar identificadores financieros mínimos requeridos para conciliación/evidencia.

No implementar anonimización irreversible hasta definir dependencias y tests.

## 7. Backups

Un borrado en la base primaria no implica editar retrospectivamente cada backup.

Política objetivo:

1. el dato se elimina/anonymiza de la base activa según su lifecycle;
2. backups existentes permanecen aislados hasta vencer su propia retención;
3. un backup restaurado para DR debe volver a someterse a los procesos de purga vencidos antes de utilizarse como base operativa normal cuando sea viable;
4. los backups no se usan como archivo histórico para evadir una solicitud de eliminación.

La retención local actual es 14 días. La política offsite se definirá cuando exista el destino real.

## 8. Wompi — gate de minimización

Antes de cerrar Fase 10 se debe inspeccionar la escritura y lectura de:

- `PagoWompi.raw_creacion`;
- `PagoWompi.raw_redirect`;
- `PagoWompi.raw_webhook`;
- `PagoWompi.ultimo_error`.

Objetivo:

- conservar identificadores, estados, montos y campos estrictamente útiles;
- evitar guardar payload completo cuando bastan campos seleccionados;
- sanitizar mensajes de error;
- asegurar que nunca persistan tarjeta, CVV, Authorization, API keys, cookies o secretos;
- añadir tests que fallen si reaparecen claves sensibles conocidas.

Hasta completar esta revisión, estos campos se consideran **riesgo de minimización pendiente**, no una vulnerabilidad confirmada.

## 9. Soporte

`SupportReport` ya minimiza automáticamente el contexto: no copia body, headers completos, cookies, traceback ni excepción cruda.

Pendiente:

- plazo para tickets resueltos;
- política para mensajes voluntarios que contengan accidentalmente información sensible;
- mecanismo de redacción/eliminación administrativa sin perder trazabilidad básica del ticket.

La advertencia al usuario debe continuar indicando que no escriba contraseñas, códigos ni datos de tarjeta.

## 10. Privacidad y cookies públicas

La página pública no debe prometer más de lo implementado.

Cuando se redacte, deberá diferenciar al menos:

- cookies estrictamente necesarias de sesión/CSRF/seguridad;
- cualquier cookie analítica o de marketing futura;
- proveedores externos realmente activos;
- finalidades de datos de cliente final vs personal del restaurante;
- conservación y mecanismo de ejercicio de derechos;
- contacto de privacidad configurable, sin hardcodear un dominio aún no comprado.

Si en V1 no existen cookies de marketing/analítica no necesarias, no añadir por costumbre un consentimiento de categorías inexistentes.

## 11. Reglas de implementación

1. Ninguna purga masiva sin `--dry-run` o mecanismo equivalente durante su introducción.
2. Procesar en lotes para no bloquear la base.
3. Filtrar explícitamente por estado/fecha y probar límites temporales.
4. Nunca permitir que un Tenant purgue datos de otro.
5. Acciones Foundation de exportación/purga deben generar auditoría.
6. Los plazos deben vivir en configuración cuando tenga sentido, con límites seguros.
7. Cambiar un plazo requiere tests y actualización de esta política.
8. Primero tests dirigidos; después suite completa.

## 12. Decisiones pendientes antes del cierre de Fase 10

- [ ] Plazo post-cancelación antes de purga del Tenant.
- [ ] Ventana de exportación.
- [ ] Retención de Cliente/dirección.
- [ ] Retención/anominización de Pedido y coordenadas.
- [ ] Retención de pagos/eventos Wompi y tratamiento fiscal exacto.
- [ ] Minimización de `raw_*` Wompi.
- [ ] Retención de SupportReport resuelto.
- [ ] Limpieza histórica de PasswordResetChallenge.
- [ ] Lifecycle de cuentas de staff tras cancelación.
- [ ] Política media/Cloudinary.
- [ ] Aceptación versionada de Términos/Privacidad.
- [ ] Texto público de Privacidad/Cookies/Términos alineado con la implementación.

## 13. Criterio de cierre de este bloque

Este bloque se considera documentado cuando:

1. el inventario representa los modelos/datos reales;
2. ningún plazo no confirmado se presenta como obligación jurídica;
3. los riesgos pendientes están explícitos;
4. las siguientes modificaciones de código pueden derivarse de esta matriz sin inventar decisiones sobre la marcha.
