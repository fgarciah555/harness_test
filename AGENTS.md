# AGENTS.md — Contexto de Arquitectura

> Este archivo es leído automáticamente por agentes de IA (Claude Code, Antigravity y
> herramientas compatibles con el estándar AGENTS.md) al iniciar una sesión sobre este
> repositorio. Contiene los principios no negociables definidos por el área de
> Arquitectura. Las reglas detalladas por tema están en `.agents/rules/`.

## Contexto del proyecto

Este repositorio forma parte de un esfuerzo de migración de sistemas legado hacia GCP,
liderado por el área de Operaciones con apoyo de herramientas de IA (Claude Code,
Antigravity). El objetivo de la migración es reducir vulnerabilidades y eliminar
dependencia de servidores físicos.

- **Backend:** Python (FastAPI por defecto, salvo que el proyecto indique otro framework)
- **Frontend:** Angular 20.x, bootstrap con `ng new --routing --style=scss --ssr=false` +
  configuración manual — standalone components, zoneless, Signals, sin `NgModule`
- **Idioma de naming:** español para todo nombre de dominio (servicios, componentes,
  variables de negocio); en inglés solo los términos propios del framework/lenguaje
- **Despliegue:** GCP — Cloud Run para servicios stateless simples, GKE cuando el proyecto
  requiere orquestación más compleja o ya vive en un cluster existente
- **Perfil del equipo que usa este contexto:** Operaciones, con foco en infraestructura y
  despliegue más que en desarrollo de software. Estas reglas existen para que el código
  generado por IA sea consistente y mantenible sin requerir experiencia previa de
  arquitectura de software.

## Principios no negociables

1. **Consistencia sobre preferencia personal.** Ante cualquier ambigüedad, el agente debe
   seguir las convenciones de este documento y de `.agents/rules/`, no el estilo por
   defecto del modelo o del framework.
2. **Nada de secretos ni credenciales en código.** Todo secreto vive en Secret Manager de
   GCP. Nunca se hardcodea una API key, password, token o connection string.
3. **Todo servicio nuevo debe ser observable.** Logging estructurado y healthcheck son
   requisito mínimo, no un "nice to have" posterior.
4. **Principio de menor privilegio.** Cualquier service account, rol IAM o permiso de GCP
   se solicita al mínimo necesario para la tarea, nunca `roles/owner` o `roles/editor`
   por comodidad.
5. **El código generado debe ser explicable.** Si el agente no puede justificar una
   decisión de diseño en una frase, debe preferir la alternativa más simple y estándar.
6. **No se introducen nuevas dependencias, servicios o patrones de arquitectura sin
   dejar constancia en el README del proyecto** (qué se agregó y por qué). Esto es lo que
   permite que este mismo contexto se siga actualizando a partir de proyectos reales.

## Cómo usar este contexto

- **Claude Code:** lee este archivo automáticamente. Ver también `CLAUDE.md`.
- **Antigravity:** configurar como regla de workspace en `.agents/rules/`, con activación
  `Always On` para este archivo raíz. Las reglas modulares pueden ir en `Model Decision`
  si se quiere que se carguen solo cuando aplican.
- **Reglas modulares** (ver `.agents/rules/`):
  - `naming-conventions.md` — nombres de archivos, variables, servicios y recursos GCP
  - `architecture-decisions.md` — cómo tratar patrones no recomendados (monolito,
    sesión de servidor): informar y dejar decidir, no bloquear
  - `backend-architecture.md` — capas de FastAPI (router/service/repository/model),
    dónde vive el `commit()`, testing con DB
  - `frontend-angular.md` — estructura Angular, bootstrap explícito, deploy
  - `local-development.md` — conectividad frontend-backend en desarrollo local
    (ejecución separada vs. docker-compose)
  - `gcp-deployment.md` — estructura de despliegue e IaC para Cloud Run / GKE
  - `security-baseline.md` — manejo de secretos, IAM, autenticación
  - `error-handling.md` — manejo de errores, logging y observabilidad

## Pendientes conocidos — sin resolver todavía

Estos puntos están identificados pero **deliberadamente no resueltos** en este MVP. Un
agente no debe asumir una respuesta por su cuenta para ninguno de estos temas; si una
tarea los requiere, debe señalarlo explícitamente en vez de decidir en silencio.

- **Exposición del backend en producción:** cómo el frontend resuelve la URL del
  backend por ambiente una vez desplegado (Cloud Run, GKE) y cómo se autentican esas
  llamadas. **Esto es intencionalmente responsabilidad de Operaciones**, no una laguna
  de arquitectura a rellenar — ver `local-development.md` para lo que sí está definido
  (conectividad en desarrollo local, que es independiente de esta decisión).
- **Cliente HTTP tipado desde OpenAPI:** FastAPI expone automáticamente un esquema
  OpenAPI; existe la posibilidad de generar el cliente Angular tipado a partir de ese
  esquema (evitando drift entre contratos de front y back) en vez de escribir servicios
  a mano. Marcado como línea de investigación a futuro, no como estándar del MVP.

## Fuera de alcance de este MVP

Este documento es una primera versión pragmática, construida a partir de convenciones
observadas en proyectos existentes. No reemplaza un ADR ni un style guide completo.
Se espera iterarlo a medida que se detecten nuevos patrones o inconsistencias.
