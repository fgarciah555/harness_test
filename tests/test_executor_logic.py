"""
Prueba manual de la lógica de Executor y orchestrator que NO depende de
LM Studio (parseo de respuesta, armado de contexto mínimo, cálculo de
estado efectivo). No usa el motor de inferencia real.

Uso: python tests/test_executor_logic.py
"""
import json
import os
import shutil
import tempfile
from itertools import count
from pathlib import Path

import sys
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from checks import format_check
from checks import smoke_test
from access_control import AgentFileGuard, Zona
from checks.plan_validator import validar_plan
from checks.api_endpoints import regenerar_catalogo_endpoints
from interfaz_real import combinar_interfaz, podar_predicha_no_generada
from engines.base import MotorInalcanzable, TimeoutDelMotor
from engines.lm_studio import LMStudioEngine
from engines.factory import set_override, get_override, clear_override, get_engine_for_agent
from engines.kimi_api import KimiEngine
from engines.deepseek_api import DeepSeekEngine
import engines.factory as engine_factory_mod
from agents.executor import construir_contexto, parsear_respuesta
from agents.compliance import parsear_respuesta as parsear_respuesta_compliance
from agents.compliance import _calcular_veredicto
from agents.compliance import construir_contexto as construir_contexto_compliance
from agents.arbitro import _parsear_falta_dependencia, _parsear_interfaz_incompleta
from agents.documentador import (
    bloques_de_rechazo,
    construir_contexto as construir_contexto_documentador,
    parsear_respuesta as parsear_respuesta_documentador,
    _marcar_candidatos_previos_superados,
    _formatear_bloque_salida,
    MARCA_SUPERSEDIDO,
)
from orchestrator import (
    calcular_estados,
    seleccionar_siguiente_item,
    seleccionar_siguiente_item_para_compliance,
    seleccionar_siguiente_para_loop,
    validar_con_format_check,
    _dependientes_transitivos,
    invalidar_dependientes,
    _contar_intentos,
    _construir_feedback_reintento,
    _actualizar_ticket_reintento,
    _leer_ticket,
    _ruta_ticket,
    _escribir_reporte_rechazo,
    _registrar_decision_reintento,
    _item_tuvo_rechazos,
    _con_fallback_motor_local,
    _documentar_si_corresponde,
    _registrar_metrica_agente,
    calcular_metricas_agentes,
    _tabla_metricas_agentes,
    KIMI_MODEL_FALLBACK,
)
import orchestrator as orchestrator_mod

REPO_ROOT = Path(__file__).resolve().parent.parent
PLAN_EJEMPLO = REPO_ROOT / "schemas" / "plan.example.json"


def test_construir_contexto_solo_expone_interfaz_de_dependencias():
    plan = json.loads(PLAN_EJEMPLO.read_text())
    contexto = construir_contexto(plan, "PED-003")

    assert set(contexto["item"].keys()) == {
        "id", "tipo", "descripcion", "archivos_destino", "detalle_tecnico"
    }, "el item no debe traer criterios_aceptacion ni depende_de al contexto"

    assert list(contexto["dependencias"].keys()) == ["PED-001"]
    interfaz_ped001 = contexto["dependencias"]["PED-001"]
    assert "dependencia_reusable" in interfaz_ped001
    assert "detalle_tecnico" not in interfaz_ped001, (
        "la dependencia solo debe traer su interfaz, no el detalle técnico completo"
    )
    print("OK: construir_contexto expone solo interfaz de dependencias")


def test_parsear_respuesta_archivos():
    texto = (
        "### FILE: backend/app/api/v1/auth.py\n"
        "contenido del archivo 1\n"
        "### END FILE\n"
        "### FILE: backend/app/schemas/auth.py\n"
        "contenido del archivo 2\n"
        "### END FILE\n"
    )
    resultado = parsear_respuesta(texto)
    assert resultado == {
        "archivos": {
            "backend/app/api/v1/auth.py": "contenido del archivo 1",
            "backend/app/schemas/auth.py": "contenido del archivo 2",
        },
        "dependencia_reusable": [],
    }
    print("OK: parsear_respuesta con archivos múltiples")


def test_parsear_respuesta_con_bloque_interfaz():
    texto = (
        "### FILE: x.py\n"
        "contenido\n"
        "### END FILE\n"
        "### INTERFAZ\n"
        '[{"nombre": "Foo", "import": "app.x.Foo", "firma": "class Foo", "uso": "reuso"}]\n'
        "### END INTERFAZ\n"
    )
    resultado = parsear_respuesta(texto)
    assert resultado["archivos"] == {"x.py": "contenido"}
    assert resultado["dependencia_reusable"] == [
        {"nombre": "Foo", "import": "app.x.Foo", "firma": "class Foo", "uso": "reuso"}
    ]
    print("OK: parsear_respuesta extrae el bloque ### INTERFAZ sin mezclarlo con los archivos")


def test_parsear_respuesta_interfaz_mal_formada_no_bloquea_el_item():
    texto = (
        "### FILE: x.py\n"
        "contenido\n"
        "### END FILE\n"
        "### INTERFAZ\n"
        "esto no es JSON válido\n"
        "### END INTERFAZ\n"
    )
    resultado = parsear_respuesta(texto)
    assert resultado["archivos"] == {"x.py": "contenido"}
    assert resultado["dependencia_reusable"] == []
    print("OK: bloque ### INTERFAZ mal formado no bloquea el item, solo queda sin interfaz real")


def test_parsear_respuesta_bloqueado():
    texto = "### BLOQUEADO\nno queda claro cómo se calcula el total del pedido"
    resultado = parsear_respuesta(texto)
    assert resultado == {"bloqueado": "no queda claro cómo se calcula el total del pedido"}
    print("OK: parsear_respuesta con bloqueo")


def test_parsear_respuesta_malformada():
    texto = "### FILE: x.py\nsin cierre"
    try:
        parsear_respuesta(texto)
        raise AssertionError("debería haber lanzado ValueError")
    except ValueError:
        print("OK: parsear_respuesta rechaza formato inválido")


def test_compliance_parsear_respuesta_valida():
    texto = json.dumps({
        "veredicto": "aprobado",
        "criterios_evaluados": [{"criterio": "x", "cumplido": True, "detalle": "y"}],
        "detalle": ""
    })
    resultado = parsear_respuesta_compliance(texto)
    assert resultado["veredicto"] == "aprobado"
    print("OK: compliance.parsear_respuesta con JSON válido")


def test_compliance_parsear_respuesta_tolera_code_fences():
    texto = "```json\n" + json.dumps({
        "veredicto": "rechazado",
        "criterios_evaluados": [{"criterio": "x", "cumplido": False, "detalle": "y"}],
    }) + "\n```"
    resultado = parsear_respuesta_compliance(texto)
    assert resultado["veredicto"] == "rechazado"
    print("OK: compliance.parsear_respuesta tolera code fences")


def test_compliance_parsear_respuesta_veredicto_invalido():
    texto = json.dumps({"veredicto": "tal_vez", "criterios_evaluados": []})
    try:
        parsear_respuesta_compliance(texto)
        raise AssertionError("debería haber lanzado ValueError")
    except ValueError:
        print("OK: compliance.parsear_respuesta rechaza veredicto inválido")


def test_calcular_veredicto_aprobado_si_todos_cumplidos():
    criterios = [{"cumplido": True}, {"cumplido": True}, {"cumplido": True}]
    assert _calcular_veredicto(criterios) == "aprobado"
    print("OK: _calcular_veredicto da aprobado si todos los criterios están cumplidos")


def test_calcular_veredicto_rechazado_si_falta_uno():
    criterios = [{"cumplido": True}, {"cumplido": False}, {"cumplido": True}]
    assert _calcular_veredicto(criterios) == "rechazado"
    print("OK: _calcular_veredicto da rechazado si falta un solo criterio")


def test_calcular_veredicto_ignora_lo_que_el_modelo_haya_escrito():
    # Caso real (2026-08-20) -- el modelo marcó los 9
    # criterios cumplido=true pero escribió veredicto="rechazado" en el
    # campo top-level. validar_item() debe recalcular, no confiar en eso.
    criterios_todos_cumplidos = [{"cumplido": True}] * 9
    assert _calcular_veredicto(criterios_todos_cumplidos) == "aprobado"
    print("OK: _calcular_veredicto no depende del campo veredicto que haya escrito el modelo")


def _item_compliance(item_id, ticket_id="T1", archivos_destino=None, depende_de=None):
    return {
        "id": item_id,
        "ticket_id": ticket_id,
        "tipo": "backend",
        "descripcion": f"item {item_id}",
        "archivos_destino": archivos_destino or [f"{item_id}.py"],
        "criterios_aceptacion": ["algún criterio"],
        "depende_de": depende_de or [],
        "interfaz": {},
    }


def test_compliance_construir_contexto_incluye_infraestructura_compartida_siempre():
    with tempfile.TemporaryDirectory() as tmp:
        project_root = Path(tmp)
        (project_root / "app" / "core").mkdir(parents=True)
        (project_root / "app" / "core" / "exception_handlers.py").write_text("STATUS_MAP = {'X': 401}\n")
        (project_root / "app" / "negocio.py").write_text("# negocio\n")

        plan = {
            "decisiones_globales": {},
            "items": [
                _item_compliance("CORE-001", ticket_id=None, archivos_destino=["app/core/exception_handlers.py"]),
                _item_compliance("NEG-001", archivos_destino=["app/negocio.py"]),  # no depende de CORE-001
            ],
        }
        guard = AgentFileGuard("compliance", str(project_root))
        contexto = construir_contexto_compliance(plan, "NEG-001", guard)

        assert "app/core/exception_handlers.py" in contexto["archivos_infraestructura_compartida"]
        assert "STATUS_MAP" in contexto["archivos_infraestructura_compartida"]["app/core/exception_handlers.py"]
        print("OK: construir_contexto de Compliance incluye infraestructura (ticket_id=null) aunque no esté en depende_de")


def test_compliance_construir_contexto_incluye_detalle_tecnico_del_item():
    """
    Encontrado en la práctica (2026-08-21): Compliance
    rechazaba items cuyos criterios_aceptacion citaban "detalle_tecnico"
    (ej. "las columnas listadas en detalle_tecnico") porque construir_contexto
    nunca se lo mandaba -- ni siquiera pasaba por infraestructura compartida
    ni dependencias, era el propio item el que faltaba. Compliance debería
    tener MÁS contexto que Executor, no menos.
    """
    with tempfile.TemporaryDirectory() as tmp:
        project_root = Path(tmp)
        (project_root / "app").mkdir()
        (project_root / "app" / "core.py").write_text("# core\n")

        item = _item_compliance("CORE-001", archivos_destino=["app/core.py"])
        item["detalle_tecnico"] = "instrucción técnica puntual para este item"
        plan = {"decisiones_globales": {}, "items": [item]}
        guard = AgentFileGuard("compliance", str(project_root))
        contexto = construir_contexto_compliance(plan, "CORE-001", guard)

        assert contexto["item"]["detalle_tecnico"] == "instrucción técnica puntual para este item"
        print("OK: construir_contexto de Compliance incluye detalle_tecnico del item (antes faltaba, causaba rechazos falsos)")


def test_compliance_construir_contexto_incluye_chequeos_previos_segun_tipo():
    """
    Encontrado en la práctica (2026-08-23): Compliance
    rechazó un item frontend correcto (7/7 criterios de contenido cumplidos)
    solo porque no podía "confirmar" que ng build compilaba -- un hecho que
    ya estaba garantizado (frontend_check.py corre y aprueba ANTES de que
    Compliance se invoque siquiera, ver orchestrator.py::
    validar_con_format_check), pero que construir_contexto nunca comunicaba.
    """
    with tempfile.TemporaryDirectory() as tmp:
        project_root = Path(tmp)
        (project_root / "app").mkdir()
        (project_root / "app" / "core.py").write_text("# core\n")

        item_fe = _item_compliance("FE-001", archivos_destino=["app/core.py"])
        item_fe["tipo"] = "frontend"
        item_be_con_tests = _item_compliance("BE-001", archivos_destino=["app/core.py"])
        item_be_con_tests["tests_requeridos"] = [{"archivo": "test_x.py", "contenido": "# ..."}]
        item_be_sin_tests = _item_compliance("BE-002", archivos_destino=["app/core.py"])

        guard = AgentFileGuard("compliance", str(project_root))

        plan_fe = {"decisiones_globales": {}, "items": [item_fe]}
        contexto_fe = construir_contexto_compliance(plan_fe, "FE-001", guard)
        assert "ng build" in contexto_fe["chequeos_deterministicos_previos"]
        assert "ya" in contexto_fe["chequeos_deterministicos_previos"]

        plan_be_tests = {"decisiones_globales": {}, "items": [item_be_con_tests]}
        contexto_be_tests = construir_contexto_compliance(plan_be_tests, "BE-001", guard)
        assert "smoke test" in contexto_be_tests["chequeos_deterministicos_previos"]

        plan_be = {"decisiones_globales": {}, "items": [item_be_sin_tests]}
        contexto_be = construir_contexto_compliance(plan_be, "BE-002", guard)
        assert "format check" in contexto_be["chequeos_deterministicos_previos"]
        assert "smoke test" not in contexto_be["chequeos_deterministicos_previos"]

        print("OK: construir_contexto de Compliance incluye chequeos_deterministicos_previos según tipo (antes faltaba, causaba rechazos falsos por 'no puedo confirmar que compila')")


