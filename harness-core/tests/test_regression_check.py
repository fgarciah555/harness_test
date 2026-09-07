"""
Tests de checks/regression_check.py — sin red, corren con
`python tests/test_regression_check.py`. Mismo estilo que
tests/test_docker_check.py / tests/test_plan_lint.py (un archivo por check
module).
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from checks import regression_check


def test_regression_check_pasa_si_toda_la_suite_existente_pasa():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "backend" / "tests").mkdir(parents=True)
        (root / "backend" / "tests" / "test_existente_1.py").write_text(
            "def test_ok_1():\n    assert 1 + 1 == 2\n"
        )
        (root / "backend" / "tests" / "test_existente_2.py").write_text(
            "def test_ok_2():\n    assert 'a' in 'abc'\n"
        )
        item = {"archivos_destino": ["backend/app/algo.py"]}
        resultado = regression_check.correr(str(root), item, python=Path(sys.executable))
        assert resultado == {"estado": "paso"}, resultado
    print("OK: regression_check.correr corre TODA la suite existente del deployable y reporta 'paso'")


def test_regression_check_falla_si_algo_de_la_suite_existente_rompe():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "backend" / "tests").mkdir(parents=True)
        (root / "backend" / "tests" / "test_existente_1.py").write_text(
            "def test_ok():\n    assert True\n"
        )
        # este test YA existía antes del item -- el item lo rompió sin que
        # nadie lo haya declarado en tests_requeridos, por eso hace falta un
        # chequeo aparte de smoke_test.py (que solo mira lo declarado).
        (root / "backend" / "tests" / "test_regresion.py").write_text(
            "def test_ya_existia_y_ahora_rompe():\n    assert False\n"
        )
        item = {"archivos_destino": ["backend/app/algo.py"]}
        resultado = regression_check.correr(str(root), item, python=Path(sys.executable))
        assert resultado["estado"] == "fallo"
        assert "test_ya_existia_y_ahora_rompe" in resultado["detalle"]
    print("OK: regression_check.correr reporta 'fallo' con el detalle real si algo de la suite ya existente se rompió")


def test_regression_check_sin_ningun_test_no_es_fallo():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "backend").mkdir(parents=True)
        item = {"archivos_destino": ["backend/app/algo.py"]}
        resultado = regression_check.correr(str(root), item, python=Path(sys.executable))
        assert resultado == {"estado": "sin_tests"}
    print("OK: regression_check.correr no penaliza un deployable sin ningún test existente ('sin_tests', no 'fallo')")


def test_regression_check_venv_no_encontrado():
    with tempfile.TemporaryDirectory() as tmp:
        item = {"archivos_destino": ["backend/app/algo.py"]}
        resultado = regression_check.correr(tmp, item)  # sin override, sin venv en el tmp -> no lo encuentra
        assert resultado["estado"] == "error"
        assert "venv" in resultado["detalle"].lower()
    print("OK: regression_check.correr reporta 'error' claro si no encuentra un venv del proyecto destino")


def test_regression_check_deriva_la_carpeta_deployable_igual_que_smoke_test():
    # reusa smoke_test._carpeta_deployable -- no duplica la lógica, esto
    # solo confirma el wiring (mismo criterio de carpeta por deployable,
    # ej. backend/ + dal/ como deployables separados).
    assert regression_check._carpeta_deployable({"archivos_destino": ["dal/app/model/x.py"]}) == "dal"
    assert regression_check._carpeta_deployable({"archivos_destino": ["backend/app/api/x.py"]}) == "backend"
    print("OK: regression_check deriva la carpeta del deployable igual que smoke_test (misma función reusada)")


if __name__ == "__main__":
    test_regression_check_pasa_si_toda_la_suite_existente_pasa()
    test_regression_check_falla_si_algo_de_la_suite_existente_rompe()
    test_regression_check_sin_ningun_test_no_es_fallo()
    test_regression_check_venv_no_encontrado()
    test_regression_check_deriva_la_carpeta_deployable_igual_que_smoke_test()
    print("\nTodos los tests de regression_check pasaron.")
