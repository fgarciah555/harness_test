"""
Documentador: cuando un item tuvo fricción real (uno o más rechazos antes de
aprobarse), analiza el error real + la resolución real que ya quedó grabada
en .harness/logs/reporte_fallas.md y el código final ya aprobado, y propone
un candidato de documentación -- nunca lo escribe directo en knowledge/ ni
en .agents/rules/.

No decide cuándo correr -- eso lo hace orchestrator.py::validar_con_format_check,
solo para items que pasaron de 'rechazado' a 'completado' (ver
_item_tuvo_rechazos). Un fallo acá nunca bloquea ni reintenta el item real,
es puramente aditivo.
"""
import json
import re
from datetime import datetime, timezone

from access_control import AgentFileGuard, Zona
from engines.base import MotorInalcanzable
from engines.factory import get_engine_for_agent
from agents import ESTILO_SALIDA_BREVE

AGENT_NAME = "documentador"

CLASIFICACIONES = ("patron_libreria", "decision_arquitectura", "bug_negocio_proyecto")

SYSTEM_PROMPT = """\
Sos el agente Documentador de un harness de migración de monolitos hacia \
FastAPI + Angular. Tu tarea: dado un item que fue rechazado una o más veces \
y después se aprobó, leer el rechazo real y la resolución real (el código \
final que ya pasó Compliance/tests), y decidir si esto vale la pena \
documentar para que no se repita en otro item o en otro proyecto.

No inventás nada nuevo -- el error y el fix ya pasaron de verdad, tu \
trabajo es clasificar y resumir, no investigar ni recordar de tu \
entrenamiento. Si necesitás decir cómo se usa correctamente una librería, \
usá EXACTAMENTE lo que ves en "código final aprobado" -- nunca completes \
con lo que crees que es correcto de memoria.

Criterio de clasificación (mismo que ya usa knowledge/README.md del \
harness):
- "patron_libreria": el rechazo fue por un patrón de uso incorrecto de una \
librería/framework (algo que depende de QUÉ librería toca el item, no algo \
que aplique siempre) -- ej. una firma de función real distinta a la \
asumida, un import que va en otro módulo, un método que no existe. \
Reusable en cualquier proyecto que use esa misma librería.
- "decision_arquitectura": el rechazo fue por no seguir una convención de \
ESTE proyecto (estructura de carpetas, naming, cómo se maneja un error, una \
decisión de diseño ya tomada) -- reusable en este proyecto pero no \
necesariamente en otro, no es sobre una librería externa.
- "bug_negocio_proyecto": el rechazo fue por lógica de negocio específica \
de este proyecto (un cálculo mal hecho, una regla de negocio mal \
interpretada) -- no generaliza a nada, no hace falta proponer una entrada \
de conocimiento, alcanza con un resumen de una línea.

""" + ESTILO_SALIDA_BREVE + """ Aplica a "resumen" y a la prosa de \
"candidato_entrada" (la línea de qué resuelve, el motivo del rechazo) -- \
nunca al fragmento de código dentro de "candidato_entrada", eso va \
literal, tal cual salió del código real aprobado.

Formato de salida OBLIGATORIO -- SOLO un objeto JSON, nada de texto antes \
ni después, sin code fences:

{
  "clasificacion": "patron_libreria" | "decision_arquitectura" | "bug_negocio_proyecto",
  "resumen": "<1-2 oraciones: qué pasó y cómo se resolvió>",
  "candidato_entrada": "<markdown listo para copiar a knowledge/ o .agents/rules/, o null si clasificacion es bug_negocio_proyecto>"
}

Si "clasificacion" es "patron_libreria" o "decision_arquitectura", \
"candidato_entrada" es OBLIGATORIO y tiene que seguir este formato \
(adaptado de knowledge/README.md -- notá que dice "confirmado en código \
real", NUNCA "verificado contra documentación oficial": vos no tenés forma \
de chequear la doc real, dejale esa verificación explícita al humano que \
revise esto antes de darlo por bueno):

## <qué hace / qué resuelve, una línea>

**Fuente:** patrón confirmado en código real que pasó Compliance/tests en \
este proyecto -- NO verificado contra documentación oficial todavía \
(revisar antes de confiar en esto en otro proyecto).
**Patrón correcto (código real, ya aprobado):**
```
<fragmento real tomado de "código final aprobado", no inventado>
```
**Patrón incorrecto que causó el rechazo:** `<qué estaba mal>` -- por qué \
falló (motivo real del rechazo).
**Encontrado en:** <item_id>, <fecha del rechazo si la tenés>.
"""


def _item_por_id(plan: dict, item_id: str) -> dict:
    for item in plan["items"]:
        if item["id"] == item_id:
            return item
    raise ValueError(f"item_id '{item_id}' no existe en plan.json")


