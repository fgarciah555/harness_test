# Pydantic v2

## `populate_by_name=True` SOLO no aplica ningún casing — necesita `alias_generator` junto

**Verificado:** 2026-08-26, pydantic==2.13.4.

**Patrón correcto:**
```python
from pydantic import BaseModel, ConfigDict, alias_generators

class UsuarioActual(BaseModel):
    model_config = ConfigDict(
        alias_generator=alias_generators.to_camel,
        populate_by_name=True,
    )
    cod_com: int
    is_admin: bool
```
Con esto, `UsuarioActual(cod_com=1, is_admin=True)` sigue funcionando (por
`populate_by_name=True`, construcción por el nombre real), y el JSON de
salida (`model_dump(by_alias=True)`, o cualquier response de FastAPI que use
este modelo como `response_model`, que serializa `by_alias=True` por
defecto) sale en camelCase real: `{"codCom": 1, "isAdmin": true}`.

**Patrón incorrecto visto en la práctica:**
```python
class UsuarioActual(BaseModel):
    model_config = ConfigDict(populate_by_name=True)  # <-- falta alias_generator
    cod_com: int
    is_admin: bool
```
`populate_by_name=True` por sí solo **no define ningún alias** — solo le
dice a Pydantic "también aceptá el nombre real además del alias, si hay
uno". Sin `alias_generator`, no hay alias que generar, así que este modelo
nunca tuvo ninguno: el JSON de salida sigue siendo `{"cod_com": 1,
"is_admin": true}`, snake_case, violando cualquier convención de casing
`camelCase` del proyecto sin que ningún error salte — Pydantic no se queja,
simplemente no hace el trabajo que el nombre del flag sugiere.

**Por qué es fácil no verlo:** el código compila, `py_compile`/`ruff` no
detectan nada (es una cuestión de configuración semántica, no de sintaxis),
y un test que construya el modelo por kwargs y lea sus atributos en Python
(`usuario.cod_com`) tampoco lo nota — el bug solo es visible inspeccionando
el JSON serializado real (`model_dump(by_alias=True)` o pegándole al
endpoint), que es exactamente lo que un criterio de Compliance débil
("el schema tiene `populate_by_name=True`") no fuerza a verificar.

**Encontrado en:** 2026-08-26, dos schemas de response distintos — en
ambos, el motor local (LM Studio) falló 3 veces seguidas agregando el
`alias_generator` pese a una instrucción explícita y literal en
`detalle_tecnico`; se resolvió recién al escalar a `executor_senior`
(DeepSeek).

## `@model_validator(mode="before")` vs `mode="after")` según cómo se instancia el modelo

**Verificado:** 2026-08-24, pydantic==2.13.4 (vía `pydantic-settings`/
`fastapi[standard]`, no pinneado directo).

**Patrón correcto:**
```python
# Cuando el modelo se instancia con MODEL.model_validate(obj) y `obj` puede ser
# cualquier cosa con atributos (una fila de SQLAlchemy, otro objeto, un dict) --
# no solo con kwargs explícitos -- usar mode="after". Un validador "after" corre
# sobre la instancia YA construida (self), sin importar el mecanismo de origen.
class ReporteJmeter(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    totalErrores: Optional[int] = 0

    @model_validator(mode="after")
    def null_a_cero(self):
        for k in self.__class__.model_fields:
            if getattr(self, k) is None:
                setattr(self, k, 0)
        return self
```

**Patrón incorrecto visto en la práctica:**
```python
@model_validator(mode="before")
@classmethod
def null_a_cero(cls, values):
    result = {}
    for k, v in values.items():   # <-- explota si `values` no es un dict
        ...
```
Un validador `mode="before"` recibe el input **crudo**, tal cual se pasó a
`model_validate()`/al constructor — antes de que Pydantic lo convierta a
atributos del modelo. Si el modelo se instancia vía
`Modelo.model_validate(row)` con `from_attributes=True` y `row` es un `Row`
de SQLAlchemy (o cualquier objeto que no sea un dict), `values` en el
validador `before` **es ese objeto crudo**, no un dict — `values.items()`
tira `AttributeError: 'Row' object has no attribute 'items'` en cada fila.

