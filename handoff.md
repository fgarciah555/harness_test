# Handoff — Harness multiagente para migración de monolitos

## Objetivo del proyecto

Harness que usa un motor de IA (local o vía API) para automatizar la
migración de monolitos legados (Flask + Jinja2, sesión de servidor) hacia
la arquitectura objetivo de la empresa: backend FastAPI + frontend Angular
separados, siguiendo las reglas de un context pack propio (`AGENTS.md` +
`.agents/rules/`: naming-conventions, backend-architecture, frontend-angular,
security-baseline, error-handling, gcp-deployment, local-development,
architecture-decisions).

Entorno: WSL2 en Windows, LM Studio corriendo del lado Windows. Conexión
resuelta vía IP del adaptador `vEthernet (WSL)` (no `localhost`, por el modo
de red NAT default de WSL2).

## Decisión de arquitectura importante — quién hace qué

Originalmente el plan era 6 agentes corriendo dentro del harness (Analyzer,
Planner, Executor, Compliance, Format check, Smoke test). **Esto cambió**:

- **Analyzer y Planner los hacemos Felipe + Claude directamente**, leyendo el
  proyecto real (con ayuda de Claude Code), no como agentes automatizados —
  el modelo local rinde peor en razonamiento multi-paso, y esta parte se
  beneficia de contexto completo del repo + criterio humano.
- El harness como tal solo corre **Executor** (genera código) y
  **Compliance** (valida contra el plan) con el motor configurado.
- Format check y Smoke test son herramientas determinísticas (ast/linters,
  no LLM) — ambas terminaron implementándose (ver más abajo).

## Estructura de archivos (vigente)

```
harness/                          <- vive fuera de cualquier proyecto, herramienta standalone
├── access_control.py              <- guardia de permisos por agente (AgentFileGuard)
├── orchestrator.py                 <- decide qué item de plan.json ejecutar, loop, métricas
├── init_harness.py                  <- crea .harness/ en un proyecto destino
├── Pendientes.md                     <- ítems de diseño abiertos, no implementar sin retomar
├── knowledge/                         <- patrones de librería verificados contra doc real
├── engines/                            <- adapters de motor (lm_studio, deepseek, kimi, anthropic)
├── agents/                              <- executor, compliance, arbitro, documentador
├── checks/                               <- format_check, smoke_test, frontend_check, docker_check, plan_lint, plan_validator
├── config/                                <- models.yaml, permissions.yaml, proyectos.yaml
└── schemas/                                <- plan.contract.md (contrato vigente), ejemplos
```

Dentro de cada proyecto real que el harness procesa:

```
mi-proyecto/
└── .harness/
    ├── config/           <- plan.json (Planner)
    ├── logs/              <- bitácora de Executor, tickets de reintento, métricas
    └── validation/         <- veredictos de Compliance
    └── handoff.md            <- bitácora propia del proyecto (negocio, no del harness)
```

## Tabla de permisos (`access_control.py`)

| Agente | Código del proyecto | .harness/config | .harness/logs | .harness/validation |
|---|---|---|---|---|
| Analyzer | sin acceso | sin acceso | sin acceso | sin acceso |
| Planner | sin acceso | lectura+escritura | sin acceso | sin acceso |
| Executor | lectura+escritura | solo lectura | escritura | sin acceso |
| Compliance | solo lectura | solo lectura | solo lectura | escritura |

`AgentFileGuard` es el único punto por el que los agentes tocan archivos —
lanza `PermissionError` si algo se sale de la tabla.

`orchestrator.py` decide **qué** item ejecutar (estado efectivo = `plan.json`
+ `.harness/logs/executor.jsonl` + `.harness/validation/*.json`). No pasa por
`AgentFileGuard` a propósito: no es uno de los agentes, es el driver que los
invoca, y necesita leer permisos que ningún agente individual tiene.

## Contrato `plan.json` (ver `schemas/plan.contract.md`)

Estructura: `metadata` + `decisiones_globales` (auth destino, prefijo API,
casing JSON, manejo de errores) + `items[]` (id, origen, tipo backend/frontend
— nunca mezclado en un mismo item —, archivos a tocar, detalle técnico,
criterios de aceptación verificables, `depende_de`, `interfaz`, estado) +
`riesgos_heredados[]`.

Dos decisiones de diseño clave:

- **`plan.json` es inmutable una vez escrito por el Planner.** `estado` en el
  item solo puede ser `"pendiente"` u `"omitido"` — nunca se reescribe. El
  progreso real (en_progreso/bloqueado/completado/rechazado) se calcula
  combinando `plan.json` con la bitácora de Executor y el último veredicto de
  Compliance.
- **`interfaz` por item** — lo que un item expone hacia afuera, separado del
  `detalle_tecnico` completo. Cuando Executor toma un item, su contexto de
  entrada es SOLO `decisiones_globales` + el item en sí + la `interfaz` (no el
  item completo) de cada id en `depende_de` — contexto acotado, sin releer
  historial completo.

## Primeras pruebas reales (2026-08-19) — ciclo Executor→Compliance contra un monolito real

Primera migración real de backend (login + reportes de un monolito Flask +
PostgreSQL). Alcance acotado a propósito: solo backend, sin frontend, sin
login admin ni reportes en la primera pasada.

**Lección 1 — `interfaz` no es una instrucción de generación.** Un item
declaraba `interfaz.dependencia_reusable` (una función a exponer) pero nunca
se generó, porque Executor arma su contexto con la `interfaz` de sus
*dependencias*, no la propia — nunca lee la interfaz de su propio item. Regla:
si algo tiene que existir, hay que pedirlo también en `detalle_tecnico`,
declararlo en `interfaz` no alcanza.

**Lección 2 — ni Executor ni Compliance leen `AGENTS.md`/`.agents/rules/`
nunca, por diseño.** Un item hizo `commit()` en el repository en vez del
service, violando una regla `always_on` de `backend-architecture.md` — no
porque el modelo la ignoró, sino porque el Planner nunca la tradujo a
`decisiones_globales`/`criterios_aceptacion`. Los agentes solo ven lo que el
plan les da. El Planner debe barrer sistemáticamente las reglas `always_on`
del context pack al armar `decisiones_globales`, no confiar en acordarse de
cada una por item.

**Lección 3 — calibración de `max_tokens` con código real es mucho más alta
que con un fixture de juguete.** Fixture (items de 3 archivos): Executor
`9000` alcanzaba (~6100 tokens de salida). Código real (items de 6 archivos,
8-11 criterios): insuficiente de nuevo, subió a `20000` Executor / `16000`
Compliance. La calibración del fixture no predijo la del código real — quedó
corta por más del doble.

**Gaps de infraestructura compartida que nadie pidió explícitamente**, mismo
patrón repetido varias veces: un archivo base (config/settings, conexión a
BD, `main.py` que monta routers) que ningún item individual tenía en su plan
porque nadie es "dueño" natural de él. Se agregaron items nuevos cada vez que
apareció. `main.py` en particular salió con imports completamente rotos la
primera vez (tratando módulos planos como si fueran paquetes) — Compliance lo
rechazó 3 veces por otra razón (un falso rechazo de prefijo de ruta) sin ver
nunca el problema real; se corrigió a mano por ser mecánico y unívoco, y
después el propio mecanismo del harness (format check + reintento con
feedback exacto) lo regeneró bien, exponiendo dos mejoras nuevas:

1. **`format_check.py`** (nuevo) — chequeo determinístico (`ast`, sin
   ejecutar nada) que corre antes de cada llamada a Compliance. Detecta
   imports internos (`app.*`) que no resuelven a ningún archivo real, y
   nombres definidos que pisan un import. Si encuentra algo, escribe un
   veredicto `rechazado` sintético sin gastar ninguna llamada al modelo.
2. **Regla nueva en `plan.contract.md`**: todo lo que otro item vaya a
   importar necesita la línea de import literal en la `interfaz` (no solo el
   nombre), y si `detalle_tecnico` cita otro item, ese item tiene que estar
   en `depende_de` — las dependencias no son transitivas.

Se decidió explícitamente **no** hacer que Compliance recomiende cómo
arreglar lo que rechaza (mezclaría juzgar con prescribir).

Otros dos bugs de integración encontrados leyendo código a mano, ninguno
atrapado por Compliance (evalúa cada item aislado, nunca los archivos de
otros items): una colisión de rutas real entre dos endpoints, y una función
de router que pisaba el nombre de la función importada del service
(recursión infinita potencial). Y un patrón de import mal resuelto en 5 de 7
archivos que necesitaban una excepción de dominio — la causa exacta era que
esos items dependían de otro item indirecto, sin recibir el import literal,
solo la mención en prosa. Confirma la regla nueva de arriba.

Se creó además la convención `deuda_negocio.md` en la raíz del proyecto
destino (no parte del harness): decisiones no técnicas documentadas en
lenguaje simple para que el cliente decida, separado de `riesgos_heredados`
(para el equipo técnico).

**Resultado:** el backend completo de esa primera migración quedó
`completado`, `py_compile` limpio en los archivos generados. Primer ciclo
Planner→Executor→Compliance exitoso contra un monolito real de punta a
punta.

## Qué falta construir (siguiente paso lógico, snapshot 2026-08-19)

1. Correr el backend de verdad con dependencias instaladas — nadie lo había
   probado ejecutando, `format_check.py` valida imports por análisis
   estático, no reemplaza correr la aplicación.
2. **Catálogo de endpoints** — paso determinístico que regenera
   `docs/api-endpoints.md` cada vez que Compliance aprueba un item backend.
   Terminó reemplazado más adelante por `generate_api_docs.py` (ver abajo).

**Ciclo automático (`--loop`)** — encadena Executor/Compliance, reintenta un
`rechazado` hasta `--max-reintentos` (default 2 = 3 intentos totales)
pasándole a Executor el motivo del rechazo, no reintenta un `bloqueado`, y
escribe un reporte de fallas cuando se agotan los intentos. Por defecto
pregunta antes de cada paso; `--sin-confirmar` lo hace desatendido.

## Notas técnicas — modelos "thinking" y calibración

- Un modelo "thinking" gasta tokens en `reasoning_content` antes de escribir
  la respuesta final. Si `max_tokens` es muy bajo y `finish_reason ==
  "length"`, el contenido viene vacío. `lm_studio.py` lanza `RuntimeError`
  explícito tanto si detecta bucle de repetición como si simplemente se
  queda sin tokens, con mensaje distinto para cada caso.
- **Calibración de `max_tokens`**, subió dos veces (fixture → código real,
  ver arriba) — patrón: un valor calibrado contra un caso chico no predice
  el de código real, puede quedar corto por más del doble. Vale la pena
  tratarlo como configuración por proyecto (`models.yaml`), no un valor fijo
  en código.
- La URL de LM Studio en `models.yaml` debe ser `http://<ip>:1234/v1`, SIN
  `/models` al final.

## Replanificación completa desde cero, Smoke test validado en vivo (2026-08-21)

Se rehizo el plan desde cero aplicando de entrada todas las lecciones ya
documentadas (interfaz con imports literales, `ticket_id`, barrido de reglas
`always_on`, tests requeridos). El plan completo quedó `completado`, con
`py_compile` limpio y los tests requeridos pasando de verdad.

