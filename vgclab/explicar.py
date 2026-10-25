"""Textos que explican una decisión de los ejercicios (por qué era buena o qué se perdía).

Todo sale de datos del simulador: la matriz de la búsqueda (cómo le va a cada jugada nuestra
contra cada respuesta del rival) y simulaciones del turno con tu jugada y con la mejor.
Las cadenas usan **negrita** al estilo Markdown; la interfaz la convierte en HTML.
"""
from __future__ import annotations

import math
import re
from typing import Any, Dict, List, Optional

PROTECT = {"Protect", "Detect", "Spiky Shield", "King's Shield", "Baneful Bunker", "Silk Trap",
           "Burning Bulwark", "Obstruct", "Max Guard"}
_SLOT_RE = re.compile(r"^(?P<mon>.*?): (?P<move>.*?)(?: → (?P<target>.*?))?(?P<mega> \(Mega\))?$")


def pct(x: float) -> str:
    return f"{round(100 * x)} %"


def parse_slot(label: str) -> Dict[str, Any]:
    label = label.strip()
    if label in ("pass", "—", "default", ""):
        return {"kind": "pass"}
    if " ⇄ " in label:
        a, b = label.split(" ⇄ ", 1)
        return {"kind": "switch", "mon": a, "to": b}
    m = _SLOT_RE.match(label)
    if not m:
        return {"kind": "other", "label": label}
    return {"kind": "move", "mon": m["mon"], "move": m["move"], "target": m["target"], "mega": bool(m["mega"])}


def split_joint(label: str) -> List[Dict[str, Any]]:
    parts = label.split(" | ")
    return [parse_slot(p) for p in parts] + [{"kind": "pass"}] * (2 - len(parts))


def _mon(out: Dict[str, Any], side: int, name: str) -> Optional[Dict[str, Any]]:
    for m in out["mons"][side]:
        if m and (m["name"] == name or m["name"].startswith(name) or name.startswith(m["name"].split("-")[0])):
            return m
    return None


def _slot_reason(a: Dict[str, Any], b: Dict[str, Any], om: Dict[str, Any], ob: Dict[str, Any]) -> Optional[str]:
    """Una frase sobre la diferencia en una ranura entre tu jugada (a) y la mejor (b)."""
    mon = a.get("mon") or b.get("mon") or "tu Pokémon"
    if a == b:
        return None
    if a["kind"] == "move" and b["kind"] == "move":
        if a["move"] == b["move"] and a["target"] == b["target"] and a["mega"] != b["mega"]:
            if b["mega"]:
                return (f"**{mon}** debía **megaevolucionar** ya: la Mega mejora sus estadísticas (y a menudo su habilidad) "
                        f"desde este mismo turno; guardarla es regalar un turno de ventaja.")
            return (f"Megaevolucionar a **{mon}** ahora no era lo mejor: sólo hay una Mega por combate y aquí "
                    f"compensa reservarla.")
        a_prot, b_prot = a["move"] in PROTECT, b["move"] in PROTECT
        if b_prot and not a_prot:
            m_a, m_b = _mon(om, 0, mon), _mon(ob, 0, mon)
            extra = ""
            if m_a and m_b and abs(m_a["ko"] - m_b["ko"]) >= 0.1:
                extra = f" Con tu jugada cae KO el **{pct(m_a['ko'])}** de las veces; protegiéndose, el {pct(m_b['ko'])}."
            elif m_a and m_b and m_b["end"] - m_a["end"] >= 0.1:
                extra = f" Con tu jugada acaba de media al **{pct(m_a['end'])}**; protegiéndose, al {pct(m_b['end'])}."
            return f"Lo mejor era **proteger a {mon}** ({b['move']}): el rival tiene motivos para ir a por él.{extra}"
        if a_prot and not b_prot:
            m_b = _mon(ob, 0, mon)
            extra = f" Sin protegerse sólo cae el {pct(m_b['ko'])} de las veces." if m_b else ""
            tgt = f" sobre {b['target']}" if b.get("target") else ""
            return (f"Proteger a **{mon}** no hacía falta: era mejor aprovechar el turno con **{b['move']}**{tgt}.{extra}")
        if a["move"] == b["move"] and a["target"] != b["target"]:
            ta, tb = _mon(om, 1, a["target"] or ""), _mon(ob, 1, b["target"] or "")
            extra = ""
            if ta and tb:
                extra = (f" Con tu jugada {a['target']} acaba de media al {pct(ta['end'])}; con la mejor, "
                         f"{b['target']} acaba al {pct(tb['end'])} (KO el {pct(tb['ko'])}).")
            return f"Con **{mon}**, mejor apuntar **{b['move']}** a **{b['target']}** que a {a['target']}.{extra}"
        tgt = f" → {b['target']}" if b.get("target") else ""
        mega = " (con Mega)" if b["mega"] and not a["mega"] else ""
        return f"Con **{mon}**, **{b['move']}{tgt}**{mega} rendía más que {a['move']}."
    if b["kind"] == "switch" and a["kind"] != "switch":
        m_a = _mon(om, 0, b["mon"])
        extra = f" Quedándose, cae KO el **{pct(m_a['ko'])}** de las veces." if m_a else ""
        return f"Lo mejor era **cambiar a {b['mon']} por {b['to']}**.{extra}"
    if a["kind"] == "switch" and b["kind"] != "switch":
        what = f"**{b['move']}**" if b.get("move") else "actuar"
        return f"Cambiar a **{a['mon']}** no compensaba: pierdes el turno; era mejor {what}."
    if a["kind"] == "switch" and b["kind"] == "switch":
        return f"Si cambias a {a['mon']}, mejor por **{b['to']}** que por {a['to']}."
    return None