Este error es fácil de no ver en local: si el mismo patrón de validador
`before` con `values.items()` se usa en OTRO modelo del mismo archivo que sí
se instancia siempre con kwargs explícitos (`Modelo(campo=x, campo2=y)`),
ese otro modelo funciona bien (Pydantic sí arma un dict a partir de kwargs) —
el bug es específico de la clase que se instancia vía `model_validate()`
sobre un objeto no-dict, no del patrón en general.

**Regla práctica:** si un modelo se instancia alguna vez vía
`model_validate(obj)` con `obj` potencialmente no-dict (fila de SQLAlchemy,
respuesta de otra librería, cualquier objeto con atributos), usar
`mode="after"` con `getattr`/`setattr(self, k, v)` en vez de `mode="before"`
con `values.items()`. Si TODAS las instancias del modelo vienen de kwargs
explícitos, `mode="before"` con `values.items()` funciona, pero `mode="after"`
es igual de válido y evita tener que acordarse de la distinción — más seguro
por defecto cuando no se sabe de antemano cómo se va a instanciar el modelo
en cada call site.

**Encontrado en:** 2026-08-24, revisión 3 de un item de reportes — Compliance
(DeepSeek `deepseek-reasoner`) rechazó el primer intento citando el
`AttributeError` real esperado antes de que se corriera contra datos
reales; se corrigió cambiando `mode="before"` a `mode="after"` en los dos
schemas que se instancian vía `model_validate(row)`, y se verificó después
contra la app real corriendo (Docker + Postgres) que el `None` proveniente
de un `SUM()` sobre puros `NULL` en la base efectivamente sale `0` en el
JSON final.

## Un modelo anidado en `List[EseModelo]` de otro modelo se REVALIDA al anidar — un `@model_validator` no idempotente se ejecuta más de una vez

**Verificado:** 2026-08-24, pydantic==2.13.4, mismo proyecto que arriba, revisión 4 del mismo item.

**Patrón incorrecto visto en la práctica** (transformación NO idempotente dentro de un validador de un modelo que luego se anida):
```python
class ReporteStepApigee(BaseModel):
    tiempoRespuestaPromedio_apigee: Optional[float] = 0

    @model_validator(mode="after")
    def transform_fields(self):
        # dividir por 1000 (ms -> seg) ACA ADENTRO es lo que rompe todo
        self.tiempoRespuestaPromedio_apigee = self.tiempoRespuestaPromedio_apigee / 1000
        return self

class ReporteApigeeEndpoint(BaseModel):
    detalleStep: List[ReporteStepApigee]
```
```python
step = ReporteStepApigee(tiempoRespuestaPromedio_apigee=820.0)
print(step.tiempoRespuestaPromedio_apigee)   # 0.82 -- correcto, primera validación

endpoint = ReporteApigeeEndpoint(detalleStep=[step])
print(endpoint.detalleStep[0].tiempoRespuestaPromedio_apigee)  # 0.00082 -- MAL, dividido de nuevo
```
Al pasar `step` (ya una instancia válida de `ReporteStepApigee`) dentro de
`detalleStep=[...]` para construir `ReporteApigeeEndpoint`, Pydantic v2
**no asume que una instancia ya sea válida solo por ser del tipo correcto**
— vuelve a correr la validación completa del campo `List[ReporteStepApigee]`
sobre cada item, lo que dispara `transform_fields()` una segunda vez. Con
`/1000` dentro del validador, la segunda pasada divide otra vez (compone el
error). Un agregado calculado en el padre ANTES de anidar (ej.
`sum(d.campo for d in detalleStep) / n` corrido antes de construir
`ReporteApigeeEndpoint`) no se ve afectado, porque lee los valores cuando
todavía están recién construidos (una sola división) — eso hace que el bug
sea invisible mirando solo el agregado, solo se ve en el detalle anidado.

