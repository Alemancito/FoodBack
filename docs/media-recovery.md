# FoodBack — Recuperación de media externa

## Alcance

Las imágenes/archivos almacenados en Cloudinary u otro proveedor no forman parte del dump de
PostgreSQL. La base puede conservar URLs, public IDs o referencias aunque el binario ya no exista.

## Objetivo

Antes de producción real debe existir una forma comprobada de recuperar media crítica sin depender
exclusivamente de la misma cuenta/proveedor que sufrió el incidente.

## Requisitos de producción

- identificar qué assets son críticos para operar;
- conocer las funciones de backup/export/versionado disponibles en el plan real del proveedor;
- evitar depender de una función comercial que todavía no haya sido verificada;
- mantener una copia/exportación adecuada cuando el riesgo y volumen lo justifiquen;
- cifrar el almacenamiento alterno cuando corresponda;
- probar recuperación real de una muestra de assets;
- documentar el procedimiento y fecha de prueba.

## Cloudinary

FoodBack usa Cloudinary actualmente, pero la política final debe validarse contra el plan y las
capacidades reales contratadas al acercarse el lanzamiento. No se asume aquí que una función de
backup concreta esté incluida.

La prueba debe recuperar archivos reales y comprobar que pueden volver a ser servidos o
reincorporados. Descargar un listado de URLs no equivale a recuperar los binarios.

## Evidencia operacional

Después de una recuperación REAL, registrar una referencia no secreta:

```powershell
.\scripts\record_media_recovery_test.ps1 `
    -Provider "Cloudinary" `
    -RecoveredAssetCount 3 `
    -Evidence "DR-test-YYYY-MM" `
    -Confirmed
```

No poner API keys, passwords, tokens, URLs firmadas ni secretos en `Evidence`.

El gate DR considera esta prueba vencida después del límite configurado por
`FOODBACK_MEDIA_RECOVERY_TEST_MAX_AGE_DAYS`.

## Frecuencia inicial

Mientras FoodBack tenga bajo volumen, objetivo inicial: prueba real al menos cada 180 días.
Revisar la frecuencia cuando aumenten clientes, volumen de assets o dependencia operacional.
