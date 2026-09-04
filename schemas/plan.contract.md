# Contrato `plan.json`

`plan.json` es el único artefacto que conecta la fase de análisis/planificación
(hoy: Felipe + Claude, leyendo el proyecto real) con la fase de ejecución
automatizada (Executor + Compliance corriendo con el modelo local). Vive en
`<proyecto>/.harness/config/plan.json` y es versionado en git — es la fuente
de verdad de qué hay que migrar, cómo y con qué criterio de aceptación.

El Planner (hoy, humano) tiene permiso `read_write` sobre `harness_config`
(ver `access_control.py` / `config/permissions.yaml`). El Executor solo tiene
`read` sobre ese mismo directorio: puede leer el plan pero no modificarlo.

## Principio general

Cada `item` debe ser ejecutable por el modelo local **sin que tenga que
tomar ninguna decisión de arquitectura**. Esas decisiones (dónde va cada
archivo, qué convención de nombres usar, cómo se maneja auth) ya están
resueltas en el plan, aplicando `AGENTS.md` + `.agents/rules/` del proyecto
destino. El modelo local rinde mal en razonamiento multi-paso — el plan
existe precisamente para no depender de que lo razone bien.

## Estructura de nivel superior

```json
{
  "metadata": { ... },
  "decisiones_globales": { ... },
  "items": [ { ... }, ... ],
  "riesgos_heredados": [ { ... }, ... ]
}
```

### `metadata`

Identifica el plan y su origen. No la usa el Executor para decidir nada, es
trazabilidad.

| Campo | Tipo | Descripción |
|---|---|---|
| `version` | string | Versión del *contrato* (este documento), no del plan. Hoy `"1.0"`. |
| `proyecto` | string | Nombre del proyecto/monolito que se está migrando. |
| `generado_en` | string (ISO 8601) | Timestamp de cuándo se escribió/actualizó el plan. |
| `generado_por` | string | Quién lo armó, ej. `"felipe+claude"`. |
| `monolito_origen` | string | Ruta o descripción de dónde vive el código fuente que se migra. |
| `arquitectura_objetivo` | object | `{ "backend": "FastAPI", "frontend": "Angular" }` en el caso default (un solo deployable backend, DAL = capa `repository/` interna). Si el proyecto separa backend y DAL en deployables propios (ver `.agents/rules/backend-architecture.md`, sección "Backend y DAL como deployables separados"), agregar `"dal": "FastAPI"` explícito — es la señal que el Planner usa para escribir `depende_de`/`interfaz` de backend→dal por red en vez de import directo, y la que confirma que `archivos_destino` de esos items va a tener más de una carpeta raíz (`backend/`, `dal/`) dentro de los items `tipo: "backend"`. |

### `decisiones_globales`

Decisiones transversales que aplican a **todos** los items y que el Executor
no debe re-derivar por su cuenta. Si una decisión afecta un solo item, va en
`detalle_tecnico` de ese item, no acá.

| Campo | Tipo | Descripción |
|---|---|---|
| `auth_destino` | string | Cómo se resuelve auth en el destino (reemplazo de la sesión de servidor). Ej. `"JWT bearer, login emite access+refresh token, FastAPI usa OAuth2PasswordBearer"`. |
| `prefijo_api` | string | Prefijo de rutas del backend FastAPI, ej. `"/api/v1"`. |
| `casing_json` | `"camelCase"` \| `"snake_case"` | Casing de los payloads JSON entre backend y frontend. |
| `manejo_errores` | string | Referencia/resumen de la convención de `error-handling.md` aplicada (formato de error estándar). |
| `schema_bd_origen` | string \| null | Opcional. Ruta o referencia al script/DDL/migración real de la base de datos del monolito origen, si existe — ver disciplina de "modelo canónico" más abajo. `null` si el proyecto no tiene BD existente (greenfield) o el Planner no tuvo acceso a ella. |

Se puede extender con más decisiones globales según haga falta (paginación,
formato de fechas, etc.) — la lista de arriba es el mínimo con el que
arrancamos, no un límite cerrado.

**Importante — ni Executor ni Compliance leen `AGENTS.md`/`.agents/rules/`
nunca.** Su contexto es exactamente `decisiones_globales` + el item +
`interfaz` de dependencias, nada más — es una decisión de diseño a
propósito (ver "Principio general" más arriba). Las reglas del context pack
del proyecto destino solo llegan a los agentes si el Planner las traduce acá
explícitamente. Cada archivo de `.agents/rules/` declara `activation:
always_on` o `model_decision` en su frontmatter — **al armar
`decisiones_globales` para un proyecto, el Planner debe recorrer
sistemáticamente todos los archivos `always_on` relevantes al tipo de item**
(backend/frontend) y volcar sus reglas fijas acá, no confiar en acordarse
de cada una al redactar cada item por separado. Nos faltó hacer esto la
primera vez que probamos contra un monolito real y un item violó una regla
`always_on` de `backend-architecture.md` (el `commit()` en la capa
equivocada) sin que ningún criterio lo detectara — ver `handoff.md`,
sección "Primeras pruebas reales".

**Antes de escribir `detalle_tecnico` de un item que usa una librería/
dependencia no trivial (más allá de built-ins de Python o del framework ya
cubierto en `.agents/rules/`), el Planner consulta `Harness/knowledge/<lib>.md`**
— ver `knowledge/README.md` para el formato y la disciplina completa. Si no
hay entrada o la librería es nueva en este proyecto, el Planner verifica el
patrón real (WebSearch/WebFetch a la documentación oficial, o un snippet
ejecutado de verdad) en vez de asumir que el conocimiento de entrenamiento
del modelo está actualizado, y agrega/actualiza la entrada correspondiente.
El patrón verificado se vuelca directo acá o en el `detalle_tecnico` del
item — **Executor no lee `Harness/knowledge/` directo**, mantiene su
contexto mínimo a propósito (ver "Principio general"); el Planner es quien
filtra y resume. Existe porque el 2026-08-21, en una replanificación completa
de un backend real, tanto Executor como el propio Planner generaron
varios patrones de librería plausibles pero incorrectos (`pydantic-settings`,
`fastapi.security`, SQLAlchemy 2.0 — ver `knowledge/` para el detalle) —
mismo tipo de riesgo que ya se había visto con imports internos alucinados
(`DomainError`, ver sección de `interfaz` más abajo), pero de conocimiento de
librería en vez de conocimiento del proyecto.

**Antes de escribir el modelo canónico de una entidad persistida (el primer
item que la define, típicamente uno del dominio `CORE`), el Planner debe
pedir/obtener el script real de la base de datos del monolito origen (DDL,
migración, o el modelo ORM ya existente) si existe, y derivar el tipo de
cada campo de ahí — no inventarlo ni adivinarlo por convención.** El tipo
resultante se declara en `decisiones_globales.schema_bd_origen` (referencia
a la fuente) y se propaga como contrato obligatorio en la `interfaz` del
item que define el modelo (import literal + tipo explícito, mismo mecanismo
ya usado para `RowSchema` en items de repository, ver `handoff.md`, sección
"Row schemas Pydantic reemplazan `list[dict]` en queries con JOIN") — todo
item dependiente que toque ese campo hereda ese tipo,
no lo vuelve a decidir. Esto no reemplaza la fase de smoke test/tests: sigue
haciendo falta un test real para casos borde de formato (ej. un dígito
verificador que puede ser una letra) — lo que sí evita es que una función
declare un tipo de retorno que contradice el tipo real de la columna que
manipula (ej. devolver `int` para un campo que en la BD real, y en todo el
resto del sistema, es `str`). Sin BD existente (proyecto greenfield o sin
acceso), `schema_bd_origen` queda `null` y el Planner decide el tipo con el
criterio habitual — no es un bloqueo, es "usar la fuente de verdad cuando
existe" en vez de asumirla ausente por defecto. No requiere que Executor,
Compliance ni ninguna pieza automatizada del harness tengan acceso a la
BD — la lectura del schema es responsabilidad exclusiva del Planner en
tiempo de planificación, igual que la investigación de librerías en
`knowledge/`; no es un agente nuevo.

