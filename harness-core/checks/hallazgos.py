"""
Registro de hallazgos fuera de alcance -- exclusivo de tipo_flujo:
"mantencion" (ver schemas/plan.contract.md, "Hallazgos fuera de alcance").
No es un agente, no pasa por AgentFileGuard -- misma categoría que
api_endpoints.py/plan_validator.py: post-proceso determinístico invocado
por orchestrator.py.

Motivo: en mantención el scope de un item debe quedar estrictamente acotado
a lo pedido -- si Executor o Compliance notan algo real fuera de ese
alcance mientras trabajan (un bug en código existente adyacente, o una
mejora técnica posible), no lo corrigen ni lo ignoran en silencio: lo
reportan (bloque "### HALLAZGOS" en Executor, clave "hallazgos" en el JSON
de Compliance -- ver agents/executor.py y agents/compliance.py) para que
alguien lo levante después. Este módulo es quien persiste ese reporte a
disco, automático, sin depender de que un humano se acuerde de llenarlo a
mano (aunque los archivos igual aceptan ediciones manuales).

Dos categorías, dos archivos destino, ambos "documentos que crecen"
(append-only, nunca se reescriben, mismo criterio y misma ubicación por
deployable que `docs/fixAplicados.md`/`docs/recomendaciones-tecnicas.md` ya
documentados en el contrato -- `<deployable>/docs/<archivo>`, no la raíz del
proyecto, reusando `smoke_test._carpeta_deployable` para no duplicar ese
criterio):
  - "riesgo": algo mal en código YA EXISTENTE (bug, práctica insegura,
    deuda) -> `<deployable>/docs/riesgos_heredados.md` (archivo nuevo,
    exclusivo de mantención -- no confundir con riesgos_heredados[] de
    plan.json, que es de migración y se escribe una sola vez, antes de que
    el código exista).
  - "recomendacion": mejora técnica posible, no necesariamente un bug ->
    `<deployable>/docs/recomendaciones-tecnicas.md` (mismo archivo que ya
    crecía a mano desde el arranque del proyecto -- ahora también se puebla
    solo).
"""
from datetime import datetime
from pathlib import Path

from checks.smoke_test import _carpeta_deployable

_ARCHIVO_POR_TIPO = {
    "riesgo": "riesgos_heredados.md",
    "recomendacion": "recomendaciones-tecnicas.md",
}

_TITULO_POR_TIPO = {
    "riesgo": "Riesgos heredados -- hallazgos fuera de alcance durante mantención",
    "recomendacion": "Recomendaciones técnicas",
}


def _registrar_uno(project_root: Path, carpeta: str, tipo: str, item_id: str, fuente: str, descripcion: str):
    ruta = project_root / carpeta / "docs" / _ARCHIVO_POR_TIPO[tipo]
    ruta.parent.mkdir(parents=True, exist_ok=True)
    encabezado = f"# {_TITULO_POR_TIPO[tipo]}\n\n" if not ruta.exists() else ""
    timestamp = datetime.now().astimezone().isoformat()
    entrada = f"## {item_id} — {fuente} — {timestamp}\n\n{descripcion}\n\n---\n\n"
    with ruta.open("a", encoding="utf-8") as f:
        f.write(encabezado + entrada)


def registrar_hallazgos(project_root: str, item: dict, fuente: str, hallazgos: list[dict]) -> int:
    """
    Escribe cada hallazgo válido ({"tipo": "riesgo"|"recomendacion",
    "descripcion": "..."}) en el archivo que le corresponde, dentro del
    deployable al que pertenece `item` (`item["archivos_destino"][0]`, mismo
    criterio que `smoke_test._carpeta_deployable` -- default "backend" si el
    item no trae `archivos_destino`, ej. fixtures viejos). Best-effort: un
    hallazgo con "tipo" desconocido o sin "descripcion" no rompe nada,
    simplemente se ignora (mismo criterio que el resto de los chequeos
    determinísticos del harness ante una entrada mal formada). Devuelve
    cuántos se registraron de verdad.

    `fuente`: "executor" | "compliance" -- de dónde vino el hallazgo, para
    que quede trazable en el propio archivo.
    """
    root = Path(project_root).resolve()
    carpeta = _carpeta_deployable(item)
    item_id = item["id"]
    registrados = 0
    for h in hallazgos:
        if not isinstance(h, dict):
            continue
        tipo = h.get("tipo")
        descripcion = h.get("descripcion")
        if tipo not in _ARCHIVO_POR_TIPO or not descripcion:
            continue
        _registrar_uno(root, carpeta, tipo, item_id, fuente, descripcion)
        registrados += 1
    return registrados
