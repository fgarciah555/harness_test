"""
Orquestador: decide qué item de plan.json ejecutar a continuación y llama a
Executor. No es un agente — no pasa por AgentFileGuard ni por
config/permissions.yaml, porque no genera código ni toma decisiones de
migración, solo lee estado y decide orden. Ver "Estado efectivo (calculado)"
en schemas/plan.contract.md para el algoritmo que implementa.

Uso:
    python orchestrator.py /ruta/al/proyecto                          # ejecuta el próximo item listo (Executor)
    python orchestrator.py /ruta/al/proyecto --rol compliance          # valida el próximo item generado (Compliance)
    python orchestrator.py /ruta/al/proyecto --item PED-002            # fuerza un item puntual (con --rol executor|compliance)
    python orchestrator.py /ruta/al/proyecto --status                  # solo muestra estados, no ejecuta
    python orchestrator.py /ruta/al/proyecto --loop                    # encadena Executor/Compliance, pregunta entre pasos
    python orchestrator.py /ruta/al/proyecto --loop --sin-confirmar    # ídem, sin preguntar (desatendido)
    python orchestrator.py /ruta/al/proyecto --loop --max-reintentos 2 # tras un rechazo, 2 reintentos en vez de 1
    python orchestrator.py /ruta/al/proyecto --item PED-002 --senior   # fuerza executor_senior para ESE item ya (sin esperar a que --loop agote los reintentos normales)

En --loop: un item rechazado (por Compliance, o por el veredicto sintético
de format_check/frontend_check/smoke_test/docker_check) actualiza
determinísticamente un ticket en .harness/logs/tickets/<item_id>.md (ver
schemas/plan.contract.md, "Ticket de reintento") con lo esperado
(criterios_aceptacion), el historial completo de intentos y el código
actual, y se reintenta con Executor pasándole ese ticket completo (no un
feedback recalculado en memoria en cada llamada). "Hechos verificados" es
la única sección de ese archivo que el orquestador nunca toca: población
manual (Analyzer+Planner) para el caso de oscilación (un reintento arregla
una cosa y rompe otra ya corregida). Esto sigue hasta agotar
--max-reintentos (default 1, o sea 2 intentos totales con el executor
normal — el primero sin thinking, el reintento con thinking normal; ver
comentario sobre ESTADOS más abajo). Si se agotan, o si Executor queda
'bloqueado' (no reintenta solo — sin info nueva no hay razón para esperar
otro resultado), se escala a executor_senior si está configurado (ver más
abajo, recibe el mismo ticket con el historial completo) o se escribe un
resumen en .harness/logs/reporte_fallas.md y el loop sigue con otros items
independientes.

Antes de cada reintento (con --loop y sin --sin-confirmar) se escribe
también un reporte en .harness/logs/reporte_fallas.md (referenciando el
ticket) y se pregunta cómo seguir: reintentar con el ticket tal cual,
pausar para completar "Hechos verificados" a mano antes de reintentar,
excluir el item del loop para arreglarlo a mano (después revalidar con
--item <id> --rol compliance — Compliance sigue siendo el gate incluso para
fixes manuales), o detener el loop. Cada decisión queda registrada en
.harness/logs/decisiones_reintento.jsonl. Con --sin-confirmar se salta la
pregunta (se reintenta con el ticket tal cual) pero el reporte se sigue
escribiendo.

Antes de cada validación (--rol compliance y dentro de --loop) corre
primero filtros determinísticos y gratis (sin LLM): format_check.py
(imports internos rotos y nombres que se pisan, solo .py) y, según el
`tipo` del item, smoke_test.py (backend, pytest real, solo si el item
declara `tests_requeridos`) o frontend_check.py (frontend, `ng build`
real, siempre). Si cualquiera encuentra algo, rechaza directo con un
veredicto sintético y ni siquiera llama a Compliance — ver format_check.py,
smoke_test.py, frontend_check.py y schemas/plan.contract.md.

Si el motor local (LM Studio) de 'executor'/'documentador' está inalcanzable
(no responde la conexión — distinto de un timeout, ver
engines/base.py::MotorInalcanzable), se pregunta si activar Kimi como
alternativa para el resto de esta corrida del proceso (nunca se edita
config/models.yaml — ver engines/factory.py::set_override y
_con_fallback_motor_local() más abajo). Con --sin-confirmar no hay a quién
preguntarle, así que se corta en vez de adivinar. Cada decisión (activar o
no) queda registrada en .harness/logs/decisiones_motor.jsonl — ver
README.md, "Motor por API (Kimi)".
"""
import argparse
import json
import re
import yaml
from datetime import datetime
from pathlib import Path

from checks import format_check
from checks import smoke_test
from checks import frontend_check
from checks import docker_check
from checks.plan_validator import validar_plan
from checks.api_endpoints import regenerar_catalogo_endpoints
from engines.base import MotorInalcanzable
from engines.factory import set_override, get_override
from agents.executor import ejecutar_item
from agents.arbitro import resolver_bloqueo
from agents.compliance import validar_item
from agents.documentador import documentar_resolucion

# Modelo Kimi que usa el fallback de motor local caído para cada agente (ver
# _con_fallback_motor_local()) -- distinto por agente a propósito:
# 'executor' genera código real, así que usa la variante especializada en
# código; 'documentador' solo clasifica/resume texto (rechazo real + fix
# real ya ocurridos, ver agents/documentador.py) -- mismo principio por el
# que ya corre en el motor local más liviano y nunca en deepseek-reasoner
# (ver README.md, "Quién hace qué"), así que no necesita ni la variante
# -code ni la versión más nueva/cara. Un agente sin entrada acá (ej.
# 'arbitro', que corre en deepseek, no lm_studio) no tiene fallback a Kimi
# -- ver _con_fallback_motor_local, que corta sin preguntar si el agente no
# está en este mapa.
KIMI_MODEL_FALLBACK = {
    "executor": "kimi-k2.7-code",
    "documentador": "kimi-k2.6",
}

ESTADOS = ("pendiente", "en_progreso", "bloqueado", "completado", "rechazado", "omitido")

# Con qwen/qwen3.8-27b, thinking "normal" (reasoning_effort default xhigh) se
# vio inestable en pruebas en vivo (2026-08-21): 3 corridas seguidas del mismo
# reintento fallaron por 3 motivos distintos (truncó a mitad de archivo,
# bloque mal formado, agotó 32000 tokens completos rumiando sobre qué incluir
# en "### INTERFAZ" sin converger) -- con el modelo cargado en LM Studio con
# un context length chico (default). Sospecha sin confirmar todavía: no era
# el thinking en sí, sino que el contexto disponible era insuficiente para
# el prompt + el razonamiento largo. Repitiendo con el modelo recargado a
# 64k de contexto para confirmar. Mientras tanto: primer intento del
# executor sin thinking (ver loop() y main()), reintento con thinking
# normal + más headroom de max_tokens (MAX_TOKENS_EXECUTOR_THINKING).
MAX_TOKENS_EXECUTOR_THINKING = 48000


def _parse_ts(ts: str) -> datetime:
    return datetime.fromisoformat(ts)


def _veredicto_desactualizado(eventos_item: list[dict], veredicto: dict | None) -> bool:
    """
    True si Executor generó código de nuevo (evento más reciente) después del
    último veredicto de Compliance — pasa en un reintento tras un rechazo.
    Ese veredicto viejo ya no vale, hay que volver a validar el código nuevo.
    """
    if veredicto is None or not eventos_item:
        return False
    return _parse_ts(eventos_item[-1]["timestamp"]) > _parse_ts(veredicto["timestamp"])


def _cargar_plan(project_root: Path) -> dict:
    ruta = project_root / ".harness" / "config" / "plan.json"
    if not ruta.exists():
        raise FileNotFoundError(f"No hay plan.json en {ruta}")
    plan = json.loads(ruta.read_text())

    errores = validar_plan(plan)
    if errores:
        detalle = "\n".join(f"  - {e}" for e in errores)
        raise ValueError(f"plan.json inválido ({ruta}):\n{detalle}")

    return plan


