"""
Tests de checks/plan_lint.py — sin red, corren con `python tests/test_plan_lint.py`.
Mismo estilo que tests/test_frontend_check.py (un archivo por check module).
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from checks.plan_lint import lintear_plan, _avisos_tamano_relativo, _claves_citadas

PLAN_EJEMPLO = Path(__file__).resolve().parent.parent / "schemas" / "plan.example.json"


def _item_minimo(item_id, depende_de=None, interfaz=None, archivos_destino=None,
                  criterios_aceptacion=None, detalle_tecnico=""):
    return {
        "id": item_id,
        "depende_de": depende_de or [],
        "interfaz": interfaz if interfaz is not None else {},
        "archivos_destino": archivos_destino if archivos_destino is not None else [f"backend/app/service/{item_id.lower()}.py"],
        "criterios_aceptacion": criterios_aceptacion if criterios_aceptacion is not None else ["algún criterio"],
        "detalle_tecnico": detalle_tecnico,
    }


def test_lintear_plan_detecta_id_citado_sin_depende_de():
    plan = {"items": [
        _item_minimo("A", detalle_tecnico="reusa la conexión ya armada en B, ver B"),
        _item_minimo("B"),
    ]}
    avisos = lintear_plan(plan)
    assert any("'B'" in a and "A:" in a for a in avisos)
    print("OK: lintear_plan detecta un item citado en prosa sin estar en depende_de")


def test_lintear_plan_no_marca_el_propio_id_del_item():
    plan = {"items": [
        _item_minimo("A", detalle_tecnico="A expone un endpoint propio, sin depender de nadie"),
    ]}
    avisos = lintear_plan(plan)
    assert avisos == []
    print("OK: lintear_plan no se auto-marca")


def test_lintear_plan_no_marca_id_citado_que_si_esta_en_depende_de():
    plan = {"items": [
        _item_minimo("A", depende_de=["B"], detalle_tecnico="reusa la conexión de B"),
        _item_minimo("B"),
    ]}
    avisos = lintear_plan(plan)
    assert avisos == []
    print("OK: lintear_plan no marca nada cuando la dependencia citada ya está declarada")


def test_lintear_plan_detecta_import_huerfano():
    plan = {"items": [
        _item_minimo(
            "A",
            detalle_tecnico="importar app.core.exceptions.DomainError para las excepciones de negocio",
        ),
    ]}
    avisos = lintear_plan(plan)
    assert any("import huérfano" in a and "app.core.exceptions.DomainError" in a for a in avisos)
    print("OK: lintear_plan detecta un import cuyo módulo ningún item genera")


def test_lintear_plan_detecta_import_de_modulo_real_sin_depende_de():
    plan = {"items": [
        _item_minimo("CORE-001", archivos_destino=["backend/app/core/config.py"]),
        _item_minimo(
            "AUTH-001",
            detalle_tecnico="usar app.core.config.get_settings() para leer el secret_key",
        ),
    ]}
    avisos = lintear_plan(plan)
    assert any(
        "app.core.config.get_settings" in a and "generado por 'CORE-001'" in a and "AUTH-001" in a
        for a in avisos
    )
    print("OK: lintear_plan detecta un import de un módulo real cuyo item dueño no está en depende_de")


def test_lintear_plan_no_marca_import_de_modulo_real_ya_declarado():
    plan = {"items": [
        _item_minimo("CORE-001", archivos_destino=["backend/app/core/config.py"]),
        _item_minimo(
            "AUTH-001",
            depende_de=["CORE-001"],
            detalle_tecnico="usar app.core.config.get_settings() para leer el secret_key",
        ),
    ]}
    avisos = lintear_plan(plan)
    assert avisos == []
    print("OK: lintear_plan no marca un import cuyo item dueño ya está en depende_de")


def test_lintear_plan_no_marca_import_del_propio_modulo_del_item():
    plan = {"items": [
        _item_minimo(
            "CORE-001",
            archivos_destino=["backend/app/core/config.py"],
            detalle_tecnico="get_settings usa lru_cache; app.core.config.get_settings queda cacheada",
        ),
    ]}
    avisos = lintear_plan(plan)
    assert avisos == []
    print("OK: lintear_plan no marca un item citando su propio módulo")


def test_lintear_plan_ignora_menciones_de_dos_segmentos_tipo_app_py():
    # "app.py" / "app.main" (un solo segmento después de "app") son el patrón
    # típico de nombre de archivo del monolito origen o de módulo raíz, no un
    # import interno de 3+ segmentos como los que realmente causaron bugs
    # reales (ver docstring del módulo) — no deberían generar ruido.
    plan = {"items": [
        _item_minimo("A", detalle_tecnico="reemplaza la ruta /login de app.py del monolito"),
    ]}
    avisos = lintear_plan(plan)
    assert avisos == []
    print("OK: lintear_plan no marca menciones de un solo segmento tipo 'app.py'")


def test_lintear_plan_contra_fixture_real_son_solo_referencias_hacia_adelante():
    plan = json.loads(PLAN_EJEMPLO.read_text())
    avisos = lintear_plan(plan)
    # El fixture de pedidos es un plan ya probado end-to-end (ver handoff.md) —
    # los únicos avisos esperables son menciones hacia adelante en prosa
    # ("usado por PED-005"), nunca un import huérfano ni un import de módulo
    # real sin depender de su dueño.
    assert all("import huérfano" not in a for a in avisos)
    assert all("generado por" not in a for a in avisos)
    print(f"OK: lintear_plan contra el fixture real -- {len(avisos)} referencia(s) hacia adelante, sin bugs de import")


def test_avisos_tamano_relativo_marca_item_muy_por_encima_de_la_mediana():
    items = [_item_minimo(f"P-{i}", archivos_destino=["a.py"], criterios_aceptacion=["c1"]) for i in range(4)]
    items.append(_item_minimo(
        "P-GRANDE",
        archivos_destino=["a.py", "b.py", "c.py", "d.py"],
        criterios_aceptacion=["c1", "c2", "c3", "c4"],
    ))
    avisos = _avisos_tamano_relativo(items)
    assert any("P-GRANDE" in a and "archivos_destino" in a for a in avisos)
    assert any("P-GRANDE" in a and "criterios_aceptacion" in a for a in avisos)
    assert all("P-0" not in a and "P-1" not in a for a in avisos)
    print("OK: _avisos_tamano_relativo marca un item muy por encima de la mediana del plan")


def test_avisos_tamano_relativo_piso_absoluto_evita_falso_positivo_con_mediana_baja():
    # Reproduce el caso real de Tesorería (2026-08-30): muchos items
    # triviales de 1 archivo empujan la mediana tan abajo que un item de 3
    # archivos (tamaño normal en cualquier plan) dispara el umbral relativo
    # solo -- el piso absoluto lo suprime.
    items = [_item_minimo(f"P-{i}", archivos_destino=["a.py"], criterios_aceptacion=["c1", "c2"]) for i in range(10)]
    items.append(_item_minimo(
        "P-NORMAL",
        archivos_destino=["a.py", "b.py", "c.py"],
        criterios_aceptacion=["c1", "c2", "c3"],
    ))
    assert _avisos_tamano_relativo(items) == []
    print("OK: el piso absoluto evita marcar un item de tamaño normal cuando la mediana del plan es muy baja")


def test_avisos_tamano_relativo_no_marca_plan_homogeneo():
    items = [_item_minimo(f"P-{i}", archivos_destino=["a.py", "b.py"], criterios_aceptacion=["c1", "c2"]) for i in range(6)]
    assert _avisos_tamano_relativo(items) == []
    print("OK: _avisos_tamano_relativo no marca nada si todos los items tienen tamaño parecido")


def test_avisos_tamano_relativo_se_salta_en_planes_chicos():
    items = [_item_minimo("A", archivos_destino=["a.py"]), _item_minimo("B", archivos_destino=["a.py", "b.py", "c.py", "d.py"])]
    assert _avisos_tamano_relativo(items) == []
    print("OK: _avisos_tamano_relativo se salta en planes con menos de 5 items (mediana no representativa)")


def test_claves_citadas_extrae_identificadores_sin_conectores():
    texto = "devuelve un dict con EXACTAMENTE las claves comision_base y comision_final -- resto de la frase"
    assert _claves_citadas(texto) == ["comision_base", "comision_final"]
    print("OK: _claves_citadas extrae las claves y descarta la conjunción 'y'")


def test_claves_citadas_no_extrae_nada_si_es_una_referencia_a_otro_simbolo():
    # Falso positivo real encontrado corriendo plan_lint contra Tesorería
    # (ya migrado y completado, 2026-08-30): "las claves que lee X()" es una
    # referencia a otro símbolo, no una lista literal -- sin este fix,
    # _claves_citadas extraía "que"/"lee" como si fueran nombres de clave.
    texto = "armarDatos() debe devolver EXACTAMENTE las claves que lee ConsultarIdService.consultar()"
    assert _claves_citadas(texto) == []
    print("OK: _claves_citadas no extrae nada de 'las claves que lee X()' (referencia, no lista literal)")


def test_lintear_plan_detecta_clave_exacta_no_mencionada_en_detalle_tecnico():
    plan = {"items": [
        _item_minimo(
            "OSC-004",
            detalle_tecnico="devuelve un diccionario con el monto antes del bono y el monto después del bono",
            criterios_aceptacion=[
                "generar_reporte devuelve un dict con EXACTAMENTE las claves comision_base y comision_final",
            ],
        ),
    ]}
    avisos = lintear_plan(plan)
    assert any("comision_base" in a and "comision_final" in a and "OSC-004" in a for a in avisos)
    print("OK: lintear_plan detecta un criterio que exige claves exactas ausentes de detalle_tecnico (bug real de OSC-004)")


def test_lintear_plan_no_marca_clave_exacta_que_si_esta_en_detalle_tecnico():
    plan = {"items": [
        _item_minimo(
            "OSC-004",
            detalle_tecnico="devuelve un dict con comision_base (sin bono) y comision_final (con bono aplicado)",
            criterios_aceptacion=[
                "generar_reporte devuelve un dict con EXACTAMENTE las claves comision_base y comision_final",
            ],
        ),
    ]}
    assert lintear_plan(plan) == []
    print("OK: lintear_plan no marca nada cuando detalle_tecnico ya menciona las claves exactas")


def test_lintear_plan_detecta_elipsis_en_codigo_citado():
    plan = {"items": [
        _item_minimo("A", detalle_tecnico="hacer el join con `.join(Local, ...)` sobre la tabla de locales"),
    ]}
    avisos = lintear_plan(plan)
    assert any("elipsis" in a and "A:" in a for a in avisos)
    print("OK: lintear_plan detecta una elipsis dentro de código citado en detalle_tecnico")


def test_lintear_plan_detecta_frase_ambigua():
    plan = {"items": [
        _item_minimo("A", detalle_tecnico="aplicar el descuento según corresponda al tipo de cliente"),
    ]}
    avisos = lintear_plan(plan)
    assert any("según corresponda" in a and "A:" in a for a in avisos)
    print("OK: lintear_plan detecta una frase ambigua en detalle_tecnico")


def test_lintear_plan_no_marca_prosa_sin_ambiguedad():
    plan = {"items": [
        _item_minimo("A", detalle_tecnico="aplicar un descuento del 10% si el cliente tiene cupon activo"),
    ]}
    assert lintear_plan(plan) == []
    print("OK: lintear_plan no marca prosa concreta sin elipsis ni frases ambiguas")


if __name__ == "__main__":
    test_lintear_plan_detecta_id_citado_sin_depende_de()
    test_lintear_plan_no_marca_el_propio_id_del_item()
    test_lintear_plan_no_marca_id_citado_que_si_esta_en_depende_de()
    test_lintear_plan_detecta_import_huerfano()
    test_lintear_plan_detecta_import_de_modulo_real_sin_depende_de()
    test_lintear_plan_no_marca_import_de_modulo_real_ya_declarado()
    test_lintear_plan_no_marca_import_del_propio_modulo_del_item()
    test_lintear_plan_ignora_menciones_de_dos_segmentos_tipo_app_py()
    test_lintear_plan_contra_fixture_real_son_solo_referencias_hacia_adelante()
    test_avisos_tamano_relativo_marca_item_muy_por_encima_de_la_mediana()
    test_avisos_tamano_relativo_piso_absoluto_evita_falso_positivo_con_mediana_baja()
    test_avisos_tamano_relativo_no_marca_plan_homogeneo()
    test_avisos_tamano_relativo_se_salta_en_planes_chicos()
    test_claves_citadas_extrae_identificadores_sin_conectores()
    test_claves_citadas_no_extrae_nada_si_es_una_referencia_a_otro_simbolo()
    test_lintear_plan_detecta_clave_exacta_no_mencionada_en_detalle_tecnico()
    test_lintear_plan_no_marca_clave_exacta_que_si_esta_en_detalle_tecnico()
    test_lintear_plan_detecta_elipsis_en_codigo_citado()
    test_lintear_plan_detecta_frase_ambigua()
    test_lintear_plan_no_marca_prosa_sin_ambiguedad()
    print("\nTodos los tests de plan_lint pasaron.")
