"""
Constantes compartidas entre los agentes reales (executor, compliance,
arbitro, documentador). No hay lógica acá a propósito -- cada agente sigue
siendo independiente, esto es solo texto de prompt que los 4 repiten igual.
"""

# Instrucción de estilo para el texto libre que cada agente escribe (motivo
# de bloqueo, detalle de un criterio, explicación de arbitro, resumen del
# documentador) -- NUNCA para el código ni para el JSON/formato estructural
# de la respuesta, eso no se toca. Pedido explícito de Felipe (2026-08-24,
# ver docs/pendientes.md/docs/handoff.md del mismo día): el harness -- vos, el motor
# local y DeepSeek -- debería comunicarse con menos relleno, sin perder la
# causa concreta ni los números que sostienen una conclusión.
ESTILO_SALIDA_BREVE = """\
Estilo del texto libre que escribas (no del código ni del JSON/formato \
estructural, eso no cambia): sé breve. Cortá preámbulos y relleno ("cabe \
destacar que", "es importante notar", "simplemente"), no repitas contexto \
que ya está en lo que te dieron. Mantené SIEMPRE la causa concreta, el \
archivo/mecanismo involucrado, y cualquier número que sostenga tu \
conclusión -- eso no se corta, solo el relleno alrededor."""