**Bug real del harness — `agents/compliance.py` nunca mandaba
`detalle_tecnico`.** A diferencia de Executor, Compliance armaba su contexto
sin incluir `detalle_tecnico` — cualquier criterio de aceptación que citara
"según detalle_tecnico" era estructuralmente imposible de verificar, y
rechazaba con "no puedo verificarlo". Corregido en una línea + tests de
regresión. Regla general: Compliance debería tener SIEMPRE más contexto que
Executor, nunca menos.

**Smoke test (`tests_requeridos`) corrió de punta a punta contra código real
por primera vez, con éxito, encontrando bugs reales antes de gastar
Compliance:**
- Patrón de test mal escrito, repetido tres veces: un `monkeypatch.setenv`
  que solo seteaba una variable de settings obligatoria sin las otras,
  reventando por `ValidationError` en vez de por el bug real que el test
  quería probar. Resuelto con un `.env` real de desarrollo (valores de dev,
  no secretos) en vez de depender solo de monkeypatch.
- Bugs reales de librería atrapados por el smoke test, corregidos vía
  `detalle_tecnico` más preciso + reintento: `pydantic-settings` usa el
  kwarg `_env_file` (con guion bajo) para override en instancia, no
  `env_file`; `HTTPBearer`/`HTTPAuthorizationCredentials` se importan de
  `fastapi.security`, no de `fastapi`; `db.scalars(stmt).first()/.all()` en
  vez de `db.execute(stmt).first()/.all()` (esto último devuelve `Row`, no
  la instancia de la entidad).
- Bug de dependencia declarada de menos: un item necesitaba un símbolo de
  otro item que nunca estaba en su `depende_de` — el import correcto nunca
  llegaba al contexto de Executor por más que se dijera en prosa que estaba
  mal. Al agregar la dependencia faltante, aprobó directo.
- Bug de forma ambigua en queries con JOIN: una columna se leía de la tabla
  equivocada porque el `detalle_tecnico` decía "join + columnas" sin
  especificar de qué tabla sale cada una. Se reescribió especificando
  columnas explícitas por tabla y una forma de retorno uniforme
  (`.mappings().all()`) — más verificable para Compliance.
- Dos rechazos fueron fallas transitorias del motor (respuesta vacía) —
  reintentar sin cambiar nada bastó.
- Un bloqueo real por bucle de repetición del modelo local, en dos
  ocasiones — el mecanismo de detección existente lo cortó limpio;
  reintentar resolvió ambas veces.

**Bug propagado del maestro del harness, no solo del proyecto:** un fix a
`frontend-angular.md` (quitar la dependencia de un cookiecutter interno) se
había hecho antes solo en la copia del proyecto, nunca se propagó de vuelta
al maestro en `Harness/.agents/rules/`. Al borrar ese proyecto para
replanificar, el fix se perdió con él y casi se reintrodujo el mismo bug.
Ver memoria `feedback_context_pack_sync_to_master`.

## Segunda y tercera pasada completa desde cero — `executor_senior` nace de una oscilación real (2026-08-21)

Repitiendo el flujo de punta a punta, un item con 4 funciones osciló 3
rechazos seguidos entre dos bugs reales distintos: cada reintento automático
solo recibía el motivo del ÚLTIMO rechazo (texto, sin código), así que el
modelo corregía uno y sin querer pisaba el otro.

**Dos mejoras nacidas de esta evidencia:**

1. **El feedback de reintento ahora incluye el contenido ACTUAL de los
   archivos del item**, no solo el texto de qué falló. Antes cada reintento
   regeneraba los archivos completos a ciegas. Con el código actual a la
   vista, el modelo corrige puntualmente en vez de reescribir desde cero —
   los reintentos siguientes convergieron en 1-2 vueltas, sin oscilar.
2. **Agente `executor_senior`** — resolutor final que el loop invoca UNA
   sola vez por item, solo cuando el executor normal agotó los reintentos y
   sigue rechazado. Recibe el HISTORIAL COMPLETO de todos los motivos de
   rechazo distintos vistos en la corrida (no solo el último) + el código
   actual, corriendo en un motor distinto (vía API, sin el mecanismo de
   bucle del motor local). Probado en vivo contra el item real que estaba
   oscilando: con historial + código actual, el motor corrigió los dos bugs
   a la vez, sin regresión, aprobado al primer intento.

En una repetición posterior, `executor_senior` se disparó dos veces más en
una corrida real (no simulado a mano), ambas veces resolviendo lo que el
executor normal no lograba:
- Un item con 2 rechazos seguidos por el MISMO error de fondo (imports
  tratando módulos planos como paquetes) — resuelto a la primera con una
  tabla de import correcto explícita en el feedback.
- El caso más severo de oscilación visto hasta ese punto — 3 rechazos
  seguidos, cada reintento normal corregía una clave de dict pero perdía o
  rompía otra ya corregida. El senior lo resolvió al primer intento, pero
  solo después de pasarle una **tabla de verdad explícita con las claves
  exactas por función** (verificada a mano) en vez de solo el historial de
  rechazos en prosa. Lección: cuando el patrón es "mismatch de
  claves/nombres verificable mecánicamente", el feedback debería incluir una
  tabla de hechos verificados, no solo la descripción del último error.

`executor_senior` también resolvió, en otra ocasión, un caso de bucle de
repetición del motor local que llevaba 3 intentos seguidos sin converger —
confirma que sirve tanto para "modelo se equivoca reiteradamente" como
salida de emergencia cuando el motor local se traba.

**Tercera mejora — Executor puede rechazar en vez de adivinar imports
internos.** El mecanismo `### BLOQUEADO` ya existía para "info insuficiente o
ambigua", pero el modelo no lo usaba para imports internos no declarados —
pattern-matcheaba una ruta plausible en vez de reconocer que estaba
adivinando. Se agregó una regla explícita al `SYSTEM_PROMPT`: si necesita
importar un símbolo `app.*` que no está en la `interfaz` de ninguna
dependencia declarada, debe responder `### BLOQUEADO` señalando exactamente
qué símbolo falta. Probado en vivo con dos items sintéticos (mismo símbolo,
uno con dependencia declarada y otro sin declarar): el primero generó código
normal, el segundo bloqueó con precisión en vez de adivinar.

## Row schemas Pydantic reemplazan `list[dict]` en queries con JOIN (2026-08-21)

Dos causas raíz por resolver, no solo parchear: (1) los repositories que
hacen JOIN devolvían `list[dict]`, un contrato no verificable — el service
adivinaba las claves y fallaba en runtime (la oscilación severa de arriba);
(2) el Planner no estaba investigando patrones de librería de forma visible
antes de escribir `detalle_tecnico` — un bug real de uso incorrecto de
`COALESCE` (no es un símbolo de nivel de módulo en SQLAlchemy, es
`func.coalesce(...)`) es evidencia de eso.

**Solución:** en vez de darle al service más contexto para adivinar mejor,
el repository devuelve `list[RowSchema]` (un `BaseModel` de Pydantic definido
junto al repository) en vez de `list[dict]` — el nombre de la clase pasa a
ser un símbolo real que la `interfaz` declara con import literal, así el
service ya no adivina, importa.

**Antes de tocar `plan.json`, se corrigió la base que lo alimenta** (pedido
explícito: la investigación tiene que ser visible, no asumida):
1. `knowledge/sqlalchemy-2.0.md` — la entrada de JOIN recomendaba
   activamente `list[dict]` (marcada obsoleta con nota `ACTUALIZADO`, no
   borrada); se agregó la entrada de `COALESCE`/`func.coalesce`.
2. `.agents/rules/backend-architecture.md` — regla nueva: retorno de
   queries con columnas explícitas es SIEMPRE un row schema Pydantic, nunca
   `dict`.
3. Recién ahí se reescribieron los dos items afectados en `plan.json`.

**Resultado, reset completo y re-corrida:** los dos items reescritos
aprobaron AL PRIMER INTENTO cada uno, sin ninguna oscilación (comparado con
7+ intentos y escalada a `executor_senior` en la corrida anterior con
`list[dict]`). Evidencia fuerte de que la causa raíz real de la oscilación no
era "el modelo necesita más contexto" — era que el contrato de retorno no
era verificable.

**Bug real encontrado por el smoke test cruzando items, no por Compliance:**
un repository ya aprobado tenía un import de librería roto (`COALESCE` en
vez de `func.coalesce`) que ni `format_check.py` (solo revisa imports
internos `app.*`) ni Compliance atraparon — salió a la luz cuando el smoke
test de un item dependiente importó la app completa. Confirma la tesis del
Smoke test: revisión estática de un item aislado no ve bugs de integración
entre archivos, ejecutar (aunque sea solo importar) sí. Se corrigió
regenerando el item roto (no a mano) y el mecanismo de invalidación de
dependientes marcó automáticamente sus 4 dependientes para revalidación.

## Backend cerrado de punta a punta — mejoras varias (2026-08-22)

**Modelo canónico grounded contra schema real de BD (implementado).** Con el
DDL real de la base de datos disponible, se formalizó en `plan.contract.md`
(`decisiones_globales.schema_bd_origen`): antes de escribir el modelo de una
entidad persistida, el Planner lee el schema real, no lo adivina. Encontró y
corrigió de raíz un bug real de limpieza de RUT chileno (reconcatenaba el
dígito verificador antes de convertir a entero — falla con cualquier DV
letra), columnas mal tipeadas por el nombre, y confirmó que varias tablas
transaccionales no tienen PK real → regla nueva: tablas sin key natural van
como `sqlalchemy.Table` de Core, no ORM declarativo.

**Comparación de modelos LM Studio, con evidencia.** `qwen3.6-35b-a3b`
entró en bucle de repetición real en el primer reintento de un item trivial;
un chequeo nuevo (`test_engines.py --check-thinking`) midió que razona ~6.7x
más verboso que `qwen3.8-27b` incluso en preguntas triviales. Se quedó con
`qwen3.8-27b`, documentado en `config/models.yaml`.

**Timeout de motor ya no crashea el proceso (implementado).**
`engines/base.py::TimeoutDelMotor` + manejo dedicado en `agents/executor.py`:
un timeout de conexión se trata como intento fallido normal (0 archivos), no
como `bloqueado` — el loop de reintentos/escalado se hace cargo solo.

**Chequeo nuevo: imports no usados.** `format_check.py` suma un 4to chequeo
vía `ruff` (F401). Código muerto a nivel de *proyecto entero* se probó con
`vulture` y se descartó con evidencia (50+ falsos positivos por wiring de
FastAPI/Pydantic, cero valor único sobre el chequeo por-item).

**Documentación de API generada desde la app real, no desde `plan.json`
(implementado).** El mecanismo original (basado en `interfaz.endpoint`)
resultó no-funcional para planes que agrupan varios endpoints por router. Se
construyó `generate_api_docs.py`: importa `app.openapi()` de la app real (sin
servidor corriendo) y genera `docs/openapi.json` + un `curl` real por
endpoint con ejemplos armados desde el schema real.