def bloques_de_rechazo(texto_reporte: str, item_id: str) -> list[str]:
    """
    Extrae de reporte_fallas.md los bloques que pertenecen a item_id.
    Cada bloque empieza en una línea "## {item_id} ..." (match exacto, con
    espacio después del id -- así "ITEM-1" no matchea bloques de
    "ITEM-10") y termina antes del próximo header "## " o al final del
    archivo.
    """
    patron = re.compile(rf"^## {re.escape(item_id)} ")
    bloques: list[str] = []
    actual: list[str] | None = None

    for linea in texto_reporte.splitlines():
        if linea.startswith("## "):
            if actual is not None:
                bloques.append("\n".join(actual).strip())
            actual = [linea] if patron.match(linea) else None
            continue
        if actual is not None:
            actual.append(linea)

    if actual is not None:
        bloques.append("\n".join(actual).strip())

    return bloques


def _leer_codigo_final(guard: AgentFileGuard, archivos_destino: list[str]) -> dict[str, str | None]:
    codigo = {}
    for ruta in archivos_destino:
        try:
            codigo[ruta] = guard.read(Zona.PROJECT, ruta)
        except FileNotFoundError:
            codigo[ruta] = None
    return codigo


def construir_contexto(plan: dict, item_id: str, guard: AgentFileGuard) -> dict:
    item = _item_por_id(plan, item_id)

    try:
        reporte = guard.read(Zona.HARNESS_LOGS, "reporte_fallas.md")
    except FileNotFoundError:
        reporte = ""
    bloques = bloques_de_rechazo(reporte, item_id)

    # Desde que reporte_fallas.md referencia el ticket de reintento en vez
    # de embeber el feedback completo (ver orchestrator.py::
    # _escribir_reporte_rechazo, schemas/plan.contract.md "Ticket de
    # reintento"), el detalle real de un item que osciló y se terminó
    # arreglando vive acá, no en bloques_rechazo -- sin esto, Documentador
    # vería mucho menos que antes para justo el caso más interesante.
    try:
        ticket_reintento = guard.read(Zona.HARNESS_LOGS, f"tickets/{item_id}.md")
    except FileNotFoundError:
        ticket_reintento = ""

    return {
        "item": {
            "id": item["id"],
            "tipo": item["tipo"],
            "descripcion": item["descripcion"],
            "detalle_tecnico": item.get("detalle_tecnico", ""),
            "criterios_aceptacion": item["criterios_aceptacion"],
        },
        "bloques_rechazo": bloques,
        "ticket_reintento": ticket_reintento,
        "codigo_final_aprobado": _leer_codigo_final(guard, item["archivos_destino"]),
    }


def construir_prompt_usuario(contexto: dict) -> str:
    return (
        "## item (ya completado, aprobado)\n"
        f"{json.dumps(contexto['item'], ensure_ascii=False, indent=2)}\n\n"
        "## rechazo(s) reales de este item, en orden (extraídos de reporte_fallas.md)\n"
        f"{json.dumps(contexto['bloques_rechazo'], ensure_ascii=False, indent=2)}\n\n"
        "## ticket de reintento completo (historial de todos los intentos, y 'Hechos "
        "verificados' si alguien los completó a mano)\n"
        f"{contexto['ticket_reintento'] or '(no quedó ticket -- se aprobó al primer intento)'}\n\n"
        "## código final aprobado (la resolución real)\n"
        f"{json.dumps(contexto['codigo_final_aprobado'], ensure_ascii=False, indent=2)}\n"
    )


def parsear_respuesta(texto: str) -> dict:
    texto = texto.strip()

    if texto.startswith("```"):
        texto = texto.strip("`").strip()
        if texto.startswith("json"):
            texto = texto[4:].strip()

    data = json.loads(texto)

    if data.get("clasificacion") not in CLASIFICACIONES:
        raise ValueError(f"clasificacion inválida o ausente: {data.get('clasificacion')!r}")
    if not data.get("resumen"):
        raise ValueError("falta 'resumen' en la respuesta")
    if data["clasificacion"] != "bug_negocio_proyecto" and not data.get("candidato_entrada"):
        raise ValueError(
            f"clasificacion '{data['clasificacion']}' requiere 'candidato_entrada', vino vacío/ausente"
        )

    return data


def _ahora() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


MARCA_SUPERSEDIDO = "**⚠ SUPERSEDIDO** por una aprobación posterior de este mismo item — ver el bloque más abajo con timestamp más reciente antes de confiar en este.\n"


