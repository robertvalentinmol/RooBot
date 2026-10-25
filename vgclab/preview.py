"""¿Qué 4 traigo y con qué lidero contra este equipo?

    python -m vgclab.preview teams/koh_mc_inspired.txt teams/rain_offense.txt

El rival puede ser un equipo completo o sólo especies/objetos/movimientos (como en un Open Team Sheet);
lo que falte (Stat Points y naturaleza) se estima.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .engine_client import Engine


def main(argv=None):
    ap = argparse.ArgumentParser(description="Recomendación de team preview (4 de 6 + leads)")
    ap.add_argument("mi_equipo")
    ap.add_argument("equipo_rival")
    ap.add_argument("--top", type=int, default=5)
    a = ap.parse_args(argv)
    with Engine() as e:
        ours = e.call("import", text=Path(a.mi_equipo).read_text(encoding="utf-8"))
        theirs_full = e.call("import", text=Path(a.equipo_rival).read_text(encoding="utf-8"))
        theirs = [{"species": s["species"], "item": s["item"], "ability": s["ability"], "moves": s["moves"]} for s in theirs_full]
        tp = e.call("teampreview", ours=ours, theirs=theirs, temperature=0)
    t = tp["table"]
    print("\nDaño estimado de MIS Pokémon a los suyos (mejor movimiento, % de sus PS, Megas activadas):")
    print(" " * 18 + "".join(f"{n[:10]:>11}" for n in t["theirs"]))
    for name, row in zip(t["ours"], t["off"]):
        print(f"{name[:17]:<18}" + "".join(f"{min(x, 1.2) * 100:10.0f}%" for x in row))
    print("\nDaño estimado de SUS Pokémon a los míos:")
    print(" " * 18 + "".join(f"{n[:10]:>11}" for n in t["theirs"]))
    for name, row in zip(t["ours"], t["thr"]):
        print(f"{name[:17]:<18}" + "".join(f"{min(x, 1.2) * 100:10.0f}%" for x in row))
    print("\nMejores opciones (leads primero):")
    for k, o in enumerate(tp["top"][: a.top], 1):
        n = o["names"]
        print(f"  {k}. Lead {n[0]} + {n[1]}  | atrás {', '.join(n[2:])}   (puntuación {o['score']})")
    print("\nAviso: es una heurística de emparejamientos con daño real del simulador; no sustituye a jugar el emparejamiento.")


if __name__ == "__main__":
    sys.exit(main())
