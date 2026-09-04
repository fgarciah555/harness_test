"""
Control de acceso a filesystem por agente.

Principio: los agentes NUNCA usan open()/pathlib directo sobre el proyecto
o el directorio .harness/. Todo pasa por AgentFileGuard, que verifica permisos
contra config/permissions.yaml antes de cada operación. Si un agente intenta
algo fuera de lo permitido, esto lanza PermissionError — no falla en silencio,
no lo "arregla" saltándose la regla.
"""
from pathlib import Path
from enum import Enum

import yaml

_PERMISSIONS_PATH = Path(__file__).parent / "config" / "permissions.yaml"

_ZONA_A_CARPETA = {
    "harness_config": ".harness/config",
    "harness_logs": ".harness/logs",
    "harness_validation": ".harness/validation",
    "harness_interfaces": ".harness/interfaces",
}

_IGNORAR_DIRS = {"node_modules", "venv", ".venv", ".git", "dist", "__pycache__", ".angular", ".harness"}


class Zona(str, Enum):
    PROJECT = "project_dir"
    HARNESS_CONFIG = "harness_config"
    HARNESS_LOGS = "harness_logs"
    HARNESS_VALIDATION = "harness_validation"
    HARNESS_INTERFACES = "harness_interfaces"


class AgentFileGuard:
    """
    Se instancia una vez por agente, ya con sus permisos resueltos.
    Uso:
        guard = AgentFileGuard("executor", project_root="/ruta/al/proyecto")
        contenido = guard.read(Zona.PROJECT, "app/api/pedido.py")
        guard.write(Zona.HARNESS_LOGS, "executor_run_001.log", "...")
    """

    def __init__(self, agent_name: str, project_root: str):
        self.agent_name = agent_name
        self.project_root = Path(project_root).resolve()
        self.harness_root = self.project_root / ".harness"

        config = yaml.safe_load(_PERMISSIONS_PATH.read_text())
        if agent_name not in config["agents"]:
            raise ValueError(f"Agente '{agent_name}' no tiene permisos definidos en permissions.yaml")

        self.permisos = config["agents"][agent_name]

    def _resolver_ruta(self, zona: Zona, relative_path: str) -> Path:
        if zona == Zona.PROJECT:
            base = self.project_root
        else:
            base = self.harness_root / _ZONA_A_CARPETA[zona.value].split("/", 1)[1]

        ruta = (base / relative_path).resolve()

        # Evita que un path relativo tipo "../../otra_cosa" escape del árbol permitido
        if base not in ruta.parents and ruta != base:
            raise PermissionError(
                f"[{self.agent_name}] Ruta '{relative_path}' intenta salir del "
                f"directorio permitido ({base}). Bloqueado."
            )
        return ruta

    def _verificar_permiso(self, zona: Zona, operacion: str):
        nivel = self.permisos.get(zona.value, "none")
        permitido = {
            "read": operacion == "read",
            "write": operacion == "write",
            "read_write": operacion in ("read", "write"),
            "none": False,
        }[nivel]

        if not permitido:
            raise PermissionError(
                f"[{self.agent_name}] no tiene permiso de '{operacion}' sobre "
                f"'{zona.value}' (nivel configurado: '{nivel}'). Ver config/permissions.yaml."
            )

    def read(self, zona: Zona, relative_path: str) -> str:
        self._verificar_permiso(zona, "read")
        ruta = self._resolver_ruta(zona, relative_path)
        return ruta.read_text()

    def write(self, zona: Zona, relative_path: str, content: str):
        self._verificar_permiso(zona, "write")
        ruta = self._resolver_ruta(zona, relative_path)
        ruta.parent.mkdir(parents=True, exist_ok=True)
        ruta.write_text(content)
        if ruta.suffix == ".sh":
            # Equivalente a `chmod +x` (agrega el bit de ejecución sin tocar
            # el resto de los permisos que dejó el umask) -- sin esto, un
            # item de plan.json que pida un script ejecutable siempre falla
            # ese criterio en Compliance (LLM, no puede setear el bit ni
            # verlo desde el contenido del archivo). Encontrado en un item
            # de arranque local (script .sh), 2026-08-31.
            ruta.chmod(ruta.stat().st_mode | 0o111)

    def append_line(self, zona: Zona, relative_path: str, line: str):
        """
        Agrega una línea a un archivo sin leerlo primero (ej. bitácoras
        .jsonl append-only). Solo requiere permiso de 'write', no 'read' —
        a diferencia de write(), nunca pisa lo que ya había en el archivo.
        """
        self._verificar_permiso(zona, "write")
        ruta = self._resolver_ruta(zona, relative_path)
        ruta.parent.mkdir(parents=True, exist_ok=True)
        with ruta.open("a", encoding="utf-8") as f:
            f.write(line.rstrip("\n") + "\n")

    def list_files(self, zona: Zona, pattern: str = "**/*") -> list[str]:
        """
        Lista archivos bajo la zona dada. Excluye siempre directorios de
        dependencias/artefactos generados (node_modules, venv, .git, dist,
        __pycache__, etc.) -- sin esto, un proyecto con un frontend Angular
        real devuelve decenas de miles de archivos de node_modules/, inútil
        para cualquier caller (encontrado armando el contexto de Compliance
        contra un proyecto con node_modules instalado de verdad).
        """
        self._verificar_permiso(zona, "read")
        base = self.project_root if zona == Zona.PROJECT else (
            self.harness_root / _ZONA_A_CARPETA[zona.value].split("/", 1)[1]
        )
        if not base.exists():
            return []
        return [
            str(p.relative_to(base))
            for p in base.glob(pattern)
            if p.is_file() and not _IGNORAR_DIRS & set(p.relative_to(base).parts)
        ]