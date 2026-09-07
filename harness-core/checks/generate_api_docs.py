"""
Documentación de API — post-proceso determinístico y gratis (sin LLM), para
correr manualmente cuando el proyecto (o una tanda de items backend) está
lista para documentar. A diferencia de api_endpoints.py (que lee
interfaz.endpoint de plan.json, una predicción del Planner que puede quedar
desalineada o directamente vacía si el plan agrupa varios endpoints por
item, ver Pendientes.md), esto le pide el OpenAPI a la app real generada
por Executor — fuente de verdad exacta, cero desalineación posible con el
código real.

No es un agente, no pasa por AgentFileGuard. Corre `app.openapi()` (método
nativo de FastAPI, no requiere levantar un servidor) en el venv del
PROYECTO DESTINO vía subprocess — mismo patrón que smoke_test.py, porque
necesita FastAPI/Pydantic instalados ahí, no en el venv del Harness.

Genera dos artefactos en docs/ del proyecto destino:
- openapi.json: se importa directo en Postman (File -> Import), sin
  conversión — Postman entiende OpenAPI 3.x nativamente.
- api-endpoints-curl.md: un comando curl real por endpoint (método, ruta,
  headers, query/path params, body de ejemplo armado desde el schema real),
  más un ejemplo de la respuesta 2xx.

Uso: python generate_api_docs.py <project_root> [base_url]
-- itera automáticamente todas las carpetas backend distintas que aparecen en
plan.json (ver `_carpetas_backend_del_plan`), una app FastAPI por deployable
(ej. backend/ + dal/, ver Pendientes.md "Tres flujos de arquitectura"). Sin
plan.json legible, cae a una sola corrida sobre "backend" (comportamiento
previo, proyectos de un solo deployable).
"""
import json
import subprocess
import sys
from pathlib import Path

_BOOTSTRAP = "import json; from app.main import app; print(json.dumps(app.openapi()))"
_TIMEOUT_SEGUNDOS = 30


def _venvs_candidatos(carpeta: str) -> tuple[str, ...]:
    return (f"{carpeta}/venv/bin/python", "venv/bin/python", ".venv/bin/python")


def _venv_python(project_root: Path, carpeta: str) -> Path | None:
    for candidato in _venvs_candidatos(carpeta):
        ruta = project_root / candidato
        if ruta.exists():
            return ruta
    return None


def _carpetas_backend_del_plan(project_root: Path) -> list[str]:
    """
    Carpetas distintas (primer segmento de archivos_destino) entre los items
    tipo "backend" de plan.json, en orden de aparición. Sin plan.json legible
    (proyecto viejo, o corrida manual fuera de un proyecto del harness),
    devuelve ["backend"] -- mismo comportamiento que antes de soportar varios
    deployables.
    """
    ruta_plan = project_root / ".harness" / "config" / "plan.json"
    try:
        plan = json.loads(ruta_plan.read_text())
    except (OSError, json.JSONDecodeError):
        return ["backend"]

    carpetas = []
    for item in plan.get("items", []):
        if item.get("tipo") != "backend":
            continue
        archivos = item.get("archivos_destino") or []
        if not archivos:
            continue
        carpeta = archivos[0].split("/")[0]
        if carpeta not in carpetas:
            carpetas.append(carpeta)
    return carpetas or ["backend"]


def obtener_openapi(project_root: str, carpeta: str = "backend") -> dict:
    """Importa la app real de un deployable del proyecto destino (en SU venv) y devuelve su OpenAPI."""
    root = Path(project_root).resolve()
    interprete = _venv_python(root, carpeta)
    if interprete is None:
        raise RuntimeError(
            f"No se encontró un venv del proyecto destino ({', '.join(_venvs_candidatos(carpeta))}) "
            "-- no se puede importar la app real."
        )
    directorio_trabajo = root / carpeta if (root / carpeta).exists() else root
    try:
        resultado = subprocess.run(
            [str(interprete), "-c", _BOOTSTRAP],
            cwd=directorio_trabajo, capture_output=True, text=True, timeout=_TIMEOUT_SEGUNDOS,
        )
    except subprocess.TimeoutExpired as e:
        raise RuntimeError(f"Importar la app no terminó en {_TIMEOUT_SEGUNDOS}s") from e
    if resultado.returncode != 0:
        raise RuntimeError(f"No se pudo importar app.main.app:\n{resultado.stderr}")
    return json.loads(resultado.stdout)