**Bugs reales encontrados probando contra Postgres local de verdad:** casing
inconsistente entre request (camelCase literal) y response (snake_case +
alias); export CSV armaba una sola fila en vez de una por elemento; falta de
`db.rollback()` tras una falla de negocio dejaba la sesión con transacción
abortada; y un bug real y confirmado heredado del monolito original (claves
comparadas/guardadas sin encriptar) — decisión explícita de arreglarlo de
verdad en la migración, no replicarlo, documentado en `riesgos_heredados`.

**Oscilación real (3+ veces) por causa del propio proceso, no del modelo.**
Las correcciones se pasaban solo como `feedback` puntual de una llamada,
nunca se escribían al `detalle_tecnico` — cada regeneración desde cero las
perdía. Lección: un fix confirmado en un `feedback` tiene que volcarse a
`detalle_tecnico` de inmediato.

**Contaminación entre archivos de test:** una variable de entorno seteada a
nivel de módulo (necesaria para que un test importe la app en collection)
nunca se revertía como sí hace `monkeypatch.setenv` — contaminaba otro
archivo de test al correr la suite completa. Corregido con
`monkeypatch.delenv(...)` explícito.

**Estado final:** backend completo `completado`, suite de tests en verde,
login/reportes/CSV/admin verificados con `curl` real y Postgres local real.

## Reorganización de bitácora (2026-08-24)

`Pendientes.md` quedó exclusivamente para ítems de diseño todavía abiertos.
Las secciones de bitácora ya resueltas (bugs/decisiones del harness con
`Estado: implementado/resuelto/descartado`) migraron a este archivo. El
contenido específico de negocio de cada migración (bugs de negocio,
decisiones de UI, rondas de rediseño visual) vive en el `handoff.md` propio
de cada proyecto, no acá — la evidencia real que motivó una decisión del
harness es evidencia de su comportamiento, no contenido del proyecto en sí.

## Smoke test real — Compliance corriendo pytest, no solo revisión estática

**Contexto:** Compliance solo leía el código generado y opinaba si cumplía
`criterios_aceptacion` — nunca lo ejecutaba. Construir esta pieza (pensada
desde el diseño original y nunca implementada) da verificación de
comportamiento real, es determinístico y no gasta tokens, y si falla, el
stack trace real es mejor feedback para un reintento que un resumen en
prosa.

**Implementado:** `smoke_test.py` + campo `tests_requeridos` en `plan.json`
+ wiring en `orchestrator.validar_con_format_check` (corre después de
format check, antes de Compliance). Decisiones: los tests los escribe el
Planner en `plan.json`, no Compliance ni Executor inventándolos al vuelo;
pytest corre primero (gratis), solo si pasa se gasta la llamada a
Compliance; aislamiento mínimo por ahora (timeout al proceso); se asume que
el venv del proyecto destino ya tiene pytest.

**Probado en vivo contra un proyecto real, con éxito.** El smoke test corrió
pytest real contra el venv del proyecto en cada item con tests requeridos —
atrapó bugs reales (imports rotos, kwargs de modelo equivocados, forma de
retorno de queries mal referenciada) antes de gastar Compliance. Limitación
que sigue vigente: los tests escritos son de lógica de negocio con
dobles/monkeypatch, no integración contra un Postgres real (sin instancia de
test disponible en este entorno).

## Agente de "bajada" de errores — revisado y resuelto de otra forma

**Contexto:** migrando de punta a punta, varios rechazos de items distintos
resultaron tener la MISMA causa raíz (ej. varios archivos adivinando mal el
mismo import). La idea original era un agente que lea los rechazos
acumulados y arme un resumen + una pista más precisa para el próximo
reintento.

**Por qué no se construyó así:** dos mejoras deterministas más baratas
(imports literales en `interfaz` + `depende_de` obligatorio, y
`format_check.py`) atacan la misma causa sin necesitar un agente nuevo. Tras
esas dos, el patrón original no volvió a aparecer — confirmado con
evidencia, no solo supuesto.

**Surgió un problema distinto, más urgente:** no había registro de las veces
que un humano tuvo que intervenir en el ciclo Executor-Compliance, y Felipe
prefiere mantenerse en el loop para decisiones de reintento en vez de
automatizar Analyzer/Planner (ver "Agente investigador de tecnologías" y
"Dividir items grandes", ambas en `Pendientes.md`, pausadas por la misma
preferencia).

**Implementado:** un gate de decisión en `orchestrator.py::loop()`, antes de
cada reintento (sin `--sin-confirmar`): escribe un reporte del rechazo,
pregunta **[s]** seguir con el reintento, **[t]** escribir un ticket con
solución propuesta, **[m]** arreglar a mano y excluir del loop (revalidado
después con Compliance, nunca se salta el gate), o **[n]** detener el loop.
Cada decisión queda registrada en un log aparte, cerrando el gap de "no hay
registro". (Este gate evolucionó más adelante al "Ticket de reintento", ver
sección de 2026-08-30.)

**Reporte a `knowledge/` cuando el fix resuelve el error** solo aplica si la
causa raíz es un patrón de librería reusable entre proyectos — el escritor
es el Planner (hoy Felipe+Claude), no un agente nuevo. Un fix de lógica de
negocio propia o un bug del harness mismo queda en el reporte de fallas, no
en `knowledge/`.

## Interfaz real reportada por Executor, no solo la predicha por el Planner (2026-08-20)

**Contexto:** Executor inventó un import de una excepción de dominio hacia un
módulo que no existía — el símbolo real vivía en otro módulo. El format
check lo atajó antes de gastar Compliance, pero el reintento necesitó pasar
el import correcto a mano. La `interfaz` en `plan.json` la escribe el
Planner *antes* de que el código exista — es una predicción, y puede quedar
incompleta.

**Idea:** que Executor, al terminar un item, reporte qué funciones/clases
quedaron pensadas para ser reusadas, con su firma real — documentación
generada desde el código que realmente escribió, no una predicción previa.
Pedirle al modelo que describa lo que acaba de escribir es mucho más
confiable que pedirle que adivine código nuevo.

