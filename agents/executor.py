"""
Executor: toma UN item de plan.json (ya seleccionado por orchestrator.py) y
genera el código correspondiente con el modelo local.

No decide qué item ejecutar ni si sus dependencias están completas — eso es
responsabilidad de orchestrator.py, que no está sujeto a AgentFileGuard
porque no es un agente. Executor solo sabe hacer una cosa: dado un item_id,
producir el código de sus archivos_destino usando el contexto mínimo
definido en schemas/plan.contract.md (decisiones_globales + el item en sí +
la interfaz de sus dependencias, nunca el detalle completo de otros items).
"""
import json
from datetime import datetime, timezone
from pathlib import Path

from access_control import AgentFileGuard, Zona
from engines.base import TimeoutDelMotor, MotorInalcanzable
from engines.factory import get_engine_for_agent
from interfaz_real import leer_interfaz_real, combinar_interfaz, podar_predicha_no_generada
from agents import ESTILO_SALIDA_BREVE

AGENT_NAME = "executor"

SYSTEM_PROMPT = """\
Sos el Executor de un harness de migración de monolitos Flask/Jinja2 hacia \
FastAPI (backend) + Angular (frontend). Tu única tarea es generar el código \
de UN item de un plan de migración ya decidido — no elegís arquitectura, no \
elegís nombres ni ubicación de archivos, no tomás decisiones de diseño que \
no estén ya especificadas en lo que se te da.

Reglas estrictas:
- Generá código SOLO para los archivos listados en "archivos_destino". No \
generes archivos adicionales, no toques nada fuera de esa lista.
- Seguí "detalle_tecnico" al pie de la letra.
- Aplicá las "decisiones_globales" dadas (auth, prefijo de API, casing, \
manejo de errores) tal cual están escritas.
- Si necesitás consumir algo de otro item (una dependencia), usá EXACTAMENTE \
lo declarado en su "interfaz" — no inventes firmas, rutas ni nombres \
distintos a los que ahí aparecen.
- Si necesitás importar un símbolo interno del proyecto (algo bajo "app.*") \
que NO aparece en la "interfaz" de ninguna dependencia declarada, NO \
adivines su ruta ni su módulo aunque te parezca obvia por convención de \
nombres del resto del proyecto — eso ya causó bugs reales de imports rotos. \
Tratalo igual que información insuficiente (ver regla siguiente).
- En queries (SQLAlchemy o SQL crudo), NO envuelvas una columna en una \
función dentro de un WHERE/filter (ej. LPAD(columna, ...), UPPER(columna), \
una función de fecha aplicada a la columna) — eso impide usar el índice de \
esa columna y puede causar latencia alta o full scans en tablas grandes. Si \
hace falta comparar formatos distintos, transformá el valor de entrada (el \
parámetro), no la columna.
- Si generás un archivo de dependencias (ej. requirements.txt), fijá \
versiones exactas (`paquete==X.Y.Z`), no rangos abiertos (`>=`) ni paquetes \
sin versión — salvo que "decisiones_globales" o "detalle_tecnico" indiquen \
explícitamente lo contrario. Una versión sin fijar puede actualizarse sola \
entre corridas y romper por funciones ya deprecadas.
- En endpoints de FastAPI, declará `response_model=<Schema>` cuando ya \
existe (o vas a generar) el schema de response para ese endpoint — no \
importes un schema de response para dejarlo sin usar.
- Para fechas/horas, usá `datetime.now(timezone.utc)` (`from datetime import \
datetime, timezone`) — nunca `datetime.utcnow()` (deprecado desde Python \
3.12, devuelve un datetime naive que después no se puede comparar con uno \
timezone-aware sin romper).
- Si la información dada es insuficiente o ambigua para completar el item \
sin inventar una decisión de diseño (incluido el caso anterior de un \
símbolo interno sin origen declarado), NO improvises: respondé únicamente \
con un bloque "### BLOQUEADO" indicando EXACTAMENTE qué símbolo/información \
te falta y para qué lo necesitás, y no generes ningún archivo.

""" + ESTILO_SALIDA_BREVE + """ Aplica solo al texto de "### BLOQUEADO" -- \
nunca al código que generás dentro de "### FILE".

Formato de salida OBLIGATORIO — un bloque por archivo:

### FILE: <ruta exacta tal cual aparece en archivos_destino>
<contenido completo del archivo>
### END FILE

Si generaste archivos (no si quedaste "### BLOQUEADO"), agregá al final un \
bloque "### INTERFAZ" con un array JSON — vacío ("[]") si no aplica — \
listando SOLO las funciones/clases que este item deja pensadas para que \
OTRO item futuro las importe (no todo lo que escribiste, solo lo reusable \
hacia afuera: ej. una excepción de dominio nueva, un método de repository \
pensado para reusarse). Un objeto por símbolo:

### INTERFAZ
[{"nombre": "<nombre>", "import": "<ruta.exacta.Simbolo>", "firma": "<firma o definición breve>", "uso": "<para qué serviría reusarlo>"}]
### END INTERFAZ

No escribas nada antes del primer "### FILE" ni después del último \
"### END INTERFAZ" (o "### END FILE" si no hay interfaz que reportar). No \
expliques lo que hiciste. No uses code fences (```) — el contenido del \
archivo va crudo entre los marcadores.
"""


