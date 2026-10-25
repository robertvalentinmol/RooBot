"""Calculadora de probabilidades estilo Marcus Koh.

Las calculadoras online dan el rango de un solo golpe (16 tiradas). Aquí combinamos:
  * varios ataques en el mismo turno (2 atacantes = 16x16 = 256 resultados, y crece exponencialmente),
  * críticos (1/24 por golpe), precisión, golpes múltiples (2-5 golpes 35/35/15/15 %),
  * Baya Zidra (cura 1/4 al bajar del 50 % entre golpes), Banda Focus, Multiescamas,
  * recursión turno a turno para escenarios de desgaste ("stall"), con curación de final de turno.

Todas las tiradas salen del simulador real de Showdown (engine/damage.js), así que son exactas
para Champions (Stat Points, Megas, cambios de movimientos y habilidades del mod).
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, Dict, List, Optional, Tuple

from .engine_client import Engine

CRIT_DEFAULT = 1 / 24

# Probabilidad del número de golpes para movimientos 2-5 (Gen 5+)
HITS_2_5 = {2: 0.35, 3: 0.35, 4: 0.15, 5: 0.15}


@dataclass
class Attack:
    """Un ataque concreto: quién, con qué, y en qué condiciones."""
    attacker: Any            # set (dict o texto de Showdown)
    move: str
    label: str = ""
    spread: Optional[bool] = None   # None = automático según el movimiento
    crit_allowed: bool = True
    opts: Dict[str, Any] = field(default_factory=dict)  # weather, terrain, atkBoosts, defBoosts, atkMega, defMega, helpingHand, screens...

    def request(self, defender) -> Dict[str, Any]:
        r = {"attacker": self.attacker, "defender": defender, "move": self.move}
        if self.spread is not None:
            r["spread"] = self.spread
        r.update(self.opts)
        return r


@dataclass
class HitModel:
    """Distribución de un ataque frente a un defensor concreto (salida del motor)."""
    label: str
    accuracy: float
    crit_chance: float
    hits: Tuple[int, int]
    rolls_full: List[List[int]]      # por golpe, 16 tiradas sin crítico, defensor a PS completos
    crits_full: List[List[int]]
    rolls_dmg: List[List[int]]       # defensor ya dañado (importa para Multiescamas)
    crits_dmg: List[List[int]]
    immune: bool = False
    raw: Dict[str, Any] = field(default_factory=dict)

    @staticmethod
    def from_engine(res: Dict[str, Any], label: str, crit_allowed: bool = True) -> "HitModel":
        mv = res["move"]
        full, dmg = res["rolls"]["full"], res["rolls"]["damaged"]
        immune = full["normal"][0] is None
        z = [[0] * 16]
        return HitModel(
            label=label or mv["name"],
            accuracy=(mv["accuracy"] or 100) / 100.0,
            crit_chance=(mv["critChance"] if crit_allowed else 0.0),
            hits=tuple(mv["hits"]),
            rolls_full=z if immune else full["normal"], crits_full=z if immune else full["crit"],
            rolls_dmg=z if immune else dmg["normal"], crits_dmg=z if immune else dmg["crit"],
            immune=immune, raw=res,
        )

    def hit_count_dist(self) -> Dict[int, float]:
        lo, hi = self.hits
        if lo == hi:
            return {lo: 1.0}
        if (lo, hi) == (2, 5):
            return dict(HITS_2_5)
        n = hi - lo + 1
        return {h: 1.0 / n for h in range(lo, hi + 1)}

    def per_hit_outcomes(self, hit_index: int, full_hp: bool) -> List[Tuple[int, float]]:
        """Resultados (daño, prob) de un golpe concreto, mezclando crítico."""
        normal = (self.rolls_full if full_hp else self.rolls_dmg)
        crit = (self.crits_full if full_hp else self.crits_dmg)
        k = min(hit_index, len(normal) - 1)
        out: Dict[int, float] = defaultdict(float)
        pc = self.crit_chance
        for d in normal[k]:
            out[d] += (1 - pc) / 16
        if pc > 0:
            for d in crit[k]:
                out[d] += pc / 16
        return list(out.items())


@dataclass
class Defender:
    maxhp: int
    hp: int
    sash: bool = False
    sitrus: bool = False
    sitrus_frac: float = 0.25   # Baya Zidra cura 1/4 de los PS máximos

    @staticmethod
    def from_engine(res: Dict[str, Any], hp: Optional[int] = None) -> "Defender":
        d = res["defender"]
        item = (d.get("item") or "").lower()
        return Defender(maxhp=d["maxhp"], hp=hp if hp is not None else d["hp"],
                        sash=item == "focussash", sitrus=item == "sitrusberry")


State = Tuple[int, bool, bool]  # (ps, banda_disponible, zidra_disponible)


def _apply_hit(dist: Dict[State, float], model: HitModel, hit_index: int, D: Defender) -> Dict[State, float]:
    out: Dict[State, float] = defaultdict(float)
    for (hp, sash, berry), p in dist.items():
        if hp <= 0:
            out[(hp, sash, berry)] += p
            continue
        full = hp >= D.maxhp
        for dmg, q in model.per_hit_outcomes(hit_index, full):
            nhp = hp - dmg
            nsash = sash
            if nhp <= 0 and sash and full:
                nhp, nsash = 1, False
            nberry = berry
            if 0 < nhp <= D.maxhp // 2 and berry:
                nhp = min(D.maxhp, nhp + int(D.maxhp * D.sitrus_frac))
                nberry = False
            out[(max(0, nhp), nsash, nberry)] += p * q
    return out


def apply_attack(dist: Dict[State, float], model: HitModel, D: Defender) -> Dict[State, float]:
    """Aplica un ataque completo (precisión + nº de golpes + golpes) a una distribución de estados."""
    if model.immune:
        return dict(dist)
    out: Dict[State, float] = defaultdict(float)
    acc = min(1.0, model.accuracy)
    for st, p in dist.items():
        out[st] += p * (1 - acc)
    for h, ph in model.hit_count_dist().items():
        cur: Dict[State, float] = {st: p * acc * ph for st, p in dist.items()}
        for k in range(h):
            cur = _apply_hit(cur, model, k, D)
        for st, p in cur.items():
            out[st] += p
    return out


def survival(models: List[HitModel], D: Defender) -> Dict[str, Any]:
    """Probabilidad de sobrevivir a una secuencia de ataques en el mismo turno (en orden de velocidad)."""
    dist: Dict[State, float] = {(D.hp, D.sash, D.sitrus): 1.0}
    for m in models:
        dist = apply_attack(dist, m, D)
    p_survive = sum(p for (hp, _, _), p in dist.items() if hp > 0)
    hp_dist: Dict[int, float] = defaultdict(float)
    for (hp, _, _), p in dist.items():
        hp_dist[hp] += p
    exp_left = sum(hp * p for hp, p in hp_dist.items())
    return {"survive": p_survive, "ko": 1 - p_survive, "expected_hp_left": exp_left,
            "expected_pct_left": 100 * exp_left / D.maxhp, "hp_distribution": dict(sorted(hp_dist.items()))}


def outcomes_count(models: List[HitModel]) -> int:
    """Nº de combinaciones de tiradas (sin contar críticos): 16^golpes. 2 ataques = 256."""
    n = 1
    for m in models:
        n *= 16 ** m.hits[1]
    return n


def ko_by_turn(models_per_turn: List[HitModel], D: Defender, max_turns: int = 10,
               heal_per_turn: float = 0.0, chip_per_turn: float = 0.0) -> List[float]:
    """Recursión de desgaste (el escenario de 'stalemate' de Koh).

    Cada turno el defensor recibe `models_per_turn` y al final del turno se cura `heal_per_turn`
    (fracción de PS máx., p.ej. Restos 1/16, Campo de Hierba 1/16) y sufre `chip_per_turn`
    (p.ej. quemadura 1/16, veneno 1/8). Devuelve P(KO en o antes del turno t) para t=1..max_turns.

    Se resuelve con recursión memorizada sobre (PS, banda, zidra, turnos_restantes).
    """
    heal = int(D.maxhp * heal_per_turn)
    chip = int(D.maxhp * chip_per_turn)

    @lru_cache(maxsize=None)
    def p_alive_after(hp: int, sash: bool, berry: bool, turns: int) -> float:
        if hp <= 0:
            return 0.0
        if turns == 0:
            return 1.0
        dist = {(hp, sash, berry): 1.0}
        for m in models_per_turn:
            dist = apply_attack(dist, m, D)
        total = 0.0
        for (h, s, b), p in dist.items():
            if h <= 0:
                continue
            h2 = h - chip
            if h2 <= 0:
                continue
            h2 = min(D.maxhp, h2 + heal)
            if 0 < h2 <= D.maxhp // 2 and b:
                h2 = min(D.maxhp, h2 + int(D.maxhp * D.sitrus_frac))
                b = False
            total += p * p_alive_after(h2, s, b, turns - 1)
        return total

    return [1 - p_alive_after(D.hp, D.sash, D.sitrus, t) for t in range(1, max_turns + 1)]


# ---------------------------------------------------------------- API de alto nivel

def model_attack(engine: Engine, atk: Attack, defender) -> Tuple[HitModel, Dict[str, Any]]:
    res = engine.call("calc", **atk.request(defender))
    return HitModel.from_engine(res, atk.label or f"{res['attacker']['species']} {res['move']['name']}", atk.crit_allowed), res


def scenario(engine: Engine, defender, attacks: List[Attack], defender_hp: Optional[int] = None,
             def_opts: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Calcula un escenario completo: ataques en orden contra el defensor."""
    models, first = [], None
    for a in attacks:
        if def_opts:
            a.opts = {**def_opts, **a.opts}
        m, res = model_attack(engine, a, defender)
        models.append(m)
        first = first or res
    D = Defender.from_engine(first, defender_hp)
    out = survival(models, D)
    out["defender"] = first["defender"]
    out["attacks"] = []
    for m in models:
        lo = min(m.rolls_full[0]); hi = max(m.rolls_full[0])
        out["attacks"].append({
            "label": m.label, "min": lo, "max": hi,
            "min_pct": 100 * lo / D.maxhp, "max_pct": 100 * hi / D.maxhp,
            "hits": m.hits, "accuracy": m.accuracy, "immune": m.immune,
        })
    out["combinations"] = outcomes_count(models)
    return out
