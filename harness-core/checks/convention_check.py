"""
Convention check: chequeo determinístico (sin LLM), exclusivo del flujo de
mantención -- ver schemas/plan.contract.md, "Los 3 flujos". En creación/
migración la convención de código es fija, la define el harness (el
Planner la traduce a decisiones_globales/criterios_aceptacion desde
.agents/rules/naming-conventions.md del proyecto destino). En mantención el
criterio es relativo: si el archivo/módulo que el item toca ya está en
camelCase, lo nuevo que Executor agregue también debe quedar en camelCase
-- no la convención por defecto del harness.

No ejecuta nada del código generado (igual que format_check.py) -- solo
`ast` sobre el código y `git show` para reconstruir la versión anterior del
archivo.

Requiere que el proyecto destino sea un repo git ya trackeado -- supuesto
explícito de mantención: se está tocando código que ya existe y ya está
versionado. Si un archivo de `archivos_destino` no está trackeado en HEAD
(es nuevo, no una modificación), se salta el chequeo relativo para ese
archivo -- no hay convención previa que heredar.

Alcance v1 (acordado explícitamente, ver Pendientes.md si se quiere
ampliar): solo casing de identificadores (snake_case / camelCase /
PascalCase) de funciones y variables de NIVEL SUPERIOR. Se excluyen las
clases de la muestra de detección -- casi siempre PascalCase universal en
cualquier convención, mezclarlas sesgaría la detección de la convención
dominante real (la de funciones/variables). No cubre estilo de imports,
docstrings, indentación, etc.
"""
import ast
import re
import subprocess
from pathlib import Path

_RE_SNAKE = re.compile(r"^[a-z_][a-z0-9_]*$")
_RE_CAMEL = re.compile(r"^[a-z][a-zA-Z0-9]*$")
_RE_PASCAL = re.compile(r"^[A-Z][a-zA-Z0-9]*$")

_MINIMO_IDENTIFICADORES_PARA_DETECTAR = 3


def _clasificar_casing(nombre: str) -> str | None:
    """
    'snake_case' | 'camelCase' | 'PascalCase' | 'ambiguo_una_palabra' (una
    sola palabra en minúsculas, ej. 'main' -- compatible con snake_case y
    camelCase a la vez, no aporta señal para detectar la dominante ni
    puede violar ninguna de las dos) | None (no clasificable, ej.
    SCREAMING_SNAKE_CASE o algo con guiones bajos Y mayúsculas mezclados).
    """
    if "_" in nombre:
        return "snake_case" if _RE_SNAKE.match(nombre) else None
    if _RE_PASCAL.match(nombre):
        return "PascalCase"
    if _RE_CAMEL.match(nombre):
        return "camelCase" if any(c.isupper() for c in nombre) else "ambiguo_una_palabra"
    return None


def _nombres_funciones_y_variables(codigo: str) -> set[str] | None:
    """
    Nombres de funciones y variables de NIVEL SUPERIOR -- excluye clases
    (ver docstring del módulo) y todo lo anidado dentro de funciones/clases,
    mismo criterio de "forma pública del módulo" que
    format_check._nombres_definidos. None si el código no parsea.
    """
    try:
        arbol = ast.parse(codigo)
    except SyntaxError:
        return None

    nombres: set[str] = set()
    for nodo in arbol.body:
        if isinstance(nodo, (ast.FunctionDef, ast.AsyncFunctionDef)):
            nombres.add(nodo.name)
        elif isinstance(nodo, ast.Assign):
            for target in nodo.targets:
                if isinstance(target, ast.Name):
                    nombres.add(target.id)
        elif isinstance(nodo, ast.AnnAssign) and isinstance(nodo.target, ast.Name):
            nombres.add(nodo.target.id)
    return nombres


def _convencion_dominante(nombres: set[str]) -> str | None:
    """
    El casing que más se repite entre los nombres clasificables. None si
    hay menos de _MINIMO_IDENTIFICADORES_PARA_DETECTAR clasificables, o si
    hay empate real -- en ambos casos no hay base suficiente para exigir
    nada (evita falsos positivos en archivos chicos o mixtos).
    """
    conteo = {"snake_case": 0, "camelCase": 0, "PascalCase": 0}
    for nombre in nombres:
        casing = _clasificar_casing(nombre)
        if casing in conteo:
            conteo[casing] += 1

    if sum(conteo.values()) < _MINIMO_IDENTIFICADORES_PARA_DETECTAR:
        return None

    dominante = max(conteo, key=conteo.get)
    if list(conteo.values()).count(conteo[dominante]) > 1:
        return None
    return dominante


def _contenido_antes(project_root: Path, ruta_relativa: str) -> str | None:
    """Contenido del archivo en HEAD (git), o None si no estaba trackeado
    (archivo nuevo -- no hay convención previa que heredar, o el proyecto
    no es un repo git -- ver docstring del módulo)."""
    resultado = subprocess.run(
        ["git", "show", f"HEAD:{ruta_relativa}"],
        cwd=project_root, capture_output=True, text=True,
    )
    if resultado.returncode != 0:
        return None
    return resultado.stdout


def _chequear_archivo(project_root: Path, ruta_relativa: str) -> list[str]:
    ruta_absoluta = project_root / ruta_relativa
    if not ruta_absoluta.exists() or ruta_absoluta.suffix != ".py":
        return []

    antes = _contenido_antes(project_root, ruta_relativa)
    if antes is None:
        return []  # archivo nuevo (o proyecto sin git) -- no hay convención previa que heredar

    nombres_antes = _nombres_funciones_y_variables(antes)
    nombres_despues = _nombres_funciones_y_variables(ruta_absoluta.read_text())
    if nombres_antes is None or nombres_despues is None:
        return []  # no debería pasar (format_check ya validó sintaxis antes) -- defensivo

    dominante = _convencion_dominante(nombres_antes)
    if dominante is None:
        return []  # sin convención dominante clara -- no se puede exigir nada

    errores = []
    for nombre in sorted(nombres_despues - nombres_antes):
        casing = _clasificar_casing(nombre)
        if casing is None or casing == "ambiguo_una_palabra":
            continue  # no aporta evidencia de violación
        if casing != dominante:
            errores.append(
                f"{ruta_relativa}: '{nombre}' es nuevo en este item y no sigue la convención "
                f"dominante del archivo ({dominante}) -- se detectó como {casing}"
            )
    return errores


def verificar(project_root: str, archivos: list[str]) -> list[str]:
    """
    Corre sobre los archivos que un item de mantención acaba de tocar
    (rutas relativas a project_root, tal como aparecen en
    archivos_destino). Devuelve la lista de errores encontrados -- vacía si
    no hay problemas, o si ninguno de los archivos tenía una convención
    previa detectable.
    """
    root = Path(project_root).resolve()
    errores: list[str] = []
    for ruta_relativa in archivos:
        errores.extend(_chequear_archivo(root, ruta_relativa))
    return errores
