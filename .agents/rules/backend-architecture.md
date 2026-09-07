---
description: Patrón de capas para backends Python/FastAPI — routers, service, repository, model. Aplicar al generar o modificar cualquier endpoint o lógica de negocio en backend.
activation: always_on
---

# Arquitectura de capas — Backend (FastAPI)

## Flujo de una request

```
router (app/api/) → service (app/service/) → repository (app/repository/) → model (app/model/)
```

- **Router**: define endpoints, inyecta la sesión de DB vía `Depends`, instancia el
  service y delega. No contiene lógica de negocio ni acceso directo a datos.
- **Service**: recibe el schema de entrada, arma el modelo, llama al repository, valida
  y arma la respuesta con el schema de salida.
- **Repository**: acceso a datos. Métodos por entidad, recibe `db_session` como
  argumento explícito. Traduce errores de base de datos (ej. `IntegrityError`) a
  excepciones de dominio propias.
- **Model**: definición SQLAlchemy de la tabla (estilo `Mapped`/`mapped_column`),
  constraints incluidos.

Esto es el default: **un solo deployable backend**, con `repository/` como capa de
acceso a datos dentro de él (front→backend, DAL = un layer, no un servicio propio).
Aplica salvo que `plan.json` declare explícitamente lo contrario — ver siguiente
sección.

## Backend y DAL como deployables separados (front→backend→DAL)

Cuando `metadata.arquitectura_objetivo` de `plan.json` declara `backend` y `dal`
como carpetas/deployables distintos (cada uno con su propio `venv`, sin import
directo entre ellos — comunicación por red), las capas se reparten así:

```
front → backend (api/ → service/ → client/ → schema/) → [red] → dal (api/ → repository/ → model/ → schema/)
```

- **`backend/`**: `api/` (routers) + `service/` (lógica de negocio real, no se
  mueve) + `client/` (llama al DAL por HTTP — mismo rol que `repository/` en la
  topología de un solo deployable, pero implementado como request de red, no
  query directa) + `schema/` (DTOs de la API del backend). **Sin `repository/`,
  sin `model/`** — no tiene credenciales de base de datos.
- **`dal/`**: `api/` (endpoints internos, no expuestos al frontend) + `repository/`
  + `model/` + `schema/` (DTOs propios del DAL, no reusar los del backend). Único
  deployable con acceso real a la base de datos.

El `service` del backend sigue siendo dueño de la lógica de negocio y de decidir
cuándo confirmar una operación — como el DAL es un servicio de red aparte, no hay
transacción compartida cruzando el límite HTTP; una operación que necesite ser
atómica across varias escrituras se resuelve *dentro* del DAL (un solo endpoint
que hace todas las escrituras en una transacción), nunca orquestando varios
llamados desde `backend/client/` con la expectativa de que se comporten como una
transacción.

**Lecturas con loop/N+1 (no solo escrituras atómicas) — mismo criterio: se
resuelven dentro del DAL, no round-trip por red por iteración.** Si el
`detalle_tecnico` de un item de `service` en el origen necesita varias queries
en loop (ej. una query por fila de un resultado anterior para enriquecerla, o
una query por mes/período en un rango) — patrón real encontrado migrando un
backend con reportes agregados: notas de crédito por entidad en un reporte
diario, y hasta 24 meses de sumas en un cálculo de saldo — el DAL
expone un endpoint compuesto que hace esas N queries (o el `JOIN`/`GROUP BY`
equivalente) en su propio proceso, y devuelve el resultado ya armado en una
sola respuesta. El `service` del backend sigue siendo quien decide *qué*
pedir y *cómo* usar el resultado (eso no se mueve); lo que cambia de lugar es
solo la composición de queries repetitivas, para no convertir N llamadas
in-process baratas en N round-trips de red caras. Esto no es "lógica de
negocio en el DAL" en el sentido que prohíbe la regla de arriba — es
agregación de datos, no una decisión de negocio (la decisión de negocio de
qué hacer con el saldo consolidado sigue en `service`). El Planner declara
este tipo de endpoint compuesto explícito en `detalle_tecnico`/`interfaz` del
item del DAL, con la forma de respuesta exacta (mismo criterio que ya rige
para `RowSchema`, nunca `dict`) — no queda a criterio del Executor decidir
si compone o no.

Cada item de `plan.json` de este tipo sigue siendo `tipo: "backend"` (`dal/` no es
un tipo nuevo) — la carpeta en `archivos_destino` es lo único que distingue a qué
deployable pertenece; `checks/smoke_test.py` y `checks/generate_api_docs.py` ya
derivan venv/carpeta de trabajo del propio item, no asumen `backend/` fijo.

## Retorno de queries con columnas explícitas (no la entidad completa) — siempre un row schema Pydantic, nunca `dict`

Cuando un método de `repository` selecciona columnas puntuales en vez de una entidad
completa (típicamente un `JOIN` o una agregación, donde el consumidor no necesita el
modelo entero), el retorno es **`list[RowSchema]`** — un `BaseModel` de Pydantic
definido junto al repository (no un `Response` de la API, no reusar entre queries con
columnas distintas), nunca `list[dict]`:

