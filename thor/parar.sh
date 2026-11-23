#!/data/data/com.termux/files/usr/bin/bash
# Cierra RooBot si está abierto y deja dormir a la Thor otra vez.
cd "$(dirname "$0")/.."
source thor/comun.sh
if roobot_cerrar; then
  echo "RooBot cerrado."
else
  echo "RooBot no estaba abierto."
fi
termux-wake-unlock 2>/dev/null || true
