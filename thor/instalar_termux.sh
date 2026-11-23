#!/data/data/com.termux/files/usr/bin/bash
# RooBot en Android (AYN Thor) con Termux. Se ejecuta UNA vez, después de clonar el repositorio:
#   bash ~/RooBot/thor/instalar_termux.sh
# Instala Python, Node.js y numpy, descarga y compila la versión de Pokémon Showdown de regulacion.json,
# baja los sprites y crea los accesos directos de Termux:Widget (RooBot, RooBot - parar, RooBot - actualizar).
set -euo pipefail
cd "$(dirname "$0")/.."
RAIZ="$(pwd)"

case "${PREFIX:-}" in
  *com.termux*) ;;
  *) echo "Aviso: esto no parece Termux. Sigo igualmente." ;;
esac

echo "[1/6] Paquetes de Termux (Python, Node.js, git, numpy)..."
pkg update -y
pkg install -y python nodejs-lts git curl python-numpy

echo "[2/6] Dependencias de Python..."
python -m pip install --disable-pip-version-check pyyaml   # numpy ya viene de pkg (con pip tardaría muchísimo)

echo "[3/6] Pokémon Showdown (la versión probada de regulacion.json)..."
bash thor/showdown.sh

echo "[4/6] Sprites de los equipos (sólo los que falten)..."
python -m vgclab.sprites || echo "  No pude bajarlos ahora; la app los descargará cuando los necesite."

echo "[5/6] Accesos directos para Termux:Widget..."
mkdir -p "$HOME/.shortcuts/icons"
printf '#!/data/data/com.termux/files/usr/bin/bash\nexec bash "%s/thor/abrir.sh"\n' "$RAIZ" > "$HOME/.shortcuts/RooBot"
printf '#!/data/data/com.termux/files/usr/bin/bash\nexec bash "%s/thor/parar.sh"\n' "$RAIZ" > "$HOME/.shortcuts/RooBot - parar"
printf '#!/data/data/com.termux/files/usr/bin/bash\nexec bash "%s/thor/actualizar.sh"\n' "$RAIZ" > "$HOME/.shortcuts/RooBot - actualizar"
chmod 700 "$HOME/.shortcuts/RooBot" "$HOME/.shortcuts/RooBot - parar" "$HOME/.shortcuts/RooBot - actualizar"
cp web/img/roobot.png "$HOME/.shortcuts/icons/RooBot.png"

echo "[6/6] Comprobando el motor..."
echo '{"id":1,"cmd":"ping"}' | node engine/engine.js
echo
echo "Listo. Añade el widget de Termux:Widget a la pantalla de inicio y toca «RooBot»."
