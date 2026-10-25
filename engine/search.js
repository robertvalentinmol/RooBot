'use strict';
/**
 * Búsqueda de 1 turno como juego simultáneo (estilo mikumiku37):
 *  1. Para cada bando se enumeran las acciones conjuntas legales (2 ranuras + objetivos + Mega + cambios).
 *  2. Un prior heurístico barato (daño esperado, KO, amenazas, control de velocidad) elige el top-K.
 *  3. Cada celda (nuestra acción i, su acción j) se simula con el motor REAL de Showdown
 *     en varios "mundos" (banquillo rival desconocido, repartos estimados) y semillas de RNG.
 *  4. El resultado se evalúa con una función de valor (heurística, o red neuronal desde Python).
 *  5. La matriz KxK se resuelve con regret matching+ -> estrategia mixta (equilibrio aproximado).
 */
const { PS, State, toID } = require('./sim');
const { buildBattle } = require('./inject');

const PROTECT_MOVES = new Set(['protect', 'detect', 'spikyshield', 'kingsshield', 'banefulbunker', 'silktrap', 'burningbulwark', 'obstruct', 'maxguard']);
const REDIRECT = new Set(['followme', 'ragepowder', 'spotlight']);
const SPEED_CONTROL_DMG = new Set(['icywind', 'electroweb', 'bulldoze', 'rocktomb', 'mudshot', 'scaryface', 'stringshot', 'cottonspore', 'thunderwave', 'nuzzle']);
const SETUP = new Set(['swordsdance', 'nastyplot', 'calmmind', 'dragondance', 'bulkup', 'quiverdance', 'shellsmash', 'coil', 'irondefense', 'tidyup', 'victorydance', 'bellydrum']);

// ---------------------------------------------------------------- utilidades
function foesOf(b, s) { return b.sides[1 - s].active; }
function alive(p) { return p && !p.fainted && p.hp > 0; }

function estDamage(b, att, def, moveId, spread) {
	const move = b.dex.getActiveMove(moveId);
	move.willCrit = false;
	const saved = b.randomizer;
	b.randomizer = function (d) { return this.trunc(this.trunc(d * 92) / 100); };
	// las bayas de resistencia se consumen dentro de getDamage: guardar y restaurar objetos
	const snap = p => [p.item, Object.assign({}, p.itemState), p.lastItem, p.ateBerry, p.usedItemThisTurn];
	const back = (p, x) => { p.item = x[0]; p.itemState = x[1]; p.lastItem = x[2]; p.ateBerry = x[3]; p.usedItemThisTurn = x[4]; };
	const sa = snap(att), sd = snap(def);
	let dmg = 0;
	try {
		b.setActiveMove(move, att, def);
		b.singleEvent('ModifyType', move, null, att, def, move, move);
		b.singleEvent('ModifyMove', move, null, att, def, move, move);
		let m = b.runEvent('ModifyType', att, def, move, move) || move;
		m = b.runEvent('ModifyMove', att, def, m, m) || m;
		if (spread) m.spreadHit = true;
		m.hit = 1;
		const d = b.actions.getDamage(att, def, m, true);
		dmg = typeof d === 'number' ? d : 0;
		let hits = 1;
		if (Array.isArray(m.multihit)) hits = (m.multihit[0] + m.multihit[1]) / 2;
		else if (typeof m.multihit === 'number') hits = m.multihit;
		dmg *= hits;
		const acc = m.accuracy === true ? 1 : (m.accuracy || 100) / 100;
		dmg *= acc;
	} catch (e) { dmg = 0; }
	back(att, sa); back(def, sd);
	b.randomizer = saved;
	b.clearActiveMove(true);
	return dmg;
}

/**
 * Por qué un movimiento con prioridad de "att" no puede tocar a "def" (o null si puede):
 * Campo Psíquico (si def está en el suelo) o Armor Tail / Queenly Majesty / Dazzling en su bando.
 */
const PRIORITY_SHIELDS = ['armortail', 'queenlymajesty', 'dazzling'];
function priorityBlock(b, att, def, moveId) {
	if (!att || !def || def.fainted || def.side === att.side) return null;
	const mv = b.dex.moves.get(moveId);
	if (!(mv.priority > 0)) return null;
	// sólo los que van contra el rival (Protect, Follow Me... también tienen prioridad)
	if (!['normal', 'any', 'adjacentFoe', 'allAdjacentFoes', 'allAdjacent', 'randomNormal'].includes(mv.target)) return null;
	if (b.field.isTerrain('psychicterrain') && def.isGrounded()) return 'Campo Psíquico';
	const moldBreaker = ['moldbreaker', 'teravolt', 'turboblaze'].includes(att.ability);
	if (!moldBreaker) {
		for (const p of def.side.active) {
			if (p && !p.fainted && PRIORITY_SHIELDS.includes(p.ability)) return b.dex.abilities.get(p.ability).name;
		}
	}
	return null;
}
/** Problemas de Fake Out contra "def" además de la prioridad: no le afecta o no le hace retroceder. */
function flinchIssue(b, att, def) {
	if (!def || def.fainted) return null;
	if (def.hasType('Ghost') && !['scrappy', 'mindseye'].includes(att.ability)) return 'Fantasma: no le afecta';
	if (['innerfocus', 'shielddust'].includes(def.ability)) return b.dex.abilities.get(def.ability).name + ': no retrocede';
	if (def.item === 'covertcloak') return 'Covert Cloak: no retrocede';
	return null;
}

function threatTo(b, s, target) {
	// máximo daño esperado (fracción de PS actuales) que el rival puede hacer a "target"
	let best = 0;
	for (const f of foesOf(b, s)) {
		if (!alive(f)) continue;
		for (const ms of f.moveSlots) {
			const mv = b.dex.moves.get(ms.id);
			if (mv.category === 'Status' || !mv.basePower && !mv.basePowerCallback) continue;
			const spread = ['allAdjacentFoes', 'allAdjacent'].includes(mv.target);
			const d = estDamage(b, f, target, ms.id, spread);
			best = Math.max(best, Math.min(1, d / Math.max(1, target.hp)));
		}
	}
	return best;
}

function sideSpeed(b, s) {
	const sp = b.sides[s].active.filter(alive).map(p => p.getStat('spe', false, false));
	return sp.length ? sp.reduce((a, c) => a + c, 0) / sp.length : 0;
}

// ---------------------------------------------------------------- enumeración
/**
 * Opciones por ranura: { choice, kind, move, target, mega, switchTo, label }
 */
/**
 * Mega "principal" del bando: la de mayor total de estadísticas en forma Mega entre las que
 * aún pueden megaevolucionar. Política: la principal evoluciona en cuanto puede actuar;
 * las demás sólo si la principal ya no está disponible (opcional).
 */
function preferredMega(b, s) {
	let best = null, bestBST = -1;
	for (const p of b.sides[s].pokemon) {
		if (!alive(p) || !p.canMegaEvo) continue;
		const sp = b.dex.species.get(p.canMegaEvo);
		if (sp.bst > bestBST) { bestBST = sp.bst; best = p; }
	}
	return best;
}

