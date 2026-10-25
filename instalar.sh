#!/usr/bin/env bash
# RooBot - instalación en Linux / macOS
# Requisitos: Node.js 20+, git, Python 3.10+
set -euo pipefail
cd "$(dirname "$0")"
COMMIT="${VGCLAB_SHOWDOWN_COMMIT:-d601443}"
PY="${PYTHON:-python3}"

command -v node >/dev/null || { echo "Falta Node.js 20+ (https://nodejs.org)"; exit 1; }
command -v git  >/dev/null || { echo "Falta git"; exit 1; }
command -v "$PY" >/dev/null || { echo "Falta Python 3.10+"; exit 1; }

echo "[1/4] Descargando Pokémon Showdown..."
if [ ! -d server/pokemon-showdown ]; then
  git clone --filter=blob:none https://github.com/smogon/pokemon-showdown.git server/pokemon-showdown
fi
pushd server/pokemon-showdown >/dev/null
git checkout -q "$COMMIT" 2>/dev/null || echo "  Aviso: no pude fijar la versión probada $COMMIT; uso la última."

echo "[2/4] Compilando el simulador de Showdown (lo usa el motor de la app)..."
npm install --omit=optional
node build
popd >/dev/null

echo "[3/4] Dependencias de Python..."
"$PY" -m pip install -r requirements.txt

echo "[4/4] Comprobando el motor..."
echo '{"id":1,"cmd":"ping"}' | node engine/engine.js
echo
echo "Listo. Abre la app con ./app.sh"
