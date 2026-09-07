"""
Tests de checks/convention_check.py — sin red, corren con
`python tests/test_convention_check.py`. Mismo estilo que
tests/test_plan_lint.py (un archivo por check module).
"""
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from checks import convention_check


def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def _repo_con_archivo_commiteado(tmp: Path, ruta_relativa: str, contenido: str) -> Path:
    _git(tmp, "init", "-q")
    _git(tmp, "config", "user.email", "test@example.com")
    _git(tmp, "config", "user.name", "test")
    archivo = tmp / ruta_relativa
    archivo.parent.mkdir(parents=True, exist_ok=True)
    archivo.write_text(contenido)
    _git(tmp, "add", ruta_relativa)
    _git(tmp, "commit", "-q", "-m", "inicial")
    return archivo


def test_clasificar_casing_detecta_snake_camel_pascal_y_ambiguos():
    assert convention_check._clasificar_casing("obtener_usuario") == "snake_case"
    assert convention_check._clasificar_casing("obtenerUsuario") == "camelCase"
    assert convention_check._clasificar_casing("ObtenerUsuario") == "PascalCase"
    assert convention_check._clasificar_casing("main") == "ambiguo_una_palabra"
    assert convention_check._clasificar_casing("MAX_TOKENS") is None
    print("OK: _clasificar_casing distingue snake_case/camelCase/PascalCase/ambiguo/no-clasificable")


def test_convencion_dominante_requiere_minimo_de_identificadores():
    assert convention_check._convencion_dominante({"obtener_usuario", "crear_pedido"}) is None
    print("OK: _convencion_dominante se salta con menos del mínimo de identificadores clasificables")


def test_convencion_dominante_se_salta_en_empate():
    nombres = {"obtener_usuario", "crear_pedido", "obtenerUsuario", "crearPedido"}
    assert convention_check._convencion_dominante(nombres) is None
    print("OK: _convencion_dominante se salta si hay empate real entre convenciones")


def test_convencion_dominante_detecta_snake_case():
    nombres = {"obtener_usuario", "crear_pedido", "borrar_pedido", "listar_pedidos"}
    assert convention_check._convencion_dominante(nombres) == "snake_case"
    print("OK: _convencion_dominante detecta snake_case como dominante")


def test_archivo_no_trackeado_en_git_no_se_chequea():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _git(root, "init", "-q")
        archivo = root / "nuevo.py"
        archivo.write_text("def snake_nuevo():\n    pass\n")
        errores = convention_check.verificar(str(root), ["nuevo.py"])
    assert errores == []
    print("OK: convention_check.verificar no chequea un archivo que no estaba trackeado en HEAD (es nuevo)")


def test_identificador_nuevo_que_rompe_la_convencion_es_error():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        contenido_antes = (
            "def obtenerUsuario():\n    pass\n\n"
            "def crearPedido():\n    pass\n\n"
            "def borrarPedido():\n    pass\n"
        )
        archivo = _repo_con_archivo_commiteado(root, "servicio.py", contenido_antes)
        # Executor agrega una función nueva en snake_case, rompiendo la
        # convención dominante (camelCase) que el archivo ya tenía.
        archivo.write_text(contenido_antes + "\ndef listar_pedidos():\n    pass\n")

        errores = convention_check.verificar(str(root), ["servicio.py"])
        assert any("listar_pedidos" in e and "camelCase" in e for e in errores)
    print("OK: convention_check detecta un identificador nuevo que no sigue la convención dominante del archivo")


def test_identificador_nuevo_que_respeta_la_convencion_no_es_error():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        contenido_antes = (
            "def obtenerUsuario():\n    pass\n\n"
            "def crearPedido():\n    pass\n\n"
            "def borrarPedido():\n    pass\n"
        )
        archivo = _repo_con_archivo_commiteado(root, "servicio.py", contenido_antes)
        archivo.write_text(contenido_antes + "\ndef listarPedidos():\n    pass\n")

        errores = convention_check.verificar(str(root), ["servicio.py"])
        assert errores == []
    print("OK: convention_check no marca nada cuando el identificador nuevo sigue la convención dominante")


def test_archivo_sin_convencion_dominante_clara_no_bloquea():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        # Solo 2 identificadores clasificables (menos del mínimo) -- no hay
        # base para detectar una convención dominante, no se puede exigir nada.
        archivo = _repo_con_archivo_commiteado(root, "chico.py", "def x():\n    pass\n\ny = 1\n")
        archivo.write_text(archivo.read_text() + "\ndef nuevo_snake():\n    pass\n")

        errores = convention_check.verificar(str(root), ["chico.py"])
        assert errores == []
    print("OK: convention_check no bloquea si el archivo no tiene suficiente evidencia de una convención dominante")


if __name__ == "__main__":
    test_clasificar_casing_detecta_snake_camel_pascal_y_ambiguos()
    test_convencion_dominante_requiere_minimo_de_identificadores()
    test_convencion_dominante_se_salta_en_empate()
    test_convencion_dominante_detecta_snake_case()
    test_archivo_no_trackeado_en_git_no_se_chequea()
    test_identificador_nuevo_que_rompe_la_convencion_es_error()
    test_identificador_nuevo_que_respeta_la_convencion_no_es_error()
    test_archivo_sin_convencion_dominante_clara_no_bloquea()
    print("\nTodos los tests de convention_check pasaron.")
