# SQLAlchemy 2.0

## `.with_only_columns(...)` con VARIAS columnas + `.scalar_one_or_none()`/`.scalars()` devuelve solo la PRIMERA columna, no la fila

**Verificado:** 2026-08-26, `sqlalchemy==2.0.35`, real (no mocks) — rompía
un login completo.

```python
db.execute(stmt).one_or_none()          # OK: Row con las columnas como atributos
db.execute(stmt).scalar_one_or_none()   # MAL: solo la 1a columna, valor suelto
```
`scalar_one_or_none()`/`.scalars()` son para un `select()` de una sola
columna/entidad. Con `with_only_columns()` de 2+ columnas, usar
`.one_or_none()`/`.first()`/`.all()`. El síntoma no menciona SQLAlchemy:
pasar el valor suelto a `Pydantic.model_validate()` tira
`Input should be a valid dictionary or object to extract fields from`.

**Encontrado en:** 2026-08-26, un DAL de autenticación.

## Declarar `primary_key=True` en una columna que no es única en el uso real de la tabla colapsa filas en silencio (identity map)

**Verificado:** 2026-08-26, `sqlalchemy==2.0.35` — `.all()` con 2 filas
reales para el mismo valor devolvía 1 sola, sin excepción.

Una tabla legada sin PK real a veces tiene una columna que "suena" a
identidad (ej. un RUT) pero no es única en el uso real (1 fila por comercio
de acceso, por ejemplo). Declararla `primary_key=True` no falla en
`INSERT`/`CREATE TABLE` — el identity map de SQLAlchemy trata las filas con
el mismo valor de PK como la misma entidad y devuelve solo la primera
cargada.

**Antes de declarar la PK:** verificar si algún código consulta esa
columna esperando una LISTA para el mismo valor — si sí, hace falta PK
compuesta.

**Encontrado en:** 2026-08-26 — ver también `backend-architecture.md`,
"Verificar identidad significa verificar multiplicidad real".

## El operador `/` sobre una columna de SQLAlchemy es SIEMPRE división real (cast a `NUMERIC`), nunca entera — usar `//`

**Verificado:** 2026-08-26, `sqlalchemy==2.0.35`, confirmado con el SQL
compilado.

```python
.where(tabla.c.fecha // 100 == anio_mes_int)   # OK: entera
.where(tabla.c.fecha / 100 == anio_mes_int)    # MAL: real, sin importar el tipo de la columna
```
`/` compila a `CAST(... AS NUMERIC)` — `20260810 / 100.0 = 202608.1`, nunca
igual al `202608` esperado. Sin excepción, la query solo devuelve vacío
con datos que sí calzaban.

**Encontrado en:** 2026-08-26, un repository de depósitos (3 ocurrencias,
alimentaba también un cálculo de saldo).

## `Base.metadata.create_all()` NO altera tablas que ya existen — solo crea las que faltan

**Verificado:** 2026-08-25, `sqlalchemy==2.0.35`.

**Contexto:** en proyectos sin Alembic/migraciones (arrancan
`Base.metadata.create_all(bind=engine)` al boot), es fácil asumir que
agregar una columna nueva a una entidad declarativa (`Mapped[str]` nuevo en
una clase `Base` existente) alcanza para que la próxima corrida de la app
la tenga disponible en la base real. **No es así**: `create_all()` compara
qué *tablas* faltan contra el `MetaData` y crea esas — nunca hace `ALTER
TABLE` sobre una tabla que ya existe, aunque su definición en Python haya
cambiado.

**Síntoma real:** código 100% correcto (el modelo, el schema Pydantic, el
service, todo alineado) rompe en runtime contra una tabla que ya tenía
filas, con un error que no menciona nada de Python:
```
sqlalchemy.exc.ProgrammingError: (psycopg2.errors.UndefinedColumn)
column test.proyecto does not exist
```
Esto pasa incluso reiniciando la app (`create_all()` corre de nuevo en cada
boot, sigue sin tocar la tabla existente) — no es un problema de caché ni de
reload.

