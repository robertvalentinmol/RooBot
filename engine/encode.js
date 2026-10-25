'use strict';
/**
 * Codificación del estado para la red neuronal (kit RL y bot con red).
 * Sólo usa información visible para el bando "s": su propio equipo completo, y del rival lo que
 * enseña el Open Team Sheet (especie/objeto/habilidad/movimientos de los 6) + lo revelado en combate.
 *
 * Tokens: 4 nuestros (los llevados) + 6 del rival (team preview) + 1 de campo.
 * Acción por ranura (A = 29): idx = mov*6 + objetivo*2 + mega (mov 0..3, objetivo 0=rival a, 1=rival b, 2=ninguno/aliado)
 *                             24..27 = cambio al miembro k de nuestro equipo, 28 = pasar.
 */
const { dexFor, toID } = require('./sim');

const N_OURS = 4, N_THEIRS = 6, F = 67, G = 25, A = 29;
const STATUS = ['', 'par', 'brn', 'slp', 'frz', 'psn', 'tox'];
const BOOSTS = ['atk', 'def', 'spa', 'spd', 'spe', 'accuracy', 'evasion'];
const STATS = ['hp', 'atk', 'def', 'spa', 'spd', 'spe'];
const TYPES = ['Normal', 'Fire', 'Water', 'Electric', 'Grass', 'Ice', 'Fighting', 'Poison', 'Ground', 'Flying', 'Psychic', 'Bug', 'Rock', 'Ghost', 'Dragon', 'Dark', 'Steel', 'Fairy', 'Stellar'];
const WEATHERS = ['', 'sunnyday', 'raindance', 'sandstorm', 'snowscape'];
const TERRAINS = ['', 'grassyterrain', 'electricterrain', 'psychicterrain', 'mistyterrain'];

const vocabCache = new Map();
function vocab(format) {
	const key = format || 'default';
	if (vocabCache.has(key)) return vocabCache.get(key);
	const dex = dexFor(format);
	const mk = list => { const m = new Map(); list.forEach((id, i) => m.set(id, i + 1)); return m; };
	const species = dex.species.all().filter(s => !s.isNonstandard).map(s => s.id).sort();
	const moves = dex.moves.all().filter(m => !m.isNonstandard).map(m => m.id).sort();
	const items = dex.items.all().filter(i => !i.isNonstandard).map(i => i.id).sort();
	const abilities = dex.abilities.all().filter(a => !a.isNonstandard).map(a => a.id).sort();
	const v = { species: mk(species), moves: mk(moves), items: mk(items), abilities: mk(abilities),
		sizes: { species: species.length + 1, moves: moves.length + 1, items: items.length + 1, abilities: abilities.length + 1 } };
	vocabCache.set(key, v);
	return v;
}

function alive(p) { return p && !p.fainted && p.hp > 0; }

function monTokens(b, s, V, foePreview = null) {
	const side = b.sides[s], foe = b.sides[1 - s];
	const cat = [], num = [];
	const pushMon = (p, set, theirs, revealed) => {
		const c = new Array(7).fill(0), x = new Array(F).fill(0);
		if (!set) { cat.push(c); num.push(x); return; }
		const species = p ? p.species : b.dex.species.get(set.species);
		c[0] = V.species.get(species.id) || 0;
		const itemId = p ? p.item : toID(set.item);
		c[1] = V.items.get(itemId) || 0;
		c[2] = V.abilities.get(p ? p.ability : toID(set.ability)) || 0;
		const mv = p ? p.moveSlots.map(m => m.id) : set.moves.map(toID);
		for (let k = 0; k < 4; k++) c[3 + k] = V.moves.get(mv[k]) || 0;
		x[0] = theirs ? 1 : 0;
		if (p && p.isActive && alive(p)) x[1 + p.position] = 1; else x[3] = 1;
		const isAlive = p ? alive(p) : true;
		x[4] = isAlive ? 1 : 0;
		x[5] = p ? (revealed || !theirs ? p.hp / p.maxhp : 1) : 1;
		x[6] = revealed ? 1 : 0;
		x[7] = p ? 1 : 0;
		if (p) {
			const st = STATUS.indexOf(p.status || ''); x[8 + Math.max(0, st)] = 1;
			BOOSTS.forEach((k, i) => { x[15 + i] = (p.boosts[k] || 0) / 6; });
		} else x[8] = 1;
		STATS.forEach((k, i) => { x[22 + i] = species.baseStats[k] / 200; });
		const types = p ? p.getTypes() : species.types;
		for (const t of types) { const ti = TYPES.indexOf(t); if (ti >= 0) x[28 + ti] = 1; }
		x[47] = species.isMega ? 1 : 0;
		x[48] = p && p.canMegaEvo ? 1 : 0;
		if (p) {
			x[49] = p.volatiles['choicelock'] ? 1 : 0;
			x[50] = p.volatiles['taunt'] ? 1 : 0;
			x[51] = p.volatiles['encore'] ? 1 : 0;
			x[52] = p.volatiles['substitute'] ? 1 : 0;
			x[53] = p.volatiles['stall'] ? 1 : 0;
			x[54] = p.isActive && p.activeMoveActions === 0 ? 1 : 0;
			x[55] = p.volatiles['perishsong'] ? (p.volatiles['perishsong'].duration || 0) / 3 : 0;
			x[56] = p.item ? 1 : 0;
			if (!theirs) STATS.forEach((k, i) => { x[57 + i] = (k === 'hp' ? p.maxhp : p.storedStats[k]) / 300; });
			if (!theirs && p.isActive) {
				const req = side.activeRequest && side.activeRequest.active && side.activeRequest.active[p.position];
				if (req) req.moves.forEach((m, k) => { if (k < 4) x[63 + k] = m.disabled ? 1 : 0; });
			}
		}
		cat.push(c); num.push(x);
	};
	for (let i = 0; i < N_OURS; i++) {
		const p = side.pokemon[i];
		pushMon(p, p ? p.set : null, false, true);
	}
	// rival: sus 6 de team preview; los llevados y revelados se emparejan por especie base
	const foeTeam = (foePreview || foe.team || []).slice(0, N_THEIRS);
	for (let i = 0; i < N_THEIRS; i++) {
		const set = foeTeam[i];
		if (!set) { pushMon(null, null, true, false); continue; }
		const base = sp => b.dex.species.get(sp).baseSpecies;
		const p = foe.pokemon.find(q => q.set === set || (q.set && base(q.set.species) === base(set.species)));
		const revealed = !!(p && (p.isActive || p.previouslySwitchedIn > 0 || p.fainted));
		pushMon(revealed ? p : null, set, true, revealed);
	}
	return { cat, num };
}