function slotOptions(b, s, slot, megaPolicy = 'asap') {
	const side = b.sides[s];
	const req = side.activeRequest;
	const pk = side.active[slot];
	if (!req || req.wait || !alive(pk) || !req.active || !req.active[slot]) return [{ choice: 'pass', kind: 'pass', label: 'pass' }];
	const ar = req.active[slot];
	const opts = [];
	const allyAlive = alive(side.active[1 - slot]);
	const foes = foesOf(b, s);
	let megaFlags = [false];
	if (ar.canMegaEvo) {
		const pref = preferredMega(b, s);
		if (megaPolicy === 'free') megaFlags = [true, false];
		else if (pref === pk) megaFlags = [true];
		else if (pref && pref.isActive) megaFlags = [false];
		else megaFlags = [true, false];
	}
	ar.moves.forEach((m, mi) => {
		if (m.disabled || (m.pp !== undefined && m.pp <= 0)) return;
		const mv = b.dex.moves.get(m.id);
		let targets = [null];
		if (['normal', 'any', 'adjacentFoe'].includes(m.target)) {
			targets = [];
			foes.forEach((f, fi) => { if (alive(f)) targets.push(fi + 1); });
			if (!targets.length) targets = [1];
			const allyOk = mv.category === 'Status' || ['pollenpuff', 'beatup', 'skillswap'].includes(mv.id);
			if (m.target !== 'adjacentFoe' && allyAlive && allyOk && mv.category === 'Status') targets.push(-(2 - slot));
		} else if (m.target === 'adjacentAlly') {
			if (!allyAlive) return;
			targets = [-(2 - slot)];
		} else if (m.target === 'adjacentAllyOrSelf') {
			targets = [-(slot + 1)];
		}
		for (const t of targets) {
			for (const mega of megaFlags) {
				let choice = `move ${m.id}`;
				if (t !== null) choice += ` ${t}`;
				if (mega) choice += ' mega';
				opts.push({ choice, kind: 'move', move: m.id, target: t, mega, slot,
					label: `${pk.species.name}: ${mv.name}${t !== null ? (t > 0 ? ' → ' + (foes[t - 1] ? foes[t - 1].species.name : t) : ' → aliado') : ''}${mega ? ' (Mega)' : ''}` });
			}
		}
	});
	if (!ar.trapped) {
		side.pokemon.forEach((p) => {
			if (p.isActive || side.active.includes(p) || !alive(p)) return;
			opts.push({ choice: `switch ${p.name}`, kind: 'switch', switchTo: p.name, slot, label: `${pk.species.name} ⇄ ${p.species.name}` });
		});
	}
	if (!opts.length) opts.push({ choice: 'default', kind: 'default', label: 'default' });
	return opts;
}

// ---------------------------------------------------------------- prior heurístico
function priorSlot(b, s, slot, o, ctx) {
	const side = b.sides[s];
	const pk = side.active[slot];
	if (o.kind === 'pass') return 0;
	if (o.kind === 'switch') {
		const cand = side.pokemon.find(p => p.name === o.switchTo);
		const now = ctx.threat[slot];
		const then = cand ? threatTo(b, s, cand) * (cand.hp / cand.maxhp) : 1;
		return 0.05 + 0.35 * Math.max(-1, Math.min(1, now - then)) - 0.1;
	}
	if (o.kind !== 'move') return 0;
	const mv = b.dex.moves.get(o.move);
	let score = 0;
	const foes = foesOf(b, s);
	const ally = side.active[1 - slot];
	if (PROTECT_MOVES.has(mv.id)) {
		const stalled = pk.volatiles['stall'] ? 1 : 0;
		score = 0.4 * ctx.threat[slot] + 0.05 - 0.6 * stalled;
	} else if (mv.id === 'fakeout' || mv.id === 'firstimpression') {
		if (pk.activeMoveActions > 0) return -1;
		const t = foes[(o.target || 1) - 1];
		score = alive(t) ? 0.35 + 0.15 * ctx.foeThreatLevel[(o.target || 1) - 1] : -0.5;
		if (t && (t.hasType('Ghost') && mv.id === 'fakeout')) score = -0.5;
		if (t && priorityBlock(b, pk, t, mv.id)) return -0.6;           // Campo Psíquico, Armor Tail...
		if (t && mv.id === 'fakeout' && flinchIssue(b, pk, t)) score -= 0.3;   // no retrocede
		if (t) score += 0.5 * Math.min(1, estDamage(b, pk, t, mv.id, false) / Math.max(1, t.hp));
	} else if (mv.category !== 'Status' && (mv.basePower || mv.basePowerCallback)) {
		const spread = ['allAdjacentFoes', 'allAdjacent'].includes(mv.target);
		let total = 0;
		const targets = spread ? foes.filter(alive) : [foes[(o.target || 1) - 1]].filter(alive);
		for (const t of targets) {
			if (priorityBlock(b, pk, t, mv.id)) continue;
			const d = estDamage(b, pk, t, mv.id, spread);
			const frac = Math.min(1, d / Math.max(1, t.hp));
			total += frac + (d >= t.hp ? 0.45 : 0);
		}
		if (mv.target === 'allAdjacent' && alive(ally)) {
			const d = estDamage(b, pk, ally, mv.id, true);
			total -= 1.2 * Math.min(1, d / Math.max(1, ally.hp)) + (d >= ally.hp ? 0.6 : 0);
		}
		if (mv.priority > 0) total += 0.05;
		if (SPEED_CONTROL_DMG.has(mv.id) && !b.field.pseudoWeather['trickroom']) total += 0.12;
		if (mv.self && mv.self.boosts && Object.values(mv.self.boosts).some(v => v < 0)) total -= 0.08;
		score = total;
	} else {
		// movimientos de estado
		const tr = !!b.field.pseudoWeather['trickroom'];
		const slower = sideSpeed(b, s) < sideSpeed(b, 1 - s);
		switch (mv.id) {
			case 'tailwind': score = side.sideConditions['tailwind'] || tr ? -0.3 : (slower ? 0.5 : 0.2); break;
			case 'trickroom': score = tr ? (slower ? -0.6 : 0.35) : (slower ? 0.6 : -0.1); break;
			case 'helpinghand': score = alive(ally) ? 0.18 : -1; break;
			case 'followme': case 'ragepowder': case 'spotlight': score = alive(ally) ? 0.1 + 0.35 * ctx.threat[1 - slot] : 0; break;
			case 'partingshot': score = 0.25; break;
			case 'willowisp': case 'thunderwave': case 'spore': case 'sleeppowder': case 'yawn': case 'darkvoid': case 'hypnosis': {
				const t = foes[(o.target || 1) - 1];
				score = t && alive(t) && !t.status ? 0.35 : -0.3; break;
			}
			case 'encore': case 'taunt': case 'disable': score = 0.15; break;
			case 'lifedew': case 'pollenpuff': case 'healpulse': case 'recover': case 'roost': case 'slackoff': case 'moonlight': case 'synthesis':
				score = 0.3 * (1 - pk.hp / pk.maxhp); break;
			case 'perishsong': score = 0.15; break;
			case 'reflect': case 'lightscreen': case 'auroraveil': score = side.sideConditions[mv.id] ? -0.3 : 0.3; break;
			case 'sunnyday': case 'raindance': case 'sandstorm': case 'snowscape': score = 0.15; break;
			case 'wideguard': case 'quickguard': score = 0.1; break;
			default:
				score = SETUP.has(mv.id) ? 0.25 - 0.4 * ctx.threat[slot] : 0.08;
		}
	}
	if (o.mega) score += 0.3;
	return score;
}