def _outcome_diffs(om: Dict[str, Any], ob: Dict[str, Any]) -> List[str]:
    """Diferencias grandes de resultado (PS y KO) entre tu jugada y la mejor."""
    rows = []
    for side, who in ((0, "tu"), (1, "su")):
        for a, b in zip(om["mons"][side], ob["mons"][side]):
            if not a or not b or a["start"] <= 0:
                continue
            dko = b["ko"] - a["ko"]
            dend = b["end"] - a["end"]
            weight = abs(dko) * 1.5 + abs(dend)
            if abs(dko) >= 0.2:
                if side == 0:
                    txt = (f"{who.capitalize()} **{a['name']}** cae KO el **{pct(a['ko'])}** de las veces con tu jugada "
                           f"(con la mejor, el {pct(b['ko'])}).")
                else:
                    txt = (f"{who.capitalize()} **{a['name']}** cae KO el **{pct(a['ko'])}** de las veces con tu jugada "
                           f"(con la mejor, el {pct(b['ko'])}).")
            elif abs(dend) >= 0.15:
                txt = (f"{who.capitalize()} **{a['name']}** acaba de media al **{pct(a['end'])}** con tu jugada "
                       f"(con la mejor, al {pct(b['end'])}).")
            else:
                continue
            good_for_me = (dko < 0 or dend > 0) if side == 0 else (dko > 0 or dend < 0)
            rows.append((weight, good_for_me, txt))
    rows.sort(key=lambda r: -r[0])
    return [r[2] for r in rows[:3]]


def _wasted(om: Dict[str, Any], ob: Dict[str, Any]) -> List[str]:
    out = []
    for sid in ("p1a", "p1b"):
        a, b = om["slots"].get(sid), ob["slots"].get(sid)
        if not a:
            continue
        bb = b or {}
        name = a["name"]
        mv = max(a["moves"], key=a["moves"].get) if a["moves"] else "su ataque"
        checks = [
            ("blocked", f"El **{mv}** de {name} choca contra una **protección** del rival el {{p}} de las veces."),
            ("terrain", f"El **{mv}** de {name} lo bloquea el **Campo Psíquico** el {{p}} de las veces: los Pokémon en el suelo son inmunes a los movimientos con prioridad."),
            ("shield", f"El **{mv}** de {name} lo bloquea **{a.get('shieldName', 'una habilidad')}** el {{p}} de las veces (anula los movimientos con prioridad)."),
            ("immune", f"El **{mv}** de {name} **no afecta** al objetivo el {{p}} de las veces."),
            ("fail", f"El **{mv}** de {name} **falla o se queda sin objetivo** el {{p}} de las veces."),
            ("faintedBefore", f"**{name} cae antes de moverse** el {{p}} de las veces (el rival es más rápido o tiene prioridad)."),
            ("cant", f"**{name} no puede moverse** el {{p}} de las veces."),
        ]
        if mv in ("Fake Out", "First Impression", "Mat Block"):
            checks = [("fail", f"El **{mv}** de {name} falla el {{p}} de las veces: **sólo funciona el primer turno** tras entrar al campo.")] + \
                     [c for c in checks if c[0] != "fail"]
        for key, txt in checks:
            if a.get(key, 0) >= 0.25 and a.get(key, 0) - bb.get(key, 0) >= 0.15:
                out.append(txt.format(p=pct(a[key])))
    return out