def _leer_eventos_executor(project_root: Path) -> dict[str, list[dict]]:
    ruta = project_root / ".harness" / "logs" / "executor.jsonl"
    eventos: dict[str, list[dict]] = {}
    if not ruta.exists():
        return eventos
    for linea in ruta.read_text().splitlines():
        linea = linea.strip()
        if not linea:
            continue
        evento = json.loads(linea)
        eventos.setdefault(evento["item_id"], []).append(evento)
    return eventos


def _leer_veredictos(project_root: Path) -> dict[str, dict]:
    carpeta = project_root / ".harness" / "validation"
    veredictos = {}
    if not carpeta.exists():
        return veredictos
    for archivo in carpeta.glob("*.json"):
        data = json.loads(archivo.read_text())
        veredictos[data["item_id"]] = data
    return veredictos


def _item_tuvo_rechazos(root: Path, item_id: str) -> bool:
    """
    True si reporte_fallas.md tiene al menos un bloque de este item_id --
    dispara el agente documentador (ver validar_con_format_check). Grep
    determinístico, no pasa por AgentFileGuard (mismo criterio que el resto
    de orchestrator.py, que no es uno de los 4 agentes).
    """
    ruta = root / ".harness" / "logs" / "reporte_fallas.md"
    if not ruta.exists():
        return False
    patron = re.compile(rf"^## {re.escape(item_id)} ", re.MULTILINE)
    return bool(patron.search(ruta.read_text()))


def _dependientes_transitivos(plan: dict, item_id: str) -> set[str]:
    """Todos los items que dependen de item_id, directa o indirectamente."""
    dependientes: set[str] = set()
    frontera = {item_id}
    cambio = True
    while cambio:
        cambio = False
        for item in plan["items"]:
            iid = item["id"]
            if iid in dependientes:
                continue
            if set(item.get("depende_de", [])) & (frontera | dependientes):
                dependientes.add(iid)
                cambio = True
    return dependientes


def invalidar_dependientes(project_root: str, item_id: str) -> list[str]:
    """
    Si item_id ya estaba 'aprobado' y se ejecuta de nuevo (reintento tras
    aprobado, típicamente forzado a mano para corregir un bug encontrado
    después), la regeneración completa del item no garantiza preservar la
    misma forma (nombres de clases/funciones) que la versión anterior. Los
    items que dependen de él pudieron haberse generado contra esa forma
    vieja. Se les borra el veredicto (no se les toca el código) para que
    vuelvan a pasar por validación antes de seguir confiando en que están
    'completado'. Ver schemas/plan.contract.md.
    """
    root = Path(project_root).resolve()
    plan = _cargar_plan(root)
    dependientes = _dependientes_transitivos(plan, item_id)

    invalidados = []
    for dep_id in dependientes:
        ruta = root / ".harness" / "validation" / f"{dep_id}.json"
        if ruta.exists():
            ruta.unlink()
            invalidados.append(dep_id)
    return invalidados


def _preguntar_activar_kimi(agente: str, modelo_kimi: str, detalle: str) -> bool:
    print(f"\n--- No se pudo conectar al motor local configurado para '{agente}' ---")
    print(detalle)
    respuesta = input(
        f"¿Activar Kimi ({modelo_kimi}) como alternativa para '{agente}' "
        "por el resto de esta corrida? [s/N]: "
    ).strip().lower()
    return respuesta in ("s", "si", "sí", "y", "yes")


def _registrar_decision_motor(project_root: Path, agente: str, activo: bool, motivo: str):
    """
    Mismo criterio que _registrar_decision_reintento -- cierra el gap de "no
    hay registro" también para esta clase de intervención humana (activar o
    no un motor alternativo), en un archivo aparte para no mezclar semántica
    con las decisiones de reintento Executor-Compliance.
    """
    ruta = project_root / ".harness" / "logs" / "decisiones_motor.jsonl"
    ruta.parent.mkdir(parents=True, exist_ok=True)
    entrada = {
        "agente": agente,
        "decision": "activar_kimi" if activo else "no_activar_kimi",
        "motivo": motivo,
        "timestamp": datetime.now().astimezone().isoformat(),
    }
    with ruta.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entrada, ensure_ascii=False) + "\n")


def _registrar_metrica_agente(project_root: Path, item_id: str, agente: str):
    """
    Una línea por cada vez que un item PASA por uno de los 4 agentes reales
    (executor, executor_senior, compliance, arbitro, documentador) -- no los
    chequeos determinísticos (format_check/frontend_check/smoke_test/
    docker_check), que no son "agentes" en el vocabulario del harness (ver
    "Decisión de arquitectura importante" en handoff.md). Cuenta intentos,
    no resultados -- se registra sea cual sea el desenlace (aprobado,
    rechazado, bloqueado, motor_inalcanzable). Base para el resumen que
    imprime `loop()` al terminar y `--metricas` (ver `calcular_metricas_agentes`).
    """
    ruta = project_root / ".harness" / "logs" / "metricas_agentes.jsonl"
    ruta.parent.mkdir(parents=True, exist_ok=True)
    entrada = {
        "item_id": item_id,
        "agente": agente,
        "timestamp": datetime.now().astimezone().isoformat(),
    }
    with ruta.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entrada, ensure_ascii=False) + "\n")


def calcular_metricas_agentes(project_root: str, desde_linea: int = 0) -> dict[str, dict[str, int]]:
    """
    { item_id: { agente: cantidad_de_veces }, ... } leído de
    .harness/logs/metricas_agentes.jsonl. Vacío (no {} por item) si el
    archivo no existe todavía -- un proyecto que nunca corrió no tiene
    métricas, no es un error.

    `desde_linea`: acota a lo agregado DESPUÉS de esa línea (0-indexed) en
    vez de todo el historial acumulado del archivo -- así `loop()` puede
    mostrar solo lo de ESTA corrida ("sesión", en las palabras de Felipe:
    una vez que dice "hagamos el trabajo" hasta que termina, no el
    acumulado de todas las veces que se corrió el proyecto). `--metricas`
    (consulta manual, sin acotar) sigue mostrando el historial completo.
    """
    ruta = Path(project_root).resolve() / ".harness" / "logs" / "metricas_agentes.jsonl"
    metricas: dict[str, dict[str, int]] = {}
    if not ruta.exists():
        return metricas
    for linea in ruta.read_text().splitlines()[desde_linea:]:
        linea = linea.strip()
        if not linea:
            continue
        entrada = json.loads(linea)
        por_item = metricas.setdefault(entrada["item_id"], {})
        por_item[entrada["agente"]] = por_item.get(entrada["agente"], 0) + 1
    return metricas


AGENTES_CON_METRICA = ("executor", "executor_senior", "compliance", "arbitro", "documentador")


def _tabla_metricas_agentes(project_root: str, desde_linea: int = 0) -> str:
    """Devuelve la tabla ya formateada, o un string vacío si no hay nada que mostrar."""
    metricas = calcular_metricas_agentes(project_root, desde_linea=desde_linea)
    if not metricas:
        return ""

    ancho_id = max(len("TOTAL"), max(len(item_id) for item_id in metricas))
    encabezado = f"{'item_id':{ancho_id}s}  " + "  ".join(f"{a:>15s}" for a in AGENTES_CON_METRICA)
    lineas = [encabezado, "-" * len(encabezado)]

    totales = {a: 0 for a in AGENTES_CON_METRICA}
    for item_id in sorted(metricas):
        conteos = metricas[item_id]
        fila = f"{item_id:{ancho_id}s}  " + "  ".join(f"{conteos.get(a, 0):>15d}" for a in AGENTES_CON_METRICA)
        lineas.append(fila)
        for a in AGENTES_CON_METRICA:
            totales[a] += conteos.get(a, 0)

    lineas.append("-" * len(encabezado))
    lineas.append(f"{'TOTAL':{ancho_id}s}  " + "  ".join(f"{totales[a]:>15d}" for a in AGENTES_CON_METRICA))
    return "\n".join(lineas)


