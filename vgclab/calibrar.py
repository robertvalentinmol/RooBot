"""Calibración del juez: ¿cuánto coincide la app con jugadores fuertes?

Descarga repeticiones públicas de Champions con rating alto (o lee las de una carpeta), analiza cada
decisión de AMBOS jugadores con el mismo análisis que «Analizar partida» y resume:
  - acuerdo: % de decisiones que la app considera buenas (correctas o dentro de la estrategia mixta);
  - pérdida media que la app les atribuye;
  - acuerdo por tipo de decisión (proteger, foco, Fake Out, Mega...);
  - las mayores discrepancias, con enlace al turno, para revisarlas a mano.

No sirve para imitar a nadie: es el control de calidad del análisis. Si los mejores jugadores «fallan»
mucho en una categoría, lo más probable es que el fallo sea de la app en esa categoría.

Además puede ENSEÑAR al juez (--aprender): con las posiciones de cada turno y quién ganó la partida
ajusta por regresión logística los pesos con los que la app valora una posición (vida, Pokémon vivos,
cambios de stats, Viento Afín, Espacio Raro, Mega por usar, quién es más rápido, quién amenaza KO...).
Se valida con repeticiones que no se usaron para aprender y sólo se activa si predice mejor que el juez
de siempre (engine/juez.json). Se aprende del RESULTADO de las partidas, no de lo que jugó cada uno.

Banco de prueba fijo: la medición del acuerdo usa siempre las mismas repeticiones (logs/calibracion/banco.json),
que nunca se usan para aprender. Así dos calibraciones se pueden comparar de verdad.

Uso (con internet):
    python -m vgclab.calibrar                                      # medir el acuerdo con el banco fijo
    python -m vgclab.calibrar --juez manual                        # lo mismo con el juez de siempre (referencia)
    python -m vgclab.calibrar --nuevas --replays 30                # medir con repeticiones recientes (no el banco)
    python -m vgclab.calibrar --aprender --replays 500 --rating 1500   # enseñar al juez (descarga más)
    python -m vgclab.calibrar --aprender --solo-guardadas          # enseñar al juez sólo con las ya descargadas
    python -m vgclab.calibrar --carpeta mis_repeticiones           # .html/.log/.json descargados
Las repeticiones descargadas se guardan en logs/calibracion/replays/ y se reutilizan.
El informe queda en logs/calibracion/ (HTML para abrir en el navegador y JSON para la app).
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import time
import zlib
import urllib.request
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional

from .app import App, CATEGORIAS
from .rutas import ROOT

FORMATS = ["gen9championsvgc2026regmc", "gen9championsvgc2026regmcbo3"]
UA = {"User-Agent": "Mozilla/5.0 (RooBot; calibracion personal)"}


def _get(url: str, timeout: float = 25):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _index() -> Dict[str, Any]:
    f = ROOT / "logs" / "calibracion" / "replays" / "indice.json"
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_index(idx: Dict[str, Any]) -> None:
    d = ROOT / "logs" / "calibracion" / "replays"
    d.mkdir(parents=True, exist_ok=True)
    (d / "indice.json").write_text(json.dumps(idx), encoding="utf-8")


def search_replays(n: int, min_rating: int, max_pages: int = 100) -> List[Dict[str, Any]]:
    """Repeticiones públicas con rating ≥ min_rating (buscador de replay.pokemonshowdown.com).
    Recorre hacia atrás en el tiempo hasta reunir n o agotar las páginas; si no llega a n, sigue con las
    que haya (no se para). Apunta el rating de cada una en un índice para poder filtrar después."""
    out, seen = [], set()
    idx = _index()
    for fmt in FORMATS:
        before, mode, page, scanned = None, "before", 1, 0
        for _ in range(max_pages):
            url = f"https://replay.pokemonshowdown.com/search.json?format={fmt}"
            if mode == "before" and before:
                url += f"&before={before}"
            elif mode == "page" and page > 1:
                url += f"&page={page}"
            try:
                rows = _get(url)
            except Exception as e:
                print(f"  (búsqueda: {e})")
                break
            if not rows:
                break
            new = 0
            for r in rows:
                rid = r.get("id")
                if not rid or rid in seen or r.get("private"):
                    continue
                seen.add(rid)
                new += 1
                if r.get("rating"):
                    idx[rid] = r.get("rating")
                if (r.get("rating") or 0) >= min_rating:
                    out.append(r)
            scanned += new
            if new == 0:
                if mode == "before":            # el buscador no admite «before»: probar con páginas
                    mode, page = "page", 2
                    continue
                break
            before = min((r.get("uploadtime") or 0) for r in rows) or None
            page += 1
            if len(out) >= n or len(rows) < 2:
                break
            if scanned and scanned % 500 < len(rows):
                print(f"  {fmt}: {scanned} revisadas · {len(out)} con rating ≥ {min_rating}", flush=True)
        if len(out) >= n:
            break
    _save_index(idx)
    out.sort(key=lambda r: -(r.get("rating") or 0))
    return out[:n]


def load_folder(folder: Path) -> List[Dict[str, Any]]:
    items = []
    for f in sorted(folder.rglob("*")):
        if f.suffix.lower() in (".html", ".htm", ".log", ".txt", ".json") and f.is_file():
            items.append({"id": f.stem, "text": f.read_text(encoding="utf-8", errors="replace"), "rating": None})
    return items


REPLAY_DIR = ROOT / "logs" / "calibracion" / "replays"


def fetch_log(rid: str) -> str:
    """Log de una repetición (de la caché local o descargándola y guardándola)."""
    f = REPLAY_DIR / f"{rid}.log"
    if f.exists():
        return f.read_text(encoding="utf-8")
    log = _get(f"https://replay.pokemonshowdown.com/{rid}.json").get("log", "")
    if log:
        REPLAY_DIR.mkdir(parents=True, exist_ok=True)
        f.write_text(log, encoding="utf-8")
    return log


def _sigmoid(z):
    import numpy as np
    return 1 / (1 + np.exp(-np.clip(z, -30, 30)))


# Signo obligatorio de cada peso: +1 = sólo puede sumar a tu favor, -1 = sólo puede restar, 0 = libre.
# El juez aprende CUÁNTO pesa cada cosa, pero no puede aprender que estar quemado es bueno.
SIGNOS = {"vivos": 1, "ps": 1, "estados": -1, "ataque+": 1, "velocidad+": 1, "defensa+": 1, "viento_afin": 1,
          "espacio_raro": 1, "pantallas": 1, "mega_por_usar": 1, "en_campo": 1, "mas_rapido": 1, "amenaza_ko": 1,
          "quemado_fisico": -1, "paralizado": -1, "dormido": -1, "envenenado": -1, "pantallas_util": 1,
          "banda_intacta": 1, "baya": 1, "intimidate_banco": 1, "fakeout_banco": 1, "redireccion": 1,
          "clima_campo": 1, "amenaza_banco": 1, "ps_mejor": 1, "presion": 1, "presion_banco": 1}
FASES = ("inicio", "medio", "final")
NOMBRES = {"vivos": "Pokémon vivos", "ps": "vida restante", "estados": "estados alterados", "ataque+": "subidas de ataque",
           "velocidad+": "subidas de velocidad", "defensa+": "subidas de defensa", "viento_afin": "Viento Afín",
           "espacio_raro": "Espacio Raro a favor", "pantallas": "pantallas", "mega_por_usar": "Mega por usar",
           "en_campo": "Pokémon en el campo", "mas_rapido": "ser más rápido en el campo", "amenaza_ko": "amenazar KO primero",
           "quemado_fisico": "atacante físico quemado", "paralizado": "paralizado", "dormido": "dormido/congelado",
           "envenenado": "envenenado", "pantallas_util": "pantallas útiles", "banda_intacta": "Banda Focus intacta",
           "baya": "baya sin usar", "intimidate_banco": "Intimidate en el banquillo", "fakeout_banco": "Fake Out en el banquillo",
           "redireccion": "redirección", "clima_campo": "clima/campo a favor", "amenaza_banco": "amenaza KO desde el banquillo",
           "ps_mejor": "vida del más sano", "presion": "presión en el campo (daño que haces)",
           "presion_banco": "presión desde el banquillo"}
LAMBDAS = (0.3, 1.0, 3.0, 10.0, 30.0, 100.0, 300.0, 1000.0)


def _newton(X, y, l2, free, iters=60, sw=None, off=None):
    import numpy as np
    n = X.shape[1]
    off = np.zeros(len(y)) if off is None else off
    w = np.zeros(n)
    idx = np.where(free)[0]
    if not len(idx):
        return w
    Xf = X[:, idx]
    lam = (np.full(n, float(l2)) if np.isscalar(l2) else np.asarray(l2, dtype=float))[idx]
    sw = np.ones(len(y)) if sw is None else sw
    wf = np.zeros(len(idx))
    for _ in range(iters):
        p = _sigmoid(off + Xf @ wf)
        g = Xf.T @ (sw * (p - y)) + lam * wf
        H = (Xf * (sw * p * (1 - p))[:, None]).T @ Xf + np.diag(lam + 1e-9)
        step = np.linalg.solve(H, g)
        wf -= step
        if np.abs(step).max() < 1e-7:
            break
    w[idx] = wf
    return w


def fit_logistic(X, y, l2=1.0, iters: int = 60, signs=None, sample_weight=None, offset=None):
    """Regresión logística sin término independiente (simétrica) por Newton, con L2 (escalar o por peso)
    y pesos por muestra. signs: +1/-1/0 por rasgo; los que salgan con el signo contrario se fijan a 0."""
    import numpy as np
    n = X.shape[1]
    free = np.ones(n, dtype=bool)
    lam = np.full(n, float(l2)) if np.isscalar(l2) else np.asarray(l2, dtype=float)
    sw = np.ones(len(y)) if sample_weight is None else np.asarray(sample_weight, dtype=float)
    off = np.zeros(len(y)) if offset is None else np.asarray(offset, dtype=float)
    if signs is None:
        return _newton(X, y, lam, free, iters, sw, off)
    sg = np.array([s or 0 for s in signs], dtype=float)
    w = np.zeros(n)
    for _ in range(3 * n):
        w = _newton(X, y, lam, free, iters, sw, off)
        bad = free & (sg * w < -1e-9)
        if bad.any():                       # el peor que se salga de su signo queda fijo a 0
            j = int(np.argmin(np.where(bad, sg * w, np.inf)))
            free[j] = False
            continue
        g = X.T @ (sw * (_sigmoid(off + X @ w) - y)) + lam * w
        back = (~free) & (sg * g < -1e-6)   # fijo a 0 pero quiere moverse hacia su lado permitido: se libera
        if back.any():
            free[int(np.argmin(np.where(back, sg * g, np.inf)))] = True
            continue
        break
    w[sg * w < 0] = 0.0
    return w


def _metrics(p, y):
    import numpy as np
    p = np.clip(p, 1e-4, 1 - 1e-4)
    return {"logloss": float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p))),
            "brier": float(np.mean((p - y) ** 2)), "acierto": float(np.mean((p > 0.5) == (y > 0.5)))}


def rating_weight(r) -> float:
    """Peso de una partida según su rating: las de 1500+ mandan; las de menos ayudan, con menos peso."""
    if r is None:
        return 0.5
    return 1.0 if r >= 1500 else 0.5 if r >= 1400 else 0.25 if r >= 1300 else 0.1


RASGOS = ROOT / "logs" / "calibracion" / "rasgos.json"


def _extract(items, app):
    """Posiciones (rasgos + juez de siempre + fase) de cada repetición, con caché en disco por versión de rasgos."""
    with app.lock:
        names = app.pool.main.call("judge_features", states=[])["names"]
    sig = "|".join(names) + "|v4"   # v3: sets completados con el uso real; v4: presión (emparejamientos)
    try:                            # si cambian los parámetros del juez de siempre, cambia su valoración (hz)
        sig += "|" + str(zlib.crc32((ROOT / "engine" / "parametros.json").read_bytes()))
    except Exception:
        pass
    try:
        cache = json.loads(RASGOS.read_text(encoding="utf-8"))
        if cache.get("sig") != sig:
            cache = {"sig": sig, "rows": {}}
    except Exception:
        cache = {"sig": sig, "rows": {}}
    store = cache["rows"]
    new = 0
    for n, it in enumerate(items, 1):
        rid = it.get("id")
        if rid in store:
            continue
        try:
            log = app._replay_text(it["text"]) if it.get("text") else fetch_log(rid)
        except Exception as e:
            print(f"  [{n}] {rid}: no se pudo leer ({e})")
            continue
        out = []
        for side in ("p1", "p2"):
            try:
                with app.lock:
                    parsed = app.pool.main.call("replay_parse", log=log, ourTeam=[], side=side)
            except Exception:
                continue
            win = parsed.get("winner")
            states = [d["state"] for d in parsed["decisions"]]
            if not win or not states:
                continue
            y = 1 if win == parsed["ourName"] else 0
            with app.lock:
                res = app.pool.main.call("judge_features", states=states)
            for d, r in zip(parsed["decisions"], res["rows"]):
                if r:
                    out.append({"s": side, "t": d["turn"], "f": [round(x, 5) for x in r["f"]], "hz": round(r["hz"], 5),
                                "h": round(r["h"], 5), "fase": r["fase"], "y": y})
        store[rid] = out
        new += 1
        if new % 25 == 0:
            print(f"  [{n}/{len(items)}] rasgos de {new} repeticiones nuevas", flush=True)
            RASGOS.write_text(json.dumps(cache), encoding="utf-8")
    RASGOS.write_text(json.dumps(cache), encoding="utf-8")
    return names, store


def _predict_judge(j, names, F, HZ, PH):
    """Probabilidad de ganar según un juez guardado (v1: sólo rasgos; v2: juez de siempre + correcciones por fase)."""
    import numpy as np
    idx = [names.index(n) for n in j["names"]]
    if j.get("fases"):
        z = np.zeros(len(HZ))
        for k in FASES:
            m = PH == k
            ph = j["fases"][k]
            z[m] = ph["a"] * HZ[m] + F[m][:, idx] @ np.array(ph["w"], dtype=float)
        return _sigmoid(z)
    return _sigmoid(F[:, idx] @ np.array(j["w"], dtype=float))


def learn(items, app, out_dir: Path, exclude=None) -> Dict[str, Any]:
    """Enseña al juez con las posiciones de las repeticiones y quién ganó (sin tocar las del banco de prueba).
    Modelo por fase de la partida (inicio / medio / final): z = a·(juez de siempre) + Σ w·rasgo, con signos
    obligatorios y L2 hacia 0 en las correcciones: si los datos no lo justifican, queda el juez de siempre."""
    import numpy as np
    import zlib
    exclude = set(exclude or ())
    items = [it for it in items if it.get("id") not in exclude]
    rating = {it.get("id"): it.get("rating") for it in items}
    names, store = _extract(items, app)
    rows = []
    for it in items:
        rid = it.get("id")
        rs = store.get(rid) or []
        last = {}
        for r in rs:
            last[r["s"]] = max(last.get(r["s"], 1), r["t"])
        for r in rs:
            rows.append((rid, r, 0.5 + 0.5 * r["t"] / max(1, last[r["s"]])))
    if len(rows) < 300:
        raise SystemExit(f"Muy pocas posiciones para aprender ({len(rows)}); descarga más repeticiones (--replays 300)")
    F = np.array([r["f"] for _, r, _ in rows], dtype=float)
    HZ = np.array([r["hz"] for _, r, _ in rows], dtype=float)
    H = np.array([r["h"] for _, r, _ in rows], dtype=float)
    PH = np.array([r["fase"] for _, r, _ in rows])
    y = np.array([r["y"] for _, r, _ in rows], dtype=float)
    R = np.array([rating.get(rid) if rating.get(rid) is not None else -1 for rid, _, _ in rows], dtype=float)
    top = R >= 1500
    SW = np.array([rating_weight(rating.get(rid)) * pw for rid, _, pw in rows])
    crc = np.array([zlib.crc32(str(rid).encode()) for rid, _, _ in rows])
    test = top & (crc % 5 == 0)                        # ~20 % de las partidas de 1500+, aparte
    if test.sum() < 50:                                # (repeticiones sin rating, p. ej. de una carpeta)
        top = np.ones(len(rows), dtype=bool)
        test = crc % 5 == 0
    val = (~test) & ((crc // 5) % 4 == 0)              # para elegir la regularización
    X = np.column_stack([HZ, F])                       # columna 0: corrección al peso del juez de siempre
    signs = [0] + [SIGNOS.get(n, 0) for n in names]
    fold = (crc // 5) % 4                              # validación cruzada por partidas (4 grupos)

    def base_a(mask):                                  # el de siempre, calibrado: z = a0·hz
        return float(fit_logistic(HZ[mask][:, None], y[mask], l2=1e-4, sample_weight=SW[mask])[0]) if mask.sum() else 1.0

    def fit(mask, lam, a_0):
        # z = a0·hz + (δ·hz + Σ w·rasgo); δ y w penalizados hacia 0: sin datos que lo justifiquen, queda el de siempre
        w = fit_logistic(X[mask], y[mask], l2=lam, signs=signs, sample_weight=SW[mask], offset=a_0 * HZ[mask])
        w = w.copy()
        w[0] += a_0
        return w

    model, chosen, base = {}, {}, {}
    for k in FASES:
        tr = (~test) & (PH == k)
        base[k] = np.concatenate([[base_a(tr)], np.zeros(F.shape[1])])
        if tr.sum() < 200:
            model[k] = base[k]
            chosen[k] = None
            continue
        cv = {}
        for lam in LAMBDAS:
            tot, cnt = 0.0, 0
            for g in range(4):
                fit_m = tr & (fold != g)
                ev = tr & (fold == g)
                ev = ev & top if (ev & top).sum() >= 50 else ev
                if not ev.sum():
                    continue
                w = fit(fit_m, lam, base_a(fit_m))
                tot += _metrics(_sigmoid(X[ev] @ w), y[ev])["logloss"] * ev.sum()
                cnt += ev.sum()
            cv[lam] = tot / max(1, cnt)
        best = min(cv.values())
        chosen[k] = max(l for l, v in cv.items() if v <= best + 0.001)   # la más prudente entre las casi mejores
        model[k] = fit(tr, chosen[k], base[k][0])

    def predict(mdl, mask):
        z = np.zeros(mask.sum())
        sub = np.where(mask)[0]
        for k in FASES:
            m = PH[sub] == k
            z[m] = X[sub[m]] @ mdl[k]
        return _sigmoid(z)

    m_new = _metrics(predict(model, test), y[test])
    m_base = _metrics(predict(base, test), y[test])
    m_raw = _metrics((H[test] + 1) / 2, y[test])
    m_act = None                                       # el aprendido que estaba activo (orientativo)
    try:
        old = json.loads((ROOT / "engine" / "juez.json").read_text(encoding="utf-8"))
        if old.get("activo"):
            m_act = _metrics(_predict_judge(old, names, F[test], HZ[test], PH[test]), y[test])
            m_act["fecha"] = old.get("fecha")
    except Exception:
        m_act = None
    enough = len(rows) >= 1500 and int(test.sum()) >= 300
    better = enough and m_new["logloss"] < m_base["logloss"] - 0.003 and m_new["acierto"] >= m_base["acierto"] - 0.005
    fases = {}
    for k in FASES:                                    # versión final con todos los datos
        msk = PH == k
        if chosen.get(k) is None:
            w = np.concatenate([[base_a(msk)], np.zeros(F.shape[1])])
        else:
            w = fit(msk, chosen[k], base_a(msk))
        fases[k] = {"a": round(float(w[0]), 4), "w": [round(float(x), 4) for x in w[1:]], "lambda": chosen.get(k),
                    "posiciones": int(msk.sum())}
    info = {"activo": bool(better), "version": 2, "fecha": time.strftime("%Y-%m-%d %H:%M"), "names": names, "fases": fases,
            "signos": True, "banco_excluido": len(exclude),
            "metricas": {"posiciones": len(rows), "partidas": len({rid for rid, _, _ in rows}),
                         "partidas_1500": len({rid for rid, _, _ in rows if (rating.get(rid) or 0) >= 1500}),
                         "prueba": int(test.sum()), "nuevo": m_new, "anterior_calibrado": m_base, "anterior": m_raw,
                         "activo_antes": m_act}}
    jf = ROOT / "engine" / "juez.json"
    if better:
        if jf.exists():                                                # copia del que había, por si acaso
            (out_dir / "juez_anterior.json").write_text(jf.read_text(encoding="utf-8"), encoding="utf-8")
        jf.write_text(json.dumps(info, ensure_ascii=False, indent=1), encoding="utf-8")
    elif not (m_act and jf.exists()):                                  # si no mejora, el activo que hubiera se queda
        jf.write_text(json.dumps(info, ensure_ascii=False, indent=1), encoding="utf-8")
    info["se_queda_el_activo"] = bool(not better and m_act)
    (out_dir / "juez_ultimo.json").write_text(json.dumps(info, ensure_ascii=False, indent=1), encoding="utf-8")
    return info


BANCO = ROOT / "logs" / "calibracion" / "banco.json"              # ladder (sin hojas)
BANCO_HOJA = ROOT / "logs" / "calibracion" / "banco_hoja.json"    # con hojas abiertas (como en torneo)


def _bank_file(hoja: bool) -> Path:
    return BANCO_HOJA if hoja else BANCO


def load_bank(hoja: bool = False) -> List[str]:
    try:
        return list(json.loads(_bank_file(hoja).read_text(encoding="utf-8")).get("ids", []))
    except Exception:
        return []


BANCO_EVAL = ROOT / "logs" / "calibracion" / "banco_eval.json"   # partidas apartadas para evaluar la experiencia


def all_bank_ids() -> List[str]:
    """Las repeticiones de cualquiera de los bancos (ninguna se usa para aprender)."""
    ev = []
    try:
        ev = list(json.loads(BANCO_EVAL.read_text(encoding="utf-8")).get("ids", []))
    except Exception:
        ev = []
    return list(dict.fromkeys(load_bank(False) + load_bank(True) + ev))


def has_sheets(rid: str) -> bool:
    """¿La repetición trae las hojas abiertas de los dos jugadores?"""
    f = REPLAY_DIR / f"{rid}.log"
    try:
        return f.read_text(encoding="utf-8").count("|showteam|") >= 2
    except Exception:
        return False


def make_bank(n: int, min_rating: int, max_pages: int, hoja: bool = False, download: bool = True) -> List[str]:
    """Elige el banco de prueba: las n repeticiones guardadas de más rating (≥ min_rating); con hoja=True, sólo
    las que tienen hojas abiertas (si faltan, descarga partidas Bo3 conocidas por el buscador)."""
    other = set(load_bank(not hoja))

    def cached():
        idx = _index()
        got = [(idx.get(f.stem), f.stem) for f in REPLAY_DIR.glob("*.log")] if REPLAY_DIR.exists() else []
        ok = [(r, i) for r, i in got if r is not None and r >= min_rating and i not in other]
        if hoja:
            ok = [(r, i) for r, i in ok if has_sheets(i)]
        return sorted(ok, reverse=True)
    have = cached()
    if len(have) < n and download:
        print(f"Sólo hay {len(have)} repeticiones guardadas que sirvan: descargo más para el banco…")
        if hoja:
            idx = _index()
            cand = sorted(((r, i) for i, r in idx.items() if r and r >= min_rating and not (REPLAY_DIR / f"{i}.log").exists()),
                          key=lambda x: (("bo3" not in x[1]), -x[0]))
            tries = 0
            for r, rid in cand:
                if len(have) >= n or tries >= 400:
                    break
                tries += 1
                try:
                    fetch_log(rid)
                except Exception:
                    continue
                if has_sheets(rid):
                    have.append((r, rid))
            have = cached()
        else:
            for it in search_replays(n, min_rating, max_pages):
                try:
                    fetch_log(it["id"])
                except Exception:
                    pass
            have = cached()
    ids = [i for _, i in have[:n]]
    if not ids:
        raise SystemExit("No hay repeticiones para el banco de prueba (prueba con --rating más bajo)")
    f = _bank_file(hoja)
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps({"creado": time.strftime("%Y-%m-%d %H:%M"), "rating_min": min_rating, "hojas": hoja,
                             "ids": ids}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"Banco de prueba {'con hojas abiertas' if hoja else 'de ladder'} creado: {len(ids)} repeticiones "
          f"(rating {have[len(ids) - 1][0]}–{have[0][0]}). Ya no se usarán para aprender.")
    return ids


def ensure_bank(n: int, min_rating: int, max_pages: int, remake: bool = False, hoja: bool = False,
                download: bool = True) -> List[str]:
    ids = [] if remake else load_bank(hoja)
    return ids or make_bank(n, min_rating, max_pages, hoja, download)


PRUEBA_REVISION = ROOT / "logs" / "calibracion" / "prueba_revision.json"


def prueba_revision(app, name: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """La prueba con tu revisión de la app (logs/revision.csv): vuelve a analizar cada desacuerdo que juzgaste
    con el criterio actual y cuenta
      · errores falsos: decisiones en las que tú diste la razón al jugador (o a los dos) y la app aún dice «Error»;
      · aciertos de la app: decisiones en las que tú diste la razón a la app y la app sigue diciendo «Error».
    Un cambio del criterio es bueno si baja los errores falsos sin perder aciertos de la app."""
    f = ROOT / "logs" / "revision.csv"
    if not f.exists():
        print("Prueba con tu revisión: aún no has revisado desacuerdos («Revisar la app»).")
        return None
    with open(f, newline="", encoding="utf-8") as fh:
        rows = [r for r in csv.DictReader(fh) if r.get("veredicto") in ("jugador", "ambas", "app")]
    seen, casos = set(), []
    for r in rows:                          # si una decisión se juzgó dos veces, cuenta la última
        k = (r["replay"], r["lado"], r["turno"])
        if k in seen:
            casos = [c for c in casos if (c["replay"], c["lado"], c["turno"]) != k]
        seen.add(k)
        casos.append(r)
    print(f"Prueba con tu revisión: {len(casos)} decisiones juzgadas…", flush=True)
    res, t0 = [], time.time()
    for i, r in enumerate(casos, 1):
        try:
            log = fetch_log(r["replay"])
            with app.lock:
                parsed = app.pool.main.call("replay_parse", log=log, ourTeam=[], side=r["lado"])
            d = next(dd for dd in parsed["decisions"] if dd["turn"] == int(r["turno"]))
            t = app._analyze_turn(parsed, d, False)
            tone = "skip" if t.get("skipped") else t["tone"]
        except Exception as e:
            tone = "skip"
        res.append({"replay": r["replay"], "lado": r["lado"], "turno": r["turno"], "veredicto": r["veredicto"], "tone": tone})
        if i % 10 == 0:
            print(f"  {i}/{len(casos)} · {(time.time() - t0) / 60:.0f} min", flush=True)
    ok = [x for x in res if x["tone"] != "skip"]
    jug = [x for x in ok if x["veredicto"] in ("jugador", "ambas")]
    appr = [x for x in ok if x["veredicto"] == "app"]
    out = {"fecha": time.strftime("%Y-%m-%d %H:%M"), "n": len(ok),
           "falsos": sum(x["tone"] == "bad" for x in jug), "n_jugador": len(jug),
           "aciertos": sum(x["tone"] == "bad" for x in appr), "n_app": len(appr), "casos": res}
    print(f"  errores falsos: {out['falsos']} de {out['n_jugador']} · aciertos de la app que se mantienen: "
          f"{out['aciertos']} de {out['n_app']}")
    if name:
        d = ROOT / "logs" / "calibracion" / "experimentos"
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{name}_revision.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    else:
        hist = []
        try:
            hist = json.loads(PRUEBA_REVISION.read_text(encoding="utf-8")).get("historial", [])
        except Exception:
            pass
        hist.append({k: out[k] for k in ("fecha", "n", "falsos", "n_jugador", "aciertos", "n_app")})
        PRUEBA_REVISION.parent.mkdir(parents=True, exist_ok=True)
        PRUEBA_REVISION.write_text(json.dumps(dict(out, historial=hist[-30:]), ensure_ascii=False, indent=1), encoding="utf-8")
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="Calibra el análisis de la app con repeticiones de jugadores fuertes")
    ap.add_argument("--replays", type=int, default=30)
    ap.add_argument("--rating", type=int, default=1500, help="rating mínimo de la repetición (ladder)")
    ap.add_argument("--carpeta", default=None, help="analizar repeticiones guardadas en una carpeta en vez de descargarlas")
    ap.add_argument("--workers", type=int, default=None)
    ap.add_argument("--profundo", action="store_true", help="más jugadas, mundos y semillas por decisión (más lento)")
    ap.add_argument("--dos-turnos", action="store_true", help="analizar cada decisión mirando 2 turnos (como el modo «2 turnos» de la app)")
    ap.add_argument("--paginas", type=int, default=100, help="páginas del buscador a revisar como máximo (≈50 repeticiones cada una)")
    ap.add_argument("--aprender", action="store_true", help="enseñar al juez con las repeticiones (posiciones + quién ganó)")
    ap.add_argument("--solo-guardadas", action="store_true", help="con --aprender: no buscar nuevas, usar sólo las descargadas")
    ap.add_argument("--rating-min-peso", type=int, default=1300,
                    help="con --aprender: también usa partidas desde este rating, con menos peso (1500+ = 1; 1400 = ½; 1300 = ¼)")
    ap.add_argument("--max-descargas", type=int, default=1500,
                    help="con --aprender: máximo de repeticiones conocidas pero no descargadas que bajar (de rating ≥ --rating-min-peso)")
    ap.add_argument("--nuevas", action="store_true", help="medir con repeticiones recientes en vez del banco de prueba fijo")
    ap.add_argument("--banco", type=int, default=30, help="tamaño del banco de prueba fijo (al crearlo)")
    ap.add_argument("--rehacer-banco", action="store_true", help="volver a elegir el banco de prueba")
    ap.add_argument("--banco-ladder", action="store_true",
                    help="medir con el banco de ladder (sin hojas) en vez del de hojas abiertas (como en torneo)")
    ap.add_argument("--experimento", default=None,
                    help="(para los experimentos) guarda el resultado aparte, sin tocar el historial ni «Mi progreso»")
    ap.add_argument("--juez", choices=["actual", "manual"], default="actual",
                    help="manual = medir con el juez de siempre aunque haya uno aprendido (para comparar)")
    ap.add_argument("--prueba-revision", action="store_true",
                    help="sólo la prueba con tu revisión de la app: errores falsos y aciertos con el criterio actual")
    a = ap.parse_args()
    os.environ["ROOBOT_SEMILLA_FIJA"] = "1"        # mismos mundos posibles en cada calibración: comparaciones justas
    if a.juez == "manual":
        os.environ["ROOBOT_JUEZ"] = "manual"
    if a.prueba_revision:
        prueba_revision(App(a.workers or 0, ["teams/mc"], ROOT / "logs" / "calibracion" / "_tmp"), a.experimento)
        return

    out_dir = ROOT / "logs" / "calibracion"
    out_dir.mkdir(parents=True, exist_ok=True)
    bank: List[str] = []
    use_bank = False
    if a.carpeta:
        items = load_folder(Path(a.carpeta))
        print(f"{len(items)} repeticiones en {a.carpeta}")
    elif a.aprender:
        # las repeticiones de los dos bancos de prueba nunca se usan para aprender
        for hoja in (False, True):
            try:
                ensure_bank(a.banco, a.rating, a.paginas, a.rehacer_banco and (hoja != a.banco_ladder), hoja,
                            download=not a.solo_guardadas)
            except SystemExit as e:
                print(f"  (banco {'con hojas' if hoja else 'de ladder'}: {e})")
        bank = all_bank_ids()
        if a.solo_guardadas:
            items = []
        else:
            print(f"Buscando repeticiones de Champions con rating ≥ {a.rating}…")
            items = search_replays(a.replays + len(bank), a.rating, a.paginas)
            print(f"{len(items)} encontradas con rating ≥ {a.rating}")
    elif a.nuevas:
        print(f"Buscando repeticiones de Champions con rating ≥ {a.rating}…")
        items = search_replays(a.replays, a.rating, a.paginas)
        print(f"{len(items)} encontradas con rating ≥ {a.rating}")
    else:
        hoja = not a.banco_ladder
        bank = ensure_bank(a.banco, a.rating, a.paginas, a.rehacer_banco, hoja)
        idx = _index()
        items = [{"id": i, "rating": idx.get(i)} for i in bank]
        use_bank = "hoja" if hoja else "ladder"
        print(f"Banco de prueba fijo {'con hojas abiertas (como en torneo)' if hoja else 'de ladder (sin hojas)'}: "
              f"{len(items)} repeticiones · juez {'de siempre (manual)' if a.juez == 'manual' else 'actual'}")
    if not items and not (a.aprender and a.solo_guardadas):
        raise SystemExit("No hay repeticiones que analizar (prueba con --rating más bajo o --carpeta)")

    app = App(a.workers or 0, ["teams/mc"], ROOT / "logs" / "calibracion" / "_tmp")
    if a.aprender:
        # también las que ya estaban guardadas de otras veces
        have = {it.get("id") for it in items} | set(bank)
        extra, low, down = 0, 0, 0
        rmin = min(a.rating, a.rating_min_peso)
        if not a.carpeta:
            idx = _index()
            cached = {f.stem for f in REPLAY_DIR.glob("*.log")} if REPLAY_DIR.exists() else set()
            for rid in sorted(cached):
                r = idx.get(rid)
                if rid not in have and r is not None and r >= rmin:           # guardadas de otras veces
                    items.append({"id": rid, "rating": r})
                    have.add(rid)
                    extra += 1
                    low += r < a.rating
            if not a.solo_guardadas:                                          # conocidas por el buscador, sin descargar
                for rid, r in sorted(idx.items(), key=lambda x: -(x[1] or 0)):
                    if down >= a.max_descargas:
                        break
                    if rid not in have and rid not in cached and r is not None and r >= rmin:
                        items.append({"id": rid, "rating": r})
                        have.add(rid)
                        down += 1
        if not a.carpeta:
            nb = sum(1 for it in items if it.get("id") in set(bank))
            print(f"Nuevas encontradas: {len(items) - extra - nb - down} · ya guardadas de otras veces: {extra}"
                  f" (de ellas {low} entre {rmin} y {a.rating - 1}, con menos peso) · por descargar de menos rating: {down}"
                  f" · apartadas para el banco de prueba: {len(bank)}")
            if len(items) - nb < a.replays and not a.solo_guardadas:
                print(f"  (No hay {a.replays} con ese rating ahora mismo: sigo con {len(items)}. Puedes volver a "
                      "ejecutarlo otro día y se irán sumando.)")
        print(f"Aprendiendo con {sum(1 for it in items if it.get('id') not in set(bank))} repeticiones…")
        try:
            info = learn(items, app, out_dir, exclude=bank)
        finally:
            app.pool.close()
        m = info["metricas"]
        print(f"\n{m['posiciones']} posiciones de {m['partidas']} partidas ({m['partidas_1500']} de 1500+; "
              f"{m['prueba']} posiciones de 1500+ apartadas para la prueba).")
        print(f"Juez nuevo:    log-loss {m['nuevo']['logloss']:.4f} · acierto {100 * m['nuevo']['acierto']:.1f} %")
        print(f"Juez de siempre: log-loss {m['anterior_calibrado']['logloss']:.4f} · acierto {100 * m['anterior_calibrado']['acierto']:.1f} % (calibrado)")
        if m.get("activo_antes"):
            ma = m["activo_antes"]
            print(f"Juez aprendido que había ({ma.get('fecha')}): log-loss {ma['logloss']:.4f} · acierto {100 * ma['acierto']:.1f} % "
                  "(orientativo: pudo ver estas partidas al aprender)")
        print("Lo aprendido, por fase (peso del juez de siempre «a» y correcciones; signo obligatorio; 0 = no aporta):")
        for k in FASES:
            ph = info["fases"][k]
            extra_w = sorted(((n_, w_) for n_, w_ in zip(info["names"], ph["w"]) if abs(w_) >= 0.01), key=lambda x: -abs(x[1]))
            print(f"  {k:6s} ({ph['posiciones']} posiciones) a={ph['a']:.2f} · " +
                  (", ".join(f"{NOMBRES.get(n_, n_)} {w_:+.2f}" for n_, w_ in extra_w[:8]) or "sin correcciones"))
        if info["activo"]:
            print("\n✔ Juez nuevo ACTIVADO (reinicia app.bat para usarlo).")
        elif m["posiciones"] < 1500 or m["prueba"] < 300:
            print("\n✖ Aún hay pocos datos para fiarse (hacen falta unas 1.500 posiciones): descarga más repeticiones "
                  "(--replays 300) y vuelve a ejecutarlo. El nuevo no se activa.")
        else:
            print("\n✖ El juez nuevo no predice mejor que el de siempre: no se activa.")
        if not info["activo"] and info.get("se_queda_el_activo"):
            print(f"  Sigue activo el juez aprendido del {m['activo_antes'].get('fecha')}.")
        return
    decisions: List[Dict[str, Any]] = []
    t0 = time.time()
    try:
        for n, it in enumerate(items, 1):
            rid = it.get("id")
            try:
                log = app._replay_text(it["text"]) if it.get("text") else fetch_log(rid)
            except Exception as e:
                print(f"  [{n}] {rid}: no se pudo leer ({e})")
                continue
            for side in ("p1", "p2"):
                try:
                    with app.lock:
                        parsed = app.pool.main.call("replay_parse", log=log, ourTeam=[], side=side)
                except Exception as e:
                    print(f"  [{n}] {rid} {side}: {str(e).splitlines()[0][:120]}")
                    continue
                for d in parsed["decisions"]:
                    try:
                        t = app._analyze_turn(parsed, d, a.profundo, a.dos_turnos)
                    except Exception as e:
                        continue
                    if t.get("skipped"):
                        continue
                    decisions.append({"replay": rid, "rating": it.get("rating"), "player": parsed["ourName"], "side": side,
                                      "turn": t["turn"], "tone": t["tone"], "loss": t["loss"], "mine": t["mine"], "best": t["best"],
                                      "partial": t.get("partial", False), "cats": t.get("cats") or [],
                                      "open": parsed.get("openSheets", False),
                                      # detalle para estudiar los desacuerdos (va a un archivo aparte)
                                      "_opts": [{k: o.get(k) for k in ("label", "q", "eq", "q2", "eq2", "mine", "best")}
                                                for o in (t.get("options") or [])[:8]],
                                      "_exp": (t.get("expected") or [])[:5], "_value": t.get("value"),
                                      "_look": t.get("look"), "_endgame": t.get("endgame"), "_raw": t.get("_raw")})
            el = time.time() - t0
            print(f"  [{n}/{len(items)}] {rid} · {len(decisions)} decisiones analizadas · {el / 60:.1f} min", flush=True)
    finally:
        app.pool.close()

    if not decisions:
        raise SystemExit("No se pudo analizar ninguna decisión")
    good = [d for d in decisions if d["tone"] == "good"]
    summary = {"fecha": time.strftime("%Y-%m-%d %H:%M"), "replays": len(items), "decisiones": len(decisions),
               "acuerdo": round(100 * len(good) / len(decisions), 1),
               "aceptables": round(100 * sum(d["tone"] == "ok" for d in decisions) / len(decisions), 1),
               "perdida_media": round(sum(d["loss"] for d in decisions) / len(decisions), 3),
               "con_hoja": round(100 * sum(d["open"] for d in decisions) / len(decisions), 1)}
    by_cat = defaultdict(lambda: [0, 0])
    for d in decisions:
        for c in d["cats"]:
            by_cat[c["cat"]][0] += 1
            by_cat[c["cat"]][1] += int(bool(c["ok"]))
    summary["categorias"] = sorted(({"cat": k, "label": CATEGORIAS.get(k, k), "n": v[0], "acuerdo": round(100 * v[1] / v[0], 1)}
                                    for k, v in by_cat.items() if v[0] >= 5), key=lambda x: x["acuerdo"])
    worst = [{k: v for k, v in d.items() if not k.startswith("_")} for d in sorted(decisions, key=lambda d: -d["loss"])[:25]]
    summary["discrepancias"] = worst
    stamp = time.strftime("%Y%m%d_%H%M")
    if a.experimento:
        ex = out_dir / "experimentos"
        ex.mkdir(parents=True, exist_ok=True)
        slim = [{k: d.get(k) for k in ("replay", "side", "turn", "tone", "loss", "mine", "best", "cats")} for d in decisions]
        (ex / f"{a.experimento}_{use_bank or 'otras'}.json").write_text(
            json.dumps({"resumen": summary, "params": os.environ.get("ROOBOT_PARAMS", ""), "decisiones": slim}, ensure_ascii=False),
            encoding="utf-8")
        print(f"\nExperimento {a.experimento}: acuerdo {summary['acuerdo']} % ({len(decisions)} decisiones)")
        return
    (out_dir / f"calibracion_{stamp}_detalle.json").write_text(json.dumps(decisions, ensure_ascii=False), encoding="utf-8")
    try:
        jf = ROOT / "engine" / "juez.json"
        jj = json.loads(jf.read_text(encoding="utf-8")) if jf.exists() else {}
        summary["juez"] = "de siempre" if (a.juez == "manual" or not jj.get("activo")) else f"aprendido {jj.get('fecha', '')}".strip()
        if a.dos_turnos:
            summary["juez"] += " · 2 turnos"
        if a.profundo:
            summary["juez"] += " · profundo"
    except Exception:
        summary["juez"] = "de siempre"
    summary["banco"] = use_bank
    (out_dir / f"calibracion_{stamp}.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    prev = out_dir / "ultima.json"
    hist = []
    if prev.exists():
        try:
            old = json.loads(prev.read_text(encoding="utf-8"))
            hist = old.get("historial", []) + [{k: old.get(k) for k in ("fecha", "acuerdo", "aceptables", "perdida_media", "decisiones", "juez", "banco", "categorias")}]
        except Exception:
            hist = []
    summary["historial"] = hist[-20:]
    (out_dir / "ultima.json").write_text(json.dumps(summary, ensure_ascii=False), encoding="utf-8")
    btype = lambda h: "ladder" if h.get("banco") is True else h.get("banco")
    same = [h for h in hist + [summary] if btype(h) == use_bank] if use_bank else []
    cmp_html = ""
    if len(same) > 1:
        cmp_html = ("<h2>Con el mismo banco de prueba</h2><table><tr><th>Fecha</th><th>Juez</th><th>Acuerdo</th><th>+ aceptables</th></tr>" +
                    "".join(f"<tr><td>{h.get('fecha')}</td><td>{h.get('juez')}</td><td><b>{h.get('acuerdo')} %</b></td>"
                            f"<td>{round((h.get('acuerdo') or 0) + (h.get('aceptables') or 0), 1)} %</td></tr>" for h in same) + "</table>")
    rows = "".join(f"<tr><td>{c['label']}</td><td>{c['n']}</td><td><b>{c['acuerdo']} %</b></td></tr>" for c in summary["categorias"])
    disc = "".join(
        f"<tr><td><a href='https://replay.pokemonshowdown.com/{d['replay']}' target='_blank'>{d['replay']}</a> T{d['turn']}</td>"
        f"<td>{d['rating'] or ''}</td><td>{d['player']}</td><td>{d['mine']}</td><td>{d['best']}</td><td>{d['loss']:.2f}</td></tr>"
        for d in worst)
    html = f"""<!doctype html><meta charset="utf-8"><title>Calibración RooBot</title>