**Patrón correcto:** cualquier transformación NO idempotente (dividir,
multiplicar, sumar, concatenar, incrementar un contador) que dependa de
"esto corre una sola vez" no puede vivir en un `@model_validator` de un
modelo que se vaya a anidar como item de un `List[...]`/campo de otro
modelo — tiene que aplicarse UNA VEZ, explícita, en el código que arma los
argumentos del constructor (fuera de Pydantic), antes de crear el objeto.
El validador del modelo anidable se deja solo con operaciones idempotentes
sobre el valor ya final: `None -> 0` (0 sigue siendo 0 en cualquier
revalidación) y `round(v, 2)` (`round(round(x,2),2) == round(x,2)`) son
seguras de repetir cualquier cantidad de veces.

**Encontrado en:** 2026-08-24, revisión 4 del mismo item de reportes —
descubierto revisando visualmente el PDF generado por un proyecto
consumidor: los tiempos de respuesta en la tabla de detalle por step salían
~1000x más chicos que el resumen del mismo servicio. Reproducido de forma
aislada (construir el step suelto, después anidarlo) antes de escribir el
fix, confirmando la causa exacta antes de tocar código.

## Un campo `str` NO coacciona un `Decimal`/numérico crudo devuelto por un driver de DB — validación falla en modo lax

**Verificado:** 2026-09-01, pydantic==2.13.4.

Un campo tipado `str` (o `str | None`) en un `BaseModel` es estricto para
tipos numéricos incluso en el modo lax por defecto de pydantic v2: si se le
pasa un `Decimal`, `int` o `float` (típicamente el valor crudo que devuelve
un cursor de DB para una columna `numeric`/`decimal`, vía
`psycopg2.extras.RealDictCursor` u otro driver), la validación FALLA con
`string_type` — no hay coacción automática a texto. Esto es asimétrico:
un campo `int`/`float` SÍ acepta un `Decimal` equivalente sin error (lax
coacciona numérico→numérico, no numérico→texto).

Consecuencia práctica para repositories que leen de una DB externa (schema
no controlado por el proyecto, tipos reales a veces distintos de lo que
sugiere el nombre de la columna o su uso en el código origen): construir el
modelo Pydantic con el valor crudo del cursor es lo que rompe, no el modelo
en sí — el fix es castear explícito en el repository, no relajar el tipo
del schema público a `Decimal`/`Any` (eso filtraría un tipo interno de DB
hacia el contrato de API, inconsistente con campos hermanos que sí son
identificadores de texto legítimos, ej. `folio`, `sub_order`).

**Patrón correcto:**
```python
class FilaRow(BaseModel):
    codigo: str | None

fila = cursor.fetchone()  # RealDictCursor -> dict
FilaRow(codigo=str(fila["codigo"]) if fila["codigo"] is not None else None)
```

**Patrón incorrecto visto en la práctica:**
```python
FilaRow(codigo=fila["codigo"])
```
— si la columna real es `numeric`/`decimal` en la DB, `fila["codigo"]` es
un `Decimal`, y esto tira:
```
pydantic_core._pydantic_core.ValidationError: 1 validation error for FilaRow
codigo
  Input should be a valid string [type=string_type, input_value=Decimal('137607226'), input_type=Decimal]
```

**Cómo se detecta antes de que explote en runtime:** si hay DDL real de la
tabla (ver `schemas/plan.contract.md`, `schema_bd_origen`), cruzar el tipo
de CADA columna contra el campo Pydantic que la recibe — tener el DDL a
mano no alcanza si no se cruza campo por campo (ver caso real abajo). Más
barato todavía: castear explícito (`str(valor) if valor is not None else
None`) en CUALQUIER campo de un repository que lee de un sistema externo,
sin importar lo que diga el DDL — blinda contra un DDL desactualizado o
mal leído, y no tiene costo real (el valor ya iba a mostrarse como texto).

**Encontrado en:** 2026-09-01, un repository de un sistema externo de solo
lectura (campo de código de boleta) — bug real en producción del
entregable. El DDL real ya estaba documentado y disponible un día ANTES de
escribirse `plan.json` — no fue un caso de DDL ausente, fue no cruzarlo
campo por campo al tipar el `RowSchema`. Los repositories hermanos del
mismo proyecto (otros dos sistemas externos de solo lectura) no tuvieron
este bug porque ya casteaban cada campo explícito, sin depender de si el
tipo "parecía" texto.
