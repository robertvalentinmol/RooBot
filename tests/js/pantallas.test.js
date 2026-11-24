// Pruebas de las piezas puras de web/pantallas.js (node --test tests/js/pantallas.test.js). Sin jsdom: un árbol falso mínimo.
'use strict';
const test = require('node:test');
const assert = require('node:assert');
const { rolDe, rutaDe, resolverRuta, morph, mensajeEspera, ultimoGana, enCola, ESPERA } = require('../../web/pantallas.js');

class Txt {
  constructor(t) { this.nodeType = 3; this.textContent = t; this.parentNode = null; }
  get outerHTML() { return this.textContent; }
  replaceWith(n) { this.parentNode._swap(this, n); }
  remove() { this.parentNode._quitar(this); }
}
class El {
  constructor(tag, attrs = {}, hijos = []) {
    this.nodeType = 1; this.tagName = tag.toUpperCase(); this._a = { ...attrs };
    this.childNodes = []; this.parentNode = null;
    for (const h of hijos) this.appendChild(typeof h === 'string' ? new Txt(h) : h);
  }
  get parentElement() { return this.parentNode; }
  get children() { return this.childNodes.filter(n => n.nodeType === 1); }
  get attributes() { return Object.entries(this._a).map(([name, value]) => ({ name, value })); }
  get id() { return this._a.id || ''; }
  get dataset() {
    const d = {};
    for (const [k, v] of Object.entries(this._a)) if (k.startsWith('data-')) d[k.slice(5).replace(/-(\w)/g, (_, c) => c.toUpperCase())] = v;
    return d;
  }
  getAttribute(k) { return k in this._a ? this._a[k] : null; }
  setAttribute(k, v) { this._a[k] = String(v); }
  removeAttribute(k) { delete this._a[k]; }
  get outerHTML() {
    const at = Object.entries(this._a).map(([k, v]) => ` ${k}="${v}"`).join('');
    const t = this.tagName.toLowerCase();
    return `<${t}${at}>${this.childNodes.map(n => n.outerHTML).join('')}</${t}>`;
  }
  appendChild(n) { if (n.parentNode) n.parentNode._quitar(n); n.parentNode = this; this.childNodes.push(n); return n; }
  _quitar(n) { this.childNodes = this.childNodes.filter(x => x !== n); n.parentNode = null; }
  _swap(viejo, nuevo) {
    if (nuevo.parentNode) nuevo.parentNode._quitar(nuevo);
    this.childNodes = this.childNodes.map(x => (x === viejo ? nuevo : x)); nuevo.parentNode = this; viejo.parentNode = null;
  }
  replaceWith(n) { this.parentNode._swap(this, n); }
  remove() { this.parentNode._quitar(this); }
}

test('rolDe: según la dirección y lo recordado', () => {
  assert.strictEqual(rolDe('/arriba', null), 'arriba');
  assert.strictEqual(rolDe('/abajo', null), 'abajo');
  assert.strictEqual(rolDe('/abajo', 'doble'), 'abajo');
  assert.strictEqual(rolDe('/', 'doble'), 'ir-arriba');
  assert.strictEqual(rolDe('/index.html', 'doble'), 'ir-arriba');
  assert.strictEqual(rolDe('/', null), 'normal');
});

test('rutaDe y resolverRuta: ida y vuelta, con id y data-*', () => {
  const objetivo = new El('div', { id: 'mon-p1a', 'data-slot': '1' }, ['Incineroar']);
  const raiz = new El('div', {}, [new El('p'), new El('section', {}, [new El('span'), new El('div', {}, [new El('i'), objetivo])])]);
  const r = rutaDe(objetivo, raiz);
  assert.deepStrictEqual(r, { ruta: [1, 1, 1], id: 'mon-p1a', clave: 'slot=1' });
  assert.strictEqual(resolverRuta(raiz, r), objetivo);
});

test('rutaDe: fuera de la raíz → null', () => {
  assert.strictEqual(rutaDe(new El('b'), new El('div')), null);
});

test('resolverRuta descarta si el elemento ya no es el mismo', () => {
  const raiz = new El('div', {}, [new El('button', { id: 'a' })]);
  assert.strictEqual(resolverRuta(raiz, { ruta: [0], id: 'b', clave: '' }), null);   // otro id
  assert.strictEqual(resolverRuta(raiz, { ruta: [3], id: '', clave: '' }), null);    // índice fuera
  assert.strictEqual(resolverRuta(raiz, { ruta: [0], id: 'a', clave: 'slot=1' }), null); // otra clave
});