function jointCandidates(b, s, K, megaPolicy = 'asap') {
	const side = b.sides[s];
	const ctx = { threat: [0, 0], foeThreatLevel: [0, 0] };
	for (let i = 0; i < 2; i++) if (alive(side.active[i])) ctx.threat[i] = threatTo(b, s, side.active[i]);
	for (let i = 0; i < 2; i++) {
		const f = b.sides[1 - s].active[i];
		// amenaza que supone el rival i: lo máximo que puede quitarnos
		if (alive(f)) {
			let best = 0;
			for (const p of side.active.filter(alive)) {
				for (const ms of f.moveSlots) {
					const mv = b.dex.moves.get(ms.id);
					if (mv.category === 'Status') continue;
					const d = estDamage(b, f, p, ms.id, ['allAdjacentFoes', 'allAdjacent'].includes(mv.target));
					best = Math.max(best, Math.min(1, d / Math.max(1, p.hp)));
				}
			}
			ctx.foeThreatLevel[i] = best;
		}
	}
	const o0 = slotOptions(b, s, 0, megaPolicy).map(o => Object.assign(o, { prior: priorSlot(b, s, 0, o, ctx) }));
	const o1 = slotOptions(b, s, 1, megaPolicy).map(o => Object.assign(o, { prior: priorSlot(b, s, 1, o, ctx) }));
	const joints = [];
	for (const a of o0) {
		for (const c of o1) {
			if (a.kind === 'switch' && c.kind === 'switch' && a.switchTo === c.switchTo) continue;
			if (a.mega && c.mega) continue;
			let sc = a.prior + c.prior;
			if (a.kind === 'move' && c.kind === 'move') {
				if (PROTECT_MOVES.has(a.move) && PROTECT_MOVES.has(c.move)) sc -= 0.25;
				if (a.move === 'fakeout' && c.move === 'fakeout' && a.target === c.target) sc -= 0.4;
				if (a.target && a.target === c.target && a.target > 0) sc += 0.05; // foco
			}
			if (a.kind === 'switch' && c.kind === 'switch') sc -= 0.15;
			joints.push({ choice: `${a.choice}, ${c.choice}`, slots: [a, c], prior: sc,
				label: `${a.label} | ${c.label}` });
		}
	}
	joints.sort((x, y) => y.prior - x.prior);
	// selección diversa: penaliza repetir la misma acción de una ranura
	const top = [];
	const used = [new Map(), new Map()];
	const pool = joints.slice();
	while (top.length < K && pool.length) {
		let bi = 0, bv = -Infinity;
		for (let i = 0; i < pool.length; i++) {
			const j = pool[i];
			const pen = 0.18 * ((used[0].get(j.slots[0].choice) || 0) + (used[1].get(j.slots[1].choice) || 0));
			const v = j.prior - pen;
			if (v > bv) { bv = v; bi = i; }
		}
		const pick = pool.splice(bi, 1)[0];
		top.push(pick);
		for (let k = 0; k < 2; k++) used[k].set(pick.slots[k].choice, (used[k].get(pick.slots[k].choice) || 0) + 1);
	}
	// asegurar al menos una opción con Protección si existe
	if (!top.some(j => j.slots.some(o => o.kind === 'move' && PROTECT_MOVES.has(o.move)))) {
		const p = joints.find(j => j.slots.some(o => o.kind === 'move' && PROTECT_MOVES.has(o.move)));
		if (p && top.length >= K) top[K - 1] = p; else if (p) top.push(p);
	}
	return top;
}

// ---------------------------------------------------------------- valor heurístico
/**
 * Parámetros del juez de siempre. Se pueden ajustar con engine/parametros.json (lo escriben los experimentos cuando
 * un cambio mejora el acuerdo con jugadores fuertes en los dos bancos de prueba) y, para probar, con ROOBOT_PARAMS.
 */
const HAND = Object.assign({ par: 0.85, brn: 0.85, brnFis: 0.72, slp: 0.7, frz: 0.6, psn: 0.93, tox: 0.88,
	off: 0.07, spe: 0.05, def: 0.03, hpBase: 0.45, tw: 0.18, screen: 0.08, tr: 0.25 }, (() => {
	let o = {};
	try {
		const j = JSON.parse(require('fs').readFileSync(require('path').join(__dirname, 'parametros.json'), 'utf8'));
		if (j && j.activo && j.p) o = Object.assign(o, j.p);
	} catch (e) { /* sin ajustes */ }
	try { if (process.env.ROOBOT_PARAMS) o = Object.assign(o, JSON.parse(process.env.ROOBOT_PARAMS)); } catch (e) { /* JSON malo */ }
	return o;
})());
const STATUS_MUL = { par: HAND.par, brn: HAND.brn, slp: HAND.slp, frz: HAND.frz, psn: HAND.psn, tox: HAND.tox };
function monValue(b, p) {
	if (!p || p.fainted || p.hp <= 0) return 0;
	let v = HAND.hpBase + (1 - HAND.hpBase) * (p.hp / p.maxhp);
	if (p.status) {
		let m = STATUS_MUL[p.status] || 1;
		if (p.status === 'brn' && p.storedStats.atk > p.storedStats.spa) m = HAND.brnFis;
		v *= m;
	}
	const off = Math.max(p.boosts.atk, p.boosts.spa);
	const bb = HAND.off * off + HAND.spe * p.boosts.spe + HAND.def * (p.boosts.def + p.boosts.spd);
	v *= 1 + Math.max(-0.35, Math.min(0.45, bb));
	if (p.volatiles['perishsong']) v *= 0.35 + 0.2 * (p.volatiles['perishsong'].duration || 0);
	return v;
}
function sideScore(b, s) {
	const side = b.sides[s];
	let sc = 0;
	for (const p of side.pokemon) sc += monValue(b, p);
	const tw = side.sideConditions['tailwind'];
	if (tw) sc += HAND.tw * Math.min(4, tw.duration || 0) / 4;
	for (const sc2 of ['reflect', 'lightscreen', 'auroraveil']) if (side.sideConditions[sc2]) sc += HAND.screen;
	return sc;
}
/** Variante de evaluación activa (0 = la de siempre; 1 = con amenazas de KO, velocidad y Mega). */
let EVAL_VARIANT = 0;
function setEvalVariant(v) { EVAL_VARIANT = v | 0; }
function actionSpeed(b, p) {
	let v = p.getStat('spe', false, false);
	if (b.field.pseudoWeather['trickroom']) v = 10000 - v;
	return v;
}
/** Términos tácticos: quién puede dejar KO antes de que el otro actúe, ventaja de velocidad y Mega por usar. */
function tacticalTerms(b, s) {
	const mine = b.sides[s].active.filter(alive), theirs = b.sides[1 - s].active.filter(alive);
	let speed = 0, threat = 0;
	for (const a of mine) for (const d of theirs) {
		const sa = actionSpeed(b, a), sd = actionSpeed(b, d);
		speed += sa > sd ? 1 : sa < sd ? -1 : 0;
		// el más rápido de la pareja amenaza KO antes de que el otro actúe
		const [fast, slow, sign] = sa > sd ? [a, d, 1] : sd > sa ? [d, a, -1] : [null, null, 0];
		if (!fast) continue;
		let best = 0;
		for (const ms of fast.moveSlots) {
			const mv = b.dex.moves.get(ms.id);
			if (mv.category === 'Status' || ms.pp <= 0) continue;
			const dmg = estDamage(b, fast, slow, ms.id, ['allAdjacentFoes', 'allAdjacent'].includes(mv.target));
			best = Math.max(best, dmg / Math.max(1, slow.hp));
		}
		if (best >= 1) threat += sign * (0.45 + 0.55 * slow.hp / slow.maxhp) * 0.5;
	}
	const n = Math.max(1, mine.length * theirs.length);
	const megaLeft = sd => sd.pokemon.some(p => alive(p) && p.canMegaEvo) ? 1 : 0;
	return 0.15 * speed / n + threat + 0.1 * (megaLeft(b.sides[s]) - megaLeft(b.sides[1 - s]));
}
// ---------------------------------------------------------------- juez aprendido
/**
 * Rasgos de una posición desde el bando s (todos como diferencia «mío − suyo», así f(s) = −f(1−s)).
 * Con ellos, calibrar.py ajusta por regresión logística unos pesos que predicen quién gana, usando
 * posiciones reales de repeticiones. Si hay pesos validados (engine/juez.json), evaluate() los usa.
 */