def _item_por_id(plan: dict, item_id: str) -> dict:
    for item in plan["items"]:
        if item["id"] == item_id:
            return item
    raise ValueError(f"item_id '{item_id}' no existe en plan.json")


def _leer_codigo_generado(guard: AgentFileGuard, archivos_destino: list[str]) -> str:
    """
    Concatena el contenido actual de los archivos_destino de un item, para
    verificar contra código real (ver
    interfaz_real.py::podar_predicha_no_generada). Un archivo que todavía no
    existe (item con interfaz real pero regenerado a medias, caso raro) se
    ignora en vez de romper -- el resto del código igual sirve para podar.
    """
    partes = []
    for ruta in archivos_destino:
        try:
            partes.append(guard.read(Zona.PROJECT, ruta))
        except FileNotFoundError:
            continue
    return "\n".join(partes)


def construir_contexto(plan: dict, item_id: str, guard: AgentFileGuard | None = None) -> dict:
    """
    Arma el contexto mínimo para ejecutar un item: decisiones_globales +
    el item en sí + solo la 'interfaz' (no el item completo) de cada
    dependencia. Ver schemas/plan.contract.md, sección `interfaz`.

    Además de las dependencias declaradas en `depende_de`, siempre se
    incluye la interfaz de todo item de infraestructura compartida
    (`ticket_id: null`, ej. COAS-CORE-001/002) -- mismo criterio que ya usa
    Compliance para el contenido de archivos (ver
    agents/compliance.py::_archivos_infraestructura). Sin esto, un símbolo
    reusable de infraestructura (ej. get_db) solo le llega a un item si
    además se acordó de listar ese item de infra en depende_de -- visto en
    vivo con COAS-AUTH-004, bloqueado por no saber de dónde importar get_db
    aunque COAS-CORE-002 ya lo definía.

    `guard`, si se pasa, permite completar la interfaz predicha del Planner
    con la interfaz REAL que cada dependencia reportó al terminar (ver
    interfaz_real.py) — sin guard (ej. en tests sin filesystem real) se usa
    solo la predicha.
    """
    item = _item_por_id(plan, item_id)

    dep_ids = list(item.get("depende_de", []))
    for otro in plan["items"]:
        if otro["id"] != item_id and otro.get("ticket_id") is None and otro["id"] not in dep_ids:
            dep_ids.append(otro["id"])

    dependencias = {}
    for dep_id in dep_ids:
        dep_item = _item_por_id(plan, dep_id)
        predicha = dep_item.get("interfaz", {})
        real = leer_interfaz_real(guard, dep_id) if guard is not None else None
        if guard is not None and real:
            try:
                codigo_generado = _leer_codigo_generado(guard, dep_item.get("archivos_destino", []))
                predicha = podar_predicha_no_generada(predicha, codigo_generado)
            except PermissionError:
                # El rol de `guard` no tiene project_dir:read (ej. arbitro,
                # que a propósito nunca debería necesitar código -- ver
                # config/permissions.yaml) -- se usa la interfaz predicha tal
                # cual, sin podar, en vez de reventar el caller. Esto NO
                # relaja el permiso: arbitro sigue sin poder leer código de
                # verdad, solo deja de crashear al intentarlo indirectamente
                # vía esta poda opcional.
                pass
        dependencias[dep_id] = combinar_interfaz(predicha, real)

    return {
        "decisiones_globales": plan["decisiones_globales"],
        "item": {
            "id": item["id"],
            "tipo": item["tipo"],
            "descripcion": item["descripcion"],
            "archivos_destino": item["archivos_destino"],
            "detalle_tecnico": item["detalle_tecnico"],
        },
        "dependencias": dependencias,
    }


