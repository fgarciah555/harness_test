"""
Smoke test: corre pytest de verdad contra el código que Executor generó,
usando los tests que el Planner ya escribió en plan.json (`tests_requeridos`)
-- no los inventa un LLM sobre la marcha, evita "corregir el propio examen".
No es un agente, no pasa por AgentFileGuard ni permissions.yaml, misma
categoría que format_check.py/api_endpoints.py. Ver schemas/plan.contract.md,
sección "Smoke test".

Aislamiento: mínimo por ahora -- timeout al proceso. Nada de tocar la base
de datos real es responsabilidad del contenido del test (usar
get_settings(env_path=".env.pytest") o el equivalente del proyecto), este
módulo no lo fuerza. No hay sandboxing real de proceso/SO -- ver
docs/pendientes.md, "Sandboxing real".
"""
import subprocess
from pathlib import Path

TIMEOUT_SEGUNDOS = 60


def _carpeta_deployable(item: dict) -> str:
    """
    Carpeta del deployable al que pertenece este item -- primer segmento de su
    propio archivos_destino[0] (ej. "backend", "dal"), no un valor fijo. Permite
    que un plan con varios deployables backend (ej. backend/ + dal/, ver
    docs/pendientes.md "Tres flujos de arquitectura") corra smoke test en el venv y
    directorio de cada uno, no siempre el mismo. Sin archivos_destino (fixtures
    de test viejos), cae a "backend" -- comportamiento previo, sin romper nada.
    """
    archivos = item.get("archivos_destino") or []
    return archivos[0].split("/")[0] if archivos else "backend"


def _venvs_candidatos(carpeta: str) -> tuple[str, ...]:
    return (f"{carpeta}/venv/bin/python", "venv/bin/python", ".venv/bin/python")


def _venv_python(project_root: Path, carpeta: str) -> Path | None:
    for candidato in _venvs_candidatos(carpeta):
        ruta = project_root / candidato
        if ruta.exists():
            return ruta
    return None


def correr(project_root: str, item: dict, python: Path | None = None) -> dict:
    """
    Devuelve uno de:
      {"estado": "sin_tests"}                       -- el item no declara tests_requeridos
      {"estado": "paso"}                             -- pytest corrió y todo pasó
      {"estado": "fallo", "detalle": "<stdout+stderr>"}   -- pytest corrió, algo falló
      {"estado": "error", "detalle": "..."}          -- no se pudo ni correr (venv no
                                                          encontrado, timeout)

    `python`, si se pasa, fuerza el intérprete a usar en vez de autodetectar
    un venv del proyecto destino -- pensado para tests de este módulo.
    """
    tests = item.get("tests_requeridos", [])
    if not tests:
        return {"estado": "sin_tests"}

    root = Path(project_root).resolve()
    carpeta = _carpeta_deployable(item)
    interprete = python or _venv_python(root, carpeta)
    if interprete is None:
        return {
            "estado": "error",
            "detalle": (
                f"No se encontró un venv del proyecto destino ({', '.join(_venvs_candidatos(carpeta))}) "
                "-- no se puede correr pytest. Instalar pytest ahí antes de declarar "
                "tests_requeridos para items de este proyecto."
            ),
        }

    rutas_test = []
    for t in tests:
        destino = root / t["archivo"]
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_text(t["contenido"])
        rutas_test.append(str(destino))

    directorio_trabajo = root / carpeta if (root / carpeta).exists() else root

    try:
        resultado = subprocess.run(
            [str(interprete), "-m", "pytest", "-q", *rutas_test],
            cwd=directorio_trabajo,
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SEGUNDOS,
        )
    except subprocess.TimeoutExpired:
        return {
            "estado": "error",
            "detalle": f"pytest no terminó en {TIMEOUT_SEGUNDOS}s -- posible loop/deadlock en el código generado.",
        }
    except OSError as e:
        return {"estado": "error", "detalle": f"no se pudo ejecutar pytest: {e}"}

    if resultado.returncode == 0:
        return {"estado": "paso"}
    return {"estado": "fallo", "detalle": (resultado.stdout + resultado.stderr)[-4000:]}
