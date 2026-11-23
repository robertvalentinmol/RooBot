"""Pruebas de la regulación activa: qué repeticiones cuentan, nombres de archivos, cambio de regulación y equipos."""
import json
import types

import pytest

from vgclab import regulacion as R
from vgclab import app as A

CFG = {
    "activa": "mc",
    "regulaciones": {
        "mc": {"nombre": "Reg M-C", "formato": "gen9championsvgc2026regmc",
               "formato_bo3": "gen9championsvgc2026regmcbo3", "equipos": "teams/mc", "showdown": "d601443",
               "sufijo_datos": ""},
        "md": {"nombre": "Reg M-D", "formato": "gen9championsvgc2026regmd",
               "formato_bo3": "gen9championsvgc2026regmdbo3", "equipos": "teams/md", "sufijo_datos": "_md"},
    },
}


@pytest.fixture(autouse=True)
def sin_entorno(monkeypatch):
    monkeypatch.delenv("VGCLAB_REG", raising=False)
    monkeypatch.delenv("VGCLAB_FORMAT", raising=False)


def test_mc_es_la_de_siempre():
    reg = R.activa(json.loads(json.dumps(R._POR_DEFECTO)))
    assert reg.formato == "gen9championsvgc2026regmc"
    assert reg.formatos == ["gen9championsvgc2026regmc", "gen9championsvgc2026regmcbo3"]
    assert reg.equipos == "teams/mc"
    assert reg.archivo(R.ROOT / "logs" / "banco.json").name == "banco.json"     # M-C conserva los nombres


def test_repeticiones_por_formato():
    mc, md = R._de(CFG, "mc"), R._de(CFG, "md")
    assert mc.es_suya("gen9championsvgc2026regmc-2702105959")
    assert mc.es_suya("gen9championsvgc2026regmcbo3-2702105959")
    assert mc.es_suya("smogtours-gen9championsvgc2026regmc-123")
    assert not mc.es_suya("gen9championsvgc2026regmd-1")
    assert md.es_suya("gen9championsvgc2026regmdbo3-1-abcdpw")
    assert not md.es_suya("gen9championsvgc2026regmc-1")
    assert mc.es_suya("partida_suelta") and md.es_suya("ok3")      # sin formato: no se puede saber, cuenta


def test_datos_con_sufijo():
    md = R._de(CFG, "md")
    assert md.archivo(R.ROOT / "logs" / "banco_hoja.json").name == "banco_hoja_md.json"


def test_entorno_manda(monkeypatch):
    monkeypatch.setenv("VGCLAB_REG", "md")
    assert R.activa(CFG).id == "md"
    monkeypatch.setenv("VGCLAB_FORMAT", "gen9championsvgc2026regmb")
    reg = R.activa(CFG)
    assert reg.formato == "gen9championsvgc2026regmb" and reg.formato_bo3 == "gen9championsvgc2026regmbbo3"


def test_nueva_y_volver(tmp_path, monkeypatch):
    monkeypatch.setattr(R, "ROOT", tmp_path)
    monkeypatch.setattr(R, "ARCHIVO", tmp_path / "regulacion.json")
    (tmp_path / "engine").mkdir()
    (tmp_path / "engine" / "juez.json").write_text('{"v": "mc"}', encoding="utf-8")
    R.ARCHIVO.write_text(json.dumps(R._POR_DEFECTO), encoding="utf-8")
    R.main(["--nueva", "md", "--nombre", "Reg M-D", "--formato", "gen9championsvgc2026regmd", "--sin-comprobar"])
    cfg = json.loads(R.ARCHIVO.read_text(encoding="utf-8"))
    assert cfg["activa"] == "md" and cfg["regulaciones"]["md"]["formato_bo3"] == "gen9championsvgc2026regmdbo3"
    assert (tmp_path / "teams" / "md").is_dir()
    assert (tmp_path / "logs" / "regulaciones" / "mc" / "juez.json").exists()     # el juez de M-C queda guardado
    with pytest.raises(SystemExit):
        R.main(["--nueva", "md", "--formato", "gen9championsvgc2026regmd", "--sin-comprobar"])   # ya existe
    R.main(["--usar", "mc", "--sin-comprobar"])
    assert json.loads(R.ARCHIVO.read_text(encoding="utf-8"))["activa"] == "mc"


def test_equipos_de_otra_regulacion_no_se_mezclan(tmp_path, monkeypatch):
    for rel in ("teams/mio.txt", "teams/mc/viejo.txt", "teams/md/nuevo.txt"):
        f = tmp_path / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("Pikachu", encoding="utf-8")
    monkeypatch.setattr(A, "ROOT", tmp_path)
    monkeypatch.setattr(A, "carpetas_de_otras", lambda: [tmp_path / "teams" / "mc"])
    rutas = [t["path"] for t in A.App.team_files(types.SimpleNamespace(rival_dirs=["teams/md"]))]
    assert sorted(rutas) == ["teams/md/nuevo.txt", "teams/mio.txt"]
    # si pides a mano los equipos de M-C como rivales, sí aparecen
    rutas = [t["path"] for t in A.App.team_files(types.SimpleNamespace(rival_dirs=["teams/mc"]))]
    assert "teams/mc/viejo.txt" in rutas
