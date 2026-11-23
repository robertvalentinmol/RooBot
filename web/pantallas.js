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
