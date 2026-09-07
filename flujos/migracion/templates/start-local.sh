#!/usr/bin/env bash

# Plantilla maestra del harness (Harness/templates/start-local.sh) para
# arrancar un proyecto migrado SIN Docker: dal (puerto 8001) + backend
# (puerto 8000) + frontend (ng serve, puerto 4200 default), cada uno con su
# propio venv/node_modules. Autodetecta qué deployables existen en el
# proyecto destino (dal/, backend/, frontend/) -- no falla si alguno no
# aplica, simplemente lo omite.
#
# Al copiar esta plantilla a un proyecto destino: si ese proyecto necesita
# un chequeo de pre-arranque específico (ej. driver ODBC de un sistema
# externo, como AS400), agregarlo como una función aparte
# ANTES de la sección "Lanzamiento de procesos" -- no tocar la lógica de
# trap/cleanup, que es genérica.
#
# Ver .agents/rules/local-development.md, sección "empaquetado / start-local.sh".

set -e

if [ -d "dal" ] && [ ! -d "dal/venv" ]; then
  echo "ERROR: falta dal/venv."
  echo "Crear con: cd dal && python -m venv venv && ./venv/bin/pip install -r requirements.txt"
  exit 1
fi

if [ -d "backend" ] && [ ! -d "backend/venv" ]; then
  echo "ERROR: falta backend/venv."
  echo "Crear con: cd backend && python -m venv venv && ./venv/bin/pip install -r requirements.txt"
  exit 1
fi

if [ -d "frontend" ] && [ ! -d "frontend/node_modules" ]; then
  echo "ERROR: falta frontend/node_modules."
  echo "Crear con: cd frontend && npm install"
  exit 1
fi

if ! mkdir -p .local-logs; then
  echo "ERROR: no se pudo crear .local-logs/"
  exit 1
fi

if [ -f .gitignore ] && ! grep -qxF '.local-logs/' .gitignore; then
  echo '.local-logs/' >> .gitignore
fi

DAL_PID=""
BACKEND_PID=""
FRONTEND_PID=""
CLEANUP_DONE=0

cleanup() {
  if [ "${CLEANUP_DONE:-0}" = "1" ]; then
    return 0
  fi
  CLEANUP_DONE=1

  local pid
  for pid in "${DAL_PID}" "${BACKEND_PID}" "${FRONTEND_PID}"; do
    if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
      kill "$pid" 2>/dev/null
    fi
  done

  echo "Procesos locales detenidos."
  exit 0
}

trap cleanup INT TERM EXIT

echo "Postgres debe estar accesible en el DATABASE_URL configurado (dal/.env u otro deployable) -- este script no lo levanta."

if [ -d "dal" ]; then
  ( cd dal && exec ./venv/bin/uvicorn app.main:app --reload --port 8001 ) > .local-logs/dal.log 2>&1 &
  DAL_PID=$!
  echo "DAL: http://localhost:8001"
fi

if [ -d "backend" ]; then
  ( cd backend && exec ./venv/bin/uvicorn app.main:app --reload --port 8000 ) > .local-logs/backend.log 2>&1 &
  BACKEND_PID=$!
  echo "Backend: http://localhost:8000/docs"
fi

if [ -d "frontend" ]; then
  ( cd frontend && exec npm start ) > .local-logs/frontend.log 2>&1 &
  FRONTEND_PID=$!
  echo "Frontend: http://localhost:4200"
fi

echo "Logs: .local-logs/*.log"
echo "Ctrl+C para detener."

wait
