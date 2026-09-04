# Base de conocimiento — pyodbc (AS/400 · DB2 / iSeries)

## IN(...) con listas de enteros: valores literales en el SQL, NUNCA parámetros "?"

**Verificado:** 2026-08-26, contra código real en producción
(`tesoreria-origen/consultas.py::extraer_as400`) — medido explícitamente por
Felipe contra el driver real: la misma consulta con `IN (?,?,?...)` (cada
"?" negociando tipo con el servidor) tardaba **más de 10 minutos** con ~100
valores; con los mismos valores escritos literalmente en el SQL, la
respuesta es de milisegundos (confirmado además ejecutándola directo en
"Run SQL Scripts" del lado AS/400).

**Patrón correcto:**
```python
# Los valores YA se validaron como enteros antes de llegar acá (ver abajo) —
# es seguro escribirlos directo en el SQL, no reciben texto arbitrario.
def _a_enteros_validos(valores, etiqueta):
    validos = []
    for v in dict.fromkeys(valores):
        if v in (None, "", "0", "nan"):
            continue
        try:
            validos.append(int(str(v).strip()))
        except (TypeError, ValueError):
            log.warning("%s '%s' no es numérico, se omite.", etiqueta, v)
    return validos

lista_folios = ",".join(str(f) for f in folios_validos)
query = f"SELECT ... FROM {lib}.VTATRAN v WHERE v.TRNV_DOC IN ({lista_folios})"
df = pd.read_sql_query(query, conn_as400)
```

**Patrón incorrecto a evitar:** `WHERE v.TRNV_DOC IN ({','.join('?' * len(folios))})`
con `params=folios` — sintácticamente correcto y seguro contra inyección,
pero **inutilizable en producción** contra este driver (`IBM i Access ODBC
Driver`) por el timeout real de arriba. No "corregir" el patrón de arriba a
parametrizado creyendo que es más seguro — la seguridad ya está garantizada
por `_a_enteros_validos` (los valores se validan como `int` antes de
interpolarse; nunca puede llegar texto ni SQL arbitrario al f-string).

**Lotes:** listas largas (cientos de valores) se dividen en lotes de a lo
más 200 (`TAMANO_LOTE`) antes de armar el `IN(...)`, para no generar un SQL
gigante en un solo query — ver `_en_lotes` en el origen.

**Timeout real de la operación completa, no solo de conexión:** el driver
no soporta fijar timeout de *consulta* (solo de conexión) — si el servidor
se cuelga respondiendo, el proceso Python queda esperando indefinidamente.
La única forma real de acotarlo es un timeout de wall-clock externo
(`ThreadPoolExecutor` + `future.result(timeout=...)`), aceptando que el
hilo interno puede quedar huérfano corriendo en segundo plano — ver
`_con_timeout` en `tesoreria-origen/app.py`. Preservar este mecanismo tal
cual, no asumir que `pyodbc.connect(..., timeout=N)` alcanza (ese timeout
solo cubre el login/conexión inicial).

**Encontrado en:** `tesoreria-migrado`, `TES-DAL-AS400-001`, 2026-08-26.

## Nombre real del driver ODBC registrado por `ibm-iaccess` (apt) — NO es el mismo que el de una instalación vieja "iSeries Access"

**Verificado:** 2026-08-27, contra una imagen Docker real (`docker run --rm
<imagen> odbcinst -q -d`, no un supuesto), paquete `ibm-iaccess` instalado
vía el repo oficial de IBM
(`https://public.dhe.ibm.com/software/ibmi/products/odbc/debs/...`).

El paquete `ibm-iaccess` (el instalador ODBC actual de IBM, "IBM i Access
Client Solutions") registra el driver en `odbcinst.ini` como:
```
[IBM i Access ODBC Driver]
[IBM i Access ODBC Driver 64-bit]
```
**NO** como `"iSeries Access ODBC Driver"` — ese es el nombre de una
instalación más vieja ("IBM i Access for Linux"/"iSeries Access"), que
puede seguir siendo el correcto en un host físico que ya la tenía instalada
de antes (ambos nombres son "correctos", cada uno para SU instalación real
— no hay que "corregir" uno al otro sin verificar cuál aplica en cada
entorno).

**Regla práctica:** el valor de `DRIVER=` en la cadena de conexión (env var
tipo `AS400_DRIVER`) tiene que coincidir EXACTO con lo que
`odbcinst -q -d` devuelve en ESE entorno específico — no asumir que el
valor que funciona en el host físico de producción sirve igual dentro de
un contenedor Docker que instaló el driver de otra forma (o viceversa). Si
se está armando un Dockerfile nuevo con `ibm-iaccess`, verificar el nombre
real corriendo el comando contra la imagen construida antes de fijar el
default en `detalle_tecnico` — no copiarlo del `.env`/config de otro
entorno.

**Encontrado en:** `tesoreria-migrado`, `DEPLOY-DAL-001`, 2026-08-27 —
`dal/app/core/config.py` (`DAL-CORE-001`) ya tenía `as400_driver =
"{iSeries Access ODBC Driver}"` heredado (correcto) del monolito/host
físico; el override para el contenedor Docker se resolvió con
`AS400_DRIVER={IBM i Access ODBC Driver}` en el `environment` de
`docker-compose.yml`, sin tocar el default de `config.py`.
