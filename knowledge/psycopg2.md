# psycopg2

## Un `%` literal en el SQL (ej. `LIKE '%TEXTO%'`) rompe la ejecución si la query se llama con `params=`

**Verificado:** 2026-09-01, `psycopg2-binary==2.9.12`.

Cuando una query se ejecuta pasando `params` (dict con `%(clave)s` o tupla
con `%s`), psycopg2 sustituye los parámetros con el operador `%` de Python
sobre el string COMPLETO de la query — no solo sobre los placeholders. Un
`%` literal en cualquier otra parte del SQL (típicamente un `LIKE
'%ALGO%'`) tiene que ir DOBLADO (`%%ALGO%%`), igual que en cualquier
`str % args` de Python normal, o la sustitución falla para toda la query,
no solo para ese fragmento.

Esto NO depende de si la query usa placeholders nombrados o posicionales —
alcanza con que se llame con `params` (vía `cursor.execute(sql, params)` o
`pandas.read_sql_query(sql, conn, params=...)`, mismo mecanismo interno).
Una query sin ningún placeholder ejecutada sin `params` no tiene este
problema (no pasa por la sustitución `%`).

**Patrón correcto:**
```python
QUERY = """
    SELECT *
    FROM tabla
    WHERE id = ANY(%(ids)s)
      AND estado LIKE '%%CANCEL%%'
"""
cur.execute(QUERY, {"ids": ids})
```

**Patrón incorrecto visto en la práctica:**
```python
QUERY = """
    SELECT *
    FROM tabla
    WHERE id = ANY(%(ids)s)
      AND estado LIKE '%CANCEL%'
"""
cur.execute(QUERY, {"ids": ids})
```
— `psycopg2.ProgrammingError: argument formats can't be mixed`. El `%C`
de `'%CANCEL%'` se lee como un intento de formato inválido, mezclado con el
`%(ids)s` real. El error no menciona `LIKE` ni da la posición del `%`
problemático — hay que revisar el texto completo del SQL a mano.

**Cómo se detecta antes de que explote en runtime:** si el SQL de origen
(monolito legado) ya tenía el `%%` doblado en un `LIKE`, preservarlo tal
cual al migrar — NO "limpiarlo" a `%` simple pensando que es un typo del
código viejo. Si se transcribe una query nueva con un `LIKE '%...%'` y va a
ejecutarse con `params`, doblar el `%` desde el vamos.

**Encontrado en:** `tesoreria-migrado`, `DAL-OMS-001`
(`oms_repository.py`, `QUERY`/`QUERY_POR_FECHA`), 2026-09-01 — bug real en
producción del entregable, confirmado con el traceback del contenedor
`dal`. El SQL de origen (`tesoreria-origen/consultas.py`) sí tenía el `%%`
correcto; se perdió al transcribir el texto a `plan.json.detalle_tecnico`.
