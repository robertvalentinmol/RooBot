# RooBot en las dos pantallas de la AYN Thor — plan de implementación

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Modo doble pantalla: `/arriba` muestra el campo de combate a pantalla completa y `/abajo` tiene los controles, sincronizadas a través del servidor local; `/` sigue igual.

**Architecture:** La ventana de abajo ejecuta la app de siempre y publica una copia de `#fieldCard` en un buzón del servidor; la de arriba la recibe por SSE, la aplica con un «morph» y devuelve sus toques por el mismo buzón. Todo lo nuevo vive en archivos propios (`vgclab/pantallas.py`, `web/pantallas.js`, `web/pantallas.css`); `index.html` y `app.py` solo los enlazan.

**Tech Stack:** Python 3.10 (stdlib, `ThreadingHTTPServer`), JavaScript sin dependencias en el navegador, pruebas con pytest y `node --test` (incluido en Node 20+).

**Spec:** `docs/superpowers/specs/2026-11-24-dos-pantallas-design.md`

## Global Constraints

- Sin dependencias nuevas: ni en `requirements.txt` ni paquetes npm.
- `/` (una sola ventana, PC incluido) se comporta exactamente como hoy; lo único nuevo visible es «Doble pantalla» en el menú «Más».
- Retraso abajo → arriba de como mucho ~150 ms.
- Sin parpadeos ni animaciones que se repitan en el campo de arriba por cambios pequeños.
- Textos de la interfaz y comentarios en español.
- Direcciones: `/arriba`, `/abajo`; API bajo `/api/pantallas/`.
- Mensaje de espera arriba, literal: «Abre RooBot en la pantalla de abajo (Chrome → 127.0.0.1:8765/abajo)».

## Review Focus

- Abajo se abre o se recarga después de arriba → arriba muestra enseguida el campo actual (el buzón entrega la última copia al conectarse). Prueba en Task 1 y Task 2.
- Abajo se cierra a mitad de sesión → arriba pasa a la pantalla de espera en ≤ 5 s, no se queda con un campo viejo como si siguiera vivo. Prueba en Task 1 (aviso de conexión) y Task 5 (manual).
- Un toque de arriba llega cuando el campo de abajo ya cambió → se descarta, nunca se pulsa otro elemento. Prueba en Task 3.
- El servidor se reinicia (`roobot-parar` / `roobot-actualizar`) con las dos ventanas abiertas → las dos reconectan solas y abajo vuelve a publicar el campo al reconectar. Requisito en Task 4 y Task 5; comprobación manual en Task 6.
- En `/` no cambia nada: `pantallas.js` no observa ni publica, y solo redirige a `/arriba` si la doble pantalla quedó recordada. Prueba en Task 3 (`rolDe`).

---

### Task 1: Buzón de pantallas (servidor, sin HTTP)

**Files:**
- Create: `vgclab/pantallas.py`
- Test: `tests/test_pantallas.py`

**Interfaces:**
- Produces:
  - `class Buzon` (seguro entre hilos):
    - `publicar_campo(datos: dict) -> int` — guarda la copia (`{"html": str, "visible": bool, "t": float}`), le pone `"v"` creciente, la reparte a las suscripciones `arriba`; devuelve `v`.
    - `ultimo_campo() -> dict | None`
    - `publicar_toque(toque: dict) -> None` — lo reparte a las suscripciones `abajo`.
    - `suscribir(rol: str) -> Suscripcion` — `rol` ∈ {`"arriba"`, `"abajo"`}, si no `ValueError`. Al suscribir `arriba` le encola enseguida `{"tipo": "campo", **ultimo}` si hay copia, y `{"tipo": "abajo", "conectada": bool}`. Cada alta o baja de una suscripción `abajo` manda `{"tipo": "abajo", "conectada": n_abajo > 0}` a las `arriba`; cada alta o baja `arriba` manda `{"tipo": "arriba", "conectada": n_arriba > 0}` a las `abajo` (y a la nueva `abajo` al suscribirse).
    - `conectados(rol: str) -> int`
  - `class Suscripcion`: `siguiente(timeout: float) -> dict | None` (cola; `None` si vence) y `cerrar() -> None` (se da de baja y avisa).
  - `servir_eventos(buzon: Buzon, rol: str, escribir: Callable[[bytes], None], seguir: Callable[[], bool], latido: float = 15.0) -> None` — bucle SSE: por cada evento escribe `b"data: " + json + b"\n\n"`; si vence `latido` sin eventos escribe `b": latido\n\n"`; sale cuando `seguir()` es falso o `escribir` lanza `OSError`/`BrokenPipeError`; siempre cierra la suscripción.

