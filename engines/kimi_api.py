"""
Adapter para la API de Kimi (Moonshot AI), compatible con OpenAI por REST.
Requiere KIMI_API_KEY en el entorno (o en un .env en la raíz del harness).

Alternativa a LM Studio cuando no hay acceso al motor local -- mismo rol que
ya cumple DeepSeek para Compliance/executor_senior/arbitro (ver
deepseek_api.py), pero pensado para reemplazar puntualmente a lm_studio en
los agentes que hoy corren local (executor, documentador) sin tocar el resto
del harness: ModelEngine es la única interfaz que le importa a orchestrator.py.
"""
import os
from pathlib import Path

import requests
from dotenv import load_dotenv

from .base import ModelEngine, EngineResponse, TimeoutDelMotor, MotorInalcanzable

load_dotenv(Path(__file__).parent.parent / ".env")


def _detectar_bucle_repetitivo(texto: str, largo_fragmento: int = 40, min_repeticiones: int = 4) -> bool:
    """
    Heurística simple: si un fragmento de ~40 caracteres se repite 4+ veces
    seguidas, asumimos que el modelo entró en bucle de razonamiento.
    """
    if len(texto) < largo_fragmento * min_repeticiones:
        return False

    fragmento = texto[-largo_fragmento:]
    conteo = texto.count(fragmento)
    return conteo >= min_repeticiones


class KimiEngine(ModelEngine):
    def __init__(self, model: str, base_url: str = "https://api.moonshot.ai/v1", timeout_seconds: int = 120):
        self.model = model
        self.base_url = base_url
        self.timeout_seconds = timeout_seconds
        self.api_key = os.environ["KIMI_API_KEY"]

    def run(
        self, system_prompt: str, user_prompt: str, *,
        max_tokens: int = 2000, enable_thinking: bool = True,
    ) -> EngineResponse:
        # enable_thinking se acepta por compatibilidad con ModelEngine, pero
        # se ignora -- Kimi no expone un flag para deshabilitar razonamiento
        # vía este parámetro (a diferencia de LM Studio). Si el modelo elegido
        # en models.yaml tiene variante "thinking", razona siempre que aplique.
        try:
            resp = requests.post(
                f"{self.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={
                    "model": self.model,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    "max_tokens": max_tokens,
                    # Confirmado en vivo (2026-08-27) contra kimi-k2.7-code y
                    # kimi-k2.6: la API rechaza con 400 "invalid temperature:
                    # only 1 is allowed for this model" cualquier valor != 1 --
                    # a diferencia de LM Studio/DeepSeek, estos modelos no
                    # aceptan bajar la temperatura para pedir más consistencia.
                    "temperature": 1,
                },
                timeout=self.timeout_seconds,
            )
        except requests.exceptions.ConnectionError as e:
            # Mismo orden que engines/lm_studio.py: ConnectTimeout hereda de
            # Timeout Y de ConnectionError, este except tiene que ir antes.
            raise MotorInalcanzable(
                f"No se pudo conectar a Kimi en {self.base_url}: {e}"
            ) from e
        except requests.exceptions.Timeout as e:
            # Confirmado en vivo (2026-08-27, migración Tesorería): sin este
            # try/except, un ReadTimeout de verdad (item grande, ej.
            # BE-CLIENT-001 con 14 funciones) se propagaba crudo y reventaba
            # todo el proceso de --loop -- exactamente el mismo tipo de bug
            # que ya se había corregido para LM Studio, pero nunca portado
            # acá. TimeoutDelMotor deja que el loop normal de reintentos se
            # haga cargo solo (intento fallido sin archivos), en vez de
            # crashear.
            raise TimeoutDelMotor(
                f"Kimi no respondió dentro de {self.timeout_seconds}s: {e}"
            ) from e
        try:
            resp.raise_for_status()
        except requests.exceptions.HTTPError as e:
            # Sin esto, un HTTPError crudo revienta orchestrator.py entero
            # en vez de tratarse como un intento fallido normal -- mismo fix
            # ya hecho para engines/lm_studio.py (2026-08-30), nunca portado
            # acá ni a deepseek_api.py hasta ahora.
            raise RuntimeError(
                f"Kimi devolvió {resp.status_code}: {resp.text[:500]}"
            ) from e
        data = resp.json()

        if "choices" not in data:
            raise RuntimeError(
                f"Kimi no devolvió el formato esperado. Respuesta cruda:\n{data}"
            )

        message = data["choices"][0]["message"]
        content = message.get("content", "")

        # Mismo patrón que deepseek-reasoner/Qwen: si el modelo separa
        # razonamiento en reasoning_content y se queda sin max_tokens
        # pensando, finish_reason == "length" con content vacío.
        if data["choices"][0].get("finish_reason") == "length" and not content.strip():
            reasoning = message.get("reasoning_content", "")

            if _detectar_bucle_repetitivo(reasoning) or _detectar_bucle_repetitivo(content):
                raise RuntimeError(
                    "El modelo parece haber entrado en un bucle de repetición. "
                    f"Preview: ...{(content or reasoning)[-200:]}"
                )

            raise RuntimeError(
                f"El modelo agotó max_tokens={max_tokens} pensando (reasoning_content) "
                "y nunca llegó a escribir la respuesta final. No es un bucle, es límite "
                "insuficiente para esta tarea — subí max_tokens en la llamada. "
                f"Preview del razonamiento: ...{reasoning[-200:]}"
            )

        return EngineResponse(
            content=content,
            model=data.get("model", self.model),
            input_tokens=data.get("usage", {}).get("prompt_tokens"),
            output_tokens=data.get("usage", {}).get("completion_tokens"),
        )
