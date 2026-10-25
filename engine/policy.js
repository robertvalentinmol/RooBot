'use strict';
/**
 * «Experiencia»: qué tipo de jugada suelen elegir los jugadores fuertes en cada situación.
 * Cada opción de una ranura (atacar, proteger, cambiar, Fake Out, subir stats…) se describe con unos rasgos
 * (daño que hace, si deja KO, cuánto peligro corre el Pokémon, cómo le va al que entra si cambia…) y un modelo
 * lineal aprendido con miles de decisiones reales (vgclab/experiencia.py) da a cada opción una probabilidad.
 * El análisis la combina con el cálculo: el cálculo manda cuando hay diferencias claras; la experiencia
 * desempata y aporta lo que un turno de cálculo no ve (cambios, proteger, preparar).
 */
const fs = require('fs');
const path = require('path');
const S = require('./search');
const { toID } = require('./sim');

const PROTECT = S.PROTECT_MOVES;
const STATUS_MOVES = new Set(['willowisp', 'thunderwave', 'spore', 'sleeppowder', 'yawn', 'darkvoid', 'hypnosis', 'glare',
	'stunspore', 'nuzzle', 'toxic', 'poisonpowder']);
const SLEEP = new Set(['spore', 'sleeppowder', 'yawn', 'darkvoid', 'hypnosis']);
const SPEED_STATUS = new Set(['tailwind', 'trickroom', 'icywind', 'electroweb', 'thunderwave', 'scaryface', 'bulldoze']);
const SUPPORT = new Set(['helpinghand', 'wideguard', 'quickguard', 'reflect', 'lightscreen', 'auroraveil', 'lifedew',
	'pollenpuff', 'healpulse', 'coaching', 'decorate', 'instruct', 'allyswitch', 'safeguard', 'mist']);
const PIVOT = new Set(['uturn', 'voltswitch', 'flipturn', 'partingshot', 'teleport', 'chillyreception', 'shedtail']);

const NAMES = [
	'prior_heur', 'mega',
	'atk', 'atk_dmg', 'atk_ko', 'atk_spread', 'atk_prio', 'atk_debil', 'atk_aliado', 'atk_x_peligro', 'atk_x_rapido',
	'atk_objetivo_amenaza', 'atk_objetivo_tocado', 'atk_pivote',
	'prot', 'prot_x_peligro', 'prot_repetido', 'prot_x_dos_amenazas', 'prot_turno1',
	'cambio', 'cambio_x_peligro', 'cambio_entra_peligro', 'cambio_entra_pega', 'cambio_renuncia', 'cambio_vida',
	'cambio_stats_bajadas', 'cambio_entra_intimidate', 'cambio_entra_fakeout',
	'fakeout', 'fakeout_valido', 'fakeout_x_amenaza',
	'preparar', 'preparar_x_peligro', 'preparar_x_vida',
	'estado', 'estado_valido', 'estado_dormir', 'estado_x_amenaza',
	'velocidad', 'velocidad_falta', 'velocidad_sobra',
	'redireccion', 'redireccion_x_aliado',
	'apoyo', 'otro_estado',
	'peligro', 'vida', 'rapido', 'turno1',
];

function alive(p) { return p && !p.fainted && p.hp > 0; }

/** Contexto de una ranura: peligro del Pokémon, su mejor daño, si es más rápido… */
function slotContext(b, s, slot) {
	const side = b.sides[s];
	const pk = side.active[slot];
	const foes = S.foesOf(b, s);
	const ctx = { threat: [0, 0], foeThreatLevel: [0, 0] };
	for (let i = 0; i < 2; i++) if (alive(side.active[i])) ctx.threat[i] = S.threatTo(b, s, side.active[i]);
	for (let i = 0; i < 2; i++) {
		const f = foes[i];
		if (!alive(f)) continue;
		let best = 0;
		for (const p of side.active.filter(alive)) {
			for (const ms of f.moveSlots) {
				const mv = b.dex.moves.get(ms.id);
				if (mv.category === 'Status') continue;
				const d = S.estDamage(b, f, p, ms.id, ['allAdjacentFoes', 'allAdjacent'].includes(mv.target));
				best = Math.max(best, Math.min(1, d / Math.max(1, p.hp)));
			}
		}
		ctx.foeThreatLevel[i] = best;
	}
	// cuántos rivales pueden dejarle KO
	let koBy = 0;
	for (const f of foes) {
		if (!alive(f) || !alive(pk)) continue;
		for (const ms of f.moveSlots) {
			const mv = b.dex.moves.get(ms.id);
			if (mv.category === 'Status') continue;
			if (S.estDamage(b, f, pk, ms.id, ['allAdjacentFoes', 'allAdjacent'].includes(mv.target)) >= pk.hp) { koBy++; break; }
		}
	}
	let myBest = 0;
	for (const ms of pk.moveSlots) {
		const mv = b.dex.moves.get(ms.id);
		if (mv.category === 'Status') continue;
		for (const f of foes) if (alive(f)) myBest = Math.max(myBest, Math.min(1, S.estDamage(b, pk, f, ms.id, false) / Math.max(1, f.hp)));
	}
	const sp = S.actionSpeed(b, pk);
	const faster = foes.filter(alive).every(f => sp > S.actionSpeed(b, f)) ? 1 : 0;
	return Object.assign(ctx, { pk, foes, koBy, myBest, faster, slower: S.sideSpeed(b, s) < S.sideSpeed(b, 1 - s),
		firstTurn: pk.activeMoveActions === 0 ? 1 : 0, stalled: pk.volatiles['stall'] ? 1 : 0 });
}

