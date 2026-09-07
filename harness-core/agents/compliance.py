"""
Compliance: valida el código que Executor ya generó para UN item, contra sus
criterios_aceptacion, y deja el veredicto en
.harness/validation/<item_id>.json.

No decide qué item validar — eso lo hace orchestrator.py (elige items cuyo
último evento en executor.jsonl es "finalizado" y todavía no tienen
veredicto). Compliance solo sabe: dado un item_id, leer el código real y
decidir si cumple lo que el plan pedía.
"""
import json
from datetime import datetime, timezone

from access_control import AgentFileGuard, Zona
from engines.factory import get_engine_for_agent
from interfaz_real import leer_interfaz_real, combinar_interfaz
from agents import ESTILO_SALIDA_BREVE

AGENT_NAME = "compliance"

SYSTEM_PROMPT = """\
Sos el agente de Compliance de un harness de desarrollo asistido por IA \
(creación desde cero, mantención de código existente, o migración de \
monolitos hacia FastAPI + Angular, según el proyecto). Tu única tarea es \
revisar el código ya generado para UN item del plan y decidir, criterio \
por criterio, si cumple lo que se pedía. No generás ni corregís código — \
solo evaluás.

Reglas estrictas:
- Evaluá CADA criterio de "criterios_aceptacion" por separado, contra el \
código real que se te muestra en "archivos". No asumas nada que no puedas \
verificar leyendo código real.
- Si un criterio depende de algo que no está en "archivos" del item (ej. \
cómo se ensambla el prefijo final de una ruta, o a qué HTTP status mapea \
un código de error), buscá esa respuesta en \
"archivos_infraestructura_compartida" y "archivos_reales_de_dependencias" \
antes de rechazar por no poder verificarlo — ahí tenés el contenido real, \
no solo lo que cada item promete en su interfaz. "arbol_archivos_proyecto" \
es solo un listado de rutas para orientarte, no todas tienen su contenido \
mostrado.
- Un criterio se marca cumplido=true solo si el código lo satisface de \
forma clara — ya sea en "archivos", en la infraestructura compartida, o en \
el contenido real de una dependencia. Ante la duda real (ni así lo podés \
confirmar), cumplido=false — no le des el beneficio de la duda al código.
- "chequeos_deterministicos_previos" es un HECHO, no algo a re-verificar: \
si dice que el proyecto ya compiló (o que los tests ya pasaron), ese \
criterio puntual va cumplido=true directo — no te falta evidencia para \
confirmarlo, ya está confirmado por una herramienta real antes de que vos \
recibieras este item. No lo re-derives leyendo el código a ojo.
- El veredicto general es "aprobado" solo si TODOS los criterios están \
cumplidos. Si falta uno solo, es "rechazado".
- Si un archivo esperado no existe (aparece como null en "archivos"), todo \
criterio que dependa de ese archivo es cumplido=false.
- No opines sobre nada que no esté en "criterios_aceptacion" (estilo, \
mejoras posibles, etc.) — eso no es tu trabajo acá, salvo la excepción \
puntual de "hallazgos" descrita más abajo (que no es opinar sobre ESTE \
item, es señalar algo fuera de él).
- Si (y SOLO si) "tipo_flujo" es "mantencion" y notás algo REAL en código \
YA EXISTENTE fuera de "archivos" de este item (un bug, una práctica \
insegura), o una mejora técnica posible fuera de alcance -- NO lo uses para \
rechazar el item (eso solo lo deciden los "criterios_aceptacion"), \
reportalo en "hallazgos" (ver formato abajo). Array vacío si no aplica -- \
nunca inventes uno para llenar el campo.

""" + ESTILO_SALIDA_BREVE + """ Aplica a "detalle" (por criterio, el \
general, y "descripcion" de cada hallazgo) -- "veredicto" y "cumplido" \
siguen siendo exactos, esto es solo sobre cómo redactás el texto.

Formato de salida OBLIGATORIO — SOLO un objeto JSON, nada de texto antes ni \
después, sin code fences:

{
  "veredicto": "aprobado" | "rechazado",
  "criterios_evaluados": [
    { "criterio": "<texto exacto del criterio>", "cumplido": true | false, "detalle": "<por qué, breve>" }
  ],
  "detalle": "<resumen breve, cadena vacía si no hace falta>",
  "hallazgos": [
    { "tipo": "riesgo" | "recomendacion", "descripcion": "<qué encontraste y dónde>" }
  ]
}
"""