def test_compliance_construir_contexto_detalle_tecnico_ausente_no_rompe():
    with tempfile.TemporaryDirectory() as tmp:
        project_root = Path(tmp)
        (project_root / "app").mkdir()
        (project_root / "app" / "core.py").write_text("# core\n")

        plan = {
            "decisiones_globales": {},
            "items": [_item_compliance("CORE-001", archivos_destino=["app/core.py"])],
        }
        guard = AgentFileGuard("compliance", str(project_root))
        contexto = construir_contexto_compliance(plan, "CORE-001", guard)

        assert contexto["item"]["detalle_tecnico"] == ""
        print("OK: construir_contexto de Compliance no rompe si un item no trae detalle_tecnico (fixtures viejos)")


def test_compliance_construir_contexto_no_duplica_el_item_propio_como_infraestructura():
    with tempfile.TemporaryDirectory() as tmp:
        project_root = Path(tmp)
        (project_root / "app").mkdir()
        (project_root / "app" / "core.py").write_text("# core\n")

        plan = {
            "decisiones_globales": {},
            "items": [_item_compliance("CORE-001", ticket_id=None, archivos_destino=["app/core.py"])],
        }
        guard = AgentFileGuard("compliance", str(project_root))
        contexto = construir_contexto_compliance(plan, "CORE-001", guard)

        assert "app/core.py" not in contexto["archivos_infraestructura_compartida"], (
            "el propio archivo del item ya viene en 'archivos', no debería duplicarse en infraestructura"
        )
        assert "app/core.py" in contexto["archivos"]
        print("OK: construir_contexto no duplica el archivo del propio item como infraestructura")


def test_compliance_construir_contexto_incluye_archivos_reales_de_dependencias():
    with tempfile.TemporaryDirectory() as tmp:
        project_root = Path(tmp)
        (project_root / "app").mkdir()
        (project_root / "app" / "auth_router.py").write_text("router = APIRouter(prefix='/auth')\n")
        (project_root / "app" / "main.py").write_text("# ensamblador\n")

        plan = {
            "decisiones_globales": {},
            "items": [
                _item_compliance("AUTH-001", archivos_destino=["app/auth_router.py"]),
                _item_compliance("CORE-003", ticket_id=None, archivos_destino=["app/main.py"], depende_de=["AUTH-001"]),
            ],
        }
        guard = AgentFileGuard("compliance", str(project_root))
        contexto = construir_contexto_compliance(plan, "CORE-003", guard)

        assert "app/auth_router.py" in contexto["archivos_reales_de_dependencias"]
        assert "prefix='/auth'" in contexto["archivos_reales_de_dependencias"]["app/auth_router.py"]
        print("OK: construir_contexto incluye el contenido real (no solo interfaz) de los items en depende_de")


def test_compliance_construir_contexto_arbol_archivos_lista_todo_el_proyecto():
    with tempfile.TemporaryDirectory() as tmp:
        project_root = Path(tmp)
        (project_root / "app").mkdir()
        (project_root / "app" / "x.py").write_text("x\n")
        (project_root / "app" / "y.py").write_text("y\n")

        plan = {
            "decisiones_globales": {},
            "items": [_item_compliance("X-001", archivos_destino=["app/x.py"])],
        }
        guard = AgentFileGuard("compliance", str(project_root))
        contexto = construir_contexto_compliance(plan, "X-001", guard)

        assert "app/x.py" in contexto["arbol_archivos_proyecto"]
        assert "app/y.py" in contexto["arbol_archivos_proyecto"], (
            "el árbol debe listar TODO el proyecto, no solo lo del item en validación"
        )
        print("OK: arbol_archivos_proyecto lista todos los archivos del proyecto, no solo los del item")


def test_orchestrator_selecciona_en_orden_y_respeta_dependencias():
    with tempfile.TemporaryDirectory() as tmp:
        project_root = Path(tmp)
        harness = project_root / ".harness"
        (harness / "config").mkdir(parents=True)
        (harness / "logs").mkdir()
        (harness / "validation").mkdir()
        shutil.copy(PLAN_EJEMPLO, harness / "config" / "plan.json")
        contador = count(1)
        ts = lambda: f"2026-01-01T00:00:{next(contador):02d}+00:00"

        # Al arrancar, nada tiene veredicto ni log -> todo pendiente,
        # el primero sin dependencias es PED-001. Tampoco hay nada listo
        # para Compliance todavía (Executor no generó nada).
        assert seleccionar_siguiente_item(str(project_root)) == "PED-001"
        assert seleccionar_siguiente_item_para_compliance(str(project_root)) is None

        # Executor "termina" PED-001 (evento finalizado, sin veredicto aún)
        # -> ahora sí queda listo para Compliance, pero no para Executor de nuevo.
        with (harness / "logs" / "executor.jsonl").open("a") as f:
            f.write(json.dumps({
                "item_id": "PED-001", "evento": "iniciado", "timestamp": ts(), "detalle": ""
            }) + "\n")
            f.write(json.dumps({
                "item_id": "PED-001", "evento": "finalizado", "timestamp": ts(), "detalle": ""
            }) + "\n")
        assert seleccionar_siguiente_item_para_compliance(str(project_root)) == "PED-001"
        assert seleccionar_siguiente_item(str(project_root)) != "PED-001"

        # Aprobamos PED-001 -> ya no está listo para Compliance, y el
        # siguiente pendiente para Executor con deps satisfechas es PED-002
        # (aparece antes que PED-003/PED-004 en items[]).
        (harness / "validation" / "PED-001.json").write_text(json.dumps({
            "item_id": "PED-001", "veredicto": "aprobado", "timestamp": ts(),
            "criterios_evaluados": [], "detalle": ""
        }))
        assert seleccionar_siguiente_item(str(project_root)) == "PED-002"
        assert seleccionar_siguiente_item_para_compliance(str(project_root)) is None

        estados = calcular_estados(str(project_root))
        assert estados["PED-001"] == "completado"
        assert estados["PED-002"] == "pendiente"

        # PED-002 queda bloqueado (evento en el log, sin veredicto) ->
        # el orquestador no debe volver a elegirlo solo.
        with (harness / "logs" / "executor.jsonl").open("a") as f:
            f.write(json.dumps({
                "item_id": "PED-002", "evento": "bloqueado", "timestamp": ts(),
                "detalle": "ambiguo"
            }) + "\n")

        estados = calcular_estados(str(project_root))
        assert estados["PED-002"] == "bloqueado"
        assert seleccionar_siguiente_item(str(project_root)) != "PED-002"

        print("OK: orchestrator respeta orden, dependencias y bloqueos")


def test_feedback_reintento_solo_incluye_criterios_no_cumplidos():
    veredicto = {
        "criterios_evaluados": [
            {"criterio": "A", "cumplido": True, "detalle": "ok"},
            {"criterio": "B", "cumplido": False, "detalle": "falta manejar el 401"},
        ],
        "detalle": "casi, falta un caso de error",
    }
    feedback = _construir_feedback_reintento(veredicto)
    assert "B" in feedback and "falta manejar el 401" in feedback
    assert "A" not in feedback.split("\n")[0], "no debería listar el criterio ya cumplido"
    assert "casi, falta un caso de error" in feedback
    print("OK: _construir_feedback_reintento solo lista lo no cumplido")


def test_loop_reintenta_rechazado_y_luego_agota():
    with tempfile.TemporaryDirectory() as tmp:
        project_root = Path(tmp)
        harness = project_root / ".harness"
        (harness / "config").mkdir(parents=True)
        (harness / "logs").mkdir()
        (harness / "validation").mkdir()
        shutil.copy(PLAN_EJEMPLO, harness / "config" / "plan.json")
        contador = count(1)
        ts = lambda: f"2026-01-01T00:00:{next(contador):02d}+00:00"

        plan = json.loads(PLAN_EJEMPLO.read_text())
        item_ped_001 = next(i for i in plan["items"] if i["id"] == "PED-001")

        def rechazar_con_intentos(n):
            with (harness / "logs" / "executor.jsonl").open("a") as f:
                for _ in range(n):
                    f.write(json.dumps({
                        "item_id": "PED-001", "evento": "iniciado", "timestamp": ts(), "detalle": ""
                    }) + "\n")
                    f.write(json.dumps({
                        "item_id": "PED-001", "evento": "finalizado", "timestamp": ts(), "detalle": ""
                    }) + "\n")
            # veredicto escrito DESPUÉS de los eventos de este intento -> no queda desactualizado
            veredicto = {
                "item_id": "PED-001", "veredicto": "rechazado", "timestamp": ts(),
                "criterios_evaluados": [{"criterio": "x", "cumplido": False, "detalle": "y"}],
                "detalle": ""
            }
            (harness / "validation" / "PED-001.json").write_text(json.dumps(veredicto))
            # mismo paso que hace validar_con_format_check en cada rechazo real
            _actualizar_ticket_reintento(project_root, item_ped_001, veredicto, fuente="compliance")

        # 1 intento ya hecho, max_reintentos=2 (3 permitidos) -> todavía elegible, con feedback
        rechazar_con_intentos(1)
        assert _contar_intentos(project_root, "PED-001") == 1
        seleccion = seleccionar_siguiente_para_loop(str(project_root), max_reintentos=2)
        assert seleccion is not None and seleccion[0] == "PED-001"
        assert seleccion[1] is not None and "y" in seleccion[1]

        # 3 intentos ya hechos (agotado) -> ya no elegible, y como todo lo demás
        # depende de PED-001 (no completado), no hay nada más para ofrecer.
        rechazar_con_intentos(2)  # total 3
        assert _contar_intentos(project_root, "PED-001") == 3
        assert seleccionar_siguiente_para_loop(str(project_root), max_reintentos=2) is None

        print("OK: seleccionar_siguiente_para_loop reintenta y luego agota correctamente")

        # excluido a mano (gate de decisión "lo arreglo yo mismo") -> no se ofrece
        # más, aunque todavía tuviera intentos disponibles.
        assert seleccionar_siguiente_para_loop(
            str(project_root), max_reintentos=5, excluir={"PED-001"}
        ) is None
        print("OK: seleccionar_siguiente_para_loop respeta el set de excluidos")


def test_reporte_rechazo_y_registro_de_decision_quedan_en_disco():
    with tempfile.TemporaryDirectory() as tmp:
        project_root = Path(tmp)
        harness = project_root / ".harness"
        (harness / "logs").mkdir(parents=True)

        ruta_ticket = _ruta_ticket(project_root, "PED-001")
        _escribir_reporte_rechazo(project_root, "PED-001", ruta_ticket, intentos=1, total_intentos_permitidos=3)
        reporte = (harness / "logs" / "reporte_fallas.md").read_text()
        assert "PED-001" in reporte and "tickets/PED-001.md" in reporte
        assert "1/3" in reporte

        _registrar_decision_reintento(project_root, "PED-001", "editar")
        lineas = (harness / "logs" / "decisiones_reintento.jsonl").read_text().strip().splitlines()
        assert len(lineas) == 1
        entrada = json.loads(lineas[0])
        assert entrada["item_id"] == "PED-001"
        assert entrada["decision"] == "editar"

        print("OK: reporte de rechazo y registro de decisión quedan en disco")


def test_actualizar_ticket_reintento_crea_y_acumula_historial():
    with tempfile.TemporaryDirectory() as tmp:
        project_root = Path(tmp)
        item = {
            "id": "PED-001",
            "archivos_destino": [],
            "criterios_aceptacion": ["el login devuelve 200 con credenciales válidas"],
        }
        veredicto_1 = {
            "criterios_evaluados": [{"criterio": "A", "cumplido": False, "detalle": "falta el 401"}],
            "detalle": "",
        }
        texto_1 = _actualizar_ticket_reintento(project_root, item, veredicto_1, fuente="compliance")
        assert "el login devuelve 200 con credenciales válidas" in texto_1
        assert "Intento 1" in texto_1 and "fuente: compliance" in texto_1
        assert "falta el 401" in texto_1

        veredicto_2 = {
            "criterios_evaluados": [{"criterio": "B", "cumplido": False, "detalle": "rompió el otro caso"}],
            "detalle": "",
        }
        texto_2 = _actualizar_ticket_reintento(project_root, item, veredicto_2, fuente="smoke_test")
        # el historial ACUMULA -- el intento 1 sigue presente, no se pisa
        assert "Intento 1" in texto_2 and "falta el 401" in texto_2
        assert "Intento 2" in texto_2 and "fuente: smoke_test" in texto_2 and "rompió el otro caso" in texto_2

        assert _leer_ticket(project_root, "PED-001") == texto_2
        print("OK: _actualizar_ticket_reintento crea el ticket y acumula el historial de intentos")


def test_actualizar_ticket_reintento_no_toca_hechos_verificados():
    with tempfile.TemporaryDirectory() as tmp:
        project_root = Path(tmp)
        item = {"id": "PED-001", "archivos_destino": [], "criterios_aceptacion": ["x"]}
        veredicto = {"criterios_evaluados": [], "detalle": "primer rechazo"}
        _actualizar_ticket_reintento(project_root, item, veredicto, fuente="compliance")

        ruta = _ruta_ticket(project_root, "PED-001")
        texto = ruta.read_text(encoding="utf-8")
        texto_con_hechos = texto.replace(
            "## Hechos verificados\n\n",
            "## Hechos verificados\n\nget_db se importa de app.core.database, no de app.repository.database\n",
        )
        ruta.write_text(texto_con_hechos, encoding="utf-8")

        veredicto_2 = {"criterios_evaluados": [], "detalle": "segundo rechazo"}
        texto_final = _actualizar_ticket_reintento(project_root, item, veredicto_2, fuente="compliance")
        assert "get_db se importa de app.core.database, no de app.repository.database" in texto_final
        print("OK: _actualizar_ticket_reintento nunca pisa 'Hechos verificados' ya escrito a mano")


