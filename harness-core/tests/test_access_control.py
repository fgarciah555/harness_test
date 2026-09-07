"""
AgentFileGuard.write() debe dejar cualquier `.sh` que escribe con el bit de
ejecución puesto (equivalente a `chmod +x`) -- sin esto, un item de
plan.json que pide un script ejecutable siempre falla ese criterio en
Compliance, porque un LLM que solo lee texto no puede setear ni ver el bit
de permisos. Bug real encontrado agregando un item de infraestructura con
script bash, 2026-08-31, ver Harness/docs/handoff.md.
"""
import os
import stat
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from access_control import AgentFileGuard, Zona


def test_write_sh_queda_ejecutable():
    with tempfile.TemporaryDirectory() as tmp:
        guard = AgentFileGuard("executor", project_root=tmp)
        guard.write(Zona.PROJECT, "start-local.sh", "#!/bin/bash\necho hola\n")
        ruta = Path(tmp) / "start-local.sh"
        modo = ruta.stat().st_mode
        assert modo & stat.S_IXUSR, "el owner debería poder ejecutar el .sh escrito"


def test_write_py_no_queda_ejecutable():
    with tempfile.TemporaryDirectory() as tmp:
        guard = AgentFileGuard("executor", project_root=tmp)
        guard.write(Zona.PROJECT, "app/main.py", "print('hola')\n")
        ruta = Path(tmp) / "app" / "main.py"
        modo = ruta.stat().st_mode
        assert not (modo & stat.S_IXUSR), "un .py no debería quedar ejecutable solo por escribirlo"


if __name__ == "__main__":
    test_write_sh_queda_ejecutable()
    test_write_py_no_queda_ejecutable()
    print("OK: write() deja los .sh ejecutables y no toca el resto")
