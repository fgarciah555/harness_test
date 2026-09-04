---
description: Baseline de seguridad para manejo de secretos, IAM y autenticación. Aplicar siempre, especialmente al generar código que acceda a credenciales, configure permisos o exponga endpoints.
activation: always_on
---

# Seguridad — Baseline mínimo

## Secretos y credenciales

- Ningún secreto (API key, password, token, connection string) se escribe en código,
  archivos `.env` versionados, ni comentarios
- Todo secreto se lee desde **Secret Manager de GCP** en runtime, con acceso vía la
  service account del servicio
- Archivos `.env.example` pueden existir para documentar qué variables se necesitan,
  pero sin valores reales
- Si el agente necesita un valor de ejemplo para desarrollo local, debe usar un
  placeholder explícito (`CHANGE_ME`, `<tu-api-key>`), nunca un valor plausible que
  parezca real

## IAM y permisos

- Principio de menor privilegio: cada service account tiene solo los roles que su
  servicio necesita para funcionar, nunca `roles/owner` o `roles/editor`
- Una service account por servicio, no compartida entre aplicaciones distintas
- Todo acceso entre servicios internos de GCP se hace vía IAM (service-to-service auth
  de Cloud Run, Workload Identity en GKE), no vía API keys estáticas cuando existe la
  alternativa nativa

## Autenticación y endpoints

- Todo endpoint de backend que exponga datos o acciones sensibles requiere
  autenticación explícita; no se asume que "es interno" como excusa para omitirla
- CORS configurado con origenes explícitos, nunca `*` en producción
- Inputs de usuario siempre validados en el backend (Pydantic en FastAPI cumple esto por
  diseño — no se debe deshabilitar validación para "ir más rápido")

## Dependencias

- No se agregan librerías sin verificar que tengan mantenimiento activo; librerías
  abandonadas o con vulnerabilidades conocidas no se introducen aunque resuelvan el
  problema más rápido