def construir_prompt_usuario(contexto: dict, feedback: str | None = None) -> str:
    prompt = (
        "## decisiones_globales\n"
        f"{json.dumps(contexto['decisiones_globales'], ensure_ascii=False, indent=2)}\n\n"
        "## item a ejecutar\n"
        f"{json.dumps(contexto['item'], ensure_ascii=False, indent=2)}\n\n"
        "## dependencias (solo interfaz, no la implementación)\n"
        f"{json.dumps(contexto['dependencias'], ensure_ascii=False, indent=2)}\n"
    )
    if feedback:
        prompt += (
            "\n## intento anterior RECHAZADO por Compliance — corregí esto\n"
            f"{feedback}\n"
        )
    return prompt


def _parsear_bloque_interfaz(texto: str) -> list[dict]:
    """
    Best-effort: si el bloque "### INTERFAZ" falta o está mal formado, no
    bloquea el item (los archivos ya están bien) — simplemente no queda
    interfaz real registrada esta vez, y los dependientes caen al fallback
    de la interfaz predicha del Planner (ver interfaz_real.py).
    """
    texto = texto.strip()
    if not texto.startswith("### INTERFAZ") or "### END INTERFAZ" not in texto:
        return []

    cuerpo = texto[len("### INTERFAZ"):].split("### END INTERFAZ", 1)[0]
    try:
        datos = json.loads(cuerpo.strip())
    except json.JSONDecodeError:
        return []

    if isinstance(datos, dict):
        datos = [datos]
    return datos if isinstance(datos, list) else []


def parsear_respuesta(texto: str) -> dict:
    """
    Devuelve {"bloqueado": motivo} o
    {"archivos": {ruta: contenido, ...}, "dependencia_reusable": [...]}.
    Lanza ValueError si la respuesta no respeta el formato esperado — eso
    se trata como bloqueo, no como error silencioso.
    """
    texto = texto.strip()

    if texto.startswith("### BLOQUEADO"):
        motivo = texto[len("### BLOQUEADO"):].strip(" :\n")
        return {"bloqueado": motivo or "el modelo no dio motivo"}

    texto_archivos, separador, resto = texto.partition("### INTERFAZ")
    dependencia_reusable = _parsear_bloque_interfaz(separador + resto) if separador else []

    archivos = {}
    bloques = texto_archivos.split("### FILE:")
    for bloque in bloques[1:]:
        if "### END FILE" not in bloque:
            raise ValueError(
                "Respuesta del modelo mal formada: falta '### END FILE' en un bloque."
            )
        cabecera, resto_bloque = bloque.split("\n", 1)
        ruta = cabecera.strip()
        contenido, _ = resto_bloque.split("### END FILE", 1)
        archivos[ruta] = contenido.strip("\n")

    if not archivos:
        raise ValueError(
            "Respuesta del modelo no contiene ni '### FILE:' ni '### BLOQUEADO'."
        )

    return {"archivos": archivos, "dependencia_reusable": dependencia_reusable}


def _ahora() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


def _log(guard: AgentFileGuard, item_id: str, evento: str, detalle: str):
    linea = json.dumps(
        {"item_id": item_id, "evento": evento, "timestamp": _ahora(), "detalle": detalle},
        ensure_ascii=False,
    )
    guard.append_line(Zona.HARNESS_LOGS, "executor.jsonl", linea)


def _escribir_interfaz_real(guard: AgentFileGuard, item_id: str, dependencia_reusable: list[dict]):
    """
    Se sobreescribe completo en cada regeneración del item (mismo motivo que
    los veredictos de Compliance) — nunca debe quedar describiendo una
    versión vieja del código.
    """
    payload = {
        "item_id": item_id,
        "timestamp": _ahora(),
        "interfaz": {"dependencia_reusable": dependencia_reusable},
    }
    guard.write(
        Zona.HARNESS_INTERFACES,
        f"{item_id}.json",
        json.dumps(payload, ensure_ascii=False, indent=2),
    )