### `items[]`

La unidad atómica de trabajo. Un item = una unidad de código que el Executor
puede generar de punta a punta y que el Compliance puede validar de forma
aislada.

| Campo | Tipo | Descripción |
|---|---|---|
| `id` | string | Identificador único, ej. `"PED-001"`. Prefijo por dominio + número. |
| `origen` | object | De dónde viene en el monolito: `{ "archivo": "...", "referencia": "nombre de función/ruta/template" }`. |
| `tipo` | `"backend"` \| `"frontend"` | Un item no mezcla ambos — si una feature necesita los dos lados, son dos items enlazados por `depende_de`. |
| `descripcion` | string | Qué hace este item, en una o dos frases, para un humano. |
| `archivos_destino` | string[] | Rutas **ya resueltas** en el proyecto destino, siguiendo `naming-conventions.md` / `backend-architecture.md` / `frontend-angular.md`. El Executor no elige nombres ni ubicación, solo escribe ahí. |
| `detalle_tecnico` | string | Instrucción concreta y suficiente para generar el código: qué endpoint/componente, inputs/outputs, validaciones, reglas de negocio a preservar del origen. |
| `criterios_aceptacion` | string[] | Lista verificable — cada criterio debe poder chequearse (por Compliance o por un humano) sin ambigüedad. Evitar criterios vagos tipo "funciona bien". |
| `depende_de` | string[] | IDs de otros items que deben estar `completado` antes de que este pueda ejecutarse. Lista vacía si no depende de nada. |
| `interfaz` | object | Lo que este item **expone hacia afuera** — deliberadamente chico. Es lo único de este item que otro item puede necesitar leer si lo tiene en `depende_de`. Ver sección siguiente. |
| `ticket_id` | string \| null | Opcional. Traza qué items vienen del mismo ticket/requerimiento de cliente, cuando el Planner parte un ticket grande en varios items chicos ("divide y vencerás" — ver `Pendientes.md`). No afecta la ejecución, es solo trazabilidad. |
| `tests_requeridos` | object[] | Opcional. `{ "archivo": "...", "contenido": "<código pytest completo>" }[]`. Escritos por el Planner, no inventados por Executor/Compliance sobre la marcha — ver "Smoke test" más abajo. Un item sin este campo (o con lista vacía) simplemente no pasa por pytest, va directo a Compliance como hoy. |
| `estado` | `"pendiente"` \| `"omitido"` | Ver nota sobre inmutabilidad abajo. |

### `interfaz` — contrato de consumo entre items

Existe para que ejecutar un item **no requiera leer los items anteriores
completos**. `detalle_tecnico` es la instrucción interna para generar el
código de *este* item — puede ser larga y no le importa a nadie más.
`interfaz` es lo contrario: el resumen mínimo que un item dependiente
necesita para consumir a este, sin saber cómo está implementado por dentro.

Forma típica según `tipo`:

- **backend**: `{ "endpoint": { "metodo", "ruta", "request", "response_2xx" }, "dependencia_reusable": { ... } }` (esta última solo si el item deja algo reusable por otros endpoints, ej. una dependency de FastAPI para validar JWT).
- **frontend**: `{ "servicio_expuesto": { "nombre", "metodos" } }` y/o `{ "selector", "ruta_angular" }` si otro componente necesita saber cómo navegar a este.

**Todo lo que otro item vaya a importar debe llevar la línea de import
literal en la `interfaz`, no solo el nombre o una descripción en prosa.**
No alcanza con `{ "nombre": "get_current_user" }` — hace falta
`{ "import": "app.service.autenticacion_service.get_current_user" }` (ruta
completa, copiable tal cual a un `from ... import ...`). Esto incluye
módulos enteros pensados para que otro item los monte/importe (ej. un
router), no solo funciones sueltas — usá una entrada tipo `modulo_router` o
similar con su `import` exacto. Motivo, con evidencia real: en una migración
real, **todos** los items que necesitaban `get_current_user`
(que sí tenía `import` literal en la `interfaz` de su dependencia) lo
importaron bien; los que necesitaban `DomainError` (mencionado solo en
prosa en `detalle_tecnico`, sin `import` literal en ninguna `interfaz`
alcanzable) lo importaron mal — el modelo adivinó un nombre de módulo
plausible pero inexistente. Sin ambigüedad estructural, no hay error; con
ambigüedad, el modelo elige entre convenciones igual de razonables y a
veces no es la que el proyecto usa.

**Si `detalle_tecnico` menciona reusar algo de otro item, ese item TIENE
que estar en `depende_de`.** Las dependencias no son transitivas: un item
solo recibe la `interfaz` de los ids que él mismo lista en `depende_de`, no
la de las dependencias de sus dependencias. Citar un item en prosa
("heredá de DomainError, definido en el item de configuración base") sin ponerlo en
`depende_de` significa que ese item nunca llega al contexto de Executor —
va a intentar cumplir la instrucción igual, adivinando de dónde importar.

**La regla de arriba no es solo sobre imports — es sobre cualquier hecho
de integración que un item dependiente necesite para verificar (o generar)
correctamente algo que involucra a esta dependencia.** Evidencia real: un
item que monta routers de FastAPI (`app.include_router(..., prefix=X)`)
necesita saber el prefijo *interno* que cada router ya declara (no solo su
ruta final) para calcular el prefijo adicional correcto — si la `interfaz`
de cada router solo dice la ruta final, ni Executor ni Compliance pueden
verificar la cuenta, y Compliance (que ante la duda marca `cumplido=false`)
rechaza código que en realidad está bien, porque no tiene con qué
confirmarlo. La corrección no es "arreglar el código" — es agregar el dato
que faltaba a la `interfaz` (ej. `prefijo_interno_router`) y volver a
evaluar. Antes de dar un item por "lo más chico que puede exponer" en su
`interfaz`, pensar: ¿con esto alcanza para que algo QUE DEPENDE DE ESTO
pueda verificar que lo usó bien, no solo para saber que existe?

**Dependencia cruzando un deployable distinto (backend→dal por red, ver
`backend-architecture.md`): `interfaz.endpoint` es el contrato, no un
`import`.** No hay proceso/venv compartido, así que "import literal" no
aplica — el item de `client/` en `backend/` no puede importar una clase
Python que vive en `dal/`. En su lugar, `interfaz.endpoint.request`/
`response_2xx` del item del DAL (mismo campo que ya se usa para endpoints
públicos) es la fuente de verdad que el `detalle_tecnico` del item cliente
copia para definir su propio schema Pydantic — duplicado a propósito (dos
definiciones, un contrato), no una importación cruzada. Mismo nivel de
precisión que `import` literal exige para dependencias en el mismo proceso:
nombres de campo y tipos exactos, no una descripción en prosa.

Regla de armado de contexto para el Executor: cuando toma un item, su
contexto de entrada es **`decisiones_globales` + el item en sí + la
`interfaz` (no el item completo) de cada id listado en `depende_de`**. Nunca
lee `detalle_tecnico`, `criterios_aceptacion` ni `archivos_destino` de otro
item, ni el log de ejecución de items anteriores — eso no le aporta nada
para escribir código y solo gasta tokens.

**`interfaz` no es una instrucción de generación, es solo lo que se
promete hacia afuera.** Executor nunca lee la `interfaz` de su propio item
— solo la de sus dependencias (ver arriba). Si algo declarado en la
`interfaz` de un item (ej. una dependencia reusable de FastAPI) tiene que
existir de verdad, hay que pedirlo también, explícitamente, en
`detalle_tecnico` — declararlo solo en `interfaz` no genera nada. Este error
concreto pasó en la primera prueba contra un proyecto real (ver
`handoff.md`).

