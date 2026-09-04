#!/usr/bin/env bash

# Plantilla maestra del harness (Harness/templates/empaquetar.sh) para
# generar un .zip entregable de un proyecto migrado a un equipo cliente que
# no conoce a fondo Docker/el harness: excluye venv/node_modules/dist/
# .angular/__pycache__/.pytest_cache (regenerables) y .harness/.git/
# .local-logs (no son la app, .harness/ puede tener datos internos en texto
# plano). Incluye .agents/ y AGENTS.md (le sirven al equipo receptor
# también) y el .env.example de cada deployable -- NUNCA el .env real (ver
# .agents/rules/local-development.md, "'.env' real vs '.env.example'").
#
# Se dispara solo a mano, nunca automático. Ver
# .agents/rules/local-development.md, sección "empaquetado / start-local.sh".

set -e

if ! command -v zip >/dev/null 2>&1; then
  echo "Error: zip no esta instalado. Instala zip: apt install zip" >&2
  exit 1
fi

NOMBRE_PROYECTO="$(basename "$(pwd)")"
TIMESTAMP="$(date +%Y%m%d-%H%M%S)"
DIR_PADRE="$(cd .. && pwd)"
ZIP_DESTINO="${DIR_PADRE}/${NOMBRE_PROYECTO}-${TIMESTAMP}.zip"

cd ..
zip -r "$ZIP_DESTINO" "$NOMBRE_PROYECTO" \
  -x "$NOMBRE_PROYECTO/*/venv/*" \
  -x "$NOMBRE_PROYECTO/*/node_modules/*" \
  -x "$NOMBRE_PROYECTO/*/dist/*" \
  -x "$NOMBRE_PROYECTO/*/.angular/*" \
  -x "$NOMBRE_PROYECTO/*/__pycache__/*" \
  -x "$NOMBRE_PROYECTO/*/*/__pycache__/*" \
  -x "$NOMBRE_PROYECTO/*/.pytest_cache/*" \
  -x "$NOMBRE_PROYECTO/.local-logs/*" \
  -x "$NOMBRE_PROYECTO/.harness/*" \
  -x "$NOMBRE_PROYECTO/.git/*" \
  -x "$NOMBRE_PROYECTO/*/.env" \
  -x "$NOMBRE_PROYECTO/.env"

LISTADO="$(unzip -l "$ZIP_DESTINO")"

if printf '%s\n' "$LISTADO" | grep -E '(^|/)\.env([[:space:]]|$)'; then
  echo "Error: se detecto un .env real dentro del paquete: $ZIP_DESTINO" >&2
  rm -f "$ZIP_DESTINO"
  exit 1
fi

# Cualquier deployable con .env real en el proyecto original (dal/.env,
# backend/.env, etc.) tiene que aparecer con su .env.example en el paquete.
cd "$NOMBRE_PROYECTO"
while IFS= read -r -d '' env_real; do
  deployable_dir="$(dirname "$env_real")"
  deployable_dir="${deployable_dir#./}"
  ejemplo="${deployable_dir}/.env.example"
  if [ ! -f "$ejemplo" ]; then
    echo "WARNING: $deployable_dir tiene .env pero no .env.example -- no quedará en el paquete" >&2
    continue
  fi
  if ! printf '%s\n' "$LISTADO" | grep -qF "$NOMBRE_PROYECTO/$ejemplo"; then
    echo "WARNING: no se encontro $ejemplo en el paquete" >&2
  fi
done < <(find . -mindepth 2 -maxdepth 2 -name ".env" -not -path "./.harness/*" -print0)
cd ..

if printf '%s\n' "$LISTADO" | grep -E '/(venv|node_modules)/'; then
  echo "Error: se detecto venv o node_modules dentro del paquete: $ZIP_DESTINO" >&2
  rm -f "$ZIP_DESTINO"
  exit 1
fi

echo "Paquete generado: $ZIP_DESTINO"
du -h "$ZIP_DESTINO"
