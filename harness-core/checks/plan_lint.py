"""
plan_lint.py
------------
Chequeos heurísticos (regex sobre texto, sin LLM) del CONTENIDO de plan.json,
en tiempo de planificación — antes de que exista ningún código generado.

No reemplaza a plan_validator.py. La diferencia es la misma que separa
format_check.py de Compliance: plan_validator.py es estructural y no puede
tener falsos positivos (IDs duplicados, ciclos, un archivo con más de un
dueño) — por eso corre automático dentro de orchestrator._cargar_plan y un
plan que lo viola ni se ejecuta. plan_lint.py lee prosa (detalle_tecnico,
criterios_aceptacion, interfaz) con regex — SÍ puede tener falsos positivos
(una mención de comentario, un import de librería externa con forma
parecida) — por eso es una herramienta de apoyo que el Planner corre a mano
antes de dar un plan por terminado, no un gate automático.

Detecta cinco clases de bug real ya vistas en proyectos migrados con este
harness (ver Harness/docs/handoff.md y docs/pendientes.md, "Dividir items grandes..."):

1. **Dependencia no declarada.** detalle_tecnico/criterios_aceptacion/
   interfaz de un item menciona el ID de otro item, pero ese id no está en
   depende_de — ej. un item de auth necesitaba get_db (de un item de DB base)
   sin tenerlo en depende_de, y otro item repitió el mismo import roto porque
   depende_de no incluía el item de DB base.
2. **Import huérfano o dependencia de módulo no declarada.** detalle_tecnico
   de un item menciona un import "app.x.y.z" cuyo módulo (app.x.y) no lo
   genera NINGÚN item del plan (import huérfano — típicamente falta un item
   de infraestructura, ver "infraestructura que nadie pidió explícitamente"
   en docs/handoff.md), o lo genera un item que no está en depende_de
   (mismo bug que el punto 1, mirado desde el import en vez del ID citado
   en prosa).
3. **Item muy por encima de la mediana del propio plan** (archivos_destino
   o criterios_aceptacion) — candidato a dividir en items más chicos. No es
   un número fijo (ver docs/pendientes.md: "más de 5 items" ya se descartó como
   arbitrario) — se compara contra la mediana de ESTE plan, no un umbral
   universal. Señal débil a propósito: el tamaño solo no predice bien la
   oscilación (ver punto 4), pero vale la pena marcarlo igual. Además del
   umbral relativo, un piso absoluto (calibrado con datos reales,
   2026-08-30): un plan con muchos items triviales de 1 archivo empuja la
   mediana muy abajo, y sin piso el umbral relativo marcaba items de 3-4
   archivos que en términos absolutos son un tamaño normal (14 de 15
   avisos en esa corrida real eran justo este falso positivo).
4. **Contrato de retorno sin claves exactas.** criterios_aceptacion exige
   "exactamente las claves X/Y" pero detalle_tecnico nunca menciona esas
   claves literalmente — la causa raíz real más repetida de oscilación
   (visto en varios proyectos reales): Executor no tiene de dónde sacar el
   nombre exacto y termina inventando uno plausible pero distinto.
5. **Ambigüedad en prosa**: elipsis dentro de código citado (ej.
   `.join(Tabla, ...)`, el bug real de vca_com en venta_repository.py) o
   frases tipo "según corresponda"/"de forma adecuada" que dejan una regla
   sin especificar.
"""

import json
import re
import statistics
import sys

_PATRON_IMPORT_APP = re.compile(r"\bapp(?:\.[a-zA-Z_][a-zA-Z0-9_]*){2,}\b")

_UMBRAL_TAMANO_RELATIVO = 2.5
_MINIMO_ITEMS_PARA_MEDIANA = 5
# Piso absoluto además del relativo -- sin esto, un plan con muchos items
# triviales de 1 archivo empuja la mediana tan abajo que 3-4 archivos (un
# tamaño normal en cualquier otro plan) dispara el umbral relativo solo.
# Calibrado con datos reales (2026-08-30): 14/15 avisos de tamaño eran
# justo este falso positivo.
_PISO_ABSOLUTO_ARCHIVOS = 4
_PISO_ABSOLUTO_CRITERIOS = 4

_PATRON_CLAVES_EXACTAS = re.compile(
    # (?!que\b) -- falso positivo real encontrado corriendo contra un
    # plan real ya migrado y completado (2026-08-30): "debe devolver
    # EXACTAMENTE las claves que lee ConsultarIdService.consultar()" no es
    # una lista literal, es una referencia a OTRO símbolo -- sin el
    # lookahead, _claves_citadas extraía "que"/"lee" como si fueran los
    # nombres de clave reales.
    r"exactamente las claves\s+(?!que\b)(.+?)(?:\s+--|\s*\(|[.\n]|$)", re.IGNORECASE,
)
_STOPWORDS_CONECTORES = {"y", "o", "u", "ni", "el", "la", "los", "las", "de", "del"}

