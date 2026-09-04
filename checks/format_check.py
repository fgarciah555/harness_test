"""
Format check: chequeo determinístico y gratis (sin LLM) del código que
Executor acaba de generar, antes de gastar una llamada a Compliance. No
ejecuta nada del código generado — solo análisis estático (ast) — así que no
necesita las dependencias del proyecto instaladas ni toca ninguna base de
datos. Es la primera pieza de "Format check" del diseño original del
harness (ver handoff.md), acotada a lo que encontramos en la práctica
migrando web-portal-coas: imports internos rotos y nombres que se pisan.

No es un agente — no pasa por AgentFileGuard, igual que orchestrator.py.

Cuatro chequeos:
1. Todo import interno del proyecto (from app.x.y import ... / import app.x.y)
   resuelve a un archivo real — atrapa el bug de tratar un módulo plano
   (app/api/v1/auth.py) como si fuera un paquete (app/api/v1/auth/__init__.py).
2. Cuando el módulo SÍ resuelve, cada nombre importado (`from app.x.y import
   NOMBRE`) existe de verdad ahí adentro (función, clase, variable de nivel
   superior, o algo que ese módulo a su vez re-exporta) — atrapa el bug de
   importar una clase/función que nunca existió en ese archivo (ej. `from
   app.repository.usuario_repository import UsuarioRepository` cuando ese
   módulo en realidad expone funciones sueltas, no una clase con ese
   nombre). Encontrado en la práctica 4 veces migrando web-portal-coas —
   el chequeo (1) por sí solo no lo atrapaba porque el módulo sí existía,
   solo el nombre adentro estaba mal.
3. Ninguna función/clase definida en el archivo pisa un nombre ya importado
   — atrapa el bug de una función de router con el mismo nombre que la
   función del service que llama (recursión infinita al primer request).
4. Ningún import (interno o de librería) queda sin usar — vía `ruff`
   (regla F401), no un detector casero: los casos borde reales (re-exports
   vía `__all__`, imports bajo `TYPE_CHECKING`, star imports) ya los
   resuelve bien una herramienta madura, y un falso positivo acá rechazaría
   código bueno y quemaría un reintento de Executor por nada. `ruff` es
   dependencia del propio Harness (`requirements.txt`), no del proyecto
   destino — análisis puramente estático, no necesita el venv del proyecto
   ni sus dependencias instaladas.

Deliberadamente NO valida que los imports de librerías de terceros
(fastapi, sqlalchemy, etc.) resuelvan a un paquete instalado — eso sí
requeriría las dependencias del proyecto destino, que es una capacidad que
el harness no tiene hoy (ver Pendientes.md, "Smoke test real", para la
versión que sí ejecuta código).
"""
import ast
import json
import subprocess
import sys
from pathlib import Path

RUFF_TIMEOUT_SEGUNDOS = 30


def _paquete_root(ruta_archivo: Path, project_root: Path) -> Path:
    """
    Dado <project_root>/backend/app/api/v1/auth.py, devuelve
    <project_root>/backend/ — todo lo que está antes del primer 'app/' en la
    ruta del archivo. No asume que el proyecto usa una carpeta 'backend/';
    lo deriva de la ruta real de cada archivo.

    Si el archivo mismo no vive bajo 'app/' (ej. backend/tests/test_x.py o
    backend/alembic/env.py -- hermanos de app/, no contenidos en ella, pero
    que igual importan app.*), se sube por los directorios ancestros del
    archivo buscando el primero que tenga un 'app/' como hijo directo. Sin
    esto, un archivo de test o de infraestructura (alembic/env.py) que
    importa app.core.config se marcaba como "no resuelve" aunque el módulo
    sí existiera -- encontrado en la práctica migrando Tesorería, 2026-08-26.
    """
    rel = ruta_archivo.relative_to(project_root)
    partes = rel.parts
    if "app" in partes:
        idx = partes.index("app")
        return project_root.joinpath(*partes[:idx])

    actual = ruta_archivo.parent
    while True:
        if (actual / "app").is_dir():
            return actual
        if actual == project_root or actual == actual.parent:
            break
        actual = actual.parent
    return project_root


def _ruta_modulo_interno(modulo: str, paquete_root: Path) -> Path | None:
    """Devuelve la ruta real del módulo si resuelve a un archivo, o None si no."""
    ruta_base = paquete_root.joinpath(*modulo.split("."))
    archivo = ruta_base.with_suffix(".py")
    if archivo.exists():
        return archivo
    init = ruta_base / "__init__.py"
    if init.exists():
        return init
    return None