def explain_position(res: Dict[str, Any], q: List[float], mine: int, best: int,
                     out_mine: Dict[str, Any], out_best: Optional[Dict[str, Any]]) -> List[str]:
    ours, theirs, M, row, col = res["ours"], res["theirs"], res["matrix"], res["row"], res["col"]
    loss = max(0.0, q[best] - q[mine])
    lines: List[str] = []
    mix = sorted((i for i in range(len(row)) if row[i] >= 0.12), key=lambda i: -row[i])
    if mine == best or loss < 0.03 or row[mine] >= 0.10:
        lines.append("Tu jugada está entre las mejores contra lo que es probable que haga el rival.")
        if len(mix) > 1:
            others = [f"«{ours[i]['label']}» ({pct(row[i])})" for i in mix if i != mine][:2]
            if others:
                lines.append("La estrategia óptima aquí **mezcla** jugadas para no ser predecible; también juega " +
                             " y ".join(others) + ".")
        # por qué funciona: lo más destacado del resultado de tu jugada
        hits = []
        for m in out_mine["mons"][1]:
            if m and m["start"] > 0 and (m["ko"] >= 0.3 or m["start"] - m["end"] >= 0.35):
                hits.append(f"{m['name']} (KO el {pct(m['ko'])})" if m["ko"] >= 0.3 else f"{m['name']} (le quitas ~{pct(m['start'] - m['end'])})")
        if hits:
            lines.append("Presionas a " + ", ".join(hits[:2]) + ".")
        safe = [m["name"] for m in out_mine["mons"][0] if m and m["active"] and m["ko"] < 0.15 and m["start"] > 0]
        if safe:
            lines.append(("Y mantienes" if hits else "Mantienes") + " a salvo a " + " y ".join(safe) + " casi siempre.")
        return lines
    lines.append(f"La mejor jugada era **{ours[best]['label']}**. La tuya rinde **{loss:.2f}** menos de valor "
                 f"(escala de −1 a +1) contra la estrategia del rival.")
    a_slots, b_slots = split_joint(ours[mine]["label"]), split_joint(ours[best]["label"])
    ob = out_best or out_mine
    for a, b in zip(a_slots, b_slots):
        r = _slot_reason(a, b, out_mine, ob)
        if r:
            lines.append(r)
    if out_best:
        lines += _wasted(out_mine, out_best)
        lines += _outcome_diffs(out_mine, out_best)
    # la respuesta del rival que más castiga tu jugada
    cand = [j for j in range(len(col)) if col[j] >= 0.05]
    if cand:
        j = max(cand, key=lambda j: M[best][j] - M[mine][j])
        if M[best][j] - M[mine][j] >= 0.1:
            lines.append(f"Lo que más castiga tu jugada es que el rival haga «{theirs[j]['label']}» "
                         f"(lo juega un {pct(col[j])}): tu jugada se queda en **{M[mine][j]:+.2f}** y la mejor en {M[best][j]:+.2f}.")
    return lines


def explain_endgame(ours, q, la, mine: int, best1: int, best: int, lines_: Dict[str, Any]) -> List[str]:
    """Final calculado hasta el último turno."""
    q2 = la["q2"]
    win = lambda i: max(0.0, min(1.0, (q2[i] + 1) / 2))
    lab = lambda i: ours[i]["label"]
    out: List[str] = []
    vals = [win(i) for i in q2]
    if min(vals) >= 0.95:
        out.append(f"🏁 **Final ganado**: calculado hasta el último turno, ganas con cualquiera de las jugadas razonables "
                   f"(tu jugada gana el {pct(win(mine))}). Aquí lo importante es no regalar nada.")
    elif max(vals) <= 0.05:
        out.append(f"🏁 **Final perdido** salvo error del rival: ninguna jugada razonable gana más del {pct(max(vals))}. "
                   "Busca la que le obligue a acertar más veces.")
    elif mine == best or win(best) - win(mine) < 0.04:
        out.append(f"🏁 Final calculado hasta el último turno: tu jugada es de las que más gana (**{pct(win(mine))}** de las veces).")
    else:
        out.append(f"🏁 Final calculado hasta el último turno: con tu jugada ganas el **{pct(win(mine))}** de las veces; "
                   f"con «{lab(best)}», el **{pct(win(best))}**.")
    if best != best1 and win(best) - win(best1) >= 0.05:
        out.append(f"Mirando un solo turno parecía mejor «{lab(best1)}», pero jugando hasta el final gana más «{lab(best)}».")

    def fmt(line):
        steps = []
        for t in line.get("turns", []):
            ko = ""
            if t.get("kos"):
                ko = " → KO a " + ", ".join(("tu " if k.startswith("p1") else "su ") + k.split(": ", 1)[-1] for k in t["kos"])
            steps.append(f"T{t['turn']}: {t['ours']} / él: {t['theirs']}{ko}")
        res = "ganas" if line.get("won") is True else ("pierdes" if line.get("won") is False else "sin terminar")
        return " · ".join(steps) + f" ⇒ **{res}**"
    if lines_.get("best"):
        out.append("Línea esperada con la mejor (contra su respuesta más probable): " + fmt(lines_["best"]))
    if lines_.get("mine"):
        out.append("Con la tuya: " + fmt(lines_["mine"]))
    return out


