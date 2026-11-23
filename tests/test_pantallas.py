"""Pruebas del modo doble pantalla: el buzón que comunica la ventana de arriba con la de abajo."""
import json

import pytest

from vgclab.pantallas import Buzon, servir_eventos


def _eventos(sus, n, timeout=0.2):
    out = []
    for _ in range(n):
        ev = sus.siguiente(timeout)
        if ev is None:
            break
        out.append(ev)
    return out


def _de_tipo(eventos, tipo):
    return [e for e in eventos if e.get("tipo") == tipo]


def test_arriba_recibe_el_ultimo_campo_al_conectarse():
    b = Buzon()
    b.publicar_campo({"html": "<a>1</a>", "visible": True, "t": 1})
    b.publicar_campo({"html": "<a>2</a>", "visible": True, "t": 2})
    campos = _de_tipo(_eventos(b.suscribir("arriba"), 3), "campo")
    assert len(campos) == 1
    assert campos[0]["html"] == "<a>2</a>" and campos[0]["v"] == 2


def test_campos_llegan_en_orden():
    b = Buzon()
    s = b.suscribir("arriba")
    for i in range(3):
        b.publicar_campo({"html": f"<p>{i}</p>", "visible": True, "t": i})
    assert [e["v"] for e in _de_tipo(_eventos(s, 6), "campo")] == [1, 2, 3]


def test_toques_solo_a_abajo():
    b = Buzon()
    arriba, abajo = b.suscribir("arriba"), b.suscribir("abajo")
    b.publicar_toque({"ruta": [0, 2]})
    assert _de_tipo(_eventos(abajo, 5), "toque") == [{"tipo": "toque", "ruta": [0, 2]}]
    assert _de_tipo(_eventos(arriba, 5, timeout=0.05), "toque") == []


def test_aviso_de_conexion_de_abajo():
    b = Buzon()
    arriba = b.suscribir("arriba")
    assert _de_tipo(_eventos(arriba, 2), "abajo") == [{"tipo": "abajo", "conectada": False}]
    abajo = b.suscribir("abajo")
    assert _de_tipo(_eventos(arriba, 2), "abajo") == [{"tipo": "abajo", "conectada": True}]
    abajo.cerrar()
    assert _de_tipo(_eventos(arriba, 2), "abajo") == [{"tipo": "abajo", "conectada": False}]
    assert b.conectados("abajo") == 0


def test_abajo_sabe_si_hay_ventana_arriba():
    b = Buzon()
    abajo = b.suscribir("abajo")
    assert _de_tipo(_eventos(abajo, 2), "arriba") == [{"tipo": "arriba", "conectada": False}]
    b.suscribir("arriba")
    assert _de_tipo(_eventos(abajo, 2), "arriba") == [{"tipo": "arriba", "conectada": True}]


def test_rol_desconocido():
    with pytest.raises(ValueError):
        Buzon().suscribir("lado")


def test_servir_eventos_escribe_sse_y_cierra():
    b = Buzon()
    b.publicar_campo({"html": "<b>x</b>", "visible": True, "t": 0})
    escrito = []

    def escribir(datos):
        if len(escrito) == 2:
            raise BrokenPipeError
        escrito.append(datos)

    servir_eventos(b, "arriba", escribir, lambda: True, latido=0.05)
    assert escrito[0].startswith(b"data: {")
    assert json.loads(escrito[0][len(b"data: "):].decode("utf-8"))["tipo"] in ("campo", "abajo")
    assert b.conectados("arriba") == 0