const JUDGE_FEATURES = ['vivos', 'ps', 'estados', 'ataque+', 'velocidad+', 'defensa+', 'viento_afin', 'espacio_raro',
	'pantallas', 'mega_por_usar', 'en_campo', 'mas_rapido', 'amenaza_ko',
	// rasgos finos (v2)
	'quemado_fisico', 'paralizado', 'dormido', 'envenenado', 'pantallas_util', 'banda_intacta', 'baya', 'intimidate_banco',
	'fakeout_banco', 'redireccion', 'clima_campo', 'amenaza_banco', 'ps_mejor',
	// emparejamientos (v4): cuánto daño hace cada bando con lo que tiene en el campo y en el banquillo
	'presion', 'presion_banco'];
const WEATHER_GOOD = {
	raindance: { types: ['Water'], moveType: 'Water', abil: ['swiftswim', 'raindish', 'hydration'] },
	primordialsea: { types: ['Water'], moveType: 'Water', abil: ['swiftswim'] },
	sunnyday: { types: ['Fire'], moveType: 'Fire', abil: ['chlorophyll', 'solarpower', 'protosynthesis', 'flowergift', 'leafguard'] },
	desolateland: { types: ['Fire'], moveType: 'Fire', abil: ['chlorophyll', 'solarpower', 'protosynthesis'] },
	sandstorm: { types: ['Rock'], moveType: null, abil: ['sandrush', 'sandforce', 'sandveil'] },
	snowscape: { types: ['Ice'], moveType: null, abil: ['slushrush', 'icebody', 'snowcloak'] },
	snow: { types: ['Ice'], moveType: null, abil: ['slushrush', 'icebody', 'snowcloak'] },
};
const TERRAIN_GOOD = {
	electricterrain: { moveType: 'Electric', abil: ['surgesurfer', 'quarkdrive', 'hadronengine'] },
	grassyterrain: { moveType: 'Grass', abil: ['grasspelt'] },
	psychicterrain: { moveType: 'Psychic', abil: [] },
	mistyterrain: { moveType: null, abil: [] },
};
function hasMoveType(b, p, type) {
	return p.moveSlots.some(ms => { const m = b.dex.moves.get(ms.id); return m.category !== 'Status' && m.type === type; });
}
function isPhysical(p) { return p.storedStats.atk > p.storedStats.spa; }
/** Fase de la partida según los Pokémon que quedan entre los dos bandos. */
function judgePhase(b) {
	const n = b.sides[0].pokemon.filter(alive).length + b.sides[1].pokemon.filter(alive).length;
	return n >= 7 ? 'inicio' : n >= 5 ? 'medio' : 'final';
}
function judgeFeatures(b, s) {
	const live = sd => sd.pokemon.filter(alive);
	const per = (sd, f) => live(sd).reduce((a, p) => a + f(p), 0);
	const d = f => per(b.sides[s], f) - per(b.sides[1 - s], f);
	const me = b.sides[s], op = b.sides[1 - s];
	const dur = (sd, id) => sd.sideConditions[id] ? Math.min(4, sd.sideConditions[id].duration || 1) : 0;
	let tr = 0;
	const trc = b.field.pseudoWeather['trickroom'];
	if (trc) {
		const a = sideSpeed(b, s), c = sideSpeed(b, 1 - s);
		tr = (a < c ? 1 : a > c ? -1 : 0) * Math.min(4, trc.duration || 1) / 4;
	}
	const screens = sd => ['reflect', 'lightscreen', 'auroraveil'].reduce((a, id) => a + (sd.sideConditions[id] ? 1 : 0), 0);
	const megaLeft = sd => sd.pokemon.some(p => alive(p) && p.canMegaEvo) ? 1 : 0;
	// velocidad y amenaza de KO entre los que están en el campo
	const mine = me.active.filter(alive), theirs = op.active.filter(alive);
	let speed = 0, threat = 0;
	for (const a of mine) for (const c of theirs) {
		const sa = actionSpeed(b, a), sc = actionSpeed(b, c);
		speed += sa > sc ? 1 : sa < sc ? -1 : 0;
		const [fast, slow, sign] = sa > sc ? [a, c, 1] : sc > sa ? [c, a, -1] : [null, null, 0];
		if (!fast) continue;
		if (bestDamage(b, fast, slow) >= Math.max(1, slow.hp)) threat += sign;
	}
	const n = Math.max(1, mine.length * theirs.length);
	// ---- rasgos finos
	const ab = p => p.ability || p.baseAbility || '';
	const hasMove = (p, ids) => p.moveSlots.some(ms => ids.includes(ms.id));
	const usefulScreens = (sd, foe) => {
		let v = 0;
		const att = foe.pokemon.filter(alive);
		const phys = att.filter(isPhysical).length, spec = att.length - phys;
		const t = id => sd.sideConditions[id] ? Math.min(5, sd.sideConditions[id].duration || 1) / 5 : 0;
		v += t('reflect') * phys + t('lightscreen') * spec + t('auroraveil') * att.length;
		return v / 4;
	};
	const fieldGood = (sd) => {
		let v = 0;
		const w = WEATHER_GOOD[b.field.weather], tr2 = TERRAIN_GOOD[b.field.terrain];
		const wd = b.field.weather ? Math.min(5, (b.field.weatherState && b.field.weatherState.duration) || 5) / 5 : 0;
		const td = b.field.terrain ? Math.min(5, (b.field.terrainState && b.field.terrainState.duration) || 5) / 5 : 0;
		for (const p of sd.pokemon) {
			if (!alive(p)) continue;
			if (w && (w.abil.includes(ab(p)) || (w.moveType ? hasMoveType(b, p, w.moveType) : p.getTypes().some(t => w.types.includes(t))))) v += wd;
			if (tr2 && (tr2.abil.includes(ab(p)) || (tr2.moveType && hasMoveType(b, p, tr2.moveType)))) {
				let grounded = true;
				try { grounded = p.isGrounded(); } catch (e) {}
				if (grounded) v += td;
			}
		}
		return v / 4;
	};
	const benchThreat = (sd, foe) => {
		let c = 0;
		const targets = foe.active.filter(alive);
		for (const p of sd.pokemon) {
			if (!alive(p) || p.isActive) continue;
			for (const t of targets) {
				let dmg = 0;
				try { dmg = bestDamage(b, p, t); } catch (e) {}
				if (dmg >= Math.max(1, t.hp)) { c++; break; }
			}
		}
		return c / 2;
	};
	const bestHp = sd => live(sd).reduce((a, p) => Math.max(a, p.hp / p.maxhp), 0);
	// presión: el mejor daño (en % de su vida máxima) que cada Pokémon hace a alguno de los rivales en el campo
	const press = (sd, foe, bench) => {
		let tot = 0;
		const targets = foe.active.filter(alive);
		for (const a of sd.pokemon) {
			if (!alive(a) || (bench ? a.isActive : !a.isActive)) continue;
			let best = 0;
			for (const t of targets) {
				try { best = Math.max(best, Math.min(1, bestDamage(b, a, t) / Math.max(1, t.maxhp))); } catch (e) {}
			}
			tot += best;
		}
		return tot / (bench ? 4 : 2);
	};
	const fine = [
		d(p => p.status === 'brn' && isPhysical(p) ? 1 : 0) / 4,
		d(p => p.status === 'par' ? 1 : 0) / 4,
		d(p => p.status === 'slp' || p.status === 'frz' ? 1 : 0) / 4,
		d(p => p.status === 'psn' || p.status === 'tox' ? 1 : 0) / 4,
		usefulScreens(me, op) - usefulScreens(op, me),
		d(p => p.item === 'focussash' && p.hp === p.maxhp ? 1 : 0) / 4,
		d(p => p.item && p.item.endsWith('berry') ? 1 : 0) / 4,
		d(p => !p.isActive && ab(p) === 'intimidate' ? 1 : 0) / 4,
		d(p => !p.isActive && hasMove(p, ['fakeout']) ? 1 : 0) / 4,
		(live(me).some(p => hasMove(p, ['followme', 'ragepowder'])) ? 1 : 0) - (live(op).some(p => hasMove(p, ['followme', 'ragepowder'])) ? 1 : 0),
		fieldGood(me) - fieldGood(op),
		benchThreat(me, op) - benchThreat(op, me),
		bestHp(me) - bestHp(op),
		press(me, op, false) - press(op, me, false),
		press(me, op, true) - press(op, me, true),
	];
	return [
		d(() => 1) / 4,
		d(p => p.hp / p.maxhp) / 4,
		d(p => (p.status ? (p.status === 'slp' || p.status === 'frz' ? 1.5 : 1) : 0)) / 4,
		d(p => Math.max(p.boosts.atk, p.boosts.spa)) / 6,
		d(p => p.boosts.spe) / 6,
		d(p => p.boosts.def + p.boosts.spd) / 6,
		(dur(me, 'tailwind') - dur(op, 'tailwind')) / 4,
		tr,
		(screens(me) - screens(op)) / 2,
		megaLeft(me) - megaLeft(op),
		(mine.length - theirs.length) / 2,
		speed / n,
		threat / n,
	].concat(fine);
}
/** Mejor daño esperado de att a def, con memoria (el juez lo pide en cada hoja de la búsqueda). */
const DMG_MEMO = new Map();
function bestDamage(b, att, def) {
	const bo = p => `${p.boosts.atk}${p.boosts.def}${p.boosts.spa}${p.boosts.spd}`;
	const key = `${att.species.id}|${att.item}|${att.ability}|${bo(att)}|${def.species.id}|${def.item}|${bo(def)}|${b.field.weather}|${b.field.terrain}|` +
		`${Object.keys(def.side.sideConditions).join(',')}|${att.side.active.filter(alive).length}`;
	let v = DMG_MEMO.get(key);
	if (v === undefined) {
		v = 0;
		for (const ms of att.moveSlots) {
			const mv = b.dex.moves.get(ms.id);
			if (mv.category === 'Status' || ms.pp <= 0) continue;
			try { v = Math.max(v, estDamage(b, att, def, ms.id, ['allAdjacentFoes', 'allAdjacent'].includes(mv.target))); } catch (e) {}
		}
		if (DMG_MEMO.size > 50000) DMG_MEMO.clear();
		DMG_MEMO.set(key, v);
	}
	return v;
}
let JUDGE = null;              // { w: [...], names: [...] } si hay un juez validado
function loadJudge() {
	try {
		const f = require('path').join(__dirname, 'juez.json');
		const j = JSON.parse(require('fs').readFileSync(f, 'utf8'));
		// ROOBOT_JUEZ=manual: usar el juez de siempre aunque haya uno aprendido (calibración de referencia)
		if (process.env.ROOBOT_JUEZ === 'manual') { JUDGE = null; return { activo: false, manual: true }; }
		JUDGE = null;
		if (j && j.activo && Array.isArray(j.names)) {
			const idx = j.names.map(n => JUDGE_FEATURES.indexOf(n));
			if (!idx.includes(-1)) {
				if (j.fases) {                                   // v2: juez de siempre + correcciones, por fase
					const ok = ['inicio', 'medio', 'final'].every(k => j.fases[k] && Array.isArray(j.fases[k].w) && j.fases[k].w.length === idx.length);
					if (ok) JUDGE = Object.assign({}, j, { idx });
				} else if (Array.isArray(j.w) && j.w.length === idx.length) {
					JUDGE = Object.assign({}, j, { idx });       // v1: sólo rasgos
				}
			}
		}
	} catch (e) { JUDGE = null; }
	return JUDGE ? { activo: true, fecha: j_fecha(JUDGE), metricas: JUDGE.metricas || null } : { activo: false };
}
function j_fecha(j) { return j.fecha || ''; }
loadJudge();
function evaluate(b, s) {
	if (b.ended) {
		if (!b.winner) return 0;
		return b.winner === b.sides[s].name ? 1 : -1;
	}
	if (JUDGE && EVAL_VARIANT !== 9) {
		try {
			const f = judgeFeatures(b, s);
			let z = 0;
			if (JUDGE.fases) {
				const ph = JUDGE.fases[judgePhase(b)];
				z = ph.a * handZ(b, s);
				for (let i = 0; i < JUDGE.idx.length; i++) z += ph.w[i] * f[JUDGE.idx[i]];
			} else {
				for (let i = 0; i < JUDGE.idx.length; i++) z += JUDGE.w[i] * f[JUDGE.idx[i]];
			}
			return Math.tanh(z / 2);        // = 2·P(ganar) − 1
		} catch (e) { /* si falla, la heurística */ }
	}
	return Math.tanh(handDiff(b, s) / 1.6);
}
/** El juez de siempre en escala logit (z = 2·atanh(valor)). */
function handZ(b, s) { return 2 * handDiff(b, s) / 1.6; }
function handDiff(b, s) {
	let diff = sideScore(b, s) - sideScore(b, 1 - s);
	if (EVAL_VARIANT === 1) {
		try { diff += tacticalTerms(b, s); } catch (e) { /* sin términos tácticos */ }
	}
	const tr = b.field.pseudoWeather['trickroom'];
	if (tr) {
		const mine = sideSpeed(b, s), theirs = sideSpeed(b, 1 - s);
		if (mine !== theirs) diff += (mine < theirs ? 1 : -1) * HAND.tr * Math.min(4, tr.duration || 0) / 4;
	}
	return diff;
}

