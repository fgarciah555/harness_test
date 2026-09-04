"""
Docker check: valida Dockerfiles/docker-compose.yml de forma determinística
(sin LLM) para items `tipo: "infra"`, ANTES de gastar una llamada a
Compliance -- misma familia que format_check.py/frontend_check.py/
smoke_test.py. No es un agente, no pasa por AgentFileGuard ni
permissions.yaml. Ver schemas/plan.contract.md, sección "Docker check".

Compliance es un LLM que solo lee texto (no ejecuta nada, ver
agents/compliance.py) -- no puede verificar que una imagen realmente
construya, ni que un driver de sistema (ej. el ODBC de AS400, instalado vía
apt, no pip) haya quedado registrado con el nombre exacto que el código
espera. Ese tipo de verificación tiene que ser determinística, acá.

Dos campos opcionales nuevos en el item de plan.json, pensados para
generalizar más allá de este caso (cualquier proyecto que use este harness
puede necesitarlos, no solo Tesorería):

- `verificacion_runtime`: lista de {"comando": str, "debe_contener": str}.
  Tras un build exitoso de un Dockerfile del item, corre cada comando
  DENTRO de la imagen recién construida (`docker run --rm`) y confirma que
  su stdout contenga el texto esperado. Ejemplo real que motivó esto
  (Tesorería, DAL-AS400): el paquete `ibm-iaccess` instala el driver ODBC
  de IBM vía apt, pero nada garantiza que lo registre en odbcinst.ini bajo
  el mismo nombre que `AS400_DRIVER` espera en el código -- la imagen
  arrancaría igual, y solo fallaría en runtime al conectar. Con
  `{"comando": "odbcinst -q -d", "debe_contener": "iSeries Access ODBC Driver"}`
  ese desajuste se detecta en el build, no en producción.

- `smoke_http`: lista de {"servicio": str, "puerto_contenedor": int,
  "path": str}, solo para el item que declara el docker-compose.yml. Tras
  `docker compose build`, hace `docker compose up -d`, resuelve el puerto
  real publicado de cada servicio (`docker compose port`) y hace polling a
  http://localhost:<puerto><path> esperando 200. Si el servicio NO tiene
  puerto publicado (ej. un DAL que a propósito nunca se expone fuera de la
  red de compose), el chequeo corre DENTRO del contenedor vía `docker
  compose exec` + `python -c` (python siempre está disponible en una imagen
  FastAPI, no depende de curl/wget instalados) -- mismo criterio de
  verificación, sin violar el aislamiento de red del servicio. `docker
  compose down -v` corre siempre al final, pase lo que pase. Deliberadamente NO verifica
  conectividad a sistemas externos reales (OMS/MRET/PTH/AS400/LDAP): sin
  credenciales reales en este entorno sería un smoke test falso. Confirmar
  que el/los endpoint(s) de `path` no dependan de esos sistemas antes de
  declararlos acá (ver ejemplo real: GET /health de Tesorería devuelve
  {"status": "ok"} estático, no toca nada externo).
"""
import shutil
import socket
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

TIMEOUT_BUILD_SEGUNDOS = 600  # el más lento es el frontend Angular (npm install adentro del build)
TIMEOUT_RUN_SEGUNDOS = 30
TIMEOUT_COMPOSE_CONFIG_SEGUNDOS = 30
TIMEOUT_COMPOSE_UP_SEGUNDOS = 120
TIMEOUT_COMPOSE_DOWN_SEGUNDOS = 60
SMOKE_HTTP_TOPE_SEGUNDOS = 30
TRUNCAR_SALIDA = 4000