test('morph: sólo cambia lo que cambió (el resto son los mismos nodos)', () => {
  const p1 = new El('p', {}, ['a']), p2 = new El('p', {}, ['b']);
  const destino = new El('div', { id: 'fieldCard' }, [p1, p2]);
  const nuevo = new El('div', { id: 'fieldCard', style: 'display:none' }, [new El('p', {}, ['a']), new El('p', {}, ['c']), new El('em', {}, ['d'])]);
  const esperado = nuevo.outerHTML;                               // morph se lleva los nodos nuevos del árbol temporal
  morph(destino, 'html', () => nuevo);
  assert.strictEqual(destino.children[0], p1);
  assert.strictEqual(p1.childNodes[0].textContent, 'a');
  assert.strictEqual(destino.children[1], p2);                    // mismo <p>, texto actualizado
  assert.strictEqual(p2.childNodes[0].textContent, 'c');
  assert.strictEqual(destino.children[2].tagName, 'EM');          // hijo nuevo añadido
  assert.strictEqual(destino.getAttribute('style'), 'display:none');
  assert.strictEqual(destino.outerHTML, esperado);
});

test('morph: quita hijos que sobran y cambia de etiqueta sustituyendo', () => {
  const destino = new El('div', {}, [new El('p', {}, ['a']), new El('span', {}, ['x']), new El('p', {}, ['b'])]);
  const nuevo = new El('div', {}, [new El('p', {}, ['a']), new El('b', {}, ['x'])]);
  const objetivo = nuevo.outerHTML;
  morph(destino, 'html', () => nuevo);
  assert.strictEqual(destino.outerHTML, objetivo);
});

test('morph: atributos que cambian en un hijo se parchean sin sustituirlo', () => {
  const mon = new El('div', { class: 'mon' }, ['Pelipper']);
  const destino = new El('div', {}, [mon]);
  morph(destino, 'html', () => new El('div', {}, [new El('div', { class: 'mon ko' }, ['Pelipper'])]));
  assert.strictEqual(destino.children[0], mon);
  assert.strictEqual(mon.getAttribute('class'), 'mon ko');
});


test('mensajeEspera: sin abajo, el aviso literal; con abajo sin campo, otra línea; con campo, nada', () => {
  assert.strictEqual(mensajeEspera(false, false), ESPERA);
  assert.strictEqual(mensajeEspera(false, true), ESPERA);
  assert.strictEqual(mensajeEspera(true, false), 'Elige un ejercicio en la pantalla de abajo');
  assert.strictEqual(mensajeEspera(true, true), null);
});

// un envío falso que se resuelve a mano, para controlar el orden
function enviosFalsos() {
  const hechos = [], pendientes = [];
  const enviar = d => new Promise(ok => pendientes.push(() => { hechos.push(d); ok(); }));
  return { enviar, hechos, pendientes };
}
const tic = () => new Promise(r => setImmediate(r));

test('ultimoGana: una petición en vuelo; al acabar sale sólo el último valor', async () => {
  const f = enviosFalsos();
  const publicar = ultimoGana(f.enviar);
  publicar('a'); publicar('b'); publicar('c');
  assert.strictEqual(f.pendientes.length, 1);          // sólo 'a' ha salido
  f.pendientes.shift()(); await tic();
  assert.strictEqual(f.pendientes.length, 1);          // ahora 'c' (b se lo salta)
  f.pendientes.shift()(); await tic();
  assert.deepStrictEqual(f.hechos, ['a', 'c']);
  assert.strictEqual(f.pendientes.length, 0);
});

test('enCola: en orden y de uno en uno', async () => {
  const f = enviosFalsos();
  const tocar = enCola(f.enviar);
  tocar(1); tocar(2); tocar(3);
  assert.strictEqual(f.pendientes.length, 1);
  for (let i = 0; i < 3; i++) { f.pendientes.shift()(); await tic(); }
  assert.deepStrictEqual(f.hechos, [1, 2, 3]);
});

test('ultimoGana: si un envío falla, el siguiente valor sigue saliendo', async () => {
  let n = 0;
  const hechos = [];
  const publicar = ultimoGana(d => (++n === 1 ? Promise.reject(new Error('red')) : Promise.resolve(hechos.push(d))));
  publicar('a'); await tic(); publicar('b'); await tic(); await tic();
  assert.deepStrictEqual(hechos, ['b']);
});
