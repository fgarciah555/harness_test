# httpx

## `httpx.Client(...)` NO sigue redirects por default — un `307`/`308` llega con cuerpo vacío y `.json()` explota

**Verificado:** 2026-08-26, `httpx==0.28.1`.

```python
httpx.Client(base_url=..., headers=..., timeout=10.0, follow_redirects=True)  # OK
httpx.Client(base_url=..., headers=..., timeout=10.0)                        # MAL
```
Default de `httpx` es `follow_redirects=False`. Si el destino responde
`307`/`308` (típico: FastAPI redirigiendo por barra final faltante, ver
`fastapi.md`), el cuerpo llega vacío y `response.json()` tira
`JSONDecodeError` sin mencionar redirects para nada.

**Regla práctica:** cualquier cliente HTTP interno servicio-a-servicio
debería declarar `follow_redirects=True` desde el vamos.

**Encontrado en:** `BE-CORE-002` (`dal_client_base.py`), 2026-08-26 — 10
items dependientes invalidados al corregirlo (cliente compartido).
