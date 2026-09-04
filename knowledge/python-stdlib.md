# Python (stdlib)

## `datetime.utcnow()` está deprecado — usar `datetime.now(timezone.utc)`

**Verificado:** 2026-08-21, Python 3.12 (`DeprecationWarning` real al llamar
`datetime.utcnow()`, deprecado desde Python 3.12 en favor de variantes
timezone-aware).

**Patrón correcto:**
```python
from datetime import datetime, timezone

fecha = datetime.now(timezone.utc)
```

**Patrón incorrecto visto en la práctica:**
```python
from datetime import datetime

fecha = datetime.utcnow()
```
Sigue funcionando hoy pero emite `DeprecationWarning` y devuelve un
`datetime` *naive* (sin tzinfo) — inconsistente con el resto del código que
sí maneja fechas timezone-aware (ver `crear_token_acceso` en
`autenticacion_service.py`, que ya usa `datetime.now(timezone.utc)`
correctamente). Mezclar naive y aware en el mismo proyecto es una fuente
clásica de bugs de comparación de fechas (`TypeError: can't compare
offset-naive and offset-aware datetimes`).

**Encontrado en:** `web-portal-coas-migrado`, `autenticacion_repository.py`
(`registrar_auditoria_login`/`_login_admin`/`_cambio_clave`), revisión manual
de calidad de código post-plan, 2026-08-21.