def test_actualizar_ticket_reintento_sobreescribe_codigo_actual():
    with tempfile.TemporaryDirectory() as tmp:
        project_root = Path(tmp)
        archivo = project_root / "backend" / "app.py"
        archivo.parent.mkdir(parents=True)
        item = {"id": "PED-001", "archivos_destino": ["backend/app.py"], "criterios_aceptacion": []}

        archivo.write_text("version_1 = True")
        texto_1 = _actualizar_ticket_reintento(project_root, item, {"criterios_evaluados": [], "detalle": ""}, fuente="compliance")
        assert "version_1 = True" in texto_1

        archivo.write_text("version_2 = True")
        texto_2 = _actualizar_ticket_reintento(project_root, item, {"criterios_evaluados": [], "detalle": ""}, fuente="compliance")
        assert "version_2 = True" in texto_2 and "version_1 = True" not in texto_2
        print("OK: _actualizar_ticket_reintento sobreescribe 'Código actual' completo, no acumula versiones viejas")


def test_format_check_detecta_import_roto_y_nombre_pisado():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "backend" / "app" / "service").mkdir(parents=True)
        (root / "backend" / "app" / "api" / "v1").mkdir(parents=True)

        (root / "backend" / "app" / "service" / "foo_service.py").write_text(
            "def hacer_algo():\n    return 42\n"
        )
        (root / "backend" / "app" / "api" / "v1" / "roto.py").write_text(
            "from app.api.v1.auth.router import router as auth_router\n"
            "from app.service.foo_service import hacer_algo\n\n"
            "def hacer_algo():\n"
            "    return hacer_algo()\n\n\n"
            "def obtener_router():\n"
            "    return auth_router\n"
        )
        (root / "backend" / "app" / "api" / "v1" / "bien.py").write_text(
            "from app.service.foo_service import hacer_algo\n\n"
            "def usar():\n"
            "    return hacer_algo()\n"
        )

        errores = format_check.verificar(str(root), [
            "backend/app/api/v1/roto.py",
            "backend/app/api/v1/bien.py",
        ])
        assert len(errores) == 2, f"esperaba 2 errores (import roto + nombre pisado), hubo {len(errores)}"
        assert any("no resuelve a ningún archivo" in e for e in errores)
        assert any("pisa al import" in e for e in errores)
        print("OK: format_check detecta import interno roto y nombre que pisa un import")


def test_format_check_detecta_nombre_importado_que_no_existe_en_el_modulo():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "backend" / "app" / "repository").mkdir(parents=True)
        (root / "backend" / "app" / "service").mkdir(parents=True)

        # el módulo real expone funciones sueltas, no una clase
        (root / "backend" / "app" / "repository" / "usuario_repository.py").write_text(
            "def get_by_rut(session, rut):\n    return None\n"
        )
        (root / "backend" / "app" / "service" / "roto.py").write_text(
            "from app.repository.usuario_repository import UsuarioRepository\n\n"
            "def usar():\n"
            "    return UsuarioRepository\n"
        )
        (root / "backend" / "app" / "service" / "bien.py").write_text(
            "from app.repository.usuario_repository import get_by_rut\n\n"
            "def usar():\n"
            "    return get_by_rut\n"
        )

        errores_roto = format_check.verificar(str(root), ["backend/app/service/roto.py"])
        assert len(errores_roto) == 1
        assert "UsuarioRepository" in errores_roto[0]
        assert "no está definido en usuario_repository.py" in errores_roto[0]

        errores_bien = format_check.verificar(str(root), ["backend/app/service/bien.py"])
        assert errores_bien == []
        print("OK: format_check detecta un nombre importado que el módulo real no expone (aunque el módulo sí exista)")


def test_format_check_detecta_import_no_usado():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "backend" / "app" / "repository").mkdir(parents=True)
        (root / "backend" / "app" / "service").mkdir(parents=True)

        (root / "backend" / "app" / "repository" / "venta_repository.py").write_text(
            "from pydantic import BaseModel\n\n\n"
            "class VentaRow(BaseModel):\n"
            "    monto: int\n\n\n"
            "def obtener_ventas():\n"
            "    return []\n"
        )
        (root / "backend" / "app" / "service" / "roto.py").write_text(
            "from app.repository.venta_repository import VentaRow, obtener_ventas\n\n"
            "def usar():\n"
            "    return obtener_ventas()\n"
        )
        (root / "backend" / "app" / "service" / "bien.py").write_text(
            "from app.repository.venta_repository import VentaRow, obtener_ventas\n\n"
            "def usar() -> list[VentaRow]:\n"
            "    return obtener_ventas()\n"
        )

        errores_roto = format_check.verificar(str(root), ["backend/app/service/roto.py"])
        assert len(errores_roto) == 1, errores_roto
        assert "VentaRow" in errores_roto[0]
        assert "F401" in errores_roto[0]

        errores_bien = format_check.verificar(str(root), ["backend/app/service/bien.py"])
        assert errores_bien == []
        print("OK: format_check detecta un import no usado (ruff F401)")


def test_format_check_no_marca_falsos_positivos():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "backend" / "app" / "service").mkdir(parents=True)
        (root / "backend" / "app" / "service" / "foo_service.py").write_text(
            "def hacer_algo():\n    return 42\n"
        )
        (root / "backend" / "app" / "service" / "bien.py").write_text(
            "from app.service.foo_service import hacer_algo\n\n"
            "def usar():\n"
            "    return hacer_algo()\n"
        )
        errores = format_check.verificar(str(root), ["backend/app/service/bien.py"])
        assert errores == []
        print("OK: format_check no marca falsos positivos en código correcto")


def test_format_check_resuelve_import_desde_archivo_hermano_de_app():
    # Un archivo que vive FUERA de app/ (backend/tests/, backend/alembic/)
    # pero al mismo nivel que ella (hermano, no contenido) también puede
    # importar app.* -- encontrado en la práctica migrando un proyecto real
    # (2026-08-26): backend/tests/test_config.py y backend/alembic/env.py
    # importaban app.core.config y format_check los marcaba como "no
    # resuelve" pese a que el módulo sí existía, porque _paquete_root
    # exigía que "app" apareciera en la ruta del propio archivo chequeado.
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "backend" / "app" / "core").mkdir(parents=True)
        (root / "backend" / "tests").mkdir(parents=True)
        (root / "backend" / "alembic").mkdir(parents=True)
        (root / "backend" / "app" / "core" / "config.py").write_text(
            "def get_settings():\n    return object()\n"
        )
        (root / "backend" / "tests" / "test_config.py").write_text(
            "from app.core.config import get_settings\n\n"
            "def test_algo():\n"
            "    assert get_settings() is not None\n"
        )
        (root / "backend" / "alembic" / "env.py").write_text(
            "from app.core.config import get_settings\n\n"
            "settings = get_settings()\n"
        )
        errores = format_check.verificar(
            str(root), ["backend/tests/test_config.py", "backend/alembic/env.py"]
        )
        assert errores == []
        print("OK: format_check resuelve imports de archivos hermanos de app/ (tests/, alembic/)")


def test_validar_con_format_check_rechaza_sin_llamar_a_compliance():
    with tempfile.TemporaryDirectory() as tmp:
        project_root = Path(tmp)
        harness = project_root / ".harness"
        (harness / "config").mkdir(parents=True)
        (harness / "logs").mkdir()
        (harness / "validation").mkdir()
        shutil.copy(PLAN_EJEMPLO, harness / "config" / "plan.json")

        # PED-001 archivos_destino declara 3 archivos que nunca se generaron
        # de verdad -> el import check no tiene nada que analizar (no son
        # .py existentes), así que en este caso el format check no encuentra
        # nada y SÍ intentaría llamar a Compliance real. Para probar el
        # camino de rechazo sin red, generamos un archivo con un import roto
        # a mano, apuntado por un item ad-hoc.
        plan = json.loads((harness / "config" / "plan.json").read_text())
        item_id = plan["items"][0]["id"]
        archivo_relativo = plan["items"][0]["archivos_destino"][0]
        archivo_abs = project_root / archivo_relativo
        archivo_abs.parent.mkdir(parents=True, exist_ok=True)
        archivo_abs.write_text("from app.no.existe import algo\n")

        resultado = validar_con_format_check(str(project_root), item_id)
        assert resultado["estado"] == "rechazado"
        assert "format check" in resultado["veredicto"]["detalle"].lower()

        # el veredicto quedó persistido, igual que si lo hubiera escrito Compliance
        veredicto_en_disco = json.loads((harness / "validation" / f"{item_id}.json").read_text())
        assert veredicto_en_disco["veredicto"] == "rechazado"
        print("OK: validar_con_format_check rechaza sin llamar a Compliance cuando hay un import roto")


def test_smoke_test_sin_tests_requeridos():
    with tempfile.TemporaryDirectory() as tmp:
        resultado = smoke_test.correr(tmp, {"tests_requeridos": []})
        assert resultado == {"estado": "sin_tests"}
    print("OK: smoke_test.correr no hace nada si el item no declara tests_requeridos")


def test_smoke_test_pasa():
    with tempfile.TemporaryDirectory() as tmp:
        item = {"tests_requeridos": [
            {"archivo": "tests/test_smoke_ok.py", "contenido": "def test_ok():\n    assert 1 + 1 == 2\n"}
        ]}
        resultado = smoke_test.correr(tmp, item, python=Path(sys.executable))
        assert resultado == {"estado": "paso"}
    print("OK: smoke_test.correr corre pytest de verdad y reporta 'paso'")


def test_smoke_test_falla():
    with tempfile.TemporaryDirectory() as tmp:
        item = {"tests_requeridos": [
            {"archivo": "tests/test_smoke_fail.py", "contenido": "def test_falla():\n    assert 1 == 2\n"}
        ]}
        resultado = smoke_test.correr(tmp, item, python=Path(sys.executable))
        assert resultado["estado"] == "fallo"
        assert "test_falla" in resultado["detalle"]
    print("OK: smoke_test.correr corre pytest de verdad y reporta 'fallo' con el detalle real")


def test_smoke_test_venv_no_encontrado():
    with tempfile.TemporaryDirectory() as tmp:
        item = {"tests_requeridos": [
            {"archivo": "tests/test_x.py", "contenido": "def test_x():\n    assert True\n"}
        ]}
        resultado = smoke_test.correr(tmp, item)  # sin override, sin venv en el tmp -> no lo encuentra
        assert resultado["estado"] == "error"
        assert "venv" in resultado["detalle"].lower()
    print("OK: smoke_test.correr reporta 'error' claro si no encuentra un venv del proyecto destino")


def test_smoke_test_carpeta_deployable_no_asume_siempre_backend():
    assert smoke_test._carpeta_deployable({"archivos_destino": ["dal/app/model/x.py"]}) == "dal"
    assert smoke_test._carpeta_deployable({"archivos_destino": ["backend/app/api/x.py"]}) == "backend"
    assert smoke_test._carpeta_deployable({}) == "backend"  # fixtures viejos sin archivos_destino
    print("OK: smoke_test deriva la carpeta del deployable de archivos_destino, no asume 'backend' siempre")


def test_smoke_test_usa_el_venv_de_la_carpeta_del_item():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        # venv real (con pytest instalado) solo bajo dal/ -- si el detector
        # siguiera asumiendo "backend" a secas, este item (carpeta "dal") no lo
        # encontraría. Symlink al venv del propio Harness (no una copia suelta
        # del binario: necesita el site-packages del venv, no solo el exe).
        (root / "dal").mkdir()
        # OJO: sin .resolve() -- resolver el symlink de sys.executable (venv/bin/python
        # -> python3 -> /usr/bin/python3) se saldría del venv hacia el intérprete de
        # sistema, perdiendo el site-packages donde vive pytest.
        venv_harness = Path(sys.executable).absolute().parent.parent
        (root / "dal" / "venv").symlink_to(venv_harness, target_is_directory=True)

        item = {
            "archivos_destino": ["dal/app/model/x.py"],
            "tests_requeridos": [
                {"archivo": "dal/tests/test_ok.py", "contenido": "def test_ok():\n    assert 1 + 1 == 2\n"}
            ],
        }
        resultado = smoke_test.correr(str(root), item)  # sin override -> autodetecta
        assert resultado == {"estado": "paso"}, resultado
    print("OK: smoke_test.correr autodetecta el venv de la carpeta propia del item (dal/), no solo backend/")


def test_validar_con_format_check_rechaza_por_smoke_test_sin_llamar_a_compliance():
    with tempfile.TemporaryDirectory() as tmp:
        project_root = Path(tmp)
        harness = project_root / ".harness"
        (harness / "config").mkdir(parents=True)
        (harness / "logs").mkdir()
        (harness / "validation").mkdir()
        shutil.copy(PLAN_EJEMPLO, harness / "config" / "plan.json")

        # venv "real" del proyecto destino: symlink al venv completo de este
        # mismo harness (symlinkear solo el binario pierde el pyvenv.cfg que
        # Python necesita para encontrar site-packages -> "No module named pytest")
        (project_root / "backend").mkdir(parents=True)
        (project_root / "backend" / "venv").symlink_to(REPO_ROOT / "venv")

        plan = json.loads((harness / "config" / "plan.json").read_text())
        item = plan["items"][0]
        item_id = item["id"]
        # archivos_destino de este item nunca se generaron de verdad, pero el
        # format check solo mira archivos que existan -> no encuentra nada. El
        # smoke test es el que debe frenarlo antes de llegar a Compliance.
        item["tests_requeridos"] = [
            {"archivo": "backend/tests/test_smoke.py", "contenido": "def test_falla_a_proposito():\n    assert False\n"}
        ]
        (harness / "config" / "plan.json").write_text(json.dumps(plan, ensure_ascii=False))

        resultado = validar_con_format_check(str(project_root), item_id)
        assert resultado["estado"] == "rechazado"
        assert "smoke test" in resultado["veredicto"]["detalle"].lower()
        assert "test_falla_a_proposito" in resultado["veredicto"]["criterios_evaluados"][0]["detalle"]
        print("OK: validar_con_format_check rechaza por smoke test (pytest real) sin llamar a Compliance")


