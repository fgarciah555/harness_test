"""
Arbitro: se invoca solo cuando Executor se bloquea ("### BLOQUEADO") porque
encontró una ambigüedad, contradicción, o dependencia faltante DENTRO del
plan -- no información que haya que inventar de cero. Ver orchestrator.py
(_ejecutar_con_arbitraje) para cuándo y cuántas veces se lo consulta
(--max-arbitrajes).

Devuelve uno de cuatro resultados (ver resolver_bloqueo):
- Una decisión puntual que Executor puede seguir al pie de la letra (ej. dos
  firmas que no calzan entre sí -- elige una, explica por qué).
- El/los item_id que deberían estar en el `depende_de` del item bloqueado y
  no están -- orchestrator.py es quien decide si aplica ese cambio a
  plan.json (agregar_dependencia_a_plan(), la ÚNICA escritura sobre
  plan.json que hace el harness fuera del Planner; deliberadamente angosta:
  solo agrega una arista al grafo, nunca contenido).
- Que el item que define lo que falta YA está declarado como dependencia,
  pero su `interfaz` no expone el símbolo -- acá arbitro no puede arreglar
  nada (no escribe contenido de items), solo señala item_productor +
  símbolo exacto para que quede registrado en el reporte de fallas y el
  Planner lo corrija. Encontrado en la práctica (2026-08-26, backend+DAL
  separados): 4 de 9 bloqueos de esa sesión eran
  justo este caso, y "falta_dependencia" los reportaba como "nada que
  agregar" sin decir POR QUÉ, obligando a diagnosticar a mano cada vez.
- Que no puede resolverlo con lo que tiene -- ahí orchestrator.py deja de
  insistir con este item.

En ningún caso genera código, reescribe el item, ni decide contenido de
plan.json -- solo señala. Deja registro de cada resolución (o intento
fallido) en .harness/logs/arbitraje.jsonl, tan auditable como cualquier otra
decisión automática del harness (ver decisiones_reintento.jsonl, mismo
patrón).
"""
import json
from datetime import datetime, timezone

from access_control import AgentFileGuard, Zona
from engines.factory import get_engine_for_agent
from agents.executor import construir_contexto
from agents import ESTILO_SALIDA_BREVE

AGENT_NAME = "arbitro"

SYSTEM_PROMPT = """\
Sos el agente de Arbitraje de un harness de desarrollo asistido por IA \
(creación desde cero, mantención de código existente, o migración de \
monolitos hacia FastAPI + Angular, según el proyecto). Te invocan solo \
cuando Executor se bloqueó al generar UN \
item porque encontró una ambigüedad, contradicción, o dependencia faltante \
dentro del plan. Tu única tarea es resolver ESE punto puntual -- no generás \
código, no reescribís el item, no opinás sobre nada que no sea el motivo \
del bloqueo.

Hay cuatro situaciones posibles, y tenés que distinguir cuál es:

1. AMBIGÜEDAD RESOLUBLE: el contexto que tenés (item, decisiones_globales, \
dependencias) alcanza para resolverlo, aunque haga falta elegir entre dos \
alternativas declaradas que no calzan entre sí (ej. dos firmas distintas \
para la misma función). Elegí una y explicá en pocas líneas cuál es y por \
qué, en términos concretos que Executor pueda aplicar directo (firma \
exacta, nombre de campo, orden de parámetros, etc.). No te cubras con \
"podría ser cualquiera de las dos" -- elegí.

2. DEPENDENCIA FALTANTE: lo que falta (un símbolo, un modelo, una firma) \
probablemente lo define OTRO item del plan que NO aparece como clave en \
"dependencias (solo interfaz...)" que te dieron -- mirá el "índice de \
items del plan" (id -> archivos que genera) para identificar cuál, \
comparando la ruta del símbolo que falta (ej. \
"app.model.usuario_model.Usuario" -> el item que tenga \
"backend/app/model/usuario_model.py" en sus archivos). Nombrá el/los \
item_id exactos tal como aparecen en el índice -- NUNCA inventes un id que \
no esté ahí.

3. INTERFAZ INCOMPLETA: mirá el índice de items y encontrás qué item \
debería definir lo que falta -- pero ese item YA aparece como clave en \
"dependencias (solo interfaz...)" (ya está correctamente declarado como \
dependencia). El problema no es una arista faltante en el grafo, es que la \
`interfaz` de ese item (lo único que ves de él) no expone el símbolo -- vos \
NO podés arreglar esto (no escribís contenido de ningún item), solo señalar \
con precisión qué item y qué símbolo para que el Planner le agregue el \
import literal a esa interfaz.

4. NO RESOLUBLE: ni con el índice de items encontrás de dónde podría salir \
lo que falta -- genuinamente no está en ningún lado del plan. No inventes.

""" + ESTILO_SALIDA_BREVE + """ Aplica a la explicación de DECISION, a \
"explicacion" de FALTA_DEPENDENCIA e INTERFAZ_INCOMPLETA, y al texto de \
NO_RESOLUBLE -- nunca a "items_faltantes"/"item_productor"/"simbolo_faltante" \
(esos van exactos, tal cual aparecen en el índice o en el motivo de bloqueo).

Formato de salida OBLIGATORIO -- una de las cuatro, nada de texto fuera de eso:

### DECISION
<explicación breve y accionable de la decisión tomada>

o

### FALTA_DEPENDENCIA
{"items_faltantes": ["<item_id>", ...], "explicacion": "<por qué creés que esos items tienen lo que falta>"}
### END FALTA_DEPENDENCIA

o

### INTERFAZ_INCOMPLETA
{"item_productor": "<item_id, ya declarado como dependencia>", "simbolo_faltante": "<import/símbolo exacto que su interfaz no expone>", "explicacion": "<por qué creés que este item lo define>"}
### END INTERFAZ_INCOMPLETA

o

### NO_RESOLUBLE
<qué información concreta falta y por qué no se puede inferir del contexto ni del índice de items>
"""


