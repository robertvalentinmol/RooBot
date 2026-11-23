#!/data/data/com.termux/files/usr/bin/bash
# Deja Pokémon Showdown en la versión probada que marca regulacion.json (y lo compila si cambia).
set -euo pipefail
cd "$(dirname "$0")/.."
SD=server/pokemon-showdown
QUIERO="$(python -m vgclab.regulacion --valor showdown 2>/dev/null || true)"

if [ ! -d "$SD/.git" ]; then
  mkdir -p server
  git clone --filter=blob:none https://github.com/smogon/pokemon-showdown.git "$SD"
fi
TENGO="$(git -C "$SD" rev-parse --short=12 HEAD)"
if [ -n "$QUIERO" ] && [ -d "$SD/dist" ] && { [[ "$TENGO" == "$QUIERO"* ]] || [[ "$QUIERO" == "$TENGO"* ]]; }; then
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
(cd "$SD" && npm install --omit=optional && node build)
