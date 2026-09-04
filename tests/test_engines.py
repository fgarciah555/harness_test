"""
Prueba manual: valida que cada agente resuelve al motor correcto
y que ese motor efectivamente responde.

Uso: python tests/test_engines.py analyzer
     python tests/test_engines.py executor --check-thinking   (ver abajo)
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engines.factory import get_engine_for_agent
from engines.lm_studio import LMStudioEngine


def _check_thinking(engine, agent_name: str) -> None:
    """
    Chequeo dedicado a una pregunta puntual: ¿el modelo cargado ahora mismo
    en LM Studio de verdad respeta enable_thinking=False, o sigue razonando
    igual (con el costo de tiempo que eso implica) sin importar el flag?
    Nace de ver timeouts nuevos en reintentos (thinking: on) que antes no
    pasaban tan seguido -- si el "thinking: off" del primer intento tampoco
    estuviera funcionando de verdad, cada llamada normal ya estaría pagando
    ese costo, no solo los reintentos. No asume la respuesta, la mide.

    Compara dos llamadas idénticas salvo por enable_thinking y reporta
    reasoning_tokens + tiempo de cada una -- un modelo/versión de LM Studio
    que ignore el flag va a mostrar reasoning_tokens > 0 y tiempos
    parecidos en ambas, no solo en la que pide pensar.
    """
    if not isinstance(engine, LMStudioEngine):
        print(
            f"'{agent_name}' no usa LM Studio (usa {engine.__class__.__name__}) "
            "-- enable_thinking no aplica, no tiene sentido este chequeo acá."
        )
        return

    prompt = "¿Cuánto es 2 + 2? Respondé solo con el número."
    resultados = {}
    for etiqueta, thinking in (("thinking OFF", False), ("thinking ON", True)):
        inicio = time.monotonic()
        respuesta = engine.run(
            system_prompt="Respondes de forma breve y directa.",
            user_prompt=prompt,
            max_tokens=2000,
            enable_thinking=thinking,
        )
        elapsed = time.monotonic() - inicio
        resultados[thinking] = (respuesta, elapsed)
        print(
            f"[{etiqueta}] {elapsed:.1f}s -- reasoning_tokens="
            f"{respuesta.reasoning_tokens} output_tokens={respuesta.output_tokens} "
            f"contenido={respuesta.content!r}"
        )

    resp_off, t_off = resultados[False]
    resp_on, t_on = resultados[True]
    razono_con_off = (resp_off.reasoning_tokens or 0) > 0

    print()
    if razono_con_off:
        print(
            "SOSPECHA CONFIRMADA: con enable_thinking=False el modelo igual generó "
            f"{resp_off.reasoning_tokens} reasoning_tokens -- no está respetando el flag. "
            "Revisar si este modelo/versión de LM Studio necesita otro parámetro además "
            "de chat_template_kwargs.enable_thinking + reasoning_effort (ver engines/lm_studio.py)."
        )
    elif t_off >= t_on * 0.7:
        print(
            f"SOSPECHOSO: thinking OFF ({t_off:.1f}s) tardó casi lo mismo que thinking ON "
            f"({t_on:.1f}s) pese a reasoning_tokens=0 -- puede ser lentitud del server/red, "
            "no necesariamente el flag ignorado. No es concluyente, repetir el chequeo."
        )
    else:
        print(
            f"OK: thinking OFF no generó reasoning_tokens y fue claramente más rápido "
            f"({t_off:.1f}s vs {t_on:.1f}s) -- el modelo sí está respetando enable_thinking."
        )


def _modelo_override(args: list[str]) -> str | None:
    """--model=<nombre> para probar un modelo puntual sin tocar models.yaml
    (ej. comparar contra un modelo que ya no está configurado para ningún
    agente pero sigue disponible en LM Studio)."""
    for arg in args:
        if arg.startswith("--model="):
            return arg.split("=", 1)[1]
    return None


if __name__ == "__main__":
    agent_name = sys.argv[1] if len(sys.argv) > 1 else "analyzer"
    check_thinking = "--check-thinking" in sys.argv[2:]
    modelo = _modelo_override(sys.argv[2:])

    engine = get_engine_for_agent(agent_name)
    if modelo:
        if not isinstance(engine, LMStudioEngine):
            sys.exit(f"--model solo tiene sentido con LM Studio, '{agent_name}' usa {engine.__class__.__name__}")
        engine.model = modelo
    print(f"Agente '{agent_name}' -> {engine.__class__.__name__} (modelo: {engine.model})")

    if check_thinking:
        _check_thinking(engine, agent_name)
    else:
        response = engine.run(
            system_prompt="Respondes de forma breve y directa.",
            user_prompt="Responde solo con la palabra: OK",
            max_tokens=500,  # margen para reasoning_content antes de la respuesta final
        )
        print(f"Respuesta: {response.content!r}")
        print(f"Tokens: in={response.input_tokens} out={response.output_tokens} reasoning={response.reasoning_tokens}")
