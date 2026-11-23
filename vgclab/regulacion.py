"""Regulación activa de RooBot: formato de Showdown, carpeta de equipos rivales y versión de Showdown probada.

Todo sale de regulacion.json (en la raíz del proyecto). Cambiar de regulación es cambiar ese archivo, o usar
«Nueva regulación» en mantenimiento.bat, que además actualiza Showdown e importa los equipos.

Variables de entorno (mandan sobre el archivo):
  VGCLAB_REG     id de una regulación ya registrada (p. ej. mc)
  VGCLAB_FORMAT  formato de Showdown suelto (compatibilidad con versiones anteriores de RooBot)

Uso:
  python -m vgclab.regulacion                       # regulación activa y las registradas
  python -m vgclab.regulacion --valor formato       # un valor suelto (lo usan los .bat / .sh)
  python -m vgclab.regulacion --listar-formatos     # formatos de Champions que conoce el Showdown instalado
  python -m vgclab.regulacion --usar mc             # volver a una regulación ya registrada
  python -m vgclab.regulacion --nueva md --nombre "Reg M-D" --formato gen9championsvgc2026regmd [--pestana "..."]
  python -m vgclab.regulacion --hoja "<enlace de Google Sheets>" --pestana "Champions M-C Featured Teams"   # sus equipos

Hoja de equipos: cada regulación puede tener una hoja pública de Google (p. ej. el VGCPastes Repository) y el nombre
de su pestaña. «python -m vgclab.importar --hoja» y las opciones 1 y 2 de mantenimiento.bat importan de ahí los
equipos nuevos (los que ya tienes no se repiten). Al crear una regulación se reutiliza la hoja de la anterior.

Los datos que dependen de la regulación (bancos de prueba, última calibración) llevan el sufijo de la regulación
en el nombre del archivo, salvo la primera (M-C), que conserva los nombres de siempre. Las repeticiones guardadas
se comparten en una carpeta, pero cada regulación usa sólo las de sus formatos (el formato va en el id).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Dict, List, Optional

from .rutas import ROOT

ARCHIVO = ROOT / "regulacion.json"
_CON_FORMATO = re.compile(r"(^|-)gen\d+[a-z0-9]+-\d")   # «gen9championsvgc2026regmc-2702105959»

# Si falta regulacion.json (una copia antigua del proyecto), se comporta como siempre: Reg M-C.
_POR_DEFECTO: Dict[str, Any] = {
    "activa": "mc",
    "regulaciones": {
        "mc": {"nombre": "Reg M-C", "formato": "gen9championsvgc2026regmc",
               "formato_bo3": "gen9championsvgc2026regmcbo3", "equipos": "teams/mc",
               "showdown": "d601443", "sufijo_datos": "",
               "hoja_equipos": {"url": "https://docs.google.com/spreadsheets/d/1axlwmzPA49rYkqXh7zHvAtSP-TKbM0ijGYBPRflLSWw/edit",
                                "pestana": "Champions M-C Featured Teams"}},
    },
}


@dataclass(frozen=True)
class Regulacion:
    id: str
    nombre: str
    formato: str
    formato_bo3: Optional[str]
    equipos: str            # carpeta de equipos rivales, relativa a la raíz y con «/»
    showdown: Optional[str]  # commit de Showdown con el que se probó
    sufijo_datos: str
    hoja: Optional[str] = None      # hoja pública de Google con los equipos (enlace)
    pestana: Optional[str] = None   # nombre de la pestaña de esta regulación en esa hoja

    @property
    def formatos(self) -> List[str]:
        """Formatos cuyas repeticiones cuentan para esta regulación (Bo1 y Bo3)."""
        return [f for f in (self.formato, self.formato_bo3) if f]

    @property
    def carpeta_equipos(self) -> Path:
        return ROOT / self.equipos

    def es_suya(self, replay_id: str) -> bool:
        """¿La repetición es de esta regulación? El id de Showdown lleva el formato («<formato>-<número>»).
        Un id sin formato reconocible (archivos sueltos de --carpeta) se acepta: no hay forma de saberlo."""
        rid = str(replay_id)
        if not _CON_FORMATO.search(rid):
            return True
        return any(rid.startswith(f + "-") or f"-{f}-" in rid for f in self.formatos)

    def archivo(self, path: Path) -> Path:
        """El archivo de datos de esta regulación: banco.json → banco_md.json (M-C conserva el nombre)."""
        path = Path(path)
        if not self.sufijo_datos:
            return path
        return path.with_name(f"{path.stem}{self.sufijo_datos}{path.suffix}")


def cargar_config() -> Dict[str, Any]:
    try:
        return json.loads(ARCHIVO.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return json.loads(json.dumps(_POR_DEFECTO))


def _de(cfg: Dict[str, Any], rid: str) -> Regulacion:
    regs = cfg.get("regulaciones", {})
    if rid not in regs:
        raise SystemExit(f"La regulación «{rid}» no está en regulacion.json (hay: {', '.join(regs) or 'ninguna'}).")
    r = regs[rid]
    return Regulacion(id=rid, nombre=r.get("nombre") or rid, formato=r["formato"], formato_bo3=r.get("formato_bo3"),
                      equipos=str(r.get("equipos") or f"teams/{rid}").replace("\\", "/").rstrip("/"),
                      showdown=r.get("showdown"),
                      sufijo_datos=r.get("sufijo_datos", f"_{rid}") or "",
                      hoja=(r.get("hoja_equipos") or {}).get("url") or None,
                      pestana=(r.get("hoja_equipos") or {}).get("pestana") or None)


def activa(cfg: Optional[Dict[str, Any]] = None) -> Regulacion:
    cfg = cfg if cfg is not None else cargar_config()
    reg = _de(cfg, os.environ.get("VGCLAB_REG") or cfg.get("activa") or "mc")
    fmt = os.environ.get("VGCLAB_FORMAT")
    if fmt and fmt != reg.formato:      # formato forzado a mano: manda sobre el de la regulación
        bo3 = fmt if fmt.endswith("bo3") else fmt + "bo3"
        reg = replace(reg, formato=fmt, formato_bo3=bo3)
    return reg


def todas(cfg: Optional[Dict[str, Any]] = None) -> List[Regulacion]:
    cfg = cfg if cfg is not None else cargar_config()
    return [_de(cfg, rid) for rid in cfg.get("regulaciones", {})]


def carpetas_de_otras(reg: Optional[Regulacion] = None) -> List[Path]:
    """Carpetas de equipos de las demás regulaciones (para no mezclar sus equipos con los de la activa)."""
    reg = reg or REG
    return [r.carpeta_equipos for r in todas() if r.carpeta_equipos != reg.carpeta_equipos]


REG = activa()


# ------------------------------------------------------------------ línea de órdenes
def _guardar(cfg: Dict[str, Any]) -> None:
    ARCHIVO.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _node_json(script: str) -> Any:
    """Ejecuta un trozo de JS con el Showdown de la app (engine/sim.js) y devuelve su JSON."""
    node = os.environ.get("VGCLAB_NODE") or shutil.which("node")
    if not node:
        raise SystemExit("No encuentro 'node'. Instala Node.js 20+ (ver README).")
    pre = f"const {{PS}} = require({json.dumps(str(ROOT / 'engine' / 'sim.js'))});"
    out = subprocess.run([node, "-e", pre + script], capture_output=True, text=True, encoding="utf-8")
    if out.returncode != 0:
        raise SystemExit("Showdown no responde:\n" + out.stderr[-800:])
    return json.loads(out.stdout.strip().splitlines()[-1])


def formatos_champions() -> List[str]:
    return _node_json("console.log(JSON.stringify(PS.Dex.formats.all()"
                      ".filter(f => f.effectType === 'Format' && /champions/.test(f.id) && /vgc/.test(f.id))"
                      ".map(f => f.id)))")


def existe_formato(fmt: str) -> bool:
    return bool(_node_json(f"console.log(JSON.stringify(PS.Dex.formats.get({json.dumps(fmt)}).exists))"))


def commit_showdown() -> Optional[str]:
    sd = ROOT / "server" / "pokemon-showdown"
    try:
        out = subprocess.run(["git", "-C", str(sd), "rev-parse", "--short", "HEAD"], capture_output=True, text=True)
        return out.stdout.strip() or None
    except Exception:
        return None


def _archivar_modelos(reg: Regulacion) -> Path:
    """Copia el juez y la experiencia actuales a logs/regulaciones/<id>/ antes de cambiar de regulación."""
    dest = ROOT / "logs" / "regulaciones" / reg.id
    dest.mkdir(parents=True, exist_ok=True)
    for nombre in ("juez.json", "experiencia.json"):
        src = ROOT / "engine" / nombre
        if src.exists():
            shutil.copy2(src, dest / nombre)
    return dest


def pestana_por_defecto(nombre: str) -> str:
    """«Reg M-D» → «Champions M-D Featured Teams»: en el VGCPastes Repository, la pestaña de la regulación con sólo
    los equipos que tienen resultados en torneos (la pestaña «Champions M-D» a secas trae todos)."""
    base = nombre.replace("Reg", "", 1).strip() if nombre.startswith("Reg") else nombre
    return f"Champions {base} Featured Teams"


def poner_hoja(cfg: Dict[str, Any], url: Optional[str], pestana: Optional[str]) -> Regulacion:
    reg = activa(cfg)
    h = cfg["regulaciones"][reg.id].setdefault("hoja_equipos", {})
    if url:
        if "docs.google.com/spreadsheets" not in url:
            raise SystemExit("El enlace debe ser de una hoja de Google (docs.google.com/spreadsheets/...).")
        h["url"] = url.split("#")[0]
    if pestana:
        h["pestana"] = pestana
    if not h.get("pestana"):
        h["pestana"] = pestana_por_defecto(reg.nombre)
    _guardar(cfg)
    reg = activa(cfg)
    print(f"Equipos de {reg.nombre}: pestaña «{reg.pestana}» de {reg.hoja or '(sin hoja)'}")
    return reg


def nueva(cfg: Dict[str, Any], rid: str, nombre: str, formato: str, formato_bo3: Optional[str],
          showdown: Optional[str], comprobar: bool = True, pestana: Optional[str] = None) -> Regulacion:
    rid = rid.strip().lower()
    if not rid.isalnum():
        raise SystemExit("El id de la regulación debe ser corto y sin espacios ni símbolos (p. ej. md).")
    if rid in cfg.get("regulaciones", {}):
        raise SystemExit(f"La regulación «{rid}» ya existe. Para volver a ella: python -m vgclab.regulacion --usar {rid}")
    if comprobar:
        if not existe_formato(formato):
            disponibles = ", ".join(formatos_champions()) or "ninguno"
            raise SystemExit(f"El Showdown instalado no conoce el formato «{formato}».\n"
                             f"Formatos de Champions que sí conoce: {disponibles}\n"
                             "Si la regulación es muy reciente, actualiza Showdown y vuelve a probar.")
        if formato_bo3 and not existe_formato(formato_bo3):
            print(f"Aviso: no existe el formato Bo3 «{formato_bo3}»; sólo se usarán repeticiones de {formato}.")
            formato_bo3 = None
    anterior = activa(cfg)
    archivados = _archivar_modelos(anterior)
    cfg.setdefault("regulaciones", {})[rid] = {
        "nombre": nombre, "formato": formato, "formato_bo3": formato_bo3, "equipos": f"teams/{rid}",
        "showdown": showdown, "sufijo_datos": f"_{rid}", "creada": time.strftime("%Y-%m-%d"),
    }
    if anterior.hoja:       # la misma hoja (el VGCPastes Repository tiene una pestaña por regulación)
        cfg["regulaciones"][rid]["hoja_equipos"] = {"url": anterior.hoja,
                                                    "pestana": pestana or pestana_por_defecto(nombre)}
    cfg["activa"] = rid
    _guardar(cfg)
    reg = _de(cfg, rid)
    reg.carpeta_equipos.mkdir(parents=True, exist_ok=True)
    print(f"Regulación activa: {reg.nombre} ({reg.formato}).")
    print(f"  Equipos rivales en {reg.equipos}/ · juez y experiencia de {anterior.nombre} guardados en "
          f"{archivados.relative_to(ROOT)}")
    if reg.hoja:
        print(f"  Sus equipos saldrán de la pestaña «{reg.pestana}» de la hoja de la regulación anterior.")
    return reg


def usar(cfg: Dict[str, Any], rid: str, comprobar: bool = True) -> Regulacion:
    reg = _de(cfg, rid)
    if comprobar and not existe_formato(reg.formato):
        print(f"Aviso: el Showdown instalado ya no conoce {reg.formato}. Para usar {reg.nombre} instala su versión "
              f"de Showdown ({reg.showdown or 'desconocida'}) con instalar.bat.")
    cfg["activa"] = rid
    _guardar(cfg)
    print(f"Regulación activa: {reg.nombre} ({reg.formato}).")
    return reg


def main(argv: Optional[List[str]] = None) -> None:
    ap = argparse.ArgumentParser(description="Regulación activa de RooBot")
    ap.add_argument("--valor", choices=["id", "nombre", "formato", "formato_bo3", "equipos", "showdown", "hoja", "pestana"])
    ap.add_argument("--tiene-hoja", action="store_true", help="sale con código 0 si la regulación activa tiene hoja de equipos")
    ap.add_argument("--hoja", metavar="ENLACE", help="hoja de Google con los equipos de la regulación activa")
    ap.add_argument("--pestana", help="nombre de la pestaña (con --hoja o --nueva)")
    ap.add_argument("--listar-formatos", action="store_true")
    ap.add_argument("--usar", metavar="ID")
    ap.add_argument("--nueva", metavar="ID")
    ap.add_argument("--nombre")
    ap.add_argument("--formato")
    ap.add_argument("--formato-bo3")
    ap.add_argument("--showdown", help="commit de Showdown probado (por defecto, el instalado)")
    ap.add_argument("--sin-comprobar", action="store_true", help="no consultar a Showdown (sólo para pruebas)")
    a = ap.parse_args(argv)
    cfg = cargar_config()
    if a.valor:
        print(getattr(activa(cfg), a.valor) or "")
        return
    if a.tiene_hoja:
        sys.exit(0 if activa(cfg).hoja else 1)
    if a.hoja or (a.pestana and not a.nueva):
        poner_hoja(cfg, a.hoja, a.pestana)
        return
    if a.listar_formatos:
        for f in formatos_champions():
            print(f)
        return
    if a.usar:
        usar(cfg, a.usar, not a.sin_comprobar)
        return
    if a.nueva:
        if not a.formato:
            raise SystemExit("Falta --formato (el id de Showdown, p. ej. gen9championsvgc2026regmd). "
                             "Mira los disponibles con --listar-formatos.")
        bo3 = a.formato_bo3 or (a.formato if a.formato.endswith("bo3") else a.formato + "bo3")
        nueva(cfg, a.nueva, a.nombre or f"Reg {a.nueva.upper()}", a.formato, bo3,
              a.showdown or (None if a.sin_comprobar else commit_showdown()), not a.sin_comprobar, a.pestana)
        return
    reg = activa(cfg)
    print(f"Regulación activa: {reg.nombre} [{reg.id}]")
    print(f"  Formatos: {', '.join(reg.formatos)}")
    print(f"  Equipos rivales: {reg.equipos}/ · Showdown probado: {reg.showdown or '?'}")
    print(f"  Hoja de equipos: " + (f"pestaña «{reg.pestana}» de {reg.hoja}" if reg.hoja else "ninguna"))
    otras = [r for r in todas(cfg) if r.id != reg.id]
    if otras:
        print("Otras registradas: " + ", ".join(f"{r.nombre} [{r.id}]" for r in otras))


if __name__ == "__main__":
    main()
