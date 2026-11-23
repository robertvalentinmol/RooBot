/* RooBot · modo doble pantalla (AYN Thor y similares).
 *   /abajo  → la app de siempre, con los controles en pestañas; publica una copia del campo.
 *   /arriba → sólo el campo, a pantalla completa; devuelve los toques a la ventana de abajo.
 *   /       → el modo de siempre (más la opción «Doble pantalla» en el menú «Más»).
 * Las dos ventanas se comunican a través del servidor local (/api/pantallas/…).
 */
'use strict';

/* ---------------------------------------------------------------- piezas puras (probadas con node --test) */

/** Papel de esta ventana según su dirección y lo que se recordó la última vez. */
function rolDe(pathname, recordado) {
  if (pathname === '/arriba') return 'arriba';
  if (pathname === '/abajo') return 'abajo';
  if ((pathname === '/' || pathname === '/index.html') && recordado === 'doble') return 'ir-arriba';
  return 'normal';
}

/** Primer data-* del elemento como «nombre=valor» (o ''): sirve para comprobar que la ruta sigue apuntando a él. */
function claveDe(el) {
  const ds = el.dataset || {};
  const k = Object.keys(ds)[0];
  return k === undefined ? '' : `${k}=${ds[k]}`;
}

/** Ruta de un elemento dentro de `raiz`: índices entre los hijos elemento, más su id y su primer data-*. */
function rutaDe(el, raiz) {
  const ruta = [];
  let n = el;
  while (n && n !== raiz) {
    const padre = n.parentElement;
    if (!padre) return null;
    ruta.unshift(Array.prototype.indexOf.call(padre.children, n));
    n = padre;
  }
  if (n !== raiz) return null;
  return { ruta, id: el.id || '', clave: claveDe(el) };
}

/** El elemento al que apunta una ruta, o null si ya no existe o no es el mismo (otro id u otro data-*). */
function resolverRuta(raiz, { ruta, id, clave }) {
  let n = raiz;
  for (const i of ruta || []) {
    n = n.children[i];
    if (!n) return null;
  }
  if ((n.id || '') !== (id || '') || claveDe(n) !== (clave || '')) return null;
  return n;
}

const htmlDe = n => (n.nodeType === 1 ? n.outerHTML : n.textContent);

/** Copia los atributos de `nuevo` en `viejo` (añade, cambia y quita) sin sustituir el nodo. */
function igualarAtributos(viejo, nuevo) {
  const quedan = new Set();
  for (const { name, value } of Array.from(nuevo.attributes)) {
    quedan.add(name);
    if (viejo.getAttribute(name) !== value) viejo.setAttribute(name, value);
  }
  for (const { name } of Array.from(viejo.attributes)) if (!quedan.has(name)) viejo.removeAttribute(name);
}

/** Deja en `viejo` el contenido de `nuevo` tocando sólo lo que cambió: los nodos iguales se quedan (y con
 *  ellos sus animaciones); un elemento con la misma etiqueta se parchea; uno distinto se sustituye. */
function igualar(viejo, nuevo) {
  igualarAtributos(viejo, nuevo);
  const v = Array.from(viejo.childNodes), w = Array.from(nuevo.childNodes);
  for (let i = 0; i < Math.max(v.length, w.length); i++) {
    const a = v[i], b = w[i];
    if (!b) { a.remove(); continue; }
    if (!a) { viejo.appendChild(b); continue; }
    if (htmlDe(a) === htmlDe(b)) continue;
    if (a.nodeType === 1 && b.nodeType === 1 && a.tagName === b.tagName) igualar(a, b);
    else if (a.nodeType === 3 && b.nodeType === 3) a.textContent = b.textContent;
    else a.replaceWith(b);
  }
}

/** Aplica en `destino` el HTML de una copia del campo (su outerHTML); `crear(html)` construye el elemento nuevo. */
function morph(destino, html, crear) {
  igualar(destino, crear(html));
}

if (typeof module !== 'undefined') module.exports = { rolDe, rutaDe, resolverRuta, morph };

/* ---------------------------------------------------------------- en el navegador */

function enviar(url, datos) {
  return fetch(url, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(datos)})
    .catch(() => {});
}

