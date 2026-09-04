# Harness multiagente para migración de monolitos

Herramienta standalone (no vive dentro de ningún proyecto) que usa un motor
de IA local (LM Studio) para automatizar parte de la migración de monolitos
legados (Flask + Jinja2, sesión de servidor) hacia backend FastAPI +
frontend Angular separados, siguiendo las reglas de un context pack propio
del proyecto destino (`AGENTS.md` + `.agents/rules/`).

Para el detalle de decisiones de diseño, qué se probó y qué falta, ver
`handoff.md` — este README es la referencia operativa (cómo instalar, cómo
correrlo), no la bitácora de diseño.

## Quién hace qué

- **Analyzer y Planner**: Felipe + Claude (hoy, Claude Code), leyendo el
  proyecto real. No son agentes automatizados — el modelo local rinde mal
  en razonamiento multi-paso, y esta parte se beneficia de contexto
  completo del repo + criterio humano. Ver
  `docs/prompt-planner.example.md` para un ejemplo de cómo arrancar esta
  fase con Claude Code.
- **Executor y Compliance**: agentes reales del harness, corren con el
  modelo local (LM Studio) o con DeepSeek vía API (ver "Motor por API" más
  abajo). Executor genera código a partir de `plan.json`, Compliance lo
  valida contra los criterios de aceptación de cada item.
- **Documentador**: agente real, motor local siempre (nunca necesita el
  razonamiento más fuerte de DeepSeek). Cuando un item pasa a `aprobado`
  habiendo tenido rechazo(s) real(es) antes, propone un candidato de
  documentación a partir de ese error real y su resolución real — nunca
  escribe directo en `knowledge/` ni en `.agents/rules/`, deja la propuesta
  en `.harness/logs/candidatos_conocimiento.md` para que el Planner decida.
  Ver `schemas/plan.contract.md`, sección "Candidatos de conocimiento".
- **Format check, validación de `plan.json`, smoke test, frontend check y
  catálogo de endpoints**: herramientas determinísticas (sin LLM) —
  `checks/format_check.py`, `checks/plan_validator.py`,
  `checks/smoke_test.py`, `checks/frontend_check.py`,
  `checks/api_endpoints.py`. No son agentes, corren dentro de
  `orchestrator.py` antes/después de Compliance (`smoke_test.py` para items
  backend, `frontend_check.py` para items frontend).
  `interfaz_real.py` (compartido entre Executor y Compliance) complementa
  la `interfaz` que predice el Planner con lo que Executor reportó de
  verdad al terminar cada item.

## Requisitos