def _resolver_ref(schema: dict, spec: dict) -> dict:
    if "$ref" in schema:
        nombre = schema["$ref"].split("/")[-1]
        return spec["components"]["schemas"][nombre]
    return schema


def _tipo_real(schema: dict) -> dict:
    """anyOf con 'null' (Optional[X] de Pydantic) -> el otro tipo, no null."""
    if "anyOf" in schema:
        for opcion in schema["anyOf"]:
            if opcion.get("type") != "null":
                return opcion
        return schema["anyOf"][0]
    return schema


def _valor_ejemplo(schema: dict, spec: dict, profundidad: int = 0) -> object:
    if profundidad > 6:
        return None
    schema = _tipo_real(_resolver_ref(schema, spec))

    if "example" in schema:
        return schema["example"]
    if "default" in schema and schema["default"] is not None:
        return schema["default"]
    if "enum" in schema and schema["enum"]:
        return schema["enum"][0]

    tipo = schema.get("type")
    if tipo == "string":
        return "texto-ejemplo" if schema.get("format") != "date-time" else "2026-01-01T00:00:00Z"
    if tipo == "integer":
        return 1
    if tipo == "number":
        return 1.0
    if tipo == "boolean":
        return True
    if tipo == "array":
        item_schema = schema.get("items", {})
        return [_valor_ejemplo(item_schema, spec, profundidad + 1)]
    if tipo == "object" or "properties" in schema:
        propiedades = schema.get("properties", {})
        return {
            nombre: _valor_ejemplo(sub, spec, profundidad + 1)
            for nombre, sub in propiedades.items()
        }
    return None


def _construir_curl(ruta: str, metodo: str, operacion: dict, spec: dict, base_url: str) -> str:
    parametros = operacion.get("parameters", [])
    ruta_final = ruta
    query = []
    for p in parametros:
        valor = _valor_ejemplo(p.get("schema", {}), spec)
        if p["in"] == "path":
            ruta_final = ruta_final.replace("{" + p["name"] + "}", str(valor))
        elif p["in"] == "query" and p.get("required", False):
            query.append(f"{p['name']}={valor}")

    url = base_url.rstrip("/") + ruta_final
    if query:
        url += "?" + "&".join(query)

    lineas = [f"curl -X {metodo.upper()} '{url}' \\"]

    if operacion.get("security"):
        lineas.append("  -H 'Authorization: Bearer <token>' \\")

    request_body = operacion.get("requestBody")
    content = request_body.get("content", {}) if request_body else {}

    if "application/json" in content:
        schema = content["application/json"]["schema"]
        cuerpo = _valor_ejemplo(schema, spec)
        lineas.append("  -H 'Content-Type: application/json' \\")
        lineas.append(f"  -d '{json.dumps(cuerpo, ensure_ascii=False)}'")
    elif "multipart/form-data" in content:
        # Form(...)/File(...) de FastAPI -- distinto de application/json, curl
        # usa -F por campo en vez de un body JSON único (bug real encontrado
        # 2026-08-27, primer proyecto con endpoints
        # multipart -- antes esto crasheaba con KeyError('application/json')).
        schema = _resolver_ref(content["multipart/form-data"]["schema"], spec)
        propiedades = schema.get("properties", {})
        campos = list(propiedades.items())
        for i, (nombre, sub_schema) in enumerate(campos):
            continua = " \\" if i < len(campos) - 1 else ""
            sub_schema_real = _tipo_real(_resolver_ref(sub_schema, spec))
            es_archivo = sub_schema_real.get("format") == "binary"
            if es_archivo:
                lineas.append(f"  -F '{nombre}=@/ruta/a/tu/archivo.txt'{continua}")
            else:
                valor = _valor_ejemplo(sub_schema, spec)
                lineas.append(f"  -F '{nombre}={valor}'{continua}")
        if not campos:
            lineas[-1] = lineas[-1].rstrip(" \\")
    else:
        lineas[-1] = lineas[-1].rstrip(" \\")

    return "\n".join(lineas)