// ---------------------------------------------------------------- mirar un turno más
/** Puntuación rápida de un Pokémon del banquillo para entrar tras un KO. */
function switchInScore(b, s, p) {
	let v = monValue(b, p), hurt = 0, hit = 0;
	for (const f of foesOf(b, s)) {
		if (!alive(f)) continue;
		for (const ms of f.moveSlots) {
			const mv = b.dex.moves.get(ms.id);
			if (mv.category === 'Status') continue;
			hurt = Math.max(hurt, Math.min(1, estDamage(b, f, p, ms.id, false) / Math.max(1, p.hp)));
		}
		for (const ms of p.moveSlots) {
			const mv = b.dex.moves.get(ms.id);
			if (mv.category === 'Status') continue;
			hit = Math.max(hit, Math.min(1, estDamage(b, p, f, ms.id, false) / Math.max(1, f.hp)));
		}
	}
	return v - 0.6 * hurt + 0.4 * hit;
}
/**
 * Cambios pendientes. midOnly=true: sólo los de mitad de turno (U-turn, Parting Shot, Volt Switch,
 * Eject Button/Pack...), para que el turno se complete: sin esto la simulación se quedaba parada
 * tras el pivote y las acciones restantes (las del rival, a menudo) nunca ocurrían.
 */
function forcedSwitches(b, midOnly = false) {
	for (let round = 0; round < 4 && !b.ended; round++) {
		if (midOnly && !(b.queue && b.queue.list.length)) break;
		let any = false;
		for (let s = 0; s < 2; s++) {
			const side = b.sides[s], req = side.activeRequest;
			if (!req || req.wait || !req.forceSwitch || side.isChoiceDone()) continue;
			any = true;
			const id = s ? 'p2' : 'p1';
			const bench = side.pokemon.filter(p => alive(p) && !p.isActive);
			const used = new Set(), parts = [];
			for (let slot = 0; slot < req.forceSwitch.length; slot++) {
				if (!req.forceSwitch[slot]) { parts.push('pass'); continue; }
				let best = null, bv = -Infinity;
				for (const p of bench) {
					if (used.has(p)) continue;
					let v = 0;
					try { v = switchInScore(b, s, p); } catch (e) { v = monValue(b, p); }
					if (v > bv) { bv = v; best = p; }
				}
				if (best) { used.add(best); parts.push(`switch ${side.pokemon.indexOf(best) + 1}`); } else parts.push('pass');
			}
			if (!b.choose(id, parts.join(', '))) b.choose(id, 'default');
		}
		if (!any) break;
	}
}
/**
 * Valor de la hoja mirando un turno más: se resuelven los cambios forzados y se juega el turno
 * siguiente como un mini-juego K2 x K2 (candidatos de ambos bandos, una semilla), resuelto con
 * regret matching. Captura planes de 2 turnos (Trick Room / Tailwind / subir stats / Protect + golpe).
 */