def _imprimir_metricas_agentes(project_root: str, desde_linea: int = 0):
    tabla = _tabla_metricas_agentes(project_root, desde_linea=desde_linea)
    if not tabla:
        if desde_linea:
            print("\nEsta sesión no pasó por ningún agente todavía (nada que resumir).")
        else:
            print("\nSin métricas de agentes todavía (.harness/logs/metricas_agentes.jsonl no existe).")
        return
    alcance = "de esta sesión" if desde_linea else "de todo el historial del proyecto"
    print(f"\nCuántas veces pasó cada item por cada agente ({alcance}):\n")
    print(tabla)


def _con_fallback_motor_local(project_root: Path, agente: str, confirmar: bool, fn):
    """
    Corre fn() (una llamada que internamente resuelve el motor de `agente`
    vía engines.factory.get_engine_for_agent). Si el motor configurado está
    inalcanzable (MotorInalcanzable -- típicamente LM Studio local
    apagado/sin red, ver engines/lm_studio.py), pregunta si activar Kimi
    como alternativa para el resto de esta corrida del PROCESO
    (engines.factory.set_override -- en memoria, nunca toca
    config/models.yaml, ver ese módulo). Devuelve un dict con
    estado="motor_inalcanzable" en vez de propagar la excepción cuando el
    fallback no se activa, para que loop()/main() lo traten con el mismo
    mecanismo de "resultado con estado" que ya usan bloqueado/rechazado, no
    con un traceback crudo.

    Nunca se pregunta dos veces por el mismo agente en la misma corrida: si
    ya hay un override activo y falla igual (Kimi también inalcanzable,
    KIMI_API_KEY inválida), se corta ahí directo -- no tiene sentido
    reofrecer la misma alternativa que ya se activó y ya falló.

    Si `agente` no tiene un modelo Kimi mapeado en KIMI_MODEL_FALLBACK (ej.
    'arbitro', que corre en deepseek, no lm_studio -- este fallback no aplica
    ahí), se corta directo sin preguntar -- no hay alternativa razonable que
    ofrecer.

    `confirmar=False` (--sin-confirmar, corrida desatendida): no hay a quién
    preguntarle, y activar un motor de pago sin que el usuario lo haya
    pedido no es una decisión que el harness deba tomar solo (mismo
    principio que ya rige el resto de los gates de loop()) -- se corta
    directo, mismo resultado que si el usuario hubiera contestado que no.
    """
    try:
        return fn()
    except MotorInalcanzable as e:
        if get_override(agente) is not None:
            return {
                "estado": "motor_inalcanzable",
                "motivo": f"Kimi (fallback ya activo para '{agente}') también inalcanzable: {e}",
            }

        modelo_kimi = KIMI_MODEL_FALLBACK.get(agente)
        if modelo_kimi is None:
            motivo = (
                f"motor local inalcanzable para '{agente}' y no hay modelo Kimi configurado "
                f"como fallback para este agente (ver KIMI_MODEL_FALLBACK en orchestrator.py): {e}"
            )
            return {"estado": "motor_inalcanzable", "motivo": motivo}

        if not confirmar:
            motivo = (
                f"motor local inalcanzable para '{agente}' y --sin-confirmar activo "
                f"(no se puede preguntar si activar Kimi): {e}"
            )
            _registrar_decision_motor(project_root, agente, activo=False, motivo=motivo)
            return {"estado": "motor_inalcanzable", "motivo": motivo}

        activar = _preguntar_activar_kimi(agente, modelo_kimi, str(e))
        _registrar_decision_motor(project_root, agente, activo=activar, motivo=str(e))
        if not activar:
            return {"estado": "motor_inalcanzable", "motivo": str(e)}

        set_override(agente, "kimi", modelo_kimi)
        print(f"  -> '{agente}' va a usar Kimi ({modelo_kimi}) por el resto de esta corrida.")
        try:
            return fn()
        except MotorInalcanzable as e2:
            # Kimi también inalcanzable ya en este mismo intento (ej. la
            # máquina está sin red por completo, no solo sin acceso al motor
            # local) -- sin este segundo try/except, esta excepción se
            # propagaría cruda y rompería el contrato de "siempre devuelve un
            # dict", tirando loop()/main() con un traceback en vez del
            # mensaje claro que se espera en cualquier otra rama de esta
            # función.
            return {
                "estado": "motor_inalcanzable",
                "motivo": f"Kimi ({modelo_kimi}) también inalcanzable para '{agente}': {e2}",
            }


def ejecutar_con_invalidacion(
    project_root: str, item_id: str, feedback: str | None = None,
    agente: str = "executor", max_tokens: int = 20000, enable_thinking: bool = True,
    confirmar: bool = True,
) -> dict:
    """
    Wrapper sobre ejecutar_item(): si el item ya estaba 'aprobado' antes de
    esta corrida, invalida el veredicto de sus dependientes después de
    regenerarlo. Punto único por el que debería pasar cualquier llamada a
    Executor en orchestrator.py -- también el único punto que ofrece el
    fallback a Kimi si el motor local de `agente` está inalcanzable (ver
    _con_fallback_motor_local).
    """
    root = Path(project_root).resolve()
    veredicto_previo = _leer_veredictos(root).get(item_id)
    ya_estaba_aprobado = bool(veredicto_previo and veredicto_previo.get("veredicto") == "aprobado")

    _registrar_metrica_agente(root, item_id, agente)
    resultado = _con_fallback_motor_local(
        root, agente, confirmar,
        lambda: ejecutar_item(
            project_root, item_id, feedback=feedback, agente=agente,
            max_tokens=max_tokens, enable_thinking=enable_thinking,
        ),
    )

    if ya_estaba_aprobado and resultado.get("estado") == "finalizado":
        invalidados = invalidar_dependientes(project_root, item_id)
        if invalidados:
            print(
                f"  -> {item_id} ya estaba aprobado antes de este reintento; se invalidó el "
                f"veredicto de {len(invalidados)} item(s) que dependían de él: {', '.join(sorted(invalidados))}"
            )

    return resultado


def calcular_estados(project_root: str) -> dict[str, str]:
    """
    Implementa el algoritmo de 'estado efectivo' de plan.contract.md:
    1. veredicto aprobado -> completado
    2. veredicto rechazado -> rechazado
    3. sin veredicto, último evento 'bloqueado' -> bloqueado
    4. sin veredicto, último evento 'iniciado' sin 'finalizado' -> en_progreso
    5. sin eventos -> lo que diga plan.json (pendiente/omitido)
    """
    root = Path(project_root).resolve()
    plan = _cargar_plan(root)
    eventos = _leer_eventos_executor(root)
    veredictos = _leer_veredictos(root)

    estados = {}
    for item in plan["items"]:
        item_id = item["id"]
        veredicto = veredictos.get(item_id)
        eventos_item = eventos.get(item_id, [])
        desactualizado = _veredicto_desactualizado(eventos_item, veredicto)

        if veredicto and veredicto["veredicto"] == "aprobado" and not desactualizado:
            estados[item_id] = "completado"
            continue
        if veredicto and veredicto["veredicto"] == "rechazado" and not desactualizado:
            estados[item_id] = "rechazado"
            continue

        if eventos_item:
            ultimo = eventos_item[-1]
            if ultimo["evento"] == "bloqueado":
                estados[item_id] = "bloqueado"
                continue
            if ultimo["evento"] == "iniciado":
                estados[item_id] = "en_progreso"
                continue
            # último evento 'finalizado' sin veredicto todavía: sigue pendiente de Compliance
            estados[item_id] = "en_progreso"
            continue

        estados[item_id] = item.get("estado", "pendiente")

    return estados


def seleccionar_siguiente_item(project_root: str) -> str | None:
    """Próximo item listo para Executor: pendiente y con dependencias completadas."""
    root = Path(project_root).resolve()
    plan = _cargar_plan(root)
    estados = calcular_estados(project_root)

    for item in plan["items"]:
        item_id = item["id"]
        if estados[item_id] != "pendiente":
            continue
        dependencias = item.get("depende_de", [])
        if all(estados.get(dep) == "completado" for dep in dependencias):
            return item_id

    return None