def ejecutar_item(
    project_root: str, item_id: str, feedback: str | None = None,
    agente: str = AGENT_NAME, max_tokens: int = 20000, enable_thinking: bool = True,
) -> dict:
    """
    Ejecuta un único item. No valida si sus dependencias están completas —
    eso ya lo hizo orchestrator.py antes de llamar acá.

    `feedback`: si este item ya fue rechazado por Compliance antes, el texto
    de por qué (armado por orchestrator.py a partir del veredicto anterior),
    para que el reintento no repita el mismo error a ciegas.

    `agente`: nombre de agente a usar para permisos (AgentFileGuard) y motor
    (models.yaml) — por defecto "executor" (lm_studio). Permite reusar toda
    esta lógica para "executor_senior" (motor más fuerte, ver
    orchestrator.py::loop()) sin duplicar código; mismos permisos que
    executor en permissions.yaml.

    `enable_thinking`: se pasa tal cual al motor (ver ModelEngine.run) —
    orchestrator.py::loop() lo pone en False solo para el primer intento de
    cada item.
    """
    guard = AgentFileGuard(agente, project_root)
    plan = json.loads(guard.read(Zona.HARNESS_CONFIG, "plan.json"))
    item = _item_por_id(plan, item_id)

    contexto = construir_contexto(plan, item_id, guard)
    prompt_usuario = construir_prompt_usuario(contexto, feedback=feedback)

    _log(guard, item_id, "iniciado", f"generando {', '.join(item['archivos_destino'])} (agente: {agente})")

    engine = get_engine_for_agent(agente)
    try:
        respuesta = engine.run(SYSTEM_PROMPT, prompt_usuario, max_tokens=max_tokens, enable_thinking=enable_thinking)
    except TimeoutDelMotor as e:
        # A diferencia de 'bloqueado' (Executor SÍ respondió pero le falta
        # info real del plan -- no tiene sentido reintentar sin que cambie
        # algo, ver seleccionar_siguiente_para_loop), un timeout es un corte
        # de infraestructura: no se generó nada. Se loguea 'finalizado' sin
        # archivos para que el loop normal lo trate como un intento fallido
        # más -- Compliance revalida el código previo (sigue rechazado por lo
        # mismo de antes), consume el intento, y si se agotan escala solo a
        # executor_senior (mismo camino que un rechazo normal).
        _log(guard, item_id, "finalizado", f"intento fallido (0 archivos): {e}")
        return {"estado": "finalizado", "archivos": []}
    except MotorInalcanzable:
        # No se pudo conectar en absoluto (motor local caído/sin red) --
        # distinto de TimeoutDelMotor (conectó, no respondió a tiempo) y de un
        # RuntimeError de contenido (bucle, respuesta rara): acá no hay nada
        # que loguear como intento del item, porque no fue un intento real.
        # Se deja sin capturar a propósito -- solo orchestrator.py decide si
        # ofrecer un motor alternativo (ver engines/factory.py::set_override),
        # Executor no toma esa decisión solo.
        raise
    except RuntimeError as e:
        _log(guard, item_id, "bloqueado", f"motor de inferencia falló: {e}")
        return {"estado": "bloqueado", "motivo": str(e)}

    try:
        resultado = parsear_respuesta(respuesta.content)
    except ValueError as e:
        # Guardamos la respuesta cruda completa (no solo el error) -- sin esto,
        # un parseo mal formado no se puede diagnosticar después: hay que
        # reproducir la llamada a mano para ver qué escribió el modelo.
        _log(
            guard, item_id, "bloqueado",
            f"respuesta mal formada: {e}\n\n--- respuesta cruda del modelo ---\n{respuesta.content}",
        )
        return {"estado": "bloqueado", "motivo": str(e)}

    if "bloqueado" in resultado:
        _log(guard, item_id, "bloqueado", resultado["bloqueado"])
        return {"estado": "bloqueado", "motivo": resultado["bloqueado"]}

    archivos = resultado["archivos"]
    esperados = set(item["archivos_destino"])
    generados = set(archivos.keys())

    if generados != esperados:
        motivo = (
            f"archivos generados ({sorted(generados)}) no coinciden con "
            f"archivos_destino ({sorted(esperados)})"
        )
        _log(guard, item_id, "bloqueado", motivo)
        return {"estado": "bloqueado", "motivo": motivo}

    for ruta, contenido in archivos.items():
        guard.write(Zona.PROJECT, ruta, contenido)

    _escribir_interfaz_real(guard, item_id, resultado.get("dependencia_reusable", []))

    _log(guard, item_id, "finalizado", f"archivos escritos: {', '.join(sorted(archivos))}")
    return {"estado": "finalizado", "archivos": sorted(archivos)}
