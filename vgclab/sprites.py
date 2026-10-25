"""Sprites de Showdown guardados en tu PC (web/sprites) para que la app funcione sin internet.

- La app pide los sprites a su propio servidor (/sprites/...). Si están en web/sprites se sirven de ahí;
  si no, el servidor intenta bajarlos de play.pokemonshowdown.com, los guarda y los sirve (así la
  carpeta se va llenando sola mientras usas la app con internet).
- `python -m vgclab.sprites` (mantenimiento.bat, opción 2) descarga de una vez los de todos tus equipos, los tipos y
  las categorías, para tenerlo todo aunque luego no haya conexión.
"""
from __future__ import annotations

import argparse
import re
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
DIR = ROOT / "web" / "sprites"
REMOTE = "https://play.pokemonshowdown.com/sprites/"
FOLDERS = {"ani", "ani-back", "gen5", "gen5-back", "dex", "types", "categories"}
TYPES = {".gif": "image/gif", ".png": "image/png"}
UA = {"User-Agent": "RooBot (uso personal; cache local de sprites)"}
_NAME = re.compile(r"^[A-Za-z0-9_.-]+$")

_lock = threading.Lock()
_missing: set = set()            # no existen en Showdown (404): no volver a preguntar
_offline_until = 0.0             # sin conexión: no reintentar durante un rato


def _split(sub: str) -> Optional[Tuple[str, str]]:
    parts = sub.split("/")
    if len(parts) != 2 or parts[0] not in FOLDERS or not _NAME.match(parts[1]) or Path(parts[1]).suffix.lower() not in TYPES:
        return None
    return parts[0], parts[1]


def _fetch(folder: str, name: str, timeout: float = 6.0) -> Optional[bytes]:
    req = urllib.request.Request(REMOTE + folder + "/" + name, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def get(sub: str) -> Optional[Tuple[bytes, str]]:
    """Devuelve (contenido, tipo) del sprite pedido, de la carpeta local o descargándolo; None si no hay."""
    global _offline_until
    sp = _split(sub)
    if not sp:
        return None
    folder, name = sp
    f = DIR / folder / name
    ctype = TYPES[Path(name).suffix.lower()]
    if f.is_file():
        return f.read_bytes(), ctype
    key = folder + "/" + name
    if key in _missing or time.time() < _offline_until:
        return None
    try:
        data = _fetch(folder, name)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            with _lock:
                _missing.add(key)
        return None
    except Exception:
        _offline_until = time.time() + 60      # sin internet: se reintenta dentro de un minuto
        return None
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_suffix(f.suffix + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(f)
    return data, ctype


TYPE_NAMES = ["Normal", "Fire", "Water", "Electric", "Grass", "Ice", "Fighting", "Poison", "Ground", "Flying", "Psychic",
              "Bug", "Rock", "Ghost", "Dragon", "Dark", "Steel", "Fairy", "Stellar", "???"]


def main() -> None:
    ap = argparse.ArgumentParser(description="Descarga los sprites de tus equipos para usar la app sin internet")
    ap.add_argument("--equipos", nargs="+", default=[str(ROOT / "teams")])
    ap.add_argument("--hilos", type=int, default=8)
    a = ap.parse_args()
    from .engine_client import Engine
    from .rutas import load_teams
    eng = Engine()
    try:
        teams = [eng.call("import", text=t) for t in load_teams(a.equipos)]
        ids = eng.call("sprite_ids", teams=teams)["ids"]
    finally:
        eng.close()
    jobs = []
    for i in ids:
        for folder, ext in (("ani", "gif"), ("ani-back", "gif"), ("gen5", "png"), ("gen5-back", "png"), ("dex", "png")):
            jobs.append((folder, f"{i}.{ext}"))
    jobs += [("types", f"{t}.png") for t in TYPE_NAMES] + [("categories", f"{c}.png") for c in ("Physical", "Special", "Status")]
    todo = [j for j in jobs if not (DIR / j[0] / j[1]).is_file()]
    print(f"{len(teams)} equipos · {len(ids)} Pokémon/formas · {len(jobs)} archivos posibles · {len(todo)} por descargar")
    stats = {"ok": 0, "no": 0, "err": 0}

    def one(job):
        folder, name = job
        try:
            data = _fetch(folder, name, timeout=15)
        except urllib.error.HTTPError as e:
            stats["no" if e.code == 404 else "err"] += 1
            return
        except Exception:
            stats["err"] += 1
            return
        f = DIR / folder / name
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(data)
        stats["ok"] += 1

    done = 0
    with ThreadPoolExecutor(a.hilos) as ex:
        for _ in ex.map(one, todo):
            done += 1
            if done % 100 == 0 or done == len(todo):
                print(f"  {done}/{len(todo)}  (descargados {stats['ok']}, no existen {stats['no']}, fallos {stats['err']})", flush=True)
    size = sum(f.stat().st_size for f in DIR.rglob("*") if f.is_file()) / 1e6 if DIR.exists() else 0
    print(f"Listo: web/sprites ocupa {size:.1f} MB.")
    if stats["err"]:
        print("Algunos fallaron por la conexión: vuelve a ejecutarlo y sólo bajará lo que falta.")
    print("Los que «no existen» son formas sin ese tipo de sprite en Showdown (la app usa otro, p. ej. el de la forma base).")


if __name__ == "__main__":
    main()