def _marcar_candidatos_previos_superados(guard: AgentFileGuard, item_id: str) -> None:
    """
    Si `item_id` ya tiene candidato(s) previo(s) en candidatos_conocimiento.md
    (de una aprobación anterior de este mismo item, invalidada después por
    una regeneración posterior -- ver invalidar_dependientes en
    orchestrator.py), les inserta una marca de "supersedido" ANTES de
    agregar el candidato nuevo.

    Por qué hace falta: un item puede aprobarse, quedar documentado, y más
    tarde regenerarse de nuevo por una razón totalmente ajena (ej. se
    invalidó como dependiente de otro item que cambió) -- el candidato
    viejo seguía siendo verdad CUANDO se escribió, pero deja de reflejar el
    código real sin ningún aviso. Encontrado en la práctica (2026-08-26,
    backend+DAL separados): un item de autenticación quedó documentado 4
    veces en la misma sesión, y los primeros 3 candidatos describían un uso
    de `CredencialesInvalidasError(code=..., message=...)` que una
    regeneración posterior (por otro motivo) reemplazó por
    `CredencialesInvalidasError(message=...)` -- sin esta marca, alguien
    revisando `candidatos_conocimiento.md` no tiene forma de saber cuál de
    los 4 refleja el código final real sin comparar manualmente contra el
    proyecto.

    No borra ni reescribe el contenido de los candidatos viejos (siguen
    siendo auditables tal cual se escribieron) -- solo antepone una marca
    dentro de su propio bloque, antes de la primera línea de resumen.
    """
    try:
        contenido = guard.read(Zona.HARNESS_LOGS, "candidatos_conocimiento.md")
    except FileNotFoundError:
        return

    # Separador real entre bloques: cada _formatear_bloque_salida() termina
    # en "...---\n\n", pero AgentFileGuard.append_line() normaliza a un solo
    # "\n" de cierre (rstrip("\n") + "\n") -- el archivo en disco queda
    # "cuerpo1\n\n---\n## ID2 — ...", NO "cuerpo1\n\n---\n\n## ID2 — ...".
    # Separar por "\n\n---\n\n" (asumiendo el formato "ideal") nunca
    # matchea nada real y deja todo el archivo como un solo bloque -- bug
    # real encontrado por un test antes de que esto se usara en producción.
    SEPARADOR = "---\n"
    patron_header = re.compile(rf"^## {re.escape(item_id)} — ")
    bloques = contenido.split(SEPARADOR)
    cambiado = False

    for i, bloque in enumerate(bloques):
        lineas = bloque.split("\n", 1)
        if not lineas or not patron_header.match(lineas[0]):
            continue
        resto = lineas[1] if len(lineas) > 1 else ""
        if MARCA_SUPERSEDIDO.strip() in resto:
            continue
        bloques[i] = f"{lineas[0]}\n\n{MARCA_SUPERSEDIDO}{resto.lstrip(chr(10))}"
        cambiado = True

    if cambiado:
        guard.write(Zona.HARNESS_LOGS, "candidatos_conocimiento.md", SEPARADOR.join(bloques))


def _formatear_bloque_salida(item_id: str, resultado: dict) -> str:
    clasificacion = resultado["clasificacion"]
    cuerpo = resultado.get("candidato_entrada") or "(sin candidato -- bug específico de este proyecto, no generaliza)"
    return (
        f"## {item_id} — candidato de documentación ({clasificacion}) — {_ahora()}\n\n"
        f"{resultado['resumen']}\n\n"
        f"{cuerpo}\n\n---\n\n"
    )


def documentar_resolucion(project_root: str, item_id: str) -> dict:
    guard = AgentFileGuard(AGENT_NAME, project_root)
    plan = json.loads(guard.read(Zona.HARNESS_CONFIG, "plan.json"))

    contexto = construir_contexto(plan, item_id, guard)
    if not contexto["bloques_rechazo"]:
        return {"estado": "omitido", "motivo": "no se encontraron bloques de rechazo para este item"}

    prompt_usuario = construir_prompt_usuario(contexto)

    engine = get_engine_for_agent(AGENT_NAME)
    try:
        respuesta = engine.run(SYSTEM_PROMPT, prompt_usuario, max_tokens=8000)
    except MotorInalcanzable:
        # Igual que en agents/executor.py: no se captura como 'error' -- esto
        # nunca es un fallo de contenido, es el motor local caído/sin red.
        # Solo orchestrator.py decide si ofrecer un motor alternativo.
        raise
    except RuntimeError as e:
        return {"estado": "error", "motivo": f"motor de inferencia falló: {e}"}

    try:
        resultado = parsear_respuesta(respuesta.content)
    except (json.JSONDecodeError, ValueError) as e:
        return {"estado": "error", "motivo": f"respuesta mal formada: {e}"}

    _marcar_candidatos_previos_superados(guard, item_id)
    bloque = _formatear_bloque_salida(item_id, resultado)
    guard.append_line(Zona.HARNESS_LOGS, "candidatos_conocimiento.md", bloque)

    return {"estado": "documentado", "clasificacion": resultado["clasificacion"]}
