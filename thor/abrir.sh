#!/data/data/com.termux/files/usr/bin/bash
# Abre RooBot desde Termux: arranca el servidor si no está en marcha y abre la app en el navegador.
cd "$(dirname "$0")/.."
source thor/comun.sh
mkdir -p logs

if ! roobot_vivo; then
  echo "Arrancando RooBot..."
  bash thor/arranque.sh
  for _ in $(seq 1 90); do
    sleep 1
    roobot_vivo && break
  done
fi

if roobot_vivo; then
  termux-open-url "$URL"
  echo "RooBot está en marcha en $URL"
  echo "Puedes cerrar esta ventana: el servidor sigue. Para pararlo: roobot-parar"
else
  echo "RooBot no ha arrancado. Últimas líneas de logs/app_termux.log:"
  tail -n 25 logs/app_termux.log
  termux-wake-unlock 2>/dev/null || true
  read -r -p "Pulsa Intro para cerrar." _
fi