/* Ventana de abajo: la app de siempre con los controles en pestañas; publica el campo y recibe los toques. */
function iniciarAbajo() {
  const raiz = document.documentElement, $ = s => document.querySelector(s);
  const campo = $('#fieldCard');

  // pestañas
  const PESTANAS = [['jugada', 'Jugada'], ['porque', 'Por qué'], ['registro', 'Registro'], ['hojas', 'Hojas'], ['menu', 'Menú']];
  const barra = document.createElement('nav');
  barra.id = 'pantTabs';
  barra.innerHTML = PESTANAS.map(([k, t]) => `<button type="button" data-pest="${k}">${t}</button>`).join('');
  document.body.appendChild(barra);
  const abrir = k => {
    raiz.dataset.pest = k;
    for (const b of barra.children) b.classList.toggle('on', b.dataset.pest === k);
    if (k === 'registro' && window.showPane) window.showPane('log');
    if (k === 'hojas' && window.showPane) window.showPane('sheet');
  };
  barra.addEventListener('click', e => { const b = e.target.closest('button'); if (b) abrir(b.dataset.pest); });
  abrir('jugada');
  // los equipos (en el campo, que aquí no se ve) pasan a la pestaña «Hojas»
  const equipos = $('#teambar'), panel = $('#sidepanel');
  if (equipos && panel) panel.prepend(equipos);
  // el veredicto abre «Por qué»; al empezar otra posición se vuelve a «Jugada»; el menú también vuelve
  const tarjeta = $('#resultCard');
  let conVeredicto = false;
  const mirarVeredicto = () => {
    // (el CSS de abajo oculta #resultCard fuera de «Por qué»: se mira lo que decide la app, su style.display)
    const ahora = !!tarjeta && tarjeta.style.display !== 'none' && tarjeta.textContent.trim() !== '';
    if (ahora && !conVeredicto) abrir('porque');
    if (!ahora && conVeredicto && raiz.dataset.pest === 'porque') abrir('jugada');
    conVeredicto = ahora;
  };
  if (tarjeta) new MutationObserver(mirarVeredicto).observe(tarjeta, {attributes: true, childList: true, subtree: true});
  const rail = $('#rail');
  if (rail) rail.addEventListener('click', e => { if (e.target.closest('.nav')) setTimeout(() => abrir('jugada'), 0); });
  const nuevo = $('#newBtn');
  if (nuevo) nuevo.addEventListener('click', () => abrir('jugada'));

  // copia del campo para la ventana de arriba: como mucho una por fotograma y sólo si cambió
  let ultimo = '', pendiente = false;
  const publicar = () => {
    pendiente = false;
    const visible = campo.style.display !== 'none';
    const html = campo.outerHTML;
    const firma = (visible ? '1' : '0') + html;
    if (firma === ultimo) return;
    ultimo = firma;
    enviar('/api/pantallas/campo', {html, visible, t: Date.now()});
  };
  const programar = () => {
    if (pendiente) return;
    pendiente = true;
    if (document.hidden) setTimeout(publicar, 50); else requestAnimationFrame(publicar);
  };
  new MutationObserver(programar).observe(campo, {subtree: true, childList: true, attributes: true, characterData: true});
  programar();

  // toques que llegan de arriba (y, al reconectar tras reiniciar el servidor, el campo otra vez)
  const es = new EventSource('/api/pantallas/eventos?rol=abajo');
  es.onopen = () => { ultimo = ''; programar(); };
  es.onmessage = e => {
    let ev;
    try { ev = JSON.parse(e.data); } catch (err) { return; }
    if (ev.tipo === 'toque') {
      const el = resolverRuta(campo, ev);
      if (el) el.click();
    }
  };
}

if (typeof window !== 'undefined' && typeof document !== 'undefined') {
  (function () {
    let recordado = null;
    try { recordado = localStorage.getItem('roobot.pantallas'); } catch (e) { /* sin almacenamiento */ }
    const rol = rolDe(location.pathname, recordado);
    if (rol === 'ir-arriba') { location.replace('/arriba'); return; }
    if (rol === 'abajo') iniciarAbajo();
  })();
}
