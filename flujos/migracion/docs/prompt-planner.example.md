# Ejemplo: qué pedirle a Claude Code para armar `plan.json`

Este harness deja el Analyzer y el Planner en manos de Felipe + Claude
(hoy Claude Code), leyendo el proyecto real — no son agentes automatizados
del modelo local (ver `handoff.md`, sección "Decisión de arquitectura
importante"). Este archivo es un ejemplo del prompt inicial para arrancar
esa fase con un proyecto real, no un template a copiar literal — ajustalo a
cada monolito.

## Contexto que Claude Code necesita antes de escribir el plan

- Dónde está el código del monolito a migrar (ruta o repo).
- Dónde está el proyecto destino, y dentro de él `AGENTS.md` +
  `.agents/rules/` (naming-conventions, backend-architecture,
  frontend-angular, security-baseline, error-handling, gcp-deployment,
  local-development, architecture-decisions).
- Que `.harness/` ya esté inicializado en el proyecto destino
  (`python harness-core/init_harness.py /ruta/al/proyecto-destino`, ver
  README).
- El contrato de `plan.json`: `harness-core/schemas/plan.contract.md` (único
  para los 3 flujos), con `flujos/migracion/schemas/plan.example.json` como
  referencia de formato aplicada a este flujo (fixture de pedidos, no al
  monolito real). No olvidar `metadata.tipo_flujo: "migracion"`.

## Prompt de ejemplo

```
Quiero que armes el plan.json de migración para el monolito en
<ruta al monolito>.

El proyecto destino es <ruta al proyecto destino>. Ahí ya está
inicializado .harness/ y están las reglas en AGENTS.md + .agents/rules/
(naming-conventions, backend-architecture, frontend-angular,
security-baseline, error-handling, gcp-deployment, local-development,
architecture-decisions) — el plan tiene que respetarlas al pie de la letra,
en particular para decidir archivos_destino de cada item.

El formato exacto de plan.json está documentado en
<ruta al harness>/harness-core/schemas/plan.contract.md. Usá
<ruta al harness>/flujos/migracion/schemas/plan.example.json como
referencia de cómo se ve aplicado (es del fixture de pedidos, no de este
monolito) -- declará metadata.tipo_flujo: "migracion".

Antes de escribir el plan:
1. Leé el monolito completo y armame un resumen de qué hace, qué rutas/
   vistas tiene, cómo maneja sesión y auth, y qué falta de contexto para
   poder migrarlo (esto reemplaza al Analyzer automatizado).
2. Marcá explícitamente cualquier riesgo heredado que encuentres (código
   que el monolito hace mal y que hay que decidir si se corrige o se
   preserva) — van a riesgos_heredados.
3. Recién ahí armá los items, respetando: un item nunca mezcla backend y
   frontend, cada item lleva su interfaz (lo que expone hacia afuera, no
   el detalle completo), y las decisiones globales (auth, prefijo de API,
   casing, manejo de errores) van una sola vez en decisiones_globales, no
   repetidas en cada item.

Guardá el resultado en <ruta al proyecto destino>/.harness/config/plan.json.
```

## Después de esto

Con `plan.json` ya escrito, el resto corre con `harness-core/orchestrator.py`
contra el modelo local (ver README, sección "Uso") — no hace falta seguir
usando Claude Code para eso, es Executor + Compliance corriendo solos.
