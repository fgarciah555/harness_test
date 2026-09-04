"""
Interfaz común para cualquier motor de inferencia (local o vía API).

El resto del harness (orquestador, agentes) solo conoce esta interfaz.
No le importa si detrás hay LM Studio, Ollama, o la API de Anthropic.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass


class TimeoutDelMotor(RuntimeError):
    """
    El motor de inferencia no respondió dentro del timeout configurado —
    distinto de un RuntimeError genérico (bucle de repetición, se quedó sin
    tokens pensando): esos son casos donde Executor SÍ tiene una respuesta
    pero está mal, y agents/executor.py los trata como 'bloqueado' (no se
    reintentan solos, ver orchestrator.py::seleccionar_siguiente_para_loop).
    Un timeout no es un problema de información/ambigüedad del plan, es un
    corte de infraestructura — agents/executor.py lo trata distinto (como un
    intento fallido sin archivos, no como 'bloqueado') para que el loop
    normal de reintentos/escalado a executor_senior se haga cargo solo.
    """


class MotorInalcanzable(RuntimeError):
    """
    No se pudo establecer conexión con el motor (host inalcanzable, conexión
    rechazada) — distinto de TimeoutDelMotor (sí conectó, pero no respondió a
    tiempo). Semántica: "no hay nada corriendo ahí", típicamente LM Studio
    local apagado o sin red hacia él (ver README.md, "Motor por API (Kimi)").
    A diferencia de TimeoutDelMotor (que agents/executor.py trata como
    intento fallido normal, sin intervención), esto se propaga sin capturar
    hasta orchestrator.py, que es el único lugar con permiso para preguntarle
    al usuario si activar un motor alternativo (engines/factory.py::
    set_override) — ni agents/executor.py ni agents/documentador.py deciden
    eso solos, ambos dejan pasar esta excepción en vez de tratarla como
    'bloqueado'/'error'.
    """


@dataclass
class EngineResponse:
    """Respuesta normalizada, sin importar qué motor la generó."""
    content: str            # texto/JSON crudo devuelto por el modelo
    model: str               # nombre del modelo que realmente respondió
    input_tokens: int | None = None
    output_tokens: int | None = None
    reasoning_tokens: int | None = None  # cuántos de output_tokens fueron "pensando" (reasoning_content), no la respuesta final


class ModelEngine(ABC):
    """Todo motor (LM Studio, Anthropic API, etc.) implementa esto."""

    @abstractmethod
    def run(
        self, system_prompt: str, user_prompt: str, *,
        max_tokens: int = 2000, enable_thinking: bool = True,
    ) -> EngineResponse:
        """
        Ejecuta una llamada de inferencia y devuelve la respuesta normalizada.
        No maneja tools/function-calling en esta v0 — cada agente del pipeline
        de JMeter usa texto/JSON plano como contrato, no tool-calling del modelo.

        `enable_thinking`: solo lo respeta LMStudioEngine (ver esquema de
        reintentos en orchestrator.py::loop() — primer intento sin thinking,
        segundo con thinking normal). Los motores que no soportan
        deshabilitar el razonamiento (ej. DeepSeekEngine con
        deepseek-reasoner) lo ignoran.
        """
        raise NotImplementedError
