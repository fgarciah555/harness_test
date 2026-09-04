# Pendientes del harness

Ítems identificados pero deliberadamente no resueltos todavía. No implementar
sin retomar la conversación de diseño primero.


## Agente de recuperación — reinicio remoto de LM Studio vía SSH

**Contexto:** el motor local a veces queda en bucle de razonamiento (mitigado
parcialmente con detección de repetición + timeout + max_tokens, ver
`engines/lm_studio.py`). Idea: agente/script que se conecte por SSH a la
máquina de LM Studio y reinicie el servicio al detectar el bucle.

**A definir antes de implementar:**
- Auth SSH con llave dedicada (no reusar credenciales personales), alineado
  con `security-baseline.md`.
- Qué se reinicia: ¿el proceso o toda la sesión? Reiniciar de más mata
  trabajo legítimo que solo tardaba, no en bucle real.
- Cómo distinguir "piensa mucho pero va a terminar" de "está en bucle" — la
  heurística de repetición de texto es una señal, no una certeza.
- Disparo automático vs. confirmación humana la primera vez.
- Alcance: ¿agente del pipeline, o herramienta de infraestructura/monitoreo
  aparte?

**Estado:** no implementado. Retomar si el problema sigue siendo recurrente
con el pipeline en marcha.

**Evidencia que baja la prioridad (cambio de modelo como mitigación más
simple):** con un pipeline real corriendo, `qwen3.6-35b-a3b` entró en bucle
de repetición en el primer reintento de un item trivial; `qwen3.8-27b`,
corrido de punta a punta el mismo día, no entró en bucle ni una vez (solo 2
timeouts de conexión recuperables). `test_engines.py --check-thinking` midió
que `qwen3.6-35b-a3b` razona ~6.7x más verboso que `qwen3.8-27b` incluso en
una pregunta trivial (241 vs 36 reasoning_tokens) — no fue mala suerte, es
consistente con que ese modelo piensa de más. Se fijó `qwen3.8-27b` como
default en `config/models.yaml`. Prioridad del agente SSH baja un escalón —
reconsiderar si el modelo actual también entra en bucle con uso sostenido.

**Mitigación menor ya implementada (problema relacionado, no el bucle en
sí):** un timeout de conexión ya no crashea `orchestrator.py` completo —
`engines/base.py` define `TimeoutDelMotor`, `lm_studio.py` lo lanza en vez
de propagar el timeout crudo, `executor.py` lo trata como intento fallido (0
archivos) en vez de `bloqueado`, así el reintento/escalado a
`executor_senior` se hace cargo solo. No resuelve el bucle en sí (sigue
necesitando reinicio manual del servidor).

## Sandboxing real de `access_control.py`

**Contexto:** `AgentFileGuard` controla lectura/escritura por convención de
código (cada agente pasa por él porque así está escrito), no por aislamiento
real de sistema operativo — un bug en un agente podría saltarlo con
`open()`/`pathlib` directo. Aceptado como límite de la PoC desde el diseño
original. Se vuelve más urgente con el Smoke test en uso, que ya ejecuta
código generado por el modelo (vía `subprocess` con timeout).

**A definir antes de implementar:**
- Nivel de aislamiento: ¿proceso separado con permisos de SO acotados, o
  contenedor/VM por corrida?
- El sandboxing de *ejecución* (CPU/memoria/red/timeout) es una dimensión
  aparte del sandboxing de *filesystem* que ya cubre `AgentFileGuard` —
  probablemente dos mecanismos, no uno.

**Estado:** no implementado, aceptado como límite de la PoC. Revisar antes
de cualquier fase de implementación o marcha blanca real, prioridad más
alta si el Smoke test se usa más.

## Agente investigador de tecnologías (grounding contra documentación real)

**Idea:** antes de decidir cómo usar una librería/framework no trivial, ir a
buscar la respuesta en documentación oficial real en vez de que el modelo
conteste de memoria (training knowledge desactualizado o inventado — mismo
riesgo que un import alucinado, pero de librería en vez de proyecto).

**Por qué:** el motor puede tener conocimiento desactualizado de una
librería (versiones que cambian rápido) o mezclar patrones de distintas
versiones. Traer el fragmento real de documentación antes de decidir reduce
ese riesgo — mismo principio que "interfaz real reportada por Executor":
preferir una fuente de verdad concreta a una respuesta adivinada.

