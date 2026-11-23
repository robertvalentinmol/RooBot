# Funciones comunes de los scripts de la Thor (se cargan con «source»). Sin curl: sólo Python.
URL="http://127.0.0.1:8765"

# ¿Responde el servidor de RooBot?
roobot_vivo() {
  python -c "import urllib.request; urllib.request.urlopen('$URL/api/version', timeout=2)" 2>/dev/null
}

# Pide al servidor que se cierre (no falla si no estaba abierto).
roobot_cerrar() {
  python -c "import urllib.request; urllib.request.urlopen(urllib.request.Request('$URL/api/shutdown', data=b'{}', method='POST'), timeout=3)" 2>/dev/null
}