function fieldToken(b, s) {
	const x = new Array(G).fill(0);
	const w = WEATHERS.indexOf(b.field.weather || ''); x[Math.max(0, w)] = 1;
	const t = TERRAINS.indexOf(b.field.terrain || ''); x[5 + Math.max(0, t)] = 1;
	const tr = b.field.pseudoWeather['trickroom']; x[10] = tr ? (tr.duration || 0) / 5 : 0;
	x[11] = b.field.pseudoWeather['gravity'] ? 1 : 0;
	const me = b.sides[s], foe = b.sides[1 - s];
	const dur = (side, id) => side.sideConditions[id] ? (side.sideConditions[id].duration || 0) : 0;
	x[12] = dur(me, 'tailwind') / 4; x[13] = dur(foe, 'tailwind') / 4;
	['reflect', 'lightscreen', 'auroraveil'].forEach((id, i) => { x[14 + i] = dur(me, id) / 5; x[17 + i] = dur(foe, id) / 5; });
	x[20] = Math.min(1, b.turn / 20);
	x[21] = me.pokemon.filter(p => p.fainted).length / 4;
	x[22] = foe.pokemon.filter(p => p.fainted).length / 4;
	x[23] = me.pokemon.some(p => p.canMegaEvo) ? 0 : 1;
	x[24] = foe.pokemon.some(p => p.canMegaEvo) ? 0 : 1;
	return x;
}

/** Máscara de acciones legales por ranura + tabla para traducir índice -> elección de Showdown. */
function actionMasks(b, s) {
	const side = b.sides[s];
	const req = side.activeRequest;
	const masks = [new Array(A).fill(0), new Array(A).fill(0)];
	const choices = [new Array(A).fill(null), new Array(A).fill(null)];
	const foes = b.sides[1 - s].active;
	for (let slot = 0; slot < 2; slot++) {
		const pk = side.active[slot];
		const passOnly = () => { masks[slot][28] = 1; choices[slot][28] = 'pass'; };
		if (!req || req.wait) { passOnly(); continue; }
		if (req.forceSwitch) {
			if (!req.forceSwitch[slot]) { passOnly(); continue; }
			let any = false;
			side.pokemon.forEach((p, k) => {
				if (k >= 4 || p.isActive || !alive(p)) return;
				masks[slot][24 + k] = 1; choices[slot][24 + k] = `switch ${p.name}`; any = true;
			});
			if (!any) passOnly();
			continue;
		}
		if (!alive(pk) || !req.active || !req.active[slot]) { passOnly(); continue; }
		const ar = req.active[slot];
		const allyAlive = alive(side.active[1 - slot]);
		ar.moves.forEach((m, mi) => {
			if (mi > 3 || m.disabled || (m.pp !== undefined && m.pp <= 0)) return;
			const mv = b.dex.moves.get(m.id);
			const opts = [];
			if (['normal', 'any', 'adjacentFoe'].includes(m.target)) {
				foes.forEach((f, fi) => { if (alive(f)) opts.push([fi, ` ${fi + 1}`]); });
				if (!opts.length) opts.push([0, ' 1']);
				if (m.target !== 'adjacentFoe' && allyAlive && mv.category === 'Status') opts.push([2, ` ${-(2 - slot)}`]);
			} else if (m.target === 'adjacentAlly') {
				if (allyAlive) opts.push([2, ` ${-(2 - slot)}`]);
			} else if (m.target === 'adjacentAllyOrSelf') {
				opts.push([2, ` ${-(slot + 1)}`]);
			} else opts.push([2, '']);
			const megas = ar.canMegaEvo ? [0, 1] : [0];
			for (const [t, tstr] of opts) for (const mg of megas) {
				const idx = mi * 6 + t * 2 + mg;
				masks[slot][idx] = 1;
				choices[slot][idx] = `move ${m.id}${tstr}${mg ? ' mega' : ''}`;
			}
		});
		if (!ar.trapped) {
			side.pokemon.forEach((p, k) => {
				if (k >= 4 || p.isActive || !alive(p)) return;
				masks[slot][24 + k] = 1; choices[slot][24 + k] = `switch ${p.name}`;
			});
		}
		if (!masks[slot].some(Boolean)) { masks[slot][28] = 1; choices[slot][28] = 'default'; }
	}
	return { masks, choices };
}

const r3 = x => Math.round(x * 1000) / 1000;

function encode(b, s, format, withMasks = true, foePreview = null) {
	const V = vocab(format);
	const { cat, num } = monTokens(b, s, V, foePreview);
	const out = { cat, num: num.map(row => row.map(r3)), field: fieldToken(b, s).map(r3) };
	if (withMasks) Object.assign(out, actionMasks(b, s));
	return out;
}

module.exports = { encode, vocab, actionMasks, N_OURS, N_THEIRS, F, G, A };
