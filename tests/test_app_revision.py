"""Pruebas de «Revisar la app» en la app: de qué banco salen los casos y qué estadísticas enseña (sin motor)."""
import csv
import json

import pytest

from vgclab import revision as rv
from vgclab.app import App

CAMPOS = ["fecha", "replay", "lado", "turno", "rating", "jugador", "app", "perdida", "categorias", "veredicto", "nota"]


def _dec(replay, turn, tone="bad", loss=0.5):
    return {"replay": replay, "side": "p1", "turn": turn, "tone": tone, "loss": loss}


@pytest.fixture
def app(tmp_path, monkeypatch):
    a = App.__new__(App)                       # sin arrancar el motor: sólo se prueban métodos que leen archivos
    a.logs = tmp_path
    (tmp_path / "calibracion").mkdir()
    monkeypatch.setattr(rv, "LIMPIO", tmp_path / "calibracion" / "banco_limpio.json")
    return a


def _calibracion(app, stamp, banco, dec):
    d = app.logs / "calibracion"
    (d / f"calibracion_{stamp}.json").write_text(json.dumps({"fecha": stamp, "banco": banco, "acuerdo": 66.0, "aceptables": 27.0}), encoding="utf-8")
    (d / f"calibracion_{stamp}_detalle.json").write_text(json.dumps(dec), encoding="utf-8")


def _banco(app, ids, examen):
    rv.LIMPIO.write_text(json.dumps({"ids": ids, "examen": examen}), encoding="utf-8")


def _revisar(app, replay, turno, veredicto="jugador"):
    f = app.logs / "revision.csv"
    nuevo = not f.exists()
    with open(f, "a", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=CAMPOS)
        if nuevo:
            w.writeheader()
        w.writerow({"replay": replay, "lado": "p1", "turno": turno, "veredicto": veredicto})


def test_sin_banco_limpio_la_cola_es_la_del_banco_con_hojas(app):
    _calibracion(app, "20261031_1649", "hoja", [_dec("h1", 1, loss=0.3), _dec("h2", 2, loss=0.9)])
    pend, summ, banco = app._revision_pendientes()
    assert banco == "hoja" and [x["replay"] for x in pend] == ["h2", "h1"]      # por gravedad, como siempre
    assert summ["fecha"] == "20261031_1649"


def test_con_banco_limpio_los_casos_salen_de_ahi(app):
    _calibracion(app, "20261031_1649", "hoja", [_dec("h1", 1)])
    _calibracion(app, "20261116_2100", "limpio", [_dec("l1", 1), _dec("l2", 2), _dec("l3", 3, tone="ok")])
    pend, summ, banco = app._revision_pendientes()
    assert banco == "limpio" and sorted(x["replay"] for x in pend) == ["l1", "l2"]
    assert summ["fecha"] == "20261116_2100"


def test_una_medida_posterior_del_banco_con_hojas_no_tapa_el_limpio(app):
    _calibracion(app, "20261116_2100", "limpio", [_dec("l1", 1)])
    _calibracion(app, "20261125_2100", "hoja", [_dec("h1", 1)])
    assert app._revision_pendientes()[2] == "limpio"


def test_agotado_el_limpio_sigue_con_el_de_hojas(app):
    _calibracion(app, "20261031_1649", "hoja", [_dec("h1", 1)])
    _calibracion(app, "20261116_2100", "limpio", [_dec("l1", 1)])
    _revisar(app, "l1", 1)
    pend, _, banco = app._revision_pendientes()
    assert banco == "hoja" and [x["replay"] for x in pend] == ["h1"]


def test_nada_pendiente_en_ningun_banco(app):
    _calibracion(app, "20261031_1649", "hoja", [_dec("h1", 1)])
    _revisar(app, "h1", 1)
    pend, _, _ = app._revision_pendientes()
    assert pend == []


def test_estadisticas_del_banco_limpio(app):
    _banco(app, ["l1", "l2", "l3"], ["l1"])
    _calibracion(app, "20261116_2100", "limpio", [_dec("l1", 1), _dec("l2", 2), _dec("l3", 3), _dec("l3", 4)])
    _revisar(app, "l1", 1)
    _revisar(app, "l2", 2, "app")
    _revisar(app, "viejo", 9)
    st = app.revision_stats()
    assert st["limpio"] == {"revisadas": {"examen": 1, "ajuste": 1}, "pendientes": 2}
    assert st["total"] == 3


def test_estadisticas_sin_medir_el_banco_limpio(app):
    _revisar(app, "viejo", 9)
    assert app.revision_stats()["limpio"] == {"revisadas": {"examen": 0, "ajuste": 0}, "pendientes": None}