def seleccionar_siguiente_item_para_compliance(project_root: str) -> str | None:
    """
    Próximo item listo para Compliance: Executor dejó 'finalizado' como
    último evento, y o no hay veredicto todavía, o el que hay quedó
    desactualizado por un reintento (código nuevo generado después de ese
    veredicto). A diferencia de calcular_estados() (que colapsa 'iniciado' y
    'finalizado' sin veredicto en 'en_progreso'), acá necesitamos distinguir
    los dos para no mandar a validar un item que Executor todavía no terminó.
    """
    root = Path(project_root).resolve()
    plan = _cargar_plan(root)
    eventos = _leer_eventos_executor(root)
    veredictos = _leer_veredictos(root)

    for item in plan["items"]:
        item_id = item["id"]
        eventos_item = eventos.get(item_id, [])
        if not eventos_item or eventos_item[-1]["evento"] != "finalizado":
            continue
        veredicto = veredictos.get(item_id)
        if veredicto is None or _veredicto_desactualizado(eventos_item, veredicto):
            return item_id

    return None


def _contar_intentos(project_root: Path, item_id: str) -> int:
    """Cuántas veces Executor arrancó este item (cuenta eventos 'iniciado')."""
    eventos = _leer_eventos_executor(project_root).get(item_id, [])
    return sum(1 for e in eventos if e["evento"] == "iniciado")


def _construir_feedback_reintento(veredicto: dict) -> str:
    lineas = []
    for c in veredicto.get("criterios_evaluados", []):
        if not c.get("cumplido", True):
            lineas.append(f"- NO CUMPLIDO: {c['criterio']}\n  motivo: {c.get('detalle', '')}")
    if veredicto.get("detalle"):
        lineas.append(f"\nresumen de Compliance: {veredicto['detalle']}")
    return "\n".join(lineas)


# Secciones fijas del ticket de reintento, en orden -- ver
# schemas/plan.contract.md, "Ticket de reintento".
TICKET_SECCIONES = (
    "Lo esperado",
    "Hechos verificados",
    "Historial de intentos",
    "Código actual (después del último intento)",
)


def _ruta_ticket(root: Path, item_id: str) -> Path:
    return root / ".harness" / "logs" / "tickets" / f"{item_id}.md"


def _leer_ticket(root: Path, item_id: str) -> str:
    ruta = _ruta_ticket(root, item_id)
    return ruta.read_text(encoding="utf-8") if ruta.exists() else ""


def _parsear_ticket(texto: str) -> dict[str, str]:
    """Divide un ticket ya escrito en sus secciones fijas (encabezados '## ')."""
    secciones: dict[str, str] = {}
    actual = None
    buffer: list[str] = []
    for linea in texto.splitlines():
        if linea.startswith("## "):
            if actual is not None:
                secciones[actual] = "\n".join(buffer).strip("\n")
            actual = linea[3:].strip()
            buffer = []
        elif actual is not None:
            buffer.append(linea)
    if actual is not None:
        secciones[actual] = "\n".join(buffer).strip("\n")
    return secciones


def _renderizar_ticket(item_id: str, secciones: dict[str, str]) -> str:
    partes = [f"# Ticket de reintento — {item_id}"]
    for nombre in TICKET_SECCIONES:
        partes.append(f"\n## {nombre}\n\n{secciones.get(nombre, '').strip()}\n")
    return "\n".join(partes).rstrip("\n") + "\n"


def _actualizar_ticket_reintento(root: Path, item: dict, veredicto: dict, fuente: str) -> str:
    """
    Crea o actualiza el ticket de reintento de un item tras un rechazo
    elegible para reintento (Compliance, o el rechazo sintético de
    format_check/frontend_check/smoke_test/docker_check — ver
    _veredicto_sintetico_rechazado y validar_con_format_check). Reemplaza
    _construir_feedback_reintento + código actual recalculados en memoria
    en cada llamada — ver schemas/plan.contract.md, "Ticket de reintento",
    para el diseño completo. Devuelve el texto renderizado (mismo que
    queda en disco), listo para usarse como `feedback` de Executor.

    "Hechos verificados" NUNCA se toca acá si ya existía algo escrito —
    es población manual (Analyzer+Planner), la única sección que este
    orquestador no recalcula.
    """
    item_id = item["id"]
    ruta = _ruta_ticket(root, item_id)
    ruta.parent.mkdir(parents=True, exist_ok=True)

    secciones = (
        _parsear_ticket(ruta.read_text(encoding="utf-8")) if ruta.exists()
        else {nombre: "" for nombre in TICKET_SECCIONES}
    )

    secciones["Lo esperado"] = "\n".join(f"- {c}" for c in item.get("criterios_aceptacion", []))

    motivo = _construir_feedback_reintento(veredicto)
    n_intento = secciones["Historial de intentos"].count("### Intento ") + 1
    timestamp = datetime.now().astimezone().isoformat()
    bloque_nuevo = f"### Intento {n_intento} — {timestamp} (fuente: {fuente})\n{motivo}"
    historial_previo = secciones["Historial de intentos"]
    secciones["Historial de intentos"] = f"{historial_previo}\n\n{bloque_nuevo}" if historial_previo else bloque_nuevo

    bloques_codigo = []
    for ruta_archivo in item.get("archivos_destino", []):
        archivo = root / ruta_archivo
        if archivo.exists():
            bloques_codigo.append(f"### {ruta_archivo}\n```\n{archivo.read_text()}\n```")
    secciones["Código actual (después del último intento)"] = "\n\n".join(bloques_codigo)

    texto = _renderizar_ticket(item_id, secciones)
    ruta.write_text(texto, encoding="utf-8")
    return texto


def seleccionar_siguiente_para_loop(
    project_root: str, max_reintentos: int, excluir: set[str] | None = None
) -> tuple[str, str | None] | None:
    """
    Para el modo --loop: además de items 'pendiente' con dependencias
    completadas, también reconsidera items 'rechazado' que no agotaron sus
    reintentos, pasando el ticket de reintento (ver
    schemas/plan.contract.md, "Ticket de reintento") como feedback -- ya
    actualizado con el motivo del rechazo al momento de validar (ver
    validar_con_format_check), no se recalcula acá. Los 'bloqueado'
    (Executor mismo dijo que le faltaba info) NO se reintentan acá — sin
    información nueva no hay razón para esperar un resultado distinto, eso
    se resuelve a mano.

    `excluir`: items 'rechazado' que el usuario decidió arreglar a mano (o
    cuyo ticket quedó pendiente de completar) en esta misma corrida del
    loop (ver gate de decisión en loop()) — se saltan hasta que alguien los
    revalide explícitamente con --item <id> --rol compliance.
    """
    root = Path(project_root).resolve()
    plan = _cargar_plan(root)
    estados = calcular_estados(project_root)
    total_intentos_permitidos = 1 + max_reintentos
    excluir = excluir or set()

    for item in plan["items"]:
        item_id = item["id"]
        estado = estados[item_id]
        dependencias = item.get("depende_de", [])
        deps_ok = all(estados.get(dep) == "completado" for dep in dependencias)

        if estado == "pendiente" and deps_ok:
            return item_id, None

        if estado == "rechazado" and deps_ok and item_id not in excluir:
            if _contar_intentos(root, item_id) < total_intentos_permitidos:
                return item_id, _leer_ticket(root, item_id)

    return None


def _escribir_reporte_falla(project_root: Path, item_id: str, motivo: str, intentos: int):
    """
    Reporte legible para reparación manual. Lo escribe el orquestador
    directamente (no un agente, no pasa por AgentFileGuard) porque no es
    ni código del proyecto ni metadata de ejecución de un agente puntual —
    es un resumen operativo del harness mismo.
    """
    ruta = project_root / ".harness" / "logs" / "reporte_fallas.md"
    ruta.parent.mkdir(parents=True, exist_ok=True)
    encabezado = "# Reporte de fallas — requieren reparación manual\n\n" if not ruta.exists() else ""
    entrada = (
        f"## {item_id} — {datetime.now().astimezone().isoformat()}\n\n"
        f"Intentos agotados ({intentos}). Último motivo:\n\n"
        f"```\n{motivo}\n```\n\n"
        f"Veredicto completo: `.harness/validation/{item_id}.json` "
        f"(si llegó a pasar por Compliance) o `.harness/logs/executor.jsonl` "
        f"(si se quedó bloqueado en Executor).\n\n---\n\n"
    )
    with ruta.open("a", encoding="utf-8") as f:
        f.write(encabezado + entrada)
    print(f"  -> reporte de falla escrito en {ruta}")


