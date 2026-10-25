"""Experimentos automáticos para mejorar el criterio de la app (mantenimiento.bat, opción 6).

Prueba variantes del «juez de siempre» (cuánto vale dormir al rival, subir stats, la vida que queda, Viento Afín…)
y se queda sólo con las que mejoran el acuerdo con jugadores de 1500+ en LOS DOS bancos de prueba:
  1) cada variante se mide con el banco con hojas abiertas, jugada a jugada frente a la versión actual;
  2) las que mejoran se confirman con el banco de ladder (partidas distintas);
  3) si hay varias, se prueba también su combinación;
  3b) y además no puede empeorar la prueba con tu revisión de la app (más errores falsos o menos aciertos);
  4) lo que pasa las pruebas se guarda en engine/parametros.json (la app lo usa al arrancar), el juez
     aprendido vuelve a aprender con la nueva valoración y se hace una calibración final para «Mi progreso».
Tarda varias horas: déjalo de noche. No hace falta internet si ya tienes las repeticiones de los bancos.

    python -m vgclab.experimentos                 # todo
    python -m vgclab.experimentos --solo dormir,vida
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .rutas import ROOT

OUT = ROOT / "logs" / "calibracion" / "experimentos"
PARAMS = ROOT / "engine" / "parametros.json"

# nombre, qué prueba, cambios sobre los parámetros actuales
VARIANTES: List[Tuple[str, str, Dict[str, float]]] = [
    ("dormir", "Dormir (o congelar) al rival vale más", {"slp": 0.45, "frz": 0.45}),
    ("dormir_suave", "Dormir al rival vale algo más", {"slp": 0.55, "frz": 0.5}),
    ("subir_stats", "Subir stats vale más (Calm Mind, Swords Dance…)", {"off": 0.11, "spe": 0.08, "def": 0.045}),
    ("subir_stats_suave", "Subir stats vale algo más", {"off": 0.09, "spe": 0.065}),
    ("vida", "La vida que queda pesa más (premia retirar al dañado)", {"hpBase": 0.35}),
    ("vida_suave", "La vida que queda pesa algo más", {"hpBase": 0.40}),
    ("viento_afin", "Viento Afín vale más", {"tw": 0.30}),
    ("paralisis", "Paralizar al rival vale más", {"par": 0.75}),
    ("pantallas", "Las pantallas valen más", {"screen": 0.14}),
]
MIN_GANA_HOJA = 5      # decisiones netas ganadas (≈1 %) para pasar a la segunda prueba
MIN_GANA_LADDER = 3


def run(name: str, params: Optional[Dict[str, float]], ladder: bool) -> Optional[Dict[str, Any]]:
    env = dict(os.environ)
    env.pop("ROOBOT_PARAMS", None)
    if params:
        env["ROOBOT_PARAMS"] = json.dumps(params)
    cmd = [sys.executable, "-m", "vgclab.calibrar", "--experimento", name] + (["--banco-ladder"] if ladder else [])
    t0 = time.time()
    print(f"\n=== {name} · banco {'de ladder' if ladder else 'con hojas'} · {time.strftime('%H:%M')} ===", flush=True)
    r = subprocess.run(cmd, cwd=str(ROOT), env=env)
    f = OUT / f"{name}_{'ladder' if ladder else 'hoja'}.json"
    if r.returncode != 0 or not f.exists():
        print(f"  (falló {name})")
        return None
    d = json.loads(f.read_text(encoding="utf-8"))
    print(f"  acuerdo {d['resumen']['acuerdo']} % · {(time.time() - t0) / 60:.0f} min", flush=True)
    return d


def run_revision(name: str, params: Optional[Dict[str, float]]) -> Optional[Dict[str, Any]]:
    """La prueba con tu revisión de la app (logs/revision.csv) con estos parámetros."""
    if not (ROOT / "logs" / "revision.csv").exists():
        return None
    env = dict(os.environ)
    env.pop("ROOBOT_PARAMS", None)
    if params:
        env["ROOBOT_PARAMS"] = json.dumps(params)
    print(f"\n=== {name} · prueba con tu revisión · {time.strftime('%H:%M')} ===", flush=True)
    r = subprocess.run([sys.executable, "-m", "vgclab.calibrar", "--prueba-revision", "--experimento", name],
                       cwd=str(ROOT), env=env)
    f = OUT / f"{name}_revision.json"
    if r.returncode != 0 or not f.exists():
        return None
    return json.loads(f.read_text(encoding="utf-8"))


def paired(base: Dict[str, Any], var: Dict[str, Any]) -> Dict[str, Any]:
    key = lambda x: (x["replay"], x["side"], x["turn"])
    b = {key(x): x["tone"] == "good" for x in base["decisiones"]}
    v = {key(x): x["tone"] == "good" for x in var["decisiones"]}
    common = [k for k in b if k in v]
    gana = sum(v[k] and not b[k] for k in common)
    pierde = sum(b[k] and not v[k] for k in common)
    return {"gana": gana, "pierde": pierde, "neto": gana - pierde, "n": len(common),
            "acuerdo": var["resumen"]["acuerdo"], "acuerdo_base": base["resumen"]["acuerdo"]}


def main() -> None:
    ap = argparse.ArgumentParser(description="Experimentos automáticos con el criterio de la app")
    ap.add_argument("--solo", default="", help="probar sólo estas variantes (separadas por comas)")
    ap.add_argument("--no-reaprender", action="store_true", help="no volver a enseñar al juez al final")
    a = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M")
    variants = [v for v in VARIANTES if not a.solo or v[0] in a.solo.split(",")]
    current = {}
    try:
        j = json.loads(PARAMS.read_text(encoding="utf-8"))
        if j.get("activo"):
            current = j.get("p", {})
    except Exception:
        pass
    print(f"Experimentos: {len(variants)} variantes. Parámetros actuales: {current or 'los de siempre'}")

    base_h = run("base", None, False)
    if not base_h:
        raise SystemExit("No se pudo calibrar la versión actual")
    report: List[Dict[str, Any]] = []
    candidates = []
    for name, desc, p in variants:
        d = run(name, p, False)
        if not d:
            report.append({"nombre": name, "desc": desc, "params": p, "hoja": None})
            continue
        r = paired(base_h, d)
        report.append({"nombre": name, "desc": desc, "params": p, "hoja": r})
        print(f"  frente a la actual: +{r['gana']} / −{r['pierde']} (neto {r['neto']:+d})")
        if r["neto"] >= MIN_GANA_HOJA:
            candidates.append((name, desc, p, r))

    accepted = []
    base_l = None
    base_rv = None
    if candidates:
        base_l = run("base", None, True)
    for name, desc, p, rh in candidates:
        if not base_l:
            break
        d = run(name, p, True)
        if not d:
            continue
        rl = paired(base_l, d)
        for it in report:
            if it["nombre"] == name:
                it["ladder"] = rl
        print(f"  {name} en ladder: +{rl['gana']} / −{rl['pierde']} (neto {rl['neto']:+d})")
        if rl["neto"] >= MIN_GANA_LADDER:
            if base_rv is None:
                base_rv = run_revision("base", None) or {}
            rv = run_revision(name, p) if base_rv else None
            if rv and base_rv:
                for it in report:
                    if it["nombre"] == name:
                        it["revision"] = {"falsos": rv["falsos"], "aciertos": rv["aciertos"],
                                          "falsos_base": base_rv["falsos"], "aciertos_base": base_rv["aciertos"]}
                if rv["falsos"] > base_rv["falsos"] or rv["aciertos"] < base_rv["aciertos"]:
                    print(f"  {name} empeora la prueba con tu revisión (errores falsos {base_rv['falsos']}→{rv['falsos']}, "
                          f"aciertos {base_rv['aciertos']}→{rv['aciertos']}): se descarta")
                    continue
            accepted.append((name, desc, p, rh, rl))

    final: Optional[Dict[str, float]] = None
    elegido = None
    if accepted:
        accepted.sort(key=lambda x: -(x[3]["neto"] + x[4]["neto"]))
        best = accepted[0]
        final, elegido = dict(best[2]), [best[0]]
        if len(accepted) > 1:
            combo: Dict[str, float] = {}
            for x in reversed(accepted):          # el mejor manda si hay claves repetidas
                combo.update(x[2])
            ch, cl = run("combinacion", combo, False), run("combinacion", combo, True)
            if ch and cl:
                rch, rcl = paired(base_h, ch), paired(base_l, cl)
                report.append({"nombre": "combinacion", "desc": " + ".join(x[0] for x in accepted), "params": combo,
                               "hoja": rch, "ladder": rcl})
                if rch["neto"] + rcl["neto"] > best[3]["neto"] + best[4]["neto"] and rch["neto"] > 0 and rcl["neto"] > 0:
                    final, elegido = combo, [x[0] for x in accepted]

    result = {"fecha": time.strftime("%Y-%m-%d %H:%M"), "base_hoja": base_h["resumen"]["acuerdo"],
              "base_ladder": base_l["resumen"]["acuerdo"] if base_l else None, "variantes": report,
              "elegido": elegido, "params_nuevos": final}
    if final:
        newp = dict(current)
        newp.update(final)
        hist = []
        try:
            hist = json.loads(PARAMS.read_text(encoding="utf-8")).get("historial", [])
        except Exception:
            pass
        hist.append({"fecha": result["fecha"], "cambios": final, "por": elegido})
        PARAMS.write_text(json.dumps({"activo": True, "fecha": result["fecha"], "p": newp, "historial": hist},
                                     ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"\n✔ Guardado en engine/parametros.json: {final} ({', '.join(elegido)})")
        if not a.no_reaprender:
            print("\nEl juez aprendido vuelve a aprender con la nueva valoración…", flush=True)
            subprocess.run([sys.executable, "-m", "vgclab.calibrar", "--aprender", "--solo-guardadas"], cwd=str(ROOT))
            print("\nCalibración final (queda en «Mi progreso»)…", flush=True)
            subprocess.run([sys.executable, "-m", "vgclab.calibrar"], cwd=str(ROOT))
            subprocess.run([sys.executable, "-m", "vgclab.calibrar", "--prueba-revision"], cwd=str(ROOT))
    else:
        print("\n✖ Ninguna variante mejora en los dos bancos: se queda todo como estaba.")
    (OUT / f"informe_{stamp}.json").write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    (OUT / "ultimo.json").write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    def cell(r):
        return "–" if not r else f"{r['acuerdo']} % ({r['neto']:+d})"
    chosen = set(elegido or []) | ({"combinacion"} if elegido and len(elegido) > 1 else set())
    rows = "".join(
        f"<tr><td><b>{it['nombre']}</b><br><small>{it['desc']}</small></td><td>{cell(it.get('hoja'))}</td>"
        f"<td>{cell(it.get('ladder'))}</td><td>{'✔' if it['nombre'] in chosen else ''}</td></tr>"
        for it in report)
    html = f"""<!doctype html><meta charset="utf-8"><title>Experimentos RooBot</title>
<style>body{{font:14px/1.5 system-ui;margin:24px;max-width:1000px;background:#10162a;color:#eef1fa}}td,th{{padding:6px 8px;border-bottom:1px solid #2a3558;text-align:left}}
table{{border-collapse:collapse;width:100%}}small{{color:#9aa6c8}}</style>
<h1>Experimentos · {result['fecha']}</h1>
<p>Versión actual: <b>{result['base_hoja']} %</b> de acuerdo con el banco con hojas{f" · {result['base_ladder']} % con el de ladder" if result['base_ladder'] is not None else ''}.
Entre paréntesis, decisiones netas ganadas frente a la versión actual (jugada a jugada).</p>
<table><tr><th>Variante</th><th>Banco con hojas</th><th>Banco de ladder</th><th>Elegida</th></tr>{rows}</table>
<p>{('✔ Se queda: <b>' + ', '.join(elegido) + '</b> → ' + json.dumps(final)) if final else '✖ Ninguna mejora en los dos bancos: todo sigue igual.'}</p>"""
    (OUT / f"informe_{stamp}.html").write_text(html, encoding="utf-8")
    print(f"Informe: {OUT / f'informe_{stamp}.html'}")


if __name__ == "__main__":
    main()
