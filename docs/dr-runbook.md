# FoodBack — Runbook de recuperación ante desastre

Este runbook describe la recuperación técnica de FoodBack sin depender de un proveedor concreto.
No contiene secretos.

## Cuándo usarlo

Usar este procedimiento ante corrupción de datos, borrado accidental, pérdida de la base de datos,
pérdida del servidor principal o indisponibilidad prolongada del proveedor.

## Prioridad operacional

1. Evitar nuevas escrituras si la infraestructura afectada todavía acepta tráfico.
2. Preservar evidencia del incidente antes de destruir recursos.
3. Elegir un backup verificado cuya antigüedad esté dentro del RPO aceptado.
4. Recuperar primero en infraestructura aislada.
5. Reabrir tráfico únicamente después de smoke tests y validaciones de aislamiento tenant.

## Recuperación de PostgreSQL

1. Seleccionar el backup `.backup` y su `.backup.json`.
2. Comparar SHA-256 con la metadata.
3. Revisar `git_commit` del backup.
4. Recuperar el código correspondiente a ese commit/tag.
5. Crear PostgreSQL limpio con versión compatible.
6. Restaurar usando herramientas estándar `pg_restore`.
7. Validar al menos:
   - `django_migrations`;
   - `pedidos_tenant`;
   - `pedidos_auditevent`;
   - `pedidos_securityincident`;
   - conteos básicos y migraciones esperadas.
8. Aplicar migraciones posteriores solamente si fueron revisadas y son necesarias.

En desarrollo, el restore-test automatizado es:

```powershell
.\scripts\restore_test_db.ps1 -BackupPath ".\backups\postgres\ARCHIVO.backup"
```

## Recuperación del código

El dump de PostgreSQL NO contiene el código de la aplicación.

Requisitos:

- repositorio Git remoto privado disponible;
- commit/tag compatible con el backup;
- `requirements.txt` del mismo estado del código;
- configuración reconstruible a partir de `.env.example` + vault externo.

Nunca reconstruir producción copiando una carpeta local no versionada como única fuente.

## Recuperación de secretos

Los secretos se recuperan desde un almacén separado del hosting principal y del repositorio.
Usar `docs/secrets-recovery-checklist.md` como inventario de categorías, no como almacenamiento.

Después de recuperar secretos críticos:

- rotar cualquiera que pudiera haber sido expuesto durante el incidente;
- validar restricciones de API keys;
- verificar credenciales Wompi/webhooks;
- confirmar credenciales de PostgreSQL por rol;
- confirmar SMTP/alertas cuando estén activados.

## Recuperación de media externa

PostgreSQL puede restaurar referencias a Cloudinary, pero no los archivos binarios perdidos del
proveedor. Seguir `docs/media-recovery.md`.

La recuperación real de media debe probarse periódicamente. Después de una prueba REAL puede
registrarse evidencia no secreta con:

```powershell
.\scripts\record_media_recovery_test.ps1 `
    -Provider "Cloudinary" `
    -RecoveredAssetCount 3 `
    -Evidence "DR-test-2026-09" `
    -Confirmed
```

Ese comando no restaura archivos: únicamente registra una prueba que ya ocurrió.

## Smoke tests antes de reabrir tráfico

Como mínimo:

1. `python manage.py check` sin issues.
2. Suite de pruebas aplicable en entorno aislado.
3. Login staff y revocación de permisos.
4. Resolución correcta de tenant/sucursal.
5. Lecturas/escrituras sin cruce entre tenants.
6. Creación/consulta de pedido de prueba.
7. Wompi en modo seguro/sandbox o sin ejecutar cargos reales.
8. Auditoría e incidentes escribiendo correctamente.
9. Media crítica accesible.
10. `dr_readiness.ps1` revisado y cualquier WARN/FAIL entendido explícitamente.

## Reapertura

Reabrir tráfico de forma controlada. Observar logs, errores, seguridad, DB y pagos. Documentar:

- hora de inicio y fin;
- backup usado;
- commit usado;
- pérdida real de datos (RPO real);
- tiempo real de recuperación (RTO real);
- problemas encontrados;
- acciones correctivas.

## Regla

Un restore técnicamente exitoso no termina el incidente: FoodBack solo está recuperado cuando
código, base de datos, secretos, media y operaciones críticas funcionan juntos.