- [ ] **Step 1: Write the failing tests** — `tests/test_pantallas.py`:
  - `test_arriba_recibe_el_ultimo_campo_al_conectarse`: `publicar_campo({"html": "<a>", "visible": True, "t": 1})` dos veces con html distinto; `suscribir("arriba").siguiente(0.1)` → `tipo == "campo"`, `html` el segundo, `v == 2`.
  - `test_campos_llegan_en_orden`: suscribir arriba, publicar 3 campos → los eventos `campo` llegan con `v` 1, 2, 3.
  - `test_toques_solo_a_abajo`: con una `arriba` y una `abajo`, `publicar_toque({"ruta": [0, 2]})` → la de abajo lo recibe (`tipo == "toque"`), la de arriba no (`siguiente(0.05)` entre los avisos de conexión no da ningún `toque`).
  - `test_aviso_de_conexion_de_abajo`: suscribir arriba (recibe `{"tipo": "abajo", "conectada": False}`), suscribir abajo → arriba recibe `conectada: True`; `cerrar()` la de abajo → arriba recibe `conectada: False`; `conectados("abajo") == 0`.
  - `test_rol_desconocido`: `pytest.raises(ValueError)` con `suscribir("lado")`.
  - `test_servir_eventos_escribe_sse_y_cierra`: `escribir` que acumula en una lista y lanza `BrokenPipeError` a la tercera escritura; publicar un campo antes; `servir_eventos(..., latido=0.05)` termina, la primera escritura empieza por `b"data: {"`, y después `conectados("arriba") == 0`.

- [ ] **Step 2: Run the tests to verify they fail**
  Run: `python -m pytest tests/test_pantallas.py -q`
  Expected: FAIL (`ModuleNotFoundError: vgclab.pantallas`)

- [ ] **Step 3: Implement `vgclab/pantallas.py`** — una `queue.Queue` por suscripción y un `threading.Lock` para las listas; JSON con `ensure_ascii=False` codificado en UTF-8.

- [ ] **Step 4: Run the tests to verify they pass**
  Run: `python -m pytest tests/test_pantallas.py -q` → todo PASS; y `python -m pytest tests -q` sin fallos nuevos.

- [ ] **Step 5: Commit** — `git add vgclab/pantallas.py tests/test_pantallas.py && git commit -m "Pantallas: buzón entre la ventana de arriba y la de abajo"`

### Task 2: Rutas del servidor

**Files:**
- Modify: `vgclab/app.py` (`App.__init__`: atributo `self.buzon`; `make_handler`: `do_GET` y `do_POST`)
- Test: `tests/test_pantallas.py`

**Interfaces:**
- Consumes: `Buzon`, `servir_eventos` (Task 1).
- Produces (HTTP):
  - `GET /arriba`, `GET /abajo` → el mismo `index.html` que `/` (con `__REGULACION__` sustituido).
  - `GET /pantallas.js` (`text/javascript; charset=utf-8`), `GET /pantallas.css` (`text/css; charset=utf-8`) desde `web/`.
  - `GET /api/pantallas/eventos?rol=arriba|abajo` → `200`, `Content-Type: text/event-stream`, `Cache-Control: no-store`, cuerpo de `servir_eventos(app.buzon, rol, …)`; rol inválido → `400`.
  - `POST /api/pantallas/campo` (cuerpo `{"html", "visible", "t"}`) → `{"v": int}`.
  - `POST /api/pantallas/toque` (cuerpo `{"ruta": [int, …], "id"?: str, "clave"?: str}`) → `{"ok": true}`.
  - `make_handler(app)` sólo necesita `app.buzon` para estas rutas (las pruebas pasan un `SimpleNamespace(buzon=Buzon())`).

- [ ] **Step 1: Write the failing tests** — en `tests/test_pantallas.py`, un fixture que arranca `ThreadingHTTPServer(("127.0.0.1", 0), make_handler(SimpleNamespace(buzon=Buzon())))` en un hilo y lo cierra al final:
  - `test_arriba_y_abajo_sirven_la_app`: `GET /arriba` y `GET /abajo` → 200 y el cuerpo contiene `<title>RooBot</title>`.
  - `test_eventos_sse_entregan_el_ultimo_campo`: `POST /api/pantallas/campo` con `{"html": "<b>x</b>", "visible": true, "t": 0}` → `{"v": 1}`; abrir `GET /api/pantallas/eventos?rol=arriba` con `urllib` y leer líneas hasta una que empiece por `data:` con `"tipo": "campo"` y `"html": "<b>x</b>"`.
  - `test_toque_llega_a_abajo`: abrir el flujo `rol=abajo` en un hilo, `POST /api/pantallas/toque` con `{"ruta": [0, 1]}` → el flujo de abajo entrega un `data:` con `"tipo": "toque"` y `"ruta": [0, 1]`.
  - `test_rol_invalido_400`: `GET /api/pantallas/eventos?rol=x` → `HTTPError` 400.

