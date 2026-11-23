#!/data/data/com.termux/files/usr/bin/bash
# Trae a la Thor lo último que subiste a GitHub desde el PC: código, equipos, juez, experiencia y regulación.
# Si la regulación ha cambiado de versión de Showdown, la descarga y la compila. Necesita internet.
set -euo pipefail
cd "$(dirname "$0")/.."
bash thor/parar.sh >/dev/null 2>&1 || true

echo "[1/3] Descargando los cambios de GitHub..."
git pull --ff-only

echo "[2/3] Pokémon Showdown..."
bash thor/showdown.sh

echo "[3/3] Sprites que falten..."
python -m vgclab.sprites || echo "  No pude bajarlos ahora; la app los descargará cuando los necesite."

echo
python -m vgclab.regulacion
echo
echo "Listo. Abre RooBot con su icono."
[ -t 0 ] && read -r -p "Pulsa Intro para cerrar." _ || true
