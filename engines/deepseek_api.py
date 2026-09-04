"""
Adapter para la API de DeepSeek (OpenAI-compatible por REST).
Requiere DEEPSEEK_API_KEY en el entorno (o en un .env en la raíz del harness).
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


class DeepSeekEngine(ModelEngine):
    def __init__(self, model: str, base_url: str = "https://api.deepseek.com", timeout_seconds: int = 120):
        self.model = model
        self.base_url = base_url
        self.timeout_seconds = timeout_seconds
        self.api_key = os.environ["DEEPSEEK_API_KEY"]

    def run(
        self, system_prompt: str, user_prompt: str, *,
        max_tokens: int = 2000, enable_thinking: bool = True,
    ) -> EngineResponse:
        # deepseek-reasoner no soporta deshabilitar el razonamiento vía API —
        # el parámetro se acepta por compatibilidad con ModelEngine, pero se ignora.
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
                    "temperature": 0.2,  # bajo: queremos consistencia, no creatividad, en Executor/Compliance
                },
                timeout=self.timeout_seconds,
            )
        except requests.exceptions.ConnectionError as e:
            # Mismo orden que engines/lm_studio.py: ConnectTimeout hereda de
            # Timeout Y de ConnectionError, este except tiene que ir antes.
            raise MotorInalcanzable(
                f"No se pudo conectar a DeepSeek en {self.base_url}: {e}"
            ) from e
        except requests.exceptions.Timeout as e:
            # Mismo fix que engines/kimi_api.py (2026-08-27, bug real
            # encontrado en producción con Kimi) -- sin esto, un timeout real
            # revienta el proceso entero de --loop en vez de tratarse como
            # un intento fallido normal.
            raise TimeoutDelMotor(
                f"DeepSeek no respondió dentro de {self.timeout_seconds}s: {e}"
            ) from e
        try:
            resp.raise_for_status()
        except requests.exceptions.HTTPError as e:
            # Sin esto, un HTTPError crudo (ej. 400 transitorio de la API)
            # revienta orchestrator.py entero con traceback en vez de
            # tratarse como un intento fallido normal -- mismo fix ya hecho
            # para engines/lm_studio.py (2026-08-30), nunca portado acá.
            raise RuntimeError(
                f"DeepSeek devolvió {resp.status_code}: {resp.text[:500]}"
            ) from e
        data = resp.json()

        if "choices" not in data:
            raise RuntimeError(
                f"DeepSeek no devolvió el formato esperado. Respuesta cruda:\n{data}"
            )

        message = data["choices"][0]["message"]
        content = message.get("content", "")

        # deepseek-reasoner separa razonamiento de la respuesta final en
        # reasoning_content, igual que los modelos "thinking" de LM Studio.
        # Si finish_reason == "length", se quedó sin tokens pensando y nunca
        # escribió la respuesta real. deepseek-chat no manda reasoning_content,
        # así que esta rama nunca dispara con ese modelo.
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
