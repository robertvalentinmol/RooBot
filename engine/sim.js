'use strict';
/**
 * Carga del simulador de Showdown y utilidades de sets/equipos.
 * Todo el motor usa el simulador REAL de Showdown (mod "champions"), así que
 * daño, habilidades, objetos y cambios de movimientos de Champions son exactos.
 */
const path = require('path');

const SD = process.env.VGCLAB_SHOWDOWN || path.join(__dirname, '..', 'server', 'pokemon-showdown');
let PS, State;
try {
	PS = require(path.join(SD, 'dist', 'sim'));
	State = require(path.join(SD, 'dist', 'sim', 'state')).State;
} catch (e) {
	console.error(`[engine] No encuentro Showdown compilado en ${SD}. Ejecuta primero server/install (ver README).`);
	throw e;
}

const DEFAULT_FORMAT = process.env.VGCLAB_FORMAT || 'gen9championsvgc2026regmc';

const dexCache = new Map();
function dexFor(formatid = DEFAULT_FORMAT) {
	if (!dexCache.has(formatid)) {
		const fmt = PS.Dex.formats.get(formatid);
		if (!fmt.exists) throw new Error(`Formato desconocido: ${formatid}`);
		dexCache.set(formatid, PS.Dex.forFormat(fmt));
	}
	return dexCache.get(formatid);
}

const toID = PS.toID;
const STATS = ['hp', 'atk', 'def', 'spa', 'spd', 'spe'];

/** Normaliza un set (acepta "sp" como alias de "evs": en Champions los EVs del set SON Stat Points). */
function normSet(raw, dex = dexFor()) {
	if (typeof raw === 'string') {
		const imported = PS.Teams.import(raw);
		if (!imported || !imported.length) throw new Error('Set ilegible: ' + raw.slice(0, 60));
		raw = imported[0];
	}
	const species = dex.species.get(raw.species || raw.name);
	if (!species.exists) throw new Error(`Especie desconocida: ${raw.species}`);
	const evs = Object.assign({ hp: 0, atk: 0, def: 0, spa: 0, spd: 0, spe: 0 }, raw.sp || raw.evs || {});
	const ivs = Object.assign({ hp: 31, atk: 31, def: 31, spa: 31, spd: 31, spe: 31 }, raw.ivs || {});
	return {
		name: raw.name || species.name,
		species: species.name,
		item: raw.item || '',
		ability: raw.ability || species.abilities['0'],
		moves: (raw.moves || []).map(m => dex.moves.get(m).id).filter(Boolean),
		nature: raw.nature || 'Serious',
		evs, ivs,
		// En Champions todo combate es a nivel 50. Showdown asume 100 si el texto no lo dice,
		// y eso dobla el daño en la fórmula: lo forzamos.
		level: 50,
		gender: raw.gender || '',
		teraType: raw.teraType || species.types[0],
	};
}

function packTeam(sets) {
	return PS.Teams.pack(sets);
}

function importTeam(text, dex = dexFor()) {
	return PS.Teams.import(text).map(s => normSet(s, dex));
}

/**
 * Reparto de Stat Points estimado para un set del rival (OTS no revela SP ni naturaleza).
 * Regla sencilla y conservadora, al estilo de los "spreads adivinados" que usó mikumiku37.
 */
function guessSpread(set, dex = dexFor()) {
	let species = dex.species.get(set.species);
	const item = dex.items.get(set.item);
	if (item.megaStone) {
		const m = item.megaStone[species.name] || item.megaStone[species.baseSpecies];
		if (m) species = dex.species.get(m);
	}
	const bs = species.baseStats;
	const moves = (set.moves || []).map(m => dex.moves.get(m));
	const dmg = moves.filter(m => m.category !== 'Status' && m.basePower > 0);
	const phys = dmg.filter(m => m.category === 'Physical').length;
	const spec = dmg.filter(m => m.category === 'Special').length;
	const physical = phys > spec || (phys === spec && bs.atk >= bs.spa);
	const atkStat = physical ? 'atk' : 'spa';
	const trMove = moves.some(m => m.id === 'trickroom');
	const evs = { hp: 0, atk: 0, def: 0, spa: 0, spd: 0, spe: 0 };
	let nature;
	if (dmg.length <= 1) {
		// soporte: tanque mixto
		evs.hp = 32; evs.def = 17; evs.spd = 17;
		nature = physical ? 'Careful' : 'Calm';
		if (trMove && bs.spe <= 60) nature = 'Sassy';
	} else if (bs.spe >= 80 && !trMove) {
		evs[atkStat] = 32; evs.spe = 32; evs.hp = 2;
		nature = physical ? 'Jolly' : 'Timid';
	} else if (bs.spe <= 55) {
		evs.hp = 32; evs[atkStat] = 32; evs.def = 2;
		nature = physical ? 'Brave' : 'Quiet';
	} else {
		evs.hp = 32; evs[atkStat] = 32; evs.spe = 2;
		nature = physical ? 'Adamant' : 'Modest';
	}
	return { evs, nature };
}

