"""Experiencia: aprender de miles de decisiones de jugadores fuertes qué jugadas suelen ser razonables.

    python -m vgclab.experiencia            # todo: extraer, aprender, evaluar y decidir (mantenimiento.bat, opción 7)
    python -m vgclab.experiencia --sin-evaluar

1) Extrae, de las repeticiones guardadas (rating ≥ 1300, sin las de los bancos de prueba), cada decisión: las
   opciones de cada Pokémon con sus rasgos (engine/policy.js) y cuál eligió el jugador.
2) Aprende un modelo (logit condicional con L2) que da a cada opción una probabilidad. Se mide con partidas de
   1500+ que no usó: acierto de la jugada exacta frente al prior heurístico de la app.
3) Evalúa cuánto peso darle (β) al combinarlo con el cálculo: analiza las partidas de los bancos de prueba y de
   un banco de evaluación (100 partidas apartadas) guardando los cálculos y prueba varios β. Mide dos cosas:
     · el acuerdo con jugadores de 1500+ en cada banco;
     · si los errores que detecta la app predicen quién pierde la partida (no depende de que el jugador acierte).
   Sólo se activa si mejora el acuerdo en todos los bancos sin empeorar la predicción del resultado.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import time
import zlib
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np

from .rutas import ROOT
from .regulacion import REG
from .calibrar import (REPLAY_DIR, BANCO_EVAL, _index, all_bank_ids, fetch_log, guardadas, load_bank, rating_weight)

EXP = ROOT / "engine" / "experiencia.json"
OUT = ROOT / "logs" / "calibracion" / "experiencia"
BETAS = (0.0, 0.005, 0.01, 0.02, 0.03, 0.05, 0.08)
# «rescate»: si la jugada es de las habituales (probabilidad ≥ p) y el cálculo sólo la ve algo peor (pérdida < x),
# no se cuenta como error (el cálculo de un turno no ve su beneficio a más largo plazo)
RESCATES = (None, {"perdida": 0.06, "p": 0.3}, {"perdida": 0.10, "p": 0.3}, {"perdida": 0.10, "p": 0.45},
            {"perdida": 0.15, "p": 0.45}, {"perdida": 0.20, "p": 0.6})


# ------------------------------------------------------------------ conjuntos de partidas
def ensure_eval_bank(n: int = 100, min_rating: int = 1500) -> List[str]:
    try:
        ids = json.loads(BANCO_EVAL.read_text(encoding="utf-8")).get("ids", [])
        if ids:
            return ids
    except Exception:
        pass
    idx = _index()
    banks = set(load_bank(False) + load_bank(True))
    cand = [i for i in guardadas()
            if (idx.get(i) or 0) >= min_rating and i not in banks]
    cand.sort(key=lambda i: zlib.crc32(i.encode()))          # al azar pero siempre las mismas
    ids = cand[:n]
    BANCO_EVAL.write_text(json.dumps({"creado": time.strftime("%Y-%m-%d %H:%M"), "rating_min": min_rating, "ids": ids},
                                     ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"Banco de evaluación creado: {len(ids)} partidas de {min_rating}+ (no se usan para aprender)")
    return ids


def training_ids(min_rating: int = 1300) -> List[str]:
    idx = _index()
    excl = set(all_bank_ids())
    return [i for i in guardadas()
            if (idx.get(i) or 0) >= min_rating and i not in excl]


# ------------------------------------------------------------------ 1) extraer
def extract(app, ids: List[str]):
    idx = _index()
    engines = app.pool.engines
    names = engines[0].call("policy_table", items=[])["names"]

    def work(k: int, rid: str):
        eng = engines[k % len(engines)]
        out = []
        try:
            log = fetch_log(rid)
        except Exception:
            return rid, out
        for side in ("p1", "p2"):
            try:
                parsed = eng.call("replay_parse", log=log, ourTeam=[], side=side)
            except Exception:
                continue
            items = [{"state": d["state"], "spec": d.get("spec")} for d in parsed["decisions"] if d.get("spec")]
            if not items:
                continue
            try:
                rows = eng.call("policy_table", items=items)["rows"]
            except Exception:
                continue
            for r in rows:
                if not r:
                    continue
                for slot in r:
                    if slot and len(slot["f"]) >= 2:
                        out.append(slot)
        return rid, out

    X, starts, y, gid, wts = [], [], [], [], []
    n = 0
    t0 = time.time()
    futs = [app.pool.exec.submit(work, k, rid) for k, rid in enumerate(ids)]
    games = {}
    for done, f in enumerate(futs, 1):
        rid, slots = f.result()
        g = games.setdefault(rid, len(games))
        w = rating_weight(idx.get(rid))
        for sl in slots:
            starts.append(n)
            y.append(n + sl["y"])
            gid.append(g)
            wts.append(w)
            X.append(np.asarray(sl["f"], dtype=np.float32))
            n += len(sl["f"])
        if done % 100 == 0 or done == len(futs):
            print(f"  [{done}/{len(futs)}] {len(starts)} decisiones · {(time.time() - t0) / 60:.1f} min", flush=True)
    X = np.concatenate(X) if X else np.zeros((0, len(names)), dtype=np.float32)
    rating = {g: idx.get(rid) or 0 for rid, g in games.items()}
    gid = np.asarray(gid)
    top = np.asarray([rating[g] >= 1500 for g in gid])
    crc = np.asarray([zlib.crc32(rid.encode()) for rid in games])
    return {"names": names, "X": X, "starts": np.asarray(starts), "y": np.asarray(y), "gid": gid,
            "w": np.asarray(wts, dtype=np.float64), "top": top, "test": top & (crc[gid] % 5 == 0)}


# ------------------------------------------------------------------ 2) aprender
def _seg(starts, n):
    ends = np.append(starts[1:], n)
    return starts, ends


def fit_clogit(X, starts, y, w, mask, l2=1.0, iters=25):
    """Logit condicional (softmax dentro de cada decisión) con L2, por Newton. mask: decisiones a usar."""
    sel = np.where(mask)[0]
    rows = np.concatenate([np.arange(starts[g], (starts[g + 1] if g + 1 < len(starts) else len(X))) for g in sel])
    lens = np.array([(starts[g + 1] if g + 1 < len(starts) else len(X)) - starts[g] for g in sel])
    st = np.concatenate([[0], np.cumsum(lens)[:-1]])
    Xs = X[rows].astype(np.float64)
    yc = np.concatenate([[0], np.cumsum(lens)[:-1]]) + (y[sel] - starts[sel])
    ws = w[sel]
    wrow = np.repeat(ws, lens)
    F = X.shape[1]
    beta = np.zeros(F)
    for _ in range(iters):
        z = Xs @ beta
        zmax = np.maximum.reduceat(z, st)
        ez = np.exp(z - np.repeat(zmax, lens))
        p = ez / np.repeat(np.add.reduceat(ez, st), lens)
        Ex = np.add.reduceat(p[:, None] * Xs, st)
        g = ((Xs[yc] - Ex) * ws[:, None]).sum(0) - l2 * beta
        H = (Xs * (p * wrow)[:, None]).T @ Xs - (Ex * ws[:, None]).T @ Ex + l2 * np.eye(F)
        step = np.linalg.solve(H, g)
        beta += step
        if np.abs(step).max() < 1e-6:
            break
    return beta


def eval_clogit(X, starts, y, mask, beta):
    acc, ll, n = 0.0, 0.0, 0
    N = len(X)
    for g in np.where(mask)[0]:
        a, b = starts[g], (starts[g + 1] if g + 1 < len(starts) else N)
        z = X[a:b].astype(np.float64) @ beta
        z -= z.max()
        lp = z - math.log(np.exp(z).sum())
        k = y[g] - a
        acc += float(np.argmax(z) == k)
        ll += float(lp[k])
        n += 1
    return {"acierto": acc / max(1, n), "logp": ll / max(1, n), "n": n}


def learn(data) -> Dict[str, Any]:
    X, starts, y, w = data["X"], data["starts"], data["y"], data["w"]
    test = data["test"]
    train = ~test
    names = data["names"]
    print(f"\nAprendiendo con {int(train.sum())} decisiones ({int(test.sum())} de 1500+ apartadas para la prueba)…", flush=True)
    best, best_ll, res = None, None, {}
    for l2 in (0.3, 3.0, 30.0):
        b = fit_clogit(X, starts, y, w, train, l2=l2)
        m = eval_clogit(X, starts, y, test, b)
        res[l2] = m
        print(f"  L2={l2}: acierto {100 * m['acierto']:.1f} % · log-verosimilitud {m['logp']:.3f}")
        if best_ll is None or m["logp"] > best_ll:
            best, best_ll = l2, m["logp"]
    # referencias: el prior heurístico de la app (sólo esa columna) y elegir al azar
    hi = names.index("prior_heur")
    Xh = np.zeros_like(X)
    Xh[:, hi] = X[:, hi]
    bh = fit_clogit(Xh, starts, y, w, train, l2=1.0)
    mh = eval_clogit(Xh, starts, y, test, bh)
    sizes = np.diff(np.append(starts, len(X)))
    unif = {"acierto": float(np.mean(1.0 / sizes[test])), "logp": float(np.mean(-np.log(sizes[test])))}
    print(f"  Referencias: prior heurístico de la app {100 * mh['acierto']:.1f} % ({mh['logp']:.3f}) · "
          f"al azar {100 * unif['acierto']:.1f} %")
    final = fit_clogit(X, starts, y, w, np.ones(len(starts), dtype=bool), l2=best)
    return {"names": names, "w": [round(float(v), 5) for v in final], "l2": best,
            "metricas": {"prueba": res[best], "heuristico": mh, "azar": unif, "decisiones": int(len(starts))}}


# ------------------------------------------------------------------ 3) evaluar
def analyze_games(app, sets: Dict[str, List[str]]) -> List[Dict[str, Any]]:
    out = []
    allg = [(name, rid) for name, ids in sets.items() for rid in ids]
    t0 = time.time()
    for n, (name, rid) in enumerate(allg, 1):
        try:
            log = fetch_log(rid)
        except Exception:
            continue
        for side in ("p1", "p2"):
            try:
                with app.lock:
                    parsed = app.pool.main.call("replay_parse", log=log, ourTeam=[], side=side)
            except Exception:
                continue
            won = parsed.get("winner") == parsed.get("ourName") if parsed.get("winner") else None
            for d in parsed["decisions"]:
                try:
                    t = app._analyze_turn(parsed, d, False)
                except Exception:
                    continue
                if t.get("skipped") or not t.get("_raw"):
                    continue
                out.append({"set": name, "replay": rid, "side": side, "turn": t["turn"], "won": won,
                            "mine": t["mine"], "cats": t.get("cats") or [], "raw": t["_raw"]})
        if n % 10 == 0 or n == len(allg):
            print(f"  [{n}/{len(allg)}] {len(out)} decisiones · {(time.time() - t0) / 60:.1f} min", flush=True)
    return out


def _prep(dec):
    """Lo que no depende de β: equilibrio y valores de cada jugada."""
    from .solver import regret_matching
    r = dec["raw"]
    M = np.array(r["M"], dtype=float)
    bad = set(r["bad"])
    mine = r["mine"]
    la = r.get("la")
    if la and la.get("endgame"):
        rows, cols = la["rows"], la["cols"]
        S = np.array(la["sub2"], dtype=float)
        row2, col2, _ = regret_matching(S.tolist(), 3000)
        q2 = S @ np.array(col2)
        if mine not in rows:
            return None
        return {"q": {rows[a]: float(q2[a]) for a in range(len(rows))}, "eq": row2[rows.index(mine)], "mine": mine, "lp": r.get("lp")}
    valid = [i for i in range(len(M)) if i not in bad]
    if mine not in valid:
        return None
    row, col, _ = regret_matching(M[valid].tolist(), 3000)
    q = M[valid] @ np.array(col)
    return {"q": {valid[k]: float(q[k]) for k in range(len(valid))}, "eq": row[valid.index(mine)], "mine": mine, "lp": r.get("lp")}


def regrade(p, beta, resc=None):
    q = p["q"]
    lp = p["lp"]
    if beta and lp:
        mx = max(lp[i] for i in q)
        q = {i: q[i] + beta * (lp[i] - mx) for i in q}
    best = max(q, key=q.get)
    loss = q[best] - q[p["mine"]]
    good = p["eq"] >= 0.10 or loss < 0.03
    if resc and lp and not good and loss < resc["perdida"]:
        mx = max(lp[i] for i in q)
        z = sum(math.exp(lp[i] - mx) for i in q)
        if math.exp(lp[p["mine"]] - mx) / z >= resc["p"]:
            good, loss = True, 0.0
    return good, loss


def sweep(decs) -> Dict[str, Any]:
    preps = [(d, _prep(d)) for d in decs]
    preps = [(d, p) for d, p in preps if p]
    table = []
    for beta, resc in [(b, r) for b in BETAS for r in RESCATES]:
        agree = defaultdict(list)
        side_loss = defaultdict(list)
        side_won = {}
        for d, p in preps:
            good, loss = regrade(p, beta, resc)
            agree[d["set"]].append(good)
            agree["todas"].append(good)
            side_loss[(d["replay"], d["side"])].append(loss)
            if d["won"] is not None:
                side_won[(d["replay"], d["side"])] = d["won"]
        # ¿quién comete menos (y menores) errores según la app gana la partida?
        hit, tot = 0, 0
        for rid in {k[0] for k in side_loss}:
            a, b = (rid, "p1"), (rid, "p2")
            if a in side_loss and b in side_loss and a in side_won and side_won[a] != side_won.get(b):
                la, lb = np.mean(side_loss[a]), np.mean(side_loss[b])
                if la == lb:
                    continue
                tot += 1
                hit += int((la < lb) == side_won[a])
        table.append({"beta": beta, "rescate": resc, "acuerdo": {k: round(100 * float(np.mean(v)), 1) for k, v in agree.items()},
                      "n": {k: len(v) for k, v in agree.items()},
                      "resultado": round(100 * hit / max(1, tot), 1), "partidas": tot})
        print(f"  β={beta:<6} rescate={'no' if not resc else str(resc['perdida']) + '/' + str(resc['p']):<9} acuerdo " + " · ".join(f"{k} {v} %" for k, v in table[-1]["acuerdo"].items()) +
              f" · predice el ganador en {table[-1]['resultado']} % de {tot} partidas", flush=True)
    base = table[0]
    best = None
    for row in table[1:]:
        gains = [row["acuerdo"][k] - base["acuerdo"][k] for k in base["acuerdo"] if k != "todas"]
        if min(gains) < 0 or row["resultado"] < base["resultado"] - 2.0:
            continue
        score = sum(gains)
        if score > 0 and (best is None or score > best[0]):
            best = (score, row)
    return {"tabla": table, "elegido": best[1] if best else None}


def report(model, ev, stamp):
    OUT.mkdir(parents=True, exist_ok=True)
    m = model["metricas"]
    rows = ""
    if ev:
        for r in ev["tabla"]:
            rs = "–" if not r.get("rescate") else f"{r['rescate']['perdida']} / {int(100 * r['rescate']['p'])} %"
            rows += (f"<tr><td>{r['beta']}</td><td>{rs}</td>" + "".join(f"<td>{r['acuerdo'].get(k, '–')} %</td>" for k in ("hoja", "ladder", "eval", "todas")) +
                     f"<td>{r['resultado']} %</td></tr>")
    ch = ev and ev.get("elegido")
    html = f"""<!doctype html><meta charset="utf-8"><title>Experiencia RooBot</title>
