# Base de conocimiento de dependencias/tecnologías

Un archivo por librería/framework (`pydantic-settings.md`, `fastapi.md`,
`sqlalchemy-2.0.md`, etc.), acumulando **patrones de uso verificados contra
documentación/comportamiento real**, no contra lo que un modelo "recuerda" de
su entrenamiento. Existe porque el 2026-08-21, migrando un backend real, el
Executor (y el propio Planner) generó varios patrones de librería plausibles
pero inexistentes o incorrectos (`Settings(env_file=...)` en vez de
`Settings(_env_file=...)`, `from fastapi import HTTPBearer` en vez de
`from fastapi.security import HTTPBearer`, `db.execute(select(Entidad)).first()`
en vez de `db.scalars(stmt).first()`) — el mismo tipo de riesgo que ya se
había visto con imports internos alucinados (`DomainError`), pero de
conocimiento de librería en vez de conocimiento del proyecto. Ver
`handoff.md`, sección "Tercera prueba real (2026-08-21)", y `Pendientes.md`,
sección "Agente investigador de tecnologías", para el contexto de decisión.

## Quién la usa y cómo

**Consumidor: el Planner** (hoy, Felipe + Claude en Claude Code — con
WebSearch/WebFetch reales disponibles), no Executor ni Compliance. Antes de
escribir `detalle_tecnico` para un item que usa una librería no trivial (algo
más que built-ins de Python o el propio framework ya cubierto en
`.agents/rules/`), el Planner:

1. Revisa si ya existe una entrada relevante acá.
2. Si no existe o la librería es nueva en este proyecto, verifica el patrón
   real (WebSearch/WebFetch a la doc oficial, o ejecutando un snippet mínimo
   como se hizo el 2026-08-21 — ver ejemplo en `pydantic-settings.md`) antes
   de asumir que el conocimiento de entrenamiento del modelo está actualizado.
3. Vuelca el patrón verificado **directo en `detalle_tecnico`/
   `decisiones_globales` del plan** (Executor ya recibe eso completo, no
   hace falta darle acceso a este directorio) — y agrega o actualiza la
   entrada acá para que el próximo plan (este proyecto u otro) no tenga que
   volver a investigarlo.

**Por qué no lo lee Executor directo:** el contexto de Executor es
deliberadamente mínimo a propósito (`decisiones_globales` + el item +
`interfaz` de dependencias, ver `plan.contract.md`, "Principio general") —
agrandarlo con una base de conocimiento genérica reintroduce el mismo riesgo
que ya se evitó con Compliance (contexto de más, sin acotar a lo que el item
necesita). El Planner ya filtra y resume lo relevante al escribir el plan.

## ¿Acá o en el `SYSTEM_PROMPT` del Executor?

Un gotcha nuevo no siempre va acá — a veces corresponde directo al
`SYSTEM_PROMPT` de `agents/executor.py` (ver ahí sus "Reglas estrictas").
Criterio para decidir: **¿la regla depende de qué toca el item, o aplica
siempre sin importar el item?**

- **Depende del item** (solo importa si ese item usa tal librería/patrón) →
  acá. El Planner la filtra y la vuelca en `detalle_tecnico` solo para los
  items que la necesitan — ejemplos: `HTTPBearer` vive en `fastapi.security`,
  `Settings(_env_file=...)` no `env_file=...`.
- **Incondicional** (aplica siempre, en cualquier proyecto, sin importar qué
  librería toque el item) → `SYSTEM_PROMPT` directo, sin pasar por el
  Planner — ejemplos ya agregados ahí: no funciones en el WHERE de una
  query, `requirements.txt` con versiones fijadas, `response_model` en
  endpoints FastAPI, `datetime.now(timezone.utc)` en vez de
  `datetime.utcnow()`.

La razón de fondo es la misma que en "Por qué no lo lee Executor directo"
arriba: una regla condicional que Executor viera siempre hincharía su
contexto con algo irrelevante para la mayoría de los items; una regla
incondicional es barata (aplica siempre igual) y no tiene sentido hacerla
pasar por el filtro del Planner.

## Cuándo agregar o actualizar una entrada

- Cada vez que un rechazo de Compliance o una falla de smoke test revele que
  el código generado usó un patrón de librería incorrecto (no un error de
  lógica de negocio propio del proyecto).
- Cada vez que el Planner tenga que investigar activamente cómo se usa algo
  antes de escribir un item, aunque no haya fallado nada todavía — mejor
  dejarlo escrito una vez que repetir la investigación.
- **Candidatos automáticos:** el agente Documentador (`agents/documentador.py`,
  motor local) revisa `.harness/logs/candidatos_conocimiento.md` de cada
  proyecto — propone, no escribe acá directo. Revisarlo al retomar un
  proyecto es más rápido que releer todo `reporte_fallas.md` a mano, pero
  el criterio de fondo (¿generaliza de verdad? ¿está bien la fuente?) lo
  sigue decidiendo el Planner, igual que siempre — el candidato dice
  explícitamente que está "confirmado en código real", no verificado contra
  documentación oficial (ver `schemas/plan.contract.md`).

## Formato de entrada

```markdown
## <qué hace / qué resuelve>

**Verificado:** <fecha>, <librería>==<versión>
**Patrón correcto:**
\`\`\`python
codigo_real_verificado()
\`\`\`
**Patrón incorrecto visto en la práctica:** `codigo_que_alucino_el_modelo()`
— por qué está mal / qué excepción tira.
**Encontrado en:** <fecha>, migrando <tipo de proyecto/contexto genérico>.
```