def _item_por_id(plan: dict, item_id: str) -> dict:
    for item in plan["items"]:
        if item["id"] == item_id:
            return item
    raise ValueError(f"item_id '{item_id}' no existe en plan.json")


def _arbol_archivos(guard: AgentFileGuard) -> list[str]:
    """
    Listado determinístico (sin LLM) de todos los archivos del proyecto
    destino -- orienta a Compliance sobre qué existe, aunque no vea el
    contenido de todo.
    """
    return sorted(guard.list_files(Zona.PROJECT))


def _leer_archivos(guard: AgentFileGuard, rutas: set[str]) -> dict[str, str]:
    contenidos = {}
    for ruta in rutas:
        try:
            contenidos[ruta] = guard.read(Zona.PROJECT, ruta)
        except FileNotFoundError:
            continue
    return contenidos


def _archivos_infraestructura(plan: dict, item_id_actual: str, guard: AgentFileGuard) -> dict[str, str]:
    """
    Contenido completo de los archivos_destino de todo item con
    `ticket_id: null` (infraestructura compartida, ej. un item de settings/DB base) --
    siempre visible para Compliance sin importar qué item se esté
    validando. Es la misma marca que el Planner ya usa hoy para distinguir
    infraestructura de items de negocio (ver schemas/plan.contract.md).
    Encontrado en la práctica: rechazos falsos porque Compliance no podía
    ver exception_handlers.py (mapeo code->HTTP status) al validar items
    que ni siquiera lo tenían en depende_de -- es infraestructura ambiente,
    no una dependencia declarada item por item.
    """
    rutas = {
        ruta
        for item in plan["items"]
        if item["id"] != item_id_actual and item.get("ticket_id") is None
        for ruta in item.get("archivos_destino", [])
    }
    return _leer_archivos(guard, rutas)


def _archivos_reales_de_dependencias(item: dict, plan: dict, guard: AgentFileGuard, excluir: set[str]) -> dict[str, str]:
    """
    Contenido completo (no solo `interfaz`) de los archivos_destino de los
    items listados en `depende_de` -- para items "ensambladores" que
    necesitan verificar el contenido real de lo que integran, no solo lo
    que cada dependencia promete exponer. Ej. un item ensamblador (main.py)
    depende de todos los routers de negocio para verificar que el ensamblado de
    prefijos en main.py da las rutas finales correctas -- interfaz.endpoint
    no alcanza para eso, hace falta ver cada router de verdad.
    """
    rutas = {
        ruta
        for dep_id in item.get("depende_de", [])
        for ruta in _item_por_id(plan, dep_id).get("archivos_destino", [])
    } - excluir
    return _leer_archivos(guard, rutas)


def construir_contexto(plan: dict, item_id: str, guard: AgentFileGuard) -> dict:
    item = _item_por_id(plan, item_id)

    dependencias = {}
    for dep_id in item.get("depende_de", []):
        dep_item = _item_por_id(plan, dep_id)
        predicha = dep_item.get("interfaz", {})
        real = leer_interfaz_real(guard, dep_id)
        dependencias[dep_id] = combinar_interfaz(predicha, real)

    archivos = {}
    for ruta in item["archivos_destino"]:
        try:
            archivos[ruta] = guard.read(Zona.PROJECT, ruta)
        except FileNotFoundError:
            archivos[ruta] = None

    archivos_infraestructura = _archivos_infraestructura(plan, item_id, guard)
    archivos_dependencias = _archivos_reales_de_dependencias(
        item, plan, guard, excluir=set(archivos) | set(archivos_infraestructura)
    )

    return {
        "decisiones_globales": plan["decisiones_globales"],
        "item": {
            "id": item["id"],
            "tipo": item["tipo"],
            "descripcion": item["descripcion"],
            "archivos_destino": item["archivos_destino"],
            "detalle_tecnico": item.get("detalle_tecnico", ""),
            "criterios_aceptacion": item["criterios_aceptacion"],
        },
        "dependencias": dependencias,
        "archivos": archivos,
        "arbol_archivos_proyecto": _arbol_archivos(guard),
        "archivos_infraestructura_compartida": archivos_infraestructura,
        "archivos_reales_de_dependencias": archivos_dependencias,
        "chequeos_deterministicos_previos": _chequeos_previos(
            item, plan.get("metadata", {}).get("tipo_flujo", "migracion")
        ),
    }


