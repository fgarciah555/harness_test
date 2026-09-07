"""
Lee config/models.yaml y devuelve la instancia de ModelEngine correcta
para un agente dado. Este es el único punto del código que sabe mapear
nombres de motor ("lm_studio", "anthropic") a clases concretas.
"""
import yaml
from pathlib import Path

from .base import ModelEngine
from .lm_studio import LMStudioEngine
from .deepseek_api import DeepSeekEngine
from .kimi_api import KimiEngine
# from .anthropic_api import AnthropicEngine

_ENGINE_REGISTRY = {
    "lm_studio": LMStudioEngine,
    "deepseek": DeepSeekEngine,
    "kimi": KimiEngine,
    # "anthropic": AnthropicEngine,
}

_CONFIG_PATH = Path(__file__).parent.parent / "config" / "models.yaml"

# Overrides de motor por agente, EN MEMORIA únicamente -- nunca se escriben a
# config/models.yaml. Existen para el fallback de motor local caído (ver
# orchestrator.py::_con_fallback_motor_local): el usuario confirma una vez por
# corrida del proceso, get_engine_for_agent empieza a devolver el motor
# alternativo para ese agente sin que nadie edite el yaml a mano, y el
# override desaparece solo al terminar el proceso (o con clear_override, para
# tests). Un override deliberadamente persistente sigue siendo editar
# models.yaml -- este mecanismo es para la sesión de trabajo puntual en la
# que el motor configurado no está disponible.
_overrides: dict[str, dict] = {}


def set_override(agent_name: str, engine_name: str, model: str) -> None:
    _overrides[agent_name] = {"engine": engine_name, "model": model}


def get_override(agent_name: str) -> dict | None:
    return _overrides.get(agent_name)


def clear_override(agent_name: str | None = None) -> None:
    """Sin `agent_name`, limpia todos los overrides -- pensado para tests."""
    if agent_name is None:
        _overrides.clear()
    else:
        _overrides.pop(agent_name, None)


def get_engine_for_agent(agent_name: str) -> ModelEngine:
    config = yaml.safe_load(_CONFIG_PATH.read_text())

    if agent_name not in config["agents"]:
        raise ValueError(f"Agente '{agent_name}' no está definido en models.yaml")

    agent_cfg = {**config["agents"][agent_name], **_overrides.get(agent_name, {})}
    engine_name = agent_cfg["engine"]
    model = agent_cfg["model"]

    if engine_name not in _ENGINE_REGISTRY:
        raise ValueError(f"Motor '{engine_name}' no está registrado en factory.py")

    engine_cls = _ENGINE_REGISTRY[engine_name]
    timeout = agent_cfg.get("timeout_seconds", 120)

    if engine_name == "lm_studio":
        base_url = config["engines"]["lm_studio"]["base_url"]
        return engine_cls(model=model, base_url=base_url, timeout_seconds=timeout)

    return engine_cls(model=model, timeout_seconds=timeout)