def _escribir_reporte_rechazo(project_root: Path, item_id: str, ruta_ticket: Path, intentos: int, total_intentos_permitidos: int):
    """
    Igual que _escribir_reporte_falla, pero se escribe en CADA rechazo
    elegible para reintento (no solo cuando se agotan los intentos) — para
    que el gate de decisión en loop() tenga algo escrito en disco que
    revisar con calma, no solo lo que se imprime en la terminal. Referencia
    el ticket de reintento (ver schemas/plan.contract.md, "Ticket de
    reintento") en vez de embeber el feedback completo — evita duplicar el
    mismo contenido (código actual incluido) en dos archivos.
    """
    ruta = project_root / ".harness" / "logs" / "reporte_fallas.md"
    ruta.parent.mkdir(parents=True, exist_ok=True)
    encabezado = "# Reporte de fallas — requieren reparación manual\n\n" if not ruta.exists() else ""
    entrada = (
        f"## {item_id} — rechazo {intentos}/{total_intentos_permitidos} — "
        f"{datetime.now().astimezone().isoformat()}\n\n"
        f"Pendiente de decisión antes de reintentar. Detalle completo en "
        f"`{ruta_ticket.relative_to(project_root)}`.\n\n---\n\n"
    )
    with ruta.open("a", encoding="utf-8") as f:
        f.write(encabezado + entrada)
    print(f"  -> reporte de rechazo escrito en {ruta}")


def _item_por_id(plan: dict, item_id: str) -> dict:
    for item in plan["items"]:
        if item["id"] == item_id:
            return item
    raise ValueError(f"item_id '{item_id}' no existe en plan.json")


def _registrar_edicion_plan(root: Path, item_id: str, items_agregados: list[str], explicacion: str):
    """
    Deja rastro de cada edición automática de plan.json -- mismo criterio
    que decisiones_reintento.jsonl, para que una arista agregada al grafo de
    dependencias sea tan auditable como cualquier otra decisión del harness.
    """
    ruta = root / ".harness" / "logs" / "ediciones_plan.jsonl"
    ruta.parent.mkdir(parents=True, exist_ok=True)
    entrada = {
        "item_id": item_id,
        "campo": "depende_de",
        "items_agregados": items_agregados,
        "explicacion": explicacion,
        "timestamp": datetime.now().astimezone().isoformat(),
    }
    with ruta.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entrada, ensure_ascii=False) + "\n")


def _agregar_dependencia_a_plan(root: Path, item_id: str, items_faltantes: list[str], explicacion: str) -> tuple[bool, str]:
    """
    Agrega items_faltantes a depende_de de item_id en plan.json, si son
    válidos (existen en el plan, no generan un ciclo) -- reusa
    plan_validator.validar_plan() sobre una copia modificada en memoria en
    vez de duplicar la lógica de detección de ciclos. Devuelve
    (aplicado, motivo); motivo explica por qué no se aplicó cuando
    aplicado es False.

    Es la ÚNICA escritura sobre plan.json que hace orchestrator.py -- todo
    lo demás ahí es de solo lectura (plan.json lo escribe el Planner).
    Deliberadamente angosta: agrega una arista al grafo de dependencias,
    nunca toca detalle_tecnico, interfaz, criterios ni ningún otro campo --
    no es una re-planificación, arbitro (agents/arbitro.py) no decide
    contenido, solo señala qué item falta declarar como dependencia.
    """
    ruta = root / ".harness" / "config" / "plan.json"
    plan = json.loads(ruta.read_text())
    ids_validos = {item["id"] for item in plan["items"]}

    invalidos = [i for i in items_faltantes if i not in ids_validos]
    if invalidos:
        return False, f"arbitro nombró item(s) que no existen en el plan: {invalidos}"

    item = _item_por_id(plan, item_id)
    nuevos = [i for i in items_faltantes if i not in item.get("depende_de", []) and i != item_id]
    if not nuevos:
        return False, "los items nombrados ya estaban en depende_de (o eran el propio item) -- no hay nada que agregar"

    item.setdefault("depende_de", []).extend(nuevos)

    errores = validar_plan(plan)
    if errores:
        item["depende_de"] = [d for d in item["depende_de"] if d not in nuevos]
        return False, f"agregar {nuevos} generaría un plan inválido: {'; '.join(errores)}"

    ruta.write_text(json.dumps(plan, ensure_ascii=False, indent=2))
    _registrar_edicion_plan(root, item_id, nuevos, explicacion)
    return True, f"se agregó {nuevos} a depende_de de {item_id}"


def _veredicto_sintetico_rechazado(root: Path, item: dict, criterio: str, detalles: list[str], resumen: str, fuente: str) -> dict:
    """
    Arma y persiste un veredicto 'rechazado' con el mismo formato que el de
    Compliance, sin haber gastado ninguna llamada al modelo — usado tanto
    por format check como por el smoke test (pytest). El resto del pipeline
    (cálculo de estado, reintentos con feedback, reporte de fallas) no
    necesita saber que esta vez no hubo ningún modelo de por medio. También
    actualiza el ticket de reintento (ver _actualizar_ticket_reintento) con
    `fuente`, para que el historial de intentos diga de dónde vino cada
    rechazo, no solo Compliance.
    """
    item_id = item["id"]
    veredicto = {
        "item_id": item_id,
        "veredicto": "rechazado",
        "timestamp": datetime.now().astimezone().isoformat(),
        "criterios_evaluados": [
            {"criterio": criterio, "cumplido": False, "detalle": d} for d in detalles
        ],
        "detalle": resumen,
    }
    ruta = root / ".harness" / "validation" / f"{item_id}.json"
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_text(json.dumps(veredicto, ensure_ascii=False, indent=2))
    _actualizar_ticket_reintento(root, item, veredicto, fuente=fuente)
    return {"estado": "rechazado", "veredicto": veredicto}


def validar_con_format_check(project_root: str, item_id: str, confirmar: bool = True) -> dict:
    """
    Punto único de validación: antes de gastar una llamada a Compliance,
    corre filtros determinísticos y gratis (sin LLM):
    1. format check (ver format_check.py) — imports rotos, nombres que se
       pisan (solo aplica a archivos .py).
    2. según `tipo` del item:
       - backend: smoke test (ver smoke_test.py) — pytest de verdad, solo si
         el item declara `tests_requeridos` en plan.json.
       - frontend: frontend check (ver frontend_check.py) — compila el
         proyecto Angular real (`ng build`), siempre (no es opcional como el
         smoke test de backend).
    Si cualquiera encuentra algo, arma un veredicto 'rechazado' sintético
    (ver _veredicto_sintetico_rechazado) y ni siquiera llama a Compliance.
    """
    root = Path(project_root).resolve()
    plan = _cargar_plan(root)
    item = _item_por_id(plan, item_id)

    errores = format_check.verificar(project_root, item["archivos_destino"])
    if errores:
        return _veredicto_sintetico_rechazado(
            root, item, fuente="format_check",
            criterio="El código generado no tiene imports internos rotos ni nombres que se pisan (format check determinístico, sin LLM)",
            detalles=errores,
            resumen=(
                f"Rechazado por format check determinístico antes de llegar a Compliance "
                f"({len(errores)} problema(s)) — no se gastó ninguna llamada al modelo."
            ),
        )

    if item.get("tipo") == "frontend":
        resultado_fe = frontend_check.verificar(project_root)
        if resultado_fe["estado"] == "error":
            return _veredicto_sintetico_rechazado(
                root, item, fuente="frontend_check",
                criterio="El proyecto Angular compila sin errores (ng build determinístico, sin LLM)",
                detalles=[resultado_fe["detalle"]],
                resumen=(
                    "Rechazado por error de compilación de Angular antes de llegar a "
                    "Compliance — no se gastó ninguna llamada al modelo."
                ),
            )
    elif item.get("tipo") == "infra":
        resultado_docker = docker_check.verificar(project_root, item)
        if resultado_docker.get("estado") == "motor_inalcanzable":
            return resultado_docker
        if resultado_docker.get("estado") == "error":
            return _veredicto_sintetico_rechazado(
                root, item, fuente="docker_check",
                criterio="La(s) imagen(es) Docker construyen y (si aplica) el stack levanta y responde /health (chequeo determinístico, sin LLM)",
                detalles=[resultado_docker["detalle"]],
                resumen=(
                    "Rechazado por error de build/runtime Docker antes de llegar a "
                    "Compliance — no se gastó ninguna llamada al modelo."
                ),
            )
    else:
        resultado_smoke = smoke_test.correr(project_root, item)
        if resultado_smoke["estado"] in ("fallo", "error"):
            return _veredicto_sintetico_rechazado(
                root, item, fuente="smoke_test",
                criterio="El código generado pasa los tests declarados en tests_requeridos (smoke test, pytest real, sin LLM)",
                detalles=[resultado_smoke["detalle"]],
                resumen=(
                    "Rechazado por el smoke test (pytest) antes de llegar a Compliance — "
                    "no se gastó ninguna llamada al modelo."
                ),
            )

    _registrar_metrica_agente(root, item_id, "compliance")
    resultado = validar_item(project_root, item_id)
    if resultado.get("estado") == "aprobado":
        if item.get("tipo") == "backend":
            regenerar_catalogo_endpoints(project_root)
        if _item_tuvo_rechazos(root, item_id):
            _documentar_si_corresponde(project_root, item_id, confirmar=confirmar)
    elif resultado.get("estado") == "rechazado":
        _actualizar_ticket_reintento(root, item, resultado["veredicto"], fuente="compliance")
    return resultado


