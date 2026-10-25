'use strict';
/**
 * Team preview: elegir 4 de 6 y los 2 leads.
 * Se construye una tabla de emparejamientos con daño REAL del simulador (cada uno de los nuestros
 * contra cada uno de los suyos y viceversa) y se puntúan todas las combinaciones 4-de-6 × leads.
 * Con temperatura > 0 se muestrea entre las mejores (para no ser predecible).
 */
const { dexFor, normSet, completeOpponentSet } = require('./sim');
const { estDamage } = require('./search');

const SUPPORT = {
	fakeout: 0.15, tailwind: 0.12, trickroom: 0.12, followme: 0.08, ragepowder: 0.08,
	icywind: 0.05, electroweb: 0.04, partingshot: 0.05, helpinghand: 0.03, spore: 0.08, wideguard: 0.03,
};

function bestFrac(b, att, def) {
	let best = 0, bestMove = null;
	for (const ms of att.moveSlots) {
		const mv = b.dex.moves.get(ms.id);
		if (mv.category === 'Status') continue;
		const spread = ['allAdjacentFoes', 'allAdjacent'].includes(mv.target);
		const d = estDamage(b, att, def, ms.id, spread);
		const f = Math.min(1.2, d / def.maxhp);
		if (f > best) { best = f; bestMove = mv.name; }
	}
	return { frac: best, move: bestMove };
}

/**
 * Tabla de emparejamientos 6x6 en UN solo combate (formato de dobles sin elección de 4):
 * los 12 Pokémon se marcan como "en el campo" para que cuenten habilidades y objetos, y se
 * megaevolucionan los que llevan piedra. ~4 veces más rápido que un combate por pareja y
 * prácticamente igual de preciso (error medio <1 %).
 */
const TABLE_FORMAT = 'gen9championsdoublescustomgame';
const DUMMY_SET = { species: 'Audino', ability: 'Klutz', item: '', moves: ['splash'], nature: 'Serious', level: 50 };

function matchupTable(ours, theirs, format) {
	const { PS } = require('./sim');
	const pad = t => t.concat(Array(Math.max(0, 1 - t.length)).fill(DUMMY_SET));
	const b = new PS.Battle({ formatid: TABLE_FORMAT, seed: '1,2,3,4' });
	b.send = () => {};
	b.setPlayer('p1', { name: 'a', team: PS.Teams.pack(pad(ours)) });
	b.setPlayer('p2', { name: 'b', team: PS.Teams.pack(pad(theirs)) });
	b.choose('p1', 'team ' + ours.map((_, i) => i + 1).join(''));
	b.choose('p2', 'team ' + theirs.map((_, i) => i + 1).join(''));
	const order = side => side.pokemon.slice().sort((x, y) => x.position - y.position);
	const A = order(b.sides[0]), B = order(b.sides[1]);
	for (const p of A.concat(B)) {
		if (p.canMegaEvo) p.formeChange(b.dex.species.get(p.canMegaEvo), p.getItem(), true);
		p.isActive = true; p.isStarted = true;
	}
	// después de megaevolucionar (Sequía, Intimidación... se activan al cambiar de forma): estado neutro
	for (const p of A.concat(B)) {
		p.boosts = { atk: 0, def: 0, spa: 0, spd: 0, spe: 0, accuracy: 0, evasion: 0 };
		p.hp = p.maxhp;
	}
	b.field.clearWeather(); b.field.clearTerrain();
	for (const id in b.field.pseudoWeather) b.field.removePseudoWeather(id);
	for (const side of b.sides) for (const id in side.sideConditions) side.removeSideCondition(id);
	const off = [], thr = [], spd = [], moves = [], tmoves = [];
	// sólo la pareja que se calcula cuenta como "en el campo" (si no, auras, Compiescolta, etc. de los
	// demás contaminarían el cálculo)
	for (const p of A.concat(B)) p.isActive = false;
	for (let i = 0; i < ours.length; i++) {
		off.push([]); thr.push([]); spd.push([]); moves.push([]); tmoves.push([]);
		for (let j = 0; j < theirs.length; j++) {
			A[i].isActive = true; B[j].isActive = true;
			b.sides[0].active = [A[i], null]; b.sides[1].active = [B[j], null];
			const o = bestFrac(b, A[i], B[j]);
			const t = bestFrac(b, B[j], A[i]);
			A[i].isActive = false; B[j].isActive = false;
			off[i].push(o.frac); thr[i].push(t.frac); moves[i].push(o.move); tmoves[i].push(t.move);
			const s1 = A[i].getStat('spe', false, false), s2 = B[j].getStat('spe', false, false);
			spd[i].push(s1 > s2 ? 1 : s1 < s2 ? -1 : 0);
		}
	}
	return { off, thr, spd, moves, tmoves };
}

