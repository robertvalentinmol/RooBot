"""Optimizador de Stat Points (la parte "ajustar el entrenamiento" de Marcus Koh).

Dado tu Pokémon y una lista de escenarios de amenaza (ataques simples o dobles, con su peso),
busca el reparto de PS / Defensa / Def. Especial que maximiza la probabilidad media ponderada
de sobrevivir, respetando el límite de Champions: 66 SP en total y 32 por estadística.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from .engine_client import Engine
from .koh import Attack, Defender, HitModel, survival

MAX_TOTAL, MAX_STAT = 66, 32


@dataclass
class Scenario:
    name: str
    attacks: List[Attack]
    weight: float = 1.0


def _set_dict(engine: Engine, s) -> Dict[str, Any]:
    if isinstance(s, str):
        return engine.call("import", text=s)[0]
    return copy.deepcopy(s)


def optimize(engine: Engine, defender, scenarios: List[Scenario], fixed: Optional[Dict[str, int]] = None,
             def_mega: bool = False, top: int = 8, min_hp: int = 0) -> Dict[str, Any]:
    d = _set_dict(engine, defender)
    evs = d.get("evs") or {}
    fixed = dict(fixed if fixed is not None else {k: evs.get(k, 0) for k in ("atk", "spa", "spe")})
    budget = MAX_TOTAL - sum(fixed.values())
    if budget < 0:
        raise ValueError("Los SP fijados superan 66")
    st = engine.call("stats", set=d, mega=def_mega)
    base_hp = st["baseStats"]["hp"]
    item = (d.get("item") or "").lower().replace(" ", "")

    # Barridos: para cada ataque, tiradas con cada valor (0..32) de la defensa relevante
    sweeps: List[List[Dict[str, Any]]] = []
    for sc in scenarios:
        row = []
        for a in sc.attacks:
            req = a.request(d)
            if def_mega:
                req["defMega"] = True
            sw = engine.call("sweep", **req)
            row.append(sw)
        sweeps.append(row)

    def models_for(si: int, defsp: int, spdsp: int) -> List[HitModel]:
        out = []
        for ai, sw in enumerate(sweeps[si]):
            v = defsp if sw["stat"] == "def" else spdsp
            r = sw["results"][v]
            res = {"move": sw["move"], "rolls": r["rolls"]}
            out.append(HitModel.from_engine(res, scenarios[si].attacks[ai].label or sw["move"]["name"],
                                            scenarios[si].attacks[ai].crit_allowed))
        return out

    cache: Dict[Tuple[int, int, int], List[HitModel]] = {}
    results = []
    for hp in range(max(0, min_hp), min(MAX_STAT, budget) + 1):
        for df in range(0, min(MAX_STAT, budget - hp) + 1):
            sd = budget - hp - df
            if sd > MAX_STAT:
                continue
            maxhp = base_hp + 75 + hp
            D = Defender(maxhp=maxhp, hp=maxhp, sash=item == "focussash", sitrus=item == "sitrusberry")
            per = []
            score = 0.0
            wsum = 0.0
            for si, sc in enumerate(scenarios):
                key = (si, df, sd)
                if key not in cache:
                    cache[key] = models_for(si, df, sd)
                p = survival(cache[key], D)["survive"]
                per.append(p)
                score += sc.weight * p
                wsum += sc.weight
            results.append({"hp": hp, "def": df, "spd": sd, "score": score / max(wsum, 1e-9), "per": per})
    results.sort(key=lambda r: (-round(r["score"], 6), -r["hp"]))

    # referencia: el reparto actual
    cur_hp, cur_def, cur_spd = evs.get("hp", 0), evs.get("def", 0), evs.get("spd", 0)
    maxhp = base_hp + 75 + cur_hp
    Dc = Defender(maxhp=maxhp, hp=maxhp, sash=item == "focussash", sitrus=item == "sitrusberry")
    current = [survival(models_for(si, cur_def, cur_spd), Dc)["survive"] for si in range(len(scenarios))]

    return {
        "species": st["species"], "budget": budget, "fixed": fixed,
        "best": results[:top], "current": {"hp": cur_hp, "def": cur_def, "spd": cur_spd, "per": current,
                                           "score": sum(s.weight * p for s, p in zip(scenarios, current)) / max(sum(s.weight for s in scenarios), 1e-9)},
        "scenarios": [s.name for s in scenarios],
    }


def min_speed_sp(engine: Engine, my_set, target_speed: int, mega: bool = False, strictly_faster: bool = True) -> Optional[int]:
    """Mínimo de SP en Velocidad para superar `target_speed` (None si no se alcanza ni con 32)."""
    d = _set_dict(engine, my_set)
    for sp in range(0, MAX_STAT + 1):
        d.setdefault("evs", {})["spe"] = sp
        spe = engine.call("stats", set=d, mega=mega)["stats"]["spe"]
        if spe > target_speed or (not strictly_faster and spe >= target_speed):
            return sp
    return None
