"""Rutas y utilidades comunes de RooBot."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load_teams(paths):
    """Textos de equipos (.txt con formato de exportación de Showdown) de archivos o carpetas (con subcarpetas)."""
    texts = []
    for p in paths:
        p = Path(p)
        files = sorted(p.rglob("*.txt")) if p.is_dir() else [p]
        for f in files:
            t = f.read_text(encoding="utf-8").strip()
            if t:
                texts.append(t)
    if not texts:
        raise SystemExit("No hay equipos (.txt con formato de exportación de Showdown)")
    return texts