def test_dependientes_transitivos_del_fixture_pedidos():
    plan = json.loads(PLAN_EJEMPLO.read_text())
    # PED-001 no tiene deps; PED-002/003/004 dependen de PED-001;
    # PED-005 depende de PED-002; PED-006 depende de PED-003.
    assert _dependientes_transitivos(plan, "PED-001") == {
        "PED-002", "PED-003", "PED-004", "PED-005", "PED-006"
    }
    assert _dependientes_transitivos(plan, "PED-002") == {"PED-005"}
    assert _dependientes_transitivos(plan, "PED-006") == set()
    print("OK: _dependientes_transitivos sigue la cadena completa, no solo directos")


def test_invalidar_dependientes_borra_veredictos_de_lo_que_depende_y_no_de_lo_demas():
    with tempfile.TemporaryDirectory() as tmp:
        project_root = Path(tmp)
        harness = project_root / ".harness"
        (harness / "config").mkdir(parents=True)
        (harness / "validation").mkdir()
        shutil.copy(PLAN_EJEMPLO, harness / "config" / "plan.json")

        for item_id in ["PED-001", "PED-002", "PED-003", "PED-004", "PED-005", "PED-006"]:
            (harness / "validation" / f"{item_id}.json").write_text(json.dumps({
                "item_id": item_id, "veredicto": "aprobado", "timestamp": "t",
                "criterios_evaluados": [], "detalle": ""
            }))

        invalidados = invalidar_dependientes(str(project_root), "PED-002")
        assert set(invalidados) == {"PED-005"}
        assert not (harness / "validation" / "PED-005.json").exists()
        # todo lo que no depende de PED-002 (incluido PED-002 mismo) queda intacto
        for item_id in ["PED-001", "PED-002", "PED-003", "PED-004", "PED-006"]:
            assert (harness / "validation" / f"{item_id}.json").exists()

        print("OK: invalidar_dependientes borra solo el veredicto de los dependientes reales")


def _item_minimo(item_id, depende_de=None, interfaz=None, archivos_destino=None, criterios_aceptacion=None):
    return {
        "id": item_id,
        "depende_de": depende_de or [],
        "interfaz": interfaz if interfaz is not None else {},
        "archivos_destino": archivos_destino if archivos_destino is not None else [f"{item_id}.py"],
        "criterios_aceptacion": criterios_aceptacion if criterios_aceptacion is not None else ["algún criterio"],
    }


def test_validar_plan_no_encuentra_nada_en_el_fixture_real():
    plan = json.loads(PLAN_EJEMPLO.read_text())
    assert validar_plan(plan) == []
    print("OK: validar_plan no marca falsos positivos contra el fixture de pedidos")


def test_validar_plan_detecta_id_duplicado():
    plan = {"items": [_item_minimo("A"), _item_minimo("A")]}
    errores = validar_plan(plan)
    assert any("duplicado" in e for e in errores)
    print("OK: validar_plan detecta id duplicado")


def test_validar_plan_detecta_dependencia_inexistente():
    plan = {"items": [_item_minimo("A", depende_de=["NO-EXISTE"])]}
    errores = validar_plan(plan)
    assert any("NO-EXISTE" in e for e in errores)
    print("OK: validar_plan detecta depende_de apuntando a un id inexistente")


def test_validar_plan_detecta_dependencia_circular():
    plan = {"items": [
        _item_minimo("A", depende_de=["B"]),
        _item_minimo("B", depende_de=["C"]),
        _item_minimo("C", depende_de=["A"]),
    ]}
    errores = validar_plan(plan)
    assert any("circular" in e for e in errores)
    print("OK: validar_plan detecta dependencia circular (no solo auto-referencia)")


def test_validar_plan_detecta_archivos_destino_vacio():
    plan = {"items": [_item_minimo("A", archivos_destino=[])]}
    errores = validar_plan(plan)
    assert any("archivos_destino" in e and "A:" in e for e in errores)
    print("OK: validar_plan detecta archivos_destino vacío")


def test_validar_plan_detecta_criterios_aceptacion_vacio():
    plan = {"items": [_item_minimo("A", criterios_aceptacion=[])]}
    errores = validar_plan(plan)
    assert any("criterios_aceptacion" in e and "A:" in e for e in errores)
    print("OK: validar_plan detecta criterios_aceptacion vacío")


def test_validar_plan_detecta_interfaz_vacia_en_dependencia_citada():
    plan = {"items": [
        _item_minimo("A", interfaz={}),
        _item_minimo("B", depende_de=["A"]),
    ]}
    errores = validar_plan(plan)
    assert any("A" in e and "interfaz" in e for e in errores)
    print("OK: validar_plan detecta interfaz vacía en un item citado por depende_de")


def test_validar_plan_detecta_archivo_con_mas_de_un_dueno():
    plan = {"items": [
        _item_minimo("A", archivos_destino=["compartido.py"]),
        _item_minimo("B", archivos_destino=["compartido.py"]),
    ]}
    errores = validar_plan(plan)
    assert any("compartido.py" in e for e in errores)
    print("OK: validar_plan detecta un archivo en archivos_destino de más de un item")


def test_combinar_interfaz_sin_real_devuelve_predicha_tal_cual():
    predicha = {"endpoint": {"metodo": "GET"}, "dependencia_reusable": {"nombre": "X", "import": "app.x.X"}}
    assert combinar_interfaz(predicha, None) == predicha
    assert combinar_interfaz(predicha, {}) == predicha
    print("OK: combinar_interfaz sin interfaz real devuelve la predicha sin tocar")


def test_combinar_interfaz_real_gana_en_conflicto_y_preserva_lo_demas():
    predicha = {"dependencia_reusable": [
        {"nombre": "A", "import": "app.x.A", "firma": "vieja"},
        {"nombre": "B", "import": "app.x.B", "firma": "solo en predicha"},
    ]}
    real = {"dependencia_reusable": [
        {"nombre": "A", "import": "app.x.A", "firma": "nueva, del código real"},
        {"nombre": "C", "import": "app.x.C", "firma": "solo en real"},
    ]}
    combinada = {r["import"]: r for r in combinar_interfaz(predicha, real)["dependencia_reusable"]}

    assert combinada["app.x.A"]["firma"] == "nueva, del código real", "la real debe ganar en conflicto"
    assert combinada["app.x.B"]["firma"] == "solo en predicha", "lo que solo está en la predicha se conserva"
    assert combinada["app.x.C"]["firma"] == "solo en real", "lo que solo está en la real se agrega"
    print("OK: combinar_interfaz hace unión por import, la real gana en conflicto")


def test_combinar_interfaz_normaliza_forma_singular_legacy():
    predicha = {"dependencia_reusable": {"nombre": "get_current_user", "import": "app.svc.get_current_user"}}
    combinada = combinar_interfaz(predicha, None)
    assert combinada == predicha, "sin interfaz real, la forma singular vieja no debería tocarse"

    real = {"dependencia_reusable": [{"nombre": "Nuevo", "import": "app.x.Nuevo"}]}
    combinada = combinar_interfaz(predicha, real)
    imports = {r["import"] for r in combinada["dependencia_reusable"]}
    assert imports == {"app.svc.get_current_user", "app.x.Nuevo"}, (
        "la forma singular vieja de la predicha debe normalizarse a lista al mezclar"
    )
    print("OK: combinar_interfaz normaliza la forma singular legacy de dependencia_reusable")


def test_combinar_interfaz_normaliza_forma_por_nombre_sin_perder_entradas():
    # Bug real (2026-08-26, backend con DAL separado): un
    # dict keyed-by-nombre en la predicha (sin 'import' como clave directa
    # del dict entero) se trataba como UN solo elemento sin 'import' y se
    # descartaba completo en cuanto la dependencia reportaba su propia
    # interfaz real -- un item se quedó sin los imports de schemas que
    # su dependencia sí declaraba en plan.json.
    predicha = {"dependencia_reusable": {
        "login_cliente": {"import": "app.service.autenticacion_service.login_cliente"},
        "LoginRequest": {"import": "app.schema.request.autenticacion_request.LoginRequest"},
        "TokenResponse": {"import": "app.schema.response.autenticacion_response.TokenResponse"},
    }}
    real = {"dependencia_reusable": [
        {"nombre": "login_cliente", "import": "app.service.autenticacion_service.login_cliente", "firma": "real"},
    ]}
    combinada = combinar_interfaz(predicha, real)
    imports = {r["import"] for r in combinada["dependencia_reusable"]}
    assert imports == {
        "app.service.autenticacion_service.login_cliente",
        "app.schema.request.autenticacion_request.LoginRequest",
        "app.schema.response.autenticacion_response.TokenResponse",
    }, "las entradas de la predicha que Executor no volvió a reportar deben sobrevivir al merge"
    por_import = {r["import"]: r for r in combinada["dependencia_reusable"]}
    assert por_import["app.service.autenticacion_service.login_cliente"]["firma"] == "real", "la real sigue ganando en conflicto"
    print("OK: combinar_interfaz normaliza dependencia_reusable en forma por-nombre sin perder entradas de la predicha")


def test_podar_predicha_no_generada_descarta_simbolo_inexistente():
    # Caso real (backend con DAL separado,
    # 2026-08-26): un item predijo un único 'router_autenticacion' que el código
    # real nunca implementó bajo ese nombre (Executor optó por 3 routers
    # separados). Ver docstring de podar_predicha_no_generada.
    predicha = {"dependencia_reusable": [
        {"nombre": "router_autenticacion", "import": "app.api.v1.autenticacion.router"},
        {"nombre": "UsuarioSchema", "import": "app.schema.autenticacion_schema.UsuarioSchema"},
    ]}
    codigo_generado = (
        "router_usuarios = APIRouter(prefix='/usuarios')\n"
        "router_administradores = APIRouter(prefix='/administradores')\n\n"
        "class UsuarioSchema(BaseModel):\n"
        "    ca_rut: int\n"
    )
    podada = podar_predicha_no_generada(predicha, codigo_generado)
    nombres = {r["nombre"] for r in podada["dependencia_reusable"]}
    assert nombres == {"UsuarioSchema"}, "router_autenticacion no existe en el código real, debe descartarse"
    print("OK: podar_predicha_no_generada descarta un símbolo que el código real nunca implementó")


def test_podar_predicha_no_generada_sin_codigo_no_toca_nada():
    predicha = {"dependencia_reusable": [{"nombre": "X", "import": "app.x.X"}]}
    assert podar_predicha_no_generada(predicha, None) == predicha
    assert podar_predicha_no_generada(predicha, "") == predicha
    print("OK: podar_predicha_no_generada sin código generado devuelve la predicha sin tocar")


def test_construir_contexto_incluye_interfaz_real_de_una_dependencia():
    with tempfile.TemporaryDirectory() as tmp:
        project_root = Path(tmp)
        (project_root / ".harness" / "interfaces").mkdir(parents=True)

        guard = AgentFileGuard("executor", str(project_root))
        guard.write(Zona.HARNESS_INTERFACES, "PED-001.json", json.dumps({
            "item_id": "PED-001",
            "timestamp": "2026-01-01T00:00:00+00:00",
            "interfaz": {"dependencia_reusable": [
                {"nombre": "hash_password", "import": "app.services.auth_service.hash_password", "firma": "(s: str) -> str"}
            ]},
        }))

        plan = json.loads(PLAN_EJEMPLO.read_text())
        contexto = construir_contexto(plan, "PED-002", guard)
        reusables = {r["import"] for r in contexto["dependencias"]["PED-001"]["dependencia_reusable"]}

        assert "app.services.auth_service.hash_password" in reusables, (
            "la interfaz real de PED-001 debe sumarse a la predicha por el Planner"
        )
        print("OK: construir_contexto de Executor mezcla la interfaz real de sus dependencias cuando hay guard")


def test_construir_contexto_no_arrastra_predicha_de_simbolo_inexistente():
    # Reproduce un caso real (2026-08-26): la
    # predicha de PED-001 dice 'get_current_user' (ver plan.example.json),
    # pero el código real generado para PED-001 nunca definió ese nombre --
    # solo 'hash_password' (que sí reporta como real). Sin la poda,
    # 'get_current_user' sobrevivía indefinidamente como opción inválida
    # para cualquier item que dependiera de PED-001.
    with tempfile.TemporaryDirectory() as tmp:
        project_root = Path(tmp)
        (project_root / ".harness" / "interfaces").mkdir(parents=True)

        guard = AgentFileGuard("executor", str(project_root))
        guard.write(Zona.HARNESS_INTERFACES, "PED-001.json", json.dumps({
            "item_id": "PED-001",
            "timestamp": "2026-01-01T00:00:00+00:00",
            "interfaz": {"dependencia_reusable": [
                {"nombre": "hash_password", "import": "app.services.auth_service.hash_password", "firma": "(s: str) -> str"}
            ]},
        }))

        archivo_real = project_root / "backend" / "app" / "services" / "auth_service.py"
        archivo_real.parent.mkdir(parents=True)
        archivo_real.write_text("def hash_password(s: str) -> str:\n    return s\n")

        plan = json.loads(PLAN_EJEMPLO.read_text())
        contexto = construir_contexto(plan, "PED-002", guard)
        reusables = contexto["dependencias"]["PED-001"]["dependencia_reusable"]
        nombres = {r["nombre"] for r in reusables if "nombre" in r}

        assert "get_current_user" not in nombres, (
            "get_current_user es de la predicha pero no existe en el código real, no debe llegar a otro item"
        )
        assert any(r.get("import") == "app.services.auth_service.hash_password" for r in reusables), (
            "la interfaz real sigue llegando normal"
        )
        print("OK: construir_contexto no arrastra una entrada de la predicha cuyo símbolo no existe en el código real")


