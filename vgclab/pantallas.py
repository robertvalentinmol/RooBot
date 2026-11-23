"""Modo doble pantalla: buzón entre la ventana de arriba (el campo) y la de abajo (los controles).

La ventana de abajo ejecuta la app de siempre y publica aquí una copia del campo; la de arriba la recibe por SSE y
devuelve sus toques, que llegan a la de abajo por el mismo camino. Cada ventana sabe si la otra está conectada.
"""
from __future__ import annotations

import json
import queue
import threading
from typing import Any, Callable, Dict, List, Optional

ROLES = ("arriba", "abajo")


class Suscripcion:
    """Una ventana conectada: sus eventos esperan en una cola hasta que los lee."""

    def __init__(self, buzon: "Buzon", rol: str):
        self.buzon, self.rol = buzon, rol
        self._cola: "queue.Queue[dict]" = queue.Queue()
        self._cerrada = False

    def poner(self, evento: dict) -> None:
        self._cola.put(evento)

    def siguiente(self, timeout: float) -> Optional[dict]:
        try:
            return self._cola.get(timeout=timeout)
        except queue.Empty:
            return None

    def cerrar(self) -> None:
        if not self._cerrada:
            self._cerrada = True
            self.buzon._baja(self)


class Buzon:
    def __init__(self):
        self._lock = threading.Lock()
        self._subs: Dict[str, List[Suscripcion]] = {r: [] for r in ROLES}
        self._campo: Optional[Dict[str, Any]] = None
        self._v = 0

    # ---- lo que manda cada ventana
    def publicar_campo(self, datos: dict) -> int:
        with self._lock:
            self._v += 1
            self._campo = {"html": datos.get("html", ""), "visible": bool(datos.get("visible", True)),
                           "t": datos.get("t", 0), "v": self._v}
            ev = {"tipo": "campo", **self._campo}
            for s in self._subs["arriba"]:
                s.poner(ev)
            return self._v

    def ultimo_campo(self) -> Optional[dict]:
        with self._lock:
            return dict(self._campo) if self._campo else None

    def publicar_toque(self, toque: dict) -> None:
        with self._lock:
            for s in self._subs["abajo"]:
                s.poner({"tipo": "toque", **toque})

    # ---- conexiones
    def suscribir(self, rol: str) -> Suscripcion:
        if rol not in ROLES:
            raise ValueError(f"rol desconocido: {rol!r}")
        s = Suscripcion(self, rol)
        with self._lock:
            self._subs[rol].append(s)
            if rol == "arriba":
                if self._campo:
                    s.poner({"tipo": "campo", **self._campo})
                s.poner({"tipo": "abajo", "conectada": bool(self._subs["abajo"])})
            else:
                s.poner({"tipo": "arriba", "conectada": bool(self._subs["arriba"])})
            self._avisar_al_otro(rol)
        return s

    def conectados(self, rol: str) -> int:
        with self._lock:
            return len(self._subs[rol])

    def _baja(self, s: Suscripcion) -> None:
        with self._lock:
            if s in self._subs[s.rol]:
                self._subs[s.rol].remove(s)
                self._avisar_al_otro(s.rol)

    def _avisar_al_otro(self, rol: str) -> None:
        """Con el lock tomado: dice a las ventanas del otro lado si queda alguna de este."""
        otro = "abajo" if rol == "arriba" else "arriba"
        ev = {"tipo": rol, "conectada": bool(self._subs[rol])}
        for o in self._subs[otro]:
            o.poner(ev)


def servir_eventos(buzon: Buzon, rol: str, escribir: Callable[[bytes], None], seguir: Callable[[], bool],
                   latido: float = 15.0) -> None:
    """Flujo SSE de una ventana: un «data:» por evento y un comentario de latido si no pasa nada.
    Termina cuando `seguir()` es falso o el navegador cierra la conexión; siempre da de baja la ventana."""
    s = buzon.suscribir(rol)
    try:
        while seguir():
            ev = s.siguiente(latido)
            if ev is None:
                escribir(b": latido\n\n")
            else:
                escribir(b"data: " + json.dumps(ev, ensure_ascii=False).encode("utf-8") + b"\n\n")
    except (BrokenPipeError, ConnectionError, OSError):
        pass
    finally:
        s.cerrar()