def _indice_items(plan: dict, item_id_actual: str) -> list[dict]:
    """
    Vista liviana de TODO el plan (solo id + archivos_destino, nunca
    detalle_tecnico/interfaz/criterios completos) para que arbitro pueda
    ubicar qué item define un símbolo que no está en las dependencias
    declaradas del item bloqueado -- sin esto no tiene forma de saber que
    ese item existe. Deliberadamente mínima (mismo criterio que
    arbol_archivos_proyecto en agents/compliance.py): un índice de
    orientación, no contenido.
    """
    return [
        {"id": item["id"], "archivos_destino": item.get("archivos_destino", [])}
        for item in plan["items"]
        if item["id"] != item_id_actual
    ]


def construir_prompt_usuario(contexto: dict, motivo_bloqueo: str, indice_items: list[dict]) -> str:
    return (
        "## decisiones_globales\n"
        f"{json.dumps(contexto['decisiones_globales'], ensure_ascii=False, indent=2)}\n\n"
        "## item bloqueado\n"
        f"{json.dumps(contexto['item'], ensure_ascii=False, indent=2)}\n\n"
        "## dependencias (solo interfaz, no la implementación)\n"
        f"{json.dumps(contexto['dependencias'], ensure_ascii=False, indent=2)}\n\n"
        "## motivo_bloqueo (razón exacta por la que Executor no pudo continuar)\n"
        f"{motivo_bloqueo}\n\n"
        "## índice de items del plan (id -> archivos que genera; para identificar quién define un símbolo que falta)\n"
        f"{json.dumps(indice_items, ensure_ascii=False, indent=2)}\n"
    )


def _ahora() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


def _log(guard: AgentFileGuard, item_id: str, motivo_bloqueo: str, resultado: dict):
    linea = json.dumps(
        {"item_id": item_id, "timestamp": _ahora(), "motivo_bloqueo": motivo_bloqueo, **resultado},
        ensure_ascii=False,
    )
    guard.append_line(Zona.HARNESS_LOGS, "arbitraje.jsonl", linea)


def _parsear_falta_dependencia(texto: str) -> dict | None:
    """None si el bloque no tiene JSON válido con 'items_faltantes' -- se
    trata igual que NO_RESOLUBLE en resolver_bloqueo (mejor perder esta
    resolución puntual que aplicar algo mal formado a plan.json)."""
    cuerpo = texto[len("### FALTA_DEPENDENCIA"):]
    if "### END FALTA_DEPENDENCIA" in cuerpo:
        cuerpo = cuerpo.split("### END FALTA_DEPENDENCIA", 1)[0]
    try:
        datos = json.loads(cuerpo.strip())
    except json.JSONDecodeError:
        return None
    items_faltantes = datos.get("items_faltantes")
    if not isinstance(items_faltantes, list) or not items_faltantes:
        return None
    return {"items_faltantes": items_faltantes, "explicacion": datos.get("explicacion", "")}