def test_construir_contexto_con_guard_sin_permiso_de_project_dir_no_revienta():
    # Regresión real (2026-08-27): arbitro.py llama a
    # construir_contexto con su propio guard para armar el mismo contexto
    # que vio Executor -- pero arbitro tiene project_dir:none a propósito
    # (config/permissions.yaml, nunca debería necesitar leer código). La
    # poda opcional (podar_predicha_no_generada) intenta leer el código real
    # de la dependencia para verificarlo, y sin este fix el PermissionError
    # se propagaba crudo, reventando el --loop completo cada vez que arbitro
    # se consultaba para un item con una dependencia ya completada (el caso
    # normal).
    with tempfile.TemporaryDirectory() as tmp:
        project_root = Path(tmp)
        (project_root / ".harness" / "interfaces").mkdir(parents=True)

        guard_executor = AgentFileGuard("executor", str(project_root))
        guard_executor.write(Zona.HARNESS_INTERFACES, "PED-001.json", json.dumps({
            "item_id": "PED-001",
            "timestamp": "2026-01-01T00:00:00+00:00",
            "interfaz": {"dependencia_reusable": [
                {"nombre": "hash_password", "import": "app.services.auth_service.hash_password", "firma": "(s: str) -> str"}
            ]},
        }))

        archivo_real = project_root / "backend" / "app" / "services" / "auth_service.py"
        archivo_real.parent.mkdir(parents=True)
        archivo_real.write_text("def hash_password(s: str) -> str:\n    return s\n")

        plan = json.loads(PLAN_EJEMPLO.read_text())
        guard_arbitro = AgentFileGuard("arbitro", str(project_root))
        # No debe lanzar PermissionError -- antes del fix, esto reventaba.
        contexto = construir_contexto(plan, "PED-002", guard_arbitro)
        reusables = {r["import"] for r in contexto["dependencias"]["PED-001"]["dependencia_reusable"]}

        assert "app.services.auth_service.hash_password" in reusables, (
            "sin permiso para podar, arbitro debe seguir viendo la interfaz predicha+real tal cual"
        )
    print("OK: construir_contexto con un guard sin project_dir:read (ej. arbitro) no revienta, usa la predicha sin podar")


def test_regenerar_catalogo_endpoints_solo_incluye_backend_aprobados():
    with tempfile.TemporaryDirectory() as tmp:
        project_root = Path(tmp)
        harness = project_root / ".harness"
        (harness / "config").mkdir(parents=True)
        (harness / "validation").mkdir()
        shutil.copy(PLAN_EJEMPLO, harness / "config" / "plan.json")

        # solo PED-001 aprobado -> el catálogo debe traer únicamente ese
        # endpoint, no PED-002/PED-003 (pendientes) ni nada más.
        (harness / "validation" / "PED-001.json").write_text(json.dumps({
            "item_id": "PED-001", "veredicto": "aprobado",
            "timestamp": "2026-01-01T00:00:00+00:00",
            "criterios_evaluados": [], "detalle": "",
        }))

        ruta = regenerar_catalogo_endpoints(str(project_root))
        assert ruta == project_root / "docs" / "api-endpoints.md"
        contenido = ruta.read_text()

        assert "## POST /api/v1/auth/login" in contenido
        assert "COAS" not in contenido  # nada de otro proyecto se cuela
        assert "PED-002" not in contenido and "PED-003" not in contenido
        assert "PED-001" in contenido
        print("OK: regenerar_catalogo_endpoints solo incluye backend con veredicto aprobado vigente")


def test_regenerar_catalogo_endpoints_se_reescribe_completo_no_append():
    with tempfile.TemporaryDirectory() as tmp:
        project_root = Path(tmp)
        harness = project_root / ".harness"
        (harness / "config").mkdir(parents=True)
        (harness / "validation").mkdir()
        shutil.copy(PLAN_EJEMPLO, harness / "config" / "plan.json")

        (harness / "validation" / "PED-001.json").write_text(json.dumps({
            "item_id": "PED-001", "veredicto": "aprobado",
            "timestamp": "2026-01-01T00:00:00+00:00",
            "criterios_evaluados": [], "detalle": "",
        }))
        ruta = regenerar_catalogo_endpoints(str(project_root))
        assert ruta.read_text().count("## POST /api/v1/auth/login") == 1

        # se regenera de nuevo sin cambiar nada -> no debe duplicarse el bloque
        regenerar_catalogo_endpoints(str(project_root))
        assert ruta.read_text().count("## POST /api/v1/auth/login") == 1
        print("OK: regenerar_catalogo_endpoints reescribe completo, no hace append")


def test_regenerar_catalogo_endpoints_soporta_interfaz_endpoint_como_lista():
    with tempfile.TemporaryDirectory() as tmp:
        project_root = Path(tmp)
        harness = project_root / ".harness"
        (harness / "config").mkdir(parents=True)
        (harness / "validation").mkdir()
        plan = {
            "metadata": {"proyecto": "test"},
            "decisiones_globales": {},
            "items": [{
                "id": "ITEM-X-001", "tipo": "backend", "descripcion": "x",
                "archivos_destino": ["dal/app/api/v1/x.py"], "detalle_tecnico": "x",
                "criterios_aceptacion": ["x"], "depende_de": [],
                "interfaz": {"endpoint": [
                    {"metodo": "GET", "ruta": "/internal/v1/x", "request": "sin body", "response_2xx": "{}"},
                    {"metodo": "POST", "ruta": "/internal/v1/x", "request": "{}", "response_2xx": "{}"},
                ]},
                "estado": "pendiente",
            }],
            "riesgos_heredados": [],
        }
        (harness / "config" / "plan.json").write_text(json.dumps(plan))
        (harness / "validation" / "ITEM-X-001.json").write_text(json.dumps({
            "item_id": "ITEM-X-001", "veredicto": "aprobado",
            "timestamp": "2026-01-01T00:00:00+00:00",
            "criterios_evaluados": [], "detalle": "",
        }))

        ruta = regenerar_catalogo_endpoints(str(project_root))
        contenido = ruta.read_text()
        assert "## GET /internal/v1/x" in contenido
        assert "## POST /internal/v1/x" in contenido
        print("OK: regenerar_catalogo_endpoints soporta interfaz.endpoint como lista (varios endpoints por item), sin romper")


REPORTE_FALLAS_EJEMPLO = """\
# Reporte de fallas — requieren reparación manual

## ITEM-REP-1 — rechazo 1/2 — 2026-08-21T10:00:00-04:00

Pendiente de decisión antes de reintentar:

```
- NO CUMPLIDO: algo específico de ITEM-REP-1
```

Veredicto completo: `.harness/validation/ITEM-REP-1.json`.

---

## ITEM-REP-10 — 2026-08-21T11:00:00-04:00

Intentos agotados (1). Último motivo:

```
otra falla, de ITEM-REP-10, no debería mezclarse con ITEM-REP-1
```

Veredicto completo: `.harness/validation/ITEM-REP-10.json`.

---
"""


def test_documentador_bloques_de_rechazo_extrae_solo_el_item_pedido():
    bloques_1 = bloques_de_rechazo(REPORTE_FALLAS_EJEMPLO, "ITEM-REP-1")
    assert len(bloques_1) == 1
    assert "ITEM-REP-1" in bloques_1[0]
    assert "ITEM-REP-10" not in bloques_1[0]
    assert "algo específico de ITEM-REP-1" in bloques_1[0]

    bloques_10 = bloques_de_rechazo(REPORTE_FALLAS_EJEMPLO, "ITEM-REP-10")
    assert len(bloques_10) == 1
    assert "de ITEM-REP-10" in bloques_10[0]

    assert bloques_de_rechazo(REPORTE_FALLAS_EJEMPLO, "ITEM-REP-99") == []
    print("OK: bloques_de_rechazo separa por item_id exacto (ITEM-REP-1 no matchea bloques de ITEM-REP-10)")


def test_documentador_construir_contexto_incluye_rechazos_y_codigo_final():
    with tempfile.TemporaryDirectory() as tmp:
        project_root = Path(tmp)
        (project_root / "app").mkdir()
        (project_root / "app" / "core.py").write_text("# version final aprobada\n")
        harness = project_root / ".harness" / "logs"
        harness.mkdir(parents=True)
        (harness / "reporte_fallas.md").write_text(REPORTE_FALLAS_EJEMPLO)

        item = _item_compliance("ITEM-REP-1", archivos_destino=["app/core.py"])
        plan = {"decisiones_globales": {}, "items": [item]}
        guard = AgentFileGuard("documentador", str(project_root))

        contexto = construir_contexto_documentador(plan, "ITEM-REP-1", guard)

        assert len(contexto["bloques_rechazo"]) == 1
        assert "ITEM-REP-10" not in contexto["bloques_rechazo"][0]
        assert contexto["codigo_final_aprobado"]["app/core.py"] == "# version final aprobada\n"
        assert contexto["ticket_reintento"] == ""  # no se creó ningún ticket en este fixture
        print("OK: construir_contexto del documentador junta el rechazo real y el código final aprobado")


def test_documentador_construir_contexto_incluye_ticket_de_reintento_si_existe():
    with tempfile.TemporaryDirectory() as tmp:
        project_root = Path(tmp)
        (project_root / "app").mkdir()
        (project_root / "app" / "core.py").write_text("# version final\n")
        harness = project_root / ".harness" / "logs"
        harness.mkdir(parents=True)

        item = _item_compliance("ITEM-REP-1", archivos_destino=["app/core.py"])
        plan = {"decisiones_globales": {}, "items": [item]}
        _actualizar_ticket_reintento(
            project_root, item, {"criterios_evaluados": [], "detalle": "rompía el otro caso"}, fuente="smoke_test",
        )
        guard = AgentFileGuard("documentador", str(project_root))

        contexto = construir_contexto_documentador(plan, "ITEM-REP-1", guard)
        assert "rompía el otro caso" in contexto["ticket_reintento"]
        assert "fuente: smoke_test" in contexto["ticket_reintento"]
        print("OK: construir_contexto del documentador incluye el ticket de reintento completo cuando existe")


def test_documentador_parsear_respuesta_valida_clasificaciones():
    for clasificacion in ("patron_libreria", "decision_arquitectura"):
        data = parsear_respuesta_documentador(json.dumps({
            "clasificacion": clasificacion,
            "resumen": "resumen breve",
            "candidato_entrada": "## algo\n\ncontenido",
        }))
        assert data["clasificacion"] == clasificacion

    data_bug = parsear_respuesta_documentador(json.dumps({
        "clasificacion": "bug_negocio_proyecto",
        "resumen": "resumen breve",
        "candidato_entrada": None,
    }))
    assert data_bug["clasificacion"] == "bug_negocio_proyecto"
    print("OK: parsear_respuesta del documentador acepta las 3 clasificaciones válidas")


def test_documentador_parsear_respuesta_rechaza_clasificacion_invalida():
    try:
        parsear_respuesta_documentador(json.dumps({
            "clasificacion": "algo_inventado",
            "resumen": "x",
            "candidato_entrada": None,
        }))
        assert False, "debería haber lanzado ValueError"
    except ValueError:
        pass
    print("OK: parsear_respuesta del documentador rechaza una clasificación fuera de las 3 válidas")


def test_documentador_parsear_respuesta_exige_candidato_salvo_bug_de_negocio():
    try:
        parsear_respuesta_documentador(json.dumps({
            "clasificacion": "patron_libreria",
            "resumen": "x",
            "candidato_entrada": None,
        }))
        assert False, "debería haber lanzado ValueError"
    except ValueError:
        pass
    print("OK: parsear_respuesta del documentador exige candidato_entrada salvo para bug_negocio_proyecto")


def test_orchestrator_item_tuvo_rechazos_segun_reporte_fallas():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        harness = root / ".harness" / "logs"
        harness.mkdir(parents=True)
        (harness / "reporte_fallas.md").write_text(REPORTE_FALLAS_EJEMPLO)

        assert _item_tuvo_rechazos(root, "ITEM-REP-1") is True
        assert _item_tuvo_rechazos(root, "ITEM-REP-10") is True
        assert _item_tuvo_rechazos(root, "ITEM-REP-99") is False

    with tempfile.TemporaryDirectory() as tmp_sin_reporte:
        assert _item_tuvo_rechazos(Path(tmp_sin_reporte), "ITEM-REP-1") is False

    print("OK: _item_tuvo_rechazos de orchestrator detecta bloques por item_id exacto, sin reporte_fallas.md da False")


def test_arbitro_parsea_interfaz_incompleta():
    texto = (
        "### INTERFAZ_INCOMPLETA\n"
        '{"item_productor": "ITEM-CORE-001", "simbolo_faltante": "app.core.database.Base", '
        '"explicacion": "ITEM-CORE-001 define database.py pero su interfaz no expone Base"}\n'
        "### END INTERFAZ_INCOMPLETA"
    )
    parseado = _parsear_interfaz_incompleta(texto)
    assert parseado["item_productor"] == "ITEM-CORE-001"
    assert parseado["simbolo_faltante"] == "app.core.database.Base"
    assert "no expone Base" in parseado["explicacion"]
    print("OK: arbitro parsea INTERFAZ_INCOMPLETA con item_productor/simbolo_faltante/explicacion")


def test_arbitro_interfaz_incompleta_json_invalido_da_none():
    assert _parsear_interfaz_incompleta("### INTERFAZ_INCOMPLETA\nesto no es json") is None
    print("OK: arbitro trata INTERFAZ_INCOMPLETA mal formado como None (no_resoluble en resolver_bloqueo)")


