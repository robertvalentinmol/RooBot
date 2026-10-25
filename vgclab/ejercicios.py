"""Ejercicios de posición (como los problemas de táctica en ajedrez).

    python -m vgclab.ejercicios --equipo teams\\mi_equipo.txt --rivales teams\\mc
    python -m vgclab.ejercicios --equipo teams\\mi_equipo.txt --tipo lead --n 15
    python -m vgclab.ejercicios --equipo teams\\mi_equipo.txt --tipo final

Tipos:  lead  = turno 1 (después del team preview)
        medio = un turno intermedio (2-5)
        final = quedan como mucho 2 Pokémon por bando
        mixto = cualquiera (por defecto)

En cada ejercicio eliges una jugada de la lista. Mientras piensas, el motor resuelve la posición
(simulador real, varias semillas, matriz de pagos y equilibrio). Luego te dice:
  * qué jugadas forman parte del equilibrio y cuánto vale cada opción,
  * qué esperaba el rival,
  * y te enseña una simulación del turno con tu jugada contra su respuesta más probable.
Los resultados se guardan en logs/ejercicios.csv para que veas tu progreso.
"""
from __future__ import annotations

import argparse
import csv
import random
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, List

from .engine_client import EnginePool
from .rutas import ROOT, load_teams

WEATHER = {"sunnyday": "Sol", "desolateland": "Sol extremo", "raindance": "Lluvia", "primordialsea": "Lluvia extrema",
           "sandstorm": "Tormenta de arena", "snowscape": "Nieve"}
TERRAIN = {"grassyterrain": "Campo de Hierba", "electricterrain": "Campo Eléctrico",
           "psychicterrain": "Campo Psíquico", "mistyterrain": "Campo de Niebla"}
STATUS = {"par": "PAR", "brn": "QUE", "slp": "DOR", "frz": "CON", "psn": "ENV", "tox": "ENV+"}
CONDS = {"tailwind": "Viento Afín", "reflect": "Reflejo", "lightscreen": "Pantalla de Luz", "auroraveil": "Velo Aurora"}
BOOST = {"atk": "Atq", "def": "Def", "spa": "AtEsp", "spd": "DefEsp", "spe": "Vel", "accuracy": "Prec", "evasion": "Eva"}
TIPOS = {"lead": "Lead", "medio": "Medio juego", "final": "Final"}


def _bar(frac: float, n: int = 12) -> str:
    k = max(0, min(n, round(frac * n)))
    return "█" * k + "·" * (n - k)


def describe_mon(p: Dict[str, Any], stats: Dict[str, Any], mine: bool, reveal_bench: bool = True) -> str:
    s = p["set"]
    name = stats.get("species", s["species"])
    maxhp = stats.get("stats", {}).get("hp") or 1
    frac = (p.get("hp", maxhp) / maxhp) if not p.get("fainted") else 0
    hp = f"{p.get('hp', 0)}/{maxhp} PS" if mine else f"{round(frac * 100)}%"
    extra = []
    if p.get("status"):
        extra.append(STATUS.get(p["status"], p["status"]))
    b = {k: v for k, v in (p.get("boosts") or {}).items() if v}
    if b:
        extra.append(" ".join(f"{'+' if v > 0 else ''}{v} {BOOST.get(k, k)}" for k, v in b.items()))
    import re as _re
    cur = p.get("item")
    item = s.get("item") or ""
    if cur is not None and _re.sub(r"[^a-z0-9]", "", item.lower()) != cur:
        item = cur or ""   # objeto consumido o cambiado
    if cur == "":
        item = "(objeto usado)" if s.get("item") else ""

    moves = ", ".join(s["moves"])
    head = f"{name:<18} {_bar(frac)} {hp:<10}"
    tail = f"  {'; '.join(extra)}" if extra else ""
    line = f"{head}{tail}\n        {s.get('ability', '')} · {item or '(sin objeto)'} · {moves}"
    if mine:
        ev = s.get("evs", {})
        spread = " / ".join(f"{v} {k}" for k, v in ev.items() if v)
        line += f"\n        {s.get('nature', '')} · SP {spread}"
        st = stats.get("stats", {})
        line += f" · Vel {st.get('spe', '?')}"
    return line