function finishTurn(b) { forcedSwitches(b, true); }
function lookValue(b, K2, seed) {
	forcedSwitches(b);
	if (b.ended) return evaluate(b, 0);
	const ready = b.sides.every(sd => sd.activeRequest && !sd.activeRequest.wait && !sd.activeRequest.forceSwitch);
	if (!ready) return evaluate(b, 0);
	const A = jointCandidates(b, 0, K2), B = jointCandidates(b, 1, K2);
	if (!A.length || !B.length) return evaluate(b, 0);
	b.log = []; b.inputLog = [];
	const snap = JSON.stringify(State.serializeBattle(b));
	const M = A.map(() => B.map(() => null));
	for (let i = 0; i < A.length; i++) for (let j = 0; j < B.length; j++) {
		const c = State.deserializeBattle(snap);
		c.send = () => {};
		c.prng = new PS.PRNG(seed);
		const ok1 = c.choose('p1', A[i].choice), ok2 = c.choose('p2', B[j].choice);
		if (ok1 && ok2) finishTurn(c);
		M[i][j] = (!ok1) ? -1 : (!ok2 ? null : evaluate(c, 0));
	}
	// columnas ilegales del rival: se quitan
	const cols = B.map((_, j) => j).filter(j => M.some(r => r[j] !== null));
	if (!cols.length) return evaluate(b, 0);
	const MM = M.map(r => cols.map(j => r[j] === null ? 0 : r[j]));
	return regretMatching(MM, 300).value;
}

// ---------------------------------------------------------------- finales: jugar hasta el final
function rng32(seed) { let x = (seed >>> 0) || 1; return () => { x ^= x << 13; x >>>= 0; x ^= x >> 17; x ^= x << 5; x >>>= 0; return x / 4294967296; }; }
function sampleIdx(p, r) { let acc = 0; for (let i = 0; i < p.length; i++) { acc += p[i]; if (r < acc) return i; } return p.length - 1; }
/** Un turno del final: mini-juego K2 x K2 (una semilla) y su equilibrio. */
function turnGame(b, K2, seed) {
	const A = jointCandidates(b, 0, K2), B = jointCandidates(b, 1, K2);
	if (!A.length || !B.length) return null;
	b.log = []; b.inputLog = [];
	const snap = JSON.stringify(State.serializeBattle(b));
	const M = A.map(() => B.map(() => null));
	for (let i = 0; i < A.length; i++) for (let j = 0; j < B.length; j++) {
		const c = State.deserializeBattle(snap);
		c.send = () => {};
		c.prng = new PS.PRNG(seed);
		const ok1 = c.choose('p1', A[i].choice), ok2 = c.choose('p2', B[j].choice);
		if (ok1 && ok2) finishTurn(c);
		M[i][j] = (!ok1) ? -1 : (!ok2 ? null : evaluate(c, 0));
	}
	const cols = B.map((_, j) => j).filter(j => M.some(r => r[j] !== null));
	if (!cols.length) return null;
	const sol = regretMatching(M.map(r => cols.map(j => r[j] === null ? 0 : r[j])), 250);
	return { A, B: cols.map(j => B[j]), row: sol.row, col: sol.col };
}
/**
 * Valor REAL de una hoja en un final: se juega hasta que alguien gana. En cada turno los dos bandos
 * eligen según el equilibrio de un mini-juego 3 x 3 (con su estrategia mixta), así que no se depende de la
 * evaluación heurística: +1 si ganas, −1 si pierdes (o la heurística si se supera el límite de turnos).
 */
