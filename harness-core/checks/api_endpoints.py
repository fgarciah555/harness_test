"""
Catálogo de endpoints: post-proceso determinístico y gratis (sin LLM), no un
agente — no pasa por AgentFileGuard ni permissions.yaml, misma categoría que
format_check.py/plan_validator.py. Ver schemas/plan.contract.md, sección
"Catálogo de endpoints", y schemas/api-endpoints.example.md para el formato.

Se regenera completo (nunca append) cada vez que Compliance aprueba un item
`tipo: "backend"` — ver el llamado desde orchestrator.validar_con_format_check.
Fuente de datos: únicamente `interfaz.endpoint` de cada item backend cuyo
estado efectivo actual sea 'completado' — no lee detalle_tecnico,
criterios_aceptacion ni código del monolito de origen.
"""
import json
from pathlib import Path


def _formatear_headers(headers: dict) -> str:
    return ", ".join(f"{k}: {v}" for k, v in headers.items())


def _cuerpo_request(request: dict) -> dict:
    if "body" in request:
        return request["body"]
    return {k: v for k, v in request.items() if k != "headers"}


def _formatear_endpoint(item_id: str, endpoint: dict) -> str:
    lineas = [f"## {endpoint['metodo']} {endpoint['ruta']}", "", f"- **Origen:** `{item_id}`"]

    # request puede venir como dict estructurado (convención de
    # plan.example.json) o como descripción en prosa suelta (ej. "query
    # fecha:int, cod_com:int") -- este catálogo es un post-proceso
    # informativo, nunca debe romper por una forma que no anticipó.
    request = endpoint.get("request", {})
    if not isinstance(request, dict):
        lineas.append("- **Request:**")
        lineas.append(f"  {request}")
        cuerpo = None
    else:
        headers = request.get("headers")
        if headers:
            lineas.append(f"- **Headers:** `{_formatear_headers(headers)}`")
        cuerpo = _cuerpo_request(request)
    if cuerpo:
        lineas.append("- **Request:**")
        lineas.append("  ```json")
        lineas.append(f"  {json.dumps(cuerpo, ensure_ascii=False)}")
        lineas.append("  ```")

    for clave, payload in endpoint.items():
        if not clave.startswith("response_"):
            continue
        codigo = clave.split("_", 1)[1]
        lineas.append(f"- **Response {codigo}:**")
        lineas.append("  ```json")
        lineas.append(f"  {json.dumps(payload, ensure_ascii=False)}")
        lineas.append("  ```")

    return "\n".join(lineas)


def regenerar_catalogo_endpoints(project_root: str) -> Path:
    # import diferido: evita ciclo de imports con orchestrator.py, que a su
    # vez importa este módulo.
    from orchestrator import _cargar_plan, calcular_estados

    root = Path(project_root).resolve()
    plan = _cargar_plan(root)
    estados = calcular_estados(project_root)

    bloques = []
    for item in plan["items"]:
        if item.get("tipo") != "backend":
            continue
        if estados.get(item["id"]) != "completado":
            continue
        endpoint = item.get("interfaz", {}).get("endpoint")
        if not endpoint:
            continue
        # Algunos items agrupan varios endpoints bajo un mismo router (ver
        # plan.contract.md, "Revisiones sucesivas..." y Pendientes.md,
        # "api_endpoints.py no funciona con planes que agrupan endpoints") --
        # interfaz.endpoint puede venir como un dict (un endpoint) o una
        # list[dict] (varios). Iterar siempre como lista para no asumir una
        # sola forma; un valor que no sea ninguna de las dos se salta sin
        # romper el resto del catálogo (post-proceso, no debe ser un gate).
        endpoints = endpoint if isinstance(endpoint, list) else [endpoint]
        for ep in endpoints:
            if not isinstance(ep, dict) or "metodo" not in ep or "ruta" not in ep:
                continue
            bloques.append(_formatear_endpoint(item["id"], ep))

    proyecto = plan.get("metadata", {}).get("proyecto", "")
    encabezado = (
        f"# API endpoints — {proyecto}\n\n"
        "> Generado automáticamente por el harness a partir de los items "
        "`backend` aprobados en `.harness/config/plan.json`. No editar a "
        "mano — se reescribe completo cada vez que Compliance aprueba un "
        "nuevo item backend."
    )
    partes = [encabezado] + bloques
    contenido = "\n\n".join(partes) + "\n"

    destino = root / "docs" / "api-endpoints.md"
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text(contenido)
    return destino
