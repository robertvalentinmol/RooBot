#!/data/data/com.termux/files/usr/bin/bash
# RooBot en Android (AYN Thor) con Termux. Se ejecuta después de clonar el repositorio, y se puede repetir:
#   bash ~/RooBot/thor/instalar_termux.sh
# Actualiza Termux entero, instala Python, Node.js y numpy, compila la versión de Pokémon Showdown de
# regulacion.json, baja sprites, deja RooBot arrancando solo con Termux:Boot y crea los atajos.
set -euo pipefail
cd "$(dirname "$0")/.."
RAIZ="$(pwd)"
SH=/data/data/com.termux/files/usr/bin/bash

case "${PREFIX:-}" in
  *com.termux*) ;;
  *) echo "Aviso: esto no parece Termux. Sigo igualmente." ;;
esac

echo "[1/6] Actualizando Termux entero e instalando Python, Node.js, git y numpy..."
# Con apt y no con pkg: si curl está roto (paquetes a medio actualizar), pkg no funciona y apt sí.
# Primero se actualiza TODO; instalar paquetes nuevos sobre bibliotecas viejas rompe node y curl.
apt-get update
apt-get -y -o Dpkg::Options::=--force-confold full-upgrade
apt-get -y -o Dpkg::Options::=--force-confold install python nodejs-lts git python-numpy

echo "[2/6] Dependencias de Python..."
python -m pip install --disable-pip-version-check pyyaml   # numpy ya viene del sistema (con pip tardaría muchísimo)

echo "[3/6] Pokémon Showdown (la versión probada de regulacion.json)..."
bash thor/showdown.sh

echo "[4/6] Sprites de los equipos (sólo los que falten)..."
python -m vgclab.sprites || echo "  No pude bajarlos ahora; la app los descargará cuando los necesite."

echo "[5/6] Arranque automático, accesos y atajos..."
# Termux:Boot (app de F-Droid): arranca el servidor al encender la Thor
mkdir -p "$HOME/.termux/boot"
printf '#!%s\nexec bash "%s/thor/arranque.sh"\n' "$SH" "$RAIZ" > "$HOME/.termux/boot/roobot"
chmod 700 "$HOME/.termux/boot/roobot"
# Termux:Widget (si el launcher lo admite)
mkdir -p "$HOME/.shortcuts/icons"
printf '#!%s\nexec bash "%s/thor/abrir.sh"\n' "$SH" "$RAIZ" > "$HOME/.shortcuts/RooBot"
printf '#!%s\nexec bash "%s/thor/parar.sh"\n' "$SH" "$RAIZ" > "$HOME/.shortcuts/RooBot - parar"
printf '#!%s\nexec bash "%s/thor/actualizar.sh"\n' "$SH" "$RAIZ" > "$HOME/.shortcuts/RooBot - actualizar"
cp web/img/roobot.png "$HOME/.shortcuts/icons/RooBot.png"
chmod -R 700 "$HOME/.shortcuts"
# Atajos para escribir en Termux: roobot, roobot-parar, roobot-actualizar
touch "$HOME/.bashrc"
grep -q "alias roobot=" "$HOME/.bashrc" || cat >> "$HOME/.bashrc" <<ALIAS
alias roobot='bash "$RAIZ/thor/abrir.sh"'
alias roobot-parar='bash "$RAIZ/thor/parar.sh"'
alias roobot-actualizar='bash "$RAIZ/thor/actualizar.sh"'
ALIAS

echo "[6/6] Comprobando el motor y arrancando RooBot..."
echo '{"id":1,"cmd":"ping"}' | node engine/engine.js
bash thor/arranque.sh
echo
echo "Listo. RooBot está en marcha en http://127.0.0.1:8765"
echo "Para tenerlo como app: ábrelo en Chrome, menú ⋮ → «Instalar aplicación» (o «Añadir a pantalla de inicio»)."
echo "Para que arranque solo al encender la Thor: instala Termux:Boot desde F-Droid y ábrelo una vez."
