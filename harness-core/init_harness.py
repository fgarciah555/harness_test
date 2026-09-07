"""
Inicializa la estructura .harness/ dentro de un proyecto, sin tocar nada
del código del proyecto en sí.

Uso: python init_harness.py /ruta/al/proyecto
"""
import json
import sys
from pathlib import Path


def init_harness(project_root: str):
    root = Path(project_root).resolve()
    if not root.exists():
        raise FileNotFoundError(f"El proyecto '{root}' no existe")

    harness_dir = root / ".harness"
    for sub in ("config", "logs", "validation", "interfaces"):
        (harness_dir / sub).mkdir(parents=True, exist_ok=True)

    gitignore = harness_dir / ".gitignore"
    if not gitignore.exists():
        # logs, validation e interfaces son artefactos de corrida, no
        # versionar por defecto. config/ (plan.json) sí puede valer la pena
        # versionarlo -> se deja fuera del ignore, igual que handoff.md.
        gitignore.write_text("logs/\nvalidation/\ninterfaces/\n")

    handoff = harness_dir / "handoff.md"
    if not handoff.exists():
        # Bitácora PROPIA de este proyecto -- nunca la migración de otro
        # proyecto ni el mecanismo del harness en sí (eso vive en
        # Harness/handoff.md, el repo del harness, no acá). Regla (ver
        # Harness/handoff.md, "Ticket de reintento", 2026-08-30):
        # bug/decisión del HARNESS (orchestrator.py, agents/*, checks/*,
        # engines/*) -> Harness/handoff.md, aunque se haya encontrado
        # migrando este proyecto. Bug de negocio/UI/decisión propia de
        # ESTE proyecto -> acá.
        #
        # Nombre del título: si ya existe plan.json (init_harness corrido
        # de nuevo sobre un proyecto en marcha, ver oms-srv-dal-delivery-
        # configuration, 2026-08-30), usar metadata.proyecto de ahí -- el
        # nombre de la carpeta puede ser genérico (ej. "destino") y no
        # decir nada del proyecto real.
        nombre_proyecto = root.name
        plan_path = harness_dir / "config" / "plan.json"
        if plan_path.exists():
            try:
                nombre_proyecto = json.loads(plan_path.read_text())["metadata"]["proyecto"]
            except (json.JSONDecodeError, KeyError):
                pass
        handoff.write_text(
            f"# Handoff — {nombre_proyecto}\n\n"
            "Bitácora de esta migración/proyecto puntual: bugs de negocio, "
            "decisiones de UI, riesgos heredados del monolito origen, "
            "rondas de rediseño -- todo lo que es específico de este "
            "proyecto, no del harness en sí.\n\n"
            "Un bug o mejora del HARNESS (orchestrator.py, agents/*, "
            "checks/*, engines/*, schemas/plan.contract.md) encontrado "
            "migrando este proyecto va en `Harness/handoff.md` (el repo del "
            "harness), no acá, aunque la evidencia haya salido de acá.\n"
        )

    print(f".harness/ inicializado en {root}")
    print(f"  - {harness_dir / 'config'}")
    print(f"  - {harness_dir / 'logs'}")
    print(f"  - {harness_dir / 'validation'}")
    print(f"  - {harness_dir / 'interfaces'}")
    print(f"  - {handoff}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Uso: python init_harness.py /ruta/al/proyecto")
        sys.exit(1)
    init_harness(sys.argv[1])