- Python 3.12+
- Node.js 20.19+/22.12+ y Angular CLI, solo si el proyecto tiene items
  `tipo: "frontend"` — necesarios para `frontend_check.py` (`ng build` real
  antes de Compliance) y para bootstrapear el proyecto Angular (`ng new`,
  ver `frontend-angular.md` en el context pack del proyecto destino). Si no
  tenés Node del lado del sistema, instalarlo vía
  [nvm](https://github.com/nvm-sh/nvm) no necesita `sudo`.
- LM Studio corriendo con un modelo cargado (probado con
  `qwen/qwen3.8-27b`, un modelo "thinking" que corremos con el thinking
  siempre deshabilitado — ver Notas técnicas).
- Si corrés el harness en WSL2 y LM Studio en Windows: necesitás la IP del
  adaptador `vEthernet (WSL)`, no `localhost` (modo NAT default de WSL2 no
  comparte localhost entre ambos).

## Instalación

```bash
pip install -r requirements.txt --break-system-packages
```

`engines/anthropic_api.py` está comentado por completo (motor alternativo,
no en uso todavía) — no hace falta instalar `anthropic` para nada de lo que
funciona hoy.

## Configuración

Copiá `config/models.yaml.example` a `config/models.yaml` y ajustá
`engines.lm_studio.base_url` a tu IP real:

```bash
cp config/models.yaml.example config/models.yaml
```

`config/permissions.yaml` no necesita tocarse — define qué puede leer/
escribir cada agente y está pensado para no cambiar salvo que agregues un
agente nuevo. Ver la tabla completa en `handoff.md`.

### Motor por API (DeepSeek)

Si Executor/Compliance están configurados con `engine: deepseek` en
`config/models.yaml` (por ejemplo, cuando no tenés acceso al LM Studio
local), necesitás la variable de entorno `DEEPSEEK_API_KEY`. Dos formas:

```bash
export DEEPSEEK_API_KEY=tu-key
```

o dejarla en un archivo `.env` en la raíz del harness (no versionado, ver
`.gitignore`):

```
DEEPSEEK_API_KEY=tu-key
```

`engines/deepseek_api.py` habla contra `https://api.deepseek.com` (API
compatible con OpenAI), sin dependencias nuevas — usa `requests`, igual que
`lm_studio.py`. Con `model: deepseek-chat` no hay razonamiento separado; con
`model: deepseek-reasoner` (en uso por Compliance desde 2026-08-20 — ver
`Pendientes.md`, "¿Compliance debería usar deepseek-reasoner?") el adapter
maneja `reasoning_content` igual que el modelo "thinking" de LM Studio (ver
Notas técnicas más abajo).

### Motor por API (Kimi)

Alternativa a LM Studio para cuando no tenés acceso al motor local (ej. lejos
de tu máquina) — pensado originalmente para `executor`/`documentador`.
`executor_senior` corre en Kimi como motor **primario** desde 2026-08-30
(`config/models.yaml`, no un fallback); `compliance`/`arbitro` siguen en
DeepSeek. Necesitás la variable de entorno `KIMI_API_KEY` (mismo mecanismo
que `DEEPSEEK_API_KEY`: exportarla o dejarla en el `.env` de la raíz del
harness).

`engines/kimi_api.py` habla contra `https://api.moonshot.ai/v1` (endpoint
oficial de Moonshot AI, internacional — si tu cuenta es de la región China,
cambiar el default a `https://api.moonshot.cn/v1` en ese archivo). Sin
dependencias nuevas.

Modelo distinto por agente (`orchestrator.py::KIMI_MODEL_FALLBACK`, 2026-08-27):
`kimi-k2.7-code` para `executor` (genera código real, variante especializada)
y `kimi-k2.6` para `documentador` (solo clasifica/resume texto ya generado —
nunca necesitó la variante `-code` ni el razonamiento más fuerte, mismo motivo
por el que corre en el motor local en vez de `deepseek-reasoner`). Un agente
sin modelo Kimi mapeado ahí (ej. `arbitro`, que corre en DeepSeek) no tiene
fallback a Kimi.

**Fallback automático (recomendado):** no hace falta editar `config/models.yaml`
a mano cuando LM Studio no está disponible. Si `orchestrator.py` no logra
conectar con el motor local configurado para `executor`/`documentador`
(conexión rechazada/host inalcanzable — distinto de un timeout, que se trata
como un intento normal fallido), pregunta:

```
--- No se pudo conectar al motor local configurado para 'executor' ---
No se pudo conectar a LM Studio en http://192.168.100.248:1234/v1 (...)
¿Activar Kimi (kimi-k2.7-code) como alternativa para 'executor' por el resto de esta corrida? [s/N]:
```

Si aceptás, el resto de la corrida del proceso usa Kimi para ese agente —
`config/models.yaml` en disco no cambia, el override es en memoria y
desaparece al terminar el proceso. Con `--sin-confirmar` no hay a quién
preguntarle, así que se corta en vez de activar Kimi solo (evita empezar a
gastar la API sin que lo hayas pedido) — el loop escribe el motivo en
`.harness/logs/reporte_fallas.md`/consola y se detiene. Cada decisión
(activar o no) queda en `.harness/logs/decisiones_motor.jsonl`.

**Activación manual** (si preferís fijarlo de entrada, sin depender del
fallback), en `config/models.yaml`:

```yaml
executor:
  engine: kimi
  model: kimi-k2.7-code
```

Probar la conexión: `python tests/test_engines.py executor` (después de
cambiar el motor de `executor` en `config/models.yaml`, o tras activar el
fallback interactivamente).

## Uso

### 1. Inicializar el harness en el proyecto a migrar

```bash
python init_harness.py /ruta/al/proyecto-destino
```

Esto crea `.harness/{config,logs,validation,interfaces}/` dentro del
proyecto — no toca nada del código del proyecto en sí.

### 2. Armar `plan.json` (fase Planner — Felipe + Claude Code)

El contrato completo está en `schemas/plan.contract.md`, con un ejemplo
aplicado al fixture de pedidos en `schemas/plan.example.json`. Para un
ejemplo de cómo pedirle esto a Claude Code, ver
`docs/prompt-planner.example.md`. Antes de escribir `detalle_tecnico` para un
item que usa una librería no trivial, revisar/actualizar `knowledge/` (ver
`knowledge/README.md`) — evita repetir investigación ya hecha y patrones de
librería alucinados que Compliance/smoke test recién atrapan después.

El resultado se guarda en `<proyecto-destino>/.harness/config/plan.json`.
Antes de darlo por terminado, correr `python checks/plan_lint.py
/ruta/al/proyecto-destino` — chequeo heurístico (regex, no LLM) que detecta
`detalle_tecnico`/`interfaz` citando otro item sin tenerlo en `depende_de`, e
imports `app.*` que ningún item genera o cuyo dueño no está declarado como
dependencia (las dos clases de bug reales vistas en migraciones reales,
ver `handoff.md`), más items que por tamaño/ambigüedad son candidatos a
dividir en items más chicos (ver `handoff.md`, "Dividir items grandes en
sub-entregables"). A diferencia de `plan_validator.py` (estructural,
sin falsos positivos, corre automático), `plan_lint.py` puede marcar
referencias hacia adelante en la prosa como falso positivo — revisar cada
aviso, no asumir que todos aplican.

Crear también, desde el arranque, `<deployable>/docs/fixAplicados.md` y
`<deployable>/docs/recomendaciones-tecnicas.md` (vacíos si todavía no hay
ningún hallazgo) — documentos que crecen a mano durante toda la migración,
nunca un item de `plan.json` (ver `schemas/plan.contract.md`, "Documentos
de proyecto que crecen durante la migración").

### 3. Correr Executor / Compliance

```bash
# ver el estado de todos los items
python orchestrator.py /ruta/al/proyecto-destino --status

# generar código para el próximo item pendiente con dependencias listas
python orchestrator.py /ruta/al/proyecto-destino

# validar el próximo item que Executor ya terminó
python orchestrator.py /ruta/al/proyecto-destino --rol compliance

# forzar un item puntual en vez del que elegiría el orquestador
python orchestrator.py /ruta/al/proyecto-destino --item PED-002 --rol executor

# forzar executor_senior (motor más fuerte, resolutor final) para ESE item
# puntual ya, sin esperar a que --loop agote los reintentos normales primero
python orchestrator.py /ruta/al/proyecto-destino --item PED-002 --senior

# modo automático: encadena Executor/Compliance solo hasta que no quede
# nada ejecutable, preguntando antes de cada paso
python orchestrator.py /ruta/al/proyecto-destino --loop

# ídem, sin preguntar (desatendido)
python orchestrator.py /ruta/al/proyecto-destino --loop --sin-confirmar
```

En `--loop`, un item `rechazado` (por Compliance, o por el chequeo
determinístico de `format_check`/`frontend_check`/`smoke_test`/
`docker_check`) actualiza un **ticket de reintento**
(`.harness/logs/tickets/<item_id>.md` — ver `schemas/plan.contract.md`,
"Ticket de reintento") con lo esperado (`criterios_aceptacion`), el
historial completo de todos los intentos y el código actual, y ese ticket
es lo que recibe Executor para el reintento (no un feedback recalculado en
memoria cada vez). "Hechos verificados" es la única sección del ticket que
el harness nunca toca — población manual para el caso de oscilación (un
reintento arregla una cosa y rompe otra ya corregida).

Los reintentos van hasta `--max-reintentos` veces (default 1, o sea 2
intentos totales con el executor normal, ambos con thinking off; ver Notas
técnicas). Agotados esos, si `config/models.yaml` define un agente
`executor_senior` (motor más fuerte, hoy `kimi-k2.7-code`) se escala
automáticamente a él como intento final, pasándole el ticket completo (con
el historial de TODOS los rechazos previos, no solo el último). Pero antes
de cada reintento (sin `--sin-confirmar`) el loop escribe un reporte en
`.harness/logs/reporte_fallas.md` (referenciando el ticket) y para: podés
**[r]** reintentar con el ticket tal cual, **[e]** pausar para completar
"Hechos verificados" a mano en el ticket antes de reintentar, **[m]**
arreglarlo vos mismo y excluirlo del loop (después lo revalidás a mano con
`--item <id> --rol compliance` — Compliance sigue siendo el gate, incluso
para un fix manual), o **[n]** detener el loop. Cada decisión queda en
`.harness/logs/decisiones_reintento.jsonl`. Si se agotan los reintentos, o
si Executor queda `bloqueado` (el propio modelo dice que le falta
información — eso **no** se reintenta solo), también se escribe en
`reporte_fallas.md` y el loop sigue con otros items independientes en vez
de trabarse ahí. Con `--sin-confirmar` se salta la pregunta (reintenta con
el ticket tal cual) pero el reporte se sigue escribiendo.

Fuera de `--loop`, cada invocación hace un solo paso manual: correr sin
`--rol` hasta que un item quede `en_progreso`, correr con `--rol compliance`
para validarlo, repetir.

Al terminar `--loop` (por cualquier motivo: nada más ejecutable, detenido a
pedido, motor inalcanzable) se imprime una tabla con cuántas veces pasó
cada item por cada agente real (`executor`/`executor_senior`/`compliance`/
`arbitro`/`documentador` — no los chequeos determinísticos) **durante esta
sesión puntual** — desde que arrancó esta corrida de `--loop`, no el
acumulado histórico de todas las corridas anteriores del proyecto — útil
para detectar de un vistazo qué items oscilaron o necesitaron escalar de
más en el trabajo que se acaba de hacer. `python orchestrator.py
/ruta/al/proyecto-destino --metricas` muestra en cambio el historial
completo acumulado en `.harness/logs/metricas_agentes.jsonl`, en cualquier
momento, sin ejecutar nada.

### 4. Ver el estado de TODOS los proyectos de un vistazo

```bash
# resumen de todos los proyectos registrados en config/proyectos.yaml
python estado_proyectos.py

# detalle item por item de uno puntual
python estado_proyectos.py --detalle "nombre-del-proyecto"
```

`config/proyectos.yaml` es un registro liviano y de mantenimiento manual
(nombre + ruta + descripción corta) — agregar una entrada cuando arranca
una migración nueva. `estado_proyectos.py` nunca guarda un snapshot: cada
corrida relee el `.harness/` real de cada proyecto y calcula su estado con
`orchestrator.calcular_estados()` (el mismo mecanismo que
`orchestrator.py <proyecto> --status`, sin duplicar el algoritmo). Un
proyecto con ruta rota o sin `plan.json` todavía se reporta igual, no
rompe el resumen de los demás.

## Estructura del harness

```
harness/
├── access_control.py       <- AgentFileGuard: único punto por el que los
│                                agentes tocan archivos, según permissions.yaml
├── orchestrator.py          <- decide qué item ejecutar/validar (no es un agente)
├── interfaz_real.py          <- lee/mezcla la interfaz real que Executor reporta
├── init_harness.py            <- crea .harness/ en un proyecto destino
├── agents/
│   ├── executor.py            <- genera código de un item
│   ├── compliance.py           <- valida un item contra sus criterios
│   └── documentador.py          <- propone candidatos de conocimiento (no escribe knowledge/ directo)
├── engines/                     <- adapters de motor de inferencia (LM Studio, Anthropic)
├── checks/                       <- herramientas determinísticas (sin LLM)
│   ├── format_check.py             <- imports rotos / nombres que se pisan
│   ├── plan_validator.py            <- reglas estructurales de plan.json (gate automático)
│   ├── plan_lint.py                   <- avisos heurísticos sobre plan.json (manual, puede tener falsos positivos)
│   ├── api_endpoints.py               <- catálogo de endpoints desde interfaz.endpoint del plan
│   ├── generate_api_docs.py             <- catálogo de endpoints desde app.openapi() real
│   ├── smoke_test.py                     <- corre pytest real contra tests_requeridos
│   └── frontend_check.py                  <- corre `ng build` real contra items frontend
├── config/
│   ├── models.yaml                <- tu configuración real (no versionada como ejemplo)
│   ├── models.yaml.example         <- template, copiá y ajustá
│   └── permissions.yaml             <- permisos filesystem por agente
├── schemas/
│   ├── plan.contract.md               <- contrato de plan.json
│   ├── plan.example.json               <- ejemplo aplicado al fixture de pedidos
│   └── api-endpoints.example.md         <- ejemplo del catálogo de endpoints
├── knowledge/                    <- patrones de librería verificados, uno por archivo
│                                     (consultados/actualizados por el Planner, no por Executor)
├── docs/
│   └── prompt-planner.example.md      <- ejemplo de prompt para la fase Planner
└── tests/
    ├── test_engines.py             <- prueba manual de conexión al motor
    ├── test_frontend_check.py       <- prueba manual de frontend_check.py (necesita Node real)
    ├── test_plan_lint.py             <- tests sin red de plan_lint.py
    └── test_executor_logic.py       <- tests sin red de Executor/Compliance/orchestrator
```

Y dentro de cada proyecto destino que el harness procese:

```
proyecto-destino/
└── .harness/
    ├── config/plan.json               <- escribe el Planner, INMUTABLE después
    ├── logs/executor.jsonl             <- bitácora append-only de Executor
    ├── logs/reporte_fallas.md           <- un rechazo por entrada (pendiente de decisión o ya agotado)
    ├── logs/decisiones_reintento.jsonl   <- qué elegiste en el gate de cada rechazo, con timestamp
    ├── logs/tickets/<item_id>.md          <- ticket de reintento: lo esperado, historial de intentos,
    │                                          código actual, y "Hechos verificados" (población manual)
    ├── validation/<item_id>.json         <- último veredicto de Compliance por item
    └── interfaces/<item_id>.json          <- interfaz real que Executor reportó (sobreescrito completo)

proyecto-destino/
└── docs/api-endpoints.md      <- catálogo de endpoints backend aprobados, regenerado
                                   completo por api_endpoints.py cada vez que Compliance
                                   aprueba un item backend (fuera de .harness/: es
                                   documentación del producto, no metadata del harness)
```

## Tests

```bash
python tests/test_executor_logic.py   # sin red, valida parseo + selección de items
python tests/test_engines.py executor # con LM Studio corriendo, valida la conexión
python tests/test_frontend_check.py /ruta/al/proyecto-destino  # necesita Node/Angular real
python tests/test_plan_lint.py        # sin red, valida los avisos heurísticos sobre plan.json
```

## Limitaciones conocidas / pendientes de diseño

Ver `Pendientes.md` — hoy incluye el agente de recuperación de LM Studio vía
SSH y la técnica de dividir items grandes en sub-entregables más chicos para
el modelo local. Ninguno de los dos está implementado a propósito, están
documentados para retomar la conversación de diseño antes de escribir código.

## Notas técnicas

- El modelo probado (`qwen/qwen3.8-27b`) es "thinking": gasta tokens en
  `reasoning_content` antes de escribir la respuesta final.
  `ModelEngine.run()` acepta `enable_thinking` (default `True`);
  `engines/lm_studio.py` lo traduce a `chat_template_kwargs.enable_thinking`
  + `reasoning_effort: "none"` a nivel top del body (NO anidado en
  `chat_template_kwargs`) cuando es `False`. Ambos campos hacen falta:
  `chat_template_kwargs.enable_thinking` solo no alcanza porque este modelo
  expone además "Reasoning Effort" (default `xhigh` en su model card de LM
  Studio), que lo pisa si no se manda también — confirmado empíricamente
  contra `qwen/qwen3.8-27b` el 2026-08-21 (con solo `enable_thinking=False`,
  `reasoning_tokens` seguía > 0; agregando `reasoning_effort: "none"` en el
  top del body da `reasoning_tokens=0` de forma consistente). El clásico
  sufijo `/no_think` de Qwen3 NO funciona vía API en este modelo/versión de
  LM Studio (probado, lo ignora). `orchestrator.py::loop()` (y el modo manual
  `--rol executor`) pone `enable_thinking=False` en el primer intento de cada
  item y `True` en el reintento, con `max_tokens` más alto para el reintento
  (`MAX_TOKENS_EXECUTOR_THINKING`, ver comentario junto a `ESTADOS`) —
  en pruebas en vivo (2026-08-21) con el modelo cargado a un context length
  chico (default de LM Studio), 3 corridas seguidas del mismo reintento con
  thinking on fallaron por 3 motivos distintos (archivo truncado a mitad,
  bloque mal formado, tokens agotados rumiando sobre qué incluir en
  `### INTERFAZ` sin converger); subir `max_tokens` solo no lo arregló, el
  modelo solo razonaba más tiempo. Sospecha sin confirmar: el problema real
  era contexto insuficiente para prompt + razonamiento largo, no el
  thinking en sí — pendiente de reconfirmar con el modelo recargado a 64k de
  contexto. Si por algún motivo el modelo igual entra en modo thinking y
  `max_tokens` es muy bajo, se queda
  sin tokens pensando y la respuesta viene vacía — el motor lo detecta y lanza
  un error explícito (distinto según sea por bucle de repetición o simplemente
  por límite insuficiente).
- Valores calibrados hoy: Executor `max_tokens=9000` (items de ~3 archivos),
  Compliance `max_tokens=6000` (items de ~4 criterios). Están hardcodeados
  en `agents/executor.py` / `agents/compliance.py` — si un item pide más,
  puede volver a no alcanzar.
- **Veredictos desactualizados en reintentos**: cuando Executor regenera
  código para un item ya `rechazado`, el veredicto viejo de Compliance no
  se borra solo — `orchestrator.py` lo detecta comparando timestamps
  (`_veredicto_desactualizado`) y vuelve a mandar ese item a Compliance en
  vez de tratarlo como si ya estuviera resuelto. Sin esto, `--loop`
  reintentaba Executor varias veces seguidas sin que Compliance revisara
  nada en el medio (bug real encontrado y corregido probando contra LM
  Studio real, ver `handoff.md`).