_PATRON_ELIPSIS_CODIGO = re.compile(r"`[^`\n]*\.\.\.[^`\n]*`")
_FRASES_AMBIGUAS = (
    "según corresponda", "lo que corresponda", "de forma adecuada",
    "como corresponda", "el que corresponda", "la que corresponda",
)


def _modulos_generados(items: list[dict]) -> dict[str, str]:
    """
    { "app.core.config": "CORE-001", ... } — mapea cada módulo Python
    (derivado de un archivo .py en archivos_destino, bajo una carpeta "app/")
    al id del item que lo genera.
    """
    modulos: dict[str, str] = {}
    for item in items:
        for archivo in item.get("archivos_destino", []):
            if not archivo.endswith(".py"):
                continue
            partes = archivo.split("/")
            if "app" not in partes:
                continue
            idx = partes.index("app")
            modulo = ".".join(partes[idx:])[: -len(".py")]
            modulos[modulo] = item["id"]
    return modulos


def _texto_relevante(item: dict) -> str:
    partes = [item.get("detalle_tecnico", "")]
    partes.extend(item.get("criterios_aceptacion", []))
    partes.append(json.dumps(item.get("interfaz", {}), ensure_ascii=False))
    return "\n".join(partes)


def _ids_mencionados(texto: str, todos_los_ids: set[str], id_propio: str) -> set[str]:
    return {
        otro_id
        for otro_id in todos_los_ids
        if otro_id != id_propio and re.search(rf"\b{re.escape(otro_id)}\b", texto)
    }


def _modulo_mas_especifico_conocido(import_encontrado: str, modulos: dict[str, str]) -> str | None:
    """
    'app.core.config.get_settings' puede resolver a módulo 'app.core.config'
    (símbolo get_settings) o, si eso no está registrado, a un módulo más
    largo. Probar de más específico a menos específico y devolver el primer
    módulo conocido, o None si ningún prefijo matchea.
    """
    partes = import_encontrado.split(".")
    for corte in range(len(partes), 1, -1):
        candidato = ".".join(partes[:corte])
        if candidato in modulos:
            return candidato
    return None


def _avisos_tamano_relativo(items: list[dict]) -> list[str]:
    """
    Compara cada item contra la MEDIANA del propio plan, no un número fijo
    (ver docs/pendientes.md: un umbral universal tipo "más de 5 items" ya se
    descartó como arbitrario porque no se ajusta a la escala real de cada
    plan). Se salta en planes chicos, donde la mediana no es representativa.
    """
    if len(items) < _MINIMO_ITEMS_PARA_MEDIANA:
        return []

    n_archivos = [len(item.get("archivos_destino", [])) for item in items]
    n_criterios = [len(item.get("criterios_aceptacion", [])) for item in items]
    mediana_archivos = statistics.median(n_archivos)
    mediana_criterios = statistics.median(n_criterios)

    avisos = []
    for item in items:
        item_id = item["id"]
        na = len(item.get("archivos_destino", []))
        nc = len(item.get("criterios_aceptacion", []))
        if na >= _UMBRAL_TAMANO_RELATIVO * mediana_archivos and na >= _PISO_ABSOLUTO_ARCHIVOS:
            avisos.append(
                f"{item_id}: {na} archivos_destino, {_UMBRAL_TAMANO_RELATIVO}x+ la "
                f"mediana del plan ({mediana_archivos:g}) -- candidato a dividir en "
                f"items más chicos."
            )
        if nc >= _UMBRAL_TAMANO_RELATIVO * mediana_criterios and nc >= _PISO_ABSOLUTO_CRITERIOS:
            avisos.append(
                f"{item_id}: {nc} criterios_aceptacion, {_UMBRAL_TAMANO_RELATIVO}x+ la "
                f"mediana del plan ({mediana_criterios:g}) -- candidato a dividir en "
                f"items más chicos."
            )
    return avisos


def _claves_citadas(texto_criterio: str) -> list[str]:
    m = _PATRON_CLAVES_EXACTAS.search(texto_criterio)
    if not m:
        return []
    candidatos = re.findall(r"[a-zA-Z_][a-zA-Z0-9_]*", m.group(1))
    return [c for c in candidatos if c.lower() not in _STOPWORDS_CONECTORES]


