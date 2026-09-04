---
description: Estándares de manejo de errores, logging y observabilidad. Aplicar al generar lógica de negocio, endpoints o cualquier código que pueda fallar en runtime.
activation: model_decision
---

# Manejo de errores y observabilidad

## Backend (Python / FastAPI)

- Excepciones de negocio con clases propias (`class PagoRechazadoError(Exception)`),
  nunca `except Exception` genérico que oculte la causa real
- Los endpoints traducen excepciones de negocio a códigos HTTP explícitos vía exception
  handlers de FastAPI, no `try/except` repetido en cada endpoint
- Todo error inesperado se loggea con contexto suficiente para debuggear (request id,
  usuario/servicio origen si aplica) antes de devolver una respuesta genérica al cliente
- Nunca se expone un stack trace ni detalle interno en la respuesta HTTP a producción

## Frontend (Angular)

- Errores de llamadas HTTP manejados centralizadamente vía un `HttpInterceptor`, no
  repetidos en cada servicio
- El usuario siempre recibe un mensaje entendible ante un error; el detalle técnico va
  a la consola/logging, no a la UI

## Logging y observabilidad

- Logging estructurado (JSON) en todos los servicios de backend, compatible con Cloud
  Logging de GCP
- Todo servicio expone un endpoint `/health` o `/healthz` mínimo, requerido para Cloud
  Run/GKE
- Nivel de log apropiado: `INFO` para eventos normales de negocio, `WARNING` para casos
  recuperables, `ERROR` para fallos que requieren atención — evitar loggear todo como
  `INFO` o todo como `ERROR`
- No se loggean datos sensibles (passwords, tokens, PII completa) ni siquiera en nivel
  `DEBUG`
