"""
Interfaz REAL que Executor reporta al terminar un item, para complementar la
interfaz PREDICHA que el Planner escribió en plan.json antes de que el
código existiera. Ver schemas/plan.contract.md, sección "Interfaz real
reportada por Executor". Compartido entre agents/executor.py (escribe la
suya, lee las de sus dependencias) y agents/compliance.py (solo lee).
"""
import json
import re

from access_control import AgentFileGuard, Zona


def leer_interfaz_real(guard: AgentFileGuard, item_id: str) -> dict | None:
    try:
        contenido = guard.read(Zona.HARNESS_INTERFACES, f"{item_id}.json")
    except FileNotFoundError:
        return None
    return json.loads(contenido).get("interfaz", {})


def _como_lista(dependencia_reusable) -> list[dict]:
    """
    Normaliza las formas que `dependencia_reusable` puede tener en la
    interfaz PREDICHA del Planner (la interfaz REAL que escribe Executor
    siempre es una lista de {nombre, import, ...}, ver
    agents/executor.py::_escribir_interfaz_real):
      - forma singular vieja: un dict suelto con 'import' como clave directa
        (`{"import": "...", "nombre": ...}`) -> se envuelve en una lista de 1.
      - forma por nombre (usada en planes reales, ej. web-portal-coas
        rehecho con DAL separado, 2026-08-26): un dict SIN 'import' propio,
        cuyas claves son nombres y cuyos valores son los dicts reusables
        (`{"LoginRequest": {"import": "..."}, "TokenResponse": {"import": "..."}}`)
        -> se aplana a lista, inyectando 'nombre' desde la clave si el valor
        no lo trae ya. Sin este caso, combinar_interfaz() trataba el dict
        entero como un único elemento sin 'import' -> lo descartaba
        silenciosamente, perdiendo TODAS las entradas de la predicha en
        cuanto la dependencia reportaba su propia interfaz real (bug real
        encontrado ejecutando ese plan: BE-AUTH-004 se quedó sin los
        imports de schemas que BE-AUTH-003 sí declaraba en plan.json).
      - forma lista: ya es lo que se necesita, se devuelve tal cual.
    """
    if isinstance(dependencia_reusable, dict):
        if not dependencia_reusable:
            return []
        if "import" in dependencia_reusable:
            return [dependencia_reusable]
        return [
            {"nombre": nombre, **valor} if "nombre" not in valor else dict(valor)
            for nombre, valor in dependencia_reusable.items()
            if isinstance(valor, dict)
        ]
    return dependencia_reusable or []


def _simbolo_existe_en_codigo(nombre: str, codigo: str) -> bool:
    """
    Heurística liviana (no un parser real, un grep con criterio): ¿aparece
    'nombre' definido como función, clase o variable de nivel superior en
    este código? Cubre los casos reales que un item de Harness suele dejar
    reusable (funciones, clases/excepciones, instancias de APIRouter u
    otras variables de módulo). Un falso negativo aquí (ej. una firma
    multilínea rara) solo poda una entrada que igual puede recuperarse por
    el mecanismo de INTERFAZ_INCOMPLETA de arbitro.py -- preferible a un
    falso positivo, que dejaría sobrevivir una predicha ya incorrecta.
    """
    patrones = (
        rf"^\s*(async\s+)?def\s+{re.escape(nombre)}\s*\(",
        rf"^\s*class\s+{re.escape(nombre)}\s*[:\(]",
        rf"^\s*{re.escape(nombre)}\s*(:[^=]*)?=",
    )
    return any(re.search(p, codigo, re.MULTILINE) for p in patrones)


def podar_predicha_no_generada(predicha: dict, codigo_generado: str | None) -> dict:
    """
    Antes de combinar con la interfaz real, descarta de `dependencia_reusable`
    de la predicha las entradas cuyo símbolo declarado ('nombre') no aparece
    definido en el código realmente generado por ese mismo item -- ataca el
    caso en que la predicha describe un símbolo que Executor nunca
    implementó bajo ese nombre y que, sin esta poda, combinar_interfaz()
    dejaría sobrevivir indefinidamente al lado de la real (ver detalle en
    handoff.md y plan.contract.md).

    A propósito NO poda lo que la real no vuelve a mencionar pero SÍ existe
    en el código (ver test_combinar_interfaz_normaliza_forma_por_nombre_sin_perder_entradas):
    ese símbolo sigue siendo real, solo Executor no lo volvió a listar.

    Sin código generado disponible, no poda nada -- se confía en la
    predicha tal cual, como siempre.
    """
    if not codigo_generado:
        return predicha
    lista = _como_lista(predicha.get("dependencia_reusable"))
    si_existen = [
        r for r in lista
        if not r.get("nombre") or _simbolo_existe_en_codigo(r["nombre"], codigo_generado)
    ]
    if len(si_existen) == len(lista):
        return predicha
    podada = dict(predicha)
    podada["dependencia_reusable"] = si_existen
    return podada


def combinar_interfaz(predicha: dict, real: dict | None) -> dict:
    """
    Unión por 'import' en dependencia_reusable: la real gana en caso de
    conflicto (viene del código de verdad), lo que solo está en la predicha
    se conserva (Executor no tiene por qué haberlo vuelto a reportar), lo
    que solo está en la real se agrega. El resto de los campos de `predicha`
    (ej. `endpoint`) se conserva tal cual, salvo que `real` los redefina.
    """
    if not real:
        return predicha

    combinada = {**predicha, **{k: v for k, v in real.items() if k != "dependencia_reusable"}}

    por_import = {
        r["import"]: r for r in _como_lista(predicha.get("dependencia_reusable")) if "import" in r
    }
    for r in _como_lista(real.get("dependencia_reusable")):
        if "import" in r:
            por_import[r["import"]] = r

    if por_import:
        combinada["dependencia_reusable"] = list(por_import.values())
    return combinada