def show_position(pos: Dict[str, Any], stats_cache, engine, rival_name: str, k: int, n: int):
    st = pos["state"]
    f = st.get("field", {})
    print("\n" + "═" * 78)
    print(f" Ejercicio {k}/{n} · {TIPOS.get(pos['kind'], pos['kind'])} (turno {pos['turn']}) · Rival: {rival_name}")
    print("═" * 78)
    campo = []
    if f.get("weather"):
        campo.append(f"{WEATHER.get(f['weather'], f['weather'])} ({f.get('weatherTurns', '?')} t)")
    if f.get("terrain"):
        campo.append(f"{TERRAIN.get(f['terrain'], f['terrain'])} ({f.get('terrainTurns', '?')} t)")
    if f.get("trickroom"):
        campo.append(f"Espacio Raro ({f['trickroom']} t)")
    for lado, sd in (("tuyo", st["sides"][0]), ("rival", st["sides"][1])):
        for c, t in (sd.get("conditions") or {}).items():
            campo.append(f"{CONDS.get(c, c)} {lado} ({t} t)")
    print(" Campo: " + (" · ".join(campo) if campo else "nada"))

    def stats_for(p, mine):
        key = (p["set"]["species"], p.get("mega"), mine, str(p["set"].get("evs")), p["set"].get("nature"))
        if key not in stats_cache:
            stats_cache[key] = engine.call("stats", set=p["set"], mega=bool(p.get("mega")))
        return stats_cache[key]

    for title, idx, mine in (("RIVAL", 1, False), ("TÚ", 0, True)):
        sd = st["sides"][idx]
        print(f"\n {title}{'  (Mega ya usada)' if sd.get('megaUsed') else ''}")
        actives = sorted([p for p in sd["pokemon"] if p.get("active") is not None], key=lambda p: p["active"])
        for p in actives:
            print(f"   [{'ab'[p['active']]}] " + describe_mon(p, stats_for(p, mine), mine))
        bench = [p for p in sd["pokemon"] if p.get("active") is None]
        if bench:
            if not mine and pos["kind"] == "lead":
                print(f"   Banquillo: {len(bench)} sin revelar (de: {', '.join(s for s in pos['preview']['theirs'] if s not in [a['set']['species'] for a in actives])})")
            else:
                txt = []
                for p in bench:
                    if p.get("fainted"):
                        txt.append(f"{p['set']['species']} (KO)")
                    else:
                        mx = stats_for(p, mine)["stats"]["hp"]
                        txt.append(f"{p['set']['species']} ({round(100 * p.get('hp', mx) / mx)}%)")
                print("   Banquillo: " + ", ".join(txt))


def render_events(events: List[List[str]]) -> List[str]:
    out = []
    who = lambda ident: ("tu " if ident.startswith("p1") else "rival ") + ident.split(": ", 1)[-1]
    for e in events:
        t = e[0]
        if t == "move":
            tgt = f" → {who(e[3])}" if len(e) > 3 and e[3] and e[3].startswith("p") else ""
            out.append(f"   {who(e[1])} usa {e[2]}{tgt}")
        elif t in ("-damage", "-heal"):
            src = f" ({e[3].replace('[from] ', '')})" if len(e) > 3 and e[3].startswith("[from]") else ""
            hp = e[2].split(" ")[0]
            pct = 0
            if "/" in hp:
                a, b = hp.split("/")
                pct = round(100 * int(a) / max(1, int(b)))
            verb = "queda al" if t == "-damage" else "se cura hasta el"
            out.append(f"      {who(e[1])} {verb} {pct}%{src}")
        elif t == "faint":
            out.append(f"      ✖ {who(e[1])} se debilita")
        elif t == "-supereffective":
            out.append("      ¡Es muy eficaz!")
        elif t == "-crit":
            out.append("      ¡Golpe crítico!")
        elif t == "-miss":
            out.append(f"      falla contra {who(e[2]) if len(e) > 2 else ''}")
        elif t == "-activate" and len(e) > 2 and "Protect" in e[2]:
            out.append(f"      {who(e[1])} se protege")
        elif t == "-mega":
            out.append(f"   {who(e[1])} megaevoluciona")
        elif t == "switch":
            out.append(f"   entra {who(e[1])}")
        elif t == "cant":
            out.append(f"      {who(e[1])} no puede moverse ({e[2]})")
        elif t in ("-boost", "-unboost"):
            sign = "+" if t == "-boost" else "-"
            out.append(f"      {who(e[1])} {sign}{e[3]} {BOOST.get(e[2], e[2])}")
        elif t == "-status":
            out.append(f"      {who(e[1])} queda {STATUS.get(e[2], e[2])}")
        elif t == "-fail":
            out.append(f"      falla ({who(e[1])})")
    return out


