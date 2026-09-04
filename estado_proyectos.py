"""
Estado real de todos los proyectos que el harness está migrando -- lee
config/proyectos.yaml (registro manual: nombre + ruta + descripción corta)
y, para cada uno, calcula el estado de sus items vía
orchestrator.calcular_estados() -- mismo mecanismo que ya usa
`orchestrator.py <proyecto> --status`, sin duplicar el algoritmo de
"estado efectivo" (ver schemas/plan.contract.md). No guarda ningún
snapshot: cada corrida relee el .harness/ real de cada proyecto.

Uso:
    python estado_proyectos.py                    # resumen de todos los proyectos
    python estado_proyectos.py --detalle "<nombre>" # detalle item por item de uno
"""
import argparse
import yaml
from pathlib import Path

from orchestrator import calcular_estados, _cargar_plan, _leer_eventos_executor

REGISTRO = Path(__file__).parent / "config" / "proyectos.yaml"


def cargar_registro() -> list[dict]:
    if not REGISTRO.exists():
        return []
    data = yaml.safe_load(REGISTRO.read_text()) or {}
    return data.get("proyectos", [])


def _ultima_actividad(root: Path) -> str | None:
    eventos = _leer_eventos_executor(root)
    timestamps = [e["timestamp"] for lista in eventos.values() for e in lista]
    return max(timestamps) if timestamps else None


def resumen_proyecto(proyecto: dict) -> dict:
    """
    Nunca lanza -- un proyecto con ruta rota, sin plan.json (todavía no
    iniciado) o con plan.json inválido es tan válido de reportar como uno
    en marcha; el registro sirve para ver TODO de un vistazo, no solo lo
    que ya anda.
    """
    ruta = Path(proyecto["ruta"])
    resultado = {"nombre": proyecto["nombre"], "ruta": str(ruta)}

    if not ruta.exists():
        resultado["estado"] = "ruta no encontrada"
        return resultado

    try:
        plan = _cargar_plan(ruta)
    except FileNotFoundError:
        resultado["estado"] = "sin plan.json (no iniciado)"
        return resultado
    except ValueError as e:
        resultado["estado"] = f"plan.json inválido -- {e}"
        return resultado

    estados = calcular_estados(str(ruta))
    conteo: dict[str, int] = {}
    for estado in estados.values():
        conteo[estado] = conteo.get(estado, 0) + 1

    resultado["total"] = len(estados)
    resultado["completados"] = conteo.get("completado", 0)
    resultado["conteo"] = conteo
    resultado["arquitectura"] = plan.get("metadata", {}).get("arquitectura_objetivo", {})
    resultado["ultima_actividad"] = _ultima_actividad(ruta)
    return resultado


def linea_resumen(r: dict) -> str:
    if "total" not in r:
        return f"{r['nombre']:42s} {r['estado']}"

    porcentaje = round(100 * r["completados"] / r["total"]) if r["total"] else 0
    otros = {k: v for k, v in r["conteo"].items() if k != "completado" and v > 0}
    detalle_otros = f" ({', '.join(f'{v} {k}' for k, v in sorted(otros.items()))})" if otros else ""
    actividad = r["ultima_actividad"] or "sin actividad registrada"
    return (
        f"{r['nombre']:42s} {r['completados']}/{r['total']} completado "
        f"({porcentaje}%){detalle_otros}  -- última actividad: {actividad}"
    )


def main():
    parser = argparse.ArgumentParser(description="Estado de todos los proyectos registrados en config/proyectos.yaml")
    parser.add_argument("--detalle", help="Detalle item por item de un proyecto puntual (nombre exacto del registro)")
    args = parser.parse_args()

    registro = cargar_registro()
    if not registro:
        print(f"No hay proyectos registrados todavía -- agregalos en {REGISTRO}")
        return

    if args.detalle:
        proyecto = next((p for p in registro if p["nombre"] == args.detalle), None)
        if proyecto is None:
            print(f"No encontré '{args.detalle}' en el registro. Nombres disponibles:")
            for p in registro:
                print(f"  - {p['nombre']}")
            return
        estados = calcular_estados(proyecto["ruta"])
        for item_id, estado in estados.items():
            print(f"  {item_id:20s} {estado}")
        return

    for proyecto in registro:
        r = resumen_proyecto(proyecto)
        print(linea_resumen(r))


if __name__ == "__main__":
    main()