def _documentar_si_corresponde(project_root: str, item_id: str, confirmar: bool = True):
    """
    Item aprobado que tuvo rechazo(s) reales antes -- candidato a
    documentación (ver agents/documentador.py). Puramente aditivo: un fallo
    acá se loguea y se ignora, nunca cambia el veredicto ni bloquea el loop
    -- a diferencia de Executor, si el motor local está inalcanzable y el
    usuario no activa Kimi (o no hay a quién preguntarle), simplemente se
    salta este item en vez de detener el loop entero.
    """
    root = Path(project_root).resolve()
    _registrar_metrica_agente(root, item_id, "documentador")
    try:
        resultado = _con_fallback_motor_local(
            root, "documentador", confirmar,
            lambda: documentar_resolucion(project_root, item_id),
        )
    except Exception as e:  # motor caído por otra razón, respuesta rara, lo que sea -- nunca tira el pipeline
        print(f"  -> documentador falló para {item_id} (ignorado): {e}")
        return
    if resultado.get("estado") == "documentado":
        print(f"  -> documentador: candidato de conocimiento propuesto ({resultado['clasificacion']}) en .harness/logs/candidatos_conocimiento.md")
    elif resultado.get("estado") in ("error", "motor_inalcanzable"):
        print(f"  -> documentador falló para {item_id} (ignorado): {resultado.get('motivo')}")


def _preguntar_seguir() -> bool:
    respuesta = input("¿Seguir con el siguiente paso? [S/n]: ").strip().lower()
    return respuesta in ("", "s", "si", "sí", "y", "yes")


def _preguntar_confirmar_dependencia(item_id: str, items_faltantes: list[str], explicacion: str) -> bool:
    """Gate antes de aplicar una edición automática a plan.json (agregar
    depende_de) -- ver _agregar_dependencia_a_plan(). Con --sin-confirmar no
    se llama, se aplica directo."""
    print(f"\n--- arbitro dice que a {item_id} le falta depende_de: {items_faltantes} ---")
    print(explicacion)
    respuesta = input(f"¿Agregar {items_faltantes} a depende_de de {item_id} en plan.json? [S/n]: ").strip().lower()
    return respuesta in ("", "s", "si", "sí", "y", "yes")


def _preguntar_decision_reintento(item_id: str, ticket_texto: str, ruta_ticket: Path) -> str:
    """
    Gate antes de que --loop reintente un item rechazado. El ticket (ver
    schemas/plan.contract.md, "Ticket de reintento") ya existe en disco con
    todo lo determinístico -- este gate decide qué hacer con él, no si
    generarlo. Devuelve la decisión: "reintentar" | "editar" | "manual" |
    "detener".
    """
    print(f"\n--- {item_id} rechazado, pendiente de decisión antes de reintentar ---")
    print(ticket_texto)
    print(f"\n(ticket completo en {ruta_ticket})")
    print(
        "\n[r] reintentar con el ticket tal cual  "
        "[e] completar 'Hechos verificados' antes de reintentar  "
        "[m] lo arreglo yo mismo (excluir del loop)  [n] detener el loop"
    )
    while True:
        respuesta = input("Elegí una opción [r/e/m/n]: ").strip().lower()
        if respuesta in ("", "r", "s", "si", "sí"):
            return "reintentar"
        if respuesta == "e":
            return "editar"
        if respuesta == "m":
            return "manual"
        if respuesta in ("n", "no"):
            return "detener"
        print("Opción no reconocida, probá de nuevo.")


def _registrar_decision_reintento(project_root: Path, item_id: str, decision: str):
    """
    Deja rastro de cada decisión tomada en el gate — cierra el gap de "no
    hay registro" de cuándo el humano tuvo que intervenir en el ciclo
    Executor-Compliance, no solo cuando se agotan los reintentos.
    """
    ruta = project_root / ".harness" / "logs" / "decisiones_reintento.jsonl"
    ruta.parent.mkdir(parents=True, exist_ok=True)
    entrada = {
        "item_id": item_id,
        "decision": decision,
        "timestamp": datetime.now().astimezone().isoformat(),
    }
    with ruta.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entrada, ensure_ascii=False) + "\n")


def _resumen_paso(rol: str, item_id: str, resultado: dict) -> str:
    estado = resultado.get("estado", "?")
    if rol == "executor" and estado == "finalizado":
        return f"[{rol}] {item_id}: generó {len(resultado.get('archivos', []))} archivo(s)."
    if rol == "compliance":
        veredicto = resultado.get("veredicto", {})
        criterios = veredicto.get("criterios_evaluados", [])
        cumplidos = sum(1 for c in criterios if c.get("cumplido"))
        return f"[{rol}] {item_id}: {estado} ({cumplidos}/{len(criterios)} criterios cumplidos)."
    return f"[{rol}] {item_id}: {estado} — {resultado.get('motivo', '')}"


def _executor_senior_disponible() -> bool:
    """True si config/models.yaml define un agente 'executor_senior' (ver loop())."""
    config_path = Path(__file__).parent / "config" / "models.yaml"
    config = yaml.safe_load(config_path.read_text())
    return "executor_senior" in config.get("agents", {})


def _arbitro_disponible() -> bool:
    """True si config/models.yaml define un agente 'arbitro' (ver _ejecutar_con_arbitraje())."""
    config_path = Path(__file__).parent / "config" / "models.yaml"
    config = yaml.safe_load(config_path.read_text())
    return "arbitro" in config.get("agents", {})


