'use strict';
/**
 * Posiciones para ejercicios.
 *  - stateFromBattle(b): convierte un combate del simulador al formato de estado de inject.js
 *  - genPosition(req): genera una posición de entrenamiento
 *      kind 'lead'  -> turno 1 tras el team preview de ambos
 *      kind 'final' -> se juega la partida (jugador heurístico con algo de aleatoriedad) hasta
 *                      que quedan pocos Pokémon vivos (maxAlive por bando)
 *      kind 'medio' -> un turno intermedio al azar (turnos 2-5)
 */
const { PS, dexFor, normSet, toID } = require('./sim');
const { teamPreview } = require('./teampreview');
const { jointCandidates } = require('./search');
const { actionMasks } = require('./encode');

function alive(p) { return p && !p.fainted && p.hp > 0; }

function stateFromBattle(b) {
	const sides = b.sides.map(side => {
		const pokemon = side.pokemon.map(p => {
			const v = p.volatiles || {};
			const st = {
				set: p.set, active: p.isActive && !p.fainted ? p.position : null, fainted: !!p.fainted,
				revealed: !!(p.previouslySwitchedIn > 0 || p.isActive || p.fainted),
				hp: p.hp, status: p.status && p.status !== 'fnt' ? p.status : '',
				boosts: Object.assign({}, p.boosts), mega: !!p.species.isMega,
				item: p.item || '',
				// convención de inject.js: 0 = aún no ha actuado desde que entró (Fake Out funciona)
				activeTurns: p.activeMoveActions > 0 ? Math.max(1, p.activeTurns || 1) : 0,
			};
			if (p.status === 'slp' && p.statusState) st.statusTurns = Math.max(0, 3 - (p.statusState.time || 1));
			if (p.status === 'tox' && p.statusState) st.toxicStage = p.statusState.stage || 1;
			if (v.choicelock) st.choiceLock = v.choicelock.move;
			if (v.taunt) st.taunt = v.taunt.duration || 1;
			if (v.encore) st.encore = { move: v.encore.move, turns: v.encore.duration || 1 };
			if (v.substitute) st.substitute = v.substitute.hp;
			if (v.confusion) st.confusion = v.confusion.time || 1;
			if (v.perishsong) st.perish = v.perishsong.duration || 1;
			if (v.stall) st.protectCount = Math.max(1, Math.round(Math.log(v.stall.counter || 3) / Math.log(3)));
			return st;
		});
		const conds = {};
		for (const id of ['tailwind', 'reflect', 'lightscreen', 'auroraveil', 'safeguard', 'mist']) {
			if (side.sideConditions[id]) conds[id] = side.sideConditions[id].duration || 1;
		}
		const hadStone = side.pokemon.some(p => b.dex.items.get(p.set.item).megaStone);
		return {
			megaUsed: side.pokemon.some(p => p.species.isMega) || (hadStone && !side.pokemon.some(p => p.canMegaEvo)),
			totalFainted: side.totalFainted, conditions: conds, pokemon,
		};
	});
	const field = {};
	if (b.field.weather) { field.weather = b.field.weather; field.weatherTurns = (b.field.weatherState && b.field.weatherState.duration) || 5; }
	if (b.field.terrain) { field.terrain = b.field.terrain; field.terrainTurns = (b.field.terrainState && b.field.terrainState.duration) || 5; }
	for (const pw of ['trickroom', 'gravity']) if (b.field.pseudoWeather[pw]) field[pw] = b.field.pseudoWeather[pw].duration || 1;
	return { turn: b.turn, field, sides };
}

function autoMove(b, s, greedyProb) {
	const side = b.sides[s];
	const id = s ? 'p2' : 'p1';
	if (!side.activeRequest || side.activeRequest.wait || side.isChoiceDone()) return;
	if (!side.activeRequest.forceSwitch && Math.random() < greedyProb) {
		const c = jointCandidates(b, s, 3);
		if (c.length) {
			const pick = c[Math.random() < 0.75 ? 0 : Math.floor(Math.random() * c.length)];
			if (b.choose(id, pick.choice)) return;
		}
	}
	const { masks, choices } = actionMasks(b, s);
	const pick = slot => {
		const legal = masks[slot].map((v, k) => v ? k : -1).filter(k => k >= 0);
		return legal.length ? legal[Math.floor(Math.random() * legal.length)] : 28;
	};
	let a0 = pick(0), a1 = pick(1);
	for (let t = 0; t < 10 && ((a0 >= 24 && a0 < 28 && a0 === a1) || (a0 < 24 && a1 < 24 && a0 % 2 && a1 % 2)); t++) a1 = pick(1);
	if (!b.choose(id, `${choices[0][a0] || 'pass'}, ${choices[1][a1] || 'pass'}`)) b.choose(id, 'default');
}