def test_arbitro_interfaz_incompleta_sin_campos_requeridos_da_none():
    sin_item_productor = '### INTERFAZ_INCOMPLETA\n{"simbolo_faltante": "x"}\n### END INTERFAZ_INCOMPLETA'
    sin_simbolo = '### INTERFAZ_INCOMPLETA\n{"item_productor": "X"}\n### END INTERFAZ_INCOMPLETA'
    assert _parsear_interfaz_incompleta(sin_item_productor) is None
    assert _parsear_interfaz_incompleta(sin_simbolo) is None
    print("OK: arbitro exige item_productor Y simbolo_faltante, ninguno de los dos es opcional")


def test_documentador_marca_candidato_previo_del_mismo_item_como_supersedido():
    with tempfile.TemporaryDirectory() as tmp:
        project_root = Path(tmp)
        (project_root / ".harness" / "logs").mkdir(parents=True)
        guard = AgentFileGuard("documentador", str(project_root))

        bloque_viejo = _formatear_bloque_salida("ITEM-AUTH-003", {
            "clasificacion": "decision_arquitectura",
            "resumen": "version vieja, ya superada",
            "candidato_entrada": "## algo viejo\ncontenido viejo",
        })
        guard.append_line(Zona.HARNESS_LOGS, "candidatos_conocimiento.md", bloque_viejo)

        _marcar_candidatos_previos_superados(guard, "ITEM-AUTH-003")

        contenido = guard.read(Zona.HARNESS_LOGS, "candidatos_conocimiento.md")
        assert MARCA_SUPERSEDIDO.strip() in contenido
        assert "version vieja, ya superada" in contenido, "no debe borrar el contenido viejo, solo marcarlo"
        print("OK: documentador marca un candidato previo del mismo item como supersedido")


def test_documentador_no_marca_candidatos_de_otro_item_ni_duplica_marca():
    with tempfile.TemporaryDirectory() as tmp:
        project_root = Path(tmp)
        (project_root / ".harness" / "logs").mkdir(parents=True)
        guard = AgentFileGuard("documentador", str(project_root))

        # ITEM-REP2-1 no debe matchear al marcar ITEM-REP2-10 (mismo cuidado que
        # bloques_de_rechazo con IDs que comparten prefijo)
        bloque_otro_item = _formatear_bloque_salida("ITEM-REP2-1", {
            "clasificacion": "decision_arquitectura",
            "resumen": "de otro item, no debe tocarse",
            "candidato_entrada": "## x\ny",
        })
        guard.append_line(Zona.HARNESS_LOGS, "candidatos_conocimiento.md", bloque_otro_item)

        _marcar_candidatos_previos_superados(guard, "ITEM-REP2-10")
        contenido = guard.read(Zona.HARNESS_LOGS, "candidatos_conocimiento.md")
        assert MARCA_SUPERSEDIDO.strip() not in contenido, "ITEM-REP2-1 no es ITEM-REP2-10, no debía marcarse"

        # ahora agregamos un candidato real de ITEM-REP2-10 y marcamos dos veces seguidas
        bloque_viejo_10 = _formatear_bloque_salida("ITEM-REP2-10", {
            "clasificacion": "patron_libreria",
            "resumen": "primera version de ITEM-REP2-10",
            "candidato_entrada": "## x\ny",
        })
        guard.append_line(Zona.HARNESS_LOGS, "candidatos_conocimiento.md", bloque_viejo_10)

        _marcar_candidatos_previos_superados(guard, "ITEM-REP2-10")
        _marcar_candidatos_previos_superados(guard, "ITEM-REP2-10")  # llamar 2 veces no debe duplicar la marca
        contenido = guard.read(Zona.HARNESS_LOGS, "candidatos_conocimiento.md")
        assert contenido.count(MARCA_SUPERSEDIDO.strip()) == 1, "la marca no debe duplicarse en llamados repetidos (idempotente)"
        print("OK: documentador no confunde IDs con prefijo compartido y no duplica la marca al llamarse dos veces")


def test_documentador_resolucion_completa_marca_supersedidos_de_verdad():
    # Regresión de punta a punta: dos aprobaciones reales del mismo item en
    # la misma sesión -- la primera queda marcada como supersedida cuando
    # llega la segunda.
    with tempfile.TemporaryDirectory() as tmp:
        project_root = Path(tmp)
        harness = project_root / ".harness"
        (harness / "config").mkdir(parents=True)
        (harness / "logs").mkdir()
        (project_root / "backend").mkdir()
        (project_root / "backend" / "archivo.py").write_text("x = 1\n")

        plan = {
            "metadata": {}, "decisiones_globales": {},
            "items": [{
                "id": "ITEM-X-002", "tipo": "backend", "descripcion": "d",
                "detalle_tecnico": "dt", "archivos_destino": ["backend/archivo.py"],
                "criterios_aceptacion": ["c"], "depende_de": [], "interfaz": {},
                "estado": "pendiente",
            }],
            "riesgos_heredados": [],
        }
        (harness / "config" / "plan.json").write_text(json.dumps(plan))
        (harness / "logs" / "reporte_fallas.md").write_text(
            "## ITEM-X-002 — rechazo 1/2 — 2026-01-01T00:00:00+00:00\n\nmotivo viejo\n\n---\n\n"
        )

        import agents.documentador as documentador_mod

        class RespuestaFalsa:
            def __init__(self, content):
                self.content = content

        respuestas = [
            json.dumps({"clasificacion": "decision_arquitectura", "resumen": "primera vez", "candidato_entrada": "## a\nb"}),
            json.dumps({"clasificacion": "decision_arquitectura", "resumen": "segunda vez, reemplaza la anterior", "candidato_entrada": "## c\nd"}),
        ]

        class MotorFalso:
            def run(self, *a, **k):
                return RespuestaFalsa(respuestas.pop(0))

        original = documentador_mod.get_engine_for_agent
        documentador_mod.get_engine_for_agent = lambda agente: MotorFalso()
        try:
            r1 = documentador_mod.documentar_resolucion(str(project_root), "ITEM-X-002")
            assert r1["estado"] == "documentado"
            r2 = documentador_mod.documentar_resolucion(str(project_root), "ITEM-X-002")
            assert r2["estado"] == "documentado"
        finally:
            documentador_mod.get_engine_for_agent = original

        contenido = (harness / "logs" / "candidatos_conocimiento.md").read_text()
        assert contenido.count(MARCA_SUPERSEDIDO.strip()) == 1, "solo el primer candidato debe quedar marcado, no el segundo (el más nuevo)"
        assert "primera vez" in contenido and "segunda vez" in contenido
        # la marca va ANTES del resumen (se ve primero al leer el bloque viejo),
        # y el bloque de "primera vez" entero precede al de "segunda vez"
        idx_marca = contenido.index(MARCA_SUPERSEDIDO.strip())
        idx_primera = contenido.index("primera vez")
        idx_segunda = contenido.index("segunda vez")
        assert idx_marca < idx_primera < idx_segunda
        print("OK: documentar_resolucion marca de punta a punta el candidato viejo cuando el mismo item se documenta dos veces")


def test_arbitro_falta_dependencia_sigue_funcionando():
    # regresión: agregar INTERFAZ_INCOMPLETA no debe romper el parser existente
    texto = (
        "### FALTA_DEPENDENCIA\n"
        '{"items_faltantes": ["ITEM-CORE-001"], "explicacion": "define Base"}\n'
        "### END FALTA_DEPENDENCIA"
    )
    parseado = _parsear_falta_dependencia(texto)
    assert parseado["items_faltantes"] == ["ITEM-CORE-001"]
    print("OK: _parsear_falta_dependencia sigue funcionando tras agregar INTERFAZ_INCOMPLETA")


# --- fallback a Kimi cuando el motor local está inalcanzable (2026-08-27) ---

def test_lm_studio_connection_error_da_motor_inalcanzable():
    engine = LMStudioEngine(model="x", base_url="http://127.0.0.1:1", timeout_seconds=1)

    def _post_falso(*a, **k):
        raise requests.exceptions.ConnectionError("Connection refused")

    original = requests.post
    requests.post = _post_falso
    try:
        try:
            engine.run("sys", "user")
            assert False, "debería haber lanzado MotorInalcanzable"
        except MotorInalcanzable:
            pass
    finally:
        requests.post = original
    print("OK: LMStudioEngine convierte ConnectionError en MotorInalcanzable, distinto de TimeoutDelMotor")


def _respuesta_http_falsa(status_code, texto):
    class _RespuestaFalsa:
        def __init__(self):
            self.status_code = status_code
            self.text = texto

        def raise_for_status(self):
            raise requests.exceptions.HTTPError(f"{self.status_code} Client Error", response=self)

    return _RespuestaFalsa()


def test_lm_studio_http_error_da_runtimeerror_no_traceback_crudo():
    # Regresión real (2026-08-30, encontrado probando --senior en vivo): un
    # HTTPError (ej. 400 "Failed to load model...SIGSEGV", servidor arriba,
    # respondió con un error real) se propagaba crudo -- revienta
    # orchestrator.py entero con traceback en vez de tratarse como un
    # intento fallido normal (mismo camino que TimeoutDelMotor/RuntimeError,
    # que ejecutar_item ya sabe manejar). No es MotorInalcanzable -- el
    # servidor SÍ respondió.
    engine = LMStudioEngine(model="x", base_url="http://127.0.0.1:1", timeout_seconds=1)

    def _post_falso(*a, **k):
        return _respuesta_http_falsa(400, "Failed to load model...SIGSEGV")

    original = requests.post
    requests.post = _post_falso
    try:
        try:
            engine.run("sys", "user")
            assert False, "debería haber lanzado RuntimeError"
        except MotorInalcanzable:
            assert False, "un HTTPError (el servidor respondió) no es MotorInalcanzable"
        except RuntimeError as e:
            assert "400" in str(e) and "SIGSEGV" in str(e)
    finally:
        requests.post = original
    print("OK: LMStudioEngine convierte HTTPError en RuntimeError con el detalle real, no un traceback crudo")


def test_lm_studio_connect_timeout_da_motor_inalcanzable_no_timeout_del_motor():
    # Regresión: requests.exceptions.ConnectTimeout hereda de Timeout Y de
    # ConnectionError -- si el except de Timeout va antes que el de
    # ConnectionError, este caso (host inalcanzable que tarda en fallar --
    # IP vieja, VPN, firewall dropeando paquetes) se malinterpreta como "se
    # quedó pensando" en vez de "no se pudo conectar", y el fallback a Kimi
    # nunca se dispara.
    engine = LMStudioEngine(model="x", base_url="http://127.0.0.1:1", timeout_seconds=1)

    def _post_falso(*a, **k):
        raise requests.exceptions.ConnectTimeout("Connection timed out")

    original = requests.post
    requests.post = _post_falso
    try:
        try:
            engine.run("sys", "user")
            assert False, "debería haber lanzado MotorInalcanzable"
        except TimeoutDelMotor:
            assert False, "ConnectTimeout es una falla de conexión, no debe tratarse como TimeoutDelMotor"
        except MotorInalcanzable:
            pass
    finally:
        requests.post = original
    print("OK: ConnectTimeout (host inalcanzable) da MotorInalcanzable, no TimeoutDelMotor")


def _con_api_key_dummy(nombre_var, valor, fn):
    previo = os.environ.get(nombre_var)
    os.environ[nombre_var] = valor
    try:
        return fn()
    finally:
        if previo is None:
            os.environ.pop(nombre_var, None)
        else:
            os.environ[nombre_var] = previo


def test_kimi_read_timeout_da_timeout_del_motor_no_crashea():
    # Regresión real (2026-08-27): un ReadTimeout real
    # de la API de Kimi (un item grande) se propagaba crudo
    # -- ni MotorInalcanzable ni TimeoutDelMotor lo capturaban -- y reventaba
    # el proceso completo de --loop. kimi_api.py no envolvía Timeout en
    # absoluto (mismo estilo que deepseek_api.py, nunca portado el fix de
    # lm_studio.py).
    def _correr():
        engine = KimiEngine(model="x", timeout_seconds=1)

        def _post_falso(*a, **k):
            raise requests.exceptions.ReadTimeout("Read timed out")

        original = requests.post
        requests.post = _post_falso
        try:
            try:
                engine.run("sys", "user")
                assert False, "debería haber lanzado TimeoutDelMotor"
            except TimeoutDelMotor:
                pass
        finally:
            requests.post = original

    _con_api_key_dummy("KIMI_API_KEY", "dummy", _correr)
    print("OK: KimiEngine convierte ReadTimeout en TimeoutDelMotor, no lo deja propagar crudo")


def test_kimi_connection_error_da_motor_inalcanzable():
    def _correr():
        engine = KimiEngine(model="x", timeout_seconds=1)

        def _post_falso(*a, **k):
            raise requests.exceptions.ConnectionError("Connection refused")

        original = requests.post
        requests.post = _post_falso
        try:
            try:
                engine.run("sys", "user")
                assert False, "debería haber lanzado MotorInalcanzable"
            except MotorInalcanzable:
                pass
        finally:
            requests.post = original

    _con_api_key_dummy("KIMI_API_KEY", "dummy", _correr)
    print("OK: KimiEngine convierte ConnectionError en MotorInalcanzable")


def test_kimi_http_error_da_runtimeerror_no_traceback_crudo():
    def _correr():
        engine = KimiEngine(model="x", timeout_seconds=1)

        def _post_falso(*a, **k):
            return _respuesta_http_falsa(400, "invalid temperature")

        original = requests.post
        requests.post = _post_falso
        try:
            try:
                engine.run("sys", "user")
                assert False, "debería haber lanzado RuntimeError"
            except MotorInalcanzable:
                assert False, "un HTTPError (el servidor respondió) no es MotorInalcanzable"
            except RuntimeError as e:
                assert "400" in str(e)
        finally:
            requests.post = original

    _con_api_key_dummy("KIMI_API_KEY", "dummy", _correr)
    print("OK: KimiEngine convierte HTTPError en RuntimeError con el detalle real, no un traceback crudo")


