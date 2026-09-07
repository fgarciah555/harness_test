"""
Prueba manual de frontend_check.py contra un proyecto Angular real -- necesita
Node/npm instalados y el proyecto ya bootstrapeado (`ng new`) con dependencias
instaladas. No entra en test_executor_logic.py (que es "sin red") por el mismo
motivo que test_engines.py tampoco entra: depende de una herramienta externa
que no siempre está disponible.

Uso: python tests/test_frontend_check.py /ruta/al/proyecto-destino
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from checks import frontend_check


def test_reporta_error_sin_proyecto_angular():
    with tempfile.TemporaryDirectory() as tmp:
        resultado = frontend_check.verificar(tmp)
        assert resultado["estado"] == "error"
        assert "angular.json" in resultado["detalle"]
    print("OK: frontend_check reporta error claro si no hay proyecto Angular")


def test_contra_proyecto_real(project_root: str):
    resultado = frontend_check.verificar(project_root)
    print(f"estado: {resultado['estado']}")
    if resultado["estado"] == "error":
        print(resultado["detalle"][-1000:])
    assert resultado["estado"] == "ok", "el proyecto real debería compilar limpio"
    print("OK: frontend_check compila el proyecto real sin errores")


if __name__ == "__main__":
    test_reporta_error_sin_proyecto_angular()
    if len(sys.argv) > 1:
        test_contra_proyecto_real(sys.argv[1])
    else:
        print("(sin ruta de proyecto real pasada por argumento -- se omite ese test)")