def _ejecutar_con_arbitraje(
    project_root: str, item_id: str, feedback: str | None,
    max_arbitrajes: int, arbitrajes_intentados: dict[str, int], confirmar: bool = True,
) -> dict:
    """
    Corre Executor para item_id (con el esquema de thinking por intento) y,
    si queda 'bloqueado' por una ambigüedad/dependencia faltante real del
    plan (no información que Executor tuvo que inventar), consulta a
    arbitro() -- hasta max_arbitrajes veces por item. Según lo que arbitro
    devuelva (ver agents/arbitro.py::resolver_bloqueo):
    - "decision": se le pasa como feedback y se reintenta.
    - "falta_dependencia": se propone agregar el/los item_id faltantes al
      depende_de de item_id (gate de confirmación salvo --sin-confirmar,
      ver _preguntar_confirmar_dependencia) -- si se aplica, se reintenta
      SIN feedback especial (construir_contexto ya va a traer la interfaz
      nueva sola). Si no se aplica (rechazado en el gate, ids inválidos, o
      generaría un ciclo), se corta como si arbitro no hubiera resuelto nada.
    - "no_resoluble": se corta.

    Si arbitro no está configurado, o se agotan los intentos, devuelve el
    resultado 'bloqueado' tal cual para que el caller lo trate como siempre
    (reporte de falla, sin reintento automático).

    `arbitrajes_intentados` es compartido entre llamadas de la misma
    corrida de loop() -- no reinicia el conteo si el item se vuelve a
    seleccionar en una vuelta posterior del while principal.
    """
    root = Path(project_root).resolve()
    enable_thinking = _contar_intentos(root, item_id) > 0
    max_tokens = MAX_TOKENS_EXECUTOR_THINKING if enable_thinking else 20000
    print(
        f"\nExecutor -> {item_id}{' (reintento)' if feedback else ''} "
        f"(thinking: {'on' if enable_thinking else 'off'})..."
    )
    resultado = ejecutar_con_invalidacion(
        project_root, item_id, feedback=feedback,
        enable_thinking=enable_thinking, max_tokens=max_tokens, confirmar=confirmar,
    )
    print(_resumen_paso("executor", item_id, resultado))

    while (
        resultado.get("estado") == "bloqueado" and _arbitro_disponible()
        and arbitrajes_intentados.get(item_id, 0) < max_arbitrajes
    ):
        arbitrajes_intentados[item_id] = arbitrajes_intentados.get(item_id, 0) + 1
        n = arbitrajes_intentados[item_id]
        print(f"  -> {item_id} bloqueado, consultando arbitro ({n}/{max_arbitrajes})...")
        _registrar_metrica_agente(root, item_id, "arbitro")
        resolucion = resolver_bloqueo(project_root, item_id, resultado.get("motivo", ""))

        if resolucion["tipo"] == "no_resoluble":
            print(f"  -> arbitro tampoco pudo resolverlo: {resolucion['explicacion']}")
            break

        if resolucion["tipo"] == "interfaz_incompleta":
            item_productor = resolucion["item_productor"]
            simbolo_faltante = resolucion["simbolo_faltante"]
            print(
                f"  -> arbitro: interfaz incompleta -- '{item_productor}' ya está declarado "
                f"como dependencia pero su interfaz no expone '{simbolo_faltante}' "
                f"({resolucion['explicacion']})"
            )
            # A diferencia de falta_dependencia, esto no es una arista que se
            # pueda agregar sola -- arbitro no escribe contenido de items. Se
            # enriquece el motivo con el diagnóstico exacto para que quede en
            # el reporte de fallas, y se corta: sigue necesitando que el
            # Planner actualice la interfaz de item_productor en plan.json.
            resultado = {
                **resultado,
                "motivo": (
                    f"{resultado.get('motivo', '')}\n\nDiagnóstico de arbitro: '{item_productor}' "
                    f"ya está correctamente declarado como dependencia, pero su interfaz no expone "
                    f"'{simbolo_faltante}'. Agregar el import literal exacto a la interfaz de "
                    f"'{item_productor}' en plan.json (arbitro no puede escribirlo, requiere al "
                    f"Planner) y volver a intentar este item."
                ),
            }
            break

        if resolucion["tipo"] == "falta_dependencia":
            items_faltantes = resolucion["items_faltantes"]
            explicacion = resolucion["explicacion"]
            proceder = (
                _preguntar_confirmar_dependencia(item_id, items_faltantes, explicacion)
                if confirmar else True
            )
            if not proceder:
                print("  -> edición de plan.json rechazada, queda para reparación manual")
                break
            aplicado, motivo = _agregar_dependencia_a_plan(root, item_id, items_faltantes, explicacion)
            print(f"  -> {motivo}")
            if not aplicado:
                break
            print(f"\nExecutor -> {item_id} (tras agregar dependencia) (thinking: off)...")
            resultado = ejecutar_con_invalidacion(
                project_root, item_id, feedback=feedback, enable_thinking=False, confirmar=confirmar,
            )
            print(_resumen_paso("executor", item_id, resultado))
            continue

        # tipo == "decision"
        feedback_arbitraje = (
            "Executor se bloqueó antes por falta de información / ambigüedad real del "
            "plan. Un agente de arbitraje ya resolvió el punto puntual que faltaba -- "
            "seguí esta decisión al pie de la letra, no la cuestiones ni la "
            f"reinterpretes:\n\n{resolucion['texto']}"
        )
        print(f"\nExecutor -> {item_id} (tras arbitraje) (thinking: off)...")
        resultado = ejecutar_con_invalidacion(
            project_root, item_id, feedback=feedback_arbitraje, enable_thinking=False, confirmar=confirmar,
        )
        print(_resumen_paso("executor", item_id, resultado))

    return resultado


def _loop_interno(project_root: str, max_reintentos: int = 1, max_arbitrajes: int = 2, confirmar: bool = True):
    root = Path(project_root).resolve()
    total_intentos_permitidos = 1 + max_reintentos
    excluidos: set[str] = set()  # items 'rechazado' que el usuario decidió arreglar a mano (o cuyo ticket falta completar) en esta corrida
    senior_intentado: set[str] = set()  # items que ya pasaron por executor_senior en esta corrida
    arbitrajes_intentados: dict[str, int] = {}  # item_id -> cuántas veces ya se consultó a arbitro en esta corrida

    while True:
        # 1) prioridad: validar lo que Executor ya haya dejado terminado
        item_compliance = seleccionar_siguiente_item_para_compliance(project_root)
        if item_compliance:
            print(f"\nCompliance -> {item_compliance}...")
            resultado = validar_con_format_check(project_root, item_compliance, confirmar=confirmar)

            if resultado.get("estado") == "motor_inalcanzable":
                print(f"Loop detenido: {resultado.get('motivo')}")
                return

            print(_resumen_paso("compliance", item_compliance, resultado))

            if resultado.get("estado") == "rechazado":
                # validar_con_format_check ya actualizó el ticket de reintento
                # (.harness/logs/tickets/<item_id>.md, ver
                # _actualizar_ticket_reintento) con el motivo de este rechazo.
                veredicto = resultado.get("veredicto", {})
                intentos = _contar_intentos(root, item_compliance)

                if intentos >= total_intentos_permitidos:
                    motivo = veredicto.get("detalle", "ver criterios_evaluados")
                    if item_compliance not in senior_intentado and _executor_senior_disponible():
                        senior_intentado.add(item_compliance)
                        ticket_texto = _leer_ticket(root, item_compliance)
                        feedback_senior = (
                            f"Este item agotó {intentos} intentos con el executor normal, rechazado por "
                            f"motivos DISTINTOS en cada uno -- ver 'Historial de intentos' en el ticket "
                            f"completo abajo (revisá también 'Hechos verificados' si está completa). Sos "
                            f"el resolutor final de este item: no repitas ningún error ya visto ni "
                            f"reintroduzcas uno ya corregido.\n\n{ticket_texto}"
                        )
                        print(f"  -> {intentos} intentos agotados, escalando a executor_senior (motor más fuerte, intento final)...")
                        resultado_senior = ejecutar_con_invalidacion(
                            project_root, item_compliance, feedback=feedback_senior,
                            agente="executor_senior", max_tokens=32000, confirmar=confirmar,
                        )
                        print(_resumen_paso("executor", item_compliance, resultado_senior))
                        if resultado_senior.get("estado") == "motor_inalcanzable":
                            print(f"Loop detenido: {resultado_senior.get('motivo')}")
                            return
                        if resultado_senior.get("estado") == "bloqueado":
                            _escribir_reporte_falla(root, item_compliance, resultado_senior.get("motivo", ""), intentos + 1)
                    else:
                        _escribir_reporte_falla(root, item_compliance, motivo, intentos)
                else:
                    print(f"  -> se reintentará ({intentos}/{total_intentos_permitidos} intentos usados)")

            if confirmar and not _preguntar_seguir():
                print("Loop detenido a pedido del usuario.")
                return
            continue

        # 2) si no hay nada para validar, generar el próximo item ejecutable
        seleccion = seleccionar_siguiente_para_loop(project_root, max_reintentos, excluir=excluidos)
        if seleccion:
            item_id, feedback = seleccion

            if feedback is not None:  # es un reintento tras rechazo: pasa por el gate de decisión
                intentos = _contar_intentos(root, item_id)
                ruta_ticket = _ruta_ticket(root, item_id)
                _escribir_reporte_rechazo(root, item_id, ruta_ticket, intentos, total_intentos_permitidos)

                decision = (
                    _preguntar_decision_reintento(item_id, feedback, ruta_ticket) if confirmar else "reintentar"
                )
                _registrar_decision_reintento(root, item_id, decision)

                if decision == "detener":
                    print("Loop detenido a pedido del usuario.")
                    return
                if decision == "manual":
                    excluidos.add(item_id)
                    print(
                        f"  -> {item_id} excluido del reintento automático. Arreglalo a mano y corré "
                        f"'--item {item_id} --rol compliance' para revalidar (Compliance sigue siendo "
                        f"el gate incluso para fixes manuales)."
                    )
                    continue
                if decision == "editar":
                    excluidos.add(item_id)
                    print(
                        f"  -> {item_id} excluido de esta corrida. Completá 'Hechos verificados' en "
                        f"{ruta_ticket} y volvé a correr --loop -- el ticket ya tiene todo lo demás."
                    )
                    continue
                # decision == "reintentar": feedback (el ticket) tal cual, sigue como antes

            resultado = _ejecutar_con_arbitraje(
                project_root, item_id, feedback, max_arbitrajes, arbitrajes_intentados, confirmar=confirmar,
            )

            if resultado.get("estado") == "motor_inalcanzable":
                print(f"Loop detenido: {resultado.get('motivo')}")
                return

            if resultado.get("estado") == "bloqueado":
                intentos = _contar_intentos(root, item_id)
                _escribir_reporte_falla(root, item_id, resultado.get("motivo", ""), intentos)

            if confirmar and not _preguntar_seguir():
                print("Loop detenido a pedido del usuario.")
                return
            continue

        break

    print("\nNo queda nada ejecutable automáticamente.")
    _imprimir_status(project_root)


