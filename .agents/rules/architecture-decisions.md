---
description: Cómo debe comportarse el agente cuando un proyecto se aparta de los patrones de arquitectura recomendados (monolito vs. separación front/back, sesión de servidor vs. token, etc.). Aplicar siempre que el código a generar diverja de una recomendación del pack.
activation: always_on
---

# Recomendaciones vs. prohibiciones

## Principio

Las convenciones de este pack son la opción por defecto recomendada por Arquitectura,
no una lista de patrones prohibidos. El agente **no bloquea** la generación de un
patrón distinto solo porque se aparte de la recomendación — pero tampoco procede en
silencio como si fuera equivalente. La regla es: **señalar explícitamente, dejar que
decida quien tiene el contexto de negocio.**

## Qué debe hacer el agente ante una divergencia

Cuando una tarea implique generar o mantener un patrón que el pack no recomienda:

1. Generarlo si es lo que se pidió — no negarse ni sustituirlo por su cuenta.
2. Indicar explícitamente, antes o junto con el resultado, que ese patrón se aparta de
   la recomendación de Arquitectura, y en una frase por qué existe esa recomendación.
3. No repetir la advertencia en cada archivo o interacción posterior una vez que ya se
   informó y la persona con el contexto de negocio decidió seguir adelante — informar
   una vez con claridad basta, no es necesario insistir.

## Casos ya identificados

- **Monolito server-rendered (ej. Flask/Django + Jinja2) en vez de backend API +
  frontend separado.** La recomendación del pack es API (FastAPI) + SPA (Angular)
  porque facilita escalar cada capa por separado y es el patrón que el resto de las
  reglas (`backend-architecture.md`, `frontend-angular.md`) asumen. Para un proyecto
  genuinamente pequeño y de vida corta, un monolito puede ser una decisión válida — el
  agente debe decirlo, no decidirlo por su cuenta.
- **Autenticación por sesión de servidor en vez de token/JWT.** Válida en un monolito
  o en un backend que sirve HTML directamente; dejar de ser recomendable en cuanto el
  frontend pasa a ser una SPA separada, porque el modelo de sesión de servidor no
  traslada bien a esa topología. Mismo tratamiento: informar, no bloquear.

## Qué NO cubre esta regla

Esto no aplica a los principios de seguridad no negociables de `AGENTS.md` (secretos en
código, permisos IAM excesivos, etc.) — esos sí son bloqueantes, no recomendaciones. La
distinción es: patrones de arquitectura son preferencia informada; seguridad básica es
piso mínimo no negociable.
