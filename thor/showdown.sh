#!/data/data/com.termux/files/usr/bin/bash
# Deja Pokémon Showdown en la versión probada que marca regulacion.json (y lo compila si cambia).
set -euo pipefail
cd "$(dirname "$0")/.."
SD=server/pokemon-showdown
QUIERO="$(python -m vgclab.regulacion --valor showdown 2>/dev/null || true)"

# Una descarga a medias (sin .git válido) se borra y se descarga de nuevo
if [ -d "$SD" ] && ! git -C "$SD" rev-parse HEAD >/dev/null 2>&1; then
  echo "  La descarga anterior de Showdown estaba a medias: la repito."
  rm -rf "$SD"
fi
if [ ! -d "$SD" ]; then
  mkdir -p server
  git clone --filter=blob:none https://github.com/smogon/pokemon-showdown.git "$SD"
fi
TENGO="$(git -C "$SD" rev-parse --short=12 HEAD)"
if [ -n "$QUIERO" ] && [ -f "$SD/dist/sim/index.js" ] && { [[ "$TENGO" == "$QUIERO"* ]] || [[ "$QUIERO" == "$TENGO"* ]]; }; then
  echo "  Showdown ya está en la versión $QUIERO."
  exit 0
fi
echo "  Cambiando Showdown a la versión ${QUIERO:-más reciente}..."
git -C "$SD" fetch -q origin master
if [ -n "$QUIERO" ]; then
  git -C "$SD" checkout -q "$QUIERO"
else
  git -C "$SD" checkout -q FETCH_HEAD
fi
# Con los paquetes opcionales: esbuild necesita su pieza para Android (@esbuild/android-arm64)
(cd "$SD" && npm install --include=optional && node build)
if [ ! -f "$SD/dist/sim/index.js" ]; then
  echo "ERROR: Showdown no se ha compilado (falta dist/sim). Mira los mensajes de arriba."
  exit 1
fi
