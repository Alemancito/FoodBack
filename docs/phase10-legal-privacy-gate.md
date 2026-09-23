# FoodBack — Fase 10: gate legal, privacidad y ciclo de vida

**Estado objetivo:** cierre técnico/provider-agnostic de Fase 10  
**Dependencia siguiente:** Fase 11 — Security Gate adversarial

## Alcance cerrado en Fase 10

1. Inventario real de datos y clasificación interna.
2. Política interna de retención/ciclo de vida sin inventar plazos legales no confirmados.
3. Minimización de payloads persistidos de Wompi mediante allow-list.
4. Documentos legales versionados con contenido exacto y SHA-256.
5. Centro público para Términos, Privacidad, Cookies, Seguridad y Soporte.
6. Evidencia de aceptación contractual por Tenant restringida a OWNER.
7. Aceptación idempotente con snapshots de versión, hash, actor, rol, fecha y request_id.
8. Auditoría de aceptación sin guardar IP, cookies, headers ni body.

## Decisiones deliberadamente no hardcodeadas

- Nombre comercial/dominio definitivos.
- Planes y precios.
- Porcentajes de comisiones de proveedor.
- Duración exacta del grace period.
- Umbrales automáticos de créditos/devoluciones por indisponibilidad.
- Plazos de purga de pedidos, clientes y evidencia fiscal que todavía requieran validación normativa/contable.
- Correos públicos de privacidad, seguridad y soporte hasta tener dominio definitivo.

## Política de cookies en V1 actual

La aplicación documenta cookies técnicas necesarias para sesión, autenticación, CSRF y continuidad de navegación. Analítica no esencial, marketing o publicidad no forman parte de la política actual. Si se incorporan posteriormente, deberán revisarse la política, la clasificación y el consentimiento antes de activarlas.

## Lifecycle del Tenant

El lifecycle definido sigue siendo:

`ALTA/PRUEBA -> ACTIVO -> GRACIA -> SUSPENDIDO -> CANCELADO -> RETENCIÓN -> PURGA/ANONIMIZACIÓN`

Fase 10 define la semántica y separación de datos. La automatización comercial de planes, cobro, grace period y suspensión se implementará cuando exista el módulo de billing/planes, sin reinterpretar esta política.

## Deferidos externos legítimos

No bloquean el cierre técnico de Fase 10, pero deben resolverse antes del primer contrato comercial real cuando apliquen:

- revisión final de textos públicos por profesional legal;
- tratamiento fiscal definitivo de proveedores extranjeros con contador;
- identidad/dominio/canales de contacto definitivos;
- validaciones reales de producción/Railway que pertenecen a gates de infraestructura ya deferidos.

## Condición para declarar Fase 10 cerrada

- migraciones aplicadas con rol migrator;
- tests nuevos dirigidos en verde;
- suite completa en verde;
- `python manage.py check` sin issues;
- `git diff --check` limpio;
- Git revisado y commit/push realizado;
- prueba manual mínima del Centro Legal y del flujo OWNER de aceptación.

Después de cumplir lo anterior, el siguiente trabajo es Fase 11 — Security Gate. No abrir Features V1 antes de superar ese gate.