<style>body{{font:14px/1.5 system-ui;margin:24px;max-width:1000px;background:#10162a;color:#eef1fa}}td,th{{padding:6px 8px;border-bottom:1px solid #2a3558;text-align:left}}
table{{border-collapse:collapse;width:100%}}</style>
<h1>Experiencia · {time.strftime('%Y-%m-%d %H:%M')}</h1>
<p>Aprendida de {m['decisiones']} decisiones. En partidas de 1500+ que no usó, acierta la jugada exacta de cada Pokémon el
<b>{100 * m['prueba']['acierto']:.1f} %</b> de las veces (el prior heurístico de la app: {100 * m['heuristico']['acierto']:.1f} %; al azar: {100 * m['azar']['acierto']:.1f} %).</p>
{('<h2>Cuánto pesa al combinarla con el cálculo (β)</h2><table><tr><th>β</th><th>Rescate (pérdida / prob.)</th><th>Banco con hojas</th><th>Banco de ladder</th><th>Banco de evaluación</th><th>Todas</th><th>Predice el ganador</th></tr>' + rows + '</table>') if ev else ''}
<p>{('✔ Activada con β = <b>' + str(ch['beta']) + '</b> y rescate ' + json.dumps(ch.get('rescate')) + '.') if ch else '✖ Ningún β mejora el acuerdo en todos los bancos sin empeorar la predicción del resultado: la experiencia queda sólo como información.'}</p>"""
    (OUT / f"informe_{stamp}.html").write_text(html, encoding="utf-8")
    return OUT / f"informe_{stamp}.html"


def main() -> None:
    ap = argparse.ArgumentParser(description="Aprender «experiencia» de decisiones de jugadores fuertes")
    ap.add_argument("--rating-min", type=int, default=1300)
    ap.add_argument("--sin-evaluar", action="store_true")
    ap.add_argument("--workers", type=int, default=None)
    a = ap.parse_args()
    os.environ["ROOBOT_SEMILLA_FIJA"] = "1"
    os.environ["ROOBOT_DUMP"] = "1"
    from .app import App
    OUT.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M")
    ev_ids = ensure_eval_bank()
    ids = training_ids(a.rating_min)
    print(f"Partidas para aprender: {len(ids)} (rating ≥ {a.rating_min}, sin los bancos de prueba ni de evaluación)")
    app = App(a.workers or 0, [REG.equipos], ROOT / "logs" / "calibracion" / "_tmp")
    try:
        data = extract(app, ids)
        model = learn(data)
        prev = {}
        try:
            prev = json.loads(EXP.read_text(encoding="utf-8"))
        except Exception:
            prev = {}
        info = {"activo": False, "beta": 0.0, "fecha": time.strftime("%Y-%m-%d %H:%M"), **model}
        EXP.write_text(json.dumps(info, ensure_ascii=False, indent=1), encoding="utf-8")
        for e in app.pool.engines:
            e.call("policy_reload")
        ev = None
        if not a.sin_evaluar:
            sets = {"hoja": load_bank(True), "ladder": load_bank(False), "eval": ev_ids}
            print(f"\nEvaluando con {sum(len(v) for v in sets.values())} partidas apartadas (se guardan los cálculos)…", flush=True)
            decs = analyze_games(app, sets)
            (OUT / f"evaluacion_{stamp}.json").write_text(json.dumps(decs), encoding="utf-8")
            print("\nProbando cuánto peso darle a la experiencia (β):", flush=True)
            ev = sweep(decs)
            if ev["elegido"]:
                info.update(activo=True, beta=ev["elegido"]["beta"], rescate=ev["elegido"]["rescate"])
            info["evaluacion"] = ev
            EXP.write_text(json.dumps(info, ensure_ascii=False, indent=1), encoding="utf-8")
        elif prev.get("activo"):
            info.update(activo=True, beta=prev.get("beta", 0))      # sin evaluar se mantiene la decisión anterior
            EXP.write_text(json.dumps(info, ensure_ascii=False, indent=1), encoding="utf-8")
        (OUT / "ultimo.json").write_text(json.dumps({k: v for k, v in info.items() if k not in ("w",)}, ensure_ascii=False, indent=1),
                                         encoding="utf-8")
        rep = report(model, ev, stamp)
        if info["activo"]:
            print(f"\n✔ Experiencia ACTIVADA (β = {info['beta']}). Reinicia app.bat para usarla.")
        else:
            print("\n✖ No mejora lo bastante: queda desactivada (la app la muestra sólo como información).")
        print(f"Informe: {rep}")
    finally:
        app.pool.close()


if __name__ == "__main__":
    main()