def _avisos_claves_sin_mencion(item: dict) -> list[str]:
    """
    criterios_aceptacion exige "exactamente las claves X/Y" -- si
    detalle_tecnico nunca menciona esa clave literalmente, Executor no
    tiene de dónde sacar el nombre exacto (mismo patrón real visto en
    varios proyectos, ver docstring del módulo).
    """
    detalle = item.get("detalle_tecnico", "")
    avisos = []
    for criterio in item.get("criterios_aceptacion", []):
        faltantes = [
            c for c in _claves_citadas(criterio)
            if not re.search(rf"\b{re.escape(c)}\b", detalle, re.IGNORECASE)
        ]
        if faltantes:
            plural = len(faltantes) > 1
            avisos.append(
                f"{item['id']}: el criterio exige \"exactamente las claves\" "
                f"{', '.join(faltantes)}, pero detalle_tecnico no menciona "
                f"{'esas claves' if plural else 'esa clave'} literalmente -- "
                f"Executor no la va a adivinar bien."
            )
    return avisos


def _avisos_ambiguedad(item: dict) -> list[str]:
    detalle = item.get("detalle_tecnico", "")
    avisos = []
    if _PATRON_ELIPSIS_CODIGO.search(detalle):
        avisos.append(
            f"{item['id']}: detalle_tecnico tiene una elipsis '...' dentro de "
            f"código citado -- si es una condición de JOIN/filtro, especificarla "
            f"completa (mismo patrón real que el bug de vca_com en "
            f"venta_repository.py)."
        )
    detalle_lower = detalle.lower()
    for frase in _FRASES_AMBIGUAS:
        if frase in detalle_lower:
            avisos.append(
                f"{item['id']}: detalle_tecnico usa \"{frase}\" -- frase "
                f"ambigua, especificar la regla exacta en vez de dejarla "
                f"implícita."
            )
    return avisos


def lintear_plan(plan: dict) -> list[str]:
    """Devuelve una lista de avisos — vacía si no se encontró nada sospechoso.
    A diferencia de validar_plan(), estos avisos son heurísticos: revisar
    antes de descartarlos como falso positivo, no asumir que todos aplican."""
    avisos: list[str] = []
    items = plan.get("items", [])
    todos_los_ids = {item["id"] for item in items}
    modulos = _modulos_generados(items)

    avisos.extend(_avisos_tamano_relativo(items))

    for item in items:
        avisos.extend(_avisos_claves_sin_mencion(item))
        avisos.extend(_avisos_ambiguedad(item))

    for item in items:
        item_id = item["id"]
        depende_de = set(item.get("depende_de", []))
        texto = _texto_relevante(item)

        for otro_id in sorted(_ids_mencionados(texto, todos_los_ids, item_id)):
            if otro_id not in depende_de:
                avisos.append(
                    f"{item_id}: menciona '{otro_id}' en detalle_tecnico/"
                    f"criterios_aceptacion/interfaz, pero '{otro_id}' no está "
                    f"en depende_de — si de verdad reusa algo de ahí, agregarlo "
                    f"a depende_de (ver plan.contract.md, 'interfaz — contrato "
                    f"de consumo entre items')."
                )

        imports_citados = sorted(set(_PATRON_IMPORT_APP.findall(item.get("detalle_tecnico", ""))))
        for import_citado in imports_citados:
            modulo = _modulo_mas_especifico_conocido(import_citado, modulos)
            if modulo is None:
                avisos.append(
                    f"{item_id}: menciona el import '{import_citado}', pero "
                    f"ningún item del plan genera un archivo para ese módulo "
                    f"(import huérfano — ¿falta un item de infraestructura?)."
                )
                continue
            dueno = modulos[modulo]
            if dueno == item_id or dueno in depende_de:
                continue
            avisos.append(
                f"{item_id}: menciona el import '{import_citado}' (módulo "
                f"'{modulo}', generado por '{dueno}'), pero '{dueno}' no está "
                f"en depende_de."
            )

    return avisos


def main() -> None:
    if len(sys.argv) < 2:
        print("Uso: python checks/plan_lint.py <proyecto>")
        sys.exit(1)

    ruta_plan = f"{sys.argv[1]}/.harness/config/plan.json"
    with open(ruta_plan, encoding="utf-8") as f:
        plan = json.load(f)

    avisos = lintear_plan(plan)
    if not avisos:
        print("plan_lint: sin avisos.")
        return

    print(f"plan_lint: {len(avisos)} aviso(s) — revisar antes de ejecutar el plan (pueden ser falsos positivos):\n")
    for aviso in avisos:
        print(f"  - {aviso}")


if __name__ == "__main__":
    main()