```python
from decimal import Decimal

class VentaDiariaRow(BaseModel):
    vca_loc: int
    vca_glosa: str
    vca_fec: int
    vca_monto: Decimal  # numeric(19,4) real en el DDL

def obtener_ventas_diarias(db: Session, ...) -> list[VentaDiariaRow]:
    stmt = select(...).join(...)
    return [VentaDiariaRow.model_validate(row) for row in db.execute(stmt).mappings().all()]
```

Un `dict` no es un contrato verificable: sus claves solo existen implícitas en el
`select()` que las genera, así que el `service` consumidor termina adivinándolas —
causó bugs reales repetidos (`KeyError` en runtime, invisibles para linters/format
check) en más de una migración. El nombre de la clase (`VentaDiariaRow`) sí es un
símbolo declarable en la `interfaz` del item (import + campos), así que el service
que depende de este repository lo importa en vez de adivinar. Ver
`Harness/knowledge/sqlalchemy-2.0.md` para el detalle completo y el patrón anterior
ya descartado.

**El tipo de cada campo es el de su columna real, columna por columna — nunca
un tipo único generalizado por el nombre.** `vca_monto` (`numeric`, tabla
agregada) y `vca_mto` (`int4`, tabla detalle) son columnas distintas con
nombres parecidos y tipos distintos, incluso en el mismo proyecto. Verificar
`numeric(p,s)` → `Decimal`, `int2`/`int4`/`int8` → `int` contra el DDL real
para CADA campo, sin asumir por similitud con otro campo ya tipado. Ver
`Harness/knowledge/sqlalchemy-2.0.md` para el caso real donde generalizar en
cualquiera de los dos sentidos produjo el mismo bug.

## Tablas sin key natural (legado) — SQLAlchemy Core, no ORM declarativo

Cuando la tabla real (verificada contra el script/DDL de origen, ver
`schemas/plan.contract.md` sección `schema_bd_origen`) no tiene ninguna columna que
sirva de identidad real — típico de tablas legadas insert-only o de solo reporte
(logs de auditoría, detalle de ventas, saldos, depósitos) — el modelo va como
`sqlalchemy.Table` de **Core**, no como clase `Base` declarativa con
`Mapped`/`mapped_column`:

```python
from sqlalchemy import Table, Column, Integer, Numeric, MetaData

metadata = MetaData(schema="portal")

depositos = Table(
    "depositos", metadata,
    Column("ca_com", Integer, nullable=False),
    Column("ca_fec", Integer, nullable=False),
    Column("ca_monto", Numeric(19, 4), nullable=False),
)

def registrar_deposito(db: Session, com: int, fec: int, monto: Decimal) -> None:
    db.execute(depositos.insert().values(ca_com=com, ca_fec=fec, ca_monto=monto))
```

**Por qué, no por conveniencia:** el ORM declarativo exige `primary_key=True` en al
menos una columna para su identity map. Si no hay clave real, la alternativa (marcar
todas las columnas `NOT NULL` como PK compuesta) tiene un riesgo real de colisión
silenciosa: dos filas legítimas con el mismo valor en todas las columnas (ej. mismo
comercio, mismo día, mismo monto redondo depositado dos veces) se hidratarían como
el mismo objeto Python dentro de una sesión, sin error. Core no tiene ese problema
porque no mantiene identidad de objeto — es una capa más baja de la misma librería,
mismo `Engine`/`Session`, sin cambio de dependencia ni de conector, y **no implica
ninguna migración de la base real**: el `Table` de Core solo describe en Python
columnas que ya existen, nunca se le pide crear ni alterar nada
(`metadata.create_all()` no se llama sobre estas tablas).

**Cuándo sí usar ORM declarativo:** cuando la tabla real tiene una identidad natural
verificable contra el schema real (ej. `usadm.ca_ip`, `locales.vca_com`+`vca_loc`
compuesta) — ahí el patrón `Mapped`/`mapped_column` de siempre sigue siendo el
correcto.

**Verificar identidad significa verificar MULTIPLICIDAD real, no solo que la columna
"suene" a identidad (bug real, 2026-08-26, migración con backend y DAL
separados):** esta misma sección citaba antes `usuarios.ca_rut` como ejemplo de
identidad natural verificada — estaba mal. `ca_rut` parece la identidad obvia de un
usuario, pero la tabla real tiene UNA FILA POR (rut, comercio) — un usuario con
acceso a varios comercios tiene varias filas con el mismo `ca_rut`. Declarar
`ca_rut` solo como `primary_key=True` no rompe en `CREATE TABLE` ni en un `INSERT`
(la tabla real no tiene PK), pero sí rompe en silencio en tiempo de ejecución: el
identity map de SQLAlchemy trata dos filas con el mismo valor de PK declarado como
LA MISMA entidad, así que un `select(Usuario).where(ca_rut==rut).scalars().all()`
con 2 filas reales en la BD devuelve solo 1 objeto (la primera cargada) — sin
excepción, sin warning, el error queda enterrado en un dato de negocio incorrecto
(ver `Harness/docs/handoff.md`, caso de un item de autenticación con multi-comercio).
Antes de declarar
`primary_key=True` en una columna (sola o compuesta) por ser "la identidad lógica"
de la entidad, hay que confirmar que ESA combinación de columnas es realmente única
por fila en el uso real de la tabla — mirando cómo el propio dominio consulta esa
tabla (¿existe algún código, del origen o del plan, que espere una LISTA de filas
para el mismo valor de esa columna? si existe, esa columna sola no es la PK). En
`usuarios`, la PK real es la compuesta `(ca_rut, ca_cod)` — una fila por persona
Y comercio.