**Reintentar un item ya `aprobado` invalida a sus dependientes.** Un item
solo puede haberse generado *después* de que sus dependencias ya estuvieran
`completado` (`seleccionar_siguiente_item` lo exige). Si una dependencia se
vuelve a ejecutar más tarde — un reintento forzado, por ejemplo, para
arreglar un bug encontrado después de la aprobación — la regeneración
completa del item (`archivos_destino` entero, no un parche) no tiene por
qué preservar la misma forma exacta (nombres de clases/funciones) que la
versión anterior, aunque `interfaz` sea la misma en el papel. Nada fuerza
esa estabilidad hoy. Encontrado en la práctica: al re-arreglar un item de
login real, `usuario_repository.py` pasó de
exponer una clase `UsuarioRepository` a funciones sueltas — rompiendo a
`comercio.py`, que ya estaba `completado` e importaba la clase vieja.

Regla: cuando un item que ya tenía veredicto `aprobado` se ejecuta de
nuevo, **se invalida el veredicto de todo item que dependa de él, directa o
transitivamente** — se les borra el veredicto, no se les toca el código.
Eso los devuelve a la cola de Compliance (con el `format_check`/import-check
de por medio) para confirmar que siguen funcionando contra la nueva
versión; si no, entran al mismo circuito de rechazo→reintento de siempre,
con el error real como feedback. No se regenera nada a ciegas — se
re-verifica, y solo se regenera lo que de verdad se rompió.

**`plan.json` es inmutable una vez escrito por el Planner.** Executor solo
tiene `read` sobre `harness_config` (ver tabla de permisos en
`access_control.py`/`permissions.yaml`) — no puede ni debe escribir en este
archivo. Por eso `estado` en `plan.json` no es un campo que evolucione en el
tiempo: el Planner lo deja en `"pendiente"` para todo item a ejecutar (o
`"omitido"` si decide dejarlo fuera de esta fase a propósito). Si hace falta
replanificar, es un acto explícito del Planner (nueva escritura completa del
archivo), no una transición automática.

El progreso real de ejecución (`en_progreso`, `bloqueado`, `completado`,
`rechazado`) se trackea **fuera de `plan.json`**, en `harness_logs` y
`harness_validation` — ver la sección siguiente.

### Smoke test — `tests_requeridos[]`

Antes de gastar una llamada a Compliance, si el item tiene `tests_requeridos`,
`smoke_test.py` corre pytest de verdad contra el código que Executor generó.
Es un filtro determinístico y gratis (sin LLM), en la misma categoría que
`format_check.py` — corre primero porque es más barato y da un motivo de
rechazo objetivo (stack trace real) en vez de que un LLM adivine leyendo.

- **Quién escribe los tests: el Planner, no un LLM del pipeline.** Si
  Compliance o Executor inventaran los tests al vuelo para validarse a sí
  mismos, sería "corregir el propio examen" — un test mal pensado podría
  validar trivialmente código equivocado. Los tests ya están especificados
  en `plan.json` antes de que exista el código.
- **Quién los escribe a disco:** `smoke_test.py` mismo, mecánicamente (no
  es un agente, no pasa por `AgentFileGuard` ni `permissions.yaml` —
  misma categoría que `api_endpoints.py`). El Planner no tiene permiso de
  escritura sobre `project_dir`, así que el contenido vive en `plan.json`
  hasta que `smoke_test.py` lo materializa como archivo real, justo antes
  de correr pytest.
- **Aislamiento (mínimo, no sandboxing real — ver `Pendientes.md`):**
  timeout al proceso (`TIMEOUT_SEGUNDOS` en `smoke_test.py`) y nada de
  tocar la base de datos real. Para esto último, `tests_requeridos` debe
  usar el mecanismo que `configuracion` en `decisiones_globales` ya prevé:
  `get_settings(env_path=".env.pytest")` (o el equivalente que defina el
  proyecto) apuntando a datos de prueba, nunca a la conexión real. Es
  responsabilidad del Planner escribir el test así — `smoke_test.py` no lo
  fuerza ni lo valida.