**Resuelto — la mitad manual ya está implementada:**
- **Fase:** disciplina del Planner (hoy Felipe+Claude Code, con
  WebSearch/WebFetch reales) — no hace falta agente automatizado mientras
  el modo sea manual.
- **Disparo:** cualquier librería no trivial usada en un item, verificada
  antes de escribir su `detalle_tecnico` — paso de proceso documentado en
  `plan.contract.md`, sin campo nuevo en el contrato.
- **Inyección sin romper el contexto mínimo:** va horneado en
  `decisiones_globales`/`detalle_tecnico`, que Executor ya recibe completo —
  nunca un canal nuevo hacia Executor.
- Resultado tangible: `Harness/knowledge/` (un archivo por librería, ver
  `knowledge/README.md`), base reusable entre proyectos.

**Sigue sin resolver — para cuando Planner se automatice:**
`ModelEngine.run()` sigue siendo texto plano sin function-calling
(`engines/base.py`) — un agente investigador automatizado necesita su
propio mecanismo de fetch, no puede ser "otro llamado más" al motor de
Executor/Compliance.

**Pausado por decisión explícita, no solo por bloqueo técnico:** Felipe
prefiere seguir en el loop para decisiones de Analyzer/Planner y reintentos
Executor-Compliance en vez de automatizar esa capa. Se conecta con una idea
relacionada ("reporte de cierre" al entregar un proyecto, para ir armando
conocimiento nuevo) — comparte el mismo bloqueo de fondo (sin fetch real,
pedirle al motor que proponga conocimiento nuevo reintroduce el riesgo de
alucinación que `knowledge/` existe para evitar) y conviene resolverlas
juntas.

**Diseño esbozado, no implementado** — separar en dos partes de riesgo
distinto:
- **Fetch real (determinístico, sin LLM):** mismo rol que WebFetch/WebSearch
  cumplen hoy en el Planner manual.
- **Extracción (motor local, rol acotado):** solo resume/extrae el patrón
  del texto ya traído — grounded en texto real, no en memoria de
  entrenamiento. No decide qué investigar ni escribe directo a
  `knowledge/`/`.agents/rules/` — propone, el Planner aprueba (mismo gate
  que `--loop` usa para reintentos).

**Pausado explícitamente — "es una mejora que tomará mucho trabajo":** el
costo real es construir y mantener el fetch tool (fuente correcta, texto
limpio, sin JS/paywalls), no el motor local. Retomar cuando haya ancho de
banda para dimensionarlo en serio.

**Confirmación posterior:** el mismo patrón de decisión reapareció al
diseñar el "Ticket de reintento" — la sección "Hechos verificados" del
ticket cumple el mismo rol de Analyzer+Planner, y quedó manual a propósito.
Confirma que el pendiente sigue pausado por preferencia, no por olvido.

## `format_check.py` — falso positivo con `from paquete import submodulo`