def explain_lookahead(ours, q, la, mine: int, best1: int, best: int, good: bool = False) -> List[str]:
    """Qué cambia al mirar un turno más (análisis «2 turnos»)."""
    q2 = la["q2"]
    lab = lambda i: ours[i]["label"]
    out: List[str] = []
    if best != best1:
        out.append(f"🔭 **Mirando 2 turnos cambia la mejor jugada**: a 1 turno parecía «{lab(best1)}» "
                   f"({q[best1]:+.2f}), pero contando el turno siguiente gana «{lab(best)}» ({q2[best]:+.2f} frente a "
                   f"{q2[best1]:+.2f}). Lo que prepara para el turno 2 pesa más que lo que gana ya.")
    d_mine = q2[mine] - q[mine]
    rest = [i for i in q2 if i != mine]
    avg = sum(q2[i] - q[i] for i in rest) / len(rest) if rest else 0.0
    rel = d_mine - avg   # cuánto gana (o pierde) tu jugada frente a las demás al mirar más lejos
    if rel >= 0.08:
        out.append(f"🔭 Al mirar el turno siguiente tu jugada sale mejor parada que las demás ({rel:+.2f} frente a la media): "
                   "tiene un plan detrás" + ("." if good else ", aunque sigue sin ser la mejor."))
    elif rel <= -0.08:
        out.append(f"🔭 Al mirar el turno siguiente tu jugada sale peor parada que las demás ({rel:+.2f} frente a la media): "
                   "lo que ganas este turno lo pagas en el siguiente (posición, velocidad o quién queda expuesto).")
    if not out:
        out.append("🔭 Mirar 2 turnos confirma el análisis de 1 turno (el orden de las jugadas apenas cambia).")
    return out


# ---------------------------------------------------------------- team preview
def explain_preview(setup: Dict[str, Any], vals: Dict[str, Dict[str, float]], scores: Dict[str, float],
                    mine: str, best: str, severe: Dict[str, float]) -> List[str]:
    O, Th = setup["ours"], setup["theirs"]
    T = setup["table"]
    R = setup["rivalLeads"]
    names = lambda key: " + ".join(O[int(i)]["name"] for i in key.split("-"))
    rnames = lambda pair: " + ".join(Th[i]["name"] for i in pair)
    mp = [int(i) for i in mine.split("-")]
    bp = [int(i) for i in best.split("-")]
    lines: List[str] = []
    if mine == best or scores[best] - scores[mine] < 0.03:
        lines.append(f"**{names(mine)}** es de los mejores leads contra lo que suele sacar este rival.")
    else:
        lines.append(f"El mejor lead era **{names(best)}** ({scores[best]:+.2f}); el tuyo, {names(mine)}, se queda en "
                     f"**{scores[mine]:+.2f}**.")
    # debilidades: golpes graves que recibes de sus leads probables
    lines.append(f"Golpes graves (≥ 50 % de PS de un solo ataque) que recibe tu lead de sus leads probables: "
                 f"**{severe[mine]:.1f}** de media" + ("" if mine == best else f"; con {names(best)}, {severe[best]:.1f}") + ".")
    threats = []
    for r in R:
        for f in r["pair"]:
            for m in mp:
                fr = T["thr"][m][f]
                if fr >= 0.5:
                    threats.append((r["p"] * fr, f"Su **{Th[f]['name']}** le quita ~{pct(min(fr, 1))} a tu {O[m]['name']}"
                                    + (f" con {T['tmoves'][m][f]}" if T.get("tmoves") and T["tmoves"][m][f] else "") + "."))
    seen = set()
    for _, t in sorted(threats, key=lambda x: -x[0]):
        if t not in seen:
            seen.add(t)
            lines.append(t)
        if len(seen) >= 3:
            break
    # Fake Out / prioridad que no funcionarán contra sus leads probables
    fo_users = [O[m]["name"] for m in mp if O[m].get("fakeout")]
    if fo_users:
        for r in R:
            blockers = [Th[f] for f in r["pair"] if Th[f].get("blocksPriority")]
            if blockers and r["p"] >= 0.1:
                bl = blockers[0]
                lines.append(f"Ojo: contra **{rnames(r['pair'])}** ({pct(r['p'])}), el Fake Out de {' y '.join(fo_users)} "
                             f"no funciona contra sus Pokémon en el suelo por **{bl['blocksPriority']}** de {bl['name']}.")
                break
    # huecos ofensivos
    blind = []
    for r in R:
        for f in r["pair"]:
            best_hit = max(T["off"][m][f] for m in mp)
            if best_hit < 0.4 and Th[f]["name"] not in blind:
                blind.append(Th[f]["name"])
    if blind:
        lines.append("A tu lead le cuesta dañar a " + ", ".join(f"**{n}**" for n in blind[:3]) +
                     " (ningún ataque de tus leads le quita más del 40 %).")
    # velocidad contra el lead más probable
    top = R[0]
    slow = [O[m]["name"] for m in mp if all(T["spd"][m][f] < 0 for f in top["pair"])]
    if slow:
        lines.append(f"Contra {rnames(top['pair'])} (su lead más probable), " + " y ".join(slow) +
                     (" es más lento que ambos." if len(slow) == 1 else " son más lentos que ambos.") +
                     ("" if any(t for t in top["tags"] if "Viento" in t or "Espacio" in t) else ""))
    # dónde se pierde más
    if mine != best:
        worst = max(R, key=lambda r: vals[best][_k(r['pair'])] - vals[mine][_k(r['pair'])])
        d = vals[best][_k(worst["pair"])] - vals[mine][_k(worst["pair"])]
        if d > 0.05:
            lines.append(f"Donde más pierdes es contra **{rnames(worst['pair'])}** ({pct(worst['p'])} de las veces): "
                         f"tu lead queda en {vals[mine][_k(worst['pair'])]:+.2f} y el mejor en {vals[best][_k(worst['pair'])]:+.2f}.")
    tags_m = next((p["tags"] for p in setup["myPairs"] if _k(p["pair"]) == mine), [])
    tags_b = next((p["tags"] for p in setup["myPairs"] if _k(p["pair"]) == best), [])
    if tags_m:
        lines.append("Sinergia de tu lead: " + "; ".join(tags_m[:2]) + ".")
    elif mine != best:
        lines.append("Tu lead no tiene una sinergia clara (Fake Out, control de velocidad, clima…)." +
                     (f" El mejor sí: {'; '.join(tags_b[:2])}." if tags_b else ""))
    order = next((p["order"] for p in setup["myPairs"] if _k(p["pair"]) == mine), None)
    if order:
        lines.append("Con ese lead, lo lógico atrás es " + " y ".join(O[i]["name"] for i in order[2:]) + ".")
    return lines


