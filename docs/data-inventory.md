# FoodBack — Inventario de datos

**Fase:** 10 — Legal, privacidad y ciclo de vida de datos
**Estado:** borrador técnico interno v0.1
**Objetivo:** describir los datos que FoodBack realmente almacena o procesa hoy antes de redactar Términos, Privacidad y Cookies.

> Este documento describe el estado técnico actual. No sustituye asesoría legal ni fija por sí solo plazos fiscales o legales. Cuando un plazo dependa de normativa o de una decisión comercial aún no confirmada, se marca como **POR DEFINIR**.

## 1. Principios

1. Recopilar solo datos necesarios para operar, cobrar, dar soporte, proteger la plataforma y cumplir obligaciones aplicables.
2. Backend y base de datos son la autoridad; el navegador no decide tenant, sucursal, actor, permisos ni estados sensibles.
3. Separar datos de negocio por Tenant/Sucursal de los datos globales de control-plane.
4. No conservar secretos o datos de pago sensibles “por si acaso”.
5. No usar datos de un Tenant para otro Tenant.
6. No prometer públicamente borrados, exportaciones o plazos que el sistema todavía no pueda cumplir técnicamente.

## 2. Clasificación usada

- **Operativo:** necesario para prestar el servicio cotidiano.
- **Personal:** identifica o puede identificar a una persona.
- **Ubicación:** dirección o coordenadas relacionadas con una entrega/persona.
- **Financiero/transaccional:** importes, referencias y estados de pago.
- **Seguridad:** datos usados para autenticación, detección, investigación o prevención de abuso.
- **Soporte:** datos aportados para investigar un problema.
- **Configuración comercial:** información del restaurante, sucursal o suscripción.
- **Control-plane:** información global necesaria para administrar FoodBack como plataforma.

## 3. Inventario por dominio

### 3.1 Tenant y sucursales

**Modelos principales:** `Tenant`, `Sucursal`, `ConfiguracionNegocio`, `SuscripcionTenant`.

| Datos | Finalidad | Ámbito | Sensibilidad | Acceso esperado | Retención actual |
|---|---|---|---|---|---|
| Nombre y slug del Tenant | Identificar al restaurante dentro de FoodBack | Tenant | Baja | Plataforma + personal autorizado | Sin política explícita |
| Estado/habilitación | Control administrativo | Control-plane/Tenant | Baja | Plataforma | Sin política explícita |
| Nombre/slug/estado de sucursal | Operación multi-sucursal | Tenant/Sucursal | Baja | Personal autorizado | Sin política explícita |
| Horarios y configuración del negocio | Operar menú, pedidos y disponibilidad | Sucursal | Baja | Personal autorizado | Sin política explícita |
| Estado de suscripción, vencimiento y `gracia_hasta` | Billing y control de acceso SaaS | Tenant | Comercial | OWNER + plataforma | Sin política explícita |

**Observación:** `SuscripcionTenant` ya define `ACTIVA`, `GRACIA`, `SUSPENDIDA` y `CANCELADA`, pero la automatización del lifecycle fue pospuesta hasta Fase 10.

### 3.2 Personal del restaurante y autenticación

**Modelos principales:** Django `User`, `StaffIdentity`, `Membership`, `MembershipSucursal`, `RepartidorSucursal`, `PasswordResetChallenge`.

| Datos | Finalidad | Ámbito | Sensibilidad | Acceso esperado | Retención actual |
|---|---|---|---|---|---|
| Username y datos de cuenta Django | Inicio de sesión e identidad | Global + relaciones Tenant | Personal | Usuario + admins autorizados | Sin política explícita |
| Email de `StaffIdentity` | Identidad/recuperación | Global | Personal | Usuario + plataforma | Sin política explícita |
| Rol y memberships | Autorización | Tenant/Sucursal | Seguridad | Backend + admins autorizados | Mientras exista relación; purga no definida |
| Hash del código de recuperación | Recuperación de contraseña | Global | Seguridad alta | Backend | Temporal por diseño; limpieza histórica POR DEFINIR |
| Expiración, intentos, usado/completado | Prevención de abuso e investigación | Global | Seguridad | Backend/plataforma | POR DEFINIR |

**No se persiste el código OTP real**; `PasswordResetChallenge` almacena un hash.

### 3.3 Clientes finales del restaurante

**Modelo principal:** `Cliente`.