function endgameValue(b, seedNum, K2 = 3, maxTurns = 10) {
	const rnd = rng32(seedNum * 2654435761);
	for (let t = 0; t < maxTurns; t++) {
		forcedSwitches(b);
		if (b.ended) break;
		const ready = b.sides.every(sd => sd.activeRequest && !sd.activeRequest.wait && !sd.activeRequest.forceSwitch);
		if (!ready) break;
		const g = turnGame(b, K2, seedFor(seedNum * 31 + t));
		if (!g) break;
		const i = sampleIdx(g.row, rnd()), j = sampleIdx(g.col, rnd());
		if (!b.choose('p1', g.A[i].choice)) b.choose('p1', 'default');
		if (!b.choose('p2', g.B[j].choice)) b.choose('p2', 'default');
		finishTurn(b);
	}
	if (b.ended) return !b.winner ? 0 : (b.winner === b.sides[0].name ? 1 : -1);
	return evaluate(b, 0);
}
/** La línea más probable desde una jugada concreta (para enseñarla): turno a turno, hasta el final. */
function endgameLine(req) {
	const built = buildBattle(req.state);
	const b = built.b;
	b.prng = new PS.PRNG(req.seed || '7,11,13,17');
	const turns = [];
	const kos = from => {
		const out = [];
		for (const line of b.log.slice(from)) {
			const p = line.split('|');
			if (p[1] === 'faint') out.push(p[2]);
		}
		return out;
	};
	let ours = req.ours, theirs = req.theirs, labO = req.oursLabel || req.ours, labT = req.theirsLabel || req.theirs;
	for (let t = 0; t < (req.maxTurns || 8); t++) {
		const from = b.log.length;
		if (!b.choose('p1', ours)) b.choose('p1', 'default');
		if (!b.choose('p2', theirs)) b.choose('p2', 'default');
		finishTurn(b);
		forcedSwitches(b);
		turns.push({ turn: t + 1, ours: labO, theirs: labT, kos: kos(from) });
		if (b.ended) break;
		const ready = b.sides.every(sd => sd.activeRequest && !sd.activeRequest.wait && !sd.activeRequest.forceSwitch);
		if (!ready) break;
		const g = turnGame(b, 3, seedFor(900 + t));
		if (!g) break;
		const i = g.row.indexOf(Math.max(...g.row)), j = g.col.indexOf(Math.max(...g.col));
		ours = g.A[i].choice; labO = g.A[i].label + (g.row[i] < 0.75 ? ` (${Math.round(g.row[i] * 100)} %)` : '');
		theirs = g.B[j].choice; labT = g.B[j].label;
	}
	return { turns, ended: !!b.ended, won: b.ended ? (b.winner === b.sides[0].name ? true : (b.winner ? false : null)) : null };
}

// ---------------------------------------------------------------- resolución de la matriz
function regretMatching(M, iters = 3000) {
	const R = M.length, C = M[0].length;
	const rReg = new Float64Array(R), cReg = new Float64Array(C);
	const rSum = new Float64Array(R), cSum = new Float64Array(C);
	const strat = (reg) => {
		let tot = 0; for (const x of reg) tot += Math.max(0, x);
		return Array.from(reg, x => tot > 0 ? Math.max(0, x) / tot : 1 / reg.length);
	};
	for (let t = 1; t <= iters; t++) {
		const rs = strat(rReg), cs = strat(cReg);
		const rVals = new Float64Array(R), cVals = new Float64Array(C);
		let v = 0;
		for (let i = 0; i < R; i++) for (let j = 0; j < C; j++) {
			rVals[i] += M[i][j] * cs[j];
			cVals[j] -= M[i][j] * rs[i];
		}
		for (let i = 0; i < R; i++) v += rs[i] * rVals[i];
		let vc = 0; for (let j = 0; j < C; j++) vc += cs[j] * cVals[j];
		for (let i = 0; i < R; i++) rReg[i] = Math.max(0, rReg[i] + rVals[i] - v);   // RM+
		for (let j = 0; j < C; j++) cReg[j] = Math.max(0, cReg[j] + cVals[j] - vc);
		const w = t; // promedio ponderado lineal
		for (let i = 0; i < R; i++) rSum[i] += w * rs[i];
		for (let j = 0; j < C; j++) cSum[j] += w * cs[j];
	}
	const norm = a => { const s = a.reduce((x, y) => x + y, 0); return Array.from(a, x => x / s); };
	const row = norm(rSum), col = norm(cSum);
	let value = 0;
	for (let i = 0; i < R; i++) for (let j = 0; j < C; j++) value += row[i] * col[j] * M[i][j];
	return { row, col, value };
}

// ---------------------------------------------------------------- búsqueda
function seedFor(k) {
	return `${(k * 7919 + 13) % 65536},${(k * 104729 + 7) % 65536},${(k * 1299709 + 3) % 65536},${(k * 15485863 + 1) % 65536}`;
}

/**
 * req: { worlds: [state...], K: 8, Kopp: 8, seeds: 16, returnLeaves: false, encode: fn? }
 * Devuelve candidatos, matriz, estrategias y valor.
 */