- **Dependencias del proyecto destino:** se asume que `pytest` (y lo que
  haga falta para levantar la app en test, ej. `httpx` para
  `TestClient`) ya están en el venv del proyecto destino
  (`backend/venv`, `venv` o `.venv`, en ese orden). El harness no instala
  nada — si no encuentra un venv, es un rechazo con motivo explícito
  ("instalar pytest antes de declarar `tests_requeridos` para este
  proyecto"), no un crash silencioso.
- **Limitación conocida:** si pytest ni siquiera puede correr (venv no
  encontrado, timeout), hoy eso también se cuenta como un rechazo — puede
  gastar reintentos de Executor en vano hasta que alguien note el problema
  de infraestructura en `reporte_fallas.md` y lo resuelva (ej. instalando
  pytest). No hay un estado separado para "no se pudo verificar" todavía.

### Frontend check — `ng build` real antes de Compliance

Equivalente al Smoke test, pero para items `tipo: "frontend"` — corre
siempre (no depende de un campo opcional como `tests_requeridos`), porque a
diferencia de pytest, compilar el proyecto no requiere que el Planner
escriba nada de antemano. Implementado en `frontend_check.py`.

- **Qué hace:** corre `ng build --configuration development` de verdad
  contra `<proyecto-destino>/frontend` (requiere que ya exista, ver
  `frontend-angular.md` para cómo bootstrapearlo con `ng new`). Si no
  compila (error de TypeScript, de template, de import), rechazo sintético
  con el diagnóstico real del compilador como feedback — mismo mecanismo
  que format check/smoke test (`_veredicto_sintetico_rechazado` en
  `orchestrator.py`).
- **Por qué siempre y no opcional:** a diferencia de pytest (que verifica
  comportamiento, requiere que alguien haya escrito qué comportamiento
  probar), que el proyecto compile es una propiedad binaria y gratis de
  verificar — no hay razón para dejarlo opcional.
- **Dependencias:** Node.js + Angular CLI deben estar disponibles.
  `frontend_check._npx_binario()` busca `npx` en `PATH` y, si no está (caso
  típico: Node instalado vía `nvm` en un shell que no sourceó `nvm.sh`,
  que es como corre por defecto un subprocess del harness), cae a buscar
  la versión más nueva en `~/.nvm/versions/node/`. El harness no instala
  Node — si no lo encuentra, rechazo con motivo explícito.
- **No hay equivalente a `format_check.py` aparte para TypeScript.** A
  diferencia de Python (donde el chequeo de imports vía `ast` es gratis y
  no necesita el toolchain completo instalado), no hay una forma barata de
  validar TypeScript sin invocar el compilador real — así que acá el único
  filtro determinístico ES la compilación completa, no hay una capa previa
  más liviana.
- **`ng build` valida el proyecto ENTERO, no solo el item en cuestión.**
  A diferencia de `format_check.py`/`smoke_test.py` (que solo miran los
  `archivos_destino` del item), un error en CUALQUIER archivo del proyecto
  Angular rechaza CUALQUIER item que se valide mientras tanto — evidencia
  real: regenerando 7 items frontend seguidos, cada validación intermedia
  mostraba errores de archivos que todavía no les tocaba el turno. Al
  regenerar varios items frontend seguidos (ej. en `--loop`), conviene
  terminar todas las regeneraciones antes de confiar en una validación
  puntual — o simplemente re-validar al final, una vez que el lote
  completo esté regenerado.
- **Los componentes standalone de Angular no traen nada implícito —
  decirlo explícitamente en `decisiones_globales`.** Evidencia real
  (2026-08-20): 6 de 9 items frontend fallaron la
  primera vez por usar `routerLink`, `*ngIf`, `*ngFor`, etc. en el
  template sin listarlos en el array `imports` del `@Component` — error
  de compilación real (`NG8002`/`NG8103`), no ceguera de contexto ni
  alucinación de import. Agregar esto a `decisiones_globales` (ej.
  `estructura_frontend`) desde el arranque de cualquier ticket frontend
  nuevo, no esperar a que falle para agregarlo.

### Docker check — build/runtime real antes de Compliance, para `tipo: "infra"`

Mismo principio que Frontend check, pero para Dockerfiles/docker-compose.yml
— corre siempre, no depende de un campo opcional. Implementado en
`docker_check.py`. Motivado por un caso real (2026-08-27): un DAL necesita el
driver ODBC de IBM para AS400 instalado vía `apt` (`ibm-iaccess`), no `pip`
— Compliance es un LLM que solo lee texto (no ejecuta nada, ver
`agents/compliance.py`), así que no puede detectar que el paquete instaló
el driver bajo un nombre distinto al que el código espera (`AS400_DRIVER`)
hasta que alguien lo prueba en runtime.

- **Qué hace:** por cada `Dockerfile` en `archivos_destino` del item,
  `docker build` real. Si el item declara `verificacion_runtime` (lista de
  `{"comando", "debe_contener"}`), corre cada comando DENTRO de la imagen
  recién construida (`docker run --rm`) y confirma que su salida real
  contenga el texto esperado — así se verifica el nombre de un driver ODBC
  registrado, no se asume. Si `archivos_destino` incluye un
  `docker-compose.yml`, corre `docker compose config -q` (rápido, YAML
  roto) y `docker compose build`; si el item declara `smoke_http` (lista de
  `{"servicio", "puerto_contenedor", "path"}`), hace `docker compose up
  -d`, resuelve el puerto real publicado y hace polling a ese endpoint
  esperando 200 — `docker compose down -v` corre siempre, pase lo que pase.
- **`docker` no disponible ≠ rechazo de código.** Si el binario o el daemon
  no responden, el estado es `"motor_inalcanzable"` (mismo mecanismo que
  usa el harness cuando LM Studio está caído) — el loop se pausa sin gastar
  un reintento de Executor por algo que no es culpa del código generado.
- **No verifica conectividad a sistemas externos reales** (bases de datos
  productivas, AS400, LDAP) — sin credenciales reales en el entorno de
  desarrollo sería un smoke test falso. Solo usar `smoke_http` contra
  endpoints que no dependan de esos sistemas (ej. `/health` estático) y
  documentar la conectividad real como `riesgos_heredados`.
- **`interfaz` de un item `tipo: "infra"` no está vacía aunque no exponga
  un símbolo de código** — si otro item de infra lo cita en `depende_de`
  (ej. `docker-compose.yml` dependiendo de los Dockerfiles que referencia),
  `plan_validator.py` igual exige `interfaz` no vacía; acá se usa para
  describir el contexto de build y el puerto que expone, información real
  que el item dependiente necesita.

### Archivos raíz/entry-point — invisibles a `ng build`/`format_check`/smoke test

**Contexto (2026-08-23):** en un proyecto real, `app.component.html`
quedó con el scaffold completo de `ng new` (logo de Angular, texto de
bienvenida, links de demo) durante las 4 tandas de items frontend — ningún
item tuvo nunca `app.component.html`/`.ts` en su `archivos_destino`. Felipe lo
encontró recién al usar el sitio (obligaba a scrollear para ver cualquier
pantalla real, debajo de ~250px de hero de demo). Ni `ng build` (compila
perfecto, es HTML/TS válido), ni `format_check.py` (no hay import roto), ni
Compliance (nunca evaluó un item que tocara ese archivo) lo detectaron.

**Por qué es un caso distinto del patrón ya conocido** (items de configuración
base del backend, "infraestructura que nadie pidió explícitamente", ver
`decisiones_globales`/sección de arriba): esos casos SÍ se detectaban solos,
porque otro item importaba un símbolo de ese archivo y el import roto
disparaba `format_check.py`/pytest de inmediato. Un archivo raíz/entry-point
(`app.component.*` en Angular, o el equivalente en cualquier framework que se
bootstrapee con un scaffold) no tiene dependientes — nada lo importa, así que
no hay ningún chequeo basado en imports que lo pueda forzar a la superficie.
Es un nodo terminal del grafo de dependencias, no uno intermedio.

**Regla para el Planner:** para cada capa que se bootstrapea con el `new`/
scaffold de un framework (`ng new`, `create-react-app`, etc.), declarar
explícito un item de limpieza del shell raíz (`ticket_id: null`, mismo
tratamiento que los items de configuración base) apenas se bootstrapea el
proyecto, no esperar a que aparezca al planificar las pantallas reales — el
criterio de aceptación debe verificar negativamente contra el texto/markup
conocido del scaffold (ej. "no contiene el texto 'Congratulations'"), no solo
que compile. Ver `handoff.md` para un ejemplo real aplicado.

### Items "ensambladores" van al FINAL, dependen de TODO lo que montan — vale igual para rutas Angular que para routers FastAPI

**Contexto (2026-08-27):** `app.routes.ts` (el
equivalente Angular de `main.py` montando routers) se planificó como item
temprano de configuración base, dependiendo solo de los servicios base — igual
error, en el otro stack, que ya se había cometido y corregido con
`main.py` en un backend real (ver "Contexto ampliado de Compliance" más abajo).
Executor se bloqueó primero (no tenía el import path de los 5 componentes
de página) y, forzado sin esa info, adivinó una carpeta plural
(`@app/pages/...`) que no existía, escribiendo un `app.routes.ts` roto.
Como `frontend_check.py` corre `ng build` del proyecto ENTERO para
cualquier item (no solo del que se está validando), ese único archivo roto
bloqueó en cascada a TODOS los demás items frontend en curso — cada uno
detectó correctamente "no es mi culpa, no toco archivos ajenos" y quedó
`### BLOQUEADO`, pero igual costó una llamada al modelo por cada uno antes
de frenar (mismo patrón ya visto en otro proyecto real, un item de reporte
rompiendo `ng build` para 3 items ajenos — la causa de fondo ahí era una respuesta vacía
del motor, no un problema de planificación, pero el efecto cascada es
idéntico).

**Regla para el Planner:** cualquier item que "monta" piezas generadas por
otros items (`main.py` ensamblando routers FastAPI, `app.routes.ts`
ensamblando páginas Angular con `loadComponent`/`RouterModule`, o el
equivalente en cualquier otro framework) es un item de **cierre**, nunca
uno temprano — su `depende_de` tiene que incluir TODO lo que monta, no solo
la infraestructura base. Y cada pieza montada tiene que declarar su símbolo
exportado (nombre de router/componente + import path exacto) en su propia
`interfaz.dependencia_reusable` — sin eso, el item de cierre no tiene de
dónde sacar el path real y Executor adivina (mismo principio de
"`interfaz` no es instrucción de generación, hay que declararla" ya
documentado para RowSchemas/routers backend, extendido acá a componentes
Angular).

**Recuperación si igual pasa** (el archivo raíz ya quedó roto en disco,
bloqueando `ng build`/`format_check` para todo el proyecto): no reintentar
el `--loop` normal a ciegas — cada item ajeno vuelve a fallar por lo mismo,
gastando una llamada cada vez. Orden manual que sí funciona: (1) generar
SOLO con Executor (sin correr Compliance todavía) cada pieza que el item de
cierre necesita y que aún no existe, en orden de dependencia; (2) recién
ahí, generar/regenerar el item de cierre en sí (ahora con los paths reales
en disco, no adivinados); (3) correr Compliance empezando por el item de
cierre (arregla la causa raíz del `ng build` roto) y siguiendo con el resto
en cualquier orden — items que habían quedado `bloqueado` solo por la
cascada pasan limpio sin necesidad de regenerar su propio código.

### Revisar un archivo ya generado: nunca "sin cambios", siempre literal

**Contexto (2026-08-23):** un `detalle_tecnico` de revisión que decía "SCSS
sin cambios respecto a la ronda anterior" (en vez de pegar el archivo
completo) resultó en que 3 componentes con la MISMA hoja de estilos
compartida (`.filtro-card`/`.acciones`, en un módulo real de reportes)
terminaran con 3 valores distintos de `align-items` y dos de los tres sin
una regla que sí estaba pedida (`height: 56px` en los botones) — cada
regeneración reescribía su propia aproximación del SCSS en vez de preservar
la versión anterior. A diferencia de HTML/TS (que si se pegan literales se
reproducen fieles, confirmado muchas veces en este proyecto), un archivo
sin contenido literal en el prompt NO se preserva de forma confiable entre
regeneraciones — Executor no tiene el archivo anterior en su contexto salvo
que se lo den explícitamente (ver "Principio general": el contexto es
deliberadamente acotado, sin historial).