def test_prueba_antigua_sin_grupos_se_sigue_leyendo(app):
    (app.logs / "calibracion" / "prueba_revision.json").write_text(
        json.dumps({"fecha": "2026-10-31 16:51", "n": 55, "falsos": 18, "n_jugador": 52, "aciertos": 2, "n_app": 3,
                    "historial": []}), encoding="utf-8")
    _revisar(app, "viejo", 9)
    pr = app.revision_stats()["prueba"]
    assert pr["falsos"] == 18 and pr.get("grupos") is None


def test_prueba_nueva_trae_los_grupos(app):
    g = {"n": 1, "falsos": 1, "n_jugador": 1, "aciertos": 0, "n_app": 0}
    (app.logs / "calibracion" / "prueba_revision.json").write_text(
        json.dumps({"fecha": "f", "n": 1, "falsos": 0, "n_jugador": 0, "aciertos": 0, "n_app": 0,
                    "grupos": {"examen": g}, "historial": []}), encoding="utf-8")
    _revisar(app, "viejo", 9)
    assert app.revision_stats()["prueba"]["grupos"]["examen"] == g


def test_medida_del_banco_limpio_para_mi_progreso(app):
    assert app._calibracion_limpio() is None
    (app.logs / "calibracion" / "ultima_limpio.json").write_text(
        json.dumps({"fecha": "f", "replays": 100, "decisiones": 1500, "acuerdo": 67.0, "aceptables": 27.0,
                    "juez": "aprendido", "discrepancias": [1, 2]}), encoding="utf-8")
    assert app._calibracion_limpio() == {"fecha": "f", "replays": 100, "decisiones": 1500, "acuerdo": 67.0,
                                         "aceptables": 27.0, "juez": "aprendido"}


def test_lo_que_revisas_cuenta_al_momento_sin_repetir_la_prueba(app):
    _banco(app, ["l1", "l2"], ["l1"])
    (app.logs / "calibracion" / "prueba_revision.json").write_text(
        json.dumps({"fecha": "2026-10-31 16:51", "n": 1, "falsos": 0, "n_jugador": 1, "aciertos": 0, "n_app": 0, "historial": [],
                    "casos": [{"replay": "viejo", "lado": "p1", "turno": "9", "veredicto": "jugador", "tone": "ok"}]}),
        encoding="utf-8")
    _revisar(app, "viejo", 9)
    _revisar(app, "l1", 1)               # examen: le doy la razón al jugador → error falso
    _revisar(app, "l2", 2, "app")        # ajuste: le doy la razón a la app → acierto
    g = app.revision_stats()["prueba"]["grupos"]
    assert g["examen"] == {"n": 1, "falsos": 1, "n_jugador": 1, "aciertos": 0, "n_app": 0}
    assert g["ajuste"] == {"n": 1, "falsos": 0, "n_jugador": 0, "aciertos": 1, "n_app": 1}
    assert g["octubre"] == {"n": 1, "falsos": 0, "n_jugador": 1, "aciertos": 0, "n_app": 0}


def test_sin_prueba_previa_lo_revisado_tambien_cuenta(app):
    _banco(app, ["l1"], ["l1"])
    _revisar(app, "l1", 1)
    pr = app.revision_stats()["prueba"]
    assert pr["grupos"]["examen"] == {"n": 1, "falsos": 1, "n_jugador": 1, "aciertos": 0, "n_app": 0}
    assert pr["historial"] == []


class _Alto(Exception):
    pass


class _MotorFalso:
    """Sólo apunta la semilla de los «mundos posibles» que le piden y corta ahí (el análisis de verdad necesita el motor)."""
    def __init__(self):
        self.semillas = []
        self.main = self

    def call(self, cmd, **kw):
        assert cmd == "realistic_worlds"
        self.semillas.append(kw["seed"])
        raise _Alto()


def test_al_revisar_se_analiza_con_los_mismos_mundos_que_al_medir(app, monkeypatch):
    import threading
    monkeypatch.delenv("ROOBOT_SEMILLA_FIJA", raising=False)
    app.lock, app.pool = threading.Lock(), _MotorFalso()
    parsed, d = {"sheets": {"theirs": []}}, {"state": {"turno": 3, "campo": ["a", "b"]}}
    for _ in range(3):
        with pytest.raises(_Alto):
            app._analyze_turn(parsed, d, False, fija=True)
    monkeypatch.setenv("ROOBOT_SEMILLA_FIJA", "1")           # así mide calibrar.py
    with pytest.raises(_Alto):
        app._analyze_turn(parsed, d, False)
    assert len(set(app.pool.semillas)) == 1