def _docker_disponible() -> tuple[bool, str]:
    if shutil.which("docker") is None:
        return False, "El binario 'docker' no está en el PATH."
    try:
        resultado = subprocess.run(
            ["docker", "info"], capture_output=True, text=True, timeout=15,
        )
    except (subprocess.TimeoutExpired, OSError) as e:
        return False, f"'docker info' no respondió: {e}"
    if resultado.returncode != 0:
        return False, f"'docker info' falló (¿el daemon está corriendo?): {(resultado.stdout + resultado.stderr)[-1000:]}"
    return True, ""


def _tag_para(item_id: str) -> str:
    return f"harness-check/{item_id.lower()}:latest"


def _dockerfiles_en(archivos_destino: list[str]) -> list[str]:
    return [a for a in archivos_destino if a.endswith("Dockerfile")]


def _compose_en(archivos_destino: list[str]) -> str | None:
    for a in archivos_destino:
        if a.endswith("docker-compose.yml") or a.endswith("compose.yaml") or a.endswith("compose.yml"):
            return a
    return None


def _build_imagen(root: Path, dockerfile_rel: str, tag: str) -> dict:
    dockerfile_path = root / dockerfile_rel
    contexto = dockerfile_path.parent
    try:
        resultado = subprocess.run(
            ["docker", "build", "-f", str(dockerfile_path), "-t", tag, str(contexto)],
            capture_output=True, text=True, timeout=TIMEOUT_BUILD_SEGUNDOS,
        )
    except subprocess.TimeoutExpired:
        return {"estado": "error", "detalle": f"docker build de {dockerfile_rel} no terminó en {TIMEOUT_BUILD_SEGUNDOS}s."}
    except OSError as e:
        return {"estado": "error", "detalle": f"no se pudo ejecutar docker build para {dockerfile_rel}: {e}"}
    if resultado.returncode != 0:
        return {"estado": "error", "detalle": f"docker build de {dockerfile_rel} falló:\n" + (resultado.stdout + resultado.stderr)[-TRUNCAR_SALIDA:]}
    return {"estado": "ok"}


def _verificar_runtime(tag: str, verificaciones: list[dict]) -> dict:
    for v in verificaciones:
        comando = v["comando"]
        esperado = v["debe_contener"]
        try:
            resultado = subprocess.run(
                ["docker", "run", "--rm", tag, "sh", "-c", comando],
                capture_output=True, text=True, timeout=TIMEOUT_RUN_SEGUNDOS,
            )
        except subprocess.TimeoutExpired:
            return {"estado": "error", "detalle": f"verificacion_runtime '{comando}' no terminó en {TIMEOUT_RUN_SEGUNDOS}s."}
        except OSError as e:
            return {"estado": "error", "detalle": f"no se pudo correr verificacion_runtime '{comando}': {e}"}
        salida = resultado.stdout + resultado.stderr
        if esperado not in salida:
            return {
                "estado": "error",
                "detalle": (
                    f"verificacion_runtime falló: el comando '{comando}' corrido dentro de la imagen "
                    f"NO contiene '{esperado}' en su salida real.\nSalida completa:\n{salida[-TRUNCAR_SALIDA:]}"
                ),
            }
    return {"estado": "ok"}


def _compose_config_y_build(root: Path, compose_rel: str) -> dict:
    compose_path = root / compose_rel
    try:
        resultado = subprocess.run(
            ["docker", "compose", "-f", str(compose_path), "config", "-q"],
            capture_output=True, text=True, timeout=TIMEOUT_COMPOSE_CONFIG_SEGUNDOS,
        )
    except subprocess.TimeoutExpired:
        return {"estado": "error", "detalle": f"'docker compose config' no terminó en {TIMEOUT_COMPOSE_CONFIG_SEGUNDOS}s."}
    except OSError as e:
        return {"estado": "error", "detalle": f"no se pudo ejecutar 'docker compose config': {e}"}
    if resultado.returncode != 0:
        return {"estado": "error", "detalle": "docker-compose.yml inválido (config -q falló):\n" + (resultado.stdout + resultado.stderr)[-TRUNCAR_SALIDA:]}

    try:
        resultado = subprocess.run(
            ["docker", "compose", "-f", str(compose_path), "build"],
            capture_output=True, text=True, timeout=TIMEOUT_BUILD_SEGUNDOS,
        )
    except subprocess.TimeoutExpired:
        return {"estado": "error", "detalle": f"'docker compose build' no terminó en {TIMEOUT_BUILD_SEGUNDOS}s."}
    except OSError as e:
        return {"estado": "error", "detalle": f"no se pudo ejecutar 'docker compose build': {e}"}
    if resultado.returncode != 0:
        return {"estado": "error", "detalle": "docker compose build falló:\n" + (resultado.stdout + resultado.stderr)[-TRUNCAR_SALIDA:]}
    return {"estado": "ok"}