**Regla para el Planner:** al escribir un item que revisa un archivo que
YA existe, `detalle_tecnico` tiene que incluir el contenido completo y
literal del archivo resultante — nunca "sin cambios respecto a la versión
anterior", ni siquiera para un archivo que en teoría no se está tocando en
esta revisión. Si de verdad no hay que tocar un archivo, la forma correcta
es sacarlo de `archivos_destino` del item (ownership acotado a lo que
realmente cambia), no dejarlo en la lista con la instrucción de "no
tocarlo" — eso le pide a Executor regenerar un archivo sin dárselo, y
correctamente responde `### BLOQUEADO` en vez de inventarlo (confirmado en
vivo en un item de reporte frontend real).

### Revisiones sucesivas de un item: `archivos_destino` acumula, no se resetea

**Contexto (2026-08-24):** corriendo un proyecto real completo desde
cero por primera vez (ver `handoff.md`), 8 de 33 items
fallaron por el mismo motivo: un archivo que el proyecto real ya tenía en
disco (`autenticacion.models.ts`, `menu-cliente.component.ts`,
`saldo_service.py`/`saldo_response.py`, `factura_response.py`, etc.) no
existía en ninguna regeneración limpia, porque ningún item lo tenía en
`archivos_destino`. Causa raíz, distinta de los dos casos de arriba (estos
archivos SÍ tienen dependientes reales, no son nodos terminales): la
revisión más reciente de cada item había acotado `archivos_destino` a solo
lo que esa ronda tocaba (siguiendo correctamente la regla de arriba), pero
`archivos_destino` de ese item en el `plan.json` vigente reemplazó a la
lista de la revisión anterior en vez de mantener la unión — el archivo
quedó huérfano, generado en una revisión vieja que ya no existe como tal.

**Por qué nunca se detectó antes:** cada regeneración parcial (probar un
item puntual, un `--item` forzado, incluso correr `--loop` sobre el
proyecto real ya bootstrapeado) corría contra un filesystem donde el
archivo YA estaba, escrito por la revisión vieja — el import resolvía
porque el archivo existía físicamente, no porque el plan lo generara. La
ausencia solo se hizo visible corriendo el proyecto ENTERO desde una
carpeta vacía, algo que no había pasado hasta esta prueba.

**Regla para el Planner:** al escribir una revisión nueva de un item que
acota `archivos_destino` a lo que cambia en esa ronda (regla de arriba),
verificar que el archivo sacado de la lista siga apareciendo en
`archivos_destino` de ALGÚN item del plan vigente — el propio (revisión
anterior conservada en otro campo no cuenta, `plan.json` no versiona
historial) u otro que lo adopte explícitamente. Un archivo con importadores
reales que desaparece de `archivos_destino` de todo el plan no se detecta
con una corrida parcial ni incremental — solo con una regeneración completa
desde cero, que no es la forma habitual de trabajar. Si se sospecha este
gap en un plan existente: `grep` cada import interno (`@app/`, `app.`)
contra el conjunto completo de `archivos_destino` de todos los items, no
contra el filesystem del proyecto ya bootstrapeado.

### `riesgos_heredados[]`

Problemas del monolito original que se preservan a propósito en la migración
(por alcance, tiempo, o porque tocarlos es una decisión aparte) y que no
deben interpretarse como bugs a corregir silenciosamente por el Executor ni
como fallas de Compliance.

| Campo | Tipo | Descripción |
|---|---|---|
| `descripcion` | string | El riesgo/problema heredado, ej. "el monolito no valida longitud máxima de `nota_pedido`". |
| `item_relacionado` | string \| null | ID del item afectado, si aplica a uno en particular. |
| `mitigacion` | string | Qué se hizo o se decidió al respecto (mitigar ahora, dejar para después, aceptar el riesgo). |

### Sistemas externos sin instancia de dev accesible — declarar el modo mock desde el arranque