def loop(project_root: str, max_reintentos: int = 1, max_arbitrajes: int = 2, confirmar: bool = True):
    """
    Wrapper delgado sobre _loop_interno(): sea cual sea el motivo de salida
    (nada más ejecutable, detenido a pedido del usuario, motor
    inalcanzable), siempre termina mostrando cuántas veces pasó cada item
    por cada agente durante ESTA sesión puntual -- "sesión" en el sentido
    de Felipe: desde que arranca esta corrida de --loop hasta que termina,
    no el acumulado histórico del proyecto (para eso está `--metricas`,
    sin acotar). Se logra recordando cuántas líneas tenía
    metricas_agentes.jsonl ANTES de correr, y mostrando solo lo agregado
    después -- no hace falta pasar un acumulador por los 4 puntos de
    registro.
    """
    root = Path(project_root).resolve()
    ruta_metricas = root / ".harness" / "logs" / "metricas_agentes.jsonl"
    lineas_antes = len(ruta_metricas.read_text().splitlines()) if ruta_metricas.exists() else 0
    try:
        _loop_interno(project_root, max_reintentos=max_reintentos, max_arbitrajes=max_arbitrajes, confirmar=confirmar)
    finally:
        _imprimir_metricas_agentes(project_root, desde_linea=lineas_antes)


def _imprimir_status(project_root: str):
    estados = calcular_estados(project_root)
    for item_id, estado in estados.items():
        print(f"  {item_id:12s} {estado}")


def main():
    parser = argparse.ArgumentParser(description="Orquestador del harness")
    parser.add_argument("project_root")
    parser.add_argument("--rol", choices=["executor", "compliance"], default="executor")
    parser.add_argument("--item", help="Forzar la ejecución/validación de un item puntual")
    parser.add_argument("--status", action="store_true", help="Solo mostrar estados, no ejecutar")
    parser.add_argument(
        "--metricas", action="store_true",
        help="Mostrar cuántas veces pasó cada item por cada agente (executor/executor_senior/"
             "compliance/arbitro/documentador) y salir, sin ejecutar nada.",
    )
    parser.add_argument("--loop", action="store_true", help="Encadenar Executor/Compliance automáticamente")
    parser.add_argument("--max-reintentos", type=int, default=1, help="Reintentos tras un rechazo, dentro de --loop")
    parser.add_argument(
        "--max-arbitrajes", type=int, default=2,
        help="Veces que se consulta a 'arbitro' para resolver un bloqueo de Executor antes de darse por vencido",
    )
    parser.add_argument("--sin-confirmar", action="store_true", help="No preguntar entre pasos dentro de --loop")
    parser.add_argument(
        "--senior", action="store_true",
        help="Con --item --rol executor: forzar executor_senior (motor más fuerte, "
             "resolutor final) para ESE item puntual, en vez de esperar a que --loop "
             "agote los reintentos normales primero. Requiere --item.",
    )
    args = parser.parse_args()

    if args.senior and (not args.item or args.rol != "executor"):
        print("--senior requiere --item <id> y --rol executor (o el default, que ya es executor).")
        return
    if args.senior and not _executor_senior_disponible():
        print("--senior pedido, pero config/models.yaml no define un agente 'executor_senior'.")
        return

    if args.status:
        _imprimir_status(args.project_root)
        return

    if args.metricas:
        _imprimir_metricas_agentes(args.project_root)
        return

    if args.loop:
        loop(
            args.project_root, max_reintentos=args.max_reintentos,
            max_arbitrajes=args.max_arbitrajes, confirmar=not args.sin_confirmar,
        )
        return

    if args.rol == "executor":
        item_id = args.item or seleccionar_siguiente_item(args.project_root)
        if item_id is None:
            print("No hay ningún item listo para Executor (pendiente + dependencias completadas).")
            return

        # Si se fuerza un item puntual con --item y ya tiene un veredicto
        # 'rechazado' (típicamente porque agotó los reintentos automáticos
        # de --loop), le pasamos su ticket de reintento como feedback igual
        # que haría --loop — forzar a mano no debería perder esa información.
        # El ticket ya lo actualizó validar_con_format_check al momento del
        # rechazo (sea vía --loop o vía --item --rol compliance manual).
        feedback = None
        if args.item:
            root_forzado = Path(args.project_root).resolve()
            veredictos = _leer_veredictos(root_forzado)
            veredicto_previo = veredictos.get(item_id)
            if veredicto_previo and veredicto_previo.get("veredicto") == "rechazado":
                feedback = _leer_ticket(root_forzado, item_id)
                print("(usando el ticket de reintento -- .harness/logs/tickets/<item>.md -- como feedback para este reintento forzado)")

        if args.senior:
            # Salta arbitro/reintento normal a propósito -- --senior es para
            # cuando ya se sabe que el executor normal no converge (mismo
            # motivo por el que --loop escala acá tras agotar
            # --max-reintentos, ver loop()), no el camino por defecto.
            print(f"Executor_senior -> {item_id} (forzado por --senior)...")
            resultado = ejecutar_con_invalidacion(
                args.project_root, item_id, feedback=feedback,
                agente="executor_senior", max_tokens=32000, confirmar=not args.sin_confirmar,
            )
            print(_resumen_paso("executor", item_id, resultado))
        else:
            # Mismo esquema que --loop, arbitraje incluido (ver _ejecutar_con_arbitraje).
            resultado = _ejecutar_con_arbitraje(
                args.project_root, item_id, feedback, args.max_arbitrajes, {},
                confirmar=not args.sin_confirmar,
            )
    else:
        item_id = args.item or seleccionar_siguiente_item_para_compliance(args.project_root)
        if item_id is None:
            print("No hay ningún item listo para Compliance (generado por Executor, sin veredicto).")
            return
        print(f"Compliance -> {item_id}...")
        resultado = validar_con_format_check(args.project_root, item_id, confirmar=not args.sin_confirmar)

    print(json.dumps(resultado, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