def test_deepseek_read_timeout_da_timeout_del_motor_no_crashea():
    # Mismo bug real que Kimi, mismo fix -- deepseek_api.py tenía el mismo
    # hueco (usado por Compliance/executor_senior/arbitro).
    def _correr():
        engine = DeepSeekEngine(model="x", timeout_seconds=1)

        def _post_falso(*a, **k):
            raise requests.exceptions.ReadTimeout("Read timed out")

        original = requests.post
        requests.post = _post_falso
        try:
            try:
                engine.run("sys", "user")
                assert False, "debería haber lanzado TimeoutDelMotor"
            except TimeoutDelMotor:
                pass
        finally:
            requests.post = original

    _con_api_key_dummy("DEEPSEEK_API_KEY", "dummy", _correr)
    print("OK: DeepSeekEngine convierte ReadTimeout en TimeoutDelMotor, no lo deja propagar crudo")


def test_deepseek_connection_error_da_motor_inalcanzable():
    def _correr():
        engine = DeepSeekEngine(model="x", timeout_seconds=1)

        def _post_falso(*a, **k):
            raise requests.exceptions.ConnectionError("Connection refused")

        original = requests.post
        requests.post = _post_falso
        try:
            try:
                engine.run("sys", "user")
                assert False, "debería haber lanzado MotorInalcanzable"
            except MotorInalcanzable:
                pass
        finally:
            requests.post = original

    _con_api_key_dummy("DEEPSEEK_API_KEY", "dummy", _correr)
    print("OK: DeepSeekEngine convierte ConnectionError en MotorInalcanzable")


def test_deepseek_http_error_da_runtimeerror_no_traceback_crudo():
    # Regresión real (2026-08-30, encontrado probando --senior en vivo
    # contra la API real de DeepSeek -- un 400 transitorio).
    def _correr():
        engine = DeepSeekEngine(model="x", timeout_seconds=1)

        def _post_falso(*a, **k):
            return _respuesta_http_falsa(400, "bad request")

        original = requests.post
        requests.post = _post_falso
        try:
            try:
                engine.run("sys", "user")
                assert False, "debería haber lanzado RuntimeError"
            except MotorInalcanzable:
                assert False, "un HTTPError (el servidor respondió) no es MotorInalcanzable"
            except RuntimeError as e:
                assert "400" in str(e)
        finally:
            requests.post = original

    _con_api_key_dummy("DEEPSEEK_API_KEY", "dummy", _correr)
    print("OK: DeepSeekEngine convierte HTTPError en RuntimeError con el detalle real, no un traceback crudo")


def test_factory_override_hace_que_get_engine_for_agent_use_el_motor_alternativo():
    with tempfile.TemporaryDirectory() as tmp:
        config_path = Path(tmp) / "models.yaml"
        config_path.write_text(
            "agents:\n"
            "  executor:\n"
            "    engine: lm_studio\n"
            "    model: qwen/algo\n"
            "    timeout_seconds: 10\n"
            "engines:\n"
            "  lm_studio:\n"
            "    base_url: http://localhost:1234/v1\n"
        )
        original_config_path = engine_factory_mod._CONFIG_PATH
        engine_factory_mod._CONFIG_PATH = config_path
        valor_previo = os.environ.get("KIMI_API_KEY")
        os.environ["KIMI_API_KEY"] = "dummy-para-test"
        try:
            engine_sin_override = get_engine_for_agent("executor")
            assert isinstance(engine_sin_override, LMStudioEngine)

            set_override("executor", "kimi", "kimi-k2-0905-preview")
            assert get_override("executor") == {"engine": "kimi", "model": "kimi-k2-0905-preview"}

            engine_con_override = get_engine_for_agent("executor")
            assert isinstance(engine_con_override, KimiEngine)
            assert engine_con_override.model == "kimi-k2-0905-preview"

            clear_override("executor")
            assert get_override("executor") is None
            assert isinstance(get_engine_for_agent("executor"), LMStudioEngine)
        finally:
            engine_factory_mod._CONFIG_PATH = original_config_path
            if valor_previo is None:
                os.environ.pop("KIMI_API_KEY", None)
            else:
                os.environ["KIMI_API_KEY"] = valor_previo
            clear_override()
    print("OK: engines.factory.set_override hace que get_engine_for_agent devuelva el motor alternativo, sin tocar el yaml")


def test_executor_propaga_motor_inalcanzable_en_vez_de_tragarselo():
    with tempfile.TemporaryDirectory() as tmp:
        project_root = Path(tmp)
        harness = project_root / ".harness"
        (harness / "config").mkdir(parents=True)
        (harness / "logs").mkdir()

        plan = {
            "metadata": {}, "decisiones_globales": {},
            "items": [{
                "id": "ITEM-X-002", "tipo": "backend", "descripcion": "d",
                "detalle_tecnico": "dt", "archivos_destino": ["backend/archivo.py"],
                "criterios_aceptacion": ["c"], "depende_de": [], "interfaz": {},
                "estado": "pendiente",
            }],
            "riesgos_heredados": [],
        }
        (harness / "config" / "plan.json").write_text(json.dumps(plan))

        import agents.executor as executor_mod

        class MotorFalso:
            def run(self, *a, **k):
                raise MotorInalcanzable("LM Studio caído")

        original = executor_mod.get_engine_for_agent
        executor_mod.get_engine_for_agent = lambda agente: MotorFalso()
        try:
            try:
                executor_mod.ejecutar_item(str(project_root), "ITEM-X-002")
                assert False, "ejecutar_item debería propagar MotorInalcanzable, no tragárselo"
            except MotorInalcanzable:
                pass
        finally:
            executor_mod.get_engine_for_agent = original
    print("OK: ejecutar_item deja pasar MotorInalcanzable sin tratarlo como 'bloqueado'")


def test_documentador_propaga_motor_inalcanzable_en_vez_de_tragarselo():
    with tempfile.TemporaryDirectory() as tmp:
        project_root = Path(tmp)
        harness = project_root / ".harness"
        (harness / "config").mkdir(parents=True)
        (harness / "logs").mkdir()
        (project_root / "backend").mkdir()
        (project_root / "backend" / "archivo.py").write_text("x = 1\n")

        plan = {
            "metadata": {}, "decisiones_globales": {},
            "items": [{
                "id": "ITEM-X-002", "tipo": "backend", "descripcion": "d",
                "detalle_tecnico": "dt", "archivos_destino": ["backend/archivo.py"],
                "criterios_aceptacion": ["c"], "depende_de": [], "interfaz": {},
                "estado": "pendiente",
            }],
            "riesgos_heredados": [],
        }
        (harness / "config" / "plan.json").write_text(json.dumps(plan))
        (harness / "logs" / "reporte_fallas.md").write_text(
            "## ITEM-X-002 — rechazo 1/2 — 2026-01-01T00:00:00+00:00\n\nmotivo viejo\n\n---\n\n"
        )

        import agents.documentador as documentador_mod

        class MotorFalso:
            def run(self, *a, **k):
                raise MotorInalcanzable("LM Studio caído")

        original = documentador_mod.get_engine_for_agent
        documentador_mod.get_engine_for_agent = lambda agente: MotorFalso()
        try:
            try:
                documentador_mod.documentar_resolucion(str(project_root), "ITEM-X-002")
                assert False, "documentar_resolucion debería propagar MotorInalcanzable, no tragárselo"
            except MotorInalcanzable:
                pass
        finally:
            documentador_mod.get_engine_for_agent = original
    print("OK: documentar_resolucion deja pasar MotorInalcanzable sin tratarlo como 'error' silencioso")


def test_con_fallback_motor_local_activa_kimi_si_el_usuario_acepta():
    with tempfile.TemporaryDirectory() as tmp:
        project_root = Path(tmp)
        llamadas = {"n": 0}

        def fn():
            llamadas["n"] += 1
            if llamadas["n"] == 1:
                raise MotorInalcanzable("LM Studio caído")
            return {"estado": "finalizado", "archivos": ["x.py"]}

        original = orchestrator_mod._preguntar_activar_kimi
        orchestrator_mod._preguntar_activar_kimi = lambda agente, modelo, detalle: True
        try:
            resultado = _con_fallback_motor_local(project_root, "executor", True, fn)
        finally:
            orchestrator_mod._preguntar_activar_kimi = original
            clear_override("executor")

        assert resultado == {"estado": "finalizado", "archivos": ["x.py"]}
        assert llamadas["n"] == 2, "debe reintentar fn() una vez tras activar el fallback"

        lineas = (project_root / ".harness" / "logs" / "decisiones_motor.jsonl").read_text().strip().splitlines()
        assert len(lineas) == 1
        entrada = json.loads(lineas[0])
        assert entrada["agente"] == "executor"
        assert entrada["decision"] == "activar_kimi"
    print("OK: _con_fallback_motor_local activa Kimi y reintenta cuando el usuario acepta")


def test_con_fallback_motor_local_kimi_tambien_inalcanzable_no_propaga_excepcion_cruda():
    # Regresión: si el usuario acepta activar Kimi pero Kimi TAMBIÉN está
    # inalcanzable en este mismo intento (ej. la máquina está sin red por
    # completo, no solo sin acceso al motor local), el reintento no debe
    # propagar MotorInalcanzable cruda -- rompería el contrato de "siempre
    # devuelve un dict" que loop()/main() dan por sentado.
    with tempfile.TemporaryDirectory() as tmp:
        project_root = Path(tmp)

        def fn():
            raise MotorInalcanzable("nada responde, sin red")

        original = orchestrator_mod._preguntar_activar_kimi
        orchestrator_mod._preguntar_activar_kimi = lambda agente, modelo, detalle: True
        try:
            resultado = _con_fallback_motor_local(project_root, "executor", True, fn)
        finally:
            orchestrator_mod._preguntar_activar_kimi = original
            clear_override("executor")

        assert resultado["estado"] == "motor_inalcanzable"
        assert "kimi" in resultado["motivo"].lower() or "Kimi" in resultado["motivo"]
    print("OK: _con_fallback_motor_local no propaga una excepción cruda si Kimi también está inalcanzable en el reintento")


def test_con_fallback_motor_local_usa_el_modelo_kimi_correcto_por_agente():
    with tempfile.TemporaryDirectory() as tmp:
        project_root = Path(tmp)

        def _fn_falla_una_vez():
            llamadas = {"n": 0}

            def fn():
                llamadas["n"] += 1
                if llamadas["n"] == 1:
                    raise MotorInalcanzable("motor caído")
                return "ok"
            return fn

        original = orchestrator_mod._preguntar_activar_kimi
        orchestrator_mod._preguntar_activar_kimi = lambda agente, modelo, detalle: True
        try:
            _con_fallback_motor_local(project_root, "executor", True, _fn_falla_una_vez())
            assert get_override("executor") == {"engine": "kimi", "model": KIMI_MODEL_FALLBACK["executor"]}

            _con_fallback_motor_local(project_root, "documentador", True, _fn_falla_una_vez())
            assert get_override("documentador") == {"engine": "kimi", "model": KIMI_MODEL_FALLBACK["documentador"]}

            assert KIMI_MODEL_FALLBACK["executor"] != KIMI_MODEL_FALLBACK["documentador"], (
                "executor y documentador deben usar modelos Kimi distintos -- "
                "executor genera código real (variante -code), documentador solo clasifica/resume texto"
            )
        finally:
            orchestrator_mod._preguntar_activar_kimi = original
            clear_override("executor")
            clear_override("documentador")
    print("OK: _con_fallback_motor_local activa el modelo Kimi correcto para cada agente (executor != documentador)")


def test_con_fallback_motor_local_corta_sin_preguntar_si_el_agente_no_tiene_modelo_kimi_mapeado():
    with tempfile.TemporaryDirectory() as tmp:
        project_root = Path(tmp)

        def fn():
            raise MotorInalcanzable("motor caído")

        def _preguntar_falla(*a, **k):
            raise AssertionError("no debería preguntar si el agente no tiene modelo Kimi mapeado")

        original = orchestrator_mod._preguntar_activar_kimi
        orchestrator_mod._preguntar_activar_kimi = _preguntar_falla
        try:
            resultado = _con_fallback_motor_local(project_root, "arbitro", True, fn)
        finally:
            orchestrator_mod._preguntar_activar_kimi = original
            clear_override("arbitro")

        assert resultado["estado"] == "motor_inalcanzable"
        assert "arbitro" in resultado["motivo"]
    print("OK: _con_fallback_motor_local corta directo (sin preguntar) para un agente sin modelo Kimi mapeado")


def test_con_fallback_motor_local_usuario_rechaza():
    with tempfile.TemporaryDirectory() as tmp:
        project_root = Path(tmp)

        def fn():
            raise MotorInalcanzable("LM Studio caído")

        original = orchestrator_mod._preguntar_activar_kimi
        orchestrator_mod._preguntar_activar_kimi = lambda agente, modelo, detalle: False
        try:
            resultado = _con_fallback_motor_local(project_root, "executor", True, fn)
        finally:
            orchestrator_mod._preguntar_activar_kimi = original
            clear_override("executor")

        assert resultado["estado"] == "motor_inalcanzable"
        assert get_override("executor") is None, "no debe activarse override si el usuario rechaza"

        lineas = (project_root / ".harness" / "logs" / "decisiones_motor.jsonl").read_text().strip().splitlines()
        entrada = json.loads(lineas[0])
        assert entrada["decision"] == "no_activar_kimi"
    print("OK: _con_fallback_motor_local no activa Kimi ni reintenta si el usuario rechaza")


