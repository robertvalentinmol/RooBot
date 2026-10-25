"""CLI de la calculadora estilo Marcus Koh.

Uso:
    python -m vgclab.calc ejemplos/koh_kingambit.yaml
    python -m vgclab.calc ejemplos/koh_kingambit.yaml --optimizar
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Dict, List

from .engine_client import Engine
from .koh import Attack, Defender, ko_by_turn, model_attack, scenario
from .optimizer import Scenario, optimize

ATTACK_OPT_KEYS = {"weather", "terrain", "atkMega", "defMega", "atkBoosts", "defBoosts", "atkStatus",
                   "helpingHand", "screens", "atkPartner", "defPartner"}


def _load(path: str) -> Dict[str, Any]:
    text = Path(path).read_text(encoding="utf-8")
    if path.endswith((".yaml", ".yml")):
        import yaml
        return yaml.safe_load(text)
    import json
    return json.loads(text)


def _resolve_set(ref, sets: Dict[str, Any], base_dir: Path):
    if isinstance(ref, dict):
        return ref
    if isinstance(ref, str) and ref in sets:
        return _resolve_set(sets[ref], {}, base_dir)
    # "archivo.txt:Especie" -> busca el set en un equipo
    if isinstance(ref, str) and "\n" not in ref.strip() and ":" in ref:
        f, sp = ref.rsplit(":", 1)
        p = (base_dir / f) if not Path(f).is_absolute() else Path(f)
        if p.exists():
            blocks = [b for b in p.read_text(encoding="utf-8").split("\n\n") if b.strip()]
            for b in blocks:
                if b.strip().split("\n")[0].split("@")[0].strip().lower().startswith(sp.strip().lower()):
                    return b
            raise SystemExit(f"No encuentro {sp} en {p}")
    return ref  # texto de Showdown


def _attack(spec: Dict[str, Any], sets, base_dir) -> Attack:
    opts = {k: v for k, v in spec.items() if k in ATTACK_OPT_KEYS}
    return Attack(attacker=_resolve_set(spec["attacker"], sets, base_dir), move=spec["move"],
                  label=spec.get("label", ""), spread=spec.get("spread"),
                  crit_allowed=spec.get("crit", True), opts=opts)


def pct(x: float) -> str:
    return f"{100 * x:6.2f} %"


def run_file(path: str, do_opt: bool, engine: Engine):
    cfg = _load(path)
    base = Path(path).resolve().parent
    sets = cfg.get("sets", {})
    defender = _resolve_set(cfg["defender"], sets, base)
    def_opts = {}
    if cfg.get("defender_mega"):
        def_opts["defMega"] = True
    print("=" * 78)
    print(f"Defensor: {defender.strip().splitlines()[0] if isinstance(defender, str) else defender.get('species')}")
    print("=" * 78)
    opt_scen: List[Scenario] = []
    for sc in cfg.get("scenarios", []):
        attacks = [_attack(a, sets, base) for a in sc["attacks"]]
        for a in attacks:
            a.opts = {**def_opts, **a.opts}
        r = scenario(engine, defender, attacks, defender_hp=cfg.get("defender_hp"))
        print(f"\n▶ {sc['name']}   (peso {sc.get('weight', 1)})")
        for a in r["attacks"]:
            if a["immune"]:
                print(f"   · {a['label']:<40} INMUNE")
                continue
            hits = f" x{a['hits'][0]}-{a['hits'][1]}" if a["hits"][1] > 1 else ""
            print(f"   · {a['label']:<40} {a['min']}-{a['max']}{hits} PS ({a['min_pct']:.1f}-{a['max_pct']:.1f} %)  prec {a['accuracy']*100:.0f} %")
        print(f"   PS del defensor: {r['defender']['maxhp']}   combinaciones de tiradas: {r['combinations']:,}")
        print(f"   Probabilidad de SOBREVIVIR: {pct(r['survive'])}   (KO: {pct(r['ko'])})   PS restantes esperados: {r['expected_pct_left']:.1f} %")
        opt_scen.append(Scenario(name=sc["name"], attacks=attacks, weight=sc.get("weight", 1.0)))

    for rec in cfg.get("recursion", []):
        attacks = [_attack(a, sets, base) for a in rec["attacks_per_turn"]]
        models, first = [], None
        for a in attacks:
            a.opts = {**def_opts, **a.opts}
            m, res = model_attack(engine, a, defender)
            models.append(m); first = first or res
        D = Defender.from_engine(first, cfg.get("defender_hp"))
        curve = ko_by_turn(models, D, max_turns=rec.get("turns", 8), heal_per_turn=rec.get("heal_per_turn", 0.0),
                           chip_per_turn=rec.get("chip_per_turn", 0.0))
        print(f"\n♻ Recursión de desgaste: {rec['name']}")
        for t, p in enumerate(curve, 1):
            bar = "█" * int(p * 40)
            print(f"   turno {t:2d}: P(KO acumulada) {pct(p)} {bar}")

    if do_opt or cfg.get("optimize"):
        o = cfg.get("optimize") or {}
        print("\n" + "=" * 78)
        print("OPTIMIZADOR DE STAT POINTS (66 total, 32 máx. por stat)")
        print("=" * 78)
        res = optimize(engine, defender, opt_scen, fixed=o.get("fixed"), def_mega=bool(cfg.get("defender_mega")),
                       top=o.get("top", 8), min_hp=o.get("min_hp", 0))
        print(f"{res['species']}: SP fijos {res['fixed']} → {res['budget']} SP para PS/Def/DefEsp")
        head = "  PS  Def DefE  | media  | " + " | ".join(f"E{i+1}" for i in range(len(res["scenarios"])))
        print(head)
        cur = res["current"]
        print(f"  {cur['hp']:>2}  {cur['def']:>3} {cur['spd']:>4}  | {cur['score']*100:5.1f}% | " +
              " | ".join(f"{p*100:5.1f}%" for p in cur["per"]) + "   ← reparto actual")
        for r in res["best"]:
            print(f"  {r['hp']:>2}  {r['def']:>3} {r['spd']:>4}  | {r['score']*100:5.1f}% | " +
                  " | ".join(f"{p*100:5.1f}%" for p in r["per"]))
        for i, n in enumerate(res["scenarios"]):
            print(f"   E{i+1} = {n}")


def main(argv=None):
    ap = argparse.ArgumentParser(description="Calculadora de supervivencia multi-ataque estilo Marcus Koh (Champions)")
    ap.add_argument("archivo", help="escenario .yaml/.json (ver ejemplos/)")
    ap.add_argument("--optimizar", action="store_true", help="buscar el mejor reparto de Stat Points")
    args = ap.parse_args(argv)
    with Engine() as eng:
        run_file(args.archivo, args.optimizar, eng)


if __name__ == "__main__":
    sys.exit(main())