def _k(pair) -> str:
    return "-".join(str(i) for i in sorted(pair))


def _turn_summary(det: Dict[str, Any], O, Th, mine_pair, foe_pair) -> str:
    """'su A acaba al 25 % (KO 60 %), su B al 70 %; tus X y Y al 90 % y 100 %'"""
    if not det or not det.get("ok"):
        return ""
    mons = det["out"]["mons"]
    parts_foe, parts_me = [], []
    for m in mons[1]:
        if m and m["active"]:
            parts_foe.append(f"su {m['name']} " + (f"cae KO el **{pct(m['ko'])}**" if m["ko"] >= 0.2 else f"acaba al {pct(m['end'])}"))
    for m in mons[0]:
        if m and m["active"]:
            parts_me.append(f"tu {m['name']} " + (f"cae KO el **{pct(m['ko'])}**" if m["ko"] >= 0.2 else f"acaba al {pct(m['end'])}"))
    return "; ".join(parts_foe + parts_me)


def explain_best(setup: Dict[str, Any], scores: Dict[str, float], severe: Dict[str, float],
                 mine: str, best: str, det_best: Optional[Dict[str, Any]], det_mine: Optional[Dict[str, Any]]) -> List[str]:
    """Por qué el mejor lead es el mejor (comparado con el tuyo)."""
    O, Th, T, R = setup["ours"], setup["theirs"], setup["table"], setup["rivalLeads"]
    bp = [int(i) for i in best.split("-")]
    mp = [int(i) for i in mine.split("-")]
    names = lambda pair: " + ".join(O[i]["name"] for i in pair)
    rnames = lambda pair: " + ".join(Th[i]["name"] for i in pair)
    top = R[0]
    lines: List[str] = []
    factors: List[str] = []
    turn: List[str] = []

    def offense(pair):
        return sum(r["p"] * sum(1 for f in r["pair"] for m in pair if T["off"][m][f] >= 0.5) for r in R)

    def fast(pair, foes):
        return sum(1 for m in pair for f in foes if T["spd"][m][f] > 0)

    same = mine == best
    if same:
        lines.append(f"Tu lead **{names(bp)}** es el mejor del ranking ({scores[best]:+.2f}). Esto es lo que lo hace bueno:")
    else:
        lines.append(f"**{names(bp)}** saca **{scores[best]:+.2f}** de media frente al {scores[mine]:+.2f} de tu lead. Así se nota en el primer turno:")
    # presión ofensiva
    ob, om = offense(bp), offense(mp)
    hits = []
    for r in R:
        for f in r["pair"]:
            m = max(bp, key=lambda i: T["off"][i][f])
            fr = T["off"][m][f]
            if fr >= 0.5:
                hits.append((r["p"] * fr, f"tu {O[m]['name']} le quita ~{pct(min(fr, 1))} a su {Th[f]['name']}"
                             + (f" con {T['moves'][m][f]}" if T.get("moves") and T["moves"][m][f] else "")))
    uniq = []
    for _, t in sorted(hits, key=lambda x: -x[0]):
        if t not in uniq:
            uniq.append(t)
    ejemplos = (" Por ejemplo, " + "; ".join(uniq[:2]) + ".") if uniq else ""
    if same:
        if uniq:
            factors.append(f"**Presión**: hace {ob:.1f} golpes graves de media a sus leads probables.{ejemplos}")
    elif ob > om + 0.05:
        factors.append(f"**Presiona más**: hace {ob:.1f} golpes graves de media a sus leads probables (tu lead: {om:.1f}).{ejemplos}")
    elif ob < om - 0.05:
        factors.append(f"Hace menos golpes graves que tu lead ({ob:.1f} frente a {om:.1f}): su ventaja no está en pegar más fuerte, sino en lo demás.{ejemplos}")
    elif uniq:
        factors.append(f"Presión parecida a la de tu lead ({ob:.1f} golpes graves).{ejemplos}")
    # aguante
    if not same:
        if severe[best] < severe[mine] - 0.05:
            factors.append(f"**Recibe menos golpes graves**: {severe[best]:.1f} de media frente a {severe[mine]:.1f} de tu lead.")
        elif severe[best] > severe[mine] + 0.05:
            factors.append(f"Recibe algo más de daño ({severe[best]:.1f} golpes graves frente a {severe[mine]:.1f}); aun así compensa por lo demás.")
        # amenazas suyas que tu lead sufre y el mejor no
        seen = set()
        for r in R:
            for f in r["pair"]:
                hm = max(T["thr"][m][f] for m in mp)
                hb = max(T["thr"][m][f] for m in bp)
                if hm >= 0.5 and hb < 0.45 and f not in seen:
                    seen.add(f)
                    factors.append(f"**Aguanta a su {Th[f]['name']}**: lo más fuerte que le hace a {names(bp)} es ~{pct(hb)}; a tu lead, ~{pct(min(hm, 1))}.")
                if len(seen) >= 2:
                    break
    # velocidad contra su lead más probable
    fb, fm = fast(bp, top["pair"]), fast(mp, top["pair"])
    if same or fb != fm:
        factors.append(f"**Velocidad** contra {rnames(top['pair'])} (su lead más probable): se mueve antes en {fb} de 4 cruces"
                     + ("" if same else f" (tu lead, en {fm})") + ".")
    # sinergia
    tags_b = next((p["tags"] for p in setup["myPairs"] if "-".join(map(str, sorted(p["pair"]))) == best), [])
    if tags_b:
        factors.append("**Sinergia**: " + "; ".join(tags_b[:2]) + ".")
    # plan del turno 1 y resultado típico
    if det_best and det_best.get("ok"):
        mix = ""
        if det_best["mix"]:
            mix = " (a veces también " + " / ".join(f"«{m['label']}» {pct(m['p'])}" for m in det_best["mix"][:1]) + ")"
        turn.append(f"**Plan del turno 1** contra {rnames(top['pair'])}: «{det_best['plan']}»{mix}.")
        if det_best["replies"]:
            rep = det_best["replies"][0]
            turn.append(f"Contra su mejor respuesta («{rep['label']}»), el turno suele acabar así: "
                         + _turn_summary(det_best, O, Th, bp, top["pair"]) + ".")
    if not same and det_mine and det_mine.get("ok"):
        turn.append(f"**Con tu lead**, el mejor plan sería «{det_mine['plan']}», y el turno suele acabar así: "
                     + _turn_summary(det_mine, O, Th, mp, top["pair"]) + ".")
    lines += turn + factors
    order = next((p["order"] for p in setup["myPairs"] if "-".join(map(str, sorted(p["pair"]))) == best), None)
    if order and not same:
        lines.append(f"Atrás llevaría a {' y '.join(O[i]['name'] for i in order[2:])}.")
    return lines