function features(b, s, slot, o, ctx) {
	const f = new Array(NAMES.length).fill(0);
	const set = (n, v) => { f[NAMES.indexOf(n)] = v; };
	const pk = ctx.pk, side = b.sides[s], foes = ctx.foes, ally = side.active[1 - slot];
	const peligro = ctx.threat[slot];
	set('peligro', peligro); set('vida', pk.hp / pk.maxhp); set('rapido', ctx.faster); set('turno1', ctx.firstTurn);
	let heur = 0;
	try { heur = S.priorSlot(b, s, slot, o, ctx); } catch (e) { heur = 0; }
	set('prior_heur', Math.max(-1.5, Math.min(2, heur)));
	if (o.mega) set('mega', 1);
	if (o.kind === 'switch') {
		const inc = side.pokemon.find(p => p.name === o.switchTo);
		set('cambio', 1); set('cambio_x_peligro', peligro + 0.5 * ctx.koBy);
		if (inc) {
			set('cambio_entra_peligro', S.threatTo(b, s, inc) * (inc.hp / inc.maxhp));
			let hit = 0;
			for (const ms of inc.moveSlots) {
				const mv = b.dex.moves.get(ms.id);
				if (mv.category === 'Status') continue;
				for (const t of foes) if (alive(t)) {
					try { hit = Math.max(hit, Math.min(1, S.estDamage(b, inc, t, ms.id, false) / Math.max(1, t.hp))); } catch (e) {}
				}
			}
			set('cambio_entra_pega', hit);
			set('cambio_vida', inc.hp / inc.maxhp - pk.hp / pk.maxhp);
			const ab = toID(inc.ability || inc.baseAbility || '');
			if (ab === 'intimidate') set('cambio_entra_intimidate', 1);
			if (inc.moveSlots.some(m => m.id === 'fakeout')) set('cambio_entra_fakeout', 1);
		}
		set('cambio_renuncia', ctx.myBest);
		const neg = Object.values(pk.boosts).reduce((a, v) => a + Math.min(0, v), 0);
		set('cambio_stats_bajadas', -neg / 2);
		return f;
	}
	if (o.kind !== 'move') { set('otro_estado', 1); return f; }
	const mv = b.dex.moves.get(o.move);
	if (PROTECT.has(mv.id)) {
		set('prot', 1); set('prot_x_peligro', peligro); set('prot_repetido', ctx.stalled);
		set('prot_x_dos_amenazas', ctx.koBy >= 2 ? 1 : ctx.koBy * 0.5); set('prot_turno1', ctx.firstTurn);
		return f;
	}
	if (mv.id === 'fakeout' || mv.id === 'firstimpression') {
		const t = foes[(o.target || 1) - 1];
		set('fakeout', 1);
		const ok = ctx.firstTurn && alive(t) && !(t.hasType && t.hasType('Ghost') && mv.id === 'fakeout') &&
			!S.priorityBlock(b, pk, t, mv.id) && !(mv.id === 'fakeout' && S.flinchIssue(b, pk, t));
		set('fakeout_valido', ok ? 1 : 0);
		set('fakeout_x_amenaza', ok ? ctx.foeThreatLevel[(o.target || 1) - 1] : 0);
		return f;
	}
	if (mv.category !== 'Status' && (mv.basePower || mv.basePowerCallback)) {
		const spread = ['allAdjacentFoes', 'allAdjacent'].includes(mv.target);
		const targets = spread ? foes.filter(alive) : [foes[(o.target || 1) - 1]].filter(alive);
		let dmg = 0, ko = 0, weak = 1;
		for (const t of targets) {
			if (S.priorityBlock(b, pk, t, mv.id)) continue;
			const d = S.estDamage(b, pk, t, mv.id, spread);
			const fr = Math.min(1, d / Math.max(1, t.hp));
			dmg += fr; if (d >= t.hp) ko++;
			if (fr >= 0.15) weak = 0;
		}
		set('atk', 1); set('atk_dmg', dmg); set('atk_ko', ko); set('atk_spread', spread ? 1 : 0);
		set('atk_prio', mv.priority > 0 ? 1 : 0); set('atk_debil', targets.length ? weak : 1);
		if (mv.target === 'allAdjacent' && alive(ally)) set('atk_aliado', Math.min(1, S.estDamage(b, pk, ally, mv.id, true) / Math.max(1, ally.hp)));
		set('atk_x_peligro', peligro); set('atk_x_rapido', ctx.faster);
		if (!spread && o.target) {
			set('atk_objetivo_amenaza', ctx.foeThreatLevel[o.target - 1]);
			const t = foes[o.target - 1];
			if (alive(t)) set('atk_objetivo_tocado', 1 - t.hp / t.maxhp);
		}
		if (PIVOT.has(mv.id)) set('atk_pivote', 1);
		if (S.SPEED_CONTROL_DMG.has(mv.id)) { set('velocidad', 1); set(ctx.slower ? 'velocidad_falta' : 'velocidad_sobra', 1); }
		return f;
	}
	// movimientos de estado
	if (S.SETUP.has(mv.id)) {
		set('preparar', 1); set('preparar_x_peligro', peligro); set('preparar_x_vida', pk.hp / pk.maxhp);
	} else if (STATUS_MOVES.has(mv.id)) {
		const t = foes[(o.target || 1) - 1];
		set('estado', 1);
		const ok = alive(t) && !t.status;
		set('estado_valido', ok ? 1 : 0);
		if (SLEEP.has(mv.id)) set('estado_dormir', ok ? 1 : 0);
		set('estado_x_amenaza', ok ? ctx.foeThreatLevel[(o.target || 1) - 1] : 0);
		if (mv.id === 'thunderwave') { set('velocidad', 1); set(ctx.slower ? 'velocidad_falta' : 'velocidad_sobra', 1); }
	} else if (SPEED_STATUS.has(mv.id)) {
		const tr = !!b.field.pseudoWeather['trickroom'];
		set('velocidad', 1);
		let need = ctx.slower;
		if (mv.id === 'trickroom') need = tr ? !ctx.slower : ctx.slower;
		if (mv.id === 'tailwind' && side.sideConditions['tailwind']) need = false;
		set(need ? 'velocidad_falta' : 'velocidad_sobra', 1);
	} else if (S.REDIRECT.has(mv.id)) {
		set('redireccion', 1); set('redireccion_x_aliado', alive(ally) ? ctx.threat[1 - slot] : 0);
	} else if (SUPPORT.has(mv.id) || PIVOT.has(mv.id)) {
		set('apoyo', 1);
	} else {
		set('otro_estado', 1);
	}
	return f;
}