<style>body{{font:14px/1.5 system-ui;margin:24px;max-width:1100px;background:#10162a;color:#eef1fa}}td,th{{padding:5px 8px;border-bottom:1px solid #2a3558;text-align:left}}
table{{border-collapse:collapse;width:100%;margin:10px 0 24px}}a{{color:#f5c542}}b{{color:#fff}}.k{{font-size:28px;font-weight:700;color:#f5c542}}</style>
<h1>Calibración del análisis · {summary['fecha']}</h1>
<p>{summary['replays']} repeticiones{(' del <b>banco de prueba fijo ' + ('con hojas abiertas' if use_bank == 'hoja' else 'de ladder') + '</b>') if use_bank else ' recientes'} · juez <b>{summary['juez']}</b> · {summary['decisiones']} decisiones de ambos jugadores · {summary['con_hoja']} % con hoja abierta.</p>{cmp_html}
<p><span class="k">{summary['acuerdo']} %</span> de sus decisiones la app las da por buenas · {summary['aceptables']} % aceptables · pérdida media {summary['perdida_media']}</p>
<h2>Acuerdo por tipo de decisión (de menos a más)</h2><p>Donde el acuerdo es bajo, revisa las discrepancias: o la app se equivoca en esa categoría o es un error suyo.</p>
<table><tr><th>Decisión</th><th>N</th><th>Acuerdo</th></tr>{rows}</table>
<h2>Mayores discrepancias</h2><table><tr><th>Repetición</th><th>Rating</th><th>Jugador</th><th>Jugó</th><th>La app prefería</th><th>Pérdida</th></tr>{disc}</table>"""
    rep = out_dir / f"calibracion_{stamp}.html"
    rep.write_text(html, encoding="utf-8")
    print(f"\nAcuerdo: {summary['acuerdo']} % · pérdida media {summary['perdida_media']} · juez {summary['juez']}"
          f"{(' · banco fijo ' + ('con hojas' if use_bank == 'hoja' else 'de ladder')) if use_bank else ''}")
    for h in (same[:-1] if use_bank else []):
        print(f"  antes, mismo banco ({h.get('fecha')}, juez {h.get('juez')}): {h.get('acuerdo')} %")
    print(f"Informe: {rep}")


if __name__ == "__main__":
    main()
