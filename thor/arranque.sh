#!/data/data/com.termux/files/usr/bin/bash
# Arranca el servidor de RooBot en segundo plano, sin abrir nada. Lo usa Termux:Boot al encender la Thor
# (~/.termux/boot/roobot), así la app instalada desde Chrome se abre directamente.
# Mantiene a Termux despierto (termux-wake-lock) para que Android no lo cierre; «roobot-parar» lo libera.
cd "$(dirname "$0")/.."
source thor/comun.sh
mkdir -p logs
roobot_vivo && exit 0
termux-wake-lock 2>/dev/null || true
nohup python -m vgclab.app --no-abrir --puerto 8765 > logs/app_termux.log 2>&1 &