/** Movimientos plausibles cuando NO hay team sheet abierto: revelados + Protect + mejores STAB. */
function guessMoves(species, revealed, dex = dexFor()) {
	const sp = dex.species.get(species);
	const learn = (dex.data.Learnsets[sp.id] || dex.data.Learnsets[toID(sp.baseSpecies)] || {}).learnset || {};
	const moves = new Set((revealed || []).map(toID));
	const pool = Object.keys(learn).map(id => dex.moves.get(id)).filter(m => m.exists && !m.isNonstandard);
	if (moves.size < 4 && pool.some(m => m.id === 'protect')) moves.add('protect');
	const score = m => {
		if (m.category === 'Status' || !m.basePower) return -1;
		if (m.flags.recharge || m.selfdestruct || m.id === 'focuspunch') return -1;
		const acc = m.accuracy === true ? 1 : m.accuracy / 100;
		const stab = sp.types.includes(m.type) ? 1.5 : 1;
		const spread = ['allAdjacentFoes', 'allAdjacent'].includes(m.target) ? 1.1 : 1;
		return m.basePower * acc * stab * spread;
	};
	const ranked = pool.filter(m => score(m) > 0).sort((a, b) => score(b) - score(a));
	const usedTypes = new Set([...moves].map(id => dex.moves.get(id).type));
	for (const m of ranked) {
		if (moves.size >= 4) break;
		if (usedTypes.has(m.type)) continue;
		moves.add(m.id); usedTypes.add(m.type);
	}
	for (const m of ranked) { if (moves.size >= 4) break; moves.add(m.id); }
	return [...moves].slice(0, 4);
}

function completeOpponentSet(partial, dex = dexFor(), spreadOverride = null, variant = 0, exclude = null) {
	const sp = dex.species.get(partial.species);
	// primero, el uso real (equipos guardados): el set más parecido a lo que se ha visto
	let u = null;
	try {
		const q = spreadOverride && spreadOverride.nature ? Object.assign({}, partial, { nature: spreadOverride.nature }) : partial;
		u = require('./usage').complete(q, dex, variant, exclude);
	} catch (e) { u = null; }
	if (u) {
		const set = {
			name: partial.name || sp.name,
			species: sp.name,
			item: u.item || '',
			ability: u.ability || sp.abilities['0'],
			moves: u.moves.length >= 4 ? u.moves : guessMoves(sp.name, u.moves, dex),
			level: 50,
		};
		const guess = guessSpread(set, dex);
		const evsOk = u.fit >= 0.75 && Object.values(u.evs).some(v => v > 0);
		const evs = (spreadOverride && spreadOverride.evs) || (evsOk ? u.evs : guess.evs);
		const nature = (spreadOverride && spreadOverride.nature) || (evsOk && u.nature) || guess.nature;
		return normSet(Object.assign(set, { evs, nature }), dex);
	}
	const set = {
		name: partial.name || sp.name,
		species: sp.name,
		item: partial.item || '',
		ability: partial.ability || sp.abilities['0'],
		moves: partial.moves && partial.moves.length >= 4 ? partial.moves.map(toID) :
			guessMoves(sp.name, partial.moves || [], dex),
		level: 50,
	};
	let g = spreadOverride || (partial.evs && partial.nature ? { evs: partial.evs, nature: partial.nature } : guessSpread(set, dex));
	if (!g.evs) g = { evs: guessSpread(set, dex).evs, nature: g.nature || guessSpread(set, dex).nature };
	return normSet(Object.assign(set, g), dex);
}

module.exports = {
	PS, State, SD, DEFAULT_FORMAT, dexFor, toID, STATS,
	normSet, packTeam, importTeam, guessSpread, guessMoves, completeOpponentSet,
};
