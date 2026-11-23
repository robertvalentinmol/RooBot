#!/data/data/com.termux/files/usr/bin/bash
# Cierra RooBot si está abierto.
URL="http://127.0.0.1:8765"
if curl -s -o /dev/null -X POST -d '{}' "$URL/api/shutdown"; then
  echo "RooBot cerrado."
else
  echo "RooBot no estaba abierto."
fi
termux-wake-unlock 2>/dev/null || true