- [ ] **Step 2: Run the tests to verify they fail**
  Run: `python -m pytest tests/test_pantallas.py -q` → FAIL (404 en las rutas nuevas).

- [ ] **Step 3: Implement the routes in `vgclab/app.py`** — en `App.__init__`: `self.buzon = Buzon()`. En `do_GET`, `"/"`, `"/index.html"`, `"/arriba"` y `"/abajo"` comparten la rama del índice. La ruta SSE escribe cabeceras, hace `flush` tras cada evento y usa `seguir=lambda: True` (sale por `BrokenPipeError` cuando el navegador cierra). Las dos rutas POST van antes del `else` final de `do_POST`.

- [ ] **Step 4: Run the tests to verify they pass**
  Run: `python -m pytest tests -q` → todo PASS.

- [ ] **Step 5: Commit** — `git add vgclab/app.py tests/test_pantallas.py && git commit -m "Pantallas: rutas /arriba, /abajo y API de sincronización"`

### Task 3: Piezas puras de `pantallas.js` (rol, ruta, morph)

**Files:**
- Create: `web/pantallas.js` (sólo las funciones puras y su `module.exports`; la parte del navegador llega en Task 4 y 5)
- Test: `tests/js/pantallas.test.js`

**Interfaces:**
- Produces (en `web/pantallas.js`, exportadas con `if (typeof module !== 'undefined') module.exports = {…}`):
  - `rolDe(pathname: string, recordado: string | null) -> 'arriba' | 'abajo' | 'normal' | 'ir-arriba'` — `/arriba` → `'arriba'`, `/abajo` → `'abajo'`; `/` o `/index.html` con `recordado === 'doble'` → `'ir-arriba'`; si no, `'normal'`.
  - `rutaDe(el, raiz) -> {ruta: number[], id: string, clave: string} | null` — índices entre los hijos **elemento** (`children`) desde `raiz` hasta `el`; `id` = `el.id` o `''`; `clave` = el primer `data-*` de `el` como `nombre=valor` o `''`; `null` si `el` no está dentro de `raiz`.
  - `resolverRuta(raiz, {ruta, id, clave}) -> Element | null` — sigue los índices; devuelve `null` si falta un índice o si el elemento encontrado no tiene el mismo `id` y la misma `clave`.
  - `morph(destino: Element, html: string, crear: (html) => Element) -> void` — deja en `destino` el contenido de `html` sustituyendo sólo los hijos cuyo `outerHTML` cambió (comparación hijo a hijo por posición, recursiva si coinciden etiqueta y atributos); `crear` construye el árbol (en el navegador, con `<template>`), para que las pruebas puedan pasar un árbol falso.
- Las pruebas usan un árbol falso mínimo (`children`, `parentElement`, `id`, `dataset`, `tagName`, `attributes`, `outerHTML`, `replaceWith`, `appendChild`, `remove`) construido en el propio test; nada de jsdom.

- [ ] **Step 1: Write the failing tests** — `tests/js/pantallas.test.js` con `node:test` y `node:assert`:
  - `rolDe`: los cuatro casos de arriba y `rolDe('/', null) === 'normal'`, `rolDe('/abajo', 'doble') === 'abajo'`.
  - `rutaDe` + `resolverRuta`: ida y vuelta de un nodo a 3 niveles; con `id='mon-p1a'` y `data-slot="1"` devuelve `clave === 'slot=1'`.
  - `resolverRuta` descarta: mismo índice pero otro `id` → `null`; índice fuera de rango → `null`.
  - `morph`: de `<div><p>a</p><p>b</p></div>` a `<div><p>a</p><p>c</p></div>` sustituye sólo el segundo `<p>` (el primero es el mismo objeto); un hijo de más se añade y uno de menos se quita.

- [ ] **Step 2: Run the tests to verify they fail**
  Run: `node --test tests/js/` → FAIL (`Cannot find module '../../web/pantallas.js'`)

- [ ] **Step 3: Implement the four functions in `web/pantallas.js`.**

- [ ] **Step 4: Run the tests to verify they pass**
  Run: `node --test tests/js/` → PASS; `node --check web/pantallas.js`.

- [ ] **Step 5: Commit** — `git add web/pantallas.js tests/js/pantallas.test.js && git commit -m "Pantallas: rol, rutas de toque y morph (con pruebas)"`

