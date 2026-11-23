"""Lógica de «Revisar la app» que no necesita el motor: el banco limpio y los grupos de casos revisados.

El banco limpio (logs/calibracion/banco_limpio.json) son partidas con hojas abiertas de 1500+ que nunca se han
usado para aprender ni para diseñar reglas. Se reparte en dos mitades fijas, por pareja de jugadores (las partidas
de una misma serie caen juntas):
  · ajuste: los casos que sí se pueden mirar para diseñar o elegir reglas;
  · examen: sólo se cuentan. Es la cifra que vale, y nunca decide nada.
Las revisiones anteriores al banco limpio forman el grupo «octubre» (orientativo: sirvieron para ajustar la app).
"""
from __future__ import annotations

import json
import zlib
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from .rutas import ROOT
from .regulacion import REG

LIMPIO = REG.archivo(ROOT / "logs" / "calibracion" / "banco_limpio.json")   # uno por regulación
GRUPOS = ("examen", "ajuste", "octubre")
_CUENTAS = ("n", "falsos", "n_jugador", "aciertos", "n_app")


def load_limpio(path: Optional[Path] = None) -> Dict[str, List[str]]:
    """El banco limpio: {"ids": [...], "examen": [...]}. Vacío si aún no existe o no se puede leer."""
    try:
        j = json.loads((path or LIMPIO).read_text(encoding="utf-8"))
        return {"ids": list(j.get("ids") or []), "examen": list(j.get("examen") or [])}
    except Exception:
        return {"ids": [], "examen": []}


def mitad(p1: str, p2: str) -> str:
    """Mitad a la que va una pareja de jugadores (da igual el orden y las mayúsculas)."""
    par = "|".join(sorted([p1.strip().lower(), p2.strip().lower()]))
    return "examen" if zlib.crc32(par.encode("utf-8")) % 2 == 0 else "ajuste"


def grupo(rid: str, banco: Dict[str, List[str]]) -> str:
    if rid in banco.get("examen", ()):
        return "examen"
    if rid in banco.get("ids", ()):
        return "ajuste"
    return "octubre"


def orden(replay: str, lado: str, turno) -> int:
    """Orden al azar pero siempre el mismo: una revisión a medias sigue siendo representativa."""
    return zlib.crc32(f"{replay}|{lado}|{turno}".encode("utf-8"))


def pendientes(dec: List[Dict[str, Any]], done: Set[str], umbral: float, azar: bool) -> List[Dict[str, Any]]:
    """Decisiones que la app marca como error y aún no has juzgado."""
    pend = [x for x in dec if x.get("tone") == "bad" and x.get("loss", 0) >= umbral
            and f"{x['replay']}|{x['side']}|{x['turn']}" not in done]
    if azar:
        pend.sort(key=lambda x: (orden(x["replay"], x["side"], x["turn"]), x["replay"], x["side"], x["turn"]))
    else:
        pend.sort(key=lambda x: -x.get("loss", 0))
    return pend


def casos_al_dia(casos: List[Dict[str, Any]], filas: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Los casos de la última prueba más los que has juzgado después. Estos cuentan como «error» de la app: lo
    eran cuando te los enseñó (si el criterio cambia, la siguiente prueba los vuelve a analizar)."""
    vistos = {(c["replay"], c["lado"], str(c["turno"])) for c in casos}
    ultima: Dict[Any, Dict[str, Any]] = {}
    for r in filas:
        if r.get("veredicto") in ("jugador", "ambas", "app"):
            ultima[(r["replay"], r["lado"], str(r["turno"]))] = r
    nuevos = [{"replay": k[0], "lado": k[1], "turno": k[2], "veredicto": r["veredicto"], "tone": "bad"}
              for k, r in ultima.items() if k not in vistos]
    return list(casos) + nuevos


def resumen_prueba(res: List[Dict[str, Any]], banco: Dict[str, List[str]]) -> Dict[str, Any]:
    """Errores falsos y aciertos de la app, por grupo. Los totales de arriba (los que usan los experimentos para
    descartar variantes) suman ajuste + octubre: el examen no decide nada."""
    grupos = {g: dict.fromkeys(_CUENTAS, 0) for g in GRUPOS}
    for x in res:
        if x.get("tone") == "skip":
            continue
        c = grupos[grupo(x["replay"], banco)]
        c["n"] += 1
        if x["veredicto"] in ("jugador", "ambas"):
            c["n_jugador"] += 1
            c["falsos"] += x["tone"] == "bad"
        elif x["veredicto"] == "app":
            c["n_app"] += 1
            c["aciertos"] += x["tone"] == "bad"
    out: Dict[str, Any] = {k: grupos["ajuste"][k] + grupos["octubre"][k] for k in _CUENTAS}
    out["grupos"] = grupos
    return out