**Contexto:** el chequeo de nombres importados (chequeo 2) rechazó
`from app.api.v1 import auth, reportes` como import roto ("'auth' no está
definido en `__init__.py`"). Es Python válido — importa los submódulos
`auth.py`/`reportes.py` del paquete, no algo reexportado por `__init__.py`
— pero `_nombres_definidos()` solo mira lo que un módulo define/reexporta
explícitamente, nunca si el nombre importado es en realidad un archivo `.py`
hermano del mismo paquete.

**Por qué no se arregló:** se resolvió el caso puntual cambiando el
`detalle_tecnico` del item al estilo de import literal completo (`from
app.api.v1.auth import router as auth_router`), ya más explícito y sin
ambigüedad. No hizo falta tocar `format_check.py`.

**Estado:** el gap sigue — un futuro item que necesite `from paquete import
submodulo` va a chocar con el mismo falso positivo. Arreglarlo implica que
`_ruta_modulo_interno`/`_nombres_definidos` reconozcan un nombre importado
que coincide con un archivo/subpaquete hermano como válido. No se justifica
todavía — no volvió a aparecer, y el import literal ya es la convención
preferida.

## Tres flujos de arquitectura — variante de 3 deployables con BFF sigue sin caso real

**Contexto:** de las 3 topologías evaluadas (monolito, front→backend→DAL,
front→BFF→BE→DAL), las primeras dos ya están resueltas e implementadas. El
mecanismo determinístico que faltaba (`_carpeta_deployable`/
`_carpetas_backend_del_plan` en `smoke_test.py`/`generate_api_docs.py`) ya
generaliza a N carpetas backend, no solo 2 — probado contra un caso real.

**Qué falta para BFF:** la capa `orquestador/` (agrega/adapta llamadas a BE
— nunca lógica de negocio ni acceso a datos, para no confundirla con
`service/`) todavía no está en `.agents/rules/backend-architecture.md` (hoy
solo describe backend+DAL; capas por deployable ya validadas: BFF =
`api/`+`client/`+`schema/`+`orquestador/` sin `repository/`/`model/`; BE =
`api/`+`service/`+`client/`+`schema/` sin `model/`; DAL = las 4 capas
completas, único con credenciales reales). Falta también un ejemplo de 3
deployables backend en `schemas/plan.contract.md` (`arquitectura_objetivo`).

**Estado:** sin caso real — retomar cuando aparezca un proyecto con BFF de
verdad, no antes.

## Gestión de esquema de base de datos con herramienta de migraciones formal

**Contexto:** en un proyecto real, agregar columnas a una entidad existente
rompió la app en runtime pese a código 100% correcto — `Base.metadata
.create_all()` no altera tablas que ya existen, solo crea las que faltan
(ver `knowledge/sqlalchemy-2.0.md`). Hubo que correr un `ALTER TABLE`
manual, fuera del harness. A partir de eso, se pidió que todo proyecto que
el harness planifique/genere use una herramienta de migraciones formal, no
depender de `create_all()`/equivalentes — "cualquier proyecto de cualquier
tecnología": la herramienta concreta depende del stack (Alembic para
SQLAlchemy/Python; Prisma Migrate/TypeORM/Flyway para otros).

**Qué implicaría, sin implementar todavía:**
- `.agents/rules/backend-architecture.md` (sección "Configuración")
  recomendaría explícito una herramienta de migraciones en vez de (o
  adicional a) `create_all()`, heredado por el Planner como regla
  `always_on`.
- Un item que agrega/modifica columnas tendría que generar también el
  archivo de migración correspondiente, no solo el cambio en el modelo ORM
  — Executor no corre CLI por su cuenta (sin shell, solo genera código),
  tendría que generar el archivo de migración con contenido correcto
  directamente.
- Un proyecto que arranca desde cero necesitaría un item de bootstrap de la
  herramienta (ej. `alembic init`, `env.py` apuntando al engine del
  proyecto) antes del primer item que defina un modelo.
- Un proyecto ya existente sin migraciones necesita un baseline al adoptar
  la herramienta: una migración inicial que capture el esquema REAL ya en
  producción, no desde cero — más delicado que el caso greenfield.
- Ni Executor ni Compliance deberían tener acceso a la base real para
  *aplicar* una migración — aplicarla seguiría siendo un paso manual del
  Planner/humano; el archivo de migración en sí sí lo generaría Executor.

**Estado:** no implementado, sin caso real todavía. Retomar con un proyecto
nuevo (greenfield, más simple) o al decidir retrofitear un proyecto
existente (con baseline, más delicado) — no mezclar los dos diseños.

## Dockerfile real de la empresa (`docs/Dockerfile.Python`) diverge de `gcp-deployment.md` — sin definición de Arquitectura todavía

**Contexto:** Felipe entregó el `Dockerfile.Python` real que usa su empresa
hoy (single-stage, corre como `root`, sin `USER` no-root). `gcp-
deployment.md` (regla `always_on`) recomienda multi-stage y nunca correr
como root. Ya se había hablado de esto antes pero "no existe una definición
clara actualmente" — Felipe lo va a levantar con Arquitectura.

**Decisión provisoria:** usar `Dockerfile.Python` tal cual (single-stage,
root) en los repos que se generen mientras tanto — no inventar una versión
propia ni bloquear por la divergencia (señalar, no bloquear). `gcp-
deployment.md` no se tocó — sigue recomendando multi-stage/non-root como
default para código nuevo sin un template de empresa real que lo reemplace.

**Estado:** abierto. Retomar cuando llegue la definición de Arquitectura.

