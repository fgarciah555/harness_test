"""
Tests de checks/hallazgos.py — sin red, corren con
`python tests/test_hallazgos.py`. Mismo estilo que tests/test_plan_lint.py
(un archivo por check module).
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from checks import hallazgos


def _item(item_id, archivos_destino=None):
    return {"id": item_id, "archivos_destino": archivos_destino or [f"backend/app/{item_id}.py"]}


def test_riesgo_va_a_riesgos_heredados_md_del_deployable():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        n = hallazgos.registrar_hallazgos(
            str(root), _item("PED-MANT-001"), "executor",
            [{"tipo": "riesgo", "descripcion": "password se compara en texto plano"}],
        )
        assert n == 1
        contenido = (root / "backend" / "docs" / "riesgos_heredados.md").read_text()
        assert "password se compara en texto plano" in contenido
        assert "PED-MANT-001" in contenido and "executor" in contenido
        assert not (root / "backend" / "docs" / "recomendaciones-tecnicas.md").exists()
    print("OK: un hallazgo tipo 'riesgo' se registra en <deployable>/docs/riesgos_heredados.md")


def test_recomendacion_va_a_recomendaciones_tecnicas_md_del_deployable():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        n = hallazgos.registrar_hallazgos(
            str(root), _item("PED-MANT-002"), "compliance",
            [{"tipo": "recomendacion", "descripcion": "extraer la validación repetida a un helper"}],
        )
        assert n == 1
        contenido = (root / "backend" / "docs" / "recomendaciones-tecnicas.md").read_text()
        assert "extraer la validación repetida a un helper" in contenido
        assert not (root / "backend" / "docs" / "riesgos_heredados.md").exists()
    print("OK: un hallazgo tipo 'recomendacion' se registra en <deployable>/docs/recomendaciones-tecnicas.md")


def test_deriva_el_deployable_de_archivos_destino_no_asume_siempre_backend():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        hallazgos.registrar_hallazgos(
            str(root), _item("DAL-001", archivos_destino=["dal/app/model/x.py"]), "executor",
            [{"tipo": "riesgo", "descripcion": "riesgo del DAL"}],
        )
        assert (root / "dal" / "docs" / "riesgos_heredados.md").exists()
        assert not (root / "backend" / "docs" / "riesgos_heredados.md").exists()
    print("OK: registrar_hallazgos deriva la carpeta del deployable de archivos_destino (mismo criterio que smoke_test)")


def test_tipo_desconocido_o_sin_descripcion_se_ignora():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        n = hallazgos.registrar_hallazgos(
            str(root), _item("PED-MANT-003"), "executor",
            [
                {"tipo": "no_existe", "descripcion": "algo"},
                {"tipo": "riesgo", "descripcion": ""},
                {"tipo": "riesgo"},
                "esto ni siquiera es un dict",
            ],
        )
        assert n == 0
        assert not (root / "backend" / "docs").exists()
    print("OK: hallazgos con tipo desconocido o sin descripción se ignoran, sin romper nada")


def test_encabezado_solo_la_primera_vez_y_acumula():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        hallazgos.registrar_hallazgos(
            str(root), _item("PED-MANT-001"), "executor",
            [{"tipo": "riesgo", "descripcion": "primer hallazgo"}],
        )
        hallazgos.registrar_hallazgos(
            str(root), _item("PED-MANT-004"), "compliance",
            [{"tipo": "riesgo", "descripcion": "segundo hallazgo"}],
        )
        contenido = (root / "backend" / "docs" / "riesgos_heredados.md").read_text()
        assert contenido.count("# Riesgos heredados") == 1
        assert "primer hallazgo" in contenido
        assert "segundo hallazgo" in contenido
        assert contenido.index("primer hallazgo") < contenido.index("segundo hallazgo")
    print("OK: el encabezado se escribe una sola vez y los hallazgos sucesivos se acumulan (append)")


def test_varios_hallazgos_en_una_sola_llamada():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        n = hallazgos.registrar_hallazgos(
            str(root), _item("PED-MANT-005"), "executor",
            [
                {"tipo": "riesgo", "descripcion": "riesgo A"},
                {"tipo": "recomendacion", "descripcion": "recomendación A"},
            ],
        )
        assert n == 2
        assert "riesgo A" in (root / "backend" / "docs" / "riesgos_heredados.md").read_text()
        assert "recomendación A" in (root / "backend" / "docs" / "recomendaciones-tecnicas.md").read_text()
    print("OK: una sola llamada con hallazgos de ambos tipos escribe en los dos archivos")


if __name__ == "__main__":
    test_riesgo_va_a_riesgos_heredados_md_del_deployable()
    test_recomendacion_va_a_recomendaciones_tecnicas_md_del_deployable()
    test_deriva_el_deployable_de_archivos_destino_no_asume_siempre_backend()
    test_tipo_desconocido_o_sin_descripcion_se_ignora()
    test_encabezado_solo_la_primera_vez_y_acumula()
    test_varios_hallazgos_en_una_sola_llamada()
    print("\nTodos los tests de hallazgos pasaron.")