def _parsear_interfaz_incompleta(texto: str) -> dict | None:
    """
    None si el bloque no tiene JSON válido con 'item_productor' y
    'simbolo_faltante' -- se trata igual que NO_RESOLUBLE en
    resolver_bloqueo, mismo criterio que _parsear_falta_dependencia.
    """
    cuerpo = texto[len("### INTERFAZ_INCOMPLETA"):]
    if "### END INTERFAZ_INCOMPLETA" in cuerpo:
        cuerpo = cuerpo.split("### END INTERFAZ_INCOMPLETA", 1)[0]
    try:
        datos = json.loads(cuerpo.strip())
    except json.JSONDecodeError:
        return None
    item_productor = datos.get("item_productor")
    simbolo_faltante = datos.get("simbolo_faltante")
    if not item_productor or not simbolo_faltante:
        return None
    return {
        "item_productor": item_productor,
        "simbolo_faltante": simbolo_faltante,
        "explicacion": datos.get("explicacion", ""),
    }


def resolver_bloqueo(project_root: str, item_id: str, motivo_bloqueo: str) -> dict:
    """
    Devuelve un dict con "tipo" en {"decision", "falta_dependencia",
    "interfaz_incompleta", "no_resoluble"}:
      {"tipo": "decision", "texto": <str>}
      {"tipo": "falta_dependencia", "items_faltantes": [<item_id>, ...], "explicacion": <str>}
      {"tipo": "interfaz_incompleta", "item_productor": <item_id>, "simbolo_faltante": <str>, "explicacion": <str>}
      {"tipo": "no_resoluble", "explicacion": <str>}
    Nunca lanza -- un fallo del motor o una respuesta mal formada se tratan
    como "no_resoluble" (orchestrator.py deja de insistir con este item).
    """
    guard = AgentFileGuard(AGENT_NAME, project_root)
    plan = json.loads(guard.read(Zona.HARNESS_CONFIG, "plan.json"))
    contexto = construir_contexto(plan, item_id, guard)
    indice_items = _indice_items(plan, item_id)
    prompt_usuario = construir_prompt_usuario(contexto, motivo_bloqueo, indice_items)

    engine = get_engine_for_agent(AGENT_NAME)
    try:
        respuesta = engine.run(SYSTEM_PROMPT, prompt_usuario, max_tokens=16000)
    except RuntimeError as e:
        resultado = {"tipo": "no_resoluble", "explicacion": f"motor de inferencia falló: {e}"}
        _log(guard, item_id, motivo_bloqueo, {"resuelto": False, **resultado})
        return resultado

    texto = respuesta.content.strip()

    if texto.startswith("### NO_RESOLUBLE"):
        explicacion = texto[len("### NO_RESOLUBLE"):].strip(" :\n")
        resultado = {"tipo": "no_resoluble", "explicacion": explicacion}
        _log(guard, item_id, motivo_bloqueo, {"resuelto": False, **resultado})
        return resultado

    if texto.startswith("### FALTA_DEPENDENCIA"):
        parseado = _parsear_falta_dependencia(texto)
        if parseado is None:
            resultado = {
                "tipo": "no_resoluble",
                "explicacion": "arbitro respondió FALTA_DEPENDENCIA pero mal formado (JSON inválido)",
            }
            _log(guard, item_id, motivo_bloqueo, {"resuelto": False, **resultado})
            return resultado
        resultado = {"tipo": "falta_dependencia", **parseado}
        _log(guard, item_id, motivo_bloqueo, {"resuelto": True, **resultado})
        return resultado

    if texto.startswith("### INTERFAZ_INCOMPLETA"):
        parseado = _parsear_interfaz_incompleta(texto)
        if parseado is None:
            resultado = {
                "tipo": "no_resoluble",
                "explicacion": "arbitro respondió INTERFAZ_INCOMPLETA pero mal formado (JSON inválido)",
            }
            _log(guard, item_id, motivo_bloqueo, {"resuelto": False, **resultado})
            return resultado
        # "resuelto": False a propósito -- a diferencia de falta_dependencia,
        # esto NUNCA cambia plan.json (arbitro no escribe contenido de
        # items), solo deja un diagnóstico preciso registrado. orchestrator.py
        # no reintenta el item con esto solo; sigue necesitando intervención
        # del Planner sobre la interfaz señalada.
        resultado = {"tipo": "interfaz_incompleta", **parseado}
        _log(guard, item_id, motivo_bloqueo, {"resuelto": False, **resultado})
        return resultado

    # Best-effort si no respetó el formato pero igual escribió una decisión con
    # contenido: tratarlo como bloqueo del propio arbitro perdería una
    # resolución válida por un detalle de formato.
    decision = texto[len("### DECISION"):].strip(" :\n") if texto.startswith("### DECISION") else texto

    if not decision:
        resultado = {"tipo": "no_resoluble", "explicacion": "respuesta vacía"}
        _log(guard, item_id, motivo_bloqueo, {"resuelto": False, **resultado})
        return resultado

    resultado = {"tipo": "decision", "texto": decision}
    _log(guard, item_id, motivo_bloqueo, {"resuelto": True, **resultado})
    return resultado