## Casing de JSON (camelCase) — aplica a schemas de request Y de response, sin excepción

Cuando el frontend espera/envía JSON en camelCase y el código Python usa `snake_case`
(`naming-conventions.md`), **todo** schema Pydantic — de request y de response, sin
distinción — declara sus campos en `snake_case` real y usa:

```python
model_config = ConfigDict(
    alias_generator=pydantic.alias_generators.to_camel,
    populate_by_name=True,
)
```

`populate_by_name=True` deja que Pydantic valide el body de entrada tanto si llega por
su alias (camelCase) como por el nombre real; en response, FastAPI serializa con
`by_alias=True` por defecto, así que el JSON de salida queda en camelCase sin tocar nada
más. **El código Python (services, routers, tests) accede/construye SIEMPRE por el
nombre real en snake_case, nunca por el alias camelCase.**

> Nota para el agente: no declarar los campos de un schema de **request** literales en
> camelCase (`claveActual`, `codigoComercio`) "porque total el JSON ya llega así" — eso
> rompe la garantía de que TODO el código Python del proyecto es snake_case sin
> excepción, y un router que asuma (correctamente, por convención) que puede acceder
> `body.clave_actual` revienta con `AttributeError` real si el schema quedó en
> camelCase. Encontrado en la práctica: `autenticacion_request.py` de una migración
> definió sus campos en camelCase literal (sin alias) mientras los de response sí
> seguían el mecanismo de arriba — la inconsistencia entre ambos causó el bug, no una
> falla de un item puntual.

> Nota para el agente, lado consumo (no solo definición): un router que arma un
> objeto Pydantic desde `body` accede SIEMPRE por el nombre real en snake_case
> (`body.token_pre_auth`), nunca por el alias camelCase (`body.tokenPreAuth`) —
> el alias no existe como atributo Python del modelo, acceder por ahí revienta
> con `AttributeError` en runtime aunque el schema esté bien definido. Encontrado
> en la práctica en un item de autenticación real, 2026-08-24.

## Dónde vive el `commit()` — decisión fijada

**El `service` es responsable de hacer `commit()`. El `repository` solo hace
`add`/`flush`.**

Esto es una decisión explícita de Arquitectura, no un detalle menor: mantener el commit
en el service permite que una operación de negocio que toque múltiples repositories lo
haga como una sola transacción. Si el repository comitea internamente, esa composición
se vuelve imposible sin refactor.

> Nota para el agente: si encuentras un proyecto existente donde el repository hace el
> `commit()` (patrón heredado observado en prototipos internos), no lo repliques en
> código nuevo. Solo corrige el proyecto existente si se te pide explícitamente — no lo
> "arregles" silenciosamente como efecto secundario de otra tarea.

## Excepciones de dominio

- Cada entidad define sus propias excepciones (`class FacturaYaExisteError(Exception)`),
  no se usa `except Exception` genérico.
- El repository mapea errores de constraint de base de datos a estas excepciones de
  dominio usando el nombre del constraint, no el mensaje crudo del driver.
- Los routers traducen excepciones de dominio a códigos HTTP vía exception handlers
  centralizados (`app/core/exception_handlers.py`), no con `try/except` repetido en
  cada endpoint.

## Configuración

- `Settings` vía `pydantic-settings`, cacheada con `lru_cache()`.
- La función que construye `Settings` acepta un `env_path` opcional — esto permite que
  tests usen un `.env.pytest` separado del `.env` real sin duplicar lógica de carga de
  configuración. Mantener este mecanismo en vez de leer variables de entorno directo
  con `os.environ` en distintos puntos del código.

## Testing de integración con DB

- Tests de integración corren contra una base real, con **transacción + savepoint por
  test** (fixture de `conftest.py`) para rollback automático — evita tener que limpiar
  datos a mano entre tests. Este es el patrón estándar a replicar, no una alternativa
  entre varias.
- Configuración de test vía archivo de entorno separado (`.env.pytest`), nunca apuntando
  a la base de `dev`/`qa` real.

## Carpetas placeholder

Si un proyecto ya trae carpetas vacías con intención declarada (ej. `middleware/`,
`deploy/`, `tests/unit/`), el agente no debe eliminarlas ni asumir que "faltan" — son
estructura ya decidida a la espera de contenido futuro. Tampoco debe rellenarlas de
oficio con contenido de relleno.