def _chequeos_previos(item: dict, tipo_flujo: str) -> str:
    """
    Compliance solo se invoca DESPUÉS de que format_check.py (y, para
    frontend, frontend_check.py/ng build; para backend con
    tests_requeridos, smoke_test.py/pytest; para mantención,
    convention_check.py y regression_check.py también) ya corrieron sobre
    este mismo item y pasaron -- ver orchestrator.py::validar_con_format_check.
    Sin este campo, Compliance no tiene forma de saberlo y puede rechazar por
    "no puedo confirmar que compila", algo que ya está confirmado (visto
    en vivo en un item frontend real, 2026-08-23: rechazo falso por exactamente
    este motivo).
    """
    if item["tipo"] == "frontend":
        return (
            "El frontend check (ng build real, configuración development) para "
            "este item ya corrió automáticamente antes de que llegaras a "
            "validarlo, y pasó -- si no hubiera compilado, el item nunca habría "
            "llegado a vos. Cualquier criterio de 'compila'/'ng build' va "
            "cumplido=true directo, sin que necesites confirmarlo leyendo el "
            "código."
        )
    if item["tipo"] == "infra":
        return (
            "El docker check (docker_check.py) para este item ya corrió "
            "automáticamente antes de que llegaras a validarlo, y pasó -- si no "
            "hubiera pasado (build fallido, verificacion_runtime sin el texto "
            "esperado, o smoke_http sin responder 200), el item nunca habría "
            "llegado a vos. Cualquier criterio de 'docker build termina sin "
            "error', 'verificacion_runtime'/'odbcinst' o 'docker compose "
            "up'/'smoke_http'/'responde 200' va cumplido=true directo, sin que "
            "necesites confirmarlo leyendo el Dockerfile/compose."
        )
    if tipo_flujo == "mantencion":
        return (
            "Este item es de mantención: además del format check (imports), el "
            "convention check (los identificadores nuevos siguen la convención "
            "de casing dominante que el archivo/módulo YA tenía -- criterio "
            "relativo, no la convención fija del harness) y el regression "
            "check (la suite de tests que el deployable YA tenía antes de este "
            "item sigue pasando, no solo los tests_requeridos propios del "
            "item) ya corrieron automáticamente antes de que llegaras a "
            "validarlo, y pasaron -- si no hubieran pasado, el item nunca "
            "habría llegado a vos. Cualquier criterio de 'sigue la convención "
            "del archivo'/'no rompe tests existentes' va cumplido=true directo. "
            "El scope de mantención es más chico que el de migración/creación "
            "(menos archivos, menos pasos), pero el nivel de exigencia es el "
            "mismo -- no le des menos rigor a un item de mantención por ser "
            "más chico. Si al revisar 'archivos_infraestructura_compartida' o "
            "'archivos_reales_de_dependencias' notás algo real fuera del "
            "alcance de este item (un bug en código existente, una mejora "
            "técnica posible), no lo uses para rechazar el item -- repórtalo "
            "en 'hallazgos' en vez de corregirlo o exigirlo."
        )
    if item.get("tests_requeridos"):
        return (
            "El format check (imports) y el smoke test (pytest real contra los "
            "tests_requeridos de este item) ya corrieron automáticamente antes "
            "de que llegaras a validarlo, y pasaron -- si no hubieran pasado, "
            "el item nunca habría llegado a vos. Cualquier criterio de "
            "'compila'/'los tests pasan' va cumplido=true directo."
        )
    return (
        "El format check (imports internos, nombres pisados, imports no "
        "usados) para este item ya corrió automáticamente antes de que "
        "llegaras a validarlo, y pasó -- si no hubiera pasado, el item nunca "
        "habría llegado a vos."
    )


def construir_prompt_usuario(contexto: dict) -> str:
    return (
        "## decisiones_globales\n"
        f"{json.dumps(contexto['decisiones_globales'], ensure_ascii=False, indent=2)}\n\n"
        "## item a validar\n"
        f"{json.dumps(contexto['item'], ensure_ascii=False, indent=2)}\n\n"
        "## dependencias (solo interfaz, no la implementación)\n"
        f"{json.dumps(contexto['dependencias'], ensure_ascii=False, indent=2)}\n\n"
        "## archivos generados (los de este item)\n"
        f"{json.dumps(contexto['archivos'], ensure_ascii=False, indent=2)}\n\n"
        "## árbol de archivos del proyecto (solo rutas, para orientarte — no todo lo listado acá tiene su contenido más abajo)\n"
        f"{json.dumps(contexto['arbol_archivos_proyecto'], ensure_ascii=False, indent=2)}\n\n"
        "## archivos de infraestructura compartida (contenido real — ej. exception_handlers.py, config.py; úsalos para verificar cosas que dependen de ellos, como mapeo de errores a HTTP status)\n"
        f"{json.dumps(contexto['archivos_infraestructura_compartida'], ensure_ascii=False, indent=2)}\n\n"
        "## archivos reales de tus dependencias (contenido real, no solo su interfaz — úsalos si necesitás verificar algo puntual, ej. el prefijo interno de un router que este item monta)\n"
        f"{json.dumps(contexto['archivos_reales_de_dependencias'], ensure_ascii=False, indent=2)}\n\n"
        "## chequeos_deterministicos_previos (HECHO, no algo a re-verificar vos)\n"
        f"{contexto['chequeos_deterministicos_previos']}\n"
    )