function search(req, encodeLeaf = null) {
	const prevEval = EVAL_VARIANT;
	if (req.evalVariant !== undefined) EVAL_VARIANT = req.evalVariant | 0;
	try { return searchInner(req, encodeLeaf); } finally { EVAL_VARIANT = prevEval; }
}
function searchInner(req, encodeLeaf = null) {
	const K = req.K || 8, Kopp = req.Kopp || req.K || 8, S = req.seeds || 8;
	const worlds = req.worlds;
	const t0 = Date.now();
	// Candidatos a partir del primer mundo (deterministas: todos los workers obtienen los mismos)
	const base0 = buildBattle(worlds[0]);
	if (base0.ended) return { error: 'battle_ended' };
	const ours = jointCandidates(base0.b, 0, K, req.megaPolicy || 'asap');
	const theirs = jointCandidates(base0.b, 1, Kopp, req.megaPolicy || 'asap');
	// jugadas concretas añadidas a mano (p. ej. lo que hizo el humano, o la que elige en un ejercicio)
	const addExtra = (list, s, specs) => {
		for (const spec of specs || []) {
			// admite {choice, label} (elección ya escrita) o la especificación por ranura
			const c = (spec && typeof spec === 'object' && !Array.isArray(spec) && spec.choice) ?
				{ choice: spec.choice, label: spec.label || spec.choice } : choiceFromSpec(base0.b, s, spec);
			if (!c) continue;
			const idx = list.findIndex(o => o.choice === c.choice);
			if (idx >= 0) { list[idx].extra = true; continue; }
			list.push(Object.assign(c, { prior: 0, extra: true }));
		}
	};
	addExtra(ours, 0, req.extraOurs);
	addExtra(theirs, 1, req.extraTheirs);
	const onlyRows = req.onlyRows ? new Set(req.onlyRows) : null, onlyCols = req.onlyCols ? new Set(req.onlyCols) : null;
	const K2 = req.lookahead ? (req.lookahead.K2 || 3) : 0;
	const END = req.endgame ? (req.endgame.K2 || 3) : 0;
	const M = ours.map(() => theirs.map(() => 0));
	const counts = ours.map(() => theirs.map(() => 0));
	const leaves = [];
	let invalid = 0;
	const badRow = new Set(), badCol = new Set();
	// reparto de trabajo entre procesos: el worker "shard" de "nShards" hace las semillas k ≡ shard (mod nShards)
	const shard = req.shard || 0, nShards = req.nShards || 1;
	for (let w = 0; w < worlds.length; w++) {
		const built = w === 0 ? base0 : buildBattle(worlds[w]);
		if (built.ended) continue;
		built.b.log = []; built.b.inputLog = [];
		const snap = JSON.stringify(State.serializeBattle(built.b));
		const seedsPerWorld = Math.max(1, Math.round(S / worlds.length));
		for (let k = 0; k < seedsPerWorld; k++) {
			if (((w * seedsPerWorld + k) % nShards) !== shard) continue;
			const seed = seedFor(w * 1000 + k);
			for (let i = 0; i < ours.length; i++) {
				if (onlyRows && !onlyRows.has(i)) continue;
				for (let j = 0; j < theirs.length; j++) {
					if (onlyCols && !onlyCols.has(j)) continue;
					const b = State.deserializeBattle(snap);
					b.send = () => {};
					b.prng = new PS.PRNG(seed);
					const ok1 = b.choose('p1', ours[i].choice);
					const ok2 = b.choose('p2', theirs[j].choice);
					if (!ok1 || !ok2) { invalid++; if (!ok1) badRow.add(i); else badCol.add(j); continue; }
					if (!req.noFinish) finishTurn(b);
					if (encodeLeaf) {
						if (b.ended) leaves.push({ i, j, w, k, terminal: evaluate(b, 0) });
						else leaves.push({ i, j, w, k, x: encodeLeaf(b, 0), h: evaluate(b, 0) });
					} else if (END) {
						M[i][j] += b.ended ? (!b.winner ? 0 : (b.winner === b.sides[0].name ? 1 : -1))
							: endgameValue(b, w * 1000 + k + 1, END, req.endgame.maxTurns || 10);
					} else if (K2 && !b.ended) {
						M[i][j] += lookValue(b, K2, seedFor(w * 1000 + k + 500));
					} else {
						M[i][j] += evaluate(b, 0);
					}
					counts[i][j]++;
				}
			}
		}
	}
	const pub = arr => arr.map(o => ({ choice: o.choice, label: o.label, prior: o.prior, extra: !!o.extra }));
	const bad = { badRows: [...badRow], badCols: [...badCol] };
	if (encodeLeaf) return Object.assign({ ours: pub(ours), theirs: pub(theirs), leaves, counts, ms: Date.now() - t0 }, bad);
	if (req.partial) return Object.assign({ ours: pub(ours), theirs: pub(theirs), sum: M, counts, invalid, ms: Date.now() - t0 }, bad);
	// celdas sin simulaciones válidas: jugada ilegal nuestra = muy mala; ilegal suya = descartada (buena para nosotros)
	for (let i = 0; i < ours.length; i++) for (let j = 0; j < theirs.length; j++) {
		M[i][j] = counts[i][j] ? M[i][j] / counts[i][j] : (badRow.has(i) ? -1 : (badCol.has(j) ? 1 : 0));
	}
	const sol = regretMatching(M);
	return {
		ours: pub(ours), theirs: pub(theirs), badRows: bad.badRows, badCols: bad.badCols,
		matrix: M, row: sol.row, col: sol.col, value: sol.value,
		invalid, ms: Date.now() - t0,
	};
}

/**
 * Construye una jugada conjunta a partir de una especificación por ranura:
 *   [{move: 'earthquake', target: 1|2|-1|-2|null, mega: bool} | {switch: 'Garchomp'} | {pass: true}, ...]
 * El objetivo sólo se escribe si el movimiento lo admite.
 */
function choiceFromSpec(b, s, spec) {
	if (!Array.isArray(spec) || spec.length !== 2) return null;
	const side = b.sides[s];
	const parts = [], labels = [];
	for (let slot = 0; slot < 2; slot++) {
		const x = spec[slot] || { pass: true };
		const pk = side.active[slot];
		if (x.pass || !pk || pk.fainted) { parts.push('pass'); labels.push('pass'); continue; }
		if (x.switch) {
			const t = side.pokemon.find(p => toID(p.name) === toID(x.switch) || toID(p.species.name) === toID(x.switch) || toID(p.baseSpecies.name) === toID(x.switch));
			if (!t) return null;
			parts.push(`switch ${t.name}`); labels.push(`${pk.species.name} ⇄ ${t.species.name}`); continue;
		}
		const mv = b.dex.moves.get(x.move);
		if (!mv.exists) return null;
		let c = `move ${mv.id}`, lab = `${pk.species.name}: ${mv.name}`;
		if (['normal', 'any', 'adjacentFoe', 'adjacentAlly', 'adjacentAllyOrSelf'].includes(mv.target)) {
			let t = x.target;
			if (mv.target === 'adjacentAlly') t = -(2 - slot);
			if (mv.target === 'adjacentAllyOrSelf') t = t || -(slot + 1);
			if (!t) t = 1;
			c += ` ${t}`;
			const foes = b.sides[1 - s].active;
			if (t > 0 && foes[t - 1]) lab += ` → ${foes[t - 1].species.name}`;
			else if (t < 0) lab += ' → aliado';
		}
		if (x.mega) { c += ' mega'; lab += ' (Mega)'; }
		parts.push(c); labels.push(lab);
	}
	return { choice: parts.join(', '), label: labels.join(' | ') };
}

/** Valor estático de un estado (para elegir cambios forzados). */
function staticValue(state) {
	const built = buildBattle(state);
	return evaluate(built.b, 0);
}

module.exports = { priorSlot, foesOf, sideSpeed, actionSpeed, SETUP, REDIRECT, SPEED_CONTROL_DMG, HAND, judgeFeatures, judgePhase, handZ, loadJudge, JUDGE_FEATURES, setEvalVariant, tacticalTerms, endgameValue, endgameLine, finishTurn, forcedSwitches, lookValue, priorityBlock, flinchIssue, search, choiceFromSpec, regretMatching, evaluate, jointCandidates, slotOptions, estDamage, threatTo, staticValue, PROTECT_MOVES };
