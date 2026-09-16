# FoodBack — Checklist de secretos para recuperación

Este archivo NO contiene valores secretos. Solo define qué debe existir en un almacén
seguro externo al repositorio para poder reconstruir producción.

## Django / plataforma

- `SECRET_KEY`
- dominio/base domain cuando se defina
- allowed hosts / CSRF trusted origins

## PostgreSQL

- credencial runtime `foodback_app`
- credencial migrator
- credencial de backup
- datos de conexión del proveedor

El rol `foodback_test` es principalmente local y no debe entregarse al proceso web de
producción.

## Pagos

- Wompi restaurante
- Wompi plataforma
- secretos de webhook/autenticación aplicables

## Media

- Cloudinary cloud name
- Cloudinary API key
- Cloudinary API secret

## Mapas

- Google Maps key y restricciones configuradas

## Email

Cuando se active el dominio definitivo:

- SMTP host/user/key de Brevo u otro proveedor
- destinatario interno de alertas Foundation

## Backup offsite

- credenciales del almacenamiento remoto si se requieren
- clave/recuperación del cifrado del medio o vault

## Reglas

- nunca guardar estos valores en Git;
- nunca incluirlos en metadata `.backup.json`;
- nunca guardar la clave de cifrado junto al backup cifrado;
- tener al menos una forma de recuperación del vault independiente del proveedor de
  hosting principal;
- revisar este checklist antes de cada cambio importante de proveedor.