function bothNeedMoves(b) {
	return b.sides.every(s => s.activeRequest && !s.activeRequest.wait && !s.activeRequest.forceSwitch);
}

function genPosition(req) {
	const format = req.format;
	const dex = dexFor(format);
	const ours = req.ours.map(s => normSet(s, dex));
	const theirs = req.theirs.map(s => normSet(s, dex));
	const kind = req.kind || 'lead';
	const maxAlive = req.maxAlive || 2;
	for (let attempt = 0; attempt < 30; attempt++) {
		const b = new PS.Battle({ formatid: format });
		b.send = () => {};
		b.setPlayer('p1', { name: 'p1', team: PS.Teams.pack(ours) });
		b.setPlayer('p2', { name: 'p2', team: PS.Teams.pack(theirs) });
		const t1 = teamPreview({ format, ours, theirs, temperature: req.previewTemperature === undefined ? 0.08 : req.previewTemperature });
		const t2 = teamPreview({ format, ours: theirs, theirs: ours, temperature: 0.15 });
		b.choose('p1', t1.choice);
		b.choose('p2', t2.choice);
		const target = kind === 'medio' ? 2 + Math.floor(Math.random() * 4) : 1;
		let guard = 0;
		const done = () => {
			if (!bothNeedMoves(b)) return false;
			if (kind === 'lead') return true;
			if (kind === 'medio') return b.turn >= target;
			return b.sides.every(s => s.pokemon.filter(alive).length <= maxAlive) && b.sides.some(s => s.active.filter(alive).length >= 1);
		};
		while (!b.ended && !done() && guard++ < 200 && b.turn < 40) {
			for (let s = 0; s < 2; s++) autoMove(b, s, 0.8);
		}
		if (b.ended || !done()) continue;
		const st = stateFromBattle(b);
		st.format = format;
		return {
			state: st, kind, turn: b.turn,
			oursBrought: b.sides[0].pokemon.map(p => p.species.name),
			theirsBrought: b.sides[1].pokemon.map(p => p.species.name),
			preview: { ours: ours.map(s => s.species), theirs: theirs.map(s => s.species) },
			theirsSets: theirs,
		};
	}
	throw new Error('No pude generar una posición de ese tipo con estos equipos');
}

/**
 * Datos para la interfaz gráfica: lo que se ve en pantalla (como en Showdown) y las opciones de
 * cada ranura propia. Del rival sólo lo que mostraría un Open Team Sheet (sin SP ni naturaleza).
 */
function describe(req) {
	const { buildBattle } = require('./inject');
	const { slotOptions, priorityBlock, flinchIssue } = require('./search');
	const built = buildBattle(req.state);
	const b = built.b;
	const dex = b.dex;
	const mon = (p, mine) => {
		const sp = p.species;
		const out = {
			name: sp.name, baseSpecies: sp.baseSpecies, spriteid: sp.spriteid,
			baseSpriteid: dex.species.get(sp.baseSpecies).spriteid,
			types: p.getTypes(), active: p.isActive && !p.fainted, slot: p.isActive ? p.position : null,
			fainted: !!p.fainted, hpPct: p.fainted ? 0 : Math.max(1, Math.round(100 * p.hp / p.maxhp)),
			status: p.status && p.status !== 'fnt' ? p.status : '', boosts: {},
			item: p.item ? dex.items.get(p.item).name : '', ability: dex.abilities.get(p.ability).name,
			isMega: !!sp.isMega, canMega: !!p.canMegaEvo, baseSpe: sp.baseStats.spe,
			// acaba de entrar y aún no ha actuado: Fake Out / First Impression funcionan
			firstTurn: !!(p.isActive && !p.fainted && p.activeMoveActions === 0),
		};
		for (const k in p.boosts) if (p.boosts[k]) out.boosts[k] = p.boosts[k];
		const foes = b.sides[1 - p.side.n].active;
		out.moves = p.moveSlots.map(ms => {
			const m = dex.moves.get(ms.id);
			const mvo = { id: m.id, name: m.name, type: m.type, category: m.category, basePower: m.basePower,
				accuracy: m.accuracy === true ? '—' : m.accuracy, pp: ms.pp, maxpp: ms.maxpp, target: m.target,
				priority: m.priority, desc: m.shortDesc || m.desc || '' };
			// contra qué rival en campo falla (prioridad bloqueada) o no hace retroceder (Fake Out)
			if (p.isActive && !p.fainted) {
				const blocked = {}, weak = {};
				foes.forEach((f, i) => {
					if (!f || f.fainted) return;
					const why = priorityBlock(b, p, f, m.id);
					if (why) blocked[i + 1] = why;
					else if (m.id === 'fakeout') { const w = flinchIssue(b, p, f); if (w) weak[i + 1] = w; }
				});
				if (Object.keys(blocked).length) mvo.blocked = blocked;
				if (Object.keys(weak).length) mvo.weak = weak;
			}
			return mvo;
		});
		// en el suelo con Campo Psíquico: inmune a los movimientos con prioridad del rival
		out.psyProtected = !!(p.isActive && !p.fainted && b.field.isTerrain('psychicterrain') && p.isGrounded());
		const shield = p.side.active.find(x => x && !x.fainted && ['armortail', 'queenlymajesty', 'dazzling'].includes(x.ability));
		if (p.isActive && !p.fainted && shield) out.prioShield = dex.abilities.get(shield.ability).name;
		if (mine) {
			out.hp = p.hp; out.maxhp = p.maxhp;
			out.stats = Object.assign({ hp: p.maxhp }, p.storedStats);
			out.nature = p.set.nature; out.sp = p.set.evs;
		}
		return out;
	};
	const sides = b.sides.map((side, s) => ({
		megaUsed: !side.pokemon.some(p => p.canMegaEvo) && side.pokemon.some(p => dex.items.get(p.set.item).megaStone),
		conditions: Object.fromEntries(Object.entries(side.sideConditions).map(([k, v]) => [k, v.duration || 0])),
		pokemon: side.pokemon.map(p => mon(p, s === 0)),
	}));
	const field = {
		weather: b.field.weather ? dex.conditions.get(b.field.weather).name : '',
		weatherTurns: b.field.weatherState && b.field.weatherState.duration,
		terrain: b.field.terrain ? dex.conditions.get(b.field.terrain).name : '',
		terrainTurns: b.field.terrainState && b.field.terrainState.duration,
		trickroom: b.field.pseudoWeather.trickroom ? b.field.pseudoWeather.trickroom.duration : 0,
	};
	const options = [0, 1].map(slot => slotOptions(b, 0, slot, 'free').map(o => ({
		choice: o.choice, kind: o.kind, move: o.move || null, target: o.target === undefined ? null : o.target,
		mega: !!o.mega, switchTo: o.switchTo || null, label: o.label,
	})));
	return { turn: req.state.turn, field, sides, options };
}

