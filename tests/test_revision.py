"""Pruebas de la lógica pura de «Revisar la app»: grupos examen / ajuste / octubre y cola de pendientes."""
from vgclab import revision as rv

BANCO = {"ids": ["a", "b", "c"], "examen": ["a"]}


def test_load_limpio_sin_archivo(tmp_path):
    assert rv.load_limpio(tmp_path / "no.json") == {"ids": [], "examen": []}


def test_load_limpio_corrupto(tmp_path):
    f = tmp_path / "b.json"
    f.write_text("{no es json", encoding="utf-8")
    assert rv.load_limpio(f) == {"ids": [], "examen": []}


def test_mitad_no_depende_del_orden_ni_de_mayusculas():
    assert rv.mitad("Ana", "luis ") == rv.mitad("LUIS", "ana")
    assert rv.mitad("Ana", "Luis") in ("examen", "ajuste")


def test_grupo():
    assert [rv.grupo(x, BANCO) for x in ("a", "b", "z")] == ["examen", "ajuste", "octubre"]
    assert rv.grupo("a", {"ids": [], "examen": []}) == "octubre"


def test_pendientes_filtra_y_ordena():
    dec = [{"replay": "r", "side": "p1", "turn": 1, "tone": "bad", "loss": 0.5},
           {"replay": "r", "side": "p1", "turn": 2, "tone": "bad", "loss": 0.9},
           {"replay": "r", "side": "p1", "turn": 3, "tone": "bad", "loss": 0.1},
           {"replay": "r", "side": "p1", "turn": 4, "tone": "ok", "loss": 0.9},
           {"replay": "r", "side": "p2", "turn": 5, "tone": "bad", "loss": 0.7}]
    p = rv.pendientes(dec, {"r|p2|5"}, 0.20, azar=False)
    assert [x["turn"] for x in p] == [2, 1]
    q = rv.pendientes(dec, set(), 0.20, azar=True)
    assert sorted(x["turn"] for x in q) == [1, 2, 5]
    assert q == rv.pendientes(list(reversed(dec)), set(), 0.20, azar=True)   # orden fijo


def test_resumen_prueba_separa_el_examen():
    res = [{"replay": "a", "veredicto": "jugador", "tone": "bad"},     # examen, falso
           {"replay": "b", "veredicto": "ambas", "tone": "ok"},        # ajuste
           {"replay": "b", "veredicto": "app", "tone": "bad"},         # ajuste, acierto
           {"replay": "z", "veredicto": "jugador", "tone": "bad"},     # octubre, falso
           {"replay": "z", "veredicto": "jugador", "tone": "skip"}]    # no cuenta
    out = rv.resumen_prueba(res, BANCO)
    assert out["grupos"]["examen"] == {"n": 1, "falsos": 1, "n_jugador": 1, "aciertos": 0, "n_app": 0}
    assert out["grupos"]["ajuste"] == {"n": 2, "falsos": 0, "n_jugador": 1, "aciertos": 1, "n_app": 1}
    assert out["grupos"]["octubre"] == {"n": 1, "falsos": 1, "n_jugador": 1, "aciertos": 0, "n_app": 0}
    assert {k: out[k] for k in ("n", "falsos", "n_jugador", "aciertos", "n_app")} == \
           {"n": 3, "falsos": 1, "n_jugador": 2, "aciertos": 1, "n_app": 1}


def test_resumen_prueba_sin_banco_es_como_v33():
    res = [{"replay": "a", "veredicto": "jugador", "tone": "bad"}]
    out = rv.resumen_prueba(res, {"ids": [], "examen": []})
    assert out["falsos"] == 1 and out["grupos"]["examen"]["n"] == 0


def test_casos_al_dia_suma_lo_juzgado_despues_de_la_ultima_prueba():
    casos = [{"replay": "z", "lado": "p1", "turno": "3", "veredicto": "jugador", "tone": "ok"}]
    filas = [{"replay": "z", "lado": "p1", "turno": "3", "veredicto": "jugador"},      # ya estaba en la prueba
             {"replay": "a", "lado": "p2", "turno": "7", "veredicto": "nose"},         # no cuenta
             {"replay": "a", "lado": "p2", "turno": "8", "veredicto": "omitida"},      # no cuenta
             {"replay": "a", "lado": "p1", "turno": "5", "veredicto": "app"},
             {"replay": "a", "lado": "p1", "turno": "5", "veredicto": "ambas"}]        # juzgada dos veces: vale la última
    out = rv.casos_al_dia(casos, filas)
    assert out[0] == casos[0] and len(out) == 2
    assert out[1] == {"replay": "a", "lado": "p1", "turno": "5", "veredicto": "ambas", "tone": "bad"}


def test_casos_al_dia_sin_nada_nuevo():
    casos = [{"replay": "z", "lado": "p1", "turno": 3, "veredicto": "jugador", "tone": "bad"}]
    assert rv.casos_al_dia(casos, [{"replay": "z", "lado": "p1", "turno": "3", "veredicto": "jugador"}]) == casos