Si un item `tipo: "backend"` (DAL o backend) define un `repository`/
`service` que toca un sistema externo (otra base de datos, AS400, LDAP,
API de terceros) sin instancia de desarrollo/QA accesible, `detalle_tecnico`
debe declarar el guard mock ahí mismo — no dejarlo para un retrofit
posterior vía Executor forzado (mismo criterio ya aplicado a
`interfaz`/`depende_de` en items "ensambladores"). Patrón completo (guard
`if get_settings().mockup:`, eco de identificadores de entrada, usuarios
de prueba fijos para login) en `.agents/rules/local-development.md`,
sección "Modo mock". El `riesgo_heredado` correspondiente ("nadie probó
contra el sistema real") sigue existiendo igual — el modo mock resuelve
navegación/pruebas de UI, no reemplaza probar contra el sistema real.

## Tracking de estado en tiempo de ejecución

`plan.json` no se toca después de escrito (ver arriba). El estado efectivo de
cada item se reconstruye combinando `plan.json` con dos artefactos separados,
cada uno escrito por el agente que tiene permiso para hacerlo:

### `.harness/logs/executor.jsonl` — bitácora de Executor (append-only)

Un objeto JSON por línea. Executor únicamente **agrega** líneas, nunca edita
ni borra las anteriores — es el permiso de `write` sobre `harness_logs` bien
usado: agregar, no reescribir el historial.

```json
{"item_id": "PED-001", "evento": "iniciado", "timestamp": "2026-08-19T15:00:00-03:00", "detalle": "generando backend/app/api/v1/auth.py"}
{"item_id": "PED-001", "evento": "finalizado", "timestamp": "2026-08-19T15:04:00-03:00", "detalle": "archivos escritos, listo para validar"}
```

`evento` es `"iniciado"` | `"finalizado"` | `"bloqueado"`. Un `"bloqueado"`
lleva `detalle` explicando por qué (dependencia no completada, ambigüedad no
resuelta en `detalle_tecnico`, etc.) — Executor no debe improvisar una
solución fuera de lo que dice el plan.

### `.harness/validation/<item_id>.json` — veredicto de Compliance

Un archivo por item, con el **último** veredicto (Compliance lo sobreescribe
en cada reevaluación — a diferencia del log de Executor, acá no hace falta
historial completo, el veredicto vigente es el que importa).

```json
{
  "item_id": "PED-001",
  "veredicto": "aprobado",
  "timestamp": "2026-08-19T15:10:00-03:00",
  "criterios_evaluados": [
    { "criterio": "POST /api/v1/auth/login con credenciales válidas devuelve 200 y un accessToken JWT decodificable", "cumplido": true },
    { "criterio": "El password se compara contra un hash almacenado, no en texto plano", "cumplido": true }
  ],
  "detalle": ""
}
```

`veredicto` es `"aprobado"` | `"rechazado"`.

### Estado efectivo (calculado, no almacenado en un solo lugar)

Para saber el estado de un item en un momento dado:

1. Si `.harness/validation/<item_id>.json` existe y `veredicto == "aprobado"`
   → **completado**.
2. Si existe y `veredicto == "rechazado"` → **rechazado** (puede volver a
   tomarlo Executor, que agrega un nuevo `"iniciado"` al log — no hace falta
   tocar `plan.json` para reintentar).
3. Si no hay veredicto pero el último evento en `executor.jsonl` para ese
   `item_id` es `"bloqueado"` → **bloqueado**.
4. Si el último evento es `"iniciado"` sin `"finalizado"` posterior →
   **en_progreso**.
5. Si no hay ningún evento para ese `item_id` → **pendiente** (el valor que
   dejó el Planner en `plan.json`), salvo que el Planner lo haya marcado
   `"omitido"`.

Esto queda como responsabilidad de quien lea el estado (un script de status,
o el propio Compliance antes de evaluar) — no requiere que ningún agente
escriba fuera de su directorio permitido.

## Interfaz real reportada por Executor

`interfaz` en `plan.json` es una **predicción** que el Planner escribe antes
de que el código exista. En la práctica puede quedar incompleta — evidencia
real: en una migración real, un item necesitaba `DomainError` (citado
solo en prosa en `detalle_tecnico`, sin `import` literal en ninguna
`interfaz` alcanzable) y el modelo adivinó un módulo plausible pero
inexistente (ver más arriba, "todos los items que necesitaban
`get_current_user`... lo importaron bien; los que necesitaban
`DomainError`... lo importaron mal").

Para cerrar ese gap, Executor también reporta, en la misma llamada donde
genera el código (no una llamada aparte — pedirle que describa lo que
acaba de escribir es mucho más confiable que pedirle que adivine código
nuevo), qué símbolos quedaron pensados para que otro item los reuse. Ver
`agents/executor.py`, bloque `### INTERFAZ` en el formato de salida.

- **Dónde queda:** `.harness/interfaces/<item_id>.json`, un archivo por
  item, **sobreescrito completo** en cada regeneración (mismo motivo que
  `validation/<item_id>.json`: nunca debe describir una versión vieja del
  código).
- **Best-effort, no bloqueante.** Si el modelo omite el bloque o lo manda
  mal formado, el item no se rechaza por eso — los archivos ya están bien.
  Simplemente no queda interfaz real registrada esta vez.
- **Precedencia al armar contexto de una dependencia (`agents/executor.py`
  y `agents/compliance.py`, vía `interfaz_real.py`):** unión por `import`
  entre la interfaz predicha (`plan.json`) y la real, con la real ganando
  en caso de conflicto (viene del código de verdad). Lo que solo está en la
  predicha se conserva — Executor no tiene por qué haberlo vuelto a
  reportar. Lo que solo está en la real se agrega.
- **Poda previa contra el código real (`interfaz_real.py::podar_predicha_no_generada`,
  2026-08-26):** antes de esa unión, se descarta de la predicha cualquier
  entrada cuyo `nombre` NO aparece definido en el código realmente
  generado por el item — la unión por `import` no detecta que dos entradas
  describen el mismo rol bajo nombres distintos, así que una predicción
  vieja podía convivir para siempre al lado de la real. Motivó esto: un
  router falso predicho que Executor nunca implementó bajo ese nombre
  sobrevivía igual en la unión, quemando 3 reintentos + 1 escalado a
  `executor_senior` (ver `handoff.md`). La
  poda es contra el código, no contra "si la real lo menciona" — un símbolo
  real que la interfaz real no vuelve a mencionar explícitamente sigue
  sobreviviendo igual, la poda no lo afecta.
- **`plan.json` sigue inmutable.** Esto vive aparte, nunca se escribe
  encima de lo que dejó el Planner.

## Contexto ampliado de Compliance (infraestructura + dependencias reales)

**Contexto (2026-08-20):** en la práctica, varios rechazos de Compliance
resultaron ser falsos negativos por "ceguera de contexto" — el modelo no
tenía forma de verificar algo porque el archivo relevante no estaba en
`archivos` del item (ej. no podía confirmar que `INVALID_CREDENTIALS`
mapea a 401 porque nunca veía `exception_handlers.py`, ni que el prefijo
de un router ensamblaba bien porque nunca veía el router real). Esto pasó
3 veces el mismo día con la misma causa raíz — dejó de tratarse caso por
caso a mano y se resolvió en `agents/compliance.py`:

- **`archivos_infraestructura_compartida`**: contenido completo de los
  `archivos_destino` de todo item con `ticket_id: null` — la misma marca
  que el Planner ya usa para "esto es infraestructura, no negocio" (ver
  ejemplos de items de configuración base más arriba). Siempre visible para Compliance, sin importar
  qué item se esté validando ni si está en su `depende_de` — infraestructura
  compartida se trata como ambiente, no como dependencia declarada
  item por item (declararla en cada item sería absurdamente repetitivo).
- **`archivos_reales_de_dependencias`**: contenido completo (no solo
  `interfaz`) de los items en `depende_de` del item en validación. Pensado
  sobre todo para items "ensambladores" (ej. un `main.py` que monta todos
  los routers, que depende de todo lo que monta) — su `interfaz` nunca fue diseñada para alcanzar
  a verificar el contenido real de una docena de dependencias.
- **`arbol_archivos_proyecto`**: listado (solo rutas, sin contenido) de
  todo el proyecto — orienta sobre qué existe, no reemplaza lo de arriba.
  Excluye `node_modules/`, `venv/`, `.git/`, `dist/`, `.angular/`,
  `__pycache__/` y `.harness/` (ver `_IGNORAR_DIRS` en
  `access_control.py` — la primera versión de esto devolvía ~25.000
  archivos de `node_modules/` en un proyecto con frontend real, inútil).
- **Solo para Compliance, no para Executor.** El contexto acotado de
  Executor sigue intacto a propósito (ver "Principio general" y
  "Interfaz real reportada por Executor" arriba) — agrandarlo tiene el
  riesgo real de que empiece a copiar/confundirse entre items al generar
  código. Compliance solo lee y opina, un contexto más grande ahí es
  más seguro.

**Evidencia de que funcionó:** re-validando un item ensamblador real con este
contexto, Compliance verificó correctamente 3 de 4 rutas citando el router
real y su prefijo exacto — algo que antes rechazaba sin poder confirmar.
El único rechazo restante ese día fue un error aritmético del modelo (dijo
que el router de facturas duplicaba `/api/v1` cuando en realidad
`""` + `"/api/v1"` + `"/facturas"` da la ruta correcta sin duplicar,
verificado leyendo el archivo) — un error de razonamiento con el contexto
correcto ya disponible, no de contexto faltante. Señal relevante para la
pregunta de si vale la pena pasar Compliance a `deepseek-reasoner`: la
causa dominante de rechazos falsos (ceguera de contexto) ya no existe;
lo que queda es la clase de error que sí podría mejorar con más
razonamiento, pero es la minoría de los casos vistos hasta ahora — no
justifica el cambio todavía por sí sola.

### `chequeos_deterministicos_previos` — lo que ya se verificó mecánicamente, no algo a re-derivar

**Contexto (2026-08-23):** un rechazo falso más, de otra causa — Compliance
rechazó un item frontend por un único criterio ("el proyecto
compila con ng build") con el motivo "no tengo evidencia, no doy el
beneficio de la duda", pese a que `frontend_check.py` (el `ng build` real)
YA había corrido y pasado automáticamente antes de que Compliance se
invocara siquiera — es un paso obligatorio previo, ver "Frontend check" y
"Smoke test" arriba. Compliance simplemente no tenía forma de saber que ese
hecho ya estaba confirmado: no es ceguera de contexto sobre el código del
proyecto (lo de arriba), es ceguera sobre el propio pipeline del harness.

`construir_contexto` (`agents/compliance.py`) agrega `chequeos_
deterministicos_previos`: una frase fija según `tipo`/`tests_requeridos`
del item, diciéndole a Compliance que el chequeo mecánico correspondiente
(frontend → `ng build`; backend con `tests_requeridos` → smoke test;
backend sin tests → format check) ya corrió y pasó. El `SYSTEM_PROMPT` lo
trata como hecho, no como algo a re-verificar leyendo código — cualquier
criterio textual de "compila"/"los tests pasan" va `cumplido=true` directo.
Mismo principio que la sección de arriba (Compliance necesita más contexto
que Executor, nunca menos) pero aplicado al ESTADO del pipeline, no solo al
código del proyecto.

## Ticket de reintento — feedback de un rechazo persistido a archivo, no recalculado en memoria

**Contexto (2026-08-30):** el feedback de un reintento
(`_construir_feedback_reintento` + `_adjuntar_codigo_actual`, ver
`orchestrator.py`) se recalcula desde cero en cada llamada y el historial
de rechazos de un item vive solo en memoria dentro de `loop()`
(`historial_feedback`, un dict local) — si el proceso se corta a mitad de
una corrida (ya documentado varias veces, ver `handoff.md`), ese historial
se pierde con él y `executor_senior` termina viendo solo lo que sobrevivió
desde el último reinicio. Además, en el caso de oscilación más severo visto
hasta ahora (ver `handoff.md`), el historial en
prosa no bastó — hizo falta que un humano armara a mano una tabla de
hechos verificados contra el código real para que `executor_senior`
convergiera al primer intento. Esa tabla nunca tuvo un lugar formal donde
vivir: quedó en el historial de la conversación de esa sesión, no en el
harness.

**Decisión:** el feedback de reintento deja de ser una cadena calculada en
memoria y pasa a ser un archivo persistente,
`.harness/logs/tickets/<item_id>.md`, que el orquestador actualiza
determinísticamente en cada rechazo elegible para reintento (Compliance, o
el rechazo sintético de `format_check`/`smoke_test`/`docker_check`) y que
Executor/`executor_senior` reciben tal cual como `feedback` — mismo
mecanismo de siempre (`construir_prompt_usuario`, `agents/executor.py`),
solo que la fuente ahora es un archivo en vez de una función que recalcula
todo de cero cada vez. **No aplica al camino de `arbitro`/`bloqueado`**
(`_ejecutar_con_arbitraje`), que sigue igual — ahí el problema es "falta
información en el plan", no "el reintento oscila entre dos fixes ya
vistos".

**Formato del ticket** (secciones fijas, en este orden):

```markdown
# Ticket de reintento — <item_id>

## Lo esperado
(criterios_aceptacion completos del item, de plan.json — se escribe una
sola vez al crear el ticket; si el Planner edita el item más adelante,
esta sección se re-escribe completa, el resto no se toca)

## Hechos verificados
(vacío al crear el archivo. Población manual — Analyzer entiende el
problema, Planner arma el plan de trabajo; hoy Felipe+Claude, más
adelante vía API de Anthropic, sin cambiar el resto de este diseño.
Nombres/tipos/claves exactas verificadas contra el código real, no prosa
general. El orquestador NUNCA escribe ni borra esta sección después de
crearla vacía.)

## Historial de intentos
### Intento 1 — 2026-08-30T10:00:00-03:00 (fuente: compliance)
- NO CUMPLIDO: <criterio> — motivo: <detalle>

### Intento 2 — 2026-08-30T10:15:00-03:00 (fuente: smoke_test)
- <salida relevante del chequeo>

## Código actual (después del último intento)
### backend/app/algo.py
\```python
...
\```
```

- **"Lo esperado" es la única exposición deliberada de `criterios_aceptacion`
  a Executor.** Contradice, a propósito, el "contexto mínimo" de siempre
  (`agents/executor.py::construir_contexto` nunca le pasa
  `criterios_aceptacion` — eso es privativo de Compliance, ver "Principio
  general"): en un reintento, Executor necesita ver TODOS los criterios (no
  solo el que Compliance marcó `NO CUMPLIDO`) para no arreglar uno rompiendo
  otro que ya cumplía — exactamente el patrón de oscilación que motiva este
  diseño. Solo aplica dentro del ticket (retry), nunca en el primer intento.
- **No se repite la interfaz de dependencias ni `detalle_tecnico`** — eso ya
  viaja en cada llamada como parte del contexto base
  (`construir_prompt_usuario`), con o sin ticket; duplicarlo en el ticket
  sería el mismo texto dos veces.
- **"Historial de intentos" acumula** (append, nunca se borra) — un bloque
  por rechazo, con la fuente exacta (`compliance` | `format_check` |
  `smoke_test` | `docker_check`) y el detalle. Mismo criterio que
  `executor.jsonl`: bitácora append-only, la verdad está en la secuencia
  completa, no en el último evento.
- **"Código actual" se sobreescribe completo** en cada actualización —
  igual que `.harness/interfaces/<item_id>.json`, describir código viejo no
  sirve nunca.
- **"Hechos verificados" es el único campo que el orquestador no toca
  después de crearlo.** Población manual, opcional — un caso simple puede
  quedar con la sección vacía y el ticket igual sirve (el resto ya es
  estrictamente más información que el feedback de hoy).

**Dónde vive:** `.harness/logs/tickets/<item_id>.md` — bajo `logs/`, no un
directorio nuevo, para heredar el `.gitignore` que ya excluye `logs/` sin
tocar `.harness/.gitignore`. Un archivo por item (no por intento) — se
sobreescriben/acumulan secciones dentro del mismo archivo, igual que
`.harness/validation/<item_id>.json` es un archivo por item, no un archivo
por veredicto.

**Quién escribe qué:**

| Sección | Quién | Cuándo |
|---|---|---|
| Lo esperado | orquestador (determinístico) | al crear el ticket, y de nuevo si `criterios_aceptacion` cambió |
| Historial de intentos | orquestador (determinístico) | en cada rechazo elegible para reintento |
| Código actual | orquestador (determinístico) | en cada rechazo elegible para reintento |
| Hechos verificados | Analyzer+Planner (hoy Felipe+Claude, no automatizado) | a demanda, antes de un reintento que lo amerite |

**Gate de decisión (`_preguntar_decision_reintento`) — se mantiene aparte
del ticket, cambian los significados de las opciones porque el ticket ya
existe siempre cuando el gate se dispara:**

- `[r]` reintentar con el ticket tal cual (antes `[s]` "seguir").
- `[e]` pausar el loop para completar "Hechos verificados" a mano en el
  archivo antes de reintentar (reemplaza `[t]`, que hoy pide la solución
  por `input()` en la terminal en vez de en el archivo persistente).
- `[m]` resolver el código a mano, excluir del loop (sin cambios).
- `[n]` detener el loop (sin cambios).

Con `--sin-confirmar`, se comporta como `[r]` siempre — el ticket ya trae
todo lo determinístico, "Hechos verificados" puede quedar vacío sin que
nada se rompa (mismo criterio que ya rige `--sin-confirmar` en el resto de
`loop()`).

**Escalada a `executor_senior`:** deja de reconstruir el feedback desde
`historial_feedback` (dict en memoria, se pierde si el proceso se corta) —
lee el mismo `.harness/logs/tickets/<item_id>.md`, que para este punto ya
tiene el historial completo de todos los intentos previos. Cierra el gap
real ya documentado en `handoff.md` ("Ticket de reintento — feedback
persistido a archivo"): un corte de proceso a mitad de corrida ya no pierde
el historial de rechazos, sigue en disco.

**Qué NO cambia:** `reporte_fallas.md` sigue existiendo (bitácora legible
de alto nivel, agregada entre todos los items), pero en vez de embeber el
feedback completo pasa a referenciar la ruta del ticket
(`.harness/logs/tickets/<item_id>.md`) — evita mantener el mismo contenido
en dos lugares. `decisiones_reintento.jsonl` sigue igual. El camino de
`arbitro`/`bloqueado` no se toca.

## Catálogo de endpoints (post-proceso determinístico, no un agente)

Al final de cada item backend aprobado, se necesita un archivo dentro del
proyecto que liste los endpoints expuestos hasta el momento — pensado como
documentación de referencia del backend que se está armando, no como algo
que un agente deba redactar.

- **No es un agente ni pasa por el modelo local.** Todo el contenido ya
  existe estructurado en `interfaz.endpoint` de cada item — generarlo es
  extracción y formateo mecánico, no razonamiento. Meterlo como un quinto
  agente sería gastar tokens en algo que no necesita un LLM.
- **Quién lo ejecuta:** el harness mismo (el driver que orquesta a los
  agentes), no uno de los cuatro agentes de `access_control.py`. No está
  sujeto a la tabla de permisos por agente porque no es un agente — es
  lógica determinística del propio harness, en la misma categoría que
  Format check / Smoke test (ver `handoff.md`).
- **Trigger:** se regenera cada vez que Compliance escribe un veredicto
  `"aprobado"` para un item con `tipo: "backend"` en
  `.harness/validation/<item_id>.json`.
- **Fuente de datos:** `interfaz.endpoint` de todo item `tipo: "backend"`
  cuyo veredicto vigente sea `"aprobado"`. No lee `detalle_tecnico`,
  `criterios_aceptacion` ni código del monolito de origen — mismo principio
  de contexto mínimo que aplica a Executor.
- **Archivo:** `docs/api-endpoints.md`, dentro del proyecto destino (no en
  `.harness/`, porque es documentación del producto, no metadata del
  harness). Se **reescribe completo** en cada regeneración (no append), para
  que nunca arrastre un endpoint que fue rechazado o quedó desactualizado.
- **Formato:** una sección por endpoint con método, ruta, request, response
  y el `id` del item de origen (trazabilidad hacia `plan.json`). Ver
  `schemas/api-endpoints.example.md` para un ejemplo completo aplicado al
  fixture de pedidos.

## Candidatos de conocimiento (agente Documentador, propone — no escribe conocimiento directo)

A diferencia del catálogo de endpoints (arriba), esto sí es un agente real
(quinto agente, `agents/documentador.py`) — la tarea (clasificar un
rechazo+resolución real y redactar un resumen) es de lectura/juicio, no
extracción mecánica, así que sí justifica pasar por el modelo. Corre en
motor **local** (`config/models.yaml`, agente `documentador`) — a
diferencia de Compliance/arbitro, no necesita el razonamiento más fuerte de
DeepSeek: analiza un error y un fix que ya pasaron de verdad por este
proyecto (no investiga, no recuerda de memoria de entrenamiento).

- **Trigger:** un item pasa a `"aprobado"` (dentro de
  `orchestrator.py::validar_con_format_check`) **y** tiene al menos un
  bloque propio en `.harness/logs/reporte_fallas.md` (helper
  `_item_tuvo_rechazos`). Un item aprobado a la primera no dispara nada — no
  hay error real que documentar. Ver `Pendientes.md`/`handoff.md` para el
  razonamiento completo de por qué este disparador y no "al fallar" ni "al
  cerrar el proyecto".
- **Fuente de datos:** los bloques de `reporte_fallas.md` que pertenecen a
  ese `item_id` + el ticket de reintento completo del item si existe
  (`.harness/logs/tickets/<item_id>.md`, ver "Ticket de reintento" arriba —
  desde que `reporte_fallas.md` solo referencia la ruta del ticket en vez
  de embeber el feedback completo, el detalle real de una oscilación vive
  ahí, no en el bloque) + el código final ya aprobado de `archivos_destino`
  (la resolución real, ya verificada por Compliance/tests) +
  `criterios_aceptacion`/`detalle_tecnico` del item.
- **Nunca escribe en `knowledge/` ni en `.agents/rules/`.** Deja su
  propuesta en `.harness/logs/candidatos_conocimiento.md` (append-only,
  mismo patrón que `reporte_fallas.md`), clasificada en
  `patron_libreria` / `decision_arquitectura` / `bug_negocio_proyecto`
  (mismo criterio que ya usa `knowledge/README.md`). El Planner revisa y
  decide qué se vuelca de verdad — mismo gate humano que usa `--loop` para
  reintentos.
- **Honestidad sobre la fuente:** el candidato dice explícitamente que está
  "confirmado en código real de este proyecto", nunca "verificado contra
  documentación oficial" — el agente no tiene forma de chequear la doc real
  (mismo bloqueo que sigue abierto en `Pendientes.md`, "Agente investigador
  de tecnologías"). Es al humano que revisa a quien le toca esa
  verificación antes de dar el candidato por bueno en otro proyecto.
- **Nunca bloquea el pipeline.** Un fallo del motor o una respuesta mal
  formada se loguea y se ignora — el veredicto real del item (ya
  `"aprobado"`) no cambia.

## Documentos de proyecto que crecen durante la migración — nunca un item de `plan.json`

**Contexto (2026-08-30):** un proyecto real modeló
`fixAplicados.md`/`recomendaciones-tecnicas.md` como items normales —
Executor los generaba una sola vez, con el contenido completo ya decidido
de antemano en `detalle_tecnico`. Error real: la intención de Felipe era
que fueran archivos que se completan **a medida que aparecen hallazgos
reales durante la migración** (un fix de comportamiento aplicado, un
riesgo detectado), no un entregable cerrado en una sola pasada. El mismo
patrón ya existía sin formalizar — `deuda_negocio.md` en un proyecto
anterior (decisiones de negocio no técnicas, en lenguaje
simple, para que el cliente decida) nunca se escribió acá, y por eso el
patrón se perdió y se reinventó mal en el proyecto siguiente.

**Regla:** un documento de proyecto (vive en el repo destino, no en
`.harness/`) cuyo contenido se espera que **crezca con hallazgos nuevos a
lo largo de la migración** — no algo que se pueda escribir completo de una
vez porque su contenido depende de lo que se vaya encontrando — nunca es
un item de `plan.json`. Se crea directo (fuera del ciclo Executor/
Compliance) con lo que ya se sepa al momento como semilla, y se sigue
completando a mano por el Planner (hoy Felipe+Claude) cuando corresponda —
append, nunca reescribir lo que ya hay. Ejemplos conocidos: `deuda_negocio.md`
(decisiones de negocio no técnicas), `fixAplicados.md` (comportamientos del
origen corregidos a propósito, no preservados), `recomendaciones-tecnicas.md`
(riesgos/observaciones abiertos para Arquitectura/Seguridad).

**Cómo distinguir esto de un item real:** si el contenido completo ya se
conoce al escribir el plan (ej. "estos 3 fixes ya decidimos aplicarlos"),
es tentador convertirlo en un item con ese contenido literal — es
exactamente el error que motivó esta sección. La pregunta correcta no es
"¿ya sé qué va a decir?" sino "¿este archivo va a seguir recibiendo
entradas nuevas después de que el plan esté escrito, a medida que se
ejecutan otros items?" — si la respuesta es sí, no es un item.

**Instrucción fija desde ahora (2026-08-30, pedido explícito de Felipe):**
todo proyecto nuevo arranca con `<deployable>/docs/fixAplicados.md` y
`<deployable>/docs/recomendaciones-tecnicas.md` creados desde el inicio de
la planificación — mismo directorio y mismos dos nombres ya usados en un
proyecto real (`dal/docs/`), no una convención a
inventar de nuevo por proyecto. `<deployable>` es la carpeta del deployable
relevante (`dal/`, `backend/`, o la raíz del proyecto si es un solo
deployable). Crearlos vacíos (con el encabezado + la nota de "se completa
a medida que aparecen hallazgos", igual que las plantillas de este proyecto)
es válido si todavía no hay ningún hallazgo al momento de planificar —
no hace falta esperar a tener contenido real para crear el archivo.
`deuda_negocio.md` (decisiones de negocio, no técnicas) sigue en la raíz
del proyecto, no en `docs/` — es para el cliente, no para quien lee la
documentación técnica del deployable.

## Reglas de validación

Implementadas en `plan_validator.py` (determinístico, sin LLM) y corridas
automáticamente al cargar el plan (`orchestrator._cargar_plan`) — un
`plan.json` que viole alguna de estas reglas hace que el orquestador falle
antes de llamar a Executor/Compliance, no llega a ejecutarse.

- Todo `id` en `items[]` debe ser único.
- Todo `id` referenciado en `depende_de` debe existir en `items[]`.
- No puede haber dependencias circulares.
- `archivos_destino` no puede estar vacío.
- `criterios_aceptacion` no puede estar vacío.
- Si un item aparece en el `depende_de` de otro, su `interfaz` no puede estar
  vacía — si nada expone, no debería ser una dependencia declarada.
- **Ningún archivo puede aparecer en `archivos_destino` de más de un item.**
  Executor genera cada item sin ver el contenido actual de sus archivos si ya
  existieran — no hay mecanismo para fusionar lo que dos items escriben en el
  mismo archivo, el segundo simplemente pisaría al primero. Si dos items
  necesitan tocar la misma pieza de código, hay que dividir en archivos
  separados o repensar la granularidad — no compartir destino. Un item SÍ
  puede *importar* código de un archivo que otro item (una dependencia) ya
  generó, eso es reuso normal, distinto de escribir sobre el mismo archivo.
  Encontrado en la práctica en una migración real — ver `handoff.md`.

## Ver también

- `schemas/plan.example.json` — ejemplo completo aplicado al fixture de
  pedidos (login, listar pedidos, crear pedido).