**Patrón correcto (sin Alembic, cuando un item agrega una columna a una
entidad con filas reales):** después de que Compliance aprueba el item,
correr a mano un `ALTER TABLE` aditivo antes de probar contra la app real:
```sql
ALTER TABLE public.test ADD COLUMN proyecto character varying NOT NULL DEFAULT 'N/A';
ALTER TABLE public.test ADD COLUMN ambiente character varying NOT NULL DEFAULT 'N/A';
ALTER TABLE public.test ALTER COLUMN proyecto DROP DEFAULT;
ALTER TABLE public.test ALTER COLUMN ambiente DROP DEFAULT;
```
El `DEFAULT` temporal evita romper las filas ya existentes contra la nueva
columna `NOT NULL`; se saca después (`DROP DEFAULT`) para que el default no
quede aplicando de forma implícita a inserts futuros que sí deberían mandar
el valor real vía el ORM.

**Quién hace esto:** no es un paso que el harness pueda automatizar — ni
Executor ni Compliance tienen (ni deberían tener) acceso a la base real (ver
`schemas/plan.contract.md`, "el harness no instala/migra nada"). Queda
como responsabilidad manual del Planner/humano, DESPUÉS de que el item con
la columna nueva esté aprobado y ANTES de probar el flujo end-to-end contra
la app real — Compliance no lo puede atrapar porque valida código, no
estado de la base.

**Encontrado en:** 2026-08-25 — una entidad de pruebas ganó dos columnas
nuevas; el código generado y aprobado por Compliance rompió dos endpoints
contra la base real (con filas ya existentes) hasta correr el `ALTER TABLE`
de arriba. Ver también `docs/pendientes.md`, "Gestión de esquema de base de
datos con herramienta de migraciones formal" — este caso fue el disparador
de ese pendiente.

## Imports de entidades solo para que `Base.metadata` las registre — `ruff` los marca F401, y hace falta `# noqa` explícito

**Verificado:** 2026-08-24, `sqlalchemy==2.0.35` + `ruff` (el mismo que corre
`checks/format_check.py`).

**Contexto:** el patrón estándar de SQLAlchemy declarative para que
`Base.metadata.create_all()` sepa crear TODAS las tablas del proyecto es
importar cada clase `Entity` al menos una vez antes de llamar
`create_all()` — típicamente en `main.py`, agrupadas bajo un comentario tipo
`# DatabaseModels`:
```python
from model.entities.testEntity import TestEntity
from model.entities.mapeoEntity import MapeoEntity, MapeoDetailEntity
...
Base.metadata.create_all(bind=engine)
```
Estas clases nunca se referencian por nombre en el resto del archivo — el
import en sí (el efecto secundario de que la metaclase declarativa las
registre en `Base.metadata`) es lo único que hace falta. `ruff` (F401) no
tiene forma de saber esto y las marca como "imported but unused", lo que
hace fallar `format_check.py` **antes** de llegar a Compliance.

**Patrón correcto:** `# noqa: F401` explícito en cada línea, con un
comentario arriba que documente que es a propósito:
```python
# DatabaseModels -- importadas solo para que Base.metadata.create_all() las
# registre (side-effect de SQLAlchemy declarative), no se usan por nombre acá
# abajo -- noqa: F401 es correcto y necesario, no un import roto.
from model.entities.testEntity import TestEntity  # noqa: F401
from model.entities.mapeoEntity import MapeoEntity, MapeoDetailEntity  # noqa: F401
```

**Cuándo se aplica esta regla (item-dependiente, no incondicional):** solo
cuando un item toca el archivo que hace este tipo de import de registro
(típicamente `main.py`/el entrypoint) — si el Planner ya sabe que el item
va a reescribir ese archivo completo, conviene declarar el `# noqa`
directo en el `detalle_tecnico` en vez de esperar el rechazo de
`format_check.py` y gastar un reintento de Executor para nada.

**Mismo síntoma, causa distinta, ya visto en la práctica:** un import
realmente muerto (citado solo en un comentario, ej. `# ReqReporte: ReqReporte`
dentro de la firma comentada de un endpoint) también dispara F401 — ahí el
fix correcto es sacar el import, no agregar `noqa`. Al reescribir el
contenido literal completo de un archivo existente para un item, vale la
pena barrer visualmente los imports antes de mandarlo a Executor — evita
un rechazo/reintento completo por algo que un `grep` rápido ya detecta.