/**
 * Qué categorías de decisión importan en una posición (para los ejercicios dirigidos).
 */
function positionTags(req) {
	const { buildBattle } = require('./inject');
	const { threatTo, estDamage, priorityBlock } = require('./search');
	const b = buildBattle(req.state).b;
	const me = b.sides[0], foe = b.sides[1];
	const mine = me.active.filter(alive), foes = foe.active.filter(alive);
	const bench = me.pokemon.filter(p => alive(p) && !p.isActive);
	const tags = new Set(['attack']);
	const has = (p, ids) => p.moveSlots.some(m => ids.includes(m.id));
	for (const p of mine) {
		const thr = threatTo(b, 0, p);
		if (has(p, ['protect', 'detect', 'spikyshield', 'kingsshield', 'banefulbunker', 'silktrap', 'burningbulwark']) && thr >= 0.6) tags.add('protect');
		if (bench.length && thr >= 0.85) tags.add('switch');
		if (has(p, ['fakeout', 'firstimpression']) && p.activeMoveActions === 0 && foes.some(f => !f.hasType('Ghost'))) tags.add('fakeout');
		if (p.canMegaEvo) tags.add('mega');
		if (has(p, ['tailwind', 'trickroom', 'icywind', 'electroweb', 'thunderwave', 'scaryface']) && !me.sideConditions['tailwind']) tags.add('speedctrl');
		if (has(p, ['followme', 'ragepowder', 'helpinghand', 'spore', 'sleeppowder', 'willowisp', 'partingshot', 'taunt', 'encore', 'lifedew', 'pollenpuff', 'wideguard'])) tags.add('support');
		for (const ms of p.moveSlots) {
			const mv = b.dex.moves.get(ms.id);
			if (mv.priority > 0 && mv.category !== 'Status' && foes.some(f => priorityBlock(b, p, f, ms.id))) tags.add('priority');
		}
	}
	// foco: dos rivales y al menos uno al alcance de un KO
	if (foes.length === 2) {
		const koable = foes.some(f => mine.some(p => p.moveSlots.some(ms => {
			const mv = b.dex.moves.get(ms.id);
			if (mv.category === 'Status') return false;
			return estDamage(b, p, f, ms.id, ['allAdjacentFoes', 'allAdjacent'].includes(mv.target)) >= f.hp;
		})));
		if (koable) tags.add('target');
	}
	// velocidad: un rival más rápido amenaza con dejar KO a uno de los tuyos antes de que actúe
	for (const p of mine) {
		const fast = foes.some(f => f.getStat('spe', false, false) > p.getStat('spe', false, false));
		if (fast && threatTo(b, 0, p) >= 1) tags.add('priority');
	}
	return { tags: [...tags] };
}

module.exports = { stateFromBattle, genPosition, describe, positionTags };
