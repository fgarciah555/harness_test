"""
Adapter para LM Studio, que expone una API OpenAI-compatible en localhost.
"""
import requests

from .base import ModelEngine, EngineResponse, TimeoutDelMotor, MotorInalcanzable

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


class LMStudioEngine(ModelEngine):
    def __init__(self, model: str, base_url: str = "http://localhost:1234/v1", timeout_seconds: int = 120):
        self.model = model
        self.base_url = base_url
        self.timeout_seconds = timeout_seconds

    def run(
        self, system_prompt: str, user_prompt: str, *,
        max_tokens: int = 2000, enable_thinking: bool = True,
    ) -> EngineResponse:
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "max_tokens": max_tokens,
            "temperature": 0.2,  # bajo: queremos consistencia, no creatividad, en Executor/Compliance
            "chat_template_kwargs": {"enable_thinking": enable_thinking},
        }
        if not enable_thinking:
            # chat_template_kwargs.enable_thinking solo no alcanza para este
            # modelo: expone además "Reasoning Effort" (default "xhigh" en su
            # model card de LM Studio) que lo pisa si no se manda también.
            # Confirmado empíricamente contra qwen/qwen3.8-27b (2026-08-21):
            # con enable_thinking=False solo, reasoning_tokens seguía > 0;
            # agregando "reasoning_effort": "none" acá -- a nivel TOP del
            # body, no anidado en chat_template_kwargs -- reasoning_tokens
            # da 0 de forma consistente.
            payload["reasoning_effort"] = "none"

        try:
            resp = requests.post(
                f"{self.base_url}/chat/completions",
                json=payload,
                timeout=self.timeout_seconds,
            )
        except requests.exceptions.ConnectionError as e:
            # ConnectTimeout hereda de Timeout Y de ConnectionError -- este
            # except tiene que ir ANTES que el de Timeout de abajo, si no un
            # host inalcanzable que tarda en fallar (IP vieja, VPN, firewall
            # dropeando paquetes -- no solo "el proceso está apagado") cae en
            # la rama de Timeout y se malinterpreta como "se quedó pensando",
            # nunca como MotorInalcanzable -- el fallback a Kimi no se
            # dispara en ese caso, que es justamente el más común detrás de
            # "no tengo acceso a mi local".
            raise MotorInalcanzable(
                f"No se pudo conectar a LM Studio en {self.base_url} "
                f"(¿está corriendo? ¿la IP/puerto sigue siendo correcta?): {e}"
            ) from e
        except requests.exceptions.Timeout as e:
            raise TimeoutDelMotor(
                f"El motor de inferencia no respondió dentro de "
                f"{self.timeout_seconds}s (probablemente se quedó pensando "
                "de más). Intento sin código generado."
            ) from e
        try:
            resp.raise_for_status()
        except requests.exceptions.HTTPError as e:
            # Ej. 400 "Failed to load model...SIGSEGV" (el modelo crasheó
            # cargando, el servidor sigue arriba) -- sin esto, un HTTPError
            # crudo revienta orchestrator.py entero con traceback en vez de
            # tratarse como un intento fallido normal (mismo camino que
            # TimeoutDelMotor/RuntimeError, que ejecutar_item/compliance/
            # arbitro/documentador ya saben manejar). No es MotorInalcanzable
            # -- el servidor SÍ respondió, solo que con un error.
            raise RuntimeError(
                f"LM Studio devolvió {resp.status_code}: {resp.text[:500]}"
            ) from e
        data = resp.json()

        if "choices" not in data:
            raise RuntimeError(
                f"LM Studio no devolvió el formato esperado. Respuesta cruda:\n{data}"
            )

        message = data["choices"][0]["message"]
        content = message.get("content", "")

        # Modelos con "thinking" (como Qwen3.6) separan el razonamiento de la
        # respuesta final en reasoning_content. Si finish_reason == "length",
        # el modelo se quedó sin tokens pensando y nunca escribió la respuesta real.
        if data["choices"][0].get("finish_reason") == "length" and not content.strip():
            reasoning = message.get("reasoning_content", "")

            if _detectar_bucle_repetitivo(reasoning) or _detectar_bucle_repetitivo(content):
                raise RuntimeError(
                    "El modelo parece haber entrado en un bucle de repetición. "
                    "Reinicia el servidor de LM Studio antes de reintentar. "
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
            reasoning_tokens=data.get("usage", {})
                .get("completion_tokens_details", {})
                .get("reasoning_tokens"),
        )