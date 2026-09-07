"""
Frontend check: compila el proyecto Angular real (`ng build`) para detectar
errores de TypeScript/template ANTES de gastar una llamada a Compliance --
equivalente determinístico y gratis (sin LLM) a format_check.py + smoke_test.py,
pero para items `tipo: "frontend"`. No es un agente, no pasa por AgentFileGuard
ni permissions.yaml, misma categoría que los otros checks. Ver
schemas/plan.contract.md, sección "Frontend check".

Requiere Node/npm y las dependencias del proyecto Angular ya instaladas (`ng
new` + `npm install` ya corridos en <project_root>/frontend) -- este módulo no
instala nada, reporta un error claro si no encuentra el proyecto o el binario
de npx.
"""
import os
import shutil
import subprocess
from pathlib import Path

TIMEOUT_SEGUNDOS = 180


def _proyecto_angular(project_root: Path) -> Path | None:
    candidato = project_root / "frontend"
    return candidato if (candidato / "angular.json").exists() else None


def _node_bin_dir() -> Path | None:
    """
    Devuelve el directorio que tiene node/npx -- el que está en PATH, o
    (fallback) el de la versión más nueva instalada vía nvm en ~/.nvm.
    Hace falta el directorio completo, no solo la ruta a npx: npx invoca
    `node` internamente vía shebang `#!/usr/bin/env node`, que necesita
    encontrar `node` en el PATH del propio subprocess -- no alcanza con
    pasarle la ruta absoluta a npx nada más (bug real: "env: node: No such
    file or directory" corriendo esto desde orchestrator.py sin nvm
    sourceado en el shell).
    """
    en_path = shutil.which("npx")
    if en_path:
        return Path(en_path).parent

    nvm_dir = Path.home() / ".nvm" / "versions" / "node"
    if not nvm_dir.exists():
        return None
    for version_dir in sorted(nvm_dir.iterdir(), reverse=True):
        candidato = version_dir / "bin"
        if (candidato / "npx").exists():
            return candidato
    return None


def verificar(project_root: str) -> dict:
    """
    Devuelve {"estado": "ok"} si el proyecto Angular compila, o
    {"estado": "error", "detalle": "..."} si no compila o no se pudo correr
    (proyecto no encontrado, npx no disponible, timeout).
    """
    root = Path(project_root).resolve()
    proyecto = _proyecto_angular(root)
    if proyecto is None:
        return {
            "estado": "error",
            "detalle": (
                f"No se encontró un proyecto Angular en {root / 'frontend'} "
                "(sin angular.json) -- no se puede compilar. Bootstrapear el "
                "proyecto (`ng new`) antes de correr items tipo frontend."
            ),
        }

    bin_dir = _node_bin_dir()
    if bin_dir is None:
        return {
            "estado": "error",
            "detalle": "No se encontró Node/npx (ni en PATH ni en ~/.nvm) -- instalar Node.js antes de correr items tipo frontend.",
        }
    env = {**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}"}

    try:
        resultado = subprocess.run(
            ["npx", "ng", "build", "--configuration", "development"],
            cwd=proyecto,
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SEGUNDOS,
            env=env,
        )
    except subprocess.TimeoutExpired:
        return {
            "estado": "error",
            "detalle": f"ng build no terminó en {TIMEOUT_SEGUNDOS}s -- posible problema de red (descarga de paquetes) o de configuración.",
        }
    except OSError as e:
        return {"estado": "error", "detalle": f"no se pudo ejecutar ng build: {e}"}

    if resultado.returncode == 0:
        return {"estado": "ok"}
    return {"estado": "error", "detalle": (resultado.stdout + resultado.stderr)[-4000:]}
