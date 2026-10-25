"""Importa equipos a RooBot: de una hoja con enlaces a Pokepaste (p. ej. el VGCPastes Repository),
de enlaces de pokepast.es sueltos o de texto exportado de Showdown.

    mantenimiento.bat, opción 2                    (lee equipos_nuevos.txt: pega ahí enlaces o equipos)
    python -m vgclab.importar "https://docs.google.com/spreadsheets/d/<ID>/edit?gid=<GID>"
    python -m vgclab.importar https://pokepast.es/abc123... https://pokepast.es/def456...
    python -m vgclab.importar mis_equipos.txt          (export de Showdown; varios con === [formato] Nombre ===)
    python -m vgclab.importar carpeta_con_txt\\
    python -m vgclab.importar ... --mios              (guarda en teams\\ como equipos tuyos, con ★ en la app)

Qué hace:
  1. Descarga la pestaña como CSV y busca todos los enlaces https://pokepast.es/...
  2. Descarga cada paste en texto (pokepast.es/<id>/raw), con caché en .cache_pastes/
  3. Lo adapta a Champions: EVs clásicos (>32) se convierten a Stat Points (1.º SP = 4 EVs,
     cada SP extra = 8 EVs; máx. 32 por stat y 66 en total), quita IVs, Tera y niveles.
  4. Lo valida con el validador real de Showdown para el formato y guarda sólo los válidos.
  5. Deja un informe (_informe_importacion.csv) con los descartados y el motivo.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import html as html_mod
import io
import re
import sys
import time
import urllib.request
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .engine_client import DEFAULT_FORMAT, Engine

ROOT = Path(__file__).resolve().parent.parent
UA = {"User-Agent": "Mozilla/5.0 (VGC-Lab importer)"}
PASTE_RE = re.compile(r"https?://pokepast\.es/([0-9a-fA-F]{8,})")
STAT_NAMES = {"hp": "HP", "atk": "Atk", "def": "Def", "spa": "SpA", "spd": "SpD", "spe": "Spe"}
STAT_KEYS = {"hp": "hp", "atk": "atk", "def": "def", "spa": "spa", "spd": "spd", "spe": "spe"}


def http_get(url: str, timeout: int = 30, tries: int = 3) -> str:
    last = None
    for k in range(tries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read().decode("utf-8", errors="replace")
        except Exception as e:  # pragma: no cover - red
            last = e
            time.sleep(1.5 * (k + 1))
    raise RuntimeError(f"No pude descargar {url}: {last}")


def sheet_csv_url(url: str) -> str:
    m = re.search(r"/spreadsheets/d/([A-Za-z0-9_-]+)", url)
    if not m:
        raise SystemExit("No reconozco el enlace de Google Sheets")
    gid = re.search(r"gid=(\d+)", url)
    return f"https://docs.google.com/spreadsheets/d/{m.group(1)}/export?format=csv" + (f"&gid={gid.group(1)}" if gid else "")


def read_rows(source: str) -> List[List[str]]:
    if source.startswith("http"):
        text = http_get(sheet_csv_url(source), timeout=60)
    else:
        text = Path(source).read_text(encoding="utf-8-sig")
    return list(csv.reader(io.StringIO(text)))


def find_teams(rows: List[List[str]]) -> List[Dict[str, str]]:
    """Una entrada por enlace de Pokepaste, con un id y un nombre si la fila los tiene."""
    out, seen = [], set()
    for row in rows:
        for cell in row:
            for m in PASTE_RE.finditer(cell):
                pid = m.group(1).lower()
                if pid in seen:
                    continue
                seen.add(pid)
                tid = next((c.strip() for c in row if re.fullmatch(r"[A-Z]{1,3}\d{1,5}", c.strip())), "")
                name = next((c.strip() for c in row if " Team" in c or "'s " in c), "")
                out.append({"id": tid or pid[:8], "name": name, "paste": pid})
    return out


HEADER_RE = re.compile(r"^===\s*(?:\[[^\]]*\]\s*)?(.*?)\s*===\s*$")


def split_showdown(text: str, default_name: str) -> List[Dict[str, str]]:
    """Separa un texto de Showdown en equipos. Admite la copia de seguridad del teambuilder
    (cabeceras '=== [formato] Nombre ===') o un solo equipo; si hay más de 6 Pokémon sin cabeceras,
    se agrupan de 6 en 6."""
    lines = [l for l in text.replace("\r", "").split("\n") if not l.lstrip().startswith("#")]
    teams, cur, name = [], [], default_name
    for l in lines:
        m = HEADER_RE.match(l.strip())
        if m:
            if "\n".join(cur).strip():
                teams.append({"name": name, "text": "\n".join(cur)})
            cur, name = [], m.group(1) or default_name
        else:
            cur.append(l)
    if "\n".join(cur).strip():
        teams.append({"name": name, "text": "\n".join(cur)})
    out = []
    for t in teams:
        blocks = [b for b in re.split(r"\n\s*\n", t["text"].strip()) if b.strip()]
        if len(blocks) <= 6:
            out.append(t)
        else:
            for k in range(0, len(blocks), 6):
                out.append({"name": f"{t['name']}_{k // 6 + 1}", "text": "\n\n".join(blocks[k:k + 6])})
    return out


def paste_title(pid: str) -> str:
    """Título que el autor puso al paste (si se puede leer)."""
    try:
        html = http_get(f"https://pokepast.es/{pid}", timeout=20, tries=1)
        m = re.search(r"<h1>(.*?)</h1>", html, re.S)
        return html_mod.unescape(re.sub(r"<[^>]+>", "", m.group(1))).strip() if m else ""
    except Exception:
        return ""


def collect(sources: List[str]) -> List[Dict[str, str]]:
    """Equipos a importar a partir de hojas, enlaces de Pokepaste, archivos .txt o carpetas."""
    out: List[Dict[str, str]] = []
    for src in sources:
        if src.startswith("http") and "docs.google.com" in src:
            out += find_teams(read_rows(src))
            continue
        m = PASTE_RE.search(src)
        if m and src.startswith("http"):
            out.append({"id": "", "name": "", "paste": m.group(1).lower()})
            continue
        path = Path(src)
        if not path.exists():
            print(f"  No encuentro {src}")
            continue
        files = sorted(path.glob("*.txt")) if path.is_dir() else [path]
        for f in files:
            if f.suffix.lower() == ".csv":
                out += find_teams(read_rows(str(f)))
                continue
            text = f.read_text(encoding="utf-8-sig", errors="replace")
            rest = []
            for line in text.splitlines():
                if line.lstrip().startswith("#"):
                    continue
                if "docs.google.com/spreadsheets" in line:
                    url = re.search(r"https?://\S+", line)
                    if url:
                        out += find_teams(read_rows(url.group(0)))
                    continue
                found = list(PASTE_RE.finditer(line))
                if found:
                    # enlaces de Pokepaste (si hay texto delante del enlace, se usa como nombre)
                    for mm in found:
                        name = line[:mm.start()].strip(" -:|\t")
                        out.append({"id": "", "name": name, "paste": mm.group(1).lower()})
                    continue
                rest.append(line)
            rest_text = "\n".join(rest)
            if "Ability:" in rest_text or re.search(r"^- ", rest_text, re.M):
                for t in split_showdown(rest_text, f.stem):
                    out.append({"id": "", "name": t["name"], "text": t["text"]})
    return out


def fetch_paste(pid: str, cache: Path) -> str:
    f = cache / f"{pid}.txt"
    if f.exists():
        return f.read_text(encoding="utf-8")
    text = http_get(f"https://pokepast.es/{pid}/raw")
    f.write_text(text, encoding="utf-8")
    time.sleep(0.25)  # no saturar pokepast.es
    return text


def _parse_spread(line: str) -> Dict[str, int]:
    vals = {}
    for part in line.split(":", 1)[1].split("/"):
        p = part.strip().split()
        if len(p) >= 2 and p[0].isdigit():
            key = p[1].lower()
            key = {"hp": "hp", "atk": "atk", "def": "def", "spa": "spa", "spd": "spd", "spe": "spe"}.get(key)
            if key:
                vals[key] = int(p[0])
    return vals


def ev_to_sp(ev: int) -> int:
    if ev < 4:
        return 0
    return min(32, 1 + (ev - 4) // 8)


def adapt_to_champions(text: str) -> Tuple[str, List[str]]:
    """Normaliza un paste para Champions. Devuelve (texto, notas)."""
    notes: List[str] = []
    blocks = [b for b in re.split(r"\r?\n\s*\r?\n", text.strip()) if b.strip()]
    out_blocks = []
    for b in blocks:
        lines = [l.rstrip() for l in b.splitlines() if l.strip()]
        new = []
        for l in lines:
            low = l.strip().lower()
            if low.startswith(("ivs:", "tera type:", "level:", "shiny:", "happiness:", "dynamax level:", "gigantamax:", "hidden power:")):
                continue
            if low.startswith("evs:"):
                sp = _parse_spread(l)
                if any(v > 32 for v in sp.values()):
                    sp = {k: ev_to_sp(v) for k, v in sp.items()}
                    notes.append("EVs convertidos a SP")
                total = sum(sp.values())
                if total > 66:  # recorte proporcional conservando lo más invertido
                    for k in sorted(sp, key=lambda k: sp[k]):
                        exceso = sum(sp.values()) - 66
                        if exceso <= 0:
                            break
                        quita = min(exceso, sp[k])
                        sp[k] -= quita
                    notes.append("SP recortados a 66")
                parts = [f"{v} {STAT_NAMES[k]}" for k, v in sp.items() if v > 0]
                if parts:
                    new.append("EVs: " + " / ".join(parts))
                continue
            new.append(l.strip() if not l.startswith((" ", "\t")) else l)
        out_blocks.append("\n".join(new))
    return "\n\n".join(out_blocks) + "\n", sorted(set(notes))


def safe_name(s: str) -> str:
    s = re.sub(r"[^\w\- ]+", "", s, flags=re.UNICODE).strip().replace(" ", "_")
    return s[:60] or "equipo"


def main(argv=None):
    ap = argparse.ArgumentParser(description="Importa equipos (hoja de VGCPastes, enlaces de Pokepaste o texto de Showdown)")
    ap.add_argument("origen", nargs="+", help="enlace de Google Sheets, enlaces de pokepast.es, .txt/.csv o carpetas")
    ap.add_argument("--salida", default=None, help="carpeta destino (por defecto teams\\mc, o teams\\ con --mios)")
    ap.add_argument("--mios", action="store_true", help="son equipos tuyos: se guardan en teams\\ (salen con ★ en la app)")
    ap.add_argument("--formato", default=DEFAULT_FORMAT)
    ap.add_argument("--max", type=int, default=0, help="importar como mucho N equipos (0 = todos)")
    a = ap.parse_args(argv)

    out = Path(a.salida) if a.salida else (ROOT / "teams" if a.mios else ROOT / "teams" / "mc")
    out.mkdir(parents=True, exist_ok=True)
    cache = ROOT / ".cache_pastes"
    cache.mkdir(exist_ok=True)

    print("Buscando equipos...")
    teams = collect(a.origen)
    if a.max:
        teams = teams[: a.max]
    print(f"{len(teams)} equipos encontrados.")
    ok = bad = same = 0
    hashes = set()
    report = []
    with Engine(a.formato) as eng:
        def canon(raw: str) -> str:
            t = adapt_to_champions(raw)[0]
            try:
                t = eng.call("normalize", text=t)
            except Exception:
                pass
            return t
        existing = set()
        for f in out.glob("*.txt"):   # para no duplicar equipos que ya tienes
            try:
                existing.add(hashlib.sha1(canon(f.read_text(encoding="utf-8")).encode()).hexdigest())
            except Exception:
                pass
        for k, t in enumerate(teams, 1):
            src = t.get("paste") or "texto"
            if "text" in t:
                raw = t["text"]
            else:
                try:
                    raw = fetch_paste(t["paste"], cache)
                except Exception as e:
                    report.append([t["id"], t["name"], src, "descarga", str(e)])
                    bad += 1
                    continue
                if not t["name"]:
                    t["name"] = paste_title(t["paste"]) or t["paste"][:8]
            text, notes = adapt_to_champions(raw)
            try:
                text = eng.call("normalize", text=text)
            except Exception:
                pass
            h = hashlib.sha1(text.encode()).hexdigest()
            if h in hashes or h in existing:
                report.append([t["id"], t["name"], src, "ya estaba", ""])
                same += 1
                continue
            hashes.add(h)
            try:
                v = eng.call("validate", team=text)
            except Exception as e:
                v = {"ok": False, "problems": [str(e).splitlines()[0]]}
            if v["ok"]:
                base = safe_name(f"{t['id']}_{t['name']}" if t["id"] else t["name"])
                fname = out / f"{base}.txt"
                n = 2
                while fname.exists():
                    fname = out / f"{base}_{n}.txt"
                    n += 1
                fname.write_text(text, encoding="utf-8")
                ok += 1
                print(f"  + {fname.name}" + (f"  ({'; '.join(notes)})" if notes else ""))
                if notes:
                    report.append([t["id"], t["name"], src, "ok (ajustado)", "; ".join(notes)])
            else:
                bad += 1
                print(f"  x {t['name'] or src}: {' | '.join(v['problems'][:2])}")
                report.append([t["id"], t["name"], src, "no válido", " | ".join(v["problems"][:4])])
            if k % 25 == 0 and len(teams) > 50:
                print(f"  {k}/{len(teams)}  válidos {ok}  descartados {bad}", flush=True)
    with open(out / "_informe_importacion.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["id", "nombre", "origen", "estado", "detalle"])
        w.writerows(report)
    print(f"\nListo: {ok} equipos nuevos en {out}  ({same} ya estaban, {bad} no válidos; ver _informe_importacion.csv)")
    if ok:
        print("En la app de ejercicios aparecen al recargar la página (F5).")


if __name__ == "__main__":
    sys.exit(main())