| Campo/dato | Finalidad | Ámbito | Sensibilidad | Acceso esperado | Retención actual |
|---|---|---|---|---|---|
| `telefono` | Identificar/reconocer al cliente dentro del Tenant y contacto operacional | Tenant | Personal | Restaurante autorizado + backend | Sin política explícita |
| `nombre`, `apellido` | Identificación del pedido | Tenant | Personal | Restaurante autorizado + backend | Sin política explícita |
| `direccion_ultima` | Facilitar entrega/recompra | Tenant | Personal + ubicación | Restaurante autorizado + backend | Sin política explícita |

**Restricción multi-tenant:** el teléfono es único por Tenant, no globalmente.

### 3.4 Pedidos y entrega

**Modelos principales:** `Pedido`, `DetallePedido` y relaciones de productos/extras.

| Datos | Finalidad | Ámbito | Sensibilidad | Acceso esperado | Retención actual |
|---|---|---|---|---|---|
| Cliente asociado | Cumplir pedido e historial | Sucursal/Tenant | Personal | Restaurante + cliente por flujo autorizado | Sin política explícita |
| Dirección de entrega | Realizar delivery | Sucursal/Tenant | Personal + ubicación | Operación/delivery | Sin política explícita |
| Latitud/longitud | Validar/ejecutar entrega | Sucursal/Tenant | Ubicación | Operación/delivery | Sin política explícita |
| Fecha, estado, método de pago | Operación e historial | Sucursal/Tenant | Operativo | Restaurante + backend | Sin política explícita |
| Totales del pedido | Cobro, conciliación y reportes | Sucursal/Tenant | Financiero | Restaurante + plataforma según permisos | Sin política explícita |
| Repartidor | Asignación operacional | Sucursal | Personal interno | Operación autorizada | Sin política explícita |
| `tracking_token` | Acceso seguro al seguimiento del pedido | Pedido | Seguridad | Cliente/backend | Sin política explícita |
| Referencias Wompi en Pedido | Enlace/confirmación de pago | Pedido | Financiero | Backend + personal autorizado | Sin política explícita |

**Punto de privacidad:** las coordenadas no deben conservarse indefinidamente por defecto sin una finalidad vigente. El plazo debe fijarse antes de V1.1 tracking avanzado.

### 3.5 Pagos Wompi

**Modelos principales:** `PagoWompi`, `EventoPagoWompi`, `EstadoPasarelaPago`.

| Datos | Finalidad | Ámbito | Sensibilidad | Acceso esperado | Retención actual |
|---|---|---|---|---|---|
| Referencia, id de enlace/transacción | Conciliación y confirmación | Tenant | Financiero | Backend/plataforma | Sin política explícita |
| Monto, estado, aprobación y fechas | Cobro/conciliación | Tenant | Financiero | Backend/plataforma | Sin política explícita |
| `cliente_token_hash` | Correlación segura sin guardar token crudo | Tenant | Seguridad | Backend | Sin política explícita |
| `raw_creacion` | Respuesta completa guardada al crear pago | Tenant | **REVISAR** | Backend | Sin política explícita |
| `raw_redirect` | Datos completos guardados del retorno | Tenant | **REVISAR** | Backend | Sin política explícita |
| `raw_webhook` | Payload completo guardado del webhook | Tenant | **REVISAR** | Backend | Sin política explícita |
| `ultimo_error` | Diagnóstico | Tenant | Puede contener dato sensible si no se sanitiza | Backend | Sin política explícita |
| Eventos Wompi sanitizados (`codigo`, `mensaje`, `metadata`) | Diagnóstico, métricas y seguridad | Tenant | Financiero/seguridad | Backend/plataforma | Sin política explícita |

**Gate obligatorio de Fase 10:** revisar qué contienen realmente los tres campos `raw_*`. No asumir que un payload del proveedor es seguro de conservar completo. El objetivo es sustituir conservación indiscriminada por campos mínimos/sanitizados cuando sea posible.

**Regla ya expresada en `EventoPagoWompi`:** `metadata` debe ser sanitizada y nunca contener tarjeta, CVV, secretos ni `Authorization`.

### 3.6 Auditoría

**Modelo:** `AuditEvent`.

Datos almacenados: `request_id`, evento/categoría/severidad/resultado/fuente, descripción, actor y snapshots de username/rol/Tenant/sucursal, IP, método HTTP, path sin query string, status, user-agent, objeto afectado, fingerprint, metadata y fecha.

**Finalidad:** seguridad, auditoría e investigación operativa.

**Ámbito:** control-plane global. No concede permisos por sí mismo.

**Retención implementada:** **180 días por defecto**, configurable mediante `FOODBACK_AUDIT_RETENTION_DAYS`.

**Controles existentes:** append-only a nivel de modelo; el path se guarda sin query string.

