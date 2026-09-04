# Base de conocimiento — PyJWT

## Emisión y verificación de JWT (FastAPI)

**Verificado:** 2026-08-26, contra la documentación oficial de FastAPI
(`fastapi.tiangolo.com/tutorial/security/oauth2-jwt/`), librería `pyjwt`
(paquete pip `PyJWT`, se importa como `jwt`).

**Patrón correcto:**
```python
import jwt
from datetime import datetime, timedelta, timezone

ALGORITHM = "HS256"

def crear_access_token(data: dict, expira_en: timedelta) -> str:
    payload = data.copy()
    payload["exp"] = datetime.now(timezone.utc) + expira_en
    return jwt.encode(payload, settings.secret_key, algorithm=ALGORITHM)

def decodificar_token(token: str) -> dict:
    # jwt.decode ya valida "exp" y lanza jwt.ExpiredSignatureError si venció
    return jwt.decode(token, settings.secret_key, algorithms=[ALGORITHM])
```

Claims mínimos: `sub` (identificador del usuario, ej. username LDAP) + `exp`
(expiración, agregado por la app, no automático). Excepciones a capturar:
`jwt.ExpiredSignatureError` (token vencido) y `jwt.InvalidTokenError` (firma
inválida/malformado) — mapear ambas a una excepción de dominio propia
(`TokenInvalidoError`) en la capa de auth, no dejar que la excepción cruda de
`pyjwt` llegue al router (ver `error-handling.md`).

**Patrón incorrecto a evitar:** `from jose import jwt` (`python-jose`) — es
la librería que usaban tutoriales más viejos de FastAPI; la documentación
actual recomienda `pyjwt` directo. No mezclar ambas en el mismo proyecto.
También evitar `datetime.utcnow()` (naive, deprecado) — usar
`datetime.now(timezone.utc)`, ya cubierto como regla incondicional en el
`SYSTEM_PROMPT` de Executor.

**Encontrado en:** 2026-08-26 — primera vez que este proyecto necesita
emitir JWT (reemplaza sesión de servidor de Flask-Login del monolito
origen).
