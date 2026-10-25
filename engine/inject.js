'use strict';
/**
 * Reconstruye una posición de combate real dentro del simulador de Showdown.
 *
 * Esquema del estado (lo genera vgclab/bot/state.py a partir de poke-env):
 * {
 *   format, turn,
 *   field: { weather, weatherTurns, terrain, terrainTurns, trickroom (turnos restantes), gravity },
 *   sides: [ // 0 = nosotros (p1), 1 = rival (p2)
 *     { megaUsed, totalFainted,
 *       conditions: { tailwind, reflect, lightscreen, auroraveil, safeguard } (turnos restantes),
 *       pokemon: [ { set, active: 0|1|null, hp|hpFrac, fainted, status, statusTurns, toxicStage,
 *                    boosts, mega, item (null = el del set), activeTurns, protectCount,
 *                    choiceLock, taunt, encore: {move, turns}, substitute, confusion, perish } ] }
 *   ]
 * }
 * Las ranuras se conservan: si una ranura está vacía (su Pokémon cayó y no hay banquillo),
 * se coloca un Pokémon debilitado como marcador para que las posiciones de objetivo coincidan.
 */
const { PS, DEFAULT_FORMAT, dexFor, normSet, toID } = require('./sim');

const BOOST_KEYS = ['atk', 'def', 'spa', 'spd', 'spe', 'accuracy', 'evasion'];
const DUMMY = { species: 'Audino', ability: 'Klutz', item: '', moves: ['splash'], nature: 'Serious', level: 50 };

function orderSide(side, dex) {
	const mons = side.pokemon;
	const order = [];
	const placeholders = [];
	const used = new Set();
	for (let slot = 0; slot < 2; slot++) {
		let idx = mons.findIndex((p, i) => p.active === slot && !used.has(i));
		if (idx >= 0 && !mons[idx].fainted) {
			used.add(idx); order.push(mons[idx]);
		} else {
			// ranura vacía: marcador debilitado
			let f = idx >= 0 ? idx : mons.findIndex((p, i) => p.fainted && !used.has(i));
			if (f >= 0) { used.add(f); order.push(Object.assign({}, mons[f], { fainted: true })); }
			else order.push({ set: DUMMY, fainted: true, dummy: true });
			placeholders.push(slot);
		}
	}
	mons.forEach((p, i) => {
		if (!used.has(i) && !p.fainted && p.active !== 0 && p.active !== 1) { used.add(i); order.push(p); }
	});
	// debilitados en el banquillo: se mantienen en el equipo (como en el combate real) hasta 4
	const faintedBench = [];
	mons.forEach((p, i) => {
		if (!used.has(i) && p.fainted && order.length < 4) { used.add(i); faintedBench.push(order.length); order.push(p); }
	});
	return { order, placeholders, faintedBench };
}

function assignVolatile(b, pk, id, data) {
	const dex = b.dex;
	const cond = dex.conditions.get(id);
	pk.volatiles[id] = b.initEffectState(Object.assign({ id: cond.id || id, name: cond.name || id, target: pk }, data));
}

function applyMon(b, pk, p) {
	const dex = b.dex;
	if (p.mega && pk.canMegaEvo) b.actions.runMegaEvo(pk);
	// PS
	if (typeof p.hp === 'number') pk.hp = Math.max(1, Math.min(pk.maxhp, Math.round(p.hp)));
	else if (typeof p.hpFrac === 'number') pk.hp = Math.max(1, Math.min(pk.maxhp, Math.round(pk.maxhp * p.hpFrac)));
	// objeto (null = el del set; '' = consumido/perdido)
	if (p.item !== null && p.item !== undefined) {
		const it = toID(p.item);
		if (it !== pk.item) { pk.item = it; pk.itemState = b.initEffectState({ id: it, target: pk }); }
	}
	// estado
	pk.status = ''; pk.statusState = b.initEffectState({});
	if (p.status && p.status !== 'fnt') {
		pk.setStatus(p.status, pk, null, true);
		if (p.status === 'slp' && typeof p.statusTurns === 'number') {
			pk.statusState.time = Math.max(1, 3 - p.statusTurns);
			pk.statusState.startTime = 3;
		}
		if (p.status === 'tox') pk.statusState.stage = p.toxicStage || 1;
		if (p.status === 'frz' && typeof p.statusTurns === 'number') pk.statusState.time = Math.max(1, 3 - p.statusTurns);
	}
	// cambios de características
	pk.boosts = {};
	for (const k of BOOST_KEYS) pk.boosts[k] = (p.boosts && p.boosts[k]) || 0;
	// volátiles: limpiamos lo que haya dejado la entrada y ponemos lo real
	for (const v in pk.volatiles) { if (!['dynamax'].includes(v)) delete pk.volatiles[v]; }
	const at = typeof p.activeTurns === 'number' ? p.activeTurns : 0;
	pk.activeTurns = at;
	// Sorpresa/Fake Out sólo funciona el primer turno en el campo
	pk.activeMoveActions = at > 0 ? 1 : 0;
	if (p.choiceLock) assignVolatile(b, pk, 'choicelock', { move: toID(p.choiceLock) });
	if (p.taunt) assignVolatile(b, pk, 'taunt', { duration: p.taunt });
	if (p.encore && p.encore.move) {
		pk.lastMove = dex.getActiveMove(p.encore.move);
		assignVolatile(b, pk, 'encore', { move: toID(p.encore.move), duration: p.encore.turns || 3 });
	}
	if (p.substitute) assignVolatile(b, pk, 'substitute', { hp: p.substitute });
	if (p.confusion) assignVolatile(b, pk, 'confusion', { time: p.confusion });
	if (p.perish) assignVolatile(b, pk, 'perishsong', { duration: p.perish });
	if (p.protectCount) assignVolatile(b, pk, 'stall', { counter: Math.min(729, Math.pow(3, p.protectCount)), duration: 2 });
	if (p.lastMove) pk.lastMove = dex.getActiveMove(p.lastMove);
}

