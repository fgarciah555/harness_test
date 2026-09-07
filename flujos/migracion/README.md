# Flujo: Migración

Migrar un monolito legado (hoy: Flask + Jinja2, sesión de servidor) hacia
backend FastAPI + frontend Angular separados, siguiendo `AGENTS.md` +
`.agents/rules/` del proyecto destino. Es el flujo original del harness — el
más probado en producción (ver `docs/handoff.md` en la raíz del repo).

`metadata.tipo_flujo` de este flujo es `"migracion"`.

## Estrategia de Planner

El Planner (hoy Felipe + Claude Code, no automatizado — ver
`../../docs/handoff.md`, "Decisión de arquitectura importante") arma `plan.json`
leyendo el monolito origen real, no de memoria:

1. **Resumen del monolito**: qué hace, qué rutas/vistas tiene, cómo maneja
   sesión y auth, qué falta de contexto — reemplaza al Analyzer automatizado
   que el diseño original consideró y descartó.
2. **`riesgos_heredados[]`**: cualquier comportamiento del monolito que se
   preserva a propósito (por alcance, tiempo, o decisión aparte) va acá, no
   se corrige en silencio.
3. **Modelo canónico grounded contra el schema real de la BD**: antes de
   escribir el modelo de una entidad persistida, obtener el script real de
   la BD del monolito origen (si existe) y derivar los tipos de campo de
   ahí — ver `../../harness-core/schemas/plan.contract.md`,
   `decisiones_globales.schema_bd_origen`.
4. **`knowledge/` antes de `detalle_tecnico`**: antes de escribir la
   instrucción técnica de un item que usa una librería no trivial, revisar/
   actualizar `../../harness-core/knowledge/` — evita repetir investigación
   ya hecha y patrones de librería alucinados.
5. Barrer sistemáticamente las reglas `always_on` de `.agents/rules/` del
   proyecto destino al armar `decisiones_globales` — no confiar en
   acordarse de cada una por item (ver contrato, "Principio general").

Ver `docs/prompt-planner.example.md` para un ejemplo de cómo arrancar esta
fase con Claude Code, y `schemas/plan.example.json` para el formato aplicado
a un fixture de referencia (pedidos).

## Qué es específico de este flujo (no aplica a creación/mantención)

- `metadata.monolito_origen`, `item.origen`, `riesgos_heredados[]`.
- `decisiones_globales.schema_bd_origen`.
- Los templates de empaquetado/arranque local (`templates/`) y los
  Dockerfiles de referencia (`docs/Dockerfile.*`) de este flujo — pensados
  para el resultado de una migración completa (backend + frontend + DAL
  como deployables propios).

Todo lo demás (contrato de `plan.json`, Executor/Compliance, checks
determinísticos, ticket de reintento) es el mecanismo genérico de
`harness-core/` — sin diferencias respecto a los otros dos flujos.
