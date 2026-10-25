'use strict';
/**
 * Uso real de cada Pokémon según los equipos guardados (teams/**.txt: los tuyos y los importados de torneos).
 * Sirve para completar con cabeza lo que no se ve de un set (habilidad, objeto, movimientos, SP y naturaleza):
 * en vez de «habilidad 1 y el ataque más fuerte», el set real más parecido a lo que se ha visto.
 */
const fs = require('fs');
const path = require('path');
const { PS, toID } = require('./sim');

const TEAMS_DIR = path.join(__dirname, '..', 'teams');
let CACHE = null;              // { key: { baseId: [ {set, n} ] } }

function walk(dir, out) {
	let list = [];
	try { list = fs.readdirSync(dir, { withFileTypes: true }); } catch (e) { return out; }
	for (const d of list) {
		const f = path.join(dir, d.name);
		if (d.isDirectory()) walk(f, out);
		else if (/\.txt$/i.test(d.name)) out.push(f);
	}
	return out;
}

/** Sets por especie base (con cuántas veces aparece cada combinación igual). */
function pool(dex) {
	const key = dex.currentMod || 'base';
	if (CACHE && CACHE[key]) return CACHE[key];
	const by = {};
	const seen = {};
	for (const f of walk(TEAMS_DIR, [])) {
		let sets = [];
		try { sets = PS.Teams.import(fs.readFileSync(f, 'utf8')) || []; } catch (e) { continue; }
		for (const raw of sets) {
			const sp = dex.species.get(raw.species || raw.name);
			if (!sp.exists) continue;
			const base = toID(sp.baseSpecies);
			const set = {
				species: sp.name, item: toID(raw.item || ''), ability: toID(raw.ability || ''),
				moves: (raw.moves || []).map(toID).filter(Boolean),
				nature: raw.nature || '', evs: Object.assign({ hp: 0, atk: 0, def: 0, spa: 0, spd: 0, spe: 0 }, raw.evs || {}),
			};
			const sig = [set.item, set.ability, set.moves.slice().sort().join(','), set.nature, JSON.stringify(set.evs)].join('|');
			by[base] = by[base] || [];
			const k = base + '|' + sig;
			if (seen[k]) { seen[k].n++; continue; }
			const entry = { set, n: 1 };
			seen[k] = entry;
			by[base].push(entry);
		}
	}
	CACHE = CACHE || {};
	CACHE[key] = by;
	return by;
}

/**
 * Candidatos para un set parcial, ordenados de más a menos probable.
 * partial: { species, item?, ability?, moves?: [ids] } con lo que se ha visto.
 */
function sigOf(set) {
	const ev = Object.assign({ hp: 0, atk: 0, def: 0, spa: 0, spd: 0, spe: 0 }, set.evs || {});
	return [toID(set.item || ''), toID(set.ability || ''), (set.moves || []).map(toID).sort().join(','), set.nature || '', JSON.stringify(ev)].join('|');
}

function candidates(partial, dex, exclude = null) {
	const sp = dex.species.get(partial.species);
	if (!sp.exists) return [];
	// el set de verdad no cuenta como «uso» (si no, con tu propio equipo guardado el rival lo adivinaría entero)
	const ex = exclude ? sigOf(exclude) : null;
	const list = (pool(dex)[toID(sp.baseSpecies)] || [])
		.map(e => (ex && sigOf(e.set) === ex) ? (e.n > 1 ? { set: e.set, n: e.n - 1 } : null) : e).filter(Boolean);
	if (!list.length) return [];
	const rm = (partial.moves || []).map(toID);
	const ri = toID(partial.item || ''), ra = toID(partial.ability || '');
	// lo típico de la especie: cuántos sets llevan cada movimiento, objeto y habilidad
	const N = list.reduce((a, e) => a + e.n, 0);
	const fm = {}, fi = {}, fa = {};
	for (const e of list) {
		for (const m of e.set.moves) fm[m] = (fm[m] || 0) + e.n;
		fi[e.set.item] = (fi[e.set.item] || 0) + e.n;
		fa[e.set.ability] = (fa[e.set.ability] || 0) + e.n;
	}
	const scored = list.map(e => {
		let s = Math.log(1 + e.n);
		s += 3 * e.set.moves.reduce((a, m) => a + fm[m] / N, 0) / Math.max(1, e.set.moves.length);
		s += 1.5 * fi[e.set.item] / N + 1.5 * fa[e.set.ability] / N;
		for (const m of rm) s += e.set.moves.includes(m) ? 3 : -4;
		if (ri) s += e.set.item === ri ? 4 : -4;
		if (ra) s += e.set.ability === ra ? 4 : -6;
		// naturaleza conocida (hoja abierta): mejor un reparto pensado para esa naturaleza
		if (partial.nature) s += e.set.nature === partial.nature ? 2.5 : 0;
		// misma forma (p. ej. Rotom-Wash frente a Rotom-Heat) cuando se sabe
		if (toID(e.set.species) === toID(sp.name)) s += 1;
		return { e, s };
	});
	scored.sort((a, b) => b.s - a.s);
	return scored;
}

/**
 * Completa un set con el uso real. variant = 0 → el más probable; 1, 2… → alternativas plausibles
 * (para los «mundos posibles»). Devuelve null si no hay datos de esa especie.
 */
function complete(partial, dex, variant = 0, exclude = null) {
	const sc = candidates(partial, dex, exclude);
	if (!sc.length) return null;
	const top = sc[0].s;
	const near = sc.filter(x => x.s >= top - 2.5);          // los que encajan casi igual de bien
	const pick = near[variant % near.length].e.set;
	const rm = (partial.moves || []).map(toID);
	const ri = toID(partial.item || ''), ra = toID(partial.ability || '');
	const moves = [...new Set(rm.concat(pick.moves))];
	if (moves.length > 4) {
		// lo visto manda; del resto, los del set elegido
		const extra = pick.moves.filter(m => !rm.includes(m));
		moves.length = 0;
		moves.push(...rm.slice(0, 4), ...extra.slice(0, Math.max(0, 4 - rm.length)));
	}
	// si faltan movimientos, los más usados por la especie
	if (moves.length < 4) {
		const freq = {};
		for (const x of sc) for (const m of x.e.set.moves) freq[m] = (freq[m] || 0) + x.e.n;
		for (const m of Object.keys(freq).sort((a, b) => freq[b] - freq[a])) {
			if (moves.length >= 4) break;
			if (!moves.includes(m)) moves.push(m);
		}
	}
	// cuánto encaja lo visto con el set elegido (para fiarse o no de sus SP)
	let hit = 0, tot = 0;
	for (const m of rm) { tot++; if (pick.moves.includes(m)) hit++; }
	if (ri) { tot++; if (pick.item === ri) hit++; }
	if (ra) { tot++; if (pick.ability === ra) hit++; }
	return {
		fit: tot ? hit / tot : 1,
		species: partial.species,
		item: partial.item || pick.item,
		ability: partial.ability || pick.ability,
		moves: moves.slice(0, 4),
		nature: pick.nature,
		evs: Object.assign({}, pick.evs),
		_uso: { n: near.length, total: sc.length },
	};
}

function reset() { CACHE = null; }
function has(species, dex) { const sp = dex.species.get(species); return sp.exists && !!(pool(dex)[toID(sp.baseSpecies)] || []).length; }

module.exports = { pool, candidates, complete, reset, has };