def _nombres_definidos(ruta_modulo: Path) -> set[str] | None:
    """
    Nombres de nivel superior que un módulo expone: funciones, clases,
    variables asignadas, y lo que el propio módulo re-exporta vía sus
    propios imports (from X import Y hace que Y también sea válido
    importarlo desde este módulo). Devuelve None si no se pudo parsear
    (error de sintaxis, ya reportado aparte).
    """
    try:
        arbol = ast.parse(ruta_modulo.read_text(), filename=str(ruta_modulo))
    except SyntaxError:
        return None

    nombres: set[str] = set()
    for nodo in arbol.body:
        if isinstance(nodo, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            nombres.add(nodo.name)
        elif isinstance(nodo, ast.Assign):
            for target in nodo.targets:
                if isinstance(target, ast.Name):
                    nombres.add(target.id)
        elif isinstance(nodo, ast.AnnAssign) and isinstance(nodo.target, ast.Name):
            nombres.add(nodo.target.id)
        elif isinstance(nodo, (ast.Import, ast.ImportFrom)):
            for alias in nodo.names:
                nombres.add(alias.asname or alias.name.split(".")[0])
    return nombres


def _chequear_imports_no_usados(ruta: Path) -> list[str]:
    """Imports (internos o de terceros) que ruff (F401) marca como no usados."""
    try:
        resultado = subprocess.run(
            [sys.executable, "-m", "ruff", "check", "--select", "F401",
             "--output-format=json", str(ruta)],
            capture_output=True, text=True, timeout=RUFF_TIMEOUT_SEGUNDOS,
        )
    except subprocess.TimeoutExpired:
        return [f"{ruta.name}: ruff no terminó en {RUFF_TIMEOUT_SEGUNDOS}s al chequear imports no usados"]
    except OSError as e:
        return [f"{ruta.name}: no se pudo ejecutar ruff para chequear imports no usados: {e}"]

    if not resultado.stdout.strip():
        return []
    try:
        violaciones = json.loads(resultado.stdout)
    except json.JSONDecodeError:
        return [f"{ruta.name}: ruff devolvió una salida inesperada al chequear imports no usados"]

    return [
        f"{ruta.name}:{v['location']['row']}: {v['message']} (import no usado, ruff F401)"
        for v in violaciones
    ]


def _chequear_archivo(ruta: Path, project_root: Path) -> list[str]:
    try:
        texto = ruta.read_text()
        arbol = ast.parse(texto, filename=str(ruta))
    except SyntaxError as e:
        return [f"{ruta.name}: error de sintaxis: {e}"]

    paquete_root = _paquete_root(ruta, project_root)
    errores = []
    nombres_importados: dict[str, str] = {}  # nombre local -> módulo de origen

    for nodo in ast.walk(arbol):
        if isinstance(nodo, ast.ImportFrom) and nodo.module and nodo.module.startswith("app."):
            ruta_modulo = _ruta_modulo_interno(nodo.module, paquete_root)
            if ruta_modulo is None:
                errores.append(
                    f"{ruta.name}: 'from {nodo.module} import ...' no resuelve a ningún archivo "
                    f"del proyecto (¿el módulo real es un archivo plano, no un paquete? ¿nombre "
                    f"distinto?)"
                )
            else:
                nombres_definidos = _nombres_definidos(ruta_modulo)
                if nombres_definidos is not None:
                    for alias in nodo.names:
                        if alias.name != "*" and alias.name not in nombres_definidos:
                            errores.append(
                                f"{ruta.name}: 'from {nodo.module} import {alias.name}' -- "
                                f"'{alias.name}' no está definido en {ruta_modulo.name} (¿nombre "
                                f"distinto, o es una clase/función que en realidad no existe ahí?)"
                            )
            for alias in nodo.names:
                nombres_importados[alias.asname or alias.name] = nodo.module

        elif isinstance(nodo, ast.Import):
            for alias in nodo.names:
                if alias.name.startswith("app.") and _ruta_modulo_interno(alias.name, paquete_root) is None:
                    errores.append(f"{ruta.name}: 'import {alias.name}' no resuelve a ningún archivo del proyecto")

    for nodo in arbol.body:
        nombre = getattr(nodo, "name", None) if isinstance(
            nodo, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
        ) else None
        if nombre and nombre in nombres_importados:
            errores.append(
                f"{ruta.name}: define '{nombre}' pero ese nombre ya estaba importado de "
                f"'{nombres_importados[nombre]}' — la definición pisa al import; cualquier código "
                f"que intente llamar a lo importado va a terminar llamando a esta definición local "
                f"en su lugar (riesgo de recursión infinita si el nombre pisado se invoca adentro)"
            )

    errores.extend(_chequear_imports_no_usados(ruta))
    return errores


def verificar(project_root: str, archivos: list[str]) -> list[str]:
    """
    Corre sobre los archivos que un item acaba de generar (rutas relativas a
    project_root, tal como aparecen en archivos_destino). Devuelve la lista
    de errores encontrados — vacía si no encontró nada.
    """
    root = Path(project_root).resolve()
    errores: list[str] = []
    for rel in archivos:
        ruta = root / rel
        if ruta.suffix == ".py" and ruta.exists():
            errores.extend(_chequear_archivo(ruta, root))
    return errores
