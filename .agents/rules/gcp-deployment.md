---
description: Estándares de despliegue e infraestructura como código para servicios en Cloud Run y GKE. Aplicar al generar Dockerfiles, manifiestos de despliegue, Terraform o configuración de CI/CD.
activation: always_on
---

# Despliegue e IaC — Cloud Run / GKE

## Decisión Cloud Run vs GKE

- **Cloud Run por defecto** para servicios stateless nuevos y de bajo tráfico. Es la
  opción preferida para proyectos pequeños de esta migración.
- **GKE** solo si: el servicio ya vive en un cluster existente, requiere orquestación de
  múltiples contenedores acoplados, o necesita configuración de red/sidecars que Cloud
  Run no soporta.
- El agente debe preferir Cloud Run salvo instrucción explícita de usar GKE.

## Contenedores

- Todo servicio Python usa una imagen base oficial `python:3.x-slim`, nunca `latest` sin
  fijar versión
- Angular se sirve como build estático (`ng build --configuration production`) detrás de
  un servidor liviano (nginx) o vía Cloud Storage + Load Balancer para casos simples
- Dockerfiles multi-stage: una etapa de build, una etapa final mínima sin herramientas
  de desarrollo
- Nunca correr el proceso como `root` dentro del contenedor

## Infraestructura como código

- Toda infraestructura de GCP (Cloud Run, IAM, Secret Manager, buckets) se define con
  Terraform, no se crea manualmente desde la consola salvo pruebas exploratorias
  descartables
- Variables de ambiente y configuración por ambiente (`dev`/`staging`/`prod`) en
  archivos `.tfvars` separados, nunca hardcodeadas en el `.tf` principal
- Cada servicio de Cloud Run define explícitamente:
  - límites de CPU/memoria (no dejar el default sin revisar)
  - `min-instances` y `max-instances` acordes al tráfico esperado
  - service account dedicada (ver `security-baseline.md`)

## CI/CD

- Todo despliegue a `prod` pasa por pipeline (Cloud Build u otro definido por el
  proyecto), nunca `gcloud deploy` manual desde una laptop
- El pipeline debe correr tests antes de construir la imagen; si no hay tests definidos
  para el proyecto, el agente debe señalarlo explícitamente en vez de omitirlo en
  silencio
