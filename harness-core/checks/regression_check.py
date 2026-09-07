"""
Regression check: chequeo determinístico (sin LLM), exclusivo del flujo de
mantención -- ver schemas/plan.contract.md, "Los 3 flujos". Corre la suite
de tests que el deployable YA TENÍA antes de este item, no solo los
`tests_requeridos` que el propio item declara (eso lo cubre smoke_test.py,
y sigue corriendo igual en mantención).

Motivo: en mantención "más laxo no es menos riguroso" -- el scope de un
item es más chico (menos archivos, menos pasos), pero el nivel de exigencia
se mantiene. Migración/creación parten de una base controlada o de cero, sin
tests previos que puedan romperse por un cambio en otro lugar del mismo
deployable; mantención toca un proyecto ya grande donde ese riesgo es real
y el blast radius de un cambio chico no es obvio a simple vista.

No es un agente, no pasa por AgentFileGuard, misma categoría que
smoke_test.py/format_check.py. Reusa la resolución de venv de
smoke_test.py (mismo criterio de carpeta por deployable) -- no duplica esa
lógica.

Alcance v1: solo backend/pytest. Regresión de frontend (`ng test`) queda
sin implementar por falta de caso real todavía -- mismo criterio que el
harness ya aplica a otras piezas sin evidencia (ver Pendientes.md); no se
construye a ciegas.
"""
import subprocess
from pathlib import Path

from checks.smoke_test import _carpeta_deployable, _venv_python, _venvs_candidatos

TIMEOUT_SEGUNDOS = 300  # una suite completa tarda más que los tests de un solo item
                        # (smoke_test.TIMEOUT_SEGUNDOS=60) -- valor calibrable, no
                        # sagrado, mismo criterio que smoke_test.py.

_PYTEST_SIN_TESTS_RECOLECTADOS = 5


def correr(project_root: str, item: dict, python: Path | None = None) -> dict:
    """
    Devuelve uno de:
      {"estado": "sin_tests"}                      -- el deployable no tiene ningún test
      {"estado": "paso"}                            -- pytest corrió y todo pasó
      {"estado": "fallo", "detalle": "<stdout+stderr>"}  -- algo de la suite existente falló
      {"estado": "error", "detalle": "..."}         -- no se pudo ni correr (venv no
                                                         encontrado, timeout)

    A diferencia de smoke_test.correr(), no depende de `tests_requeridos`
    del item -- corre TODA la suite del deployable (carpeta completa), sin
    especificar archivos puntuales.

    `python`, si se pasa, fuerza el intérprete a usar en vez de
    autodetectar un venv del proyecto destino -- pensado para tests de
    este módulo.
    """
    root = Path(project_root).resolve()
    carpeta = _carpeta_deployable(item)
    interprete = python or _venv_python(root, carpeta)
    if interprete is None:
        return {
            "estado": "error",
            "detalle": (
                f"No se encontró un venv del proyecto destino ({', '.join(_venvs_candidatos(carpeta))}) "
                "-- no se puede correr la suite de regresión."
            ),
        }

    directorio_trabajo = root / carpeta if (root / carpeta).exists() else root

    try:
        resultado = subprocess.run(
            [str(interprete), "-m", "pytest", "-q"],
            cwd=directorio_trabajo,
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SEGUNDOS,
        )
    except subprocess.TimeoutExpired:
        return {
            "estado": "error",
            "detalle": f"la suite de regresión no terminó en {TIMEOUT_SEGUNDOS}s.",
        }
    except OSError as e:
        return {"estado": "error", "detalle": f"no se pudo ejecutar pytest: {e}"}

    if resultado.returncode == _PYTEST_SIN_TESTS_RECOLECTADOS:
        return {"estado": "sin_tests"}
    if resultado.returncode == 0:
        return {"estado": "paso"}
    return {"estado": "fallo", "detalle": (resultado.stdout + resultado.stderr)[-4000:]}