# ---------------------------------------------------------------- categorías de decisiones
CATEGORIAS = {
    "protect": "Cuándo proteger",
    "fakeout": "Fake Out",
    "target": "Elegir objetivo (foco)",
    "mega": "Momento de la Mega",
    "switch": "Cambiar o quedarse",
    "speedctrl": "Control de velocidad",
    "support": "Movimientos de apoyo",
    "priority": "Velocidad y prioridad",
    "attack": "Elegir ataque",
}
FAKE = {"Fake Out", "First Impression"}
SPEEDCTRL = {"Tailwind", "Trick Room", "Icy Wind", "Electroweb", "Thunder Wave", "Scary Face", "Bulldoze", "Rock Tomb", "Glare"}
SUPPORT = {"Follow Me", "Rage Powder", "Helping Hand", "Spore", "Sleep Powder", "Will-O-Wisp", "Parting Shot", "Taunt",
           "Encore", "Life Dew", "Pollen Puff", "Wide Guard", "Quick Guard", "Coaching", "Snarl", "Yawn", "Haze", "Clear Smog"}


def _kind(slot: Dict[str, Any]) -> str:
    if slot["kind"] == "switch":
        return "switch"
    if slot["kind"] != "move":
        return "pass"
    mv = slot["move"]
    if mv in PROTECT:
        return "protect"
    if mv in FAKE:
        return "fakeout"
    if mv in SPEEDCTRL:
        return "speedctrl"
    if mv in SUPPORT:
        return "support"
    return "attack"