def run(args):
    my_text = Path(args.equipo).read_text(encoding="utf-8")
    rival_files = []
    for r in args.rivales:
        p = Path(r)
        rival_files += sorted(p.glob("*.txt")) if p.is_dir() else [p]
    if not rival_files:
        raise SystemExit("No hay equipos rivales")
    log_path = Path(args.logs) / "ejercicios.csv"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    total_pts, hechos = 0, 0
    with EnginePool(args.workers) as pool:
        eng = pool.main
        ours = eng.call("import", text=my_text)
        v = eng.call("validate", team=my_text)
        if not v["ok"]:
            raise SystemExit("Tu equipo no es válido:\n  " + "\n  ".join(v["problems"]))
        stats_cache: Dict[Any, Any] = {}
        for k in range(1, args.n + 1):
            rf = random.choice(rival_files)
            try:
                theirs = eng.call("import", text=rf.read_text(encoding="utf-8"))
                kind = args.tipo if args.tipo != "mixto" else random.choice(["lead", "medio", "final"])
                pos = eng.call("gen_position", ours=ours, theirs=theirs, kind=kind, maxAlive=2)
            except Exception as e:
                print(f"(saltando {rf.name}: {str(e).splitlines()[0]})")
                continue
            # resolver la posición en segundo plano mientras el jugador piensa
            result: Dict[str, Any] = {}

            def solve():
                result["res"] = pool.search([pos["state"]], K=args.opciones, Kopp=8, seeds=args.semillas)
            th = threading.Thread(target=solve, daemon=True)
            th.start()
            show_position(pos, stats_cache, eng, rf.stem, k, args.n)
            # opciones: las mismas que analiza el motor, barajadas
            th.join()
            res = result["res"]
            opts = [i for i in range(len(res["ours"])) if i not in res.get("bad_rows", [])]
            random.shuffle(opts)
            print("\n ¿Qué haces?")
            for n_, i in enumerate(opts, 1):
                print(f"   {n_:>2}. {res['ours'][i]['label']}")
            t0 = time.time()
            while True:
                ans = input("\n Tu elección (número, 'q' para salir): ").strip().lower()
                if ans == "q":
                    break
                if ans.isdigit() and 1 <= int(ans) <= len(opts):
                    break
            if ans == "q":
                break
            pick = opts[int(ans) - 1]
            secs = time.time() - t0
            M, row, col = res["matrix"], res["row"], res["col"]
            q = [sum(M[i][j] * col[j] for j in range(len(col))) for i in range(len(M))]
            best = max(range(len(q)), key=lambda i: q[i])
            loss = q[best] - q[pick]
            pts = round(100 * max(0.0, 1 - loss / 0.4))
            if row[pick] >= 0.10 or loss < 0.03:
                verdict, pts = "✔ CORRECTA: forma parte del equilibrio", max(pts, 90)
            elif loss < 0.10:
                verdict = "~ ACEPTABLE: un poco peor que la mejor"
            else:
                verdict = "✘ ERROR"
            print(f"\n {verdict}   (puntos {pts}/100, pérdida {loss:.2f}, {secs:.0f}s)")
            print("\n Valor de cada opción contra lo que suele hacer el rival  (equilibrio = cómo mezclarlas):")
            for i in sorted(range(len(q)), key=lambda i: -q[i]):
                mark = "←" if i == pick else " "
                print(f"   {q[i]:+.2f}  eq {row[i] * 100:4.0f}%  {res['ours'][i]['label']} {mark}")
            print("\n Lo que esperaba el rival:")
            for j in sorted(range(len(col)), key=lambda j: -col[j])[:4]:
                if col[j] >= 0.03:
                    print(f"   {col[j] * 100:4.0f}%  {res['theirs'][j]['label']}")
            jbest = max(range(len(col)), key=lambda j: col[j])
            sim = eng.call("simulate", state=pos["state"], ours=res["ours"][pick]["choice"], theirs=res["theirs"][jbest]["choice"])
            print(f"\n Simulación de tu jugada contra su respuesta más probable:")
            for line in render_events(sim["events"])[:24]:
                print(line)
            hechos += 1
            total_pts += pts
            new = not log_path.exists()
            with open(log_path, "a", newline="", encoding="utf-8") as f:
                w = csv.writer(f)
                if new:
                    w.writerow(["fecha", "tipo", "rival", "eleccion", "mejor", "puntos", "perdida", "segundos"])
                w.writerow([time.strftime("%Y-%m-%d %H:%M"), pos["kind"], rf.stem, res["ours"][pick]["label"],
                            res["ours"][best]["label"], pts, round(loss, 3), round(secs)])
            input("\n (Enter para el siguiente)")
    if hechos:
        print(f"\nSesión: {hechos} ejercicios, media {total_pts / hechos:.0f}/100. Historial en {log_path}")


def main(argv=None):
    ap = argparse.ArgumentParser(description="Ejercicios de posición con análisis del motor")
    ap.add_argument("--equipo", required=True, help="tu equipo (.txt de exportación de Showdown)")
    ap.add_argument("--rivales", nargs="+", default=[str(ROOT / "teams" / "mc")])
    ap.add_argument("--tipo", choices=["mixto", "lead", "medio", "final"], default="mixto")
    ap.add_argument("--n", type=int, default=10)
    ap.add_argument("--opciones", type=int, default=8, help="nº de opciones a mostrar/analizar")
    ap.add_argument("--semillas", type=int, default=16)
    ap.add_argument("--workers", type=int, default=None)
    ap.add_argument("--logs", default=str(ROOT / "logs"))
    run(ap.parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
