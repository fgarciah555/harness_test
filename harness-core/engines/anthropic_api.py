# """
# Adapter para la API de Anthropic. Requiere ANTHROPIC_API_KEY en el entorno.
# Instalar: pip install anthropic --break-system-packages
# """
# import os

# import anthropic

# from .base import ModelEngine, EngineResponse


# class AnthropicEngine(ModelEngine):
#     def __init__(self, model: str = "claude-sonnet-4-6"):
#         self.model = model
#         self.client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

#     def run(self, system_prompt: str, user_prompt: str, *, max_tokens: int = 2000) -> EngineResponse:
#         response = self.client.messages.create(
#             model=self.model,
#             max_tokens=max_tokens,
#             system=system_prompt,
#             messages=[{"role": "user", "content": user_prompt}],
#         )
#         return EngineResponse(
#             content=response.content[0].text,
#             model=response.model,
#             input_tokens=response.usage.input_tokens,
#             output_tokens=response.usage.output_tokens,
#         )
