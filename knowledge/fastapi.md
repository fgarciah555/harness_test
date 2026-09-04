# FastAPI

## Un router montado con `prefix="/"` obliga a llamarlo CON barra final — sin ella, FastAPI redirige (307) en vez de responder

**Verificado:** 2026-08-26, `fastapi==0.115.6`, contra un cliente HTTP real
(`TestClient` sí sigue redirects por default, por eso no se ve ahí).

```python
router = APIRouter(prefix="/depositos")

@router.get("/")   # ruta final real: /depositos/  (CON barra)
```
Llamarla sin la barra final (`/depositos`) devuelve `307`, no los datos.
Un cliente que no sigue redirects (ver `httpx.md`) recibe el 307 con
cuerpo vacío — `response.json()` sobre eso tira `JSONDecodeError`, sin
mencionar rutas ni barras para nada.

**Regla práctica:** el cliente HTTP compartido debería seguir redirects
por default (`follow_redirects=True`, ver `httpx.md`) en vez de depender
de que cada llamador recuerde la barra exacta de cada router.

**Encontrado en:** `BE-CORE-002`/`DAL-DEPOSITO-001`, 2026-08-26.

## Un router definido en un módulo plano se importa como atributo del módulo, no como submódulo `.router`

**Verificado:** 2026-08-26, `fastapi==0.115.6`, código real que pasó
`format_check.py`/Compliance en `Web_coas/web-portal-coas-destino`.

**Patrón correcto:**
```python
# app/api/v1/auth.py -- MÓDULO PLANO (no un paquete), define una variable `router`
router = APIRouter(prefix="/auth")

# quien lo monta:
from app.api.v1.auth import router as router_auth
app.include_router(router_auth, prefix="/api/v1")
```