### Task 4: Ventana de abajo

**Files:**
- Modify: `web/pantallas.js` (arranque en el navegador), `web/index.html` (enlaces)
- Create: `web/pantallas.css`

**Interfaces:**
- Consumes: `rolDe`, `resolverRuta` (Task 3); `POST /api/pantallas/campo`, `GET /api/pantallas/eventos?rol=abajo` (Task 2); de `index.html`: `#fieldCard`, `#controlsCard`, `#resultCard`, `aside #sidepanel` con `showPane('log'|'sheet')`, `nav#rail`, `#navMore`, `#topbar`, `#newBtn`.
- Produces:
  - En `index.html`, en `<head>`: un `<script>` en línea que pone en `<html>` la clase `pantalla-arriba` o `pantalla-abajo` según `location.pathname` (antes del primer pintado), `<link rel="stylesheet" href="/pantallas.css">`; y justo antes de `</body>`: `<script src="/pantallas.js"></script>`.
  - El arranque de la app al final del script principal (`init().catch(…)`, ~línea 3097) sólo se ejecuta si `<html>` no tiene `pantalla-arriba`: la ventana de arriba no carga equipos ni pide ejercicios.
  - Con `html.pantalla-abajo`: barra de pestañas `#pantTabs` con botones `data-pest="jugada|porque|registro|hojas|menu"`; la pestaña activa se marca con `html[data-pest="…"]` y el CSS muestra sólo su bloque: `jugada` → la columna `main` tal como la muestra la app (las tarjetas de inicio, progreso, plan, analizar partida… y `#controlsCard`), salvo `#fieldCard` y `#resultCard`; `porque` → `#resultCard` (desviación pequeña del diseño: el veredicto y su «¿Por qué?» están juntos en `#result`, así que van juntos en esta pestaña, que se abre sola al confirmar); `registro` → `#sidepanel` con `showPane('log')`; `hojas` → `#sidepanel` con `showPane('sheet')`; `menu` → `#rail`. Al pasar `#resultCard` a visible se abre `porque`; al volver a ocultarse, `jugada`. Tocar un botón de `#rail` vuelve a `jugada`.
  - `#fieldCard` oculto con CSS (sigue funcionando y midiéndose; no `display:none`, sino fuera de la vista con su tamaño, para que `fitStage()` siga calculando).

- [ ] **Step 1: Enlazar en `index.html`** el script del `<head>`, la hoja de estilos y `pantallas.js`, y condicionar `init()`. Con `/` abierto, comprobar en el navegador del PC que la página se ve idéntica a antes (mismo aspecto, sin errores en la consola).

- [ ] **Step 2: Publicar el campo** — en `pantallas.js`, si el rol es `abajo`: un `MutationObserver` sobre `#fieldCard` (`subtree`, `childList`, `attributes`, `characterData`) que, como mucho una vez por `requestAnimationFrame`, hace `POST /api/pantallas/campo` con `{html: fieldCard.outerHTML, visible: getComputedStyle(fieldCard).display !== 'none', t: Date.now()}` si el html cambió desde el último envío. También publica al cargar y en cada `open` del `EventSource` (reconexión tras reiniciar el servidor).

- [ ] **Step 3: Recibir toques** — `new EventSource('/api/pantallas/eventos?rol=abajo')`; en cada `{"tipo": "toque"}`, `resolverRuta(fieldCard, toque)` y, si da un elemento, `el.click()`.

- [ ] **Step 4: Disposición de abajo en `web/pantallas.css`** (todo bajo `html.pantalla-abajo`): `#topbar` en una línea con título y «Nuevo»; `#pantTabs` fija abajo; `#controlsCard` sin márgenes y con los dos Pokémon en dos columnas; tamaños con `clamp()`/`vh` para que la jugada entera (movimientos, objetivos y confirmar) quepa sin desplazar en una ventana de **470×410 px CSS** y en una de **620×540 px CSS** (los dos extremos probables de la pantalla de abajo).

- [ ] **Step 5: Comprobar en el PC** — `python -m vgclab.app`, abrir `127.0.0.1:8765/abajo` en Chrome con la ventana a 470×410 (DevTools → dimensiones): en Posiciones y en Partida vs bot se elige y confirma sin desplazar; el veredicto abre «Por qué»; «Nuevo» vuelve a «Jugada»; Registro, Hojas y Menú funcionan. En `/` todo como antes.

- [ ] **Step 6: Commit** — `git add web/index.html web/pantallas.js web/pantallas.css && git commit -m "Pantallas: ventana de abajo (controles en pestañas y copia del campo)"`

### Task 5: Ventana de arriba y «Doble pantalla»