**`Dockerfile.Angular` (completado después) — sí es multi-stage**, a
diferencia de `Dockerfile.Python` (inconsistencia entre plantillas de
empresa, dato para Arquitectura). Tenía un bug real de path: asumía
`dist/browser/`, pero el builder moderno de Angular
(`@angular/build:application`, el que usa `ng new` en 22.x) genera la
salida en `dist/<nombre-del-proyecto>/browser/` — confirmado contra un
build real. Corregido a `dist/frontend/browser/` (atado al nombre
`frontend` de este template — si un proyecto nombra su carpeta Angular
distinto, hay que ajustar esa línea, o cambiar `outputPath` en
`angular.json` para que la salida quede en `dist/browser/` sin el segmento
del nombre).

**Resuelto, se materializó al probar un `docker-compose` real:** `npm
install -g @angular/cli` instalaba una versión del CLI que no coincidía con
`@angular/build` (dependencia local pineada en `package.json`), y el error
(`Cannot find module '.../application/index' ... Did you mean 'index.js'?`)
no mencionaba versiones — lejos de la causa real. Cambiado a `npx
--no-install ng build -c $ENV_BUILD` (usa siempre el CLI local ya
instalado, nunca uno global distinto). Corregido en `docs/Dockerfile.Angular`.

Ese cambio destapó un segundo bug real: la imagen base `node:22.6.0`
(pineada por versión exacta) es demasiado vieja para el Angular CLI 22.1.6
(pide Node ≥22.22.3, ≥24.15 o ≥26 — el error con `npx` sí fue explícito
sobre esto). Cambiada a `node:22-slim` (sigue la última patch de Node 22 en
vez de clavar una fecha de armado) en ambos Dockerfiles. `docker compose
build` + `docker compose up` corrieron completos con ambos fixes; verificado
end-to-end con un login real a través de nginx → backend → dal → Postgres
(401 `INVALID_CREDENTIALS` correcto, sin datos de seed — la cadena completa
respondió).

## `config/proyectos.yaml` no se actualiza solo al inicializar un proyecto nuevo

**Contexto:** `estado_proyectos.py` depende de que cada proyecto esté
registrado a mano en `config/proyectos.yaml` (nombre + ruta + descripción).
`init_harness.py` ya aprendió a no dejar un proyecto sin `handoff.md` por
puro olvido, pero sigue sin tocar el registro: nada recuerda agregar la
fila nueva al arrancar una migración, y un proyecto no registrado es
invisible para `estado_proyectos.py` sin que nadie lo note — más silencioso
que el gap de `handoff.md`, no hay error, el proyecto simplemente no
aparece en el resumen.

**A definir antes de implementar:**
- ¿`init_harness.py` agrega la entrada solo (determinístico, `input()` o
  defaults derivables de la ruta), o solo imprime un recordatorio, dejando
  la edición a mano?
- Si es automático, ¿qué pasa si corre sobre un proyecto que ya tiene
  entrada (evitar duplicados)?

**Estado:** no implementado. Bajo riesgo mientras el registro tenga pocos
proyectos y se revise a mano — reconsiderar si empiezan a perderse
migraciones del resumen por este motivo.

## Revisar timeout de `executor` (300s) en la próxima migración

**Contexto:** bajado de 420s a 300s con evidencia real — sobre 217
generaciones exitosas con `thinking: on` en 3 proyectos completados, el
máximo real fue 242s, sin ningún caso entre 250s-420s. Los 41 timeouts
observados en esos mismos proyectos (3%/9%/16% de los intentos totales,
tasa creciente) son cuelgues reales del motor, no generaciones lentas que
necesitaban más tiempo — bajar el timeout no arriesga ningún caso exitoso
histórico.

**Duda abierta, a verificar con datos nuevos:** las mejoras de "divide y
vencerás" (`checks/plan_lint.py`, señales de tamaño/ambigüedad) recién se
aplicaron sobre un plan ya escrito — ningún proyecto se planificó todavía
con esas señales revisadas desde el arranque. Hipótesis: items más
chicos/menos ambiguos podrían generar más rápido y confiable (menos
"pensar de más" → menos cuelgues), cambiando por completo la distribución
de duraciones de arriba.

**Retomar en la próxima migración real:** correr `--metricas`
(`orchestrator.py`) y comparar tasa de timeout y distribución de duraciones
contra estos números. Si mejoró claramente, reconsiderar si 300s sigue
siendo correcto (podría subir de nuevo si los cuelgues bajaron, o confirmar
300s si el cuello de botella era el motor y no el tamaño de los items).