**Patrón incorrecto visto en la práctica:**
```python
from app.api.v1.auth.router import router as router_auth
```
Falla con import roto (`format_check.py` lo detecta: "no resuelve a ningún
archivo del proyecto") porque `app.api.v1.auth` es un **archivo**
(`auth.py`), no un **paquete** con un submódulo `router.py` adentro — el
patrón `<algo>.router` es válido cuando `<algo>` es un paquete
(`app/api/v1/auth/router.py`), pero este proyecto usa un router por
archivo plano (`app/api/v1/auth.py`, `app/api/v1/reportes.py`), donde
`router` es simplemente una variable de nivel de módulo. El modelo
adivinó la convención de paquete (frecuente en otros proyectos FastAPI)
sin verificar cuál de las dos usa este proyecto en particular.

**Regla práctica:** antes de escribir el import de un router en
`detalle_tecnico`, confirmar si el archivo que lo define es un módulo
plano (`archivo.py`, entonces `from paquete.archivo import router`) o un
paquete con submódulo dedicado (`carpeta/router.py`, entonces
`from paquete.carpeta.router import router`) — no asumir ninguna de las
dos por defecto, y declarar el import literal completo en la `interfaz`
del item que define el router (ver `plan.contract.md`) para que quien lo
consume no tenga que adivinar.

**Encontrado en:** `Web_coas/web-portal-coas-destino`, items `BE-CORE-003`
y `DAL-CORE-002` (montaje de routers en `main.py`), 2026-08-26 — se repitió
varias veces durante regeneraciones en cascada del mismo bug, no son
hallazgos independientes.

## `HTTPBearer` / `HTTPAuthorizationCredentials` viven en `fastapi.security`, no en `fastapi`

**Verificado:** 2026-08-21, `fastapi==0.141.1` (versión resuelta en
`web-portal-coas-migrado/backend/requirements.txt`, confirmado con
`python -c "import fastapi; print(fastapi.__version__)"`).

**Patrón correcto:**
```python
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

def obtener_usuario_actual(
    credenciales: HTTPAuthorizationCredentials = Depends(HTTPBearer()),
) -> UsuarioAutenticado:
    token = credenciales.credentials
    ...
```

**Patrón incorrecto visto en la práctica:**
```python
from fastapi import Depends, HTTPBearer  # ImportError real
```
`ImportError: cannot import name 'HTTPBearer' from 'fastapi'`. Los esquemas
de seguridad (`HTTPBearer`, `OAuth2PasswordBearer`, `APIKeyHeader`, etc.)
viven en el submódulo `fastapi.security`, no en el paquete raíz.

**Encontrado en:** `web-portal-coas-migrado`, `COAS-AUTH-003`, 2026-08-21 —
atrapado por el smoke test (`ImportError` al importar el módulo en
`pytest`), antes de gastar Compliance.

## `include_router` con prefijo — no repetir el prefijo interno del router

**Contexto (no es un error de la librería, es una convención del propio
plan que vale la pena verificar dos veces):** si un router ya declara
`APIRouter(prefix="/auth")`, montarlo con
`app.include_router(auth.router, prefix="/api/v1/auth")` duplica el
segmento: la ruta final queda en `/api/v1/auth/auth/login`. El prefijo de
`include_router` se **concatena** con el `prefix` que el router ya trae, no
lo reemplaza. Usar `app.include_router(auth.router, prefix=settings.api_prefix)`
(solo el prefijo global) cuando el router ya trae su propio segmento.

**Encontrado en:** primera migración de `web-portal-coas` (2026-08-20, en
producción real del proyecto, no en un test) y de nuevo como caso de prueba
explícito en la replanificación del 2026-08-21 (`COAS-CORE-003`, esta vez
con un test de regresión — `test_ninguna_ruta_duplica_el_segmento_auth_o_reportes`
— que lo verifica automáticamente).

## `app.routes` no lista sub-rutas de `include_router` directamente en versiones recientes

**Verificado:** 2026-08-21, `fastapi==0.141.1`.

Iterar `app.routes` para inspeccionar rutas montadas vía `include_router` ya
no devuelve los `APIRoute` aplanados directo en la lista — devuelve un
`fastapi.routing._IncludedRouter` que envuelve al router incluido. Un chequeo
tipo `[r.path for r in app.routes if hasattr(r, 'path')]` filtra estos
objetos en silencio (no tienen `.path` directo) y da una falsa sensación de
"no hay rutas montadas". **Esto no significa que el ruteo esté roto** — un
`TestClient` real sí resuelve las rutas correctamente end-to-end. Para
inspeccionar rutas montadas de verdad, usar `app.routes` combinado con
recorrer `_IncludedRouter.routes` (o, más simple y confiable: pegarle a la
ruta real con `TestClient`/`client.get(...)` en vez de inspeccionar
`app.routes` a mano).

**Encontrado en:** `web-portal-coas-migrado`, verificación manual post-plan,
2026-08-21 — casi se interpretó como un bug real del ensamblado de
`main.py` hasta confirmar con `TestClient` que las rutas sí respondían.

## Un default de parámetro de dependencia SIN `Depends(...)` recién explota al montar la app de verdad, no al importar el archivo suelto

**Verificado:** 2026-08-21, `fastapi==0.141.1`.

**Patrón correcto:**
```python
def obtener_usuario_actual(
    credenciales: HTTPAuthorizationCredentials = Depends(HTTPBearer()),
    db: Session = Depends(get_db),
) -> UsuarioAutenticado:
    ...
```

**Patrón incorrecto visto en la práctica:**
```python
def obtener_usuario_actual(
    credenciales: HTTPAuthorizationCredentials = HTTPBearer(),  # falta Depends()
    db: Session = get_db,                                       # falta Depends()
) -> UsuarioAutenticado:
    ...
```
`db: Session = get_db` asigna la función misma como default en vez de
envolverla en `Depends(get_db)`. El módulo importa sin error, y un test que
llama `obtener_usuario_actual(...)` directo (monkeypatcheando todo) también
pasa — FastAPI recién intenta resolver los defaults como parte del árbol de
dependencias cuando el endpoint se registra en una `APIRouter`/`FastAPI` de
verdad. Ahí explota con
`fastapi.exceptions.FastAPIError: Invalid args for response field!`
mencionando el tipo anotado (ej. `sqlalchemy.orm.session.Session`), porque
sin `Depends()` FastAPI trata el parámetro como un campo de request/response
normal, y un tipo no-Pydantic ahí es inválido.

**Por qué se coló hasta un item tan tarde (`COAS-CORE-003`):** el item que
define esta función (`COAS-AUTH-003`) y el que la usa como router
(`COAS-AUTH-004`) tienen smoke tests que llaman las funciones directo desde
Python (`monkeypatch` de las dependencias del repository), nunca montan un
`APIRouter`/`FastAPI` real — así que ninguno de los dos ejercitó el camino
que rompe. Recién `COAS-CORE-003` (el item que arma `main.py` y monta todos
los routers de verdad) lo detectó. Para atraparlo antes: un smoke test que
monte el router en un `APIRouter`/`TestClient` mínimo, no solo que llame las
funciones sueltas.

**Encontrado en:** `web-portal-coas-migrado`, `COAS-CORE-003`, 2026-08-21 —
bloqueó el ensamblado de `main.py`, rechazado incluso tras escalar a
`executor_senior` (el bug vivía en `COAS-AUTH-003`, no en el item que
fallaba).

## `fastapi run` (el comando CLI) necesita `fastapi[standard]`/`fastapi-cli` instalado — `fastapi` + `uvicorn` planos NO alcanzan

**Verificado:** 2026-08-27, contra un `docker build` real (no un supuesto),
`fastapi==0.141.1` + `uvicorn==0.52.4` (sin extras) instalados vía
`requirements.txt` armado con `pip freeze` real (`pip freeze` nunca
preserva marcadores de extras como `[standard]`, aunque el venv original sí
los haya tenido instalados en algún momento — no es un indicador confiable
de que la extra esté presente).

**Síntoma real:** el contenedor bootea, `pip install` no tira ningún error,
pero al ejecutar el `CMD` de un `Dockerfile.Python` estándar
(`CMD ["fastapi", "run", "app/main.py", ...]`) el proceso muere apenas
arranca:
```
RuntimeError: To use the fastapi command, please install "fastapi[standard]":

	pip install "fastapi[standard]"
```

**Patrón correcto cuando `requirements.txt` NO tiene `fastapi[standard]`/
`fastapi-cli`** (no asumir que agregarlo es gratis — puede no ser una
opción si el `requirements.txt` es de un item ya `completado` fuera de
alcance): usar `uvicorn` DIRECTO en el `CMD`, sin pasar por el wrapper CLI
de FastAPI —`uvicorn` plano (sin `[standard]`) sí alcanza para esto:
```dockerfile
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
```

**Antes de escribir `detalle_tecnico` de un item que arma un `Dockerfile`
con `CMD ["fastapi", "run", ...]`:** verificar si el `requirements.txt` REAL
del deployable tiene `fastapi[standard]`/`fastapi-cli` (`grep -i
"fastapi-cli\|standard" requirements.txt`, o revisar si el proyecto de
referencia que se está copiando —ej. otra migración ya hecha— lo tiene). Si
no lo tiene, usar `uvicorn` directo en vez de copiar el `CMD` del template a
ciegas.

**Encontrado en:** `tesoreria-migrado`, `DEPLOY-BACKEND-001`/
`DEPLOY-DAL-001`, 2026-08-27 — a diferencia de `Web_coas`
(`fastapi[standard]==0.115.6`/`uvicorn[standard]==0.34.0`, `fastapi run`
funciona ahí sin problema), este proyecto nunca tuvo la extra instalada.