### 3.7 Incidentes de seguridad

**Modelo:** `SecurityIncident`.

Datos almacenados: fingerprint/evento, severidad/estado, título/descripcion, snapshots de actor/Tenant/sucursal, IP, ruta, conteos, timestamps, referencias a eventos de auditoría y estado de notificaciones.

**Finalidad:** correlación, investigación, respuesta y alertamiento de seguridad.

**Ámbito:** control-plane global.

**Retención implementada:** incidentes **resueltos 365 días por defecto**, configurable mediante `FOODBACK_SECURITY_INCIDENT_RETENTION_DAYS`. Los incidentes activos no se purgan automáticamente.

### 3.8 Soporte

**Modelo:** `SupportReport`.

Datos almacenados: identificadores públicos/técnicos, status HTTP, estado del ticket, mensaje voluntario, actor y snapshots de Tenant/sucursal, fechas.

**Finalidad:** investigar errores reportados por una persona y correlacionarlos con el request técnico.

**Ámbito:** control-plane global.

**Protecciones actuales:** no copia `request.POST`, headers completos, cookies, tracebacks ni mensajes técnicos automáticamente. El mensaje está limitado a 1500 caracteres.

**Retención:** POR DEFINIR. Debe diferenciar ticket abierto/en revisión de ticket resuelto.

### 3.9 Rate-limit y datos técnicos de seguridad

**Modelo principal:** `RateLimitBucket` y datos de sesión.

Finalidad: limitar abuso, login, recuperación, soporte y acciones sensibles.

Puede utilizar claves derivadas de sesión/IP/Tenant según el flujo. Su retención y limpieza deben seguir siendo mínimas y orientadas a seguridad, no a perfilado.

### 3.10 Backups y media

**PostgreSQL:** backups custom `pg_dump`, verificación, restore-test y estado/readiness.

**Retención local actual:** 14 días según el diseño de DR vigente.

**Pendientes externos:** copia offsite en failure domain distinto y evidencia de recuperación real de media/Cloudinary antes de producción estable.

**Importante:** eliminar/anonymizar un registro de la base primaria no implica desaparición instantánea de backups ya creados. La política pública deberá explicar esta ventana sin prometer borrado inmediato de copias de seguridad.

### 3.11 Proveedores externos conocidos

| Proveedor | Datos/función | Estado |
|---|---|---|
| Wompi | Procesamiento/confirmación de pagos | Integrado |
| Cloudinary | Media | Integrado; DR real pendiente |
| Google Maps / Places | Geolocalización, mapas y rutas | Usado/previsto según flujo |
| Firebase Realtime Database | Tracking/eventos realtime | Previsto, no activar antes de necesidad |
| Railway | Hosting/PostgreSQL producción | Diferido |
| Proveedor de correo/SMTP | Email operativo/seguridad | Diferido hasta dominio definitivo |

La Política de Privacidad deberá reflejar únicamente proveedores realmente activos en producción y la finalidad concreta con la que reciben datos.

## 4. Datos que FoodBack no debe almacenar

Salvo cambio explícito y revisado de diseño, FoodBack no debe conservar:

- número completo de tarjeta;
- CVV/CVC;
- PIN;
- contraseña en texto claro;
- código OTP de recuperación en texto claro;
- secretos/API keys dentro de modelos o logs;
- header `Authorization`;
- cookies completas;
- bodies completos de requests de forma general;
- traceback o `exception.message` crudos en tickets de soporte;
- payloads de proveedores completos si contienen información que no sea necesaria.

## 5. Pendientes que bloquean el cierre de Fase 10

1. Fijar plazos de `Cliente`, `Pedido`, `PagoWompi`, `EventoPagoWompi`, `SupportReport`, identidades/memberships y recuperación de contraseña.
2. Revisar y minimizar `PagoWompi.raw_creacion`, `raw_redirect` y `raw_webhook`.
3. Definir exportación de datos al cancelar un Tenant.
4. Definir qué se anonimiza, qué se elimina y qué debe conservarse por obligación fiscal/contractual/seguridad.
5. Definir procedimiento técnico de cancelación y purga sin romper integridad referencial.
6. Definir cómo se reflejan borrados/anominización en backups durante su ventana de retención.
7. Diseñar aceptación versionada de Términos y Privacidad.
8. Redactar las páginas públicas solo después de que las políticas anteriores sean implementables.

## 6. Regla de mantenimiento

Toda feature nueva que introduzca una categoría de datos personales, ubicación, tracking, pago, soporte o seguridad debe actualizar este inventario antes de considerarse terminada.
