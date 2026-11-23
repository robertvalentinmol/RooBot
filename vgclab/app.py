"""Ejercicios de posición con interfaz gráfica (estilo Showdown), en tu navegador.

    python -m vgclab.app                      # abre http://127.0.0.1:8765
    python -m vgclab.app --puerto 8765 --workers 2

Sólo escucha en tu PC (127.0.0.1). Los sprites se cargan del servidor público de Showdown,
igual que hace la web de Showdown, así que necesitas conexión a internet para verlos.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import csv
import json
import os
import socket
import urllib.request
import random
import re
import threading
import time
import zlib
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, urlparse

from .engine_client import EngineError, EnginePool
from .explicar import explain_endgame, duel_matrix, explain_selection, selection_weaknesses, explain_lookahead, explain_position, explain_preview, explain_best, classify, CATEGORIAS, _k
from .rutas import ROOT
from .regulacion import REG, carpetas_de_otras
from . import revision as revisar
from . import sprites

WEB = ROOT / "web"
APP_VERSION = "34"
# análisis a 2 turnos: respuestas por bando en el turno siguiente, semillas y mezcla con la valoración de 1 turno
# umbrales de pérdida (escala −1…+1) para llamar «error» a una jugada: validados viendo si la posición empeora después
LOSS_ERROR = 0.20
LOSS_GRAVE = 0.35
# Veredicto prudente (v33). En tu revisión de 55 desacuerdos con jugadores de 1500+, cuando el jugador hizo una
# jugada «de plan» (proteger, cambiar, estado, preparar, control de velocidad) en una ranura donde la app prefería
# atacar, la app sólo tenía razón 1 de cada 28 veces: el cálculo de un turno infravalora lo que rinde después.
PLAN_DMG = {"icywind", "electroweb", "bulldoze", "rocktomb", "mudshot", "snarl", "strugglebug", "nuzzle"}
VEREDICTO_PRUDENTE = "Discutible: jugada de plan que el cálculo de un turno infravalora"
LOOK_K2 = int(os.environ.get("ROOBOT_LOOK_K2", "3"))
LOOK_SEEDS = int(os.environ.get("ROOBOT_LOOK_SEEDS", "8"))
LOOK_BLEND = float(os.environ.get("ROOBOT_LOOK_BLEND", "1.0"))   # la página comprueba que el servidor es de su misma versión


class Repaso:
    """Errores guardados para repasar con repetición espaciada: 1, 3, 7 y 21 días.
    Si lo aciertas sube de nivel; si fallas, vuelve a mañana. Tras acertarlo 4 veces se da por dominado."""
    INTERVALOS = [1, 3, 7, 21]

    def __init__(self, path: Path):
        self.path = path
        self.lock = threading.Lock()
        try:
            self.items: List[Dict[str, Any]] = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            self.items = []

    def _save(self) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.items, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.path)

    def add(self, item: Dict[str, Any]) -> None:
        with self.lock:
            now = time.time()
            item.update({"id": uuid.uuid4().hex[:10], "creado": time.strftime("%Y-%m-%d %H:%M"), "nivel": 0,
                         "repasos": 0, "due": now + 86400 * self.INTERVALOS[0], "historial": []})
            self.items.append(item)
            if len(self.items) > 400:          # tope: se olvidan los más antiguos
                self.items = self.items[-400:]
            self._save()

    def stats(self) -> Dict[str, int]:
        now = time.time()
        return {"pendientes": sum(1 for i in self.items if i["due"] <= now), "total": len(self.items)}

    def next(self) -> Optional[Dict[str, Any]]:
        with self.lock:
            if not self.items:
                return None
            now = time.time()
            due = sorted((i for i in self.items if i["due"] <= now), key=lambda i: i["due"])
            item = due[0] if due else min(self.items, key=lambda i: i["due"])
            item["_adelantado"] = not due
            return item

    def update(self, rid: str, ok: bool, eleccion: str, loss: float) -> Dict[str, Any]:
        with self.lock:
            item = next((i for i in self.items if i["id"] == rid), None)
            if not item:
                return {}
            antes = {"eleccion": item["eleccion"], "perdida": item["perdida"], "fecha": item["creado"]}
            item["repasos"] = item.get("repasos", 0) + 1
            item["historial"].append({"fecha": time.strftime("%Y-%m-%d %H:%M"), "eleccion": eleccion,
                                      "perdida": round(loss, 3), "ok": ok})
            if ok:
                item["nivel"] += 1
                if item["nivel"] >= len(self.INTERVALOS):
                    self.items.remove(item)
                    self._save()
                    return {"repaso": True, "ok": True, "dominado": True, "antes": antes}
            else:
                item["nivel"] = 0
            dias = self.INTERVALOS[item["nivel"]]
            item["due"] = time.time() + 86400 * dias
            self._save()
            return {"repaso": True, "ok": ok, "dominado": False, "proximo": dias, "antes": antes}


class Quiz:
    """Entrenador de velocidades y daños: historial y repaso espaciado de las preguntas falladas."""
    INTERVALOS = [1, 3, 7]

    def __init__(self, logs: Path):
        self.csv = logs / "velocidades.csv"
        self.path = logs / "velocidades_repaso.json"
        self.lock = threading.Lock()
        try:
            self.review: List[Dict[str, Any]] = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            self.review = []

    def _save(self):
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.review, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.path)

    def due(self) -> Optional[Dict[str, Any]]:
        now = time.time()
        d = [r for r in self.review if r["due"] <= now]
        return min(d, key=lambda r: r["due"]) if d else None

    def record(self, q: Dict[str, Any], ok: bool, ms: int, timeout: bool, from_review: bool) -> Dict[str, Any]:
        with self.lock:
            with open(self.csv, "a", newline="", encoding="utf-8") as f:
                w = csv.writer(f)
                if f.tell() == 0:
                    w.writerow(["fecha", "tipo", "correcto", "ms", "agotado", "clave"])
                w.writerow([time.strftime("%Y-%m-%d %H:%M"), q["type"], int(ok), ms, int(timeout), q["key"]])
            item = next((r for r in self.review if r["q"]["key"] == q["key"]), None)
            info: Dict[str, Any] = {}
            if item:
                if ok:
                    item["nivel"] += 1
                    if item["nivel"] >= len(self.INTERVALOS):
                        self.review.remove(item)
                        info = {"dominada": True}
                    else:
                        item["due"] = time.time() + 86400 * self.INTERVALOS[item["nivel"]]
                        info = {"proximo": self.INTERVALOS[item["nivel"]]}
                else:
                    item["nivel"] = 0
                    item["due"] = time.time() + 86400
                    info = {"proximo": 1}
            elif not ok:
                self.review.append({"q": q, "nivel": 0, "due": time.time() + 86400})
                info = {"guardada": True}
            self._save()
            return info

    def stats(self) -> Dict[str, Any]:
        if not self.csv.exists():
            return {"n": 0, "pendientes": sum(1 for r in self.review if r["due"] <= time.time())}
        rows = list(csv.DictReader(open(self.csv, encoding="utf-8")))
        last = rows[-60:]
        pct = lambda rs: round(100 * sum(int(r["correcto"]) for r in rs) / len(rs)) if rs else None
        streak = 0
        for r in reversed(rows):
            if r["correcto"] != "1":
                break
            streak += 1
        return {"n": len(rows), "acierto": pct(last), "velocidad": pct([r for r in last if r["tipo"] == "speed"]),
                "dano": pct([r for r in last if r["tipo"] == "damage"]), "orden": pct([r for r in last if r["tipo"] == "orden"]), "racha": streak,
                "pendientes": sum(1 for r in self.review if r["due"] <= time.time()), "guardadas": len(self.review)}


class Debilidades:
    """Aciertos por categoría de decisión (proteger, foco, Mega...), de ejercicios, repasos y partidas."""

    def __init__(self, logs: Path):
        self.csv = logs / "categorias.csv"
        self.lock = threading.Lock()

    def record(self, origen: str, tipo: str, cats: List[Dict[str, Any]]) -> None:
        if not cats:
            return
        with self.lock, open(self.csv, "a", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            if f.tell() == 0:
                w.writerow(["fecha", "origen", "tipo", "categoria", "acierto"])
            for c in cats:
                w.writerow([time.strftime("%Y-%m-%d %H:%M"), origen, tipo, c["cat"], int(bool(c["ok"]))])

    def stats(self, window: int = 400) -> Dict[str, Any]:
        rows = list(csv.DictReader(open(self.csv, encoding="utf-8")))[-window:] if self.csv.exists() else []
        out = []
        for cat, label in CATEGORIAS.items():
            rs = [r for r in rows if r["categoria"] == cat]
            n = len(rs)
            ok = sum(int(r["acierto"]) for r in rs)
            last = rs[-15:]
            out.append({"cat": cat, "label": label, "n": n, "pct": round(100 * ok / n) if n else None,
                        "errores": n - ok, "reciente": round(100 * sum(int(r["acierto"]) for r in last) / len(last)) if last else None})
        ranked = sorted((c for c in out if c["n"] >= 3), key=lambda c: (c["pct"], -c["n"]))
        return {"categorias": out, "peor": ranked[0] if ranked else None, "total": len(rows)}


class Rating:
    """Rating tipo Elo por área (táctica y team preview). Cada ejercicio tiene una dificultad (de 1000 a
    2000 según cuánto se pierde con las jugadas tentadoras) y tu rating sube o baja según tus puntos."""

    AREAS = ("tactico", "preview")

    def __init__(self, path: Path):
        self.path = path
        self.lock = threading.Lock()
        try:
            self.data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            self.data = {}
        for a in self.AREAS:
            self.data.setdefault(a, {"r": 1500.0, "n": 0, "hist": []})

    def update(self, area: str, difficulty: float, points: float) -> Dict[str, Any]:
        with self.lock:
            d = self.data[area]
            expected = 1 / (1 + 10 ** ((difficulty - d["r"]) / 400))
            k = 40 if d["n"] < 30 else 24          # al principio se mueve más rápido
            delta = k * (points / 100 - expected)
            before = d["r"]
            d["r"] = round(d["r"] + delta, 1)
            d["n"] += 1
            d["hist"].append([time.strftime("%Y-%m-%d %H:%M"), d["r"], round(difficulty), round(points), before])
            d["hist"] = d["hist"][-2000:]
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.data, ensure_ascii=False), encoding="utf-8")
            tmp.replace(self.path)
            return {"area": area, "rating": round(d["r"]), "delta": round(delta, 1), "difficulty": round(difficulty),
                    "expected": round(100 * expected), "n": d["n"]}

    def summary(self) -> Dict[str, Any]:
        out = {}
        for a in self.AREAS:
            d = self.data[a]
            h = d["hist"]
            week = [x for x in h if x[0] >= time.strftime("%Y-%m-%d", time.localtime(time.time() - 7 * 86400))]
            start = (week[0][4] if len(week[0]) > 4 else week[0][1]) if week else d["r"]
            out[a] = {"r": round(d["r"]), "n": d["n"], "week": round(d["r"] - start)}
        return out


def difficulty_from(q: List[float], prior: List[float], valid: List[int]) -> float:
    """Dificultad de una decisión: cuánto se pierde con la jugada que más tienta (la favorita de la
    heurística) y lo poco que perdonan las demás."""
    qs = sorted((q[i] for i in valid), reverse=True)
    if len(qs) < 2:
        return 1000.0
    best = qs[0]
    pt = max(valid, key=lambda i: prior[i])               # la jugada «obvia»
    trap = max(0.0, best - q[pt])
    med = qs[len(qs) // 2]
    spread = max(0.0, best - med)
    close = sum(1 for x in qs if best - x < 0.03)          # varias igual de buenas = más fácil
    x = 0.6 * min(1.0, trap / 0.35) + 0.4 * min(1.0, spread / 0.5) - 0.08 * max(0, close - 1)
    return 1000 + 1000 * max(0.0, min(1.0, x))


class App:
    def __init__(self, workers: int, rivals: List[str], logs: Path):
        self.pool = EnginePool(workers)
        self.lock = threading.Lock()          # el motor principal atiende una petición a la vez
        self.positions: Dict[str, Dict[str, Any]] = {}
        self.logs = logs
        self.logs.mkdir(parents=True, exist_ok=True)
        self.rival_dirs = rivals
        self.repaso = Repaso(self.logs / "repaso.json")
        self.quiz = Quiz(self.logs)
        self.debil = Debilidades(self.logs)
        self.rating = Rating(self.logs / "rating.json")
        self.quiz_pending: Dict[str, Dict[str, Any]] = {}

    # ------------------------------------------------------------------ equipos
    def team_files(self) -> List[Dict[str, str]]:
        out = []
        otras = carpetas_de_otras()    # equipos de otras regulaciones: no se mezclan con los de la activa
        for base in [ROOT / "teams"] + [Path(r) for r in self.rival_dirs]:
            base = base if base.is_absolute() else ROOT / base
            if not base.exists():
                continue
            for f in sorted(base.rglob("*.txt")):
                if any(o in f.parents for o in otras if o not in base.parents and o != base):
                    continue
                try:
                    rel = str(f.relative_to(ROOT))
                except ValueError:
                    rel = str(f.resolve())
                out.append({"path": rel.replace("\\", "/"), "name": f.stem})
        seen, uniq = set(), []
        for t in out:
            if t["path"] not in seen:
                seen.add(t["path"])
                uniq.append(t)
        return uniq

    def _read_team(self, rel: str) -> List[Dict[str, Any]]:
        if rel not in {t["path"] for t in self.team_files()}:
            raise ValueError("equipo desconocido")
        p = (ROOT / rel).resolve()
        with self.lock:
            return self.pool.main.call("import", text=p.read_text(encoding="utf-8"))

    # ------------------------------------------------------------------ ejercicios
    def new_position(self, my_team: str, rival: str, kind: str, realistic: bool = False) -> Dict[str, Any]:
        ours = self._read_team(my_team)
        with self.lock:
            v = self.pool.main.call("validate", team=ours)
        if not v["ok"]:
            raise ValueError(f"Tu equipo no es válido para {REG.nombre}: " + "; ".join(v["problems"][:3]))
        rival_file = self._pick_rival(my_team, rival)
        theirs = self._read_team(rival_file)
        focus = None
        if kind.startswith("focus"):
            # ejercicios dirigidos: posiciones en las que importa esa categoría (o tu punto más débil)
            focus = kind.split(":", 1)[1] if ":" in kind else ((self.debil.stats()["peor"] or {}).get("cat") or "protect")
            if focus not in CATEGORIAS:
                focus = "protect"
            pos = None
            for i in range(40):
                k = random.choice(["lead", "medio", "medio", "final"])
                if i and i % 8 == 0 and not rival:
                    rival_file = self._pick_rival(my_team, rival)
                    theirs = self._read_team(rival_file)
                with self.lock:
                    cand = self.pool.main.call("gen_position", ours=ours, theirs=theirs, kind=k, maxAlive=2)
                    tags = self.pool.main.call("position_tags", state=cand["state"])["tags"]
                if focus in tags:
                    pos = cand
                    break
            if pos is None:
                raise ValueError(f"No encuentro posiciones de «{CATEGORIAS[focus]}» con tu equipo; prueba otra categoría")
        elif kind in ("critica", "lectura"):
            pos, crit = self._critical_position(ours, theirs, my_team, rival, kind)
        else:
            if kind == "mixto":
                kind = random.choice(["lead", "medio", "final"])
            with self.lock:
                pos = self.pool.main.call("gen_position", ours=ours, theirs=theirs, kind=kind, maxAlive=2)
        out = self._present(pos, ours, theirs, my_team, rival_file, realistic)
        if kind in ("critica", "lectura"):
            self.positions[out["id"]].update({"difficulty": crit["difficulty"], "critical": crit})
            out["critical"] = crit
        if focus:
            self.positions[out["id"]]["focus"] = {"cat": focus, "label": CATEGORIAS[focus]}
            out["focus"] = {"cat": focus, "label": CATEGORIAS[focus]}
        return out

    def _critical_position(self, ours, theirs, my_team, rival, mode: str = "critica"):
        """Busca una posición en la que equivocarse cuesta: la jugada que más tienta pierde bastante o casi
        todas las opciones son claramente peores que la mejor. Los finales ya decididos se descartan."""
        best_pos, best_crit = None, None
        for attempt in range(14):
            k = random.choices(["medio", "final", "lead"], weights=[0.45, 0.35, 0.2])[0]
            with self.lock:
                pos = self.pool.main.call("gen_position", ours=ours, theirs=theirs, kind=k, maxAlive=2)
            res = self.pool.search([pos["state"]], K=8, Kopp=8, seeds=6, mega_policy="free")
            M, col = res["matrix"], res["col"]
            bad = set(res.get("bad_rows", []))
            valid = [i for i in range(len(M)) if i not in bad]
            if len(valid) < 3:
                continue
            q = [sum(M[i][j] * col[j] for j in range(len(col))) for i in range(len(M))]
            if self._is_endgame(pos["state"]) and (min(q[i] for i in valid) > 0.75 or max(q[i] for i in valid) < -0.75):
                continue                                   # final ya decidido
            prior = [o.get("prior", 0) for o in res["ours"]]
            best = max(valid, key=lambda i: q[i])
            pt = max(valid, key=lambda i: prior[i])
            trap = q[best] - q[pt]
            qs = sorted((q[i] for i in valid), reverse=True)
            spread = qs[0] - qs[len(qs) // 2]
            diff = difficulty_from(q, prior, valid)
            crit = {"difficulty": round(diff), "trap": round(trap, 3), "spread": round(spread, 3), "kind": k, "mode": mode}
            if mode == "lectura":
                # 50/50 de verdad: el equilibrio mezcla 2+ jugadas y lo que pasa depende mucho de lo que haga él
                row = res["row"]
                sup = [i for i in valid if row[i] >= 0.2]
                cs = [j for j in range(len(col)) if col[j] >= 0.15]
                rng_ = max((M[i][j] for i in sup for j in cs), default=0) - min((M[i][j] for i in sup for j in cs), default=0)
                score = (len(sup) >= 2) * (len(cs) >= 2) * rng_
                crit.update({"difficulty": round(max(diff, 1300 + 600 * min(1.0, rng_ / 0.8))), "range": round(rng_, 3)})
                if best_crit is None or score > best_crit.get("score", 0):
                    best_pos, best_crit = pos, dict(crit, score=score)
                if score >= 0.35 and max(row[i] for i in valid) <= 0.75:
                    return pos, crit
                continue
            if best_crit is None or diff > best_crit["difficulty"]:
                best_pos, best_crit = pos, crit
            if trap >= LOSS_ERROR or (spread >= 0.35 and diff >= 1400):
                return pos, crit
        if best_pos is None:
            raise ValueError("No encontré una posición así con estos equipos; prueba con otro rival")
        return best_pos, best_crit

    def _register(self, entry: Dict[str, Any]) -> str:
        pid = uuid.uuid4().hex[:12]
        self.positions[pid] = entry
        if len(self.positions) > 200:
            for k in list(self.positions)[:100]:
                self.positions.pop(k, None)
        return pid

    def _present(self, pos, ours, theirs, my_team, rival_file, realistic, review=None) -> Dict[str, Any]:
        """Prepara lo que ve la interfaz de una posición (nueva o de repaso)."""
        with self.lock:
            view = self.pool.main.call("describe", state=pos["state"])
            sheet = self.pool.main.call("sheet", team=theirs)
            my_sheet = self.pool.main.call("sheet", team=ours, mine=True)
        # en los leads el banquillo rival aún no se ha visto; en modo realista tampoco los que no ha sacado
        hidden = pos["kind"] == "lead" or realistic
        if pos["kind"] == "lead":
            view["sides"][1]["pokemon"] = [m for m in view["sides"][1]["pokemon"] if m["active"]]
        elif realistic:
            unseen = {p["set"]["species"] for p in pos["state"]["sides"][1]["pokemon"] if not p.get("revealed", True)}
            view["sides"][1]["pokemon"] = [m for m in view["sides"][1]["pokemon"]
                                           if not any(m["name"] == u or m["baseSpecies"] == u or u.startswith(m["baseSpecies"] + "-") for u in unseen)]
        pid = self._register({"pos": pos, "rival": Path(rival_file).stem, "t0": time.time(), "ours": ours, "theirs": theirs,
                              "my_team": my_team, "rival_file": rival_file, "realistic": realistic,
                              "review_id": review["id"] if review else None})
        return {"id": pid, "kind": pos["kind"], "turn": pos["turn"], "rival": Path(rival_file).stem,
                "preview": pos["preview"], "hiddenBench": hidden, "view": view, "theirsSheet": sheet,
                "sheets": {"ours": my_sheet, "theirs": sheet}, "realistic": realistic,
                "review": self._review_info(review)}

    @staticmethod
    def _is_endgame(state) -> bool:
        """Final: a cada bando le quedan 2 Pokémon o menos (contando el banquillo)."""
        alive = lambda side: sum(1 for p in side["pokemon"] if not p.get("fainted") and (p.get("hp") is None or p["hp"] > 0))
        return all(alive(sd) <= 2 for sd in state["sides"])

    @staticmethod
    def _core_matrix(res, mine, best) -> Optional[Dict[str, Any]]:
        """El corazón del «50/50»: las jugadas que usa el equilibrio (y la tuya) contra sus respuestas probables."""
        row, col, M = res["row"], res["col"], res["matrix"]
        rows = sorted((i for i in range(len(row)) if row[i] >= 0.1), key=lambda i: -row[i])[:4]
        for extra in (best, mine):
            if extra not in rows:
                rows.append(extra)
        cols = sorted((j for j in range(len(col)) if col[j] >= 0.1), key=lambda j: -col[j])[:4]
        if len(cols) < 2 or sum(1 for i in rows if row[i] >= 0.15) < 2:
            return None
        return {"rows": [{"label": res["ours"][i]["label"], "p": round(row[i], 3), "mine": i == mine, "best": i == best} for i in rows],
                "cols": [{"label": res["theirs"][j]["label"], "p": round(col[j], 3)} for j in cols],
                "m": [[round(M[i][j], 3) for j in cols] for i in rows]}

    def _lookahead(self, worlds, extras, K, res, q, mine, best, valid, deep, endgame: bool = False) -> Dict[str, Any]:
        """Refina las mejores jugadas mirando un turno más (cambios forzados + turno siguiente) o, en un
        final, jugando la partida hasta el final."""
        col = res["col"]
        rows: List[int] = []
        for i in [mine, best] + sorted(valid, key=lambda i: -q[i])[:4]:
            if i not in rows:
                rows.append(i)
        rows = rows[:5]
        order = sorted(range(len(col)), key=lambda j: -col[j])
        cols = [j for j in order if col[j] >= 0.03][:5] or order[:1]
        if endgame:
            res2 = self.pool.search(worlds, K=K, Kopp=K, seeds=(24 if deep else 12),
                                    extra_ours=extras, mega_policy="free",
                                    endgame={"K2": 3, "maxTurns": 10}, only_rows=rows, only_cols=cols)
        else:
            res2 = self.pool.search(worlds, K=K, Kopp=K, seeds=(LOOK_SEEDS * 2 if deep else LOOK_SEEDS),
                                    extra_ours=extras, mega_policy="free",
                                    lookahead={"K2": LOOK_K2}, only_rows=rows, only_cols=cols)
        if [o["choice"] for o in res2["ours"]] != [o["choice"] for o in res["ours"]]:
            raise ValueError("candidatos distintos en la búsqueda a 2 turnos")
        # a 2 turnos la valoración tiene más ruido: se mezcla con la de 1 turno (en los finales manda la de verdad)
        alpha = 1.0 if endgame else LOOK_BLEND
        M1 = res["matrix"]
        sub = [[alpha * res2["matrix"][i][j] + (1 - alpha) * M1[i][j] for j in cols] for i in rows]
        from .solver import regret_matching
        row2, col2, v2 = regret_matching(sub)
        q2 = {i: sum(sub[a][b] * col2[b] for b in range(len(cols))) for a, i in enumerate(rows)}
        return {"rows": rows, "cols": cols, "sub": sub, "row2": dict(zip(rows, row2)), "endgame": endgame,
                "sub2_raw": [[res2["matrix"][i][j] for j in cols] for i in rows],
                "col2": dict(zip(cols, col2)), "q2": q2, "value": v2, "best": max(rows, key=lambda i: q2[i])}

    def _endgame_lines(self, state, res, la, mine, best) -> Dict[str, Any]:
        """Líneas esperadas (hasta el final) con la mejor jugada y con la tuya, contra su respuesta más probable."""
        j = max(la["col2"], key=la["col2"].get)
        th = res["theirs"][j]
        out = {}
        with self.lock:
            for key, i in (("best", best), ("mine", mine)):
                if key == "mine" and mine == best:
                    continue
                try:
                    out[key] = self.pool.main.call("endgame_line", state=state, ours=res["ours"][i]["choice"], theirs=th["choice"],
                                                   oursLabel=res["ours"][i]["label"], theirsLabel=th["label"])
                except Exception:
                    pass
        return out

    def evaluate(self, pid: str, choice: str, label: str, deep: bool = False, tournament: bool = False,
                 timeout: bool = False, look: bool = False) -> Dict[str, Any]:
        entry = self.positions.get(pid)
        if not entry or "pos" not in entry:
            raise ValueError("Ejercicio caducado; pide uno nuevo")
        state = entry["pos"]["state"]
        secs = time.time() - entry["t0"]
        K, seeds, n_worlds, out_seeds = (10, 40, 6, 12) if deep else (8, 16, 4, 6)
        if entry.get("realistic"):
            # lo que tú sabes: su hoja abierta (sin SP ni naturaleza) y los Pokémon que ya ha sacado
            with self.lock:
                worlds = self.pool.main.call("realistic_worlds", state=state, sheet=entry["theirs"], n=n_worlds,
                                             seed=random.randint(1, 10 ** 6))["worlds"]
        else:
            worlds = [state]
        res = self.pool.search(worlds, K=K, Kopp=K, seeds=seeds, extra_ours=[{"choice": choice, "label": label}],
                               mega_policy="free")
        ours, M, row, col = res["ours"], res["matrix"], res["row"], res["col"]
        bad = set(res.get("bad_rows", []))
        mine = next((i for i, o in enumerate(ours) if o["choice"] == choice), None)
        if mine is None or mine in bad:
            raise ValueError("Esa combinación no es legal en esta posición")
        q = [sum(M[i][j] * col[j] for j in range(len(col))) for i in range(len(M))]
        valid = [i for i in range(len(q)) if i not in bad]
        best = max(valid, key=lambda i: q[i])
        best1 = best
        la = None
        endg = self._is_endgame(state)
        if look or endg:
            try:
                la = self._lookahead(worlds, [{"choice": choice, "label": label}], K, res, q, mine, best, valid, deep, endgame=endg)
            except Exception as e:  # si falla, se queda el análisis de 1 turno
                print("2 turnos / final:", e)
        if la:
            # a 2 turnos manda la matriz refinada (sólo las mejores jugadas y respuestas probables)
            best = la["best"]
            loss = max(0.0, la["q2"][best] - la["q2"][mine])
            eq_mine = la["row2"][mine]
            exp_res = dict(res)
            exp_res["matrix"] = [list(r) for r in M]
            for a, i in enumerate(la["rows"]):
                for b, j in enumerate(la["cols"]):
                    exp_res["matrix"][i][j] = la["sub"][a][b]
            exp_res["row"] = [la["row2"].get(i, 0.0) for i in range(len(M))]
            exp_res["col"] = [la["col2"].get(j, 0.0) for j in range(len(col))]
            q_exp = [la["q2"].get(i, -9.0) for i in range(len(M))]
        else:
            loss = max(0.0, q[best] - q[mine])
            eq_mine = row[mine]
            exp_res, q_exp = res, q
        # experiencia: lo que suelen jugar los jugadores fuertes (desempata y aporta lo que el cálculo no ve)
        lp = self._policy_lp(worlds[0], ours)
        experiencia = self._exp_summary(lp, valid, ours) if lp else None
        cfg = self._policy_cfg()
        if lp and cfg.get("activo") and cfg.get("beta", 0) > 0:
            qm = dict(la["q2"]) if la else {i: q[i] for i in valid}
            if mine in qm:
                mx = max(lp[i] for i in qm)
                qa = {i: qm[i] + cfg["beta"] * (lp[i] - mx) for i in qm}
                best = max(qa, key=qa.get)
                loss = max(0.0, qa[best] - qa[mine])
        resc = cfg.get("rescate") if (lp and cfg.get("activo")) else None
        if resc and not (eq_mine >= 0.10 or loss < 0.03) and loss < resc["perdida"]:
            # jugada habitual entre jugadores fuertes que el cálculo sólo ve algo peor: no se cuenta como error
            import math
            pool_rows = list(la["q2"].keys()) if la else valid
            mx = max(lp[i] for i in pool_rows)
            z = sum(math.exp(lp[i] - mx) for i in pool_rows)
            if mine in pool_rows and math.exp(lp[mine] - mx) / z >= resc["p"]:
                loss = 0.0
        # escala validada con 160 partidas de 1500+: por debajo de ~0,20 de pérdida la posición no empeora después
        # (son matices dentro del margen del cálculo); a partir de 0,20 sí, y mucho a partir de 0,35
        pts = round(100 * max(0.0, 1 - max(0.0, loss - 0.05) / 0.45))
        if eq_mine >= 0.10 or loss < 0.03:
            verdict, pts, tone = "Correcta: forma parte de la mejor estrategia", max(pts, 90), "good"
        elif loss < LOSS_ERROR:
            verdict, tone = "Aceptable: el cálculo la ve algo peor, pero es una diferencia pequeña", "ok"
        elif loss < LOSS_GRAVE:
            verdict, tone = "Error", "bad"
        else:
            verdict, tone = "Error grave", "bad"
        prudent = tone == "bad" and not os.environ.get("ROOBOT_SIN_PRUDENTE") and self._plan_vs_attack(ours[mine]["choice"], ours[best]["choice"])
        if prudent:
            verdict, tone, pts = VEREDICTO_PRUDENTE, "ok", max(pts, 70)
        order = sorted(range(len(col)), key=lambda j: -col[j])
        replies = [j for j in order if col[j] >= 0.05][:4] or order[:1]
        rep = [{"choice": res["theirs"][j]["choice"], "w": col[j]} for j in replies]
        with self.lock:
            # el turno se reproduce con el equipo real del rival (en modo realista, alguna respuesta
            # pensada para un mundo posible puede no existir: se usa la siguiente más probable)
            sim, jbest = None, order[0]
            for j in order[:6]:
                sim = self.pool.main.call("simulate", state=state, ours=choice, theirs=res["theirs"][j]["choice"])
                if sim.get("ok"):
                    jbest = j
                    break
            out_mine = self.pool.main.call("outcome", state=state, ours=choice, theirs=rep, seeds=out_seeds)
            out_best = None if best == mine else \
                self.pool.main.call("outcome", state=state, ours=ours[best]["choice"], theirs=rep, seeds=out_seeds)
        try:
            why = explain_position(exp_res, q_exp, mine, best, out_mine, out_best)
        except Exception as e:  # una explicación fallida no debe tumbar el ejercicio
            why = [f"(No pude generar la explicación: {e})"]
        if la:
            if la.get("endgame"):
                why = explain_endgame(ours, q, la, mine, best1, best, self._endgame_lines(state, res, la, mine, best)) + why
            else:
                why = explain_lookahead(ours, q, la, mine, best1, best, tone == "good") + why
        if prudent:
            why.insert(0, self._prudent_line(loss))
        self._log(entry["pos"]["kind"], entry["rival"], label, ours[best]["label"], pts, loss, secs)
        try:
            cats = classify(ours[mine]["label"], ours[best]["label"], tone == "good" or prudent, out_mine, out_best)
        except Exception:
            cats = []
        if not entry.get("cats_done"):
            entry["cats_done"] = True
            self.debil.record("repaso" if entry.get("review_id") else "ejercicio", entry["pos"]["kind"], cats)
        if tournament:
            self._log_torneo(entry["pos"]["kind"], pts, secs, timeout)
        rating = None
        if not entry.get("review_id") and not entry.get("rated") and not prudent:
            entry["rated"] = True
            diff = entry.get("difficulty") or difficulty_from(q, [o.get("prior", 0) for o in ours], valid)
            rating = self.rating.update("tactico", diff, pts)
        review = self._after_eval(entry, "good" if prudent else tone, label, ours[best]["label"], loss,
                                  {"tipo": entry["pos"]["kind"], "pos": entry["pos"]})
        options = sorted(({"label": ours[i]["label"], "q": round(q[i], 3), "eq": round(row[i], 3), "mine": i == mine,
                           "best": i == best,
                           **({"q2": round(la["q2"][i], 3), "eq2": round(la["row2"][i], 3)} if la and i in la["q2"] else {})}
                          for i in valid), key=lambda o: (-o.get("q2", -9), -o["q"]))
        expected = sorted(({"label": res["theirs"][j]["label"], "p": round(col[j], 3)} for j in range(len(col)) if col[j] >= 0.03),
                          key=lambda o: -o["p"])
        core = self._core_matrix(exp_res, mine, best)
        return {"core": core, "verdict": verdict, "tone": tone, "points": pts, "loss": round(loss, 3), "seconds": round(secs),
                "options": options, "expected": expected, "reply": res["theirs"][jbest]["label"],
                "events": (sim or {}).get("events", []), "value": round(res["value"], 3), "why": why,
                "deep": deep, "realistic": bool(entry.get("realistic")), "review": review, "experiencia": experiencia,
                "look": bool(la), "endgame": bool(la and la.get("endgame")), "value2": round(la["value"], 3) if la else None,
                "rating": rating, "critical": entry.get("critical"),
                "tournament": tournament, "timeout": timeout, "cats": cats, "focus": entry.get("focus"), "prudente": prudent}

    # ------------------------------------------------------------------ veredicto prudente
    def _move_kind(self, mid: str) -> Optional[str]:
        cache = self.__dict__.setdefault("_mk_cache", {})
        if mid not in cache:
            try:
                with self.lock:
                    cache.update(self.pool.main.call("move_kinds", ids=[mid]) or {})
            except Exception:
                cache[mid] = None
        return cache.get(mid)

    def _slot_kinds(self, choice: str) -> List[str]:
        """Por ranura: 'plan' (cambiar, movimiento de estado o de control de velocidad), 'ataque' o 'nada'."""
        out = []
        for part in (choice or "").split(","):
            w = part.strip().split()
            if not w or w[0] in ("pass", "default"):
                out.append("nada")
            elif w[0] == "switch":
                out.append("plan")
            elif w[0] == "move" and len(w) > 1:
                mid = w[1]
                out.append("plan" if (mid in PLAN_DMG or self._move_kind(mid) == "Status") else "ataque")
            else:
                out.append("nada")
        return out

    def _plan_vs_attack(self, mine_choice: str, best_choice: str) -> bool:
        """¿Hay una ranura en la que tú hiciste una jugada de plan y la app prefería atacar?"""
        try:
            a, b = self._slot_kinds(mine_choice), self._slot_kinds(best_choice)
        except Exception:
            return False
        return any(x == "plan" and y == "ataque" for x, y in zip(a, b))

    @staticmethod
    def _prudent_line(loss: float) -> str:
        return (f"**Discutible, no error.** Tu jugada prepara el turno siguiente (proteger, cambiar, estado, velocidad…) y el "
                f"cálculo, que mira un solo turno, la ve peor (pérdida {loss:.2f}). En la revisión de jugadas de 1500+ este tipo "
                f"de desacuerdo casi nunca era un error de verdad, así que no cuenta como fallo ni baja tu rating. "
                f"Pregúntate si el plan rinde de verdad el turno que viene.")

    def _log(self, tipo, rival, eleccion, mejor, pts, loss, secs) -> None:
        with open(self.logs / "ejercicios.csv", "a", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            if f.tell() == 0:
                w.writerow(["fecha", "tipo", "rival", "eleccion", "mejor", "puntos", "perdida", "segundos"])
            w.writerow([time.strftime("%Y-%m-%d %H:%M"), tipo, rival, eleccion, mejor, pts, round(loss, 3), round(secs)])

    def _log_torneo(self, tipo, pts, secs, timeout) -> None:
        with open(self.logs / "torneo.csv", "a", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            if f.tell() == 0:
                w.writerow(["fecha", "tipo", "puntos", "segundos", "agotado"])
            w.writerow([time.strftime("%Y-%m-%d %H:%M"), tipo, pts, round(secs), int(bool(timeout))])

    def torneo_stats(self) -> Dict[str, Any]:
        f = self.logs / "torneo.csv"
        if not f.exists():
            return {"n": 0}
        rows = list(csv.DictReader(open(f, encoding="utf-8")))
        if not rows:
            return {"n": 0}
        last = rows[-30:]
        mean = lambda xs: round(sum(xs) / len(xs), 1) if xs else None
        return {"n": len(rows), "media": mean([float(r["puntos"]) for r in last]),
                "segundos": mean([float(r["segundos"]) for r in last if r["tipo"] != "preview"]),
                "agotados": round(100 * sum(int(r["agotado"]) for r in last) / len(last))}

    # ------------------------------------------------------------------ repaso de errores
    def _after_eval(self, entry, tone, mine_label, best_label, loss, payload) -> Optional[Dict[str, Any]]:
        """Guarda los errores para repasarlos y actualiza el repaso si este ejercicio era uno."""
        rid = entry.get("review_id")
        if rid:
            if entry.get("review_done"):
                return None
            entry["review_done"] = True
            return self.repaso.update(rid, tone == "good", mine_label, loss)
        if tone != "good":
            item = {"tipo": payload["tipo"], "my_team": entry["my_team"], "rival_file": entry["rival_file"],
                    "rival": entry["rival"], "realistic": bool(entry.get("realistic")), "ours": entry["ours"],
                    "theirs": entry["theirs"], "eleccion": mine_label, "mejor": best_label, "perdida": round(loss, 3)}
            item.update({k: v for k, v in payload.items() if k != "tipo"})
            self.repaso.add(item)
            return {"guardado": True}
        return None

    def _review_info(self, item) -> Optional[Dict[str, Any]]:
        if not item:
            return None
        return {"fecha": item["creado"][:10], "repasos": item.get("repasos", 0), "adelantado": item.get("_adelantado", False),
                "pendientes": self.repaso.stats()["pendientes"]}

    def review_next(self) -> Dict[str, Any]:
        item = self.repaso.next()
        if not item:
            raise ValueError("No tienes errores guardados todavía. Se guardan solos cuando fallas un ejercicio.")
        if item["tipo"] == "preview":
            return self._start_preview(item["ours"], item["theirs"], item["my_team"], item["rival_file"],
                                       item.get("deep", False), item.get("realistic", False),
                                       setup=item["setup"], vals=item["vals"], review=item, rolls=item.get("rolls"))
        return self._present(item["pos"], item["ours"], item["theirs"], item["my_team"], item["rival_file"],
                             item.get("realistic", False), review=item)

    # ------------------------------------------------------------------ fichas de plan por cruce
    def _plan_dir(self) -> Path:
        d = self.logs / "fichas"
        d.mkdir(exist_ok=True)
        return d

    @staticmethod
    def _plan_key(team: str, rival: str) -> str:
        safe = lambda x: re.sub(r"[^A-Za-z0-9_-]+", "_", Path(x).stem)[:70]
        return f"{safe(team)}__{safe(rival)}"

    def plan_start(self, team: str, rival: str, deep: bool = False, refresh: bool = False) -> Dict[str, Any]:
        if not rival:
            raise ValueError("Elige un rival concreto arriba (no «Aleatorio») para hacer su ficha")
        key = self._plan_key(team, rival)
        f = self._plan_dir() / f"{key}.json"
        if f.exists() and not refresh:
            return {"ready": True, "ficha": json.loads(f.read_text(encoding="utf-8"))}
        ours, theirs = self._read_team(team), self._read_team(rival)
        out = self._start_preview(ours, theirs, team, rival, deep, True)
        self.positions[out["id"]]["plan"] = {"key": key, "team": team, "rival": rival}
        return {"ready": False, "id": out["id"], "total": out["total"]}

    def plan_progress(self, pid: str) -> Dict[str, Any]:
        e = self.positions.get(pid)
        if not e or "plan" not in e:
            return {"error": "Ficha caducada; vuelve a generarla"}
        if not e["event"].is_set():
            return {"ready": False, "done": e["done"], "total": e["total"]}
        if e.get("error"):
            return {"error": e["error"]}
        try:
            if "ficha" not in e:
                e["ficha"] = self._build_ficha(e)
        except Exception as ex:
            return {"error": f"No pude montar la ficha: {str(ex).splitlines()[0][:200]}"}
        return {"ready": True, "ficha": e["ficha"]}

    def _build_ficha(self, e) -> Dict[str, Any]:
        setup, vals, info = e["setup"], e["vals"], e["plan"]
        R, T, O, Th = setup["rivalLeads"], setup["table"], setup["ours"], setup["theirs"]
        order_of = {_k(p["pair"]): p["order"] for p in setup["myPairs"]}
        nm = lambda k: " + ".join(O[int(i)]["name"] for i in k.split("-"))
        back = lambda k: " y ".join(O[i]["name"] for i in order_of[k][2:])
        scores, severe = {}, {}
        for p in setup["myPairs"]:
            k = _k(p["pair"])
            scores[k] = sum(r["p"] * vals[k][_k(r["pair"])] for r in R)
            severe[k] = sum(r["p"] * sum(1 for f in r["pair"] for m in p["pair"] if T["thr"][m][f] >= 0.5) for r in R)
        best = max(scores, key=scores.get)
        top = R[0]
        with self.lock:
            det = self.pool.main.call("lead_detail", ours=e["ours"], theirs=e["theirs"], oursOrder=order_of[best],
                                      theirsOrder=top["order"], K=8 if e.get("deep") else 6, Kopp=8 if e.get("deep") else 6,
                                      seeds=10 if e.get("deep") else 4)
            speeds = self.pool.main.call("speed_tiers", ours=e["ours"], theirs=e["theirs"])["rows"]
            cards_o = self.pool.main.call("sheet", team=e["orig_ours"], mine=True)
            cards_t = self.pool.main.call("sheet", team=e["orig_theirs"])
        try:
            why_best = explain_best(setup, scores, severe, best, best, det, None)
        except Exception as ex:
            why_best = [f"(No pude generar el plan: {ex})"]
        ranking = sorted(({"label": nm(k), "pair": [int(i) for i in k.split("-")], "back": back(k), "q": round(v, 3),
                           "severe": round(severe[k], 1)} for k, v in scores.items()), key=lambda o: -o["q"])[:5]
        vs_leads = []
        for r in R:
            rk = _k(r["pair"])
            bk = max(vals, key=lambda k: vals[k][rk])
            vs_leads.append({"label": " + ".join(Th[i]["name"] for i in r["pair"]), "pair": r["pair"], "p": r["p"], "tags": r["tags"],
                             "best": nm(bk), "bestPair": [int(i) for i in bk.split("-")], "bestBack": back(bk),
                             "bestValue": round(vals[bk][rk], 3), "generalValue": round(vals[best][rk], 3)})
        threats = []
        for f in range(len(Th)):
            m = max(range(len(O)), key=lambda i: T["thr"][i][f])
            hits = sum(1 for i in range(len(O)) if T["thr"][i][f] >= 0.5)
            threats.append({"foe": Th[f]["name"], "move": (T.get("tmoves") or [[None]])[m][f], "target": O[m]["name"],
                            "pct": round(100 * min(T["thr"][m][f], 1.2)), "hits": hits})
        threats.sort(key=lambda x: (-x["hits"], -x["pct"]))
        answers = []
        for f in range(len(Th)):
            m = max(range(len(O)), key=lambda i: T["off"][i][f])
            answers.append({"foe": Th[f]["name"], "mon": O[m]["name"], "move": T["moves"][m][f] if T.get("moves") else None,
                            "pct": round(100 * min(T["off"][m][f], 1.2))})
        answers.sort(key=lambda x: x["pct"])
        f = self._plan_dir() / f"{info['key']}.json"
        notes = ""
        if f.exists():
            try:
                notes = json.loads(f.read_text(encoding="utf-8")).get("notes", "")
            except Exception:
                pass
        selection = None
        if e.get("rolls") and setup.get("bring"):
            try:
                sc = self._selection_scores(setup, vals, e["rolls"])
                bo = sc["options"][0]
                bk_ = bo["lk"]
                weak = selection_weaknesses(sc["D"], bo["S"], sc["presence"])
                selection = {"lead": " + ".join(O[i]["name"] for i in bo["L"]), "back": " y ".join(O[i]["name"] for i in bo["S"] if i not in bo["L"]),
                             "pair": bo["L"], "win": round(sc["winp"][bo["sk"]], 3), "leadValue": round(sc["leadv"][bk_], 3),
                             "weak": [{"foe": Th[w["j"]]["name"], "p": round(w["p"], 2), "beats": [O[i]["name"] for i in w["beats"]]} for w in weak[:3]],
                             "top": [{"lead": " + ".join(O[i]["name"] for i in o["L"]), "back": " y ".join(O[i]["name"] for i in o["S"] if i not in o["L"]),
                                      "win": round(sc["winp"][o["sk"]], 3), "q": round(o["total"], 3)}
                                     for o in [x for n, x in enumerate(sc["options"])
                                               if x["sk"] not in {y["sk"] for y in sc["options"][:n]}][:5]]}
            except Exception:
                selection = None
        ficha = {"key": info["key"], "team": info["team"], "rival": info["rival"], "rivalName": Path(info["rival"]).stem,
                 "teamName": Path(info["team"]).stem, "fecha": time.strftime("%Y-%m-%d %H:%M"), "deep": bool(e.get("deep")),
                 "ours": cards_o, "theirs": cards_t, "best": {"label": nm(best), "pair": [int(i) for i in best.split("-")],
                 "back": back(best), "q": round(scores[best], 3)}, "plan": why_best, "ranking": ranking,
                 "vsLeads": vs_leads, "threats": threats, "answers": answers, "speeds": speeds, "notes": notes,
                 "selection": selection}
        f.write_text(json.dumps(ficha, ensure_ascii=False), encoding="utf-8")
        return ficha

    def plan_notes(self, key: str, notes: str) -> Dict[str, Any]:
        f = self._plan_dir() / f"{re.sub(r'[^A-Za-z0-9_-]+', '_', key)}.json"
        if not f.exists():
            raise ValueError("No encuentro esa ficha")
        data = json.loads(f.read_text(encoding="utf-8"))
        data["notes"] = notes[:20000]
        f.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return {"ok": True}

    def plan_list(self) -> Dict[str, Any]:
        out = []
        for f in sorted(self._plan_dir().glob("*.json"), key=lambda x: -x.stat().st_mtime):
            try:
                d = json.loads(f.read_text(encoding="utf-8"))
                out.append({"key": d["key"], "team": d["team"], "rival": d["rival"], "rivalName": d["rivalName"],
                            "teamName": d["teamName"], "fecha": d["fecha"], "notas": bool(d.get("notes"))})
            except Exception:
                continue
        return {"fichas": out}

    def plan_get(self, key: str) -> Dict[str, Any]:
        f = self._plan_dir() / f"{re.sub(r'[^A-Za-z0-9_-]+', '_', key)}.json"
        if not f.exists():
            raise ValueError("No encuentro esa ficha")
        return {"ready": True, "ficha": json.loads(f.read_text(encoding="utf-8"))}

    # ------------------------------------------------------------------ análisis de partidas reales
    @staticmethod
    def _replay_text(source: str) -> str:
        """Acepta un enlace de replay.pokemonshowdown.com, el log pegado o el HTML de una repetición."""
        src = (source or "").strip()
        if src.startswith("http"):
            u = urlparse(src)
            if "pokemonshowdown.com" not in u.netloc:
                raise ValueError("Sólo puedo descargar repeticiones de replay.pokemonshowdown.com; si no, pega el log o el archivo")
            path = re.sub(r"\.(json|log)$", "", u.path.rstrip("/"))
            url = f"https://replay.pokemonshowdown.com{path}.json"
            if "?" in src and "p" in parse_qs(u.query):       # repeticiones privadas: ...?p=contraseña
                url = f"https://replay.pokemonshowdown.com{path}-{parse_qs(u.query)['p'][0]}pw.json"
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (RooBot)"})
                with urllib.request.urlopen(req, timeout=20) as r:
                    data = json.loads(r.read().decode("utf-8"))
            except Exception as e:
                raise ValueError(f"No pude descargar la repetición ({e}). Comprueba el enlace o pega el log.")
            return data.get("log") or ""
        if "|" not in src:
            raise ValueError("Pega el enlace de la repetición, su log (líneas que empiezan por |) o el archivo .html descargado")
        return src

    def _view(self, state: Dict[str, Any]) -> Dict[str, Any]:
        with self.lock:
            view = self.pool.main.call("describe", state=state)
        unseen = {p["set"]["species"] for p in state["sides"][1]["pokemon"] if not p.get("revealed", True)}
        view["sides"][1]["pokemon"] = [m for m in view["sides"][1]["pokemon"]
                                       if not any(m["name"] == u or m["baseSpecies"] == u or u.startswith(m["baseSpecies"] + "-") for u in unseen)]
        return view

    def replay_start(self, source: str, my_team: str, side: str = "", deep: bool = False, look: bool = False) -> Dict[str, Any]:
        log = self._replay_text(source)
        ours = self._read_team(my_team) if my_team else []
        with self.lock:
            parsed = self.pool.main.call("replay_parse", log=log, ourTeam=ours, side=side or None)
        if not parsed["decisions"]:
            raise ValueError("No encuentro turnos en esa repetición")
        return self._start_analysis(parsed, my_team, deep, look)

    def _start_analysis(self, parsed, my_team: str, deep: bool, look: bool) -> Dict[str, Any]:
        """Analiza en segundo plano cada decisión de una partida (repetición o partida contra el bot)."""
        if parsed.get("icon") != "⚔":   # las partidas contra el bot no cancelan ni se cancelan
            for e in self.positions.values():
                if e.get("kind") == "replay" and e["parsed"].get("icon") != "⚔":
                    e["cancel"] = True
        with self.lock:
            sheet_ours = self.pool.main.call("sheet", team=parsed["sheets"]["ours"], mine=True)
            sheet_theirs = self.pool.main.call("sheet", team=parsed["sheets"]["theirs"])
        entry = {"kind": "replay", "parsed": parsed, "sheet_cards": {"ours": sheet_ours, "theirs": sheet_theirs}, "turns": [None] * len(parsed["decisions"]), "done": 0,
                 "total": len(parsed["decisions"]), "cancel": False, "error": None, "deep": deep, "look": look, "my_team": my_team,
                 "event": threading.Event(), "t0": time.time()}
        rid = self._register(entry)
        threading.Thread(target=self._analyze_replay, args=(entry,), daemon=True).start()
        won = parsed.get("winner")
        return {"id": rid, "players": parsed["players"], "side": parsed["side"], "ourName": parsed["ourName"],
                "oppName": parsed["oppName"], "winner": won, "won": won == parsed["ourName"] if won else None,
                "usedOurFile": parsed["usedOurFile"], "openSheets": parsed["openSheets"],
                "total": entry["total"], "format": parsed["format"], "sheets": entry["sheet_cards"],
                "turns": [{"turn": d["turn"], "label": d["label"], "known": d["known"], "reason": d["reason"]} for d in parsed["decisions"]]}

    def _analyze_turn(self, parsed, d, deep: bool, look: bool = False, fija: bool = False) -> Dict[str, Any]:
        state = d["state"]
        sheet = parsed["sheets"]["theirs"]
        K, seeds, nw = (10, 32, 6) if deep else (8, 16, 4)
        with self.lock:
            wseed = (zlib.crc32(json.dumps(state, sort_keys=True).encode()) % 10 ** 6 + 1) if fija or os.environ.get("ROOBOT_SEMILLA_FIJA") \
                else random.randint(1, 10 ** 6)
            worlds = self.pool.main.call("realistic_worlds", state=state, sheet=sheet, n=nw, seed=wseed)["worlds"]
        spec = d.get("spec")
        if d.get("choice"):
            spec, base = None, {"choice": d["choice"], "label": d["label"]}
        else:
            acted = [x for x in spec if not x.get("pass")]
            if not acted:
                return {"turn": d["turn"], "skipped": True, "reason": d["reason"] or "No se ve ninguna de tus acciones"}
            with self.lock:
                base = self.pool.main.call("spec_choice", state=worlds[0], spec=spec)
        partial = not d["known"]
        extras, labels = [], {}
        if not base:
            return {"turn": d["turn"], "skipped": True, "reason": "No pude reproducir tu jugada en esa posición"}
        if partial:
            # no se ve qué eligió uno de tus Pokémon: se completa con sus opciones y se queda la mejor
            slot = next(i for i, x in enumerate(spec) if x.get("pass"))
            view = self._view(worlds[0])
            known_parts = base["choice"].split(", ")
            known_labels = base["label"].split(" | ")
            opts = [o for o in view["options"][slot] if o["kind"] in ("move", "switch")][:7]
            for o in opts:
                parts = list(known_parts)
                labs = list(known_labels)
                parts[slot] = o["choice"]
                labs[slot] = o["label"]
                extras.append({"choice": ", ".join(parts), "label": " | ".join(labs)})
        else:
            extras = [{"choice": base["choice"], "label": base["label"]}]
        res = self.pool.search(worlds, K=K, Kopp=K, seeds=seeds, extra_ours=extras, mega_policy="free")
        ours, M, row, col = res["ours"], res["matrix"], res["row"], res["col"]
        bad = set(res.get("bad_rows", []))
        q = [sum(M[i][j] * col[j] for j in range(len(col))) for i in range(len(M))]
        mine_rows = [i for i, o in enumerate(ours) if o["choice"] in {x["choice"] for x in extras} and i not in bad]
        if not mine_rows:
            return {"turn": d["turn"], "skipped": True, "reason": "Tu jugada no es legal en la posición reconstruida"}
        mine = max(mine_rows, key=lambda i: q[i])
        valid = [i for i in range(len(q)) if i not in bad]
        best = max(valid, key=lambda i: q[i])
        best1 = best
        la = None
        endg = self._is_endgame(state)
        if look or endg:
            try:
                la = self._lookahead(worlds, extras, K, res, q, mine, best, valid, deep, endgame=endg)
            except Exception as e:  # si falla, se queda el análisis de 1 turno
                print("2 turnos / final:", e)
        if la:
            # a 2 turnos manda la matriz refinada (sólo las mejores jugadas y respuestas probables)
            best = la["best"]
            loss = max(0.0, la["q2"][best] - la["q2"][mine])
            eq_mine = la["row2"][mine]
            exp_res = dict(res)
            exp_res["matrix"] = [list(r) for r in M]
            for a, i in enumerate(la["rows"]):
                for b, j in enumerate(la["cols"]):
                    exp_res["matrix"][i][j] = la["sub"][a][b]
            exp_res["row"] = [la["row2"].get(i, 0.0) for i in range(len(M))]
            exp_res["col"] = [la["col2"].get(j, 0.0) for j in range(len(col))]
            q_exp = [la["q2"].get(i, -9.0) for i in range(len(M))]
        else:
            loss = max(0.0, q[best] - q[mine])
            eq_mine = row[mine]
            exp_res, q_exp = res, q
        # experiencia: lo que suelen jugar los jugadores fuertes (desempata y aporta lo que el cálculo no ve)
        lp = self._policy_lp(worlds[0], ours)
        experiencia = self._exp_summary(lp, valid, ours) if lp else None
        cfg = self._policy_cfg()
        if lp and cfg.get("activo") and cfg.get("beta", 0) > 0:
            qm = dict(la["q2"]) if la else {i: q[i] for i in valid}
            if mine in qm:
                mx = max(lp[i] for i in qm)
                qa = {i: qm[i] + cfg["beta"] * (lp[i] - mx) for i in qm}
                best = max(qa, key=qa.get)
                loss = max(0.0, qa[best] - qa[mine])
        resc = cfg.get("rescate") if (lp and cfg.get("activo")) else None
        if resc and not (eq_mine >= 0.10 or loss < 0.03) and loss < resc["perdida"]:
            # jugada habitual entre jugadores fuertes que el cálculo sólo ve algo peor: no se cuenta como error
            import math
            pool_rows = list(la["q2"].keys()) if la else valid
            mx = max(lp[i] for i in pool_rows)
            z = sum(math.exp(lp[i] - mx) for i in pool_rows)
            if mine in pool_rows and math.exp(lp[mine] - mx) / z >= resc["p"]:
                loss = 0.0
        # escala validada con 160 partidas de 1500+: por debajo de ~0,20 de pérdida la posición no empeora después
        # (son matices dentro del margen del cálculo); a partir de 0,20 sí, y mucho a partir de 0,35
        pts = round(100 * max(0.0, 1 - max(0.0, loss - 0.05) / 0.45))
        if eq_mine >= 0.10 or loss < 0.03:
            verdict, pts, tone = "Correcta: forma parte de la mejor estrategia", max(pts, 90), "good"
        elif loss < LOSS_ERROR:
            verdict, tone = "Imprecisión: algo peor que la mejor, pero es una diferencia pequeña", "ok"
        elif loss < LOSS_GRAVE:
            verdict, tone = "Error", "bad"
        else:
            verdict, tone = "Error grave", "bad"
        prudent = tone == "bad" and not os.environ.get("ROOBOT_SIN_PRUDENTE") and self._plan_vs_attack(ours[mine]["choice"], ours[best]["choice"])
        if prudent:
            verdict, tone, pts = VEREDICTO_PRUDENTE, "ok", max(pts, 70)
        order = sorted(range(len(col)), key=lambda j: -col[j])
        reps = [{"choice": res["theirs"][j]["choice"], "w": col[j]} for j in order if col[j] >= 0.05][:4] or \
               [{"choice": res["theirs"][order[0]]["choice"], "w": 1}]
        with self.lock:
            out_mine = self.pool.main.call("outcome", state=worlds[0], ours=ours[mine]["choice"], theirs=reps, seeds=6)
            out_best = None if best == mine else self.pool.main.call("outcome", state=worlds[0], ours=ours[best]["choice"], theirs=reps, seeds=6)
        try:
            why = explain_position(exp_res, q_exp, mine, best, out_mine, out_best)
        except Exception as ex:
            why = [f"(No pude generar la explicación: {ex})"]
        if la:
            if la.get("endgame"):
                why = explain_endgame(ours, q, la, mine, best1, best, self._endgame_lines(state, res, la, mine, best)) + why
            else:
                why = explain_lookahead(ours, q, la, mine, best1, best, tone == "good") + why
        if prudent:
            why.insert(0, self._prudent_line(loss))
        if partial:
            why.insert(0, f"Ojo: {d['reason']}. Para no penalizarte, tu jugada se evalúa con la mejor opción posible para ese Pokémon.")
        try:
            cats = classify(ours[mine]["label"], ours[best]["label"], tone == "good" or prudent, out_mine, out_best)
        except Exception:
            cats = []
        options = sorted(({"label": ours[i]["label"], "q": round(q[i], 3), "eq": round(row[i], 3), "mine": i == mine,
                           "best": i == best,
                           **({"q2": round(la["q2"][i], 3), "eq2": round(la["row2"][i], 3)} if la and i in la["q2"] else {})}
                          for i in valid), key=lambda o: (-o.get("q2", -9), -o["q"]))
        expected = sorted(({"label": res["theirs"][j]["label"], "p": round(col[j], 3)} for j in range(len(col)) if col[j] >= 0.03),
                          key=lambda o: -o["p"])
        raw = None
        if os.environ.get("ROOBOT_DUMP"):
            # todo lo necesario para recalcular el veredicto con otras reglas (calibración)
            raw = {"ours": [o["label"] for o in ours], "theirs": [o["label"] for o in res["theirs"]],
                   "M": [[round(v, 4) for v in r] for r in M], "bad": sorted(bad), "mine": mine, "best1": best1,
                   "lp": [round(v, 4) for v in lp] if lp else None, "won": d.get("_won"),
                   "la": ({"rows": la["rows"], "cols": la["cols"], "endgame": la.get("endgame"),
                           "sub2": [[round(v, 4) for v in r] for r in la.get("sub2_raw", la["sub"])]} if la else None)}
        return {"_raw": raw, "experiencia": experiencia, "turn": d["turn"], "skipped": False, "partial": partial, "verdict": verdict, "tone": tone, "points": pts,
                "loss": round(loss, 3), "mine": ours[mine]["label"], "best": ours[best]["label"], "why": why,
                "options": options, "expected": expected, "value": round(res["value"], 3), "deep": deep, "realistic": True,
                "look": bool(la), "endgame": bool(la and la.get("endgame")), "world": worlds[0], "cats": cats, "prudente": prudent}

    def _analyze_replay(self, entry) -> None:
        parsed = entry["parsed"]
        try:
            for i, d in enumerate(parsed["decisions"]):
                if entry["cancel"]:
                    return
                try:
                    entry["turns"][i] = self._analyze_turn(parsed, d, entry["deep"], entry.get("look", False))
                except Exception as ex:
                    entry["turns"][i] = {"turn": d["turn"], "skipped": True, "reason": f"Error al analizar: {str(ex).splitlines()[0][:200]}"}
                entry["done"] += 1
            self._replay_summary(entry, save=True)
        except Exception as ex:
            entry["error"] = str(ex).splitlines()[0][:300]
        finally:
            entry["event"].set()

    def _replay_summary(self, entry, save: bool = False) -> Dict[str, Any]:
        done = [t for t in entry["turns"] if t and not t.get("skipped")]
        if not done:
            return {}
        prec = round(sum(t["points"] for t in done) / len(done), 1)
        summ = {"precision": prec, "turnos": len(done), "errores": sum(t["tone"] == "bad" for t in done),
                "imprecisiones": sum(t["tone"] == "ok" for t in done), "buenas": sum(t["tone"] == "good" for t in done)}
        worst = max(done, key=lambda t: t["loss"])
        if worst["loss"] >= LOSS_ERROR:
            summ["peor"] = {"turn": worst["turn"], "loss": worst["loss"], "mine": worst["mine"], "best": worst["best"]}
        if save and not entry.get("saved"):
            entry["saved"] = True
            parsed = entry["parsed"]
            with open(self.logs / "partidas.csv", "a", newline="", encoding="utf-8") as f:
                w = csv.writer(f)
                if f.tell() == 0:
                    w.writerow(["fecha", "rival", "resultado", "precision", "turnos", "errores", "imprecisiones"])
                won = parsed.get("winner")
                w.writerow([time.strftime("%Y-%m-%d %H:%M"), parsed["oppName"],
                            "" if not won else ("victoria" if won == parsed["ourName"] else "derrota"),
                            prec, summ["turnos"], summ["errores"], summ["imprecisiones"]])
            for t in entry["turns"]:
                if t and not t.get("skipped"):
                    self.debil.record("partida", "partida", t.get("cats") or [])
            # los errores pasan a «Repasar mis errores»
            for d, t in zip(parsed["decisions"], entry["turns"]):
                if t and not t.get("skipped") and t["tone"] == "bad":
                    self.repaso.add({"tipo": "partida", "my_team": entry["my_team"], "rival_file": f"{parsed.get('icon', '📼')} {parsed['oppName']}",
                                     "rival": f"{parsed.get('icon', '📼')} {parsed['oppName']}", "realistic": True,
                                     "ours": parsed["sheets"]["ours"], "theirs": parsed["sheets"]["theirs"],
                                     "eleccion": t["mine"], "mejor": t["best"], "perdida": t["loss"],
                                     "pos": {"state": d["state"], "kind": "lead" if d["turn"] == 1 else "medio", "turn": d["turn"],
                                             "preview": parsed["preview"]}})
            summ["guardados"] = summ["errores"]
        return summ

    def replay_progress(self, rid: str) -> Dict[str, Any]:
        e = self.positions.get(rid)
        if not e or e.get("kind") != "replay":
            return {"done": 0, "total": 0}
        brief = [None if t is None else {k: t[k] for k in ("turn", "skipped", "reason", "tone", "points", "loss", "mine", "best", "partial") if k in t}
                 for t in e["turns"]]
        return {"done": e["done"], "total": e["total"], "ready": e["event"].is_set(), "turns": brief,
                "summary": self._replay_summary(e), "error": e["error"]}

    def replay_turn(self, rid: str, idx: int) -> Dict[str, Any]:
        e = self.positions.get(rid)
        if not e or e.get("kind") != "replay":
            raise ValueError("Análisis caducado; vuelve a cargar la partida")
        t = e["turns"][idx]
        if not t or t.get("skipped"):
            raise ValueError("Ese turno no se ha podido analizar")
        parsed = e["parsed"]
        view = self._view(t["world"])
        return {**{k: v for k, v in t.items() if k != "world"}, "view": view, "kind": "lead" if t["turn"] == 1 else "medio",
                "rival": f"{parsed.get('icon', '📼')} {parsed['oppName']}", "preview": parsed["preview"], "hiddenBench": True,
                "theirsSheet": e["sheet_cards"]["theirs"], "sheets": e["sheet_cards"]}

    def replay_practice(self, rid: str, idx: int) -> Dict[str, Any]:
        e = self.positions.get(rid)
        if not e or e.get("kind") != "replay":
            raise ValueError("Análisis caducado; vuelve a cargar la partida")
        parsed = e["parsed"]
        d = parsed["decisions"][idx]
        pos = {"state": d["state"], "kind": "lead" if d["turn"] == 1 else "medio", "turn": d["turn"], "preview": parsed["preview"]}
        return self._present(pos, parsed["sheets"]["ours"], parsed["sheets"]["theirs"], e["my_team"],
                             f"{parsed.get('icon', '📼')} {parsed['oppName']}", True)

    # ------------------------------------------------------------------ revisar a la app (tú juzgas sus desacuerdos)
    REV_FIELDS = ["fecha", "replay", "lado", "turno", "rating", "jugador", "app", "perdida", "categorias", "veredicto", "nota"]

    # ------------------------------------------------------------------ experiencia (engine/experiencia.json)
    _POLICY = {"mtime": None, "cfg": {}}

    def _policy_cfg(self) -> Dict[str, Any]:
        f = ROOT / "engine" / "experiencia.json"
        try:
            mt = f.stat().st_mtime
        except OSError:
            self._POLICY.update(mtime=None, cfg={})
            return {}
        if self._POLICY["mtime"] != mt:
            try:
                cfg = json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                cfg = {}
            if self._POLICY["mtime"] is not None:          # cambió: los motores la vuelven a leer
                for e in self.pool.engines:
                    try:
                        e.call("policy_reload")
                    except Exception:
                        pass
            self._POLICY.update(mtime=mt, cfg=cfg)
        return self._POLICY["cfg"]

    def _policy_lp(self, state, ours) -> Optional[List[float]]:
        if not self._policy_cfg():
            return None
        try:
            with self.lock:
                r = self.pool.main.call("policy_logp", state=state, choices=[o["choice"] for o in ours])
            return r.get("lp")
        except Exception:
            return None

    @staticmethod
    def _exp_summary(lp, valid, ours) -> List[Dict[str, Any]]:
        import math
        mx = max(lp[i] for i in valid)
        w = {i: math.exp(lp[i] - mx) for i in valid}
        z = sum(w.values()) or 1.0
        top = sorted(valid, key=lambda i: -w[i])[:3]
        return [{"label": ours[i]["label"], "p": round(100 * w[i] / z)} for i in top]

    def _latest_detail(self, banco: str = "hoja"):
        """El detalle de la última calibración con ese banco. Para el banco con hojas abiertas, si no hay ninguna
        vale la última de otro tipo (ladder o recientes), pero nunca la del banco limpio, que va aparte."""
        d = self.logs / "calibracion"
        otras = []
        for f in reversed(sorted(d.glob("calibracion_*_detalle.json"))):
            try:
                summ = json.loads(f.with_name(f.name.replace("_detalle", "")).read_text(encoding="utf-8"))
            except Exception:
                otras.append(f)
                continue
            if summ.get("banco") == banco:
                return f, summ
            if summ.get("banco") != "limpio":
                otras.append(f)
        return (otras[0], None) if banco == "hoja" and otras else (None, None)

    def _revision_pendientes(self):
        """Los desacuerdos que te quedan por juzgar: primero los del banco limpio (en orden al azar fijo) y, si no
        hay o se han acabado, los del banco con hojas (por gravedad). Devuelve (casos, resumen, banco); banco es
        None si aún no hay ninguna calibración."""
        done = {f"{r['replay']}|{r['lado']}|{r['turno']}" for r in self._revision_rows()}
        ultimo = ([], None, None)
        for banco in ("limpio", "hoja"):
            f, summ = self._latest_detail(banco)
            if not f:
                continue
            dec = json.loads(f.read_text(encoding="utf-8"))
            pend = revisar.pendientes(dec, done, LOSS_ERROR, azar=(banco == "limpio"))
            if pend:
                return pend, summ, banco
            ultimo = ([], summ, banco)
        return ultimo

    def _calibracion_limpio(self) -> Optional[Dict[str, Any]]:
        """La última medida con el banco limpio (para «Mi progreso»), o None si aún no se ha hecho."""
        try:
            c = json.loads(REG.archivo(self.logs / "calibracion" / "ultima_limpio.json").read_text(encoding="utf-8"))
            return {k: c.get(k) for k in ("fecha", "replays", "decisiones", "acuerdo", "aceptables", "juez")}
        except Exception:
            return None

    def _revision_rows(self) -> List[Dict[str, str]]:
        f = self.logs / "revision.csv"
        if not f.exists():
            return []
        with open(f, newline="", encoding="utf-8") as fh:
            return list(csv.DictReader(fh))

    def revision_next(self) -> Dict[str, Any]:
        pend, summ, banco = self._revision_pendientes()
        if banco is None:
            raise ValueError("Primero mide la app (mantenimiento.bat, opción 9): aquí salen las jugadas en las que no está de acuerdo con jugadores fuertes")
        if not pend:
            if revisar.load_limpio()["ids"]:
                raise ValueError("Ya has revisado todos los desacuerdos. Saldrán más cuando cambie el juez y vuelvas a medir (mantenimiento.bat, opción 9).")
            raise ValueError("Ya has revisado todos los desacuerdos. Para tener más: mantenimiento.bat, opción 9 (banco limpio).")
        from .calibrar import fetch_log
        last_err = None
        for x in pend[:6]:          # si una no se puede reconstruir (o ya no es desacuerdo), la siguiente
            try:
                log = fetch_log(x["replay"])
                with self.lock:
                    parsed = self.pool.main.call("replay_parse", log=log, ourTeam=[], side=x["side"])
                d = next(dd for dd in parsed["decisions"] if dd["turn"] == x["turn"])
                t = self._analyze_turn(parsed, d, False, fija=True)      # los mismos mundos posibles que al medir
                if t.get("skipped"):
                    raise ValueError(t.get("reason") or "turno no reproducible")
                if t["tone"] != "bad":
                    raise ValueError("al volver a analizarla, la app ya no la marca como error")
                with self.lock:
                    sheet_ours = self.pool.main.call("sheet", team=parsed["sheets"]["ours"], mine=True)
                    sheet_theirs = self.pool.main.call("sheet", team=parsed["sheets"]["theirs"])
                # lo que se guarda es el análisis que ves ahora (puede variar algo respecto a la calibración)
                x = dict(x, best=t["best"], loss=t["loss"], cats=t.get("cats") or x.get("cats") or [])
                rid = self._register({"kind": "revision", "x": x})
                view = self._view(t["world"])
                return {**{k: v for k, v in t.items() if k not in ("world", "_raw")}, "view": view,
                        "kind": "lead" if t["turn"] == 1 else "medio", "rival": f"📼 {parsed['oppName']}",
                        "preview": parsed["preview"], "hiddenBench": True, "theirsSheet": sheet_theirs,
                        "sheets": {"ours": sheet_ours, "theirs": sheet_theirs},
                        "rev": {"id": rid, "replay": x["replay"], "player": x.get("player"), "rating": x.get("rating"),
                                "turn": x["turn"], "jugador": x["mine"], "app": x["best"], "loss": x["loss"],
                                "cats": x.get("cats") or [], "pending": len(pend), "banco": banco,
                                "url": f"https://replay.pokemonshowdown.com/{x['replay']}",
                                "fecha_cal": (summ or {}).get("fecha")}}
            except Exception as e:
                last_err = e
                with open(self.logs / "revision.csv", "a", newline="", encoding="utf-8") as fh:
                    w = csv.DictWriter(fh, fieldnames=self.REV_FIELDS)
                    if fh.tell() == 0:
                        w.writeheader()
                    w.writerow({"fecha": time.strftime("%Y-%m-%d %H:%M"), "replay": x["replay"], "lado": x["side"],
                                "turno": x["turn"], "veredicto": "omitida", "nota": str(e)[:120]})
        raise ValueError(f"No pude reconstruir esas posiciones ({last_err}). Prueba otra vez.")

    def revision_answer(self, rid: str, veredicto: str, nota: str = "") -> Dict[str, Any]:
        e = self.positions.get(rid)
        if not e or e.get("kind") != "revision":
            raise ValueError("Revisión caducada; pide la siguiente")
        if veredicto not in ("jugador", "app", "ambas", "nose"):
            raise ValueError("Veredicto no válido")
        x = e["x"]
        with open(self.logs / "revision.csv", "a", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=self.REV_FIELDS)
            if fh.tell() == 0:
                w.writeheader()
            w.writerow({"fecha": time.strftime("%Y-%m-%d %H:%M"), "replay": x["replay"], "lado": x["side"], "turno": x["turn"],
                        "rating": x.get("rating") or "", "jugador": x["mine"], "app": x["best"], "perdida": x["loss"],
                        "categorias": ";".join(c["cat"] for c in (x.get("cats") or [])), "veredicto": veredicto,
                        "nota": (nota or "")[:500]})
        return {"ok": True, "stats": self.revision_stats()}

    def _last_experience(self) -> Optional[Dict[str, Any]]:
        f = self.logs / "calibracion" / "experiencia" / "ultimo.json"
        try:
            j = json.loads(f.read_text(encoding="utf-8"))
            ev = j.get("evaluacion") or {}
            return {"fecha": j.get("fecha"), "activo": j.get("activo"), "beta": j.get("beta"), "rescate": j.get("rescate"),
                    "metricas": j.get("metricas"), "base": (ev.get("tabla") or [None])[0], "elegido": ev.get("elegido")}
        except Exception:
            return None

    def _last_experiment(self) -> Optional[Dict[str, Any]]:
        f = self.logs / "calibracion" / "experimentos" / "ultimo.json"
        try:
            return json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            return None

    def revision_stats(self) -> Dict[str, Any]:
        rows = [r for r in self._revision_rows() if r.get("veredicto") in ("jugador", "app", "ambas", "nose")]
        judged = [r for r in rows if r["veredicto"] != "nose"]
        cnt = {k: sum(r["veredicto"] == k for r in rows) for k in ("jugador", "app", "ambas", "nose")}
        app_ok = cnt["app"] / len(judged) if judged else None
        by = defaultdict(lambda: [0, 0])
        for r in judged:
            for c in (r.get("categorias") or "").split(";"):
                if c:
                    by[c][0] += 1
                    by[c][1] += r["veredicto"] == "app"
        cats = sorted(({"cat": c, "label": CATEGORIAS.get(c, c), "n": v[0], "app": round(100 * v[1] / v[0])}
                       for c, v in by.items()), key=lambda x: x["app"])
        est = None
        try:
            _, summ = self._latest_detail()
            if summ and app_ok is not None:
                bad = 100 - summ["acuerdo"] - summ["aceptables"]
                # de las que marca como error, sólo acierta en la parte que tú le das la razón
                est = round(summ["acuerdo"] + summ["aceptables"] * 0.5 + bad * app_ok, 1)
        except Exception:
            est = None
        prueba = None
        try:
            pf = self.logs / "calibracion" / "prueba_revision.json"
            pj = json.loads(pf.read_text(encoding="utf-8")) if pf.exists() else {"casos": []}
            prueba = {k: pj.get(k) for k in ("fecha", "n", "falsos", "n_jugador", "aciertos", "n_app", "grupos")}
            prueba["historial"] = (pj.get("historial") or [])[-6:]
            if pj.get("casos") is not None:        # al día: con lo que has juzgado desde la última prueba
                prueba["grupos"] = revisar.resumen_prueba(revisar.casos_al_dia(pj["casos"], self._revision_rows()),
                                                          revisar.load_limpio())["grupos"]
        except Exception:
            pass
        banco = revisar.load_limpio()
        revisadas = {g: sum(revisar.grupo(r["replay"], banco) == g for r in rows) for g in ("examen", "ajuste")}
        pendientes = None
        try:
            f, _ = self._latest_detail("limpio")
            if f:
                done = {f"{r['replay']}|{r['lado']}|{r['turno']}" for r in self._revision_rows()}
                pendientes = len(revisar.pendientes(json.loads(f.read_text(encoding="utf-8")), done, LOSS_ERROR, azar=True))
        except Exception:
            pendientes = None
        return {"total": len(rows), "counts": cnt, "app_ok": None if app_ok is None else round(100 * app_ok, 1),
                "cats": cats, "estimate": est, "prueba": prueba,
                "limpio": {"revisadas": revisadas, "pendientes": pendientes}}

    # ------------------------------------------------------------------ partida completa contra el bot
    BOT_LEVELS = {
        # K jugadas candidatas, semillas, mundos posibles (no conoce tus SP), temperatura del team preview
        "facil": {"K": 4, "seeds": 4, "worlds": 1, "tp": 0.6, "name": "fácil"},
        "normal": {"K": 6, "seeds": 8, "worlds": 3, "tp": 0.25, "name": "normal"},
        "dificil": {"K": 8, "seeds": 16, "worlds": 4, "tp": 0.1, "name": "difícil"},
    }

    def game_new(self, my_team: str, rival: str, bo3: bool = True, level: str = "normal") -> Dict[str, Any]:
        ours = self._read_team(my_team)
        with self.lock:
            v = self.pool.main.call("validate", team=ours)
        if not v["ok"]:
            raise ValueError(f"Tu equipo no es válido para {REG.nombre}: " + "; ".join(v["problems"][:3]))
        rival_file = self._pick_rival(my_team, rival)
        theirs = self._read_team(rival_file)
        if len(ours) < 4 or len(theirs) < 4:
            raise ValueError("Hacen falta equipos de al menos 4 Pokémon")
        level = level if level in self.BOT_LEVELS else "normal"
        with self.lock:
            sheet_ours = self.pool.main.call("sheet", team=ours, mine=True)
            sheet_theirs = self.pool.main.call("sheet", team=theirs)
        series = {"kind": "series", "my_team": my_team, "rival_file": rival_file, "rival": Path(rival_file).stem,
                  "ours": ours, "theirs": theirs, "bo3": bool(bo3), "level": level, "score": [0, 0], "games": [],
                  "game": None, "sheets": {"ours": sheet_ours, "theirs": sheet_theirs}, "t0": time.time()}
        sid = self._register(series)
        return self._game_preview(sid, series)

    def _series(self, sid: str) -> Dict[str, Any]:
        e = self.positions.get(sid)
        if not e or e.get("kind") != "series":
            raise ValueError("Partida caducada: empieza otra")
        return e

    def _game_preview(self, sid, series) -> Dict[str, Any]:
        return {"id": sid, "phase": "preview", "game": len(series["games"]) + 1, "score": series["score"],
                "bo3": series["bo3"], "rival": series["rival"], "level": self.BOT_LEVELS[series["level"]]["name"],
                "sheets": series["sheets"], "history": [{"won": g.get("won"), "turns": g.get("turns"), "report": g.get("report_id")}
                                                         for g in series["games"]]}

    def game_start(self, sid: str, order: List[int]) -> Dict[str, Any]:
        series = self._series(sid)
        if series.get("game") and not series["game"].get("over"):
            raise ValueError("Ya hay una partida en curso")
        if self._series_over(series):
            raise ValueError("La serie ya ha terminado")
        n = len(series["ours"])
        if len(order) != 4 or len(set(order)) != 4 or any(not (0 <= i < n) for i in order):
            raise ValueError("Elige 4 Pokémon (los 2 primeros salen de lead)")
        lv = self.BOT_LEVELS[series["level"]]
        with self.lock:
            info = self.pool.main.call("game_new", ours=series["ours"], theirs=series["theirs"], oursOrder=order,
                                       botTemperature=lv["tp"], seed=",".join(str(random.randint(1, 65535)) for _ in range(4)))
        series["game"] = {"gid": info["id"], "decisions": [], "order": order, "t_dec": time.time(), "over": False,
                          "n": len(series["games"]) + 1}
        return self._game_view(sid, series, info)

    def _series_over(self, series) -> bool:
        need = 2 if series["bo3"] else 1
        return max(series["score"]) >= need

    def _bot_choice(self, series, bot_state) -> Optional[str]:
        lv = self.BOT_LEVELS[series["level"]]
        with self.lock:
            if lv["worlds"] > 1:
                # el bot juega con lo que vería un humano: tu hoja abierta (sin SP) y lo que ya has sacado
                worlds = self.pool.main.call("realistic_worlds", state=bot_state, sheet=series["ours"], n=lv["worlds"],
                                             seed=random.randint(1, 10 ** 6))["worlds"]
            else:
                worlds = [bot_state]
        res = self.pool.search(worlds, K=lv["K"], Kopp=lv["K"], seeds=lv["seeds"], mega_policy="free")
        bad = set(res.get("bad_rows", []))
        rows = [i for i in range(len(res["ours"])) if i not in bad]
        if not rows:
            return None
        if series["level"] == "facil":
            M, col = res["matrix"], res["col"]
            q = {i: sum(M[i][j] * col[j] for j in range(len(col))) for i in rows}
            import math
            w = [math.exp(q[i] / 0.15) for i in rows]
        else:
            # estrategia mixta del equilibrio: impredecible como un buen jugador (descarta lo residual)
            w = [res["row"][i] if res["row"][i] >= 0.08 else 0.0 for i in rows]
            if sum(w) <= 0:
                w = [res["row"][i] for i in rows]
        i = random.choices(rows, weights=w)[0]
        return res["ours"][i]["choice"]

    def game_move(self, sid: str, choice: str, label: str = "") -> Dict[str, Any]:
        series = self._series(sid)
        g = series.get("game")
        if not g or g["over"]:
            raise ValueError("No hay ninguna partida en curso")
        with self.lock:
            before = self.pool.main.call("game_info", id=g["gid"])
            try:   # primero tu jugada (si no es legal, no cuenta ni hace pensar al bot)
                info = self.pool.main.call("game_step", id=g["gid"], ours=choice)
            except EngineError as e:
                raise ValueError(str(e).splitlines()[0])
        if before["need"] == "move":
            g["decisions"].append({"turn": before["turn"], "state": before["state"], "choice": choice, "label": label,
                                   "known": True, "reason": "", "secs": round(time.time() - g["t_dec"])})
        if info.get("awaitingBot"):
            bot = self._bot_choice(series, info["botState"])
            with self.lock:
                info = self.pool.main.call("game_step", id=g["gid"], theirs=bot)
        return self._game_view(sid, series, info)

    def game_forfeit(self, sid: str) -> Dict[str, Any]:
        series = self._series(sid)
        g = series.get("game")
        if not g or g["over"]:
            raise ValueError("No hay ninguna partida en curso")
        return self._game_finish(sid, series, {"winner": "p2", "turn": None, "events": []}, forfeit=True)

    def _game_view(self, sid, series, info) -> Dict[str, Any]:
        g = series["game"]
        if info["ended"]:
            return self._game_finish(sid, series, info)
        view = self._view(info["state"])
        if info["need"] == "switch":
            view["options"] = info["switchOptions"]
        else:
            for slot, dis in enumerate(info.get("disabled") or []):
                if dis and slot < len(view["options"]):
                    view["options"][slot] = [o for o in view["options"][slot]
                                             if not (o.get("kind") == "move" and o.get("move") in dis)] or view["options"][slot]
        g["t_dec"] = time.time()
        return {"id": sid, "phase": "battle", "need": info["need"], "turn": info["turn"], "view": view,
                "events": info["events"], "game": g["n"], "score": series["score"], "bo3": series["bo3"],
                "rival": series["rival"], "kind": "partida", "hiddenBench": True,
                "preview": {"ours": [m["species"] for m in series["ours"]], "theirs": [m["species"] for m in series["theirs"]]},
                "theirsSheet": series["sheets"]["theirs"], "sheets": series["sheets"],
                "level": self.BOT_LEVELS[series["level"]]["name"]}

    def _game_finish(self, sid, series, info, forfeit: bool = False) -> Dict[str, Any]:
        g = series["game"]
        g["over"] = True
        won = info["winner"] == "p1"
        if info["winner"] in ("p1", "p2"):
            series["score"][0 if won else 1] += 1
        with self.lock:
            try:
                self.pool.main.call("game_end", id=g["gid"])
            except Exception:
                pass
        report = None
        if g["decisions"]:
            parsed = {"format": REG.formato, "players": ["Tú", "Bot"], "side": "p1", "opp": "p2",
                      "winner": None if info["winner"] not in ("p1", "p2") else ("Tú" if won else f"Bot · {series['rival']}"),
                      "ourName": "Tú", "oppName": f"Bot · {series['rival']}", "icon": "⚔", "usedOurFile": True,
                      "openSheets": True, "sheets": {"ours": series["ours"], "theirs": series["theirs"]},
                      "preview": {"ours": [m["species"] for m in series["ours"]], "theirs": [m["species"] for m in series["theirs"]]},
                      "decisions": g["decisions"]}
            report = self._start_analysis(parsed, series["my_team"], False, False)
            report["game"] = g["n"]
        series["games"].append({"won": won if info["winner"] in ("p1", "p2") else None, "turns": len(g["decisions"]),
                                "report_id": report["id"] if report else None, "forfeit": forfeit})
        over = self._series_over(series)
        if over:
            self._log_series(series)
        return {"id": sid, "phase": "end", "won": won if info["winner"] in ("p1", "p2") else None, "forfeit": forfeit,
                "events": info.get("events", []), "score": series["score"], "bo3": series["bo3"], "game": g["n"],
                "seriesOver": over, "seriesWon": over and series["score"][0] > series["score"][1], "report": report,
                "rival": series["rival"], "history": self._game_preview(sid, series)["history"]}

    def game_next(self, sid: str) -> Dict[str, Any]:
        series = self._series(sid)
        if self._series_over(series):
            raise ValueError("La serie ya ha terminado: empieza otra")
        return self._game_preview(sid, series)

    def _log_series(self, series) -> None:
        with open(self.logs / "series_bot.csv", "a", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            if f.tell() == 0:
                w.writerow(["fecha", "equipo", "rival", "nivel", "formato", "resultado", "marcador"])
            sc = series["score"]
            w.writerow([time.strftime("%Y-%m-%d %H:%M"), Path(series["my_team"]).stem, series["rival"], series["level"],
                        "Bo3" if series["bo3"] else "Bo1", "victoria" if sc[0] > sc[1] else "derrota", f"{sc[0]}-{sc[1]}"])

    def game_stats(self) -> Dict[str, Any]:
        f = self.logs / "series_bot.csv"
        if not f.exists():
            return {"n": 0}
        rows = list(csv.DictReader(open(f, encoding="utf-8")))
        out = {"n": len(rows)}
        for lv in self.BOT_LEVELS:
            rr = [r for r in rows if r["nivel"] == lv]
            if rr:
                out[lv] = {"n": len(rr), "ganadas": sum(r["resultado"] == "victoria" for r in rr)}
        return out

    # ------------------------------------------------------------------ velocidades y daños
    def _recent_rival(self, my_team: str) -> str:
        """Rival del metajuego, con preferencia por los equipos más recientes (número de MC más alto)."""
        def num(t):
            m = re.match(r"[A-Za-z]+(\d+)_", Path(t["path"]).name)
            return int(m.group(1)) if m else 0
        pool = [t for t in self.team_files() if t["path"] != my_team and "/" in t["path"].replace("teams/", "", 1)] or \
               [t for t in self.team_files() if t["path"] != my_team]
        pool.sort(key=num, reverse=True)
        if random.random() < 0.65:
            pool = pool[:120]
        return random.choice(pool)["path"]

    def quiz_next(self, my_team: str, rival: str, kind: str) -> Dict[str, Any]:
        item = self.quiz.due() if random.random() < 0.4 else None
        if item and (kind in ("", "mix") or item["q"]["type"] == kind):
            q, rv, rival_name = item["q"], True, item["q"].get("rival", "")
        else:
            ours = self._read_team(my_team)
            rival_file = rival or self._recent_rival(my_team)
            theirs = self._read_team(rival_file)
            q = None
            for _ in range(4):
                with self.lock:
                    q = self.pool.main.call("quiz_question", ours=ours, theirs=theirs, kind=kind)
                if q:
                    break
                if not rival:
                    theirs = self._read_team(self._recent_rival(my_team))
            if not q:
                raise ValueError("No he encontrado una pregunta interesante con estos equipos; prueba otra vez")
            rv, rival_name = False, Path(rival_file).stem
            q["rival"] = rival_name
        qid = uuid.uuid4().hex[:10]
        self.quiz_pending[qid] = {"q": q, "review": rv}
        if len(self.quiz_pending) > 300:
            for k in list(self.quiz_pending)[:150]:
                self.quiz_pending.pop(k, None)
        public = {k: v for k, v in q.items() if k not in ("answer", "explanation", "key")}
        return {"id": qid, "review": rv, **public, "stats": self.quiz.stats()}

    def quiz_answer(self, qid: str, choice: str, ms: int, timeout: bool) -> Dict[str, Any]:
        e = self.quiz_pending.pop(qid, None)
        if not e:
            raise ValueError("Pregunta caducada")
        q = e["q"]
        ok = (choice == q["answer"]) and not timeout
        info = self.quiz.record(q, ok, int(ms or 0), timeout, e["review"])
        return {"correct": ok, "answer": q["answer"], "explanation": q["explanation"], "review": info,
                "timeout": timeout, "stats": self.quiz.stats()}

    # ------------------------------------------------------------------ team preview
    def _pick_rival(self, my_team: str, rival: str) -> str:
        top = {str(p.relative_to(ROOT)).replace("\\", "/") for p in (ROOT / "teams").glob("*.txt")}
        candidates = [t for t in self.team_files() if t["path"] != my_team and t["path"] not in top] or \
                     [t for t in self.team_files() if t["path"] != my_team]
        return rival if rival else random.choice(candidates)["path"]

    def preview_new(self, my_team: str, rival: str, deep: bool = False, realistic: bool = False) -> Dict[str, Any]:
        ours = self._read_team(my_team)
        rival_file = self._pick_rival(my_team, rival)
        theirs = self._read_team(rival_file)
        if len(ours) < 4 or len(theirs) < 4:
            raise ValueError("Hacen falta equipos de al menos 4 Pokémon")
        return self._start_preview(ours, theirs, my_team, rival_file, deep, realistic)

    ROLL_WEIGHT = 4      # una tarea de partidas enteras pesa como 4 de turno 1 en la barra de progreso

    def _start_preview(self, ours, theirs, my_team, rival_file, deep, realistic, setup=None, vals=None, review=None,
                       rolls=None):
        if setup is None or "bring" not in setup:
            work_theirs = setup["sets"]["theirs"] if setup else theirs
            if realistic and setup is None:   # sólo su hoja: SP y naturalezas estimados
                with self.lock:
                    work_theirs = self.pool.main.call("guess_team", team=theirs)
            with self.lock:
                fresh = self.pool.main.call("preview_setup", ours=setup["sets"]["ours"] if setup else ours, theirs=work_theirs)
            if setup is None:
                setup = fresh
            else:   # repaso guardado antes de existir la selección de 4
                setup = dict(setup, bring=fresh["bring"])
        for e in self.positions.values():          # cancelar cálculos de ejercicios anteriores
            if e.get("kind") == "preview":
                e["cancel"] = True
        tasks = [] if vals else [("lead", _k(p["pair"]), _k(r["pair"]), p["order"], r["order"])
                                 for r in setup["rivalLeads"] for p in setup["myPairs"]]
        if not rolls:
            tasks += [("roll", _k(b["set"]), b["order"]) for b in setup["bring"]]
        weight = sum(self.ROLL_WEIGHT if t[0] == "roll" else 1 for t in tasks)
        entry = {"kind": "preview", "setup": setup, "vals": dict(vals or {}), "rolls": dict(rolls or {}), "done": 0,
                 "total": weight or 1, "t0": time.time(), "rival": Path(rival_file).stem,
                 "ours": setup["sets"]["ours"], "theirs": setup["sets"]["theirs"], "event": threading.Event(),
                 "cancel": False, "error": None, "deep": deep, "realistic": realistic, "my_team": my_team,
                 "rival_file": rival_file, "orig_ours": ours, "orig_theirs": theirs,
                 "review_id": review["id"] if review else None}
        pid = self._register(entry)
        if tasks:
            # primero las partidas enteras (más largas), repartidas entre todos los motores
            tasks.sort(key=lambda t: t[0] != "roll")
            threading.Thread(target=self._compute_preview, args=(entry, tasks), daemon=True).start()
        else:
            entry["done"] = entry["total"]
            entry["event"].set()
        return {"id": pid, "kind": "preview", "rival": entry["rival"], "ours": setup["ours"], "theirs": setup["theirs"],
                "total": entry["total"], "deep": deep, "realistic": realistic, "review": self._review_info(review)}

    def _compute_preview(self, entry: Dict[str, Any], tasks) -> None:
        engines = self.pool.engines
        n = len(engines)
        K, seeds = (8, 10) if entry.get("deep") else (6, 4)

        R = entry["setup"]["rivalLeads"]
        n_games = 64 if entry.get("deep") else 32
        games = [max(6, round(n_games * r["p"])) for r in R]

        def work(eng, chunk):
            for t in chunk:
                if entry["cancel"]:
                    return
                if t[0] == "roll":
                    _, sk, order = t
                    v = eng.call("bring_rollouts", ours=entry["ours"], theirs=entry["theirs"], selections=[order],
                                 rivals=[r["order"] for r in R], games=games, seed0=17)
                    entry["rolls"][sk] = {"wr": v["wr"][0], "margin": v["margin"][0], "order": order}
                    entry["done"] += self.ROLL_WEIGHT
                    continue
                _, mk, rk, mo, ro = t
                v = eng.call("lead_value", ours=entry["ours"], theirs=entry["theirs"], oursOrder=mo, theirsOrder=ro,
                             K=K, Kopp=K, seeds=seeds)
                entry["vals"].setdefault(mk, {})[rk] = v["value"]
                entry["done"] += 1
        try:
            futs = [self.pool.exec.submit(work, engines[i], tasks[i::n]) for i in range(n)]
            for f in futs:
                f.result()
        except Exception as e:
            entry["error"] = str(e).splitlines()[0][:300]
        finally:
            entry["event"].set()

    def preview_progress(self, pid: str) -> Dict[str, Any]:
        e = self.positions.get(pid)
        if not e or e.get("kind") != "preview":
            return {"done": 0, "total": 0}
        return {"done": e["done"], "total": e["total"], "ready": e["event"].is_set()}

    def preview_eval(self, pid: str, pair: List[int], tournament: bool = False, timeout: bool = False) -> Dict[str, Any]:
        e = self.positions.get(pid)
        if not e or e.get("kind") != "preview":
            raise ValueError("Ejercicio caducado; pide uno nuevo")
        if len(pair) == 4 and len(set(pair)) == 4:
            return self._preview_eval_full(e, [int(x) for x in pair], tournament, timeout)
        if len(set(pair)) != 2:
            raise ValueError("Elige 4 Pokémon: los 2 primeros son tu lead")
        secs = time.time() - e["t0"]
        e["event"].wait(600)
        if e["error"]:
            raise ValueError(e["error"])
        setup, vals = e["setup"], e["vals"]
        R = setup["rivalLeads"]
        T = setup["table"]
        scores, severe = {}, {}
        for p in setup["myPairs"]:
            k = _k(p["pair"])
            scores[k] = sum(r["p"] * vals[k][_k(r["pair"])] for r in R)
            severe[k] = sum(r["p"] * sum(1 for f in r["pair"] for m in p["pair"] if T["thr"][m][f] >= 0.5) for r in R)
        mine = _k(pair)
        best = max(scores, key=scores.get)
        loss = max(0.0, scores[best] - scores[mine])
        pts = round(100 * max(0.0, 1 - loss / 0.4))
        if loss < 0.03:
            verdict, pts, tone = "Lead correcto: de los mejores contra este rival", max(pts, 90), "good"
        elif loss < 0.10:
            verdict, tone = "Lead aceptable: algo peor que el mejor", "ok"
        else:
            verdict, tone = "Lead mejorable", "bad"
        O, Th = setup["ours"], setup["theirs"]
        nm = lambda k: " + ".join(O[int(i)]["name"] for i in k.split("-"))
        # detalle del turno 1 contra su lead más probable: mejor lead y el tuyo (en paralelo)
        top = setup["rivalLeads"][0]
        order_of = {_k(p["pair"]): p["order"] for p in setup["myPairs"]}
        if "details" not in e:
            e["details"] = {}
        need = [k for k in {best, mine} if k not in e["details"]]
        engs = self.pool.engines
        futs = {k: self.pool.exec.submit(engs[i % len(engs)].call, "lead_detail", ours=e["ours"], theirs=e["theirs"],
                                         oursOrder=order_of[k], theirsOrder=top["order"],
                                         K=8 if e.get("deep") else 6, Kopp=8 if e.get("deep") else 6,
                                         seeds=10 if e.get("deep") else 4)
                for i, k in enumerate(need)}
        for k, f in futs.items():
            try:
                e["details"][k] = f.result()
            except Exception:
                e["details"][k] = None
        try:
            why_best = explain_best(setup, scores, severe, mine, best, e["details"].get(best), e["details"].get(mine))
        except Exception as ex:
            why_best = [f"(No pude generar la explicación del mejor lead: {ex})"]
        try:
            why = explain_preview(setup, vals, scores, mine, best, severe)
        except Exception as ex:
            why = [f"(No pude generar la explicación: {ex})"]
        ranking = sorted(({"label": nm(k), "pair": [int(i) for i in k.split("-")], "q": round(v, 3),
                           "severe": round(severe[k], 1), "mine": k == mine, "best": k == best} for k, v in scores.items()),
                         key=lambda o: -o["q"])
        rival_rows = [{"label": " + ".join(Th[i]["name"] for i in r["pair"]), "pair": r["pair"], "p": r["p"],
                       "tags": r["tags"], "mine": round(vals[mine][_k(r["pair"])], 3),
                       "best": round(vals[best][_k(r["pair"])], 3)} for r in R]
        best_order = next(p["order"] for p in setup["myPairs"] if _k(p["pair"]) == best)
        self._log("preview", e["rival"], nm(mine), nm(best), pts, loss, secs)
        if tournament:
            self._log_torneo("preview", pts, secs, timeout)
        review = self._after_eval({**e, "ours": e["orig_ours"], "theirs": e["orig_theirs"]}, tone, nm(mine), nm(best), loss,
                                  {"tipo": "preview", "setup": setup, "vals": vals, "deep": e.get("deep", False)})
        if e.get("review_id") and review is not None:
            e["review_done"] = True
        return {"review": review, "deep": e.get("deep", False), "realistic": e.get("realistic", False),
                "tournament": tournament, "timeout": timeout, "verdict": verdict, "tone": tone, "points": pts, "loss": round(loss, 3), "seconds": round(secs),
                "ranking": ranking, "rivalLeads": rival_rows, "why": why, "whyBest": why_best,
                "bestLabel": nm(best), "best": [int(i) for i in best.split("-")],
                "bestBack": best_order[2:], "value": round(scores[mine], 3)}

    SEL_W = (0.4, 0.4, 0.2)   # pesos de la selección de 4: turno 1, partidas simuladas, cobertura 1 contra 1

    @classmethod
    def _selection_scores(cls, setup, vals, rolls) -> Dict[str, Any]:
        """Puntúa las 90 opciones (4 de 6 × lead) contra lo que suele traer el rival."""
        R, Th = setup["rivalLeads"], setup["theirs"]
        leadv = {k: sum(r["p"] * vals[k][_k(r["pair"])] for r in R) for k in vals}
        winp = {k: sum(r["p"] * v["wr"][i] for i, r in enumerate(R)) for k, v in rolls.items()}
        # partida: victorias y margen final de las partidas simuladas (el margen reduce el ruido)
        game = {k: 0.5 * (2 * winp[k] - 1) + 0.5 * sum(r["p"] * v["margin"][i] for i, r in enumerate(R)) for k, v in rolls.items()}
        D = duel_matrix(setup["table"])
        presence = [sum(r["p"] for r in R if j in r["order"]) for j in range(len(Th))]
        pw = sum(presence) or 1.0
        # cobertura 1 contra 1: lo bien que responde tu mejor Pokémon a cada uno de los que suele traer
        cover = {_k(b["set"]): sum(p * max(D[i][j]["v"] for i in b["set"]) for j, p in enumerate(presence)) / pw
                 for b in setup["bring"]}
        wl, wg, wc = cls.SEL_W
        options = []
        for b in setup["bring"]:
            S = b["set"]
            sk = _k(S)
            for a in range(4):
                for c in range(a + 1, 4):
                    L = [S[a], S[c]]
                    lk = _k(L)
                    options.append({"S": S, "L": L, "sk": sk, "lk": lk, "total": wl * leadv[lk] + wg * game[sk] + wc * cover[sk]})
        options.sort(key=lambda o: -o["total"])
        return {"options": options, "leadv": leadv, "winp": winp, "game": game, "cover": cover, "D": D, "presence": presence}

    def _preview_eval_full(self, e, sel: List[int], tournament: bool, timeout: bool) -> Dict[str, Any]:
        """Team preview completo: tus 4 y tu lead contra lo que suele traer el rival."""
        secs = time.time() - e["t0"]
        e["event"].wait(900)
        if e["error"]:
            raise ValueError(e["error"])
        setup, vals, rolls = e["setup"], e["vals"], e["rolls"]
        R, O, Th = setup["rivalLeads"], setup["ours"], setup["theirs"]
        sc = self._selection_scores(setup, vals, rolls)
        options, leadv, winp, game, cover, D, presence = (sc[k] for k in ("options", "leadv", "winp", "game", "cover", "D", "presence"))
        W_LEAD, W_GAME, W_COVER = self.SEL_W
        mS, mL = sorted(sel), sel[:2]
        mine = next(o for o in options if o["sk"] == _k(mS) and o["lk"] == _k(mL))
        best = options[0]
        loss = max(0.0, best["total"] - mine["total"])
        pts = round(100 * max(0.0, 1 - loss / 0.4))
        if loss < 0.04:
            verdict, pts, tone = "Selección correcta: de las mejores contra este rival", max(pts, 90), "good"
        elif loss < 0.10:
            verdict, tone = "Selección aceptable: algo peor que la mejor", "ok"
        else:
            verdict, tone = "Selección mejorable", "bad"
        try:
            why = explain_selection(setup, D, presence, mS, mL, best["S"], best["L"], leadv, winp, cover,
                                    {o["sk"] + "|" + o["lk"]: o["total"] for o in options})
        except Exception as ex:
            why = [f"(No pude generar la explicación: {ex})"]
        # el detalle de los leads (turno 1) con las explicaciones de siempre
        severe = {k: sum(r["p"] * sum(1 for f in r["pair"] for m in [int(x) for x in k.split("-")]
                                      if setup["table"]["thr"][m][f] >= 0.5) for r in R) for k in leadv}
        top = R[0]
        order_of = {_k(p["pair"]): p["order"] for p in setup["myPairs"]}
        e.setdefault("details", {})
        need = [k for k in {best["lk"], mine["lk"]} if k not in e["details"]]
        engs = self.pool.engines
        futs = {k: self.pool.exec.submit(engs[i % len(engs)].call, "lead_detail", ours=e["ours"], theirs=e["theirs"],
                                         oursOrder=order_of[k], theirsOrder=top["order"],
                                         K=8 if e.get("deep") else 6, Kopp=8 if e.get("deep") else 6,
                                         seeds=10 if e.get("deep") else 4)
                for i, k in enumerate(need)}
        for k, f in futs.items():
            try:
                e["details"][k] = f.result()
            except Exception:
                e["details"][k] = None
        try:
            why_lead = explain_best(setup, leadv, severe, mine["lk"], best["lk"], e["details"].get(best["lk"]),
                                    e["details"].get(mine["lk"])) if mine["lk"] != best["lk"] else \
                explain_preview(setup, vals, leadv, mine["lk"], best["lk"], severe)
        except Exception as ex:
            why_lead = [f"(No pude generar la explicación del lead: {ex})"]
        names = lambda idx: " + ".join(O[i]["name"] for i in idx)
        label = lambda o: f"{names(o['L'])} · atrás {', '.join(O[i]['name'] for i in o['S'] if i not in o['L'])}"
        ranking = []
        for i, o in enumerate(options):
            if i < 10 or o is mine:
                ranking.append({"rank": i + 1, "label": label(o), "lead": round(leadv[o["lk"]], 3), "win": round(winp[o["sk"]], 3),
                                "game": round(game[o["sk"]], 3), "cover": round(cover[o["sk"]], 3), "q": round(o["total"], 3), "mine": o is mine, "best": o is best,
                                "sel": o["L"] + [x for x in o["S"] if x not in o["L"]]})
        weak_m = selection_weaknesses(D, mS, presence)
        weak_b = selection_weaknesses(D, best["S"], presence)
        rival_rows = [{"label": " + ".join(Th[i]["name"] for i in r["pair"]), "pair": r["pair"], "p": r["p"], "tags": r["tags"],
                       "back": [i for i in r["order"] if i not in r["pair"]],
                       "mine": round(vals[mine["lk"]][_k(r["pair"])], 3), "best": round(vals[best["lk"]][_k(r["pair"])], 3),
                       "winMine": round(rolls[mine["sk"]]["wr"][n], 3), "winBest": round(rolls[best["sk"]]["wr"][n], 3)}
                      for n, r in enumerate(R)]
        self._log("preview", e["rival"], label(mine), label(best), pts, loss, secs)
        rating = None
        if not e.get("review_id") and not e.get("rated"):
            e["rated"] = True
            tots = sorted((o["total"] for o in options), reverse=True)
            spread = tots[0] - tots[len(tots) // 4]          # lo que se pierde ya en el primer cuartil
            near = sum(1 for t in tots if tots[0] - t < 0.04)
            diff = 1000 + 1000 * max(0.0, min(1.0, spread / 0.3 - 0.05 * max(0, near - 2)))
            rating = self.rating.update("preview", diff, pts)
        if tournament:
            self._log_torneo("preview", pts, secs, timeout)
        review = self._after_eval({**e, "ours": e["orig_ours"], "theirs": e["orig_theirs"]}, tone, label(mine), label(best), loss,
                                  {"tipo": "preview", "setup": setup, "vals": vals, "rolls": rolls, "deep": e.get("deep", False)})
        if e.get("review_id") and review is not None:
            e["review_done"] = True
        return {"full": True, "rating": rating, "review": review, "deep": e.get("deep", False), "realistic": e.get("realistic", False),
                "tournament": tournament, "timeout": timeout, "verdict": verdict, "tone": tone, "points": pts,
                "loss": round(loss, 3), "seconds": round(secs), "value": round(mine["total"], 3),
                "why": why, "whyLead": why_lead, "ranking": ranking, "rivalLeads": rival_rows,
                "best": best["L"] + [x for x in best["S"] if x not in best["L"]], "bestLabel": label(best),
                "mine": {"lead": round(leadv[mine["lk"]], 3), "win": round(winp[mine["sk"]], 3), "cover": round(cover[mine["sk"]], 3)},
                "bestStats": {"lead": round(leadv[best["lk"]], 3), "win": round(winp[best["sk"]], 3), "cover": round(cover[best["sk"]], 3)},
                "weights": {"lead": W_LEAD, "game": W_GAME, "cover": W_COVER},
                "matrix": {"d": [[{"v": c["v"], "hu": c["hu"], "ht": c["ht"], "spd": c["spd"], "off": c["off"], "thr": c["thr"]}
                                  for c in row] for row in D], "presence": [round(p, 3) for p in presence],
                           "moves": setup["table"].get("moves"), "tmoves": setup["table"].get("tmoves")},
                "weak": {"mine": [{"j": w["j"], "p": round(w["p"], 3), "beats": w["beats"]} for w in weak_m],
                         "best": [{"j": w["j"], "p": round(w["p"], 3), "beats": w["beats"]} for w in weak_b]}}

    def session_save(self, body: Dict[str, Any]) -> Dict[str, Any]:
        with open(self.logs / "sesiones.csv", "a", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            if f.tell() == 0:
                w.writerow(["fecha", "duracion", "minutos", "incompleta", "bloques", "fallos", "d_tactico", "d_preview"])
            w.writerow([time.strftime("%Y-%m-%d %H:%M"), body.get("len", ""), body.get("minutes", 0), int(bool(body.get("early"))),
                        " · ".join(f"{b.get('kind')} {b.get('n')} ({b.get('score')})" for b in body.get("blocks", [])),
                        body.get("errors", 0), body.get("dTactico", 0), body.get("dPreview", 0)])
        return {"ok": True}

    def progress(self) -> Dict[str, Any]:
        """Evolución semana a semana: rating, puntos, categorías, quiz y series contra el bot."""
        import datetime as dt

        def rows(name):
            f = self.logs / name
            return list(csv.DictReader(open(f, encoding="utf-8"))) if f.exists() else []

        def week(date: str) -> str:
            d = dt.date.fromisoformat(date[:10])
            return (d - dt.timedelta(days=d.weekday())).isoformat()   # lunes de esa semana

        today = dt.date.today()
        weeks = [(today - dt.timedelta(days=today.weekday()) - dt.timedelta(weeks=k)).isoformat() for k in range(11, -1, -1)]
        ej = rows("ejercicios.csv")
        per_week = []
        for w in weeks:
            rs = [r for r in ej if week(r["fecha"]) == w]
            pos = [r for r in rs if r["tipo"] != "preview"]
            pv = [r for r in rs if r["tipo"] == "preview"]
            avg = lambda xs: round(sum(float(r["puntos"]) for r in xs) / len(xs), 1) if xs else None
            per_week.append({"week": w, "n": len(rs), "pts": avg(pos), "npos": len(pos), "preview": avg(pv), "npv": len(pv)})
        ratings = {a: [{"t": h[0], "r": h[1], "d": h[2], "p": h[3]} for h in self.rating.data[a]["hist"][-400:]] for a in Rating.AREAS}
        cats = rows("categorias.csv")
        cut_new = (today - dt.timedelta(days=28)).isoformat()
        cut_old = (today - dt.timedelta(days=56)).isoformat()
        cat_out = []
        for cat, label in CATEGORIAS.items():
            new = [r for r in cats if r["categoria"] == cat and r["fecha"][:10] >= cut_new]
            old = [r for r in cats if r["categoria"] == cat and cut_old <= r["fecha"][:10] < cut_new]
            pct = lambda xs: round(100 * sum(int(r["acierto"]) for r in xs) / len(xs)) if xs else None
            cat_out.append({"cat": cat, "label": label, "now": pct(new), "nNow": len(new), "before": pct(old), "nBefore": len(old)})
        qz = rows("velocidades.csv")
        quiz_w = []
        for w in weeks[-8:]:
            rs = [r for r in qz if week(r["fecha"]) == w]
            quiz_w.append({"week": w, "n": len(rs), "pct": round(100 * sum(int(r["correcto"]) for r in rs) / len(rs)) if rs else None})
        series = rows("series_bot.csv")
        bot = {}
        for r in series:
            b = bot.setdefault(r["nivel"], {"n": 0, "won": 0, "last30": 0, "won30": 0})
            b["n"] += 1
            b["won"] += r["resultado"] == "victoria"
            if r["fecha"][:10] >= (today - dt.timedelta(days=30)).isoformat():
                b["last30"] += 1
                b["won30"] += r["resultado"] == "victoria"
        partidas = rows("partidas.csv")[-20:]
        sesiones = rows("sesiones.csv")[-12:]
        calib = None
        cf = REG.archivo(self.logs / "calibracion" / "ultima.json")
        if cf.exists():
            try:
                c = json.loads(cf.read_text(encoding="utf-8"))
                calib = {k: c.get(k) for k in ("fecha", "replays", "decisiones", "acuerdo", "aceptables", "perdida_media", "categorias", "juez", "banco", "historial")}
            except Exception:
                calib = None
        juez = None
        jf = ROOT / "engine" / "juez.json"
        if jf.exists():
            try:
                j = json.loads(jf.read_text(encoding="utf-8"))
                if j.get("fases"):          # v2: juez de siempre (peso «a») + correcciones, por fase
                    fases = {k: {"a": v.get("a"), "lambda": v.get("lambda"), "posiciones": v.get("posiciones"),
                                 "pesos": sorted(({"n": n, "w": w} for n, w in zip(j.get("names", []), v.get("w", [])) if abs(w) >= 0.01),
                                                 key=lambda x: -abs(x["w"]))}
                             for k, v in j["fases"].items()}
                    pesos = fases.get("medio", {}).get("pesos", [])
                else:
                    fases = None
                    pesos = sorted(({"n": n, "w": w} for n, w in zip(j.get("names", []), j.get("w", []))), key=lambda x: -abs(x["w"]))
                juez = {"activo": bool(j.get("activo")), "fecha": j.get("fecha"), "metricas": j.get("metricas"),
                        "pesos": pesos, "fases": fases}
            except Exception:
                juez = None
        return {"juez": juez, "weeks": per_week, "ratings": ratings, "summary": self.rating.summary(), "cats": cat_out, "quiz": quiz_w,
                "bot": bot, "partidas": [{"fecha": r["fecha"], "rival": r["rival"], "precision": r["precision"],
                                          "resultado": r["resultado"]} for r in partidas],
                "total": len(ej), "calibracion": calib, "calibracion_limpio": self._calibracion_limpio(), "revision": self.revision_stats(), "experimentos": self._last_experiment(), "experiencia": self._last_experience(), "sesiones": [{"fecha": r["fecha"], "minutos": r["minutos"], "bloques": r["bloques"],
                                                 "fallos": r["fallos"], "dTactico": r["d_tactico"], "incompleta": r["incompleta"]} for r in sesiones]}

    def history(self) -> Dict[str, Any]:
        f = self.logs / "ejercicios.csv"
        if not f.exists():
            return {"n": 0, "repaso": self.repaso.stats(), "torneo": self.torneo_stats(), "rating": self.rating.summary()}
        rows = list(csv.DictReader(open(f, encoding="utf-8")))
        if not rows:
            return {"n": 0, "repaso": self.repaso.stats(), "torneo": self.torneo_stats(), "rating": self.rating.summary()}
        last = rows[-20:]
        avg = lambda rs: round(sum(float(r["puntos"]) for r in rs) / len(rs), 1) if rs else None
        return {"n": len(rows), "media": avg(rows), "ultimos20": avg(last), "repaso": self.repaso.stats(), "rating": self.rating.summary(),
                "torneo": self.torneo_stats(),
                "porTipo": {k: avg([r for r in rows if r["tipo"] == k]) for k in ("lead", "medio", "final", "preview")}}


def make_handler(app: App):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code: int, body: bytes, ctype: str, cache: bool = False):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "max-age=604800" if cache else "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, obj, code=200):
            self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

        def do_GET(self):
            u = urlparse(self.path)
            if u.path in ("/", "/index.html"):
                self._send(200, (WEB / "index.html").read_bytes().replace(b"__REGULACION__", REG.nombre.encode("utf-8")),
                           "text/html; charset=utf-8")
            elif u.path == "/favicon.ico":
                self._send(200, (WEB / "img" / "roobot.ico").read_bytes(), "image/x-icon", cache=True)
            elif u.path.startswith("/img/"):
                f = (WEB / "img" / Path(u.path[5:]).name).resolve()
                types = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif", ".webp": "image/webp", ".ico": "image/x-icon"}
                if f.parent == (WEB / "img").resolve() and f.is_file() and f.suffix.lower() in types:
                    self._send(200, f.read_bytes(), types[f.suffix.lower()])
                else:
                    self._send(404, b"no encontrado", "text/plain")
            elif u.path.startswith("/sprites/"):
                got = sprites.get(u.path[len("/sprites/"):])
                if got:
                    self._send(200, got[0], got[1], cache=True)
                else:
                    self._send(404, b"no encontrado", "text/plain")
            elif u.path == "/api/version":
                self._json({"version": APP_VERSION})
            elif u.path == "/api/teams":
                self._json({"teams": app.team_files()})
            elif u.path == "/api/history":
                self._json(app.history())
            elif u.path == "/api/progress":
                self._json(app.progress())
            elif u.path == "/api/game/stats":
                self._json(app.game_stats())
            elif u.path == "/api/plan/list":
                self._json(app.plan_list())
            elif u.path == "/api/plan/progress":
                self._json(app.plan_progress(parse_qs(u.query).get("id", [""])[0]))
            elif u.path == "/api/weak":
                self._json(app.debil.stats())
            elif u.path == "/api/replay/progress":
                self._json(app.replay_progress(parse_qs(u.query).get("id", [""])[0]))
            elif u.path == "/api/preview_progress":
                self._json(app.preview_progress(parse_qs(u.query).get("id", [""])[0]))
            else:
                self._send(404, b"no encontrado", "text/plain")

        def do_POST(self):
            u = urlparse(self.path)
            try:
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n) or b"{}")
                if u.path == "/api/shutdown":
                    self._json({"ok": True})
                    threading.Thread(target=self.server.shutdown, daemon=True).start()
                    return
                if u.path == "/api/new":
                    self._json(app.new_position(body["team"], body.get("rival") or "", body.get("kind", "mixto"),
                                                bool(body.get("realistic"))))
                elif u.path == "/api/plan/start":
                    self._json(app.plan_start(body.get("team", ""), body.get("rival", ""), bool(body.get("deep")),
                                              bool(body.get("refresh"))))
                elif u.path == "/api/plan/get":
                    self._json(app.plan_get(body.get("key", "")))
                elif u.path == "/api/plan/notes":
                    self._json(app.plan_notes(body.get("key", ""), body.get("notes", "")))
                elif u.path == "/api/session/save":
                    self._json(app.session_save(body))
                elif u.path == "/api/game/new":
                    self._json(app.game_new(body.get("team", ""), body.get("rival", ""), bool(body.get("bo3", True)),
                                            body.get("level", "normal")))
                elif u.path == "/api/game/start":
                    self._json(app.game_start(body["id"], [int(x) for x in body.get("order", [])]))
                elif u.path == "/api/game/move":
                    self._json(app.game_move(body["id"], body["choice"], body.get("label", "")))
                elif u.path == "/api/game/next":
                    self._json(app.game_next(body["id"]))
                elif u.path == "/api/game/forfeit":
                    self._json(app.game_forfeit(body["id"]))
                elif u.path == "/api/replay/start":
                    self._json(app.replay_start(body.get("source", ""), body.get("team", ""), body.get("side", ""),
                                                bool(body.get("deep")), bool(body.get("look"))))
                elif u.path == "/api/replay/turn":
                    self._json(app.replay_turn(body["id"], int(body["idx"])))
                elif u.path == "/api/replay/practice":
                    self._json(app.replay_practice(body["id"], int(body["idx"])))
                elif u.path == "/api/revision/next":
                    self._json(app.revision_next())
                elif u.path == "/api/revision/answer":
                    self._json(app.revision_answer(body["id"], body.get("veredicto", ""), body.get("nota", "")))
                elif u.path == "/api/quiz/next":
                    self._json(app.quiz_next(body["team"], body.get("rival") or "", body.get("kind") or "mix"))
                elif u.path == "/api/quiz/answer":
                    self._json(app.quiz_answer(body["id"], body.get("choice", ""), body.get("ms", 0), bool(body.get("timeout"))))
                elif u.path == "/api/review_next":
                    self._json(app.review_next())
                elif u.path == "/api/preview_new":
                    self._json(app.preview_new(body["team"], body.get("rival") or "", bool(body.get("deep")),
                                               bool(body.get("realistic"))))
                elif u.path == "/api/preview_eval":
                    self._json(app.preview_eval(body["id"], [int(x) for x in body["pair"]],
                                                bool(body.get("tournament")), bool(body.get("timeout"))))
                elif u.path == "/api/evaluate":
                    self._json(app.evaluate(body["id"], body["choice"], body.get("label", ""), bool(body.get("deep")),
                                            bool(body.get("tournament")), bool(body.get("timeout")), bool(body.get("look"))))
                else:
                    self._json({"error": "ruta desconocida"}, 404)
            except Exception as e:
                self._json({"error": str(e).splitlines()[0][:300]}, 400)
    return H


class Server(ThreadingHTTPServer):
    # en Windows, SO_REUSEADDR deja que DOS programas escuchen en el mismo puerto (y contestaría
    # el antiguo): allí se pide el puerto en exclusiva
    allow_reuse_address = os.name != "nt"
    daemon_threads = True

    def server_bind(self):
        if os.name == "nt" and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


def _bind(handler, port: int):
    try:
        return Server(("127.0.0.1", port), handler), port
    except OSError:
        pass
    # ¿hay otra RooBot abierta en ese puerto? Se le pide que se cierre
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{port}/api/shutdown", data=b"{}", method="POST")
        urllib.request.urlopen(req, timeout=3)
        time.sleep(2)
        return Server(("127.0.0.1", port), handler), port
    except Exception:
        pass
    for p in range(port + 1, port + 20):
        try:
            srv = Server(("127.0.0.1", p), handler)
            print(f"AVISO: el puerto {port} está ocupado (seguramente una ventana antigua de app.bat). "
                  f"Uso el {p}. Cierra la ventana antigua cuando puedas.")
            return srv, p
        except OSError:
            continue
    raise SystemExit("No encuentro un puerto libre. Cierra las ventanas antiguas de app.bat.")


def main(argv=None):
    ap = argparse.ArgumentParser(description="Ejercicios de posición con interfaz gráfica")
    ap.add_argument("--puerto", type=int, default=8765)
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--rivales", nargs="+", default=[REG.equipos])
    ap.add_argument("--no-abrir", action="store_true")
    a = ap.parse_args(argv)
    app = App(a.workers, a.rivales, ROOT / "logs")
    srv, port = _bind(make_handler(app), a.puerto)
    url = f"http://127.0.0.1:{port}"
    print(f"RooBot · Ejercicios (versión {APP_VERSION}) en {url}  (Ctrl+C para salir)")
    if not a.no_abrir:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        app.pool.close()


if __name__ == "__main__":
    main()