**Encontrado en:** 2026-08-24, dos casos el mismo día — imports de
entidades de registro en `main.py`, y un import muerto de un tipo/`datetime`
citado solo en comentario — ambos rechazados por `format_check.py` en el
primer intento, cero costo de LLM porque el rechazo es determinístico y
previo a Compliance.

## `db.execute(select(Entidad)).first()` NO devuelve la instancia de la entidad

**Verificado:** 2026-08-21, `sqlalchemy>=2.0`.

**Patrón correcto** (para obtener instancias de UNA sola entidad):
```python
stmt = select(Usuario).where(Usuario.ca_rut == rut)
db.scalars(stmt).first()   # -> Usuario | None
db.scalars(stmt).all()     # -> list[Usuario]
```

**Patrón incorrecto visto en la práctica:**
```python
db.execute(stmt).first()   # -> Row | None, NO Usuario
db.execute(stmt).all()     # -> list[Row], NO list[Usuario]
```
`db.execute(select(Entidad))` devuelve `Row` objects que *envuelven* la
entidad (accesible como `row[0]`, no como atributos directos) — acceder
`resultado.ca_rut` sobre un `Row` de este tipo falla en runtime. Con
`db.scalars(...)` se pide explícitamente "dame la columna/entidad escalar",
que sí da la instancia directa.

**Encontrado en:** 2026-08-21 — no lo atrapó ningún test hasta que se
especificó explícito en `detalle_tecnico`; el bug es silencioso (no rompe
en `pytest` con dobles/monkeypatch, solo rompería contra una base real) —
ver también la entrada de abajo sobre queries con `JOIN`, que si usan
`select()`/`.mappings()` con columnas explícitas evitan esta ambigüedad de
raíz.

## Queries con `JOIN` entre dos tablas: evitar el resultado ambiguo `Row`/tupla-de-entidades

**Contexto:** un `select(EntidadA, EntidadB).join(...)` (o el equivalente
`db.query(EntidadA, EntidadB).join(...)`) devuelve, por fila, un objeto que
combina ambas entidades — acceder a una columna específica requiere saber de
qué entidad viene (`fila[0].columna` vs `fila[1].columna`, o acceso por
nombre de entidad según la API usada), y es fácil pedir una columna que
"suena" a que está en una tabla cuando en realidad solo existe en la otra
tabla del `JOIN` (pasó de verdad: se pidió una columna de glosa de local
cuando esa columna solo existe en la tabla `Local`, la tabla joineada).

**ACTUALIZADO 2026-08-21 (mismo día, sesión posterior) — `list[dict]` queda
SUPERADO por un row schema Pydantic.** Seleccionar columnas explícitas +
`.label()` en agregaciones (ver abajo) sigue siendo necesario, pero devolver
`list[dict]` crudo resultó insuficiente en la práctica: en dos
replanificaciones completas distintas del mismo proyecto, el service
consumidor adivinó mal las claves del dict (nombres de columna parecidos
pero distintos según la función, hasta una clave inventada que nunca
existió) — errores silenciosos (`KeyError` en runtime, invisibles para
`format_check.py` porque no es un import roto) que Compliance aprobó
falsamente más de una vez porque revisa código, no ejecuta contra datos
reales. La causa de fondo: un `dict` no es un contrato — sus claves no
aparecen en ningún lado que la `interfaz` del item pueda declarar de forma
verificable.