**Implementado:** `.harness/interfaces/<item_id>.json` + bloque
`### INTERFAZ` en Executor + `interfaz_real.py` (unión por `import`, la real
gana en conflicto). No resuelve los falsos rechazos de Compliance por
archivos de infraestructura compartida que no pertenecen a ningún item
puntual (ej. el prefijo de ruta en un ensamblador) — eso es un problema de
visibilidad distinto, atacado más adelante ("contexto ampliado de
Compliance").

## Descartado: estado "observación" entre aprobado y rechazado (2026-08-20)

**Idea:** que Compliance pueda devolver un veredicto intermedio que le pida
cambios a Executor sin que cuente como intento gastado.

**Por qué se descartó:** rompe la protección contra loops infinitos que
`max_reintentos` da hoy — habría que inventarle su propio límite aparte, la
misma protección con otro nombre. Tampoco ataca la causa real de varios
rechazos vistos ese día: no eran "casi bien, con un ajuste menor", eran
Compliance sin contexto suficiente para verificar algo que en realidad
estaba correcto. Lo que sí apunta a esa causa es "Interfaz real reportada
por Executor" (arriba) y el contexto ampliado de Compliance.

## ¿Compliance debería usar un modelo con más razonamiento? (2026-08-20)

**Contexto:** varias veces terminó validándose manualmente lo que Compliance
rechazaba. Antes de cambiar de modelo, se diagnosticaron 4 validaciones
manuales del día: 3 eran ceguera de contexto (Compliance no podía ver
archivos fuera del item) y 1 era inconsistencia interna (marcó todos los
criterios cumplidos pero escribió veredicto rechazado). Ninguna era
claramente "el modelo razonó mal con la info correcta" — se resolvieron
ambas causas raíz sin cambiar de modelo: veredicto calculado
determinísticamente de `criterios_evaluados`, y contexto ampliado de
Compliance.

**Resultado tras el fix:** re-validando con el contexto ampliado, Compliance
verificó bien 3 de 4 rutas — pero cometió un error aritmético real en la
cuarta (dijo que un router duplicaba un prefijo, cuando en realidad no).
Evidencia a favor de reconsiderar un modelo con más razonamiento, pero un
caso, no un patrón todavía.

**Implementado:** Compliance pasó a `deepseek-reasoner` (antes
`deepseek-chat`), `max_tokens` subido de 16000 a 32000. Probado en vivo:
re-validando un item que con `deepseek-chat` había dado la contradicción
"criterios todos cumplidos pero rechazado", con `deepseek-reasoner` salió
consistente y correcto al primer intento. No se decidió solo por
costo/beneficio — el costo (más lento, más caro) es real; si en la práctica
no se nota mejora clara sobre `deepseek-chat` + las dos correcciones
deterministas, vale la pena volver.

## Conexión front-back real — evidencia fuerte a favor del Smoke test (2026-08-20)

Se armó la conexión local Modo A (`local-development.md`) y se corrió el
backend y el frontend reales por primera vez en todo el proyecto.

**Lo que se encontró al ejecutar de verdad, invisible para Compliance con
solo lectura estática y para `format_check.py` en su versión de antes de
esta sesión:**
- Un router montado con prefijo duplicado — la ruta real quedaba distinta a
  la esperada (bug presente desde antes, en items ya "completados").
- Dos items asumían una clase que nunca existió — `ImportError` real al
  arrancar.
- Una función de autenticación devuelve un dict plano, pero varios
  endpoints accedían a sus campos como si fuera un objeto — ninguno habría
  funcionado en producción.
- Varios repositories definían su propia excepción de dominio local (no la
  real) o llamaban al constructor real con aridad equivocada — el exception
  handler central nunca los habría atrapado.
- Dos códigos de error faltaban en el mapeo código→HTTP.

**Por qué importa para el Smoke test:** ninguno de estos bugs lo hubiera
atrapado una revisión de Compliance, ni con contexto ampliado — hacía falta
*ejecutar* el código. Los de lógica (acceso a campo equivocado, aridad de
constructor) solo se ven corriendo. Evidencia concreta de que el Smoke test
real es la pieza de mayor valor pendiente del harness en ese momento.

**Mejora permanente implementada:** `format_check.py` ahora verifica, para
cada `from app.x.y import NOMBRE`, que `NOMBRE` exista de verdad en el
módulo — no solo que el módulo resuelva. Atrapó el 90% de los bugs de import
de esta sesión antes de gastar Compliance.

## Auditoría de convenciones — el proceso de hoy violó su propio principio (2026-08-20)

**Contexto:** durante la sesión de conexión front-back, se editó código
generado directamente, por fuera de Executor/Compliance, para avanzar
rápido. Esto **no es aceptable en un escenario real** — rompe la premisa
completa del harness (Compliance como gate automatizado, no un humano
parcheando por atrás). Se aceptó como excepción de esa sesión, no como
práctica a repetir.

**Violaciones reales encontradas y corregidas contra `.agents/rules/`:**
1. Un `SECRET_KEY` real (aleatorio) en `.env` en vez de un placeholder
   explícito — violaba `security-baseline.md`.
2. Nombres de servicio/guard/interceptor en inglés para un dominio de
   negocio en español — violaba `naming-conventions.md`. Renombrado.
3. Cada componente frontend extraía y mostraba el error HTTP a su manera,
   violando "manejados centralizadamente vía un HttpInterceptor" de
   `error-handling.md`. Se agregó un interceptor de error central.
4. Un componente usaba `fetch()` crudo en vez de `HttpClient`, violando
   `frontend-angular.md` — y de paso se saltaba los interceptors.

**Lo que sí se respetó** (verificado, no asumido): CORS con origen
explícito, `commit()` solo en `service`, excepciones de dominio por entidad,
capas `router→service→repository→model`.

**Estado:** las violaciones quedaron corregidas y re-validadas con
Compliance real. El punto de fondo — que editar a mano es inaceptable en
real — queda como aprendizaje de proceso: el flujo correcto es Planner
actualiza `plan.json` → Executor regenera → Compliance valida.

## Modelo canónico grounded contra el schema real de la BD — debate y decisión (2026-08-22)

**Debate:** ¿debería el Planner tener acceso de antemano al schema real de la
BD del monolito origen para derivar los modelos, en vez de que cada función
decida su propio tipo por convención? Se evaluó también si el problema de
fondo era que Compliance carece de herramientas (TDD estricto, ciclo
rojo-verde iterativo dentro de Executor) — se descartó: el harness ya hace
test-first en espíritu (tests requeridos los escribe el Planner antes de que
exista el código, el smoke test corre pytest real antes de Compliance). Ir a
un ciclo iterativo de verdad exigiría rehacer el loop del orchestrator por un
beneficio marginal, y contradice la decisión ya tomada de que el troceo pasa
en planificación, no en Executor.

**Decisión:** generalizar el mismo principio que ya funcionó para
`RowSchema` en repositories, un escalón más abajo, al modelo base de cada
entidad. Antes de escribir el modelo canónico de una entidad persistida, el
Planner obtiene el script real de la BD del monolito origen (si existe) y
deriva los tipos de campo de ahí, propagándolos como contrato obligatorio
vía `interfaz`. Ver `plan.contract.md`, campo `schema_bd_origen`.

**Qué sí y qué no resuelve:** resuelve la clase de bug donde una función
declara un tipo que contradice el tipo real de la columna, porque ese tipo
queda grounded una sola vez y se hereda. No reemplaza el smoke test para
casos borde de formato — eso sigue necesitando un test real con ese valor.

## Imports no usados — implementado; código muerto a nivel proyecto — descartado con evidencia (2026-08-22)

**Implementado:** `format_check.py` suma un 4to chequeo (regla F401 de
`ruff`, corrida como subproceso — no un detector casero, para no reinventar
casos borde ya resueltos: re-exports, `__all__`, imports bajo
`TYPE_CHECKING`). `ruff` pasa a ser dependencia del propio Harness, no del
proyecto destino.

**Código muerto a nivel proyecto — probado con `vulture` y descartado con
evidencia concreta:** de 50+ hallazgos, prácticamente todos falsos positivos
— `vulture` no entiende wiring de framework (decorators de FastAPI,
reflection de Pydantic/SQLAlchemy). El único hallazgo genuino ya lo atrapaba
el chequeo F401 por-item — cero valor único aportado por un paso a nivel
proyecto en esta prueba. No se implementa salvo que aparezca evidencia nueva
de un tipo de código muerto que el chequeo por-item no cubra.

## `api_endpoints.py` no funciona con planes que agrupan endpoints por router (2026-08-22)

**Contexto:** el catálogo de API que se regenera tras cada aprobación de
Compliance quedaba vacío. Causa: lee `interfaz.endpoint` de cada item (diseño
original: un item = un endpoint), pero un plan real puede agrupar varios
endpoints bajo un solo item de router — patrón más práctico para routers
reales, pero el mecanismo original nunca tuvo el dato que esperaba.

**No se arregló — se reemplazó el enfoque.** Se construyó
`generate_api_docs.py`: en vez de leer una predicción de `plan.json`,
importa la app real (`app.openapi()`, sin servidor corriendo) y genera la
documentación desde ahí — cero desalineación posible con lo que Executor
generó de verdad, mismo principio que "Interfaz real reportada por
Executor". El mecanismo original queda no-funcional para esta granularidad,
sin justificar arreglarlo ahora que el nuevo cubre el caso mejor.

## Shell raíz del frontend nunca reemplazado — resuelto, lección volcada al contrato (2026-08-23)

**Contexto:** el frontend recién generado obligaba a scrollear para ver
cualquier pantalla real. Causa: el shell raíz (`app.component.html`) seguía
con el scaffold completo del `ng new` por encima de `<router-outlet />`.
Ningún item de un plan de ~17 items frontend tuvo nunca ese archivo en su
`archivos_destino` — Executor jamás lo tocó.

**La pregunta que importa, más que el fix en sí:** ¿por qué pasó? No es un
fallo de Executor ni de Compliance, es un gap del Planner al armar las
tandas de items frontend — se enumeraron interceptors, guards, config de
rutas y pantallas, pero nunca el shell raíz que las envuelve. Es el mismo
patrón ya conocido de "infraestructura que nadie pidió explícitamente"
(backend), pero con una variante importante: los gaps de backend SÍ se
detectaban solos porque otro item importaba algo de ese archivo y el import
roto disparaba un chequeo de inmediato. El shell raíz no tiene ningún
dependiente — nada lo importa, es un nodo terminal del grafo de
dependencias — así que ningún chequeo basado en imports podía forzarlo a la
superficie. Solo lo atrapó un humano usando el sitio.

**Resuelto vía el harness, no a mano:** se agregó un item nuevo a
`plan.json`, Executor lo generó y Compliance aprobó al primer intento.
Verificado además con un build real.

**Lección volcada al contrato:** nueva sección en `plan.contract.md` sobre
archivos raíz/entry-point invisibles a build/format-check/smoke-test — regla
para el Planner: declarar un item explícito de limpieza del shell raíz apenas
se bootstrapea cualquier capa con el scaffold de un framework, con criterios
de aceptación que verifiquen negativamente contra el contenido del scaffold,
no solo que compile.

## Corrida completa desde cero en sandbox limpio, agente Documentador validado (2026-08-24)

Primera vez que se corrió un proyecto de punta a punta desde una carpeta
vacía (sandbox separado del proyecto real), para probar el agente
Documentador nuevo (`agents/documentador.py`) y las reglas de brevedad de
salida sin el sesgo de un filesystem ya poblado por sesiones anteriores.

**Resultado: el plan completo quedó `completado`.** Arrancó con solo unos
pocos items pasando en frío; el resto fueron rondas de diagnóstico + fix +
reintento.

**7 gaps reales de `plan.json` encontrados, mismo patrón en todos:** un
archivo con importadores reales, ausente de `archivos_destino` de todo el
plan porque una revisión posterior lo acotó y ninguna otra lo adoptó. Nunca
se habían detectado antes porque toda prueba previa corrió contra el
proyecto real, donde esos archivos ya existían en disco desde revisiones
viejas. Nueva sección en `plan.contract.md`: "Revisiones sucesivas de un
item: `archivos_destino` acumula, no se resetea".

**Compliance en `deepseek-chat` (cambio manual para esta prueba) —
autocontradicciones/rechazos falsos en ~30% de los items.** Mismo patrón
exacto que motivó el cambio a `deepseek-reasoner` — confirma la causa con
evidencia nueva, no solo la sospecha original. Se volvió a
`deepseek-reasoner`.

**Agente Documentador: validado en producción, no solo en tests.** Se
disparó 14 veces a lo largo de la corrida (cada vez que un item con rechazo
real llegó a `completado`), clasificando la mayoría como decisión de
arquitectura, sin falsos positivos de clasificación. Revisando los
candidatos: la mayoría eran el mismo gap repetido sin valor nuevo (el
documentador no tiene visión entre items para deduplicar — juicio del
Planner), algunos eran patrones reusables reales (volcados a `knowledge/`),
uno correctamente marcado sin candidato, y algunos eran la misma lección de
proceso del harness que terminó en la sección nueva de `plan.contract.md`.

**Bug propio al corregir uno de los gaps a mano:** se confundieron dos
columnas de nombre parecido pero tipo y tabla distintos, escribiendo una
regla al revés que generalizaba de más. Revertido al notar la contradicción
con el `detalle_tecnico` original (que ya tenía el tipo correcto). Quedó
como ejemplo en `knowledge/sqlalchemy-2.0.md`: tipar columna por columna,
nunca por similitud de nombre — la lección aplica igual a un humano editando
el plan que al modelo generando código.

## `plan_lint.py` — chequeo heurístico de dependencias/imports en prosa (2026-08-26)

Arrancando una migración con muchas integraciones externas de solo lectura,
surgieron dos clases de bug reales en `plan.json` que el validador
estructural no puede atrapar porque viven en la *prosa*, no en la
estructura: un item cita el ID de otro en `detalle_tecnico` sin declararlo
en `depende_de`, y un import `app.*` mencionado en prosa que ningún item
genera o cuyo dueño no está declarado como dependencia.

**Implementado:** `checks/plan_lint.py` (regex sobre
`detalle_tecnico`/`criterios_aceptacion`/`interfaz`, sin LLM) detecta ambos
patrones. Es **heurístico, no un gate automático** — puede marcar
referencias hacia adelante en la prosa como falso positivo. El Planner lo
corre a mano antes de dar un plan por terminado.

**Encontró un bug real al primer uso:** una dependencia transitiva no
declarada (mismo patrón ya conocido). De 9 avisos totales, 8 falsos
positivos esperados y 1 bug real — tasa de falso positivo alta pero esperada
para una herramienta basada en regex, consistente con por qué se diseñó como
advisory.

## Backend/DAL como deployables separados — harness generalizado a N carpetas backend (2026-08-26)

Para permitir que "backend" y "dal" (o cualquier topología con más de una
carpeta `tipo: "backend"`) corran como deployables de verdad (carpetas/venvs
propios, comunicación por red) — variante ya prevista en `Pendientes.md`
pero sin caso real hasta este día:

- `checks/smoke_test.py` y `checks/generate_api_docs.py` ya no asumen una
  carpeta `backend/` fija — derivan la carpeta (y su venv) del propio item o,
  para el catálogo de API a nivel de proyecto entero, de todas las carpetas
  `tipo: "backend"` distintas en `plan.json`.
- `.agents/rules/backend-architecture.md` — nueva sección "Backend y DAL
  como deployables separados" (capas por deployable) y nueva regla
  "Lecturas con loop/N+1": una operación atómica se resuelve dentro del DAL,
  nunca orquestando varias llamadas de red desde `backend/client/` —
  motivada por rutas reales del monolito origen que hacían queries en loop.
- `schemas/plan.contract.md` documenta que agregar `"dal": "FastAPI"`
  explícito es la señal para que el Planner declare `depende_de`/`interfaz`
  de backend→dal por red.

**Bug real del harness — `interfaz_real.py::_como_lista` descartaba en
silencio la interfaz predicha cuando `dependencia_reusable` se escribe como
dict-por-nombre** (forma más legible que la lista de siempre, usada por
primera vez en este plan). Solo sabía normalizar el dict plano singular o
una lista — el dict-por-nombre caía en la rama singular y se descartaba
entero, dejando a un dependiente `bloqueado` pidiendo información que el
plan ya daba. Corregido generalizando a las 3 formas + tests de regresión.

**Nota operativa:** un reintento forzado puede tardar más de 2 minutos —
conviene correr `orchestrator.py` en background en vez de bloquear con
timeout corto; un corte de proceso a mitad de generación deja un evento
huérfano, se destraba forzando el item de nuevo.

Resultado de esta migración concreta: backend+DAL y frontend completos —
detalle específico de negocio en el `handoff.md` propio del proyecto.

## Escalada a `executor_senior` confirma el heurístico "3 veces mismo error = escalar a mano" (2026-08-26)

Migrando un frontend, el motor local falló 3 veces seguidas agregando una
transformación de casing ya pedida de forma explícita y literal en
`detalle_tecnico` — escalado a mano a `executor_senior`, resuelto al primer
intento. Tercera vez que este patrón exacto se repite en el mismo proyecto —
confirma que "mismo error 3 veces seguidas con feedback ya preciso" es la
señal correcta para escalar a mano, no seguir reintentando con el executor
normal.

Confirmado también: un item con un archivo roto bloquea en cascada a todo lo
que depende de un build completo, pero cada item bloqueado se comportó bien
— confirmó que el archivo roto no era su culpa y quedó `### BLOQUEADO` en
vez de tocarlo o inventar contenido.

## `arbitro` — cuarta salida `INTERFAZ_INCOMPLETA` (2026-08-26)

Revisando la métrica de reintentos de una migración completa
(backend+DAL+frontend): de 11 consultas a `arbitro`, 5 fueron
`no_resoluble` por falla transitoria del motor y **4 de las 6 restantes
fueron `falta_dependencia` que resolvían "no hay nada que agregar"** — el
item que definía el símbolo faltante YA estaba en `depende_de`, el problema
real era que su `interfaz` no exponía ese símbolo. `arbitro` reconocía esto
pero no tenía forma de comunicarlo — solo podía elegir entre "agregar una
arista" o "rendirse", cayendo en un `no_resoluble` sin explicación.

Se agregó una cuarta salida, `INTERFAZ_INCOMPLETA`
(`{item_productor, simbolo_faltante, explicacion}`) — `arbitro` sigue sin
escribir contenido de ningún item, pero ahora puede señalar con precisión
"este item ya es tu dependencia, pero le falta exponer X". Nunca reintenta
solo con esto (a diferencia de `falta_dependencia` resuelto), porque nada
cambió en el plan; sigue necesitando que el Planner actualice la `interfaz`.

## Documentador — marca de "supersedido" para candidatos del mismo item (2026-08-26)

Revisando a mano ~39 candidatos que el Documentador propuso durante una
migración con DAL separado: varios candidatos sobre la misma excepción de
dominio describían una firma que el código final real ya no usa — cada
candidato reflejó la verdad exacta en el momento en que se escribió, pero el
item se regeneró varias veces por invalidaciones en cascada de otros fixes,
y cada aprobación disparaba un candidato nuevo sin que nada marcara los
anteriores como obsoletos. Casi la mitad de los candidatos de esa sesión
quedaban obsoletos por una regeneración posterior no relacionada.

Se agregó `_marcar_candidatos_previos_superados()` en
`agents/documentador.py`: busca bloques previos del MISMO item_id y les
antepone una marca `**⚠ SUPERSEDIDO**` — nunca borra ni reescribe el
contenido viejo, solo lo señala. Idempotente.

**Bug real encontrado escribiendo el primer test:** la implementación
asumía un separador de bloques de dos saltos de línea, pero el método real
de escritura normaliza el contenido antes de escribirlo, dejando un solo
salto de línea en disco. Con el separador viejo, el split nunca encontraba
nada — un fallo silencioso que solo un test end-to-end (escribir candidatos
reales con un motor falso y verificar el archivo resultante) atrapó de
inmediato; un test que solo mockeara la función sin pasar por el archivo
real no lo hubiera visto.

## `combinar_interfaz()` no resolvía conflictos por nombre distinto para el mismo rol (2026-08-26)

Causa raíz de varios rechazos y un escalado: un router *falso* predicho por
el Planner (que Executor nunca implementó bajo ese nombre) sobrevivía
indefinidamente al lado del real, porque `combinar_interfaz()` solo resuelve
conflictos por `import` exacto — dos entradas de distinto nombre para el
mismo rol coexisten para siempre.

**Corregido:** nueva `podar_predicha_no_generada()` en `interfaz_real.py` —
descarta de la predicha cualquier símbolo que no exista en el código
generado real (no reabre el caso opuesto: un símbolo real que la interfaz
real no repite sigue sobreviviendo). Con el plan cerrado se probó
`docker-compose` por primera vez: los servicios levantaron y un login real
viajó de punta a punta a través de todas las capas.

## 5 bugs reales con datos mockup — nuevas entradas en `knowledge/` (2026-08-26)

Datos de prueba + `docker-compose` real destaparon bugs que ningún
`pytest`/build atrapa. Dos de patrón de librería reusable, volcados a
`knowledge/sqlalchemy-2.0.md` (`.with_only_columns()` +
`.scalar_one_or_none()` devuelve solo la 1a columna; división de columnas es
real, no entera, usar `//`) y `knowledge/httpx.md` (`httpx.Client` sin
`follow_redirects=True` rompe cualquier ruta llamada sin barra final). El
resto eran bugs de negocio propios de ese proyecto.

## Regla nueva: español neutro en todo texto de UI (2026-08-26)

Verificando un frontend en navegador real, apareció una variante regional de
español distinta a la del cliente. Se agregó una regla nueva a
`.agents/rules/frontend-angular.md`: español neutro en todo texto de UI, sin
asumir la variante regional de quien escriba el código.

De paso, un bug real de Angular (`NG02100`: `LOCALE_ID` sin
`registerLocaleData()`) quedó documentado en `knowledge/angular.md`.

## Tercer motor: Kimi (Moonshot AI) como alternativa a LM Studio (2026-08-27)

Se agregó la API de Kimi como motor de inferencia alternativo, para poder
seguir trabajando (`executor`/`documentador`) sin acceso al motor local —
mismo rol que DeepSeek ya cumple para Compliance/`executor_senior`/`arbitro`,
pensado como reemplazo puntual de `lm_studio`, no de DeepSeek.

**Implementado**, mismo patrón que el adapter de DeepSeek (cero cambios a
`orchestrator.py`/agentes — `ModelEngine` es la única interfaz que les
importa): `engines/kimi_api.py` (REST compatible con OpenAI), registrado en
`engines/factory.py`, bloque `engines.kimi` en `config/models.yaml`.
Verificado: import limpio, resolución de motor correcta, suite completa sin
regresión.

### Fallback automático a Kimi cuando el motor local está inalcanzable (mismo día)

Pedido: que el propio orquestador ofrezca cambiar a Kimi solo, en vez de
editar `config/models.yaml` a mano cada vez que LM Studio no está disponible
— con la condición de que primero pregunte (no cambiar de motor en
silencio).

**Implementado:**
- `engines/base.py` — nueva excepción `MotorInalcanzable`, distinta de
  `TimeoutDelMotor`: "no se pudo conectar en absoluto" vs. "conectó pero no
  respondió a tiempo". `lm_studio.py` ahora atrapa
  `requests.exceptions.ConnectionError` (antes solo `Timeout`) — antes de
  este fix, un motor local apagado tiraba un `ConnectionError` crudo sin
  capturar, reventando el proceso entero sin mensaje útil.
- `engines/factory.py` — overrides de motor por agente EN MEMORIA
  únicamente, nunca tocan `config/models.yaml` en disco.
- `orchestrator.py::_con_fallback_motor_local` — envuelve cualquier llamada
  que dependa del motor de un agente. Si sale `MotorInalcanzable`, pregunta
  si activar Kimi para el resto de la corrida; si acepta, reintenta una vez.
  Nunca vuelve a preguntar por el mismo agente en la misma corrida. Con
  `--sin-confirmar` no pregunta — corta con estado explícito en vez de
  adivinar. Cada decisión queda registrada en un log aparte.

**Probado (sin red real):** conversión de excepción, override de motor sin
tocar el yaml, propagación sin capturar, y las 4 ramas del wrapper de
fallback. Sin probar todavía en esta etapa: el flujo completo en vivo con el
motor local realmente apagado.

### Modelo Kimi distinto por agente, y 2 bugs reales encontrados en revisión manual (mismo día)

Afinado el modelo por agente (`executor` con una variante, `documentador`
con otra más liviana — su trabajo es clasificar/resumir texto, no generar
código). El mapeo de modelo pasó de un string único a un dict por agente;
corta directo (sin preguntar) si el agente que falló no tiene entrada
mapeada.

**Revisión manual del código nuevo (sin repo git en ese momento, sin
`/code-review`) encontró 2 bugs reales, ninguno cosmético — ambos hacían que
el fallback fallara silenciosamente justo en el escenario que lo motivó:**

1. **Orden de excepciones equivocado en `lm_studio.py`.**
   `requests.exceptions.ConnectTimeout` hereda de **ambas** `Timeout` y
   `ConnectionError` (confirmado con `issubclass()`). Con el `except
   Timeout` antes que el de `ConnectionError`, un host inalcanzable que
   tarda en fallar se clasificaba como `TimeoutDelMotor` en vez de
   `MotorInalcanzable` — el fallback nunca se disparaba en ese caso.
   Corregido invirtiendo el orden.
2. **El reintento tras activar Kimi no estaba protegido.** Si Kimi TAMBIÉN
   fallaba en el mismo intento, la excepción se propagaba cruda, rompiendo
   el contrato de "esta función siempre devuelve un dict" — reventaba el
   proceso con un traceback en vez del mensaje claro esperado. Corregido
   envolviendo ese segundo intento en su propio try/except.

Ninguno de los dos bugs lo hubiera atrapado la suite de tests existente —
ambos son casos borde de la jerarquía de excepciones de `requests` y de un
segundo fallo dentro del mismo camino de control.

### Primera llamada real contra la API de Kimi — bug de `temperature` (mismo día)

Con la key real cargada, se confirmó que la conexión funciona y los nombres
de modelo son reales. **Bug real en la primera llamada:** el adapter de Kimi
mandaba `temperature: 0.2` fijo (copiado del estilo del adapter de
DeepSeek). Los modelos de Kimi lo rechazaron con 400: solo aceptan
`temperature: 1`. Corregido, confirmado con una llamada cruda y con el
camino de producción completo — ambos devuelven 200 con contenido.

**Estado: fallback a Kimi validado en vivo, extremo a extremo.** Sigue
pendiente la prueba dentro de una corrida real de `--loop` con el motor
local efectivamente apagado.

## Fallback a Kimi validado en un `--loop` real de punta a punta — 3 bugs más del harness (2026-08-27)

Con el motor local inalcanzable durante una sesión completa de
replanificación + ejecución real, se autorizó activar Kimi — primera vez que
el mecanismo completo (detección + gate + override + `--loop
--sin-confirmar`) se ejercita en producción, no en test. 3 bugs reales
encontrados y corregidos:

**1. `agents/executor.py::construir_contexto` reventaba con
`PermissionError` al consultar `arbitro` para un item bloqueado cuya
dependencia ya estaba completada — el caso normal, no el borde.** `arbitro`
tiene `project_dir: none` a propósito, pero la poda opcional de interfaz
(agregada en una sesión posterior a cuando se fijaron esos permisos) intenta
leer el código real de la dependencia vía el mismo guard, sin chequear
permiso. Como cualquier item bloqueado casi siempre tiene dependencias ya
completadas, esto crasheaba el `--loop` completo cada vez que arbitro se
consultaba. Corregido envolviendo esa poda en `try/except PermissionError`
(usa la interfaz predicha sin podar en vez de crashear).

**2. Los adapters de Kimi y DeepSeek no envolvían
`requests.exceptions.Timeout`/`ConnectionError` en absoluto** (a diferencia
del de LM Studio) — un timeout real de la API (un item grande que tardó más
de lo configurado) se propagaba crudo y reventaba el `--loop` entero, el
mismo bug de clase ya corregido para LM Studio pero nunca portado a los
motores por API. Corregido en los dos con el mismo patrón.

**3. `checks/generate_api_docs.py::_construir_curl` asumía
`request_body["content"]["application/json"]` siempre presente** —
crasheaba con `KeyError` en el primer endpoint `multipart/form-data`
(subida de archivo) visto por el harness. Corregido: detecta el content-type
real (JSON → `-d`, multipart → un `-F` por campo, marcando campos binarios
como adjunto de archivo).

**Comportamiento correcto observado en vivo, no un bug:** `documentador`
intentó usar el motor local (sin override activo) y, con `--sin-confirmar`
activo, no había a quién preguntarle — se saltó ese candidato limpio en vez
de romper nada. Exactamente el diseño previsto: falla gracefully, es
puramente aditivo.

**Resultado de la corrida real:** el plan completo (backend+DAL separados)
quedó `completado`, con varios rechazos reales encontrados y corregidos por
el ciclo normal — el mecanismo de fallback no volvió a fallar después de
estos 3 fixes.

## Regla "ensambladores van al final" generalizada también al frontend (2026-08-27)

Arrancando un frontend nuevo: mismo patrón ya conocido del backend — un item
"ensamblador" (rutas de la app, planificado temprano dependiendo solo de
servicios base) generó una ruta de import plausible pero inexistente porque
sus dependencias reales (varios componentes de página) todavía no tenían
`interfaz` declarada. El chequeo de frontend corre un build completo para
cualquier item, así que el archivo roto bloqueó en cascada al resto — cada
item detectó bien "no es mi culpa", pero costó una llamada cada vez.

Generalizado a `schemas/plan.contract.md` (sección "Items 'ensambladores'
van al FINAL...") y espejado en `.agents/rules/frontend-angular.md` —
primera vez documentado explícito también para el lado frontend.

## Regla de "ensambladores" necesaria pero no suficiente — Executor no siempre inspecciona la interfaz de sus dependencias (2026-08-27)

Con `depende_de`+`interfaz.dependencia_reusable` ya declarados
correctamente, Executor igual volvió a adivinar una ruta de import en vez de
leerla de la interfaz disponible, dos veces más en la misma migración. Un
reintento forzado con el motivo del rechazo como feedback resolvió el 100%
de los casos vistos hasta ese punto — la información estaba disponible,
Executor la usó bien cuando el feedback se la señaló explícitamente, pero no
fue a buscarla sola en la generación inicial.

**Sigue pendiente:** reforzar el prompt de Executor para que, en items
ensambladores (`interfaz: {}`), liste explícitamente los imports de sus
`depende_de` como parte del contexto obligatorio.

**Patrón operativo confirmado (también en `docker_check`, más abajo):** un
reintento forzado que genera 0 archivos no es señal de "nada que arreglar" —
puede significar que el modelo no entendió el feedback ese intento; forzar
de nuevo antes de escalar a mano.

## Nuevo tipo de item `"infra"` + `checks/docker_check.py` (2026-08-27)

Se pidió que Dockerfile/docker-compose/orígenes de datos entraran al ciclo
Executor/Compliance (hasta ahora se hacían a mano) — motivado por un caso
real no trivial: un DAL necesitaba el driver ODBC de AS400 (IBM) instalado
vía `apt`, no `pip`. Compliance es un LLM que solo lee texto, sin ejecutar nada
— no puede detectar que un paquete de sistema haya quedado mal registrado
hasta que alguien construye la imagen de verdad.

**Pieza nueva:** `checks/docker_check.py`, mismo patrón que
`frontend_check.py`/`smoke_test.py` (determinístico, gratis, corre antes de
Compliance). Para items `tipo: "infra"`: `docker build` de cada Dockerfile
en `archivos_destino`; campo opcional `verificacion_runtime` (comandos que
corren DENTRO de la imagen recién construida y confirman su salida real —
así se detectó que el driver de AS400 registra su nombre real distinto al
que la documentación heredada indicaba). Para `docker-compose.yml`: `docker
compose config -q` + `build`, y campo opcional `smoke_http` que levanta los
servicios y hace polling a 200 (o corre el chequeo dentro del contenedor si
el servicio a propósito no expone puerto, ej. un DAL interno). `docker` no
disponible ⇒ mismo mecanismo que un motor de IA caído, no un rechazo de
código.

**Bug real ya conocido, recurrió en la primera pasada:** Compliance rechazó
un item de infra porque no sabía que el `docker build` ya había pasado —
mismo bug ya documentado para frontend. Corregido agregando la rama
`tipo == "infra"` al mismo chequeo previo.

**Bugs reales en el módulo nuevo, encontrados contra builds/containers
reales:**
1. `docker compose port <servicio> <puerto>` NO falla ni devuelve stdout
   vacío cuando el servicio no tiene ese puerto publicado — devuelve
   literalmente `":0"` con exit code 0. Se interpretaba como puerto real
   `0`. Fix: tratar puerto `0` igual que "no encontrado".
2. Ningún soporte inicial para healthcheck de servicios sin `ports` — el
   primer intento de Executor "arregló" el rechazo publicando un puerto que
   una decisión explícita de arquitectura prohibía exponer. Se agregó el
   chequeo interno vía `docker compose exec`.

**Matiz nuevo al patrón operativo de arriba:** si un reintento forzado sin
resultado tampoco alcanza una segunda vez, correr `--rol compliance` primero
para generar un rechazo FRESCO (reflejando el estado real del código/plan)
antes de forzar de nuevo — un reintento forzado sin una validación fresca de
por medio reusa el feedback del ÚLTIMO veredicto guardado, que puede estar
desactualizado.

## Convención nueva: modo mock (`MOCKUP=true`) para sistemas externos sin instancia de dev (2026-08-28)

Motivado por confirmar que un frontend se podía *servir* pero no *usar* de
verdad — login necesita un directorio LDAP real, otras pantallas necesitan
sistemas externos de solo lectura, ninguno accesible desde el
entorno de desarrollo. Se pidió que esto sea un cambio de flujo general, no
un parche puntual.

**Patrón:** en cada función pública de un `repository`/`service` que toca un
sistema externo sin instancia de dev accesible, un guard `if
get_settings().mockup:` al inicio (antes de conectar) devolviendo datos
falsos con la misma forma Pydantic real — con eco de los identificadores de
entrada cuando la función los recibe. Login/auth en modo mock usa usuarios
de prueba fijos, no cualquier credencial no vacía. `Settings.mockup: bool =
False` (env var `MOCKUP`) por deployable. Un sistema con instancia real
accesible en cualquier entorno (ej. la propia BD del proyecto,
containerizada) NUNCA se mockea.

Documentado en `.agents/rules/local-development.md` (sección "Modo mock") y
`schemas/plan.contract.md` (junto a `riesgos_heredados`), para que el
Planner lo declare desde el arranque de cualquier item nuevo que toque un
sistema externo así.

## Retrofit del modo mock — 2 bugs propios del harness encontrados (2026-08-28)

**1. Editar `detalle_tecnico`/`criterios_aceptacion` de items ya
`completado` (vía script directo sobre `plan.json`, no vía Planner) perdió
el campo `estado` de TODOS los demás items del plan**, no solo los tocados —
decenas de items pasaron a `pendiente` de golpe. La causa exacta no se
investigó a fondo, pero el síntoma es reproducible: una edición que solo
debería tocar unos pocos items terminó afectando el campo `estado` de todos.
Mitigado a mano esa vez (verificando `archivos_destino` en disco antes de
restaurar `estado: completado`). Pendiente: blindar el código que escribe
`plan.json` para que una edición de items existentes preserve el `estado`
de los que no toca.

**2. Un 400 de LM Studio con mensaje de carga de modelo fallida (servidor
arriba, pero el modelo crasheó cargando) no dispara `MotorInalcanzable`** —
`raise_for_status()` deja propagar un `HTTPError` crudo, reventando
`orchestrator.py` con traceback completo en vez de ofrecer el fallback.
Resuelto reintentando a mano esa vez; el bug de manejo de errores quedó
pendiente (resuelto más adelante, ver "2 bugs reales más", 2026-08-30).

## `--item X --rol executor` no expone `executor_senior` (2026-08-28)

Se pidió explícitamente escalar a `executor_senior` para un item puntual que
llevaba varios reintentos locales sin converger. `executor_senior` solo se
activaba automáticamente dentro de `loop()` tras agotar reintentos — no
había forma de pedirlo para UN item vía CLI. Se resolvió invocando la
función interna directo desde un script de una línea, fuera del CLI.

**Resuelto (2026-08-30):** agregado `--senior` a `orchestrator.py` — con
`--item <id>` (rol executor, el default), fuerza `executor_senior` directo,
saltando el arbitraje a propósito (arbitro no aplica al executor senior).
Sigue usando el ticket de reintento como feedback si el item ya estaba
rechazado. Valida antes de correr: requiere `--item` + rol executor, y que
`executor_senior` esté configurado.

De paso, `executor_senior` se quedó sin `max_tokens` pensando 2 veces
seguidas en un item grande/con muchas dependencias hasta que se le pasó el
CÓDIGO FUENTE REAL de las dependencias como feedback (no solo el resumen de
`interfaz`) — con eso convergió al primer intento. Sugiere que armar el
contexto podría beneficiarse de incluir el source completo de dependencias
chicas, al menos cuando el modelo se bloquea pidiendo explícitamente la
firma real.

## Bug recurrente: notación `archivo.simbolo` en `interfaz.dependencia_reusable.import` se toma como ruta literal (2026-08-28)

Una convención usada en varios items nuevos de frontend (`"import":
"@app/shared/utils/algo.NombreFuncion"`, pensada como notación descriptiva
"modulo.simbolo", no como import statement literal) hizo que Executor la
tomara tal cual como ruta de módulo al menos 3 veces distintas, rompiendo el
build (`Cannot find module`). Corregido a mano cada vez. Pendiente: cambiar
la convención de ese campo en `schemas/plan.contract.md` a algo inambiguo
(separar `modulo` y `simbolo` en dos campos, o escribir el import statement
completo literal).

## Ticket de reintento — feedback persistido a archivo, reemplaza el feedback recalculado en memoria (2026-08-30)

Foco explícito en mejorar el flujo del harness mismo, atacando el patrón de
oscilación ya documentado varias veces: un reintento corrige lo que
Compliance marcó y rompe algo que ya andaba bien, porque regenera a ciegas
sin ver el historial completo ni los criterios que SÍ cumplía. Idea: en vez
de seguir mejorando el feedback en memoria, formalizarlo como un ticket en
disco — mismo espíritu que `arbitro`, pero para el circuito de
rechazo/reintento en vez de bloqueos.

**Implementado** (diseño completo en `schemas/plan.contract.md`, sección
"Ticket de reintento"): cada rechazo elegible para reintento actualiza
`.harness/logs/tickets/<item_id>.md` — un archivo por item que acumula
"Historial de intentos" (append, con la fuente exacta de cada rechazo),
sobreescribe "Código actual" completo, y expone `criterios_aceptacion`
completos en "Lo esperado" (excepción deliberada al contexto mínimo de
Executor: en un reintento necesita ver TODOS los criterios, no solo el que
falló). "Hechos verificados" es la única sección que el harness nunca toca —
población manual (Analyzer entiende el problema, Planner arma el plan de
trabajo; hoy Felipe+Claude, decisión explícita de no automatizar esto
todavía).

El ticket reemplaza el feedback recalculado en memoria como única fuente de
`feedback` para Executor normal, `executor_senior`, y el reintento forzado —
los tres leen el mismo archivo. Cierra de paso un bug latente real: el
historial en memoria dentro de `loop()` se perdía si el proceso se cortaba a
mitad de corrida — ahora el historial completo sobrevive en disco.

El gate de decisión cambia de `[s]/[t]/[m]/[n]` a `[r]/[e]/[m]/[n]`: el
ticket ya existe siempre antes de preguntar, así que "escribir ticket" se
reemplaza por "completar 'Hechos verificados' a mano antes de reintentar".

**Efecto secundario real, corregido en la misma sesión:** el reporte de
fallas dejó de tener el detalle completo de un rechazo, del que dependía el
Documentador para el caso más interesante (un item que osciló pero terminó
aprobado). Corregido: el Documentador ahora también lee el ticket completo
del item, que tiene estrictamente más detalle que antes.

**Validado en vivo con una oscilación real generada a propósito** (fixture
sintético en scratchpad, no forma parte del repo): un rechazo real (2/5
criterios) generó un ticket con los 5 criterios completos (primera vez que
Executor los ve) → el reintento corrigió ambos defectos en un solo intento.
Forzando el mismo bug de nuevo y completando "Hechos verificados" con la
regla exacta: el reintento aplicó la regla correctamente pero introdujo un
typo nuevo que osciló 2 rechazos más antes de escalar a `executor_senior`,
que lo resolvió con el ticket completo (historial + hechos verificados
intactos las 4 actualizaciones). Confirmado también que el Documentador usó
el ticket para un resumen preciso del antes/después, y que la marca de
"supersedido" se disparó en vivo, no solo en test.

## Limpieza de `handoff.md` — separar bitácora del harness de bitácora de cada migración (2026-08-30)

Esta bitácora venía acumulando narrativa específica de cada migración (bugs
de negocio, decisiones de UI, resultados de ejecución) que ya debería vivir
en el handoff propio de cada proyecto — regla que ya existía desde el
2026-08-24 pero dejó de aplicarse de forma consistente después de esa fecha.

**Limpieza retroactiva** (con backup antes de tocar nada, dado que este repo
no tenía git en ese momento): las secciones de esa fecha en adelante
quedaron reducidas al núcleo de mecanismo del harness (bug/decisión en
`orchestrator.py`/`agents/*`/`checks/*`/`engines/*`, o regla nueva en
`.agents/rules/`), con puntero al handoff del proyecto para el resto.

**Reforzado a futuro:** `init_harness.py` ahora crea `.harness/handoff.md`
vacío (con la regla escrita arriba) en cada proyecto nuevo — idempotente,
nunca pisa uno que ya exista.

*(Nota de 2026-09-04: esta misma bitácora, `Harness/handoff.md`, se reescribió
por completo para sacarle cualquier nombre de proyecto/cliente real — el
criterio de la sección de arriba se aplicó en retrospectiva a todo el
historial, no solo hacia adelante.)*

## `estado_proyectos.py` — registro y estado de todos los proyectos, sin narrativa (2026-08-30)

A continuación de la limpieza de arriba: se quería un registro de qué
proyectos están en marcha y en qué estado, sin la DATA de las migraciones —
algo que la bitácora narrativa no resuelve bien.

**Implementado:** `config/proyectos.yaml` (registro liviano, mantenimiento
manual: nombre + ruta + descripción corta) + `estado_proyectos.py`, que para
cada proyecto registrado calcula su estado real vía
`orchestrator.calcular_estados()` — mismo mecanismo que ya usa
`orchestrator.py <proyecto> --status`, sin duplicar el algoritmo de "estado
efectivo". Nunca guarda un snapshot: cada corrida relee el `.harness/` real
de cada proyecto. Un proyecto con ruta rota o sin `plan.json` todavía se
reporta igual, no rompe el resumen de los demás.

Probado contra los proyectos reales registrados en ese momento, con
distintos grados de avance (uno recién arrancado, sin narrativa propia
todavía) — el resumen los reflejó correctamente a todos.

## Dividir items grandes en sub-entregables — pendiente cerrado (2026-08-19 a 2026-08-30)

**Contexto original:** un prompt que le pide al modelo generar demasiado de
una sola vez da una respuesta menos confiable, visto en la práctica con el
modelo local quedándose sin `max_tokens` pensando antes de terminar de
escribir.

**Decisión tomada:** la división pasa en tiempo de planificación (el Planner
parte cada ticket en items chicos vía `depende_de`/`interfaz`), no es un
mecanismo nuevo dentro de Executor. **Umbral:** a criterio del Planner caso
por caso, sin número fijo. El campo `ticket_id` (para trazar qué items
vienen del mismo requerimiento) está en el contrato desde entonces. La otra
mitad de este pendiente (un agente automatizado "tomador de requerimientos")
sigue pausada por la preferencia explícita de seguir en el loop para
decisiones de Analyzer/Planner.

**`plan_lint.py` — 3 chequeos nuevos para detectar candidatos a dividir, con
bug real de calibración corregido:**
1. Tamaño relativo a la mediana del propio plan, umbral 2.5x, se salta en
   planes de menos de 5 items.
2. Contrato de retorno sin claves exactas — cruza `criterios_aceptacion`
   ("exactamente las claves X/Y") contra `detalle_tecnico`.
3. Ambigüedad en prosa — elipsis en código citado y frases tipo "según
   corresponda".

**Validado contra un bug real en vivo:** corrido contra un plan real de la
sesión, el chequeo (2) detectó exactamente el bug que causó un rechazo real
visto en vivo (el `detalle_tecnico` nunca mencionaba claves que
`criterios_aceptacion` exigía literal).

**Bug real de calibración, corriendo contra los proyectos reales
registrados** (todos ya completados, corrida retrospectiva): uno de los
planes dio muchos avisos de tamaño, la mayoría falsos positivos. Causa: el
plan mezcla muchos items triviales de 1 archivo con un grupo normal de 3-4
archivos — la mediana quedó pegada en 1, así que el umbral relativo (2.5x)
marcaba como "candidato a dividir" cualquier item de 3+ archivos, un tamaño
perfectamente normal en términos absolutos. **Corregido agregando un piso
absoluto** (4 archivos / 4 criterios, además del umbral relativo) — bajó los
avisos falsos drásticamente, dejando solo los items genuinamente más
grandes de cada plan real.

## Regla nueva: documentos de proyecto que crecen durante la migración nunca son un item de `plan.json` (2026-08-30)

Aplicando `plan_lint.py` contra un plan real de más de 100 items se
dividieron varios candidatos reales y se revisaron uno por uno los avisos de
dependencia restantes — todos resultaron falsos positivos confirmados
(analogías de patrón, notas de consumidor futuro, prohibiciones explícitas
de acoplar dominios), 0 bugs reales.

**Bug real de modelado, no del harness — de cómo se usó el contrato:** dos
items estaban armados como tickets normales (Executor los iba a generar una
sola vez con contenido ya decidido de antemano), pero la intención real era
que fueran documentos que se completan a medida que aparecen hallazgos
durante la migración (un fix aplicado, un riesgo detectado), no un
entregable cerrado. El mismo patrón ya existía sin formalizar (la
convención `deuda_negocio.md`, ver primeras pruebas reales) — nunca se
escribió al contrato, y por eso se reinventó mal acá.

**Corregido:** los 2 items se sacaron de `plan.json` y los archivos se
crearon directo (fuera del ciclo Executor/Compliance) con el contenido ya
conocido como semilla. Regla nueva en `schemas/plan.contract.md`: la
pregunta que decide si algo es un item o no es "¿este archivo va a seguir
recibiendo entradas nuevas después de que el plan esté escrito, a medida que
se ejecutan otros items?" — si la respuesta es sí, no es un item.

## `--senior` validado en vivo — 2 bugs reales más encontrados y corregidos (2026-08-30)

Probando `--senior` contra un rechazo real (mismo sandbox sintético de
"Ticket de reintento", ningún proyecto real tocado) salieron 2 bugs reales:

**1. Los tres adapters de motor no capturaban `HTTPError`.**
`raise_for_status()` dejaba propagar un `HTTPError` crudo, reventando el
proceso con traceback en vez de tratarse como un intento fallido normal —
mismo bug de clase que el pendiente ya anotado el 2026-08-28 para LM Studio,
nunca portado a los otros dos motores. Corregido en los 3:
`raise_for_status()` envuelto, `HTTPError` → `RuntimeError` con el status
code + cuerpo real de la respuesta.

**2. Configuración cruzada real en `config/models.yaml`:** `executor_senior`
tenía un nombre de modelo de un proveedor apuntando a otro proveedor —
rechazaba cada llamada con 400 nombrando los modelos válidos reales. Sin el
fix del punto 1, este bug quedaba invisible detrás de un traceback crudo.
Corregido al valor correcto.

Con los dos fixes, `--senior` corrigió el ítem real en un solo intento. La
revalidación sostuvo un rechazo por un motivo genuino y distinto (un posible
error de acumulación de punto flotante) — no un bug del harness, evidencia
de que un criterio en prosa aparentemente preciso puede ser ambiguo en un
caso borde numérico.

## `executor_senior` migrado de DeepSeek a Kimi (2026-08-30)

El cruce de configuración de la sección anterior no era accidental — era una
migración a Kimi ya decidida pero incompleta: solo se había cambiado
`model`, no `engine`. Corregido de verdad: `engine: kimi` + el modelo de
código correspondiente (mismo que ya usa `executor` cuando cae a Kimi por el
motor local caído). Compliance/`arbitro` quedan en DeepSeek, decisión
explícita, no se tocaron.

**Validado en vivo:** primer intento dio un `ReadTimeout` real (manejado
limpio como intento fallido, sin crash, gracias al fix de motores de la
sección anterior — confirma que ese fix también cubre Kimi bajo carga real),
segundo intento generó código.

## `metricas_agentes.jsonl` — cuántas veces pasó cada item por cada agente (2026-08-30)

Se pidió una tabla al terminar el flujo con la cantidad de veces que cada
item pasó por cada agente, como métrica para detectar oscilación o escalada
excesiva — mismo análisis que ya se había hecho a mano una vez, ahora
automatizado.

**Implementado:** `_registrar_metrica_agente()` agrega una línea a
`.harness/logs/metricas_agentes.jsonl` (`{item_id, agente, timestamp}`) en
los 4 puntos únicos por los que pasa cualquier invocación real de un agente.
Cuenta intentos, no resultados. `calcular_metricas_agentes()` arma la tabla;
`loop()` la imprime siempre al salir (try/finally), sea cual sea el motivo
de salida. `--metricas` la muestra en cualquier momento sin ejecutar nada.

**Corrección (mismo día):** "cuando el flujo termine" significaba por SESIÓN
(desde que arranca el trabajo hasta que termina), no el acumulado histórico
de todas las corridas — el auto-print original mostraba todo el archivo
desde siempre, mezclando sesiones. Corregido: acepta un punto de inicio, y
`loop()` cuenta cuántas líneas tenía el archivo ANTES de arrancar para
mostrar solo lo agregado después. `--metricas` (consulta manual) sigue
mostrando el historial completo sin acotar — los dos casos de uso son
distintos a propósito.

## Timeout de `executor` bajado de 420s a 300s, con evidencia real de 3 proyectos completados (2026-08-30)

Se analizó la bitácora de Executor de los 3 proyectos reales completados
hasta ese momento buscando patrones, no solo el estado final de cada item.

**Bug real de calibración encontrado de paso:** el chequeo de "claves
citadas" de `plan_lint.py` tenía un falso positivo real — una frase con
formato "debe devolver EXACTAMENTE las claves que lee Servicio.metodo()"
(una referencia a otro símbolo, no una lista literal) se extraía como si
fueran nombres de clave reales. Corregido con un lookahead negativo + test
de regresión.

**Hallazgo principal — la tasa de timeout del motor viene subiendo, no
bajando:** 3% de los intentos en el primer proyecto, 9% en el segundo,
**16% en el tercero** terminaron en `TimeoutDelMotor` (0 archivos, 420s
agotados) — todos exclusivamente en reintentos con `thinking: on` (el
primer intento de cada item, sin thinking, prácticamente nunca tiempea).
Analizando la duración real de los 217 reintentos que SÍ terminaron bien en
esos mismos 3 proyectos: el máximo fue 242s, sin ningún caso entre 250s y
420s — un vacío limpio que indica que los 41 timeouts son cuelgues reales
del motor, no generaciones lentas que hubieran terminado con más tiempo.

**Decisión:** bajar `timeout_seconds` de `executor` de 420 a 300 — margen de
~25% sobre el máximo real observado, corta los cuelgues reales ~120s más
rápido sin haber arriesgado ningún caso exitoso histórico. Queda como
pendiente explícito revisar con `--metricas` en la próxima migración real y
reconsiderar el valor si las mejoras de "divide y vencerás" (recién
aplicadas sobre planes ya escritos, no desde el arranque) cambian la
distribución de duraciones (ver `Pendientes.md`).

## Dos bugs reales del harness encontrados agregando un item de infra (2026-08-31)

Pedido explícito: un equipo real que no usa Docker necesitaba un script que
levante todos los deployables como procesos nativos. Se agregó un item de
infra al plan real y se corrió por el pipeline normal (Executor →
Compliance), no se escribió a mano — encontró 2 bugs reales del harness en
el camino, ninguno específico de ese proyecto:

**1. `docker_check.py` exigía Docker disponible para CUALQUIER item
`tipo:"infra"`, incluso sin ningún Dockerfile/`docker-compose.yml` en
`archivos_destino`.** `verificar()` llamaba a la detección de Docker
incondicionalmente antes de mirar si había algo que construir — un script
bash puro quedaba `motor_inalcanzable` en un entorno sin Docker aunque nunca
fuera a tocarlo. Corregido: ahora resuelve primero si hay
Dockerfiles/compose reales en los archivos y solo exige Docker si encontró
algo que construir.

**2. `AgentFileGuard.write()` nunca dejaba ejecutable un `.sh` recién
escrito.** El primer intento generó el script con contenido correcto pero
sin el bit `+x` — Compliance (LLM, solo lee texto) lo rechazó correctamente:
no hay forma de que un LLM que solo ve texto verifique un bit de permisos
del filesystem. Corregido en la raíz real del problema,
`access_control.py::AgentFileGuard.write()` — cualquier `.sh` que un agente
escriba por ese método queda con `chmod +x` aplicado automáticamente. Con el
fix a nivel de harness, el criterio de aceptación original del item se
volvió redundante e imposible de verificar por Compliance de todas formas —
se sacó del `plan.json` real en vez de dejarlo esperando que "se demuestre".

**Regla general para el Planner:** cualquier criterio de aceptación que
dependa de un atributo del filesystem (permisos, ownership, symlinks) que no
sea legible desde el CONTENIDO de texto del archivo nunca lo puede verificar
Compliance (LLM) — o se resuelve con un chequeo determinístico dedicado, o
se convierte en una garantía estructural del harness (como este fix) y se
saca de `criterios_aceptacion`. No dejarlo como un criterio de texto que
nunca puede pasar.

Suite completa del harness sin regresión (125 tests en ese momento).

## Patrón real: item de ensamblado define un router de salud inline en vez de importar el ya aprobado — dos veces el mismo día (2026-09-02)

Encontrado en dos proyectos distintos el mismo día: en uno, un `main.py`
redefinía el endpoint de liveness inline y nunca montaba el router ya
aprobado — el endpoint de health quedaba sin exponer. En el otro, el código
SÍ montaba el router bien, pero Compliance rechazó igual alegando lo mismo
que el caso anterior — falso negativo, resuelto re-corriendo Compliance solo
sin regenerar nada.

**Causa raíz probable:** el criterio de aceptación de estos items ("GET
/liveness responde 200") es verificable sin importar CÓMO se montó el
router — un router inline mal hecho también puede devolver 200 para
`/liveness` (aunque pierda `/health`). Nada en `criterios_aceptacion` obliga
a que el `import` literal declarado en la `interfaz` de la dependencia
aparezca de verdad en el archivo.

**Regla a aplicar la próxima vez que un item de ensamblado dependa de un
router ya aprobado:** agregar un criterio de aceptación explícito y
grep-eable (ej. "main.py importa literalmente `app.api.salud.router`, no
redefine `/liveness`/`/health` inline") — mismo espíritu que la sección de
`plan.contract.md` sobre `interfaz` ("declararlo en `interfaz` no genera
nada, hay que pedirlo también en `detalle_tecnico`"), pero acá el punto es
que el criterio de aceptación tampoco lo estaba pidiendo explícito. No es un
bug de `orchestrator.py`/`checks/*` — es disciplina del Planner al redactar
`criterios_aceptacion` de items de ensamblado.

## Expansión a 3 flujos: creación / mantención / migración (2026-09-06)

**Pedido:** hasta acá el harness solo servía al flujo de migración. Felipe
pidió generalizarlo para cubrir también creación de proyectos desde cero y
mantención de proyectos existentes, sin triplicar la herramienta en 3 repos
— un proyecto destino puede necesitar solo uno de los tres.

**Decisión de arquitectura:** un solo repo, reorganizado en `harness-core/`
(todo lo genérico: `orchestrator.py`, `agents/`, `engines/`,
`access_control.py`, `checks/` estructurales, `knowledge/`, `config/`,
`schemas/plan.contract.md`) + `flujos/{creacion,mantencion,migracion}/`
(estrategia de Planner propia de cada uno, en su propio `README.md` +
`schemas/plan.example.json`). Los tres emiten al mismo contrato — Executor/
Compliance/checks no tienen una rama de código "por flujo", leen
`metadata.tipo_flujo` como un dato más del plan (mismo mecanismo que ya
usaban para `item.tipo`). Ver `schemas/plan.contract.md`, "Los 3 flujos".

**Compatibilidad hacia atrás confirmada:** los proyectos reales ya migrados
(o en curso) tienen `plan.json` sin `tipo_flujo` — `orchestrator._cargar_plan`
lo defaultea a `"migracion"` sin tocar el archivo en disco, y
`plan_validator.py` solo rechaza un valor presente pero inválido, nunca lo
exige si falta.

**Hallazgo de la exploración previa a implementar (importante para el
diseño):** la mecánica central del harness (orchestrator, contrato de
`plan.json`, ticket de reintento, permisos, construcción de contexto de
Executor/Compliance) ya era enteramente flow-agnostic — la única mención de
"migración de monolitos" vivía en una sola línea de framing por
`SYSTEM_PROMPT` (`executor.py`, `arbitro.py`, `documentador.py`,
`compliance.py`), neutralizada acá. El trabajo real no fue "generalizar
código migración-específico" (casi no existía a nivel de lógica), fue mover
carpetas + agregar el campo `tipo_flujo` + construir las dos piezas
genuinamente nuevas que exige mantención (ver abajo).

**Piezas nuevas, exclusivas de `tipo_flujo == "mantencion"`:**

1. **`checks/convention_check.py`** — convención de casing (snake_case/
   camelCase/PascalCase) relativa al archivo que el item toca, no la fija
   del harness. Reconstruye la versión "antes" del archivo vía
   `git show HEAD:<ruta>` (mantención asume un repo ya versionado), detecta
   el casing dominante de funciones/variables de nivel superior que YA
   tenía (excluyendo clases de la muestra — casi siempre PascalCase
   universal, sesgaría la detección), y valida que los identificadores
   NUEVOS lo sigan. Sin convención dominante clara (pocos identificadores,
   empate) no bloquea nada — evita falsos positivos en archivos chicos.
2. **`checks/regression_check.py`** — corre la suite de tests COMPLETA del
   deployable (no solo los `tests_requeridos` propios del item, eso lo
   sigue cubriendo `smoke_test.py` igual que en los otros flujos) para
   confirmar que un item de mantención no rompió algo que ya andaba.
   Reusa `smoke_test._carpeta_deployable`/`_venv_python` sin duplicar esa
   lógica. Alcance v1: solo backend/pytest — regresión de frontend
   (`ng test`) queda pendiente por falta de caso real, mismo criterio que
   ya aplica el harness a otras piezas sin evidencia (ver `Pendientes.md`).

Ambos corren en `orchestrator.validar_con_format_check`, condicionados a
`tipo_flujo`, en el mismo punto que ya usan `format_check`/`smoke_test`/
`frontend_check`/`docker_check` — un rechazo sintético corta antes de gastar
Compliance, igual que los 4 chequeos preexistentes. `agents/compliance.py`
(`_chequeos_previos`) declara como HECHO que ambos ya corrieron y pasaron,
mismo mecanismo ya usado para frontend/infra/smoke test — evita que
Compliance re-derive "sigue la convención" leyendo código a ojo.

**Sin caso real todavía** para creación ni mantención — el diseño de los dos
checks nuevos está acotado a propósito (solo casing de identificadores, solo
backend/pytest) para no construir a ciegas más de lo que hay evidencia de
que hace falta. Retomar y ampliar cuando corra un proyecto real de alguno de
los dos flujos nuevos.
