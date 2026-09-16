# FoodBack — Gate de Disaster Recovery antes de producción

Este documento separa lo que ya puede quedar terminado localmente de lo que exige infraestructura
real de producción.

## Debe estar listo antes de aceptar clientes reales

### Base de datos

- backup PostgreSQL automático al menos diario;
- `foodback_backup` separado del runtime;
- dump verificado y SHA-256;
- copia offsite fuera del proveedor/volumen principal dentro del RPO;
- restore-test real al menos mensual;
- retención remota/lifecycle configurada.

### Código

- repositorio remoto privado;
- despliegue reproducible desde commit/tag;
- producción sin cambios locales no versionados;
- backup asociado a un commit conocido.

### Secretos

- secretos fuera de Git;
- vault/gestor o copia cifrada recuperable independiente del hosting;
- procedimiento de recuperación probado;
- credenciales del almacenamiento offsite no guardadas junto al backup.

### Media

- estrategia real verificada para Cloudinary/proveedor elegido;
- recuperación de assets probada y registrada;
- dependencia y limitaciones del plan documentadas.

### Operación

- RPO/RTO aprobados;
- responsables y contactos definidos;
- runbook accesible sin depender del servidor caído;
- alertas/monitorización del fallo de backups;
- prueba de recuperación total antes del lanzamiento o muy cerca de él.

## Objetivos iniciales FoodBack

Estos son objetivos técnicos iniciales, no un SLA contractual:

- **RPO de base de datos:** <= 24 horas.
- **RPO offsite:** <= 24 horas en producción.
- **RTO:** <= 2 horas para recuperación manual controlada.
- **Restore-test PostgreSQL:** al menos cada 35 días.
- **Prueba de media:** al menos cada 180 días inicialmente.

Con métricas reales puede reducirse el RPO a 12h, 6h o menor sin cambiar el diseño base.

## Estado durante desarrollo local

Es correcto que `dr_readiness.ps1` termine en `WARN` si las únicas advertencias son controles que
requieren infraestructura externa real, por ejemplo:

- offsite de prueba en el mismo volumen;
- media externa todavía no probada;
- cambios locales de desarrollo todavía sin commit.

En producción esos controles se vuelven estrictos cuando corresponda y un requisito incumplido
puede convertir el gate en `FAIL`.