def parsear_respuesta(texto: str) -> dict:
    texto = texto.strip()

    # el modelo a veces envuelve el JSON en code fences pese a la instrucción
    if texto.startswith("```"):
        texto = texto.strip("`").strip()
        if texto.startswith("json"):
            texto = texto[4:].strip()

    data = json.loads(texto)  # deja subir json.JSONDecodeError tal cual si falla

    if data.get("veredicto") not in ("aprobado", "rechazado"):
        raise ValueError(f"veredicto inválido o ausente: {data.get('veredicto')!r}")
    if "criterios_evaluados" not in data or not isinstance(data["criterios_evaluados"], list):
        raise ValueError("falta 'criterios_evaluados' (lista) en la respuesta")

    return data


def _ahora() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


def _calcular_veredicto(criterios_evaluados: list[dict]) -> str:
    """
    El veredicto general es una función determinística de los criterios
    individuales (misma regla que ya le pedimos al modelo en el system
    prompt: "aprobado solo si TODOS están cumplidos") -- no hace falta
    confiar en que el modelo la calcule bien él mismo. Encontrado en la
    práctica (item frontend real, 2026-08-20): el modelo marcó los 9
    criterios cumplido=true pero escribió veredicto="rechazado" -- una
    inconsistencia interna que este cálculo elimina de raíz, gratis.
    """
    return "aprobado" if all(c.get("cumplido") for c in criterios_evaluados) else "rechazado"


def validar_item(project_root: str, item_id: str) -> dict:
    guard = AgentFileGuard(AGENT_NAME, project_root)
    plan = json.loads(guard.read(Zona.HARNESS_CONFIG, "plan.json"))

    contexto = construir_contexto(plan, item_id, guard)
    prompt_usuario = construir_prompt_usuario(contexto)

    engine = get_engine_for_agent(AGENT_NAME)
    try:
        # 32000: con deepseek-reasoner el modelo gasta tokens en reasoning_content
        # antes de la respuesta final -- 16000 alcanzaba para deepseek-chat sin
        # razonamiento, pero se quedaría corto acá (mismo problema que Qwen3.6 en
        # LM Studio, ver engines/deepseek_api.py y Notas técnicas del README).
        respuesta = engine.run(SYSTEM_PROMPT, prompt_usuario, max_tokens=32000)
    except RuntimeError as e:
        return {"estado": "error", "motivo": f"motor de inferencia falló: {e}"}

    try:
        resultado = parsear_respuesta(respuesta.content)
    except (json.JSONDecodeError, ValueError) as e:
        return {"estado": "error", "motivo": f"respuesta mal formada: {e}"}

    veredicto_calculado = _calcular_veredicto(resultado["criterios_evaluados"])
    detalle = resultado.get("detalle", "")
    if veredicto_calculado != resultado["veredicto"]:
        detalle = (
            f"[veredicto recalculado: el modelo escribió '{resultado['veredicto']}', pero eso "
            f"es inconsistente con sus propios criterios_evaluados -- se usó '{veredicto_calculado}', "
            f"calculado de ahí] {detalle}"
        ).strip()

    veredicto = {
        "item_id": item_id,
        "veredicto": veredicto_calculado,
        "timestamp": _ahora(),
        "criterios_evaluados": resultado["criterios_evaluados"],
        "detalle": detalle,
    }
    guard.write(
        Zona.HARNESS_VALIDATION,
        f"{item_id}.json",
        json.dumps(veredicto, ensure_ascii=False, indent=2),
    )

    return {
        "estado": veredicto["veredicto"],
        "veredicto": veredicto,
        "hallazgos": resultado.get("hallazgos", []),
    }