def classify(mine_label: str, best_label: str, good: bool, out_mine: Optional[Dict[str, Any]] = None,
             out_best: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """Categorías que intervienen en una decisión y si en cada una acertaste.

    Una categoría «interviene» si tu jugada o la mejor la usan; si tu jugada no era buena, se marca
    como fallo la categoría en la que tu jugada y la mejor se separan."""
    a_slots, b_slots = split_joint(mine_label), split_joint(best_label)
    seen: Dict[str, bool] = {}

    def touch(cat: str, ok: bool):
        seen[cat] = seen.get(cat, True) and ok

    for a, b in zip(a_slots, b_slots):
        ka, kb = _kind(a), _kind(b)
        if "pass" in (ka, kb) and ka == kb:
            continue
        for k in {ka, kb} - {"pass"}:
            if k in ("protect", "fakeout", "speedctrl", "support", "switch"):
                touch(k, good or ka == kb)
        if a.get("mega") or b.get("mega"):
            touch("mega", good or bool(a.get("mega")) == bool(b.get("mega")))
        if ka == kb == "switch" and a.get("to") != b.get("to"):
            touch("switch", good)
        if a["kind"] == b["kind"] == "move" and a["move"] == b["move"] and (a.get("target") or b.get("target")):
            touch("target", good or a.get("target") == b.get("target"))
        if ka == kb == "attack":
            touch("attack", good or a["move"] == b["move"])
    # velocidad y prioridad: tu Pokémon cae antes de moverse o su prioridad queda bloqueada
    if out_mine:
        wasted = max([max(r.get("faintedBefore", 0), r.get("terrain", 0), r.get("shield", 0))
                      for k, r in out_mine.get("slots", {}).items() if k.startswith("p1")] or [0])
        best_w = 0.0
        if out_best:
            best_w = max([max(r.get("faintedBefore", 0), r.get("terrain", 0), r.get("shield", 0))
                          for k, r in out_best.get("slots", {}).items() if k.startswith("p1")] or [0])
        if wasted >= 0.25 or best_w >= 0.25:
            touch("priority", good or wasted <= best_w + 0.1)
    if not good and all(seen.values()):
        touch("attack", False)            # fallo que no encaja en otra categoría
    return [{"cat": c, "label": CATEGORIAS[c], "ok": ok} for c, ok in seen.items()]


# ---------------------------------------------------------------- selección de 4 (team preview completo)
def _hits(frac: float) -> int:
    """Golpes que hacen falta para el KO con el mejor ataque (daño medio, sin críticos)."""
    if frac <= 0.02:
        return 99
    return max(1, math.ceil(1.0 / min(frac, 1.0) - 1e-9))


def duel_matrix(T: Dict[str, Any]) -> List[List[Dict[str, Any]]]:
    """«Quién gana a quién» 1 contra 1: golpes para el KO de cada lado y, si empatan, la velocidad.
    v = +1 gana con claridad, +0.5 gana por ser más rápido, 0 empate, -0.5 / -1 pierde."""
    out = []
    for i in range(len(T["off"])):
        row = []
        for j in range(len(T["off"][i])):
            hu, ht, sp = _hits(T["off"][i][j]), _hits(T["thr"][i][j]), T["spd"][i][j]
            if hu < ht:
                v = 1.0
            elif hu > ht:
                v = -1.0
            else:
                v = 0.5 * sp if hu < 99 else 0.0
            row.append({"v": v, "hu": hu, "ht": ht, "spd": sp, "off": round(min(T["off"][i][j], 1.2), 3),
                        "thr": round(min(T["thr"][i][j], 1.2), 3)})
        out.append(row)
    return out


def selection_weaknesses(D, S: List[int], presence: List[float], min_p: float = 0.2) -> List[Dict[str, Any]]:
    """Rivales que suelen venir y a los que ninguno de tus 4 gana 1 contra 1 (ordenados por gravedad)."""
    out = []
    for j, p in enumerate(presence):
        if p < min_p:
            continue
        best = max(D[i][j]["v"] for i in S)
        if best > 0:
            continue
        beats = [i for i in S if D[i][j]["v"] < 0]
        out.append({"j": j, "p": p, "best": best, "beats": beats, "sev": p * (0.25 + len(beats) / 4)})
    return sorted(out, key=lambda w: -w["sev"])


def explain_selection(setup, D, presence, mine_S, mine_L, best_S, best_L, leadv, winp, cover, totals) -> List[str]:
    O, Th = setup["ours"], setup["theirs"]
    nm = lambda idx: ", ".join(O[i]["name"] for i in idx)
    lab = lambda S, L: f"{' + '.join(O[i]['name'] for i in L)} de lead, atrás {nm([i for i in S if i not in L])}"
    sk, bk = _k(mine_S), _k(best_S)
    lk, blk = _k(mine_L), _k(best_L)
    tot = lambda S_k, L_k: totals[S_k + "|" + L_k]
    lines: List[str] = []
    if sk == bk and lk == blk:
        lines.append(f"**{lab(mine_S, mine_L)}** es la mejor selección contra lo que suele traer este rival.")
    else:
        lines.append(f"La mejor selección era **{lab(best_S, best_L)}** ({tot(bk, blk):+.2f}); la tuya se queda en "
                     f"**{tot(sk, lk):+.2f}**.")
        parts = []
        if lk != blk:
            parts.append(f"turno 1 **{leadv[lk]:+.2f}** frente a {leadv[blk]:+.2f}")
        if sk != bk:
            parts.append(f"partidas simuladas con esos 4: ganas el **{pct(winp[sk])}** frente al {pct(winp[bk])}")
            parts.append(f"cobertura 1 contra 1 **{cover[sk]:+.2f}** frente a {cover[bk]:+.2f}")
        lines.append("Dónde se pierde: " + "; ".join(parts) + ".")
    # qué cambia en los 4
    out_ = [i for i in mine_S if i not in best_S]
    in_ = [i for i in best_S if i not in mine_S]
    common = [j for j, p in enumerate(presence) if p >= 0.2]
    for a, b in zip(out_, in_):
        gains = [j for j in common if D[b][j]["v"] > 0 and D[a][j]["v"] <= 0]
        holds = [j for j in common if D[a][j]["v"] < 0 and D[b][j]["v"] >= 0 and j not in gains]
        loses = [j for j in common if D[a][j]["v"] > 0 and D[b][j]["v"] <= 0]
        txt = f"Cambia **{O[a]['name']}** por **{O[b]['name']}**"
        bits = []
        if gains:
            bits.append(f"{O[b]['name']} gana 1 contra 1 a {', '.join(Th[j]['name'] for j in gains)} (y {O[a]['name']} no)")
        if holds:
            bits.append(f"aguanta mejor a {', '.join(Th[j]['name'] for j in holds)}, que sí le ganan a {O[a]['name']}")
        if loses:
            bits.append(f"a cambio pierdes la respuesta de {O[a]['name']} a {', '.join(Th[j]['name'] for j in loses)}")
        if not gains and not holds:
            bits.append("en los 1 contra 1 no mejora, pero el conjunto juega mejor las partidas simuladas "
                        "(apoyo, velocidad o daño en área que el 1 contra 1 no ve)")
        lines.append(txt + ": " + "; ".join(bits) + ".")
    # debilidades sin cubrir
    wm = selection_weaknesses(D, mine_S, presence)
    wb = selection_weaknesses(D, best_S, presence)
    for w in wm[:3]:
        j = w["j"]
        if w["beats"]:
            txt = (f"Sin respuesta en tu selección: su **{Th[j]['name']}** (lo trae el {pct(w['p'])}) gana 1 contra 1 a "
                   f"{', '.join(O[i]['name'] for i in w['beats'])}, y ninguno de tus 4 le gana.")
        else:
            txt = f"Su **{Th[j]['name']}** (lo trae el {pct(w['p'])}) no pierde 1 contra 1 con ninguno de tus 4: como mucho empata."
        cover_by = [i for i in best_S if D[i][j]["v"] > 0]
        if cover_by and sk != bk:
            txt += f" En la mejor selección lo cubre {', '.join(O[i]['name'] for i in cover_by)}."
        lines.append(txt)
    if not wm:
        lines.append("Todo lo que suele traer el rival tiene al menos una respuesta 1 contra 1 en tus 4.")
    elif sk != bk and len(wb) < len(wm):
        lines.append(f"Debilidades sin cubrir: **{len(wm)}** con tu selección y {len(wb)} con la mejor.")
    if wb and sk != bk:
        names = ", ".join(Th[w["j"]]["name"] for w in wb[:2])
        lines.append(f"Ojo: la mejor selección tampoco gana 1 contra 1 a {names}; ahí cuenta jugarlo en pareja "
                     "(foco, control de velocidad, Protect).")
    if sk == bk and lk != blk:
        lines.append(f"Los 4 son los mejores, pero el lead no: con ellos conviene abrir con **{' + '.join(O[i]['name'] for i in best_L)}** "
                     f"({leadv[blk]:+.2f} en el turno 1 frente a {leadv[lk]:+.2f}).")
    return lines
