"""
Validación estructural determinística de plan.json, sin LLM — implementa la
sección "Reglas de validación" de schemas/plan.contract.md.

No es un agente, no pasa por AgentFileGuard, igual que orchestrator.py y
format_check.py. Corre sobre el plan ya cargado (dict), antes de que
orchestrator.py lo use para decidir nada — un plan inválido no debe llegar a
Executor/Compliance, porque las garantías que ambos asumen (ids resolubles,
interfaz de toda dependencia citada, un solo dueño por archivo) dejarían de
sostenerse.
"""


def _ids_duplicados(items: list[dict]) -> list[str]:
    vistos = set()
    duplicados = []
    for item in items:
        item_id = item["id"]
        if item_id in vistos:
            duplicados.append(item_id)
        vistos.add(item_id)
    return duplicados


def _dependencias_inexistentes(items: list[dict], ids: set[str]) -> list[str]:
    errores = []
    for item in items:
        for dep in item.get("depende_de", []):
            if dep not in ids:
                errores.append(f"{item['id']}: depende_de '{dep}', que no existe en items[]")
    return errores


def _ciclo_dependencias(items: list[dict]) -> list[str]:
    """DFS con pila de recursión para detectar cualquier ciclo, no solo auto-referencia."""
    grafo = {item["id"]: item.get("depende_de", []) for item in items}
    estado: dict[str, str] = {}  # id -> "visitando" | "listo"

    def visitar(item_id: str, camino: list[str]) -> list[str] | None:
        estado[item_id] = "visitando"
        for dep in grafo.get(item_id, []):
            if dep not in grafo:
                continue  # ya reportado por _dependencias_inexistentes
            if estado.get(dep) == "visitando":
                return camino + [item_id, dep]
            if estado.get(dep) != "listo":
                ciclo = visitar(dep, camino + [item_id])
                if ciclo:
                    return ciclo
        estado[item_id] = "listo"
        return None

    for item_id in grafo:
        if estado.get(item_id) is None:
            ciclo = visitar(item_id, [])
            if ciclo:
                return [f"dependencia circular: {' -> '.join(ciclo)}"]
    return []


def _archivos_destino_vacios(items: list[dict]) -> list[str]:
    return [
        f"{item['id']}: archivos_destino está vacío"
        for item in items
        if not item.get("archivos_destino")
    ]


def _criterios_aceptacion_vacios(items: list[dict]) -> list[str]:
    return [
        f"{item['id']}: criterios_aceptacion está vacío"
        for item in items
        if not item.get("criterios_aceptacion")
    ]


def _interfaz_vacia_en_dependencia_citada(items: list[dict]) -> list[str]:
    citados = {dep for item in items for dep in item.get("depende_de", [])}
    por_id = {item["id"]: item for item in items}
    errores = []
    for item_id in citados:
        item = por_id.get(item_id)
        if item is not None and not item.get("interfaz"):
            errores.append(
                f"{item_id}: aparece en depende_de de otro item pero su interfaz está vacía"
            )
    return errores


def _archivos_con_mas_de_un_dueno(items: list[dict]) -> list[str]:
    dueno_por_archivo: dict[str, str] = {}
    errores = []
    for item in items:
        for archivo in item.get("archivos_destino", []):
            dueno_previo = dueno_por_archivo.get(archivo)
            if dueno_previo is not None:
                errores.append(
                    f"'{archivo}' aparece en archivos_destino de {dueno_previo} y de {item['id']}"
                )
            else:
                dueno_por_archivo[archivo] = item["id"]
    return errores


def validar_plan(plan: dict) -> list[str]:
    """Devuelve la lista de errores encontrados — vacía si el plan es válido."""
    items = plan.get("items", [])
    ids = {item["id"] for item in items}

    errores: list[str] = []
    errores += [f"id duplicado: '{d}'" for d in _ids_duplicados(items)]
    errores += _dependencias_inexistentes(items, ids)
    errores += _ciclo_dependencias(items)
    errores += _archivos_destino_vacios(items)
    errores += _criterios_aceptacion_vacios(items)
    errores += _interfaz_vacia_en_dependencia_citada(items)
    errores += _archivos_con_mas_de_un_dueno(items)
    return errores
