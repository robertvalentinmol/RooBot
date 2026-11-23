#!/data/data/com.termux/files/usr/bin/bash
# Abre RooBot: arranca el servidor si no está en marcha y abre la app en el navegador.
# Deja esta ventana de Termux abierta (por ejemplo en la pantalla de abajo) mientras entrenas.
cd "$(dirname "$0")/.."
URL="http://127.0.0.1:8765"
mkdir -p logs
termux-wake-lock 2>/dev/null || true     # que Android no duerma a Termux con la app abierta

PID=""
if ! curl -s -o /dev/null "$URL/"; then
  echo "Arrancando RooBot..."
  python -m vgclab.app --no-abrir --puerto 8765 > logs/app_termux.log 2>&1 &
  PID=$!
  for _ in $(seq 1 90); do
    sleep 1
    curl -s -o /dev/null "$URL/" && break
    kill -0 "$PID" 2>/dev/null || break
  done
fi

if curl -s -o /dev/null "$URL/"; then
  termux-open-url "$URL"
  echo "RooBot está abierto en $URL"
  echo "Para cerrarlo: el acceso «RooBot - parar» o Ctrl+C aquí."
  [ -n "$PID" ] && wait "$PID"
else
  echo "RooBot no ha arrancado. Últimas líneas de logs/app_termux.log:"
  tail -n 25 logs/app_termux.log
  read -r -p "Pulsa Intro para cerrar." _
fi
termux-wake-unlock 2>/dev/null || true
