"""Pruebas del banco limpio: qué partidas elige, que no se rehace y que queda fuera del aprendizaje."""
import json

import pytest

from vgclab import calibrar as c
from vgclab import revision as rv


def _log(p1, p2, hojas=True):
    return f"|player|p1|{p1}|a|\n|player|p2|{p2}|b|\n" + ("|showteam|p1|x\n|showteam|p2|y\n" if hojas else "")


@pytest.fixture
def mundo(tmp_path, monkeypatch):
    rep = tmp_path / "logs" / "calibracion" / "replays"
    rep.mkdir(parents=True)
    cal = rep.parent
    monkeypatch.setattr(c, "ROOT", tmp_path)
    monkeypatch.setattr(c, "REPLAY_DIR", rep)
    monkeypatch.setattr(c, "BANCO", cal / "banco.json")
    monkeypatch.setattr(c, "BANCO_HOJA", cal / "banco_hoja.json")
    monkeypatch.setattr(c, "BANCO_EVAL", cal / "banco_eval.json")
    monkeypatch.setattr(rv, "LIMPIO", cal / "banco_limpio.json")
    idx = {}

    def add(rid, rating, p1="A", p2="B", hojas=True):
        (rep / f"{rid}.log").write_text(_log(p1, p2, hojas), encoding="utf-8")
        idx[rid] = rating
        (rep / "indice.json").write_text(json.dumps(idx), encoding="utf-8")
    return add, cal


def test_elige_solo_validas(mundo):
    add, cal = mundo
    for i in range(30):
        add(f"ok{i}", 1500 + i, f"j{i}", f"k{i}")
    add("bajo", 1499)
    add("sinhoja", 1700, hojas=False)
    add("enbanco", 1700)
    (cal / "banco_hoja.json").write_text(json.dumps({"ids": ["enbanco"]}), encoding="utf-8")
    b = c.make_limpio(n=25)
    assert len(b["ids"]) == 25 and all(i.startswith("ok") for i in b["ids"])
    assert set(b["examen"]) <= set(b["ids"]) and 0 < len(b["examen"]) < 25


def test_no_se_rehace(mundo):
    add, cal = mundo
    for i in range(30):
        add(f"ok{i}", 1600, f"j{i}", f"k{i}")
    a = c.make_limpio(n=25)
    add("nueva", 1900, "x", "y")
    assert c.make_limpio(n=25)["ids"] == a["ids"]


def test_menos_de_n_usa_las_que_hay(mundo):
    add, _ = mundo
    for i in range(22):
        add(f"ok{i}", 1600, f"j{i}", f"k{i}")
    assert len(c.make_limpio(n=100)["ids"]) == 22


def test_menos_de_20_no_escribe(mundo):
    add, cal = mundo
    for i in range(19):
        add(f"ok{i}", 1600, f"j{i}", f"k{i}")
    with pytest.raises(SystemExit):
        c.make_limpio()
    assert not (cal / "banco_limpio.json").exists()


def test_misma_pareja_misma_mitad(mundo):
    add, _ = mundo
    for i in range(20):
        add(f"s{i}", 1600, "Ana", "Luis")       # una «serie» larga
    b = c.make_limpio(n=20)
    assert len(b["examen"]) in (0, 20)


def test_fuera_del_aprendizaje(mundo):
    add, _ = mundo
    for i in range(20):
        add(f"ok{i}", 1600, f"j{i}", f"k{i}")
    b = c.make_limpio(n=20)
    assert set(b["ids"]) <= set(c.all_bank_ids())


def test_prueba_revision_por_grupos(mundo):
    _, cal = mundo
    (cal / "banco_limpio.json").write_text(json.dumps({"ids": ["a", "b"], "examen": ["a"]}), encoding="utf-8")
    res = [{"replay": "a", "lado": "p1", "turno": "3", "veredicto": "jugador", "tone": "bad"},
           {"replay": "b", "lado": "p1", "turno": "3", "veredicto": "jugador", "tone": "ok"},
           {"replay": "viejo", "lado": "p2", "turno": "1", "veredicto": "app", "tone": "bad"}]
    out = c._resultado_prueba(res)
    assert [x["grupo"] for x in out["casos"]] == ["examen", "ajuste", "octubre"]
    assert out["grupos"]["examen"]["falsos"] == 1
    assert (out["falsos"], out["n_jugador"], out["aciertos"], out["n_app"]) == (0, 1, 1, 1)   # sin el examen
    assert out["fecha"]


def test_medir_el_limpio_no_toca_el_historial_del_banco_con_hojas(tmp_path):
    (tmp_path / "ultima.json").write_text('{"fecha": "antes", "acuerdo": 66.6, "banco": "hoja", "historial": []}', encoding="utf-8")
    antes = (tmp_path / "ultima.json").read_bytes()
    hist = c._guardar_ultima(tmp_path, {"fecha": "f1", "acuerdo": 70.0, "banco": "limpio"}, "limpio")
    assert hist == [] and (tmp_path / "ultima.json").read_bytes() == antes
    hist = c._guardar_ultima(tmp_path, {"fecha": "f2", "acuerdo": 71.0, "banco": "limpio"}, "limpio")
    assert [h["fecha"] for h in hist] == ["f1"]
    assert json.loads((tmp_path / "ultima_limpio.json").read_text(encoding="utf-8"))["fecha"] == "f2"


def test_medir_el_banco_con_hojas_sigue_guardando_en_ultima(tmp_path):
    (tmp_path / "ultima.json").write_text('{"fecha": "antes", "acuerdo": 66.6, "banco": "hoja", "historial": []}', encoding="utf-8")
    hist = c._guardar_ultima(tmp_path, {"fecha": "f1", "acuerdo": 67.0, "banco": "hoja"}, "hoja")
    assert [h["fecha"] for h in hist] == ["antes"]
    assert json.loads((tmp_path / "ultima.json").read_text(encoding="utf-8"))["fecha"] == "f1"
    assert not (tmp_path / "ultima_limpio.json").exists()


@pytest.mark.parametrize("otra", ["--aprender", "--nuevas", "--banco-ladder", "--carpeta=x", "--experimento=x"])
def test_banco_limpio_no_se_combina(monkeypatch, capsys, otra):
    monkeypatch.setattr("sys.argv", ["calibrar", "--banco-limpio", otra])
    with pytest.raises(SystemExit) as e:
        c.main()
    assert e.value.code == 2 and "no se puede combinar" in capsys.readouterr().err


def test_rehacer_otro_banco_no_coge_partidas_del_limpio(mundo):
    add, _ = mundo
    for i in range(30):
        add(f"ok{i}", 1600 + i, f"j{i}", f"k{i}")
    limpio = set(c.make_limpio(n=20)["ids"])
    hojas = c.make_bank(30, 1500, 0, hoja=True, download=False)
    assert hojas and not (set(hojas) & limpio)