function applyField(b, field) {
	field = field || {};
	const src = b.getAllActive()[0] || b.sides[0].pokemon[0];
	if (field.weather) {
		const w = toID(field.weather);
		if (b.field.weather !== w) b.field.setWeather(w, src, b.dex.conditions.get(w));
		if (b.field.weatherState) b.field.weatherState.duration = field.weatherTurns || 5;
	} else if (b.field.weather) b.field.clearWeather();
	if (field.terrain) {
		const t = toID(field.terrain);
		if (b.field.terrain !== t) b.field.setTerrain(t, src, b.dex.conditions.get(t));
		if (b.field.terrainState) b.field.terrainState.duration = field.terrainTurns || 5;
	} else if (b.field.terrain) b.field.clearTerrain();
	for (const pw of ['trickroom', 'gravity']) {
		if (field[pw]) {
			if (!b.field.pseudoWeather[pw]) b.field.addPseudoWeather(pw, src);
			if (b.field.pseudoWeather[pw]) b.field.pseudoWeather[pw].duration = field[pw];
		} else if (b.field.pseudoWeather[pw]) b.field.removePseudoWeather(pw);
	}
}

function applySideConditions(b, side, conds) {
	conds = conds || {};
	for (const id of ['tailwind', 'reflect', 'lightscreen', 'auroraveil', 'safeguard', 'mist']) {
		if (conds[id]) {
			if (!side.sideConditions[id]) side.addSideCondition(id, 'debug');
			if (side.sideConditions[id]) side.sideConditions[id].duration = conds[id];
		} else if (side.sideConditions[id]) side.removeSideCondition(id);
	}
}

/**
 * Construye el combate. Devuelve { b, maps } donde maps[s] = lista de objetos de estado en el
 * orden del equipo inyectado (para traducir cambios de vuelta a nombres reales).
 */
function buildBattle(state, seed = '1,2,3,4') {
	const format = state.format || DEFAULT_FORMAT;
	const dex = dexFor(format);
	const b = new PS.Battle({ formatid: format, seed });
	const layouts = state.sides.map(side => orderSide(side, dex));
	layouts.forEach((lay, s) => {
		const sets = lay.order.map(p => normSet(p.set, dex));
		b.setPlayer(s === 0 ? 'p1' : 'p2', { name: s === 0 ? 'p1' : 'p2', team: PS.Teams.pack(sets) });
	});
	for (let s = 0; s < 2; s++) {
		const n = Math.min(4, layouts[s].order.length);
		b.choose(s === 0 ? 'p1' : 'p2', 'team ' + Array.from({ length: n }, (_, i) => i + 1).join(''));
	}
	// Mega ya usada en el equipo: bloquear para todos
	for (let s = 0; s < 2; s++) {
		const side = b.sides[s], st = state.sides[s], lay = layouts[s];
		lay.order.forEach((p, i) => {
			const pk = side.pokemon.find(x => x.position === i) || side.pokemon[i];
			if (!pk || p.fainted) return;
			applyMon(b, pk, p);
		});
		if (st.megaUsed || lay.order.some(p => p.mega)) for (const pk of side.pokemon) pk.canMegaEvo = false;
	}
	// marcadores debilitados y debilitados del banquillo
	for (let s = 0; s < 2; s++) {
		const side = b.sides[s];
		for (const slot of layouts[s].placeholders) {
			const pk = side.active[slot];
			if (pk && !pk.fainted) { pk.hp = 0; pk.faint(); }
		}
		for (const idx of layouts[s].faintedBench || []) {
			const pk = side.pokemon[idx];
			if (pk && !pk.fainted && !pk.isActive) {
				pk.hp = 0; pk.fainted = true; pk.status = 'fnt';
				if (side.pokemonLeft) side.pokemonLeft--;
			}
		}
	}
	b.resolveFaints({ checkWin: false });
	for (let s = 0; s < 2; s++) {
		const side = b.sides[s], st = state.sides[s];
		side.faintedThisTurn = null;
		if (typeof st.totalFainted === 'number') side.totalFainted = st.totalFainted;
		applySideConditions(b, side, st.conditions);
	}
	applyField(b, state.field);
	if (b.ended) return { b, layouts, ended: true };
	// como al final de cada turno real: efectos que bloquean movimientos (Mofa, bloqueo de elección...) y los
	// del propio movimiento (en Champions, Fake Out queda bloqueado si ya ha actuado desde que entró)
	for (const side of b.sides) {
		for (const pk of side.active) {
			if (!pk || pk.fainted) continue;
			for (const ms of pk.moveSlots) { ms.disabled = false; ms.disabledSource = ''; }
			b.runEvent('DisableMove', pk);
			for (const ms of pk.moveSlots) b.singleEvent('DisableMove', b.dex.getActiveMove(ms.id), null, pk);
		}
	}
	b.makeRequest('move');
	// Movimientos bloqueados según la petición REAL del servidor (bloqueo de elección, Anulación, Mofa...)
	for (let s = 0; s < 2; s++) {
		const req = b.sides[s].activeRequest;
		if (!req || !req.active) continue;
		layouts[s].order.slice(0, 2).forEach((p, slot) => {
			if (!p || !p.disabledMoves || !req.active[slot]) return;
			const dis = new Set(p.disabledMoves.map(toID));
			for (const m of req.active[slot].moves) if (dis.has(m.id)) m.disabled = true;
		});
	}
	return { b, layouts, ended: false };
}

module.exports = { buildBattle, orderSide };