def _respuesta_ejemplo(operacion: dict, spec: dict) -> tuple[str, dict] | None:
    for codigo, resp in operacion.get("responses", {}).items():
        if not codigo.startswith("2"):
            continue
        contenido = resp.get("content", {}).get("application/json", {}).get("schema")
        if contenido:
            return codigo, _valor_ejemplo(contenido, spec)
    return None


def generar_markdown(spec: dict, base_url: str) -> str:
    proyecto = spec.get("info", {}).get("title", "")
    bloques = [
        f"# API endpoints — {proyecto}\n\n"
        "> Generado automáticamente por `generate_api_docs.py` a partir del OpenAPI real de "
        "la app (`app.openapi()`) — no de una predicción de `plan.json`. Reflejar el código, "
        "no editar a mano; volver a correr el script si el código cambió.\n>\n"
        "> `<token>` es un placeholder — reemplazar por un access token real de "
        "`POST /auth/login`. Los valores de body/query son de ejemplo (tipo correcto, "
        "dato no necesariamente válido de negocio)."
    ]

    for ruta in sorted(spec.get("paths", {})):
        operaciones = spec["paths"][ruta]
        for metodo, operacion in operaciones.items():
            if metodo not in ("get", "post", "put", "patch", "delete"):
                continue
            titulo = operacion.get("summary") or f"{metodo.upper()} {ruta}"
            partes = [f"## {metodo.upper()} {ruta}", "", f"**{titulo}**", "", "```bash",
                      _construir_curl(ruta, metodo, operacion, spec, base_url), "```"]

            respuesta = _respuesta_ejemplo(operacion, spec)
            if respuesta:
                codigo, ejemplo = respuesta
                partes += ["", f"Respuesta {codigo} (ejemplo):", "```json",
                           json.dumps(ejemplo, ensure_ascii=False, indent=2), "```"]

            bloques.append("\n".join(partes))

    return "\n\n".join(bloques) + "\n"


def generar_docs(project_root: str, carpeta: str = "backend", base_url: str = "http://localhost:8000") -> tuple[Path, Path]:
    """
    Genera los dos artefactos para UN deployable. Nombre de archivo sin sufijo
    para "backend" (comportamiento/nombres previos, proyectos de un solo
    deployable no ven ningún cambio); con sufijo `-<carpeta>` para cualquier
    otra (ej. openapi-dal.json), para no pisar el de otro deployable del mismo
    proyecto.
    """
    spec = obtener_openapi(project_root, carpeta)
    # FastAPI no declara 'servers' salvo que se configure explícito (root_path,
    # FastAPI(servers=[...])) -- este proyecto no lo hace. Sin 'servers', Postman
    # importa las requests sin URL base precargada (hay que setearla a mano en
    # cada una). Se lo agregamos acá con el mismo base_url que ya usan los curls.
    spec["servers"] = [{"url": base_url}]

    root = Path(project_root).resolve()
    docs_dir = root / "docs"
    docs_dir.mkdir(parents=True, exist_ok=True)

    sufijo = "" if carpeta == "backend" else f"-{carpeta}"
    ruta_openapi = docs_dir / f"openapi{sufijo}.json"
    ruta_openapi.write_text(json.dumps(spec, ensure_ascii=False, indent=2))

    ruta_markdown = docs_dir / f"api-endpoints-curl{sufijo}.md"
    ruta_markdown.write_text(generar_markdown(spec, base_url))

    return ruta_openapi, ruta_markdown


def generar_docs_proyecto(project_root: str, base_url: str = "http://localhost:8000") -> list[tuple[str, Path, Path]]:
    """Corre generar_docs() una vez por cada carpeta backend distinta del plan."""
    root = Path(project_root).resolve()
    resultados = []
    for carpeta in _carpetas_backend_del_plan(root):
        ruta_openapi, ruta_markdown = generar_docs(project_root, carpeta, base_url)
        resultados.append((carpeta, ruta_openapi, ruta_markdown))
    return resultados


if __name__ == "__main__":
    project_root = sys.argv[1]
    base_url = sys.argv[2] if len(sys.argv) > 2 else "http://localhost:8000"
    for carpeta, ruta_openapi, ruta_markdown in generar_docs_proyecto(project_root, base_url):
        print(f"[{carpeta}] OpenAPI (Postman): {ruta_openapi}")
        print(f"[{carpeta}] Markdown (curl):   {ruta_markdown}")