/** Opciones de cada ranura con sus rasgos (bando s). */
function slotTable(b, s) {
	const out = [];
	for (let slot = 0; slot < 2; slot++) {
		const opts = S.slotOptions(b, s, slot, 'free');
		if (opts.length === 1 && (opts[0].kind === 'pass' || opts[0].kind === 'default')) { out.push(null); continue; }
		const ctx = slotContext(b, s, slot);
		out.push(opts.map(o => ({ choice: o.choice, label: o.label, f: features(b, s, slot, o, ctx) })));
	}
	return out;
}

let MODEL = null;
function loadModel() {
	try {
		const j = JSON.parse(fs.readFileSync(path.join(__dirname, 'experiencia.json'), 'utf8'));
		const idx = (j.names || []).map(n => NAMES.indexOf(n));
		MODEL = (j && Array.isArray(j.w) && !idx.includes(-1)) ? Object.assign({}, j, { idx }) : null;
	} catch (e) { MODEL = null; }
	return MODEL ? { cargado: true, activo: !!MODEL.activo, beta: MODEL.beta || 0 } : { cargado: false };
}
loadModel();

function slotLogp(opts) {
	const sc = opts.map(o => { let z = 0; for (let i = 0; i < MODEL.idx.length; i++) z += MODEL.w[i] * o.f[MODEL.idx[i]]; return z; });
	const m = Math.max(...sc);
	const lse = m + Math.log(sc.reduce((a, z) => a + Math.exp(z - m), 0));
	const out = new Map();
	opts.forEach((o, i) => out.set(o.choice, sc[i] - lse));
	return out;
}

/** log-probabilidad de cada jugada conjunta (lista de "choice" del motor) según la experiencia. */
function jointLogp(b, s, choices) {
	if (!MODEL) return null;
	const tab = slotTable(b, s);
	const lp = tab.map(t => (t ? slotLogp(t) : null));
	return choices.map(c => {
		const parts = c.split(', ');
		let v = 0;
		for (let slot = 0; slot < 2; slot++) {
			if (!lp[slot]) continue;
			const p = (parts[slot] || '').trim();
			if (p === 'pass' || !p) continue;
			const x = lp[slot].get(p);
			v += x === undefined ? -6 : x;
		}
		return v;
	});
}

/** Rasgos de una decisión real (para aprender): opciones de cada ranura y cuál eligió el jugador. */
function decisionTable(b, spec) {
	const tab = slotTable(b, 0);
	const ch = spec ? S.choiceFromSpec(b, 0, spec) : null;
	if (!ch) return null;
	const parts = ch.choice.split(', ');
	return tab.map((t, slot) => {
		if (!t || !spec[slot] || spec[slot].pass) return null;
		const k = t.findIndex(o => o.choice === parts[slot]);
		if (k < 0) return null;
		return { f: t.map(o => o.f.map(x => Math.round(x * 1000) / 1000)), y: k };
	});
}

module.exports = { NAMES, slotTable, decisionTable, jointLogp, loadModel, model: () => MODEL };
