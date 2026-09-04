# Base de conocimiento — ldap3

## Bind directo contra Active Directory (autenticación por UPN, sin cuenta de servicio)

**Verificado:** 2026-08-26, contra código real en producción (no doc oficial
re-chequeada aparte — el patrón ya corre contra el AD real de la empresa en
`tesoreria-origen/auth_ldap.py`, tratado como fuente confiable equivalente a
"verificado contra comportamiento real" por `knowledge/README.md`).

**Patrón correcto:**
```python
from ldap3 import ALL, SIMPLE, Connection, Server
from ldap3.core.exceptions import LDAPSocketOpenError, LDAPSocketReceiveError

server = Server(host, port=port, use_ssl=use_ssl, get_info=ALL, connect_timeout=timeout_seg)
principal = f"{usuario}@{dominio}"  # UPN, ej. dominio derivado de "DC=hites,DC=global" -> "hites.global"

conn = None
try:
    conn = Connection(server, user=principal, password=password, authentication=SIMPLE)
    autenticado = conn.bind()  # True/False, nunca lanza por credenciales malas
except (LDAPSocketOpenError, LDAPSocketReceiveError) as exc:
    # esto SÍ es una falla real de conectividad (sin red/VPN, server caído) —
    # distinguir explícitamente de "usuario/clave incorrectos"
    raise LDAPConexionError(str(exc)) from exc
finally:
    if conn is not None:
        conn.unbind()
```

**Distinción importante:** `conn.bind()` devuelve `False` (no lanza
excepción) cuando la credencial es incorrecta — ese caso es "usuario o clave
incorrectos", HTTP 401. `LDAPSocketOpenError`/`LDAPSocketReceiveError` son un
problema de infraestructura (sin conexión al servidor LDAP), HTTP 503 o
similar — mapear a una excepción de dominio distinta (`LDAPConexionError`,
ya existe en el proyecto origen) para que el mensaje al usuario no confunda
"tu clave está mal" con "no hay red hacia el servidor".

**Patrón incorrecto a evitar:** no envolver `conn.bind()` en un
`except Exception` genérico que trate credencial-inválida igual que
falla-de-red — el usuario final recibe el mensaje equivocado en cada caso.

**Modo alternativo (cuenta de servicio + búsqueda + re-bind)** existe en el
código origen (`_autenticar_con_cuenta_servicio`) para cuando haya
`LDAP_ADMIN_PRINCIPAL`/`LDAP_ADMIN_PASSWORD` configurados — preservar ambos
modos en la migración, no solo el directo (hoy activo), por si se configura
la cuenta de servicio más adelante sin tocar código.

**Encontrado en:** `tesoreria-migrado`, `TES-AUTH-001`, 2026-08-26.