**Files:**
- Modify: `web/pantallas.js`, `web/pantallas.css`

**Interfaces:**
- Consumes: `rolDe`, `rutaDe`, `morph` (Task 3); `GET /api/pantallas/eventos?rol=arriba`, `POST /api/pantallas/toque` (Task 2).
- Produces:
  - Con `html.pantalla-arriba`: sólo `#fieldCard` visible, centrado y escalado a toda la ventana conservando la proporción (las reglas de `fitStage()` siguen valiendo porque escalan con el ancho de `#field`; el CSS da a `#field` el ancho que cabe en la altura de la ventana); el resto de la app (`#rail`, `#topbar`, `aside`, demás tarjetas) oculto. La app no arranca aquí (Task 4 deja `init()` fuera en `pantalla-arriba`).
  - `#pantEspera`: capa con el logo, el mensaje literal de Global Constraints y el botón «Usar solo esta pantalla»; visible si no hay ventana abajo conectada (`{"tipo": "abajo", "conectada": false}`) o si el último campo llegó con `visible: false` (entonces el último campo queda detrás atenuado).
  - Toques: un `click` en captura sobre `#fieldCard` hace `preventDefault()` + `stopPropagation()`, calcula `rutaDe(e.target, fieldCard)` y hace `POST /api/pantallas/toque`.
  - `localStorage['roobot.pantallas']`: `'doble'` al entrar en «Doble pantalla»; se borra con «Usar solo esta pantalla». `rolDe(location.pathname, guardado) === 'ir-arriba'` → `location.replace('/arriba')`.
  - Botón `#navDual` («Doble pantalla») añadido al final de `#navMore` por `pantallas.js` sólo en el rol `normal` (no lleva `data-kind`, así que el manejador de `#rail` no lo toca): guarda `'doble'` y va a `/arriba`.

- [ ] **Step 1: Pintar el campo arriba** — `EventSource('/api/pantallas/eventos?rol=arriba')`; en `{"tipo": "campo"}`, `morph(fieldCard, ev.html, crearConTemplate)` y guardar `ev.t`; tras aplicarlo, `console.debug('pantallas: retraso', Date.now() - ev.t, 'ms')`.

- [ ] **Step 2: Espera y desconexión** — mostrar u ocultar `#pantEspera` con los eventos `abajo` y `visible`.

- [ ] **Step 3: Toques** — el manejador en captura y el `POST`.

- [ ] **Step 4: «Doble pantalla», recuerdo y vuelta** — `#navDual`, `localStorage` y la redirección de `ir-arriba`.

- [ ] **Step 5: Comprobar en el PC con dos ventanas** — una a 16:9 en `/arriba` y otra a 470×410 en `/abajo`:
  - arriba ve la posición nada más abrir abajo, y al cerrar abajo pasa a la espera en ≤ 5 s;
  - en Team preview, marcar los 4 tocando arriba los marca abajo, y tocando abajo se ve arriba;
  - el retraso que imprime la consola de arriba no pasa de 150 ms;
  - cambiar de turno o de posición no hace parpadear los Pokémon que no cambian;
  - `/` con `roobot.pantallas = 'doble'` va solo a `/arriba`; «Usar solo esta pantalla» vuelve a `/` y ya no redirige.

- [ ] **Step 6: Commit** — `git add web/pantallas.js web/pantallas.css && git commit -m "Pantallas: ventana de arriba, espera y modo doble pantalla recordado"`

### Task 6: Prueba en la Thor y documentación

**Files:**
- Modify: `docs/MANUAL_ES.md` (sección nueva «Doble pantalla en la AYN Thor»), `README.md` (una línea en «Qué hace»)

- [ ] **Step 1: En la Thor** — `roobot-actualizar`; arriba la app RooBot → Más → Doble pantalla; abajo Chrome en `127.0.0.1:8765/abajo`. Recorrer: Posiciones, Decisiones críticas, Lecturas, Team preview (marcar arriba y abajo), Partida vs bot, Repasar errores y una posición de Analizar partida. Comprobar que la jugada cabe abajo sin desplazar.

- [ ] **Step 2: Reinicio del servidor** — con las dos abiertas, `roobot-parar` y `roobot` en Termux: arriba muestra la espera y, al volver, el campo sin recargar a mano; abajo sigue usable.

- [ ] **Step 3: Manual y README** — cómo abrir cada ventana, qué hay en cada pestaña de abajo y que Mjolnir puede abrir las dos con Home.

- [ ] **Step 4: Commit** — `git add docs/MANUAL_ES.md README.md && git commit -m "Pantallas: manual del modo doble pantalla"`