def _puerto_publicado(compose_path: Path, servicio: str, puerto_contenedor: int) -> int | None:
    """
    None si el servicio no tiene ese puerto publicado al host. OJO: `docker
    compose port <servicio> <puerto>` NO falla (exit 0) ni devuelve stdout
    vacío en ese caso -- devuelve literalmente ":0" (confirmado real contra
    `dal`, que a propósito no tiene `ports`). Puerto 0 nunca es un puerto
    real publicado, así que se trata igual que "no encontrado".
    """
    try:
        resultado = subprocess.run(
            ["docker", "compose", "-f", str(compose_path), "port", servicio, str(puerto_contenedor)],
            capture_output=True, text=True, timeout=15,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    if resultado.returncode != 0 or not resultado.stdout.strip():
        return None
    # salida esperada: "0.0.0.0:32768" (o "[::]:32768"), o ":0" si no hay mapeo
    try:
        puerto = int(resultado.stdout.strip().rsplit(":", 1)[1])
    except (ValueError, IndexError):
        return None
    return puerto if puerto != 0 else None


def _esperar_http_ok(puerto: int, path: str, tope_segundos: int) -> str | None:
    """Devuelve None si respondió 200 a tiempo, o un mensaje de error si no."""
    url = f"http://localhost:{puerto}{path}"
    limite = time.monotonic() + tope_segundos
    ultimo_error = "sin intentos"
    while time.monotonic() < limite:
        try:
            with urllib.request.urlopen(url, timeout=5) as resp:
                if resp.status == 200:
                    return None
                ultimo_error = f"status {resp.status}"
        except (urllib.error.URLError, socket.timeout, ConnectionError) as e:
            ultimo_error = str(e)
        time.sleep(1)
    return f"{url} no respondió 200 en {tope_segundos}s (último intento: {ultimo_error})"


def _chequeo_http_interno_una_vez(compose_path: Path, servicio: str, puerto: int, path: str) -> str | None:
    """
    Para servicios SIN puerto publicado (ej. dal, nunca expuesto fuera de la
    red de compose por diseño -- ver plan.contract.md). Corre el chequeo
    DENTRO del contenedor, vía `docker compose exec` + `python -m urllib`
    (python siempre está disponible, es una imagen FastAPI -- no depende de
    curl/wget instalados). Devuelve None si respondió 200, o un mensaje de
    error si no.
    """
    codigo = (
        f"import urllib.request,sys; "
        f"sys.exit(0 if urllib.request.urlopen('http://localhost:{puerto}{path}', timeout=5).status == 200 else 1)"
    )
    try:
        resultado = subprocess.run(
            ["docker", "compose", "-f", str(compose_path), "exec", "-T", servicio, "python", "-c", codigo],
            capture_output=True, text=True, timeout=10,
        )
    except (subprocess.TimeoutExpired, OSError) as e:
        return str(e)
    if resultado.returncode != 0:
        return (resultado.stdout + resultado.stderr)[-500:] or f"exit code {resultado.returncode}"
    return None


def _esperar_http_ok_interno(compose_path: Path, servicio: str, puerto: int, path: str, tope_segundos: int) -> str | None:
    limite = time.monotonic() + tope_segundos
    ultimo_error = "sin intentos"
    while time.monotonic() < limite:
        error = _chequeo_http_interno_una_vez(compose_path, servicio, puerto, path)
        if error is None:
            return None
        ultimo_error = error
        time.sleep(1)
    return f"http://localhost:{puerto}{path} (dentro del contenedor '{servicio}') no respondió 200 en {tope_segundos}s (último intento: {ultimo_error})"


def _smoke_http(root: Path, compose_rel: str, specs: list[dict]) -> dict:
    compose_path = root / compose_rel
    try:
        subprocess.run(
            ["docker", "compose", "-f", str(compose_path), "up", "-d"],
            capture_output=True, text=True, timeout=TIMEOUT_COMPOSE_UP_SEGUNDOS, check=False,
        )
        errores = []
        for spec in specs:
            servicio, puerto_contenedor, path = spec["servicio"], spec["puerto_contenedor"], spec["path"]
            puerto_publicado = _puerto_publicado(compose_path, servicio, puerto_contenedor)
            if puerto_publicado is not None:
                error = _esperar_http_ok(puerto_publicado, path, SMOKE_HTTP_TOPE_SEGUNDOS)
            else:
                # Sin puerto publicado (ej. dal) -- chequeo interno vía
                # 'docker compose exec', no un fallo del smoke test.
                error = _esperar_http_ok_interno(compose_path, servicio, puerto_contenedor, path, SMOKE_HTTP_TOPE_SEGUNDOS)
            if error:
                errores.append(f"{servicio}: {error}")
        if errores:
            return {"estado": "error", "detalle": "smoke_http falló:\n" + "\n".join(errores)}
        return {"estado": "ok"}
    finally:
        subprocess.run(
            ["docker", "compose", "-f", str(compose_path), "down", "-v"],
            capture_output=True, text=True, timeout=TIMEOUT_COMPOSE_DOWN_SEGUNDOS, check=False,
        )


def verificar(project_root: str, item: dict) -> dict:
    """
    Devuelve uno de:
      {"estado": "ok"}
      {"estado": "error", "detalle": "..."}            -- rechazo determinístico real
      {"estado": "motor_inalcanzable", "motivo": "..."} -- Docker no disponible en
                                                            este entorno; el loop se
                                                            pausa sin gastar un
                                                            reintento de Executor.
    """
    root = Path(project_root).resolve()
    archivos = item.get("archivos_destino", [])
    item_id = item["id"]

    dockerfiles = _dockerfiles_en(archivos)
    compose_rel = _compose_en(archivos)
    if not dockerfiles and not compose_rel:
        # Item tipo:"infra" sin ningún Dockerfile/docker-compose.yml en
        # archivos_destino (ej. un script de arranque local) -- no hay nada
        # que este check deba construir, así que no tiene sentido exigir
        # Docker disponible para él.
        return {"estado": "ok"}

    disponible, motivo = _docker_disponible()
    if not disponible:
        return {"estado": "motor_inalcanzable", "motivo": f"Docker no disponible: {motivo}"}

    for dockerfile_rel in dockerfiles:
        tag = _tag_para(item_id)
        resultado = _build_imagen(root, dockerfile_rel, tag)
        if resultado["estado"] != "ok":
            return resultado

        verificaciones = item.get("verificacion_runtime", [])
        if verificaciones:
            resultado = _verificar_runtime(tag, verificaciones)
            if resultado["estado"] != "ok":
                return resultado

    if compose_rel:
        resultado = _compose_config_y_build(root, compose_rel)
        if resultado["estado"] != "ok":
            return resultado

        smoke_specs = item.get("smoke_http", [])
        if smoke_specs:
            resultado = _smoke_http(root, compose_rel, smoke_specs)
            if resultado["estado"] != "ok":
                return resultado

    return {"estado": "ok"}