**Patrón correcto (vigente) — el repository devuelve `list[RowSchema]`, un
Pydantic model definido junto al repository, no un dict:**
```python
from decimal import Decimal
from pydantic import BaseModel

class VentaDiariaRow(BaseModel):
    vca_loc: int
    vca_glosa: str
    vca_fec: int
    vca_monto: Decimal  # numeric(19,4) real en el DDL -- ver regla de tipos abajo

def obtener_ventas_diarias(db: Session, fecha_int: int, cod_com: int) -> list[VentaDiariaRow]:
    stmt = (
        select(
            VentaDetalleLocal.vca_loc,
            Local.vca_glosa,              # de la tabla correcta, explícito
            VentaDetalleLocal.vca_fec,
            func.sum(VentaDetalleLocal.vca_monto).label("vca_monto"),  # SIEMPRE .label() en agregaciones
        )
        .join(Local, ...)
        .group_by(...)
    )
    return [VentaDiariaRow.model_validate(row) for row in db.execute(stmt).mappings().all()]
```
`Pydantic.model_validate()` acepta un `RowMapping` (lo que da
`.mappings()`) directo, sin necesitar `from_attributes=True` ni convertirlo
a `dict` primero. El nombre de la clase (`VentaDiariaRow`) es ahora un
símbolo real que la `interfaz` del item declara con import literal —
el service que depende de este repository ya no adivina claves, importa el
schema y el propio editor/typechecker (o Compliance leyendo el código)
puede verificar los campos que existen de verdad. Un row schema por cada
forma de retorno distinta (no reusar uno entre queries con columnas
distintas). Estos row schemas son internos (nunca llevan
`alias_generator=to_camel` ni se exponen tal cual por la API) — el service
arma el `Response` schema de la API a partir de los campos ya tipados de la
row.

**Patrón viejo, ya no usar** (queda documentado para reconocerlo si
aparece en código generado, no para copiarlo):
```python
return [dict(row) for row in db.execute(stmt).mappings().all()]
```

**Función de agregación `COALESCE` — no es un símbolo de sqlalchemy, es
`func.coalesce(...)`:**
```python
from sqlalchemy import select, func
stmt = select(func.coalesce(func.sum(VentaDetalleLocal.vca_monto), 0)).where(...)
```
Patrón incorrecto visto en la práctica: `from sqlalchemy import COALESCE` —
`ImportError: cannot import name 'COALESCE' from 'sqlalchemy'`. Como con
cualquier función SQL, se llama vía `func.<nombre_en_minuscula>(...)`, nunca
como símbolo importado directo.

## Tipo Python de un campo derivado: siempre el de la columna real, columna por columna — no un tipo único para "todo lo que se llama monto"

**Contexto:** un mismo nombre de campo (`monto`) puede derivar de columnas
reales distintas con tipos distintos, incluso dentro del mismo proyecto.
Una columna puede ser `numeric(19,4)` (→ `Decimal`) mientras otra con
nombre parecido, de otra tabla, es `int4` (→ `int`). Generalizar ("todos
los 'monto' son Decimal") produce el error tan fácil como asumir que todos
son `int`.

**Patrón correcto:** para cada campo de un `RowSchema`/response schema,
verificar el tipo de la columna real de origen (DDL o `schema_bd_origen`,
ver `schemas/plan.contract.md`) antes de tipar — `numeric(p,s)` → `Decimal`,
`int2`/`int4`/`int8` → `int`, sin excepción por similitud de nombre.

**Patrón incorrecto visto en la práctica:** tipar `monto`/`total_monto`
como `int` en todos los schemas de un módulo de reportes porque "así están
los otros", cuando el campo puntual venía de una columna `Numeric` real —
o al revés, asumir `Decimal` en todos por la misma razón inversa. Ambos
errores ya se vieron en el mismo proyecto, en items distintos.

**Encontrado en:** 2026-08-24 — además, un intento de arreglar esto a mano
(edición directa de `detalle_tecnico` sin re-verificar contra el DDL)
generalizó mal la regla en sentido inverso y produjo el mismo tipo de bug
por el motivo opuesto — la lección aplica tanto al modelo como a quien
edita el plan a mano.

También, 2026-08-21: el patrón `list[dict]` (ver entrada de arriba) fue el
origen de una oscilación severa — 3 rechazos seguidos, cada reintento
perdía un fix anterior al corregir otro; hizo falta escalar a
`executor_senior` con una tabla de claves verificadas a mano para
resolverlo. El bug de `COALESCE` se coló sin que `format_check.py` (no
revisa librerías externas) ni Compliance lo atraparan — recién lo destapó
el smoke test de otro item al importar la cadena completa de la app.