def test_con_fallback_motor_local_sin_confirmar_no_pregunta():
    with tempfile.TemporaryDirectory() as tmp:
        project_root = Path(tmp)

        def fn():
            raise MotorInalcanzable("LM Studio caído")

        def _preguntar_falla(*a, **k):
            raise AssertionError("no debería preguntar con confirmar=False")

        original = orchestrator_mod._preguntar_activar_kimi
        orchestrator_mod._preguntar_activar_kimi = _preguntar_falla
        try:
            resultado = _con_fallback_motor_local(project_root, "executor", False, fn)
        finally:
            orchestrator_mod._preguntar_activar_kimi = original
            clear_override("executor")

        assert resultado["estado"] == "motor_inalcanzable"
        assert "confirmar" in resultado["motivo"].lower()
    print("OK: _con_fallback_motor_local con confirmar=False corta sin preguntar")


def test_con_fallback_motor_local_no_repregunta_si_el_override_ya_esta_activo():
    with tempfile.TemporaryDirectory() as tmp:
        project_root = Path(tmp)
        set_override("executor", "kimi", KIMI_MODEL_FALLBACK["executor"])

        def fn():
            raise MotorInalcanzable("Kimi también inalcanzable")

        def _preguntar_falla(*a, **k):
            raise AssertionError("no debería volver a preguntar si el override ya está activo")

        original = orchestrator_mod._preguntar_activar_kimi
        orchestrator_mod._preguntar_activar_kimi = _preguntar_falla
        try:
            resultado = _con_fallback_motor_local(project_root, "executor", True, fn)
        finally:
            orchestrator_mod._preguntar_activar_kimi = original
            clear_override("executor")

        assert resultado["estado"] == "motor_inalcanzable"
    print("OK: _con_fallback_motor_local no vuelve a preguntar si el override ya estaba activo y falla igual")


def test_documentar_si_corresponde_no_tira_el_pipeline_si_el_motor_esta_inalcanzable():
    with tempfile.TemporaryDirectory() as tmp:
        project_root = Path(tmp)
        (project_root / ".harness" / "logs").mkdir(parents=True)

        def _documentar_falso(project_root_arg, item_id):
            raise MotorInalcanzable("LM Studio caído")

        original = orchestrator_mod.documentar_resolucion
        orchestrator_mod.documentar_resolucion = _documentar_falso
        try:
            # no debe lanzar, ni con confirmar=False (nadie puede responder la pregunta)
            _documentar_si_corresponde(str(project_root), "ITEM-X-002", confirmar=False)
        finally:
            orchestrator_mod.documentar_resolucion = original

        lineas = (project_root / ".harness" / "logs" / "decisiones_motor.jsonl").read_text().strip().splitlines()
        entrada = json.loads(lineas[0])
        assert entrada["agente"] == "documentador"
        assert entrada["decision"] == "no_activar_kimi"
    print("OK: _documentar_si_corresponde nunca tira el pipeline si el motor local está inalcanzable")


def test_calcular_metricas_agentes_vacio_si_no_hay_archivo():
    with tempfile.TemporaryDirectory() as tmp:
        assert calcular_metricas_agentes(tmp) == {}
    print("OK: calcular_metricas_agentes devuelve vacío si metricas_agentes.jsonl no existe todavía")


def test_registrar_metrica_agente_y_calcular_metricas_agentes():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _registrar_metrica_agente(root, "PED-001", "executor")
        _registrar_metrica_agente(root, "PED-001", "executor")
        _registrar_metrica_agente(root, "PED-001", "compliance")
        _registrar_metrica_agente(root, "PED-002", "executor")

        metricas = calcular_metricas_agentes(str(root))
        assert metricas["PED-001"] == {"executor": 2, "compliance": 1}
        assert metricas["PED-002"] == {"executor": 1}
        assert "arbitro" not in metricas["PED-001"]  # nunca se consultó, no aparece en 0
    print("OK: _registrar_metrica_agente acumula y calcular_metricas_agentes cuenta bien por item y por agente")


def test_tabla_metricas_agentes_incluye_fila_total():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _registrar_metrica_agente(root, "PED-001", "executor")
        _registrar_metrica_agente(root, "PED-001", "executor_senior")
        _registrar_metrica_agente(root, "PED-002", "compliance")

        tabla = _tabla_metricas_agentes(str(root))
        assert "PED-001" in tabla and "PED-002" in tabla
        assert "TOTAL" in tabla
        # 1 executor + 1 executor_senior + 1 compliance registrados en total
        assert tabla.count("TOTAL") == 1
    print("OK: _tabla_metricas_agentes arma la tabla con fila TOTAL")


def test_tabla_metricas_agentes_vacia_si_no_hay_datos():
    with tempfile.TemporaryDirectory() as tmp:
        assert _tabla_metricas_agentes(tmp) == ""
    print("OK: _tabla_metricas_agentes devuelve string vacío si no hay métricas todavía")


def test_calcular_metricas_agentes_desde_linea_acota_a_la_sesion_actual():
    # "sesión" en las palabras de Felipe: desde que arranca una corrida de
    # --loop hasta que termina -- no el acumulado histórico de todas las
    # corridas anteriores del mismo proyecto. loop() usa desde_linea para
    # esto (cuenta líneas antes de correr, muestra solo lo agregado después).
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _registrar_metrica_agente(root, "PED-001", "executor")  # sesión anterior
        _registrar_metrica_agente(root, "PED-001", "compliance")  # sesión anterior

        lineas_antes = len((root / ".harness" / "logs" / "metricas_agentes.jsonl").read_text().splitlines())

        _registrar_metrica_agente(root, "PED-002", "executor")  # sesión actual

        historico = calcular_metricas_agentes(str(root))
        solo_esta_sesion = calcular_metricas_agentes(str(root), desde_linea=lineas_antes)

        assert historico == {"PED-001": {"executor": 1, "compliance": 1}, "PED-002": {"executor": 1}}
        assert solo_esta_sesion == {"PED-002": {"executor": 1}}
    print("OK: calcular_metricas_agentes(desde_linea=...) acota a lo agregado en la sesión actual, no al histórico completo")


if __name__ == "__main__":
    test_construir_contexto_solo_expone_interfaz_de_dependencias()
    test_parsear_respuesta_archivos()
    test_parsear_respuesta_bloqueado()
    test_parsear_respuesta_malformada()
    test_compliance_parsear_respuesta_valida()
    test_compliance_parsear_respuesta_tolera_code_fences()
    test_compliance_parsear_respuesta_veredicto_invalido()
    test_orchestrator_selecciona_en_orden_y_respeta_dependencias()
    test_feedback_reintento_solo_incluye_criterios_no_cumplidos()
    test_loop_reintenta_rechazado_y_luego_agota()
    test_reporte_rechazo_y_registro_de_decision_quedan_en_disco()
    test_actualizar_ticket_reintento_crea_y_acumula_historial()
    test_actualizar_ticket_reintento_no_toca_hechos_verificados()
    test_actualizar_ticket_reintento_sobreescribe_codigo_actual()
    test_documentador_construir_contexto_incluye_ticket_de_reintento_si_existe()
    test_format_check_detecta_import_roto_y_nombre_pisado()
    test_format_check_detecta_nombre_importado_que_no_existe_en_el_modulo()
    test_format_check_detecta_import_no_usado()
    test_format_check_no_marca_falsos_positivos()
    test_format_check_resuelve_import_desde_archivo_hermano_de_app()
    test_validar_con_format_check_rechaza_sin_llamar_a_compliance()
    test_dependientes_transitivos_del_fixture_pedidos()
    test_invalidar_dependientes_borra_veredictos_de_lo_que_depende_y_no_de_lo_demas()
    test_validar_plan_no_encuentra_nada_en_el_fixture_real()
    test_validar_plan_detecta_id_duplicado()
    test_validar_plan_detecta_dependencia_inexistente()
    test_validar_plan_detecta_dependencia_circular()
    test_validar_plan_detecta_archivos_destino_vacio()
    test_validar_plan_detecta_criterios_aceptacion_vacio()
    test_validar_plan_detecta_interfaz_vacia_en_dependencia_citada()
    test_validar_plan_detecta_archivo_con_mas_de_un_dueno()
    test_parsear_respuesta_con_bloque_interfaz()
    test_parsear_respuesta_interfaz_mal_formada_no_bloquea_el_item()
    test_combinar_interfaz_sin_real_devuelve_predicha_tal_cual()
    test_combinar_interfaz_real_gana_en_conflicto_y_preserva_lo_demas()
    test_combinar_interfaz_normaliza_forma_singular_legacy()
    test_combinar_interfaz_normaliza_forma_por_nombre_sin_perder_entradas()
    test_podar_predicha_no_generada_descarta_simbolo_inexistente()
    test_podar_predicha_no_generada_sin_codigo_no_toca_nada()
    test_construir_contexto_incluye_interfaz_real_de_una_dependencia()
    test_construir_contexto_no_arrastra_predicha_de_simbolo_inexistente()
    test_construir_contexto_con_guard_sin_permiso_de_project_dir_no_revienta()
    test_calcular_veredicto_aprobado_si_todos_cumplidos()
    test_calcular_veredicto_rechazado_si_falta_uno()
    test_calcular_veredicto_ignora_lo_que_el_modelo_haya_escrito()
    test_compliance_construir_contexto_incluye_infraestructura_compartida_siempre()
    test_compliance_construir_contexto_incluye_detalle_tecnico_del_item()
    test_compliance_construir_contexto_incluye_chequeos_previos_segun_tipo()
    test_compliance_construir_contexto_detalle_tecnico_ausente_no_rompe()
    test_compliance_construir_contexto_no_duplica_el_item_propio_como_infraestructura()
    test_compliance_construir_contexto_incluye_archivos_reales_de_dependencias()
    test_compliance_construir_contexto_arbol_archivos_lista_todo_el_proyecto()
    test_smoke_test_sin_tests_requeridos()
    test_smoke_test_pasa()
    test_smoke_test_falla()
    test_smoke_test_venv_no_encontrado()
    test_smoke_test_carpeta_deployable_no_asume_siempre_backend()
    test_smoke_test_usa_el_venv_de_la_carpeta_del_item()
    test_validar_con_format_check_rechaza_por_smoke_test_sin_llamar_a_compliance()
    test_regenerar_catalogo_endpoints_solo_incluye_backend_aprobados()
    test_regenerar_catalogo_endpoints_se_reescribe_completo_no_append()
    test_regenerar_catalogo_endpoints_soporta_interfaz_endpoint_como_lista()
    test_documentador_bloques_de_rechazo_extrae_solo_el_item_pedido()
    test_documentador_construir_contexto_incluye_rechazos_y_codigo_final()
    test_documentador_parsear_respuesta_valida_clasificaciones()
    test_documentador_parsear_respuesta_rechaza_clasificacion_invalida()
    test_documentador_parsear_respuesta_exige_candidato_salvo_bug_de_negocio()
    test_orchestrator_item_tuvo_rechazos_segun_reporte_fallas()
    test_arbitro_parsea_interfaz_incompleta()
    test_arbitro_interfaz_incompleta_json_invalido_da_none()
    test_arbitro_interfaz_incompleta_sin_campos_requeridos_da_none()
    test_documentador_marca_candidato_previo_del_mismo_item_como_supersedido()
    test_documentador_no_marca_candidatos_de_otro_item_ni_duplica_marca()
    test_documentador_resolucion_completa_marca_supersedidos_de_verdad()
    test_arbitro_falta_dependencia_sigue_funcionando()
    test_lm_studio_connection_error_da_motor_inalcanzable()
    test_lm_studio_http_error_da_runtimeerror_no_traceback_crudo()
    test_lm_studio_connect_timeout_da_motor_inalcanzable_no_timeout_del_motor()
    test_kimi_read_timeout_da_timeout_del_motor_no_crashea()
    test_kimi_connection_error_da_motor_inalcanzable()
    test_kimi_http_error_da_runtimeerror_no_traceback_crudo()
    test_deepseek_read_timeout_da_timeout_del_motor_no_crashea()
    test_deepseek_connection_error_da_motor_inalcanzable()
    test_deepseek_http_error_da_runtimeerror_no_traceback_crudo()
    test_factory_override_hace_que_get_engine_for_agent_use_el_motor_alternativo()
    test_executor_propaga_motor_inalcanzable_en_vez_de_tragarselo()
    test_documentador_propaga_motor_inalcanzable_en_vez_de_tragarselo()
    test_con_fallback_motor_local_activa_kimi_si_el_usuario_acepta()
    test_con_fallback_motor_local_kimi_tambien_inalcanzable_no_propaga_excepcion_cruda()
    test_con_fallback_motor_local_usa_el_modelo_kimi_correcto_por_agente()
    test_con_fallback_motor_local_corta_sin_preguntar_si_el_agente_no_tiene_modelo_kimi_mapeado()
    test_con_fallback_motor_local_usuario_rechaza()
    test_con_fallback_motor_local_sin_confirmar_no_pregunta()
    test_con_fallback_motor_local_no_repregunta_si_el_override_ya_esta_activo()
    test_documentar_si_corresponde_no_tira_el_pipeline_si_el_motor_esta_inalcanzable()
    test_calcular_metricas_agentes_vacio_si_no_hay_archivo()
    test_registrar_metrica_agente_y_calcular_metricas_agentes()
    test_tabla_metricas_agentes_incluye_fila_total()
    test_tabla_metricas_agentes_vacia_si_no_hay_datos()
    test_calcular_metricas_agentes_desde_linea_acota_a_la_sesion_actual()
    print("\nTodo OK.")