/** La misma tabla vista desde el otro lado (evita recalcularla). */
function transposeTable(T) {
	const tr = M => M[0] ? M[0].map((_, j) => M.map(r => r[j])) : [];
	return { off: tr(T.thr), thr: tr(T.off), spd: tr(T.spd).map(r => r.map(x => -x)), moves: null };
}

function combos(n, k) {
	const out = [];
	const rec = (start, acc) => {
		if (acc.length === k) { out.push(acc.slice()); return; }
		for (let i = start; i < n; i++) { acc.push(i); rec(i + 1, acc); acc.pop(); }
	};
	rec(0, []);
	return out;
}

function supportBonus(set) {
	let s = 0;
	for (const m of set.moves) s += SUPPORT[m] || 0;
	if (['intimidate', 'drought', 'drizzle', 'sandstream', 'snowwarning', 'grassysurge', 'psychicsurge', 'electricsurge', 'mistysurge']
		.includes(set.ability.toLowerCase().replace(/[^a-z]/g, ''))) s += 0.1;
	return s;
}

function teamPreview(req) {
	const format = req.format;
	const dex = dexFor(format);
	const ours = req.ours.map(s => normSet(s, dex));
	const theirs = req.theirs.map(s => (s.evs && s.nature) ? normSet(s, dex) : completeOpponentSet(s, dex));
	const T = req.table || matchupTable(ours, theirs, format);
	const nT = theirs.length;
	const options = [];
	const megaHolder = ours.map(s => !!dex.items.get(s.item).megaStone);
	const bringKey = req.bring ? req.bring.slice().sort((x, y) => x - y).join('-') : null;
	for (const S of combos(ours.length, Math.min(4, ours.length))) {
		if (bringKey && S.join('-') !== bringKey) continue;
		let cover = 0, risk = 0;
		for (let j = 0; j < nT; j++) {
			cover += Math.min(1, Math.max(...S.map(i => T.off[i][j])));
			risk += S.reduce((a, i) => a + Math.min(1, T.thr[i][j]), 0) / S.length;
		}
		const megas = S.filter(i => megaHolder[i]).length;
		let teamScore = cover / nT - 0.45 * risk / nT + S.reduce((a, i) => a + supportBonus(ours[i]), 0) / 4;
		if (megas > 2) teamScore -= 0.15;
		if (megas === 0 && megaHolder.some(Boolean)) teamScore -= 0.1;
		for (const L of combos(S.length, 2)) {
			const leads = L.map(k => S[k]);
			let lead = 0;
			for (let j = 0; j < nT; j++) lead += Math.min(1, Math.max(...leads.map(i => T.off[i][j]))) - 0.3 * leads.reduce((a, i) => a + T.thr[i][j], 0) / 2 + 0.05 * leads.reduce((a, i) => a + T.spd[i][j], 0) / 2;
			lead /= nT;
			lead += leads.reduce((a, i) => a + supportBonus(ours[i]), 0) * 0.6;
			const back = S.filter(i => !leads.includes(i));
			if (req.leads && !(leads.length === 2 && req.leads.includes(leads[0]) && req.leads.includes(leads[1]))) continue;
			options.push({ order: [...leads, ...back], score: teamScore + 0.6 * lead });
		}
	}
	options.sort((a, b) => b.score - a.score);
	const temp = req.temperature === undefined ? 0.05 : req.temperature;
	let pick = options[0];
	if (temp > 0) {
		const top = options.slice(0, 12);
		const w = top.map(o => Math.exp((o.score - top[0].score) / temp));
		let r = Math.random() * w.reduce((a, c) => a + c, 0);
		for (let i = 0; i < top.length; i++) { r -= w[i]; if (r <= 0) { pick = top[i]; break; } }
	}
	return {
		choice: 'team ' + pick.order.map(i => i + 1).join(''),
		order: pick.order,
		names: pick.order.map(i => ours[i].species),
		score: pick.score,
		top: options.slice(0, 5).map(o => ({ names: o.order.map(i => ours[i].species), score: +o.score.toFixed(3) })),
		table: { ours: ours.map(s => s.species), theirs: theirs.map(s => s.species), off: T.off, thr: T.thr, moves: T.moves },
	};
}

module.exports = { teamPreview, matchupTable, transposeTable, combos, supportBonus };
