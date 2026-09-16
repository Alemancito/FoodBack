# FoodBack — Backup y Disaster Recovery

## Objetivo

FoodBack debe poder recuperarse después de corrupción, borrado accidental, migración defectuosa,
fallo del proveedor, pérdida de una máquina o pérdida completa del proveedor principal.

Un archivo `.backup` no se considera confiable hasta que:

1. `pg_restore --list` puede leerlo;
2. su SHA-256 coincide con la metadata;
3. puede restaurarse en una base temporal;
4. la restauración contiene las tablas críticas esperadas.

## Objetivos técnicos iniciales

No son un SLA comercial:

- **RPO PostgreSQL:** <= 24 horas en producción.
- **RPO offsite:** <= 24 horas en producción.
- **RTO:** <= 2 horas para recuperación manual controlada.
- **Restore-test PostgreSQL:** al menos cada 35 días.
- **Prueba de recuperación de media:** al menos cada 180 días inicialmente.

Se revisarán usando volumen, costo, criticidad y datos reales de operación.

## Estrategia 3-2-1

En producción FoodBack debe tender a:

- 3 copias de los datos;
- 2 medios/ubicaciones lógicamente diferentes;
- 1 copia fuera del proveedor o volumen principal.

El backup local por sí solo NO cumple 3-2-1. Una segunda carpeta en `C:` tampoco.

## PostgreSQL

PostgreSQL es la fuente principal de verdad para usuarios, tenants, sucursales, catálogo, pedidos,
pagos, suscripciones, auditoría e incidentes.

Los backups usan `pg_dump` formato custom (`-Fc`).

### Rol dedicado

`foodback_backup`:

- LOGIN;
- NOSUPERUSER;
- NOCREATEDB;
- NOCREATEROLE;
- read-only por defecto;
- SELECT sobre los datos;
- BYPASSRLS únicamente para obtener todos los tenants.

Django nunca debe usar este rol en runtime.

### Crear backup

```powershell
.\scripts\backup_db.ps1
```

El script crea primero `.partial`, ejecuta `pg_dump`, valida catálogo, publica el `.backup`, calcula
SHA-256, genera metadata sin secretos, registra `last_backup.json` y aplica retención local.

### Verificar backup

```powershell
.\scripts\verify_backup.ps1 -BackupPath ".\backups\postgres\ARCHIVO.backup"
```

### Probar restore

```powershell
.\scripts\restore_test_db.ps1 -BackupPath ".\backups\postgres\ARCHIVO.backup"
```

Crea una BD temporal, restaura, valida tablas críticas/migraciones y elimina la BD. Registra
`backups/status/last_restore_test.json`.

`-KeepDatabase` es solo para diagnóstico manual.

## Copia offsite

```powershell
.\scripts\export_offsite_backup.ps1 `
    -BackupPath ".\backups\postgres\ARCHIVO.backup" `
    -Destination "E:\FoodBackBackups"
```

La copia exige metadata, usa `.partial`, verifica SHA-256, evita sobrescrituras incompatibles y
registra `last_offsite_export.json`.

Por defecto rechaza el mismo volumen. `-AllowSameVolumeForDevelopmentTest` existe únicamente para
probar el flujo y jamás convierte esa copia en offsite real.

En producción el destino offsite debe mantenerse dentro del RPO de 24h y estar protegido mediante
cifrado del medio/almacenamiento cuando corresponda.

## DR readiness

```powershell
.\scripts\dr_readiness.ps1
```

Comprueba de forma no destructiva:

- backup más reciente e integridad;
- restore-test;
- offsite;
- `.env` fuera de Git;
- recuperabilidad del código vía Git;
- relación entre backup y commit;
- evidencia de recuperación real de media externa.

Genera `backups/status/dr_readiness.json` para uso operacional y futuro dashboard Foundation.

En desarrollo puede terminar `WARN` por dependencias externas todavía diferidas. En producción los
controles críticos faltantes deben bloquear readiness.

## Retención

### Local

- dumps verificados: 14 días inicialmente.

### Offsite de producción

Objetivo recomendado cuando el almacenamiento remoto soporte lifecycle:

- diarios: 30 días;
- semanales: 12 semanas;
- mensuales: 12 meses.

La retención larga debe vivir preferentemente en el almacenamiento offsite, no en un script local
con capacidad de borrar masivamente el repositorio remoto.

## Código

El código se protege mediante Git y remoto privado. La metadata del backup registra `git_commit`
cuando Git está disponible.

Un dump no captura cambios locales sin commit. Producción debe ser reproducible desde un commit/tag
conocido y working tree limpio.

## Secretos

Los secretos NO se incluyen en dumps, metadata ni Git. Debe existir una copia cifrada/vault
independiente del hosting principal. Ver `docs/secrets-recovery-checklist.md`.

## Media

PostgreSQL no restaura binarios externos. Ver `docs/media-recovery.md`.

## Recuperación total

El procedimiento detallado está en `docs/dr-runbook.md`. Resumen:

1. contener incidente y detener escrituras si procede;
2. seleccionar backup verificado;
3. recuperar commit compatible;
4. crear infraestructura limpia;
5. restaurar PostgreSQL;
6. recuperar secretos desde vault;
7. recuperar/verificar media;
8. ejecutar checks/smoke tests;
9. validar aislamiento tenant, auth, pedidos, auditoría y pagos;
10. reabrir tráfico controladamente;
11. medir RPO/RTO reales y documentar el incidente.

## Railway y proveedor de producción

La capa construida aquí es provider-agnostic. Cuando Railway/hosting final se active se añadirá:

- scheduling real de backups;
- destino offsite real;
- alertas de fallo;
- credenciales/roles de producción;
- simulacro final sobre infraestructura equivalente.

Nada de eso debe reemplazar el procedimiento estándar PostgreSQL ya probado.

## Regla fundamental

**Un backup que nunca se ha restaurado es solamente una esperanza.**
