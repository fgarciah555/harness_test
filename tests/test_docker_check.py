"""
docker_check.verificar() no debe exigir Docker disponible para un item
tipo:"infra" cuyo archivos_destino no incluye ningún Dockerfile/
docker-compose.yml (ej. un script de arranque local) -- bug real encontrado
en Tesorería 2026-08-31, ver Harness/handoff.md.
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from checks import docker_check


def test_item_sin_dockerfile_ni_compose_no_requiere_docker():
    item = {
        "id": "DEPLOY-LOCAL-START-001",
        "tipo": "infra",
        "archivos_destino": ["start-local.sh"],
    }
    with tempfile.TemporaryDirectory() as tmp:
        resultado = docker_check.verificar(tmp, item)
    assert resultado == {"estado": "ok"}


def test_item_con_dockerfile_si_exige_docker_disponible(monkeypatch):
    monkeypatch.setattr(
        docker_check, "_docker_disponible", lambda: (False, "docker no encontrado")
    )
    item = {
        "id": "DEPLOY-DAL-001",
        "tipo": "infra",
        "archivos_destino": ["dal/Dockerfile"],
    }
    with tempfile.TemporaryDirectory() as tmp:
        resultado = docker_check.verificar(tmp, item)
    assert resultado["estado"] == "motor_inalcanzable"


if __name__ == "__main__":
    test_item_sin_dockerfile_ni_compose_no_requiere_docker()
    print("OK: item sin Dockerfile/compose no exige Docker disponible")
