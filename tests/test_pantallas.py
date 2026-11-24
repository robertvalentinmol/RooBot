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


# ------------------------------------------------------------------ rutas HTTP
import threading
import types
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

from vgclab.app import make_handler


@pytest.fixture
def servidor():
    app = types.SimpleNamespace(buzon=Buzon())
    srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(app))
    srv.daemon_threads = True
    hilo = threading.Thread(target=srv.serve_forever, daemon=True)
    hilo.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}", app
    srv.shutdown()
    srv.server_close()


def _post(url, datos):
    req = urllib.request.Request(url, data=json.dumps(datos).encode(), method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.loads(r.read())


def _primer_evento(url, tipo, timeout=5):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        assert r.headers["Content-Type"].startswith("text/event-stream")
        for linea in r:
            if linea.startswith(b"data:"):
                ev = json.loads(linea[5:])
                if ev.get("tipo") == tipo:
                    return ev


def test_arriba_y_abajo_sirven_la_app(servidor):
    base, _ = servidor
    for ruta in ("/arriba", "/abajo"):
        with urllib.request.urlopen(base + ruta, timeout=5) as r:
            assert r.status == 200 and b"<title>RooBot</title>" in r.read()


def test_eventos_sse_entregan_el_ultimo_campo(servidor):
    base, _ = servidor
    assert _post(base + "/api/pantallas/campo", {"html": "<b>x</b>", "visible": True, "t": 0}) == {"v": 1}
    ev = _primer_evento(base + "/api/pantallas/eventos?rol=arriba", "campo")
    assert ev["html"] == "<b>x</b>" and ev["visible"] is True


def test_toque_llega_a_abajo(servidor):
    base, app = servidor
    recibido = {}
    hilo = threading.Thread(target=lambda: recibido.update(
        ev=_primer_evento(base + "/api/pantallas/eventos?rol=abajo", "toque")), daemon=True)
    hilo.start()
    for _ in range(50):                      # esperar a que la ventana de abajo esté conectada
        if app.buzon.conectados("abajo"):
            break
        threading.Event().wait(0.05)
    assert _post(base + "/api/pantallas/toque", {"ruta": [0, 1]}) == {"ok": True}
    hilo.join(5)
    assert recibido["ev"]["ruta"] == [0, 1]


def test_rol_invalido_400(servidor):
    base, _ = servidor
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(base + "/api/pantallas/eventos?rol=x", timeout=5)
    assert e.value.code == 400


def test_pantallas_js_y_css(servidor):
    base, _ = servidor
    for ruta, tipo in (("/pantallas.js", "text/javascript"), ("/pantallas.css", "text/css")):
        with urllib.request.urlopen(base + ruta, timeout=5) as r:
            assert r.headers["Content-Type"].startswith(tipo)


def test_arriba_sabe_en_pocos_segundos_que_abajo_se_cerro(servidor):
    """El navegador de abajo se cierra sin avisar: arriba debe pasar a la espera en ≤ 5 s."""
    import time
    base, app = servidor
    r = urllib.request.urlopen(base + "/api/pantallas/eventos?rol=abajo", timeout=5)
    r.readline()                                              # conectada
    eventos = []
    hilo = threading.Thread(target=lambda: eventos.extend(
        json.loads(l[5:]) for l in urllib.request.urlopen(base + "/api/pantallas/eventos?rol=arriba", timeout=10)
        if l.startswith(b"data:") and json.loads(l[5:]).get("tipo") == "abajo" and not json.loads(l[5:])["conectada"]
        and not eventos), daemon=True)
    hilo.start()
    time.sleep(0.5)
    r.close()
    t0 = time.time()
    while not eventos and time.time() - t0 < 6:
        time.sleep(0.1)
    assert eventos, "arriba no se enteró de que abajo se cerró"
    assert time.time() - t0 <= 5


# ------------------------------------------------------------------ revisión final
def test_toques_solo_a_la_ultima_ventana_de_abajo_que_publico():
    b = Buzon()
    vieja, nueva = b.suscribir("abajo", cid="A"), b.suscribir("abajo", cid="B")
    b.publicar_campo({"html": "<a>", "visible": True, "t": 0}, cid="A")
    b.publicar_campo({"html": "<b>", "visible": True, "t": 0}, cid="B")
    b.publicar_toque({"ruta": [1]})
    assert _de_tipo(_eventos(nueva, 5), "toque") == [{"tipo": "toque", "ruta": [1]}]
    assert _de_tipo(_eventos(vieja, 5, timeout=0.05), "toque") == []


def test_sin_publicador_conocido_el_toque_va_a_todas():
    b = Buzon()
    s1, s2 = b.suscribir("abajo"), b.suscribir("abajo")
    b.publicar_toque({"ruta": [0]})
    assert _de_tipo(_eventos(s1, 5), "toque") and _de_tipo(_eventos(s2, 5), "toque")


def test_http_cid_de_abajo_recibe_el_toque(servidor):
    base, app = servidor
    recibido = {}
    hilo = threading.Thread(target=lambda: recibido.update(
        ev=_primer_evento(base + "/api/pantallas/eventos?rol=abajo&cid=X1", "toque")), daemon=True)
    hilo.start()
    for _ in range(50):
        if app.buzon.conectados("abajo"):
            break
        threading.Event().wait(0.05)
    _post(base + "/api/pantallas/campo", {"html": "<i>", "visible": True, "t": 0, "cid": "X1"})
    _post(base + "/api/pantallas/toque", {"ruta": [2]})
    hilo.join(5)
    assert recibido["ev"]["ruta"] == [2]


def _post_crudo(url, cuerpo, cabeceras):
    req = urllib.request.Request(url, data=cuerpo, method="POST", headers=cabeceras)
    return urllib.request.urlopen(req, timeout=5)


def test_otra_web_no_puede_publicar_ni_tocar(servidor):
    """Una página de otro origen no puede meter HTML en la ventana de arriba ni mandar toques."""
    base, app = servidor
    for ruta in ("/api/pantallas/campo", "/api/pantallas/toque"):
        with pytest.raises(urllib.error.HTTPError) as e:     # origen ajeno
            _post_crudo(base + ruta, b'{"html":"<img src=x onerror=alert(1)>","ruta":[0]}',
                        {"Content-Type": "application/json", "Origin": "https://malo.example"})
        assert e.value.code == 403
        with pytest.raises(urllib.error.HTTPError) as e:     # text/plain: el truco sin preflight
            _post_crudo(base + ruta, b'{"html":"x","ruta":[0]}', {"Content-Type": "text/plain"})
        assert e.value.code == 403
    assert app.buzon.ultimo_campo() is None
    # la propia app (mismo origen) sí puede
    host = base.replace("http://", "")
    r = _post_crudo(base + "/api/pantallas/campo", b'{"html":"<p>","visible":true,"t":0}',
                    {"Content-Type": "application/json", "Origin": f"http://{host}"})
    assert json.loads(r.read())["v"] == 1
