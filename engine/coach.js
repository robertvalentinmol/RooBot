'use strict';
/**
 * Datos para explicar decisiones en los ejercicios.
 *  - outcome(req): juega un turno concreto contra varias respuestas del rival (con varias semillas)
 *                  y resume qué pasa: PS finales, probabilidad de KO, si cada acción se bloqueó, etc.
 *  - previewSetup(req): ejercicio de team preview: hojas de ambos equipos, leads más probables del
 *                  rival (sinergia + emparejamientos contra tu equipo) y tabla de emparejamientos.
 *  - leadValue(req): valor del turno 1 para una pareja de leads concreta de cada lado (búsqueda).
 */
const { PS, State, dexFor, normSet, toID, guessSpread, completeOpponentSet } = require('./sim');
const { buildBattle } = require('./inject');
const { search, finishTurn } = require('./search');
const { matchupTable, transposeTable, teamPreview, combos, supportBonus } = require('./teampreview');
const { stateFromBattle } = require('./positions');

const PROTECT_RE = /Protect|Detect|Spiky Shield|King's Shield|Baneful Bunker|Silk Trap|Burning Bulwark|Obstruct|Max Guard|Wide Guard|Quick Guard/;

function seedFor(k) {
	return `${(k * 7919 + 101) % 65536},${(k * 104729 + 17) % 65536},${(k * 1299709 + 5) % 65536},${(k * 15485863 + 3) % 65536}`;
}

// ---------------------------------------------------------------- outcome
function outcome(req) {
	const built = buildBattle(req.state);
	const b0 = built.b;
	b0.log = []; b0.inputLog = [];
	const snap = JSON.stringify(State.serializeBattle(b0));
	const replies = (req.theirs || []).filter(r => r.w > 0);
	const wsum = replies.reduce((a, r) => a + r.w, 0) || 1;
	const S = req.seeds || 6;
	const mons = [[], []];
	const slots = {};
	const slotRec = id => slots[id] || (slots[id] = { name: '', moves: {}, n: 0, moved: 0, blocked: 0, immune: 0, miss: 0, fail: 0, cant: 0, faintedBefore: 0, terrain: 0, shield: 0, before: {} });
	let total = 0;
	for (const r of replies) {
		for (let k = 0; k < S; k++) {
			const b = State.deserializeBattle(snap);
			b.send = () => {};
			b.prng = new PS.PRNG(seedFor(k));
			const objs = b.sides.map(s => s.pokemon.slice());
			const start = objs.map(arr => arr.map(p => (p.fainted ? 0 : p.hp / p.maxhp)));
			const startActive = b.sides.map(s => s.active.map(p => (p && !p.fainted ? p : null)));
			const startName = new Map();
			for (const arr of objs) for (const p of arr) startName.set(p, p.species.name);
			if (!b.choose('p1', req.ours) || !b.choose('p2', r.choice)) continue;
			finishTurn(b);
			const w = r.w / wsum / S;
			total += w;
			// PS finales
			for (let s = 0; s < 2; s++) {
				objs[s].forEach((p, i) => {
					const m = mons[s][i] || (mons[s][i] = { name: startName.get(p), start: start[s][i], end: 0, ko: 0, active: false, mega: 0 });
					if (startActive[s].includes(p)) m.active = true;
					const end = p.fainted ? 0 : p.hp / p.maxhp;
					m.end += w * end;
					if (p.fainted && start[s][i] > 0) m.ko += w;
					if (p.species.isMega) m.mega += w;
				});
			}
			// registro: qué hizo cada ranura y qué le pasó a la acción
			const acts = [];
			let cur = null;
			const cants = new Set();
			for (const line of b.log) {
				const parts = line.split('|');
				const t = parts[1];
				if (t === 'move') {
					cur = { slot: parts[2].slice(0, 3), move: parts[3], target: (parts[4] || '').slice(0, 3), flags: line };
					if (line.includes('[notarget]') || line.includes('[miss]')) cur.fail = true;
					acts.push(cur);
				} else if (t === 'cant' && (parts[3] || '').startsWith('ability:') && line.includes('[of]')) {
					// Armor Tail / Queenly Majesty / Dazzling: bloquean la prioridad del atacante ([of])
					const of = (parts.find(x => x.startsWith('[of]')) || '').slice(5, 8);
					const a = acts.slice().reverse().find(x => x.slot === of);
					if (a) a.shield = (parts[3] || '').slice(9);
				} else if (cur && t === '-activate' && (parts[3] || '').includes('Psychic Terrain')) {
					cur.terrain = true;
				} else if (t === 'cant') {
					cants.add(parts[2].slice(0, 3));
					cur = null;
				} else if (cur && t === '-activate' && PROTECT_RE.test(parts[3] || '') && parts[2].slice(0, 2) !== cur.slot.slice(0, 2)) {
					cur.blocked = true;
				} else if (cur && t === '-immune') cur.immune = true;
				else if (cur && t === '-miss') cur.miss = true;
				else if (cur && t === '-fail') cur.fail = true;
				else if (t === 'turn' || t === 'upkeep') cur = null;
			}
			for (let s = 0; s < 2; s++) {
				for (let slot = 0; slot < 2; slot++) {
					const p = startActive[s][slot];
					if (!p) continue;
					const id = `p${s + 1}${slot ? 'b' : 'a'}`;
					const rec = slotRec(id);
					rec.name = startName.get(p);
					rec.n += w;
					const a = acts.find(x => x.slot === id);
					if (a) {
						rec.moved += w;
						rec.moves[a.move] = (rec.moves[a.move] || 0) + w;
						if (a.blocked) rec.blocked += w;
						if (a.immune) rec.immune += w;
						if (a.miss) rec.miss += w;
						if (a.terrain) rec.terrain += w;
						if (a.shield) { rec.shield += w; rec.shieldName = a.shield; }
						if (a.fail && !a.shield && !a.terrain) rec.fail += w;
						const ia = acts.indexOf(a);
						for (const o of acts) {
							if (o.slot.slice(0, 2) === id.slice(0, 2)) continue;
							const key = o.slot;
							const e = rec.before[key] || (rec.before[key] = { n: 0, first: 0 });
							e.n += w;
							if (ia < acts.indexOf(o)) e.first += w;
						}
					} else if (cants.has(id)) rec.cant += w;
					else if (p.fainted) rec.faintedBefore += w;
				}
			}
		}
	}
	const norm = x => (total ? x / total : 0);
	for (const side of mons) for (const m of side) if (m) { m.end = norm(m.end); m.ko = norm(m.ko); m.mega = norm(m.mega); }
	for (const id in slots) {
		const r = slots[id];
		const n = r.n || 1;
		for (const k of ['moved', 'blocked', 'immune', 'miss', 'fail', 'cant', 'faintedBefore', 'terrain', 'shield']) r[k] = r[k] / n;
		for (const k in r.moves) r.moves[k] /= n;
		for (const k in r.before) r.before[k] = r.before[k].n ? r.before[k].first / r.before[k].n : null;
		delete r.n;
	}
	return { mons, slots, ok: total > 0 };
}

// ---------------------------------------------------------------- team preview
const SETUP = new Set(['swordsdance', 'nastyplot', 'calmmind', 'dragondance', 'bulkup', 'quiverdance', 'shellsmash', 'coil', 'irondefense', 'tidyup', 'victorydance', 'bellydrum', 'geomancy']);
const WEATHER_SET = { drought: 'sun', drizzle: 'rain', sandstream: 'sand', snowwarning: 'snow', orichalcumpulse: 'sun', desolateland: 'sun', primordialsea: 'rain' };
const WEATHER_MOVE = { sunnyday: 'sun', raindance: 'rain', sandstorm: 'sand', snowscape: 'snow' };
const WEATHER_ABUSE = { chlorophyll: 'sun', solarpower: 'sun', flowergift: 'sun', protosynthesis: 'sun', harvest: 'sun',
	swiftswim: 'rain', raindish: 'rain', dryskin: 'rain', sandrush: 'sand', sandforce: 'sand', sandveil: 'sand', slushrush: 'snow', icebody: 'snow', snowcloak: 'snow' };
const WEATHER_ES = { sun: 'Sol', rain: 'Lluvia', sand: 'Arena', snow: 'Nieve' };
const TERRAIN_SET = { psychicsurge: 'psychic', grassysurge: 'grassy', electricsurge: 'electric', mistysurge: 'misty', hadronengine: 'electric' };
const TERRAIN_ABUSE = { expandingforce: 'psychic', grassyglide: 'grassy', risingvoltage: 'electric', mistyexplosion: 'misty' };
const TERRAIN_ES = { psychic: 'Campo Psíquico', grassy: 'Campo de Hierba', electric: 'Campo Eléctrico', misty: 'Campo de Niebla' };

function megaSpecies(set, dex) {
	const it = dex.items.get(set.item);
	if (!it.megaStone) return null;
	let name = it.megaStone;
	if (typeof name === 'object') name = name[dex.species.get(set.species).baseSpecies] || Object.values(name)[0];
	const sp = dex.species.get(name);
	return sp.exists ? sp : null;
}

function features(set, dex) {
	const sp = dex.species.get(set.species);
	const mega = megaSpecies(set, dex);
	const form = mega || sp;
	const moves = set.moves.map(m => toID(m));
	const ab = toID(mega ? Object.values(mega.abilities)[0] : set.ability);
	const ab0 = toID(set.ability);
	const types = moves.map(m => dex.moves.get(m)).filter(m => m.category !== 'Status').map(m => m.type);
	const f = {
		name: sp.name, mega: !!mega, spe: form.baseStats.spe,
		fakeout: moves.includes('fakeout'), tailwind: moves.includes('tailwind'), trickroom: moves.includes('trickroom'),
		redirect: moves.includes('followme') || moves.includes('ragepowder'), helpinghand: moves.includes('helpinghand'),
		setup: moves.some(m => SETUP.has(m)), intimidate: ab0 === 'intimidate' || ab === 'intimidate',
		weatherSet: WEATHER_SET[ab0] || WEATHER_SET[ab] || null, weatherMove: moves.map(m => WEATHER_MOVE[m]).find(Boolean) || null,
		weatherAbuse: WEATHER_ABUSE[ab0] || WEATHER_ABUSE[ab] || null,
		terrainSet: TERRAIN_SET[ab0] || TERRAIN_SET[ab] || null,
		terrainAbuse: moves.map(m => TERRAIN_ABUSE[m]).find(Boolean) || null,
		fireMoves: types.filter(t => t === 'Fire').length, waterMoves: types.filter(t => t === 'Water').length,
		speedControl: moves.some(m => ['icywind', 'electroweb', 'thunderwave', 'scaryface'].includes(m)),
		prioShield: ['armortail', 'queenlymajesty', 'dazzling'].includes(ab0) ? dex.abilities.get(ab0).name : null,
	};
	f.weather = f.weatherSet || f.weatherMove;
	return f;
}

/** Sinergia de una pareja de leads: puntuación y etiquetas legibles. */
function pairSynergy(a, b) {
	let score = 0;
	const tags = [];
	const add = (v, tag) => { score += v; if (tag && !tags.includes(tag)) tags.push(tag); };
	for (const [x, y] of [[a, b], [b, a]]) {
		if (x.fakeout && (y.tailwind || y.trickroom || y.setup || y.weatherAbuse)) add(0.15, `Fake Out de ${x.name} protege a ${y.name}`);
		else if (x.fakeout) add(0.06, `Fake Out (${x.name})`);
		if (x.redirect && (y.trickroom || y.setup || y.tailwind)) add(0.15, `${x.name} redirige ataques mientras ${y.name} prepara`);
		if (x.tailwind && !y.trickroom) add(y.spe >= 50 ? 0.12 : 0.06, `Viento Afín (${x.name}) acelera a ${y.name}`);
		if (x.trickroom && y.spe <= 60) add(0.2, `Espacio Raro (${x.name}) + ${y.name} lento`);
		if (x.trickroom && y.spe >= 90 && !y.trickroom) add(-0.05);
		if (x.weather && y.weatherAbuse === x.weather) add(0.28, `${WEATHER_ES[x.weather]} (${x.name}) + ${y.name}`);
		else if (x.weather === 'sun' && y.fireMoves) add(0.07, `Sol potencia el Fuego de ${y.name}`);
		else if (x.weather === 'rain' && y.waterMoves) add(0.07, `Lluvia potencia el Agua de ${y.name}`);
		if (x.terrainSet && y.terrainAbuse === x.terrainSet) add(0.2, `${TERRAIN_ES[x.terrainSet]} (${x.name}) + ${y.name}`);
		if (x.helpinghand) add(0.04, `Refuerzo (${x.name})`);
		if (x.terrainSet === 'psychic') add(0.1, `Campo Psíquico (${x.name}): ${y.name} inmune a Fake Out y prioridad si está en el suelo`);
		if (x.prioShield) add(0.08, `${x.prioShield} (${x.name}) bloquea Fake Out y prioridad`);
		if (x.speedControl && !x.tailwind) add(0.03);
	}
	if (a.intimidate || b.intimidate) add(0.05, `Intimidación (${a.intimidate ? a.name : b.name})`);
	if (a.weather && b.weather && a.weather !== b.weather) add(-0.15, 'Climas que se pisan');
	if (a.mega && b.mega) add(-0.03);
	return { score, tags };
}

/** Estadísticas a nivel 50 con la fórmula de Champions (base + SP + 75 en PS, base + SP + 20 en el resto). */
function calcStats(set, dex) {
	const sp = dex.species.get(set.species);
	const nat = dex.natures.get(set.nature || 'Serious');
	const out = {};
	for (const k of ['hp', 'atk', 'def', 'spa', 'spd', 'spe']) {
		const base = sp.baseStats[k], ev = (set.evs && set.evs[k]) || 0;
		if (k === 'hp') { out.hp = base + ev + 75; continue; }
		let v = base + ev + 20;
		if (nat.plus === k) v = Math.floor(v * 1.1);
		if (nat.minus === k) v = Math.floor(v * 0.9);
		out[k] = v;
	}
	return out;
}

// ---------------------------------------------------------------- modo realista
/**
* Variantes de reparto de SP para un set del que sólo se conoce la hoja (Open Team Sheet):
* 0 = la estimación habitual, 1 = ofensivo con PS, 2 = rápido (o bulky si es de Espacio Raro / soporte).
*/
function spreadVariant(set, dex, k) {
	const g = guessSpread(set, dex);
	const v = k % 3;
	if (v === 0) return g;
	const z = () => ({ hp: 0, atk: 0, def: 0, spa: 0, spd: 0, spe: 0 });
	const support = !g.evs.atk && !g.evs.spa;
	const tr = (set.moves || []).map(toID).includes('trickroom');
	const atk = g.evs.atk >= g.evs.spa ? 'atk' : 'spa';
	const e = z();
	if (support) {
		e.hp = 32; e[v === 1 ? 'def' : 'spd'] = 32; e[v === 1 ? 'spd' : 'def'] = 2;
		return { evs: e, nature: v === 1 ? 'Bold' : 'Calm' };
	}
	if (v === 1 || tr) {
		e.hp = 32; e[atk] = 32; e.def = 2;
		return { evs: e, nature: tr ? (atk === 'atk' ? 'Brave' : 'Quiet') : (atk === 'atk' ? 'Adamant' : 'Modest') };
	}
	e[atk] = 32; e.spe = 32; e.hp = 2;
	return { evs: e, nature: atk === 'atk' ? 'Jolly' : 'Timid' };
}

/**
* Mundos posibles desde el punto de vista del jugador: del rival sólo se conoce su hoja.
* Sus SP y naturalezas se estiman (con variantes) y los Pokémon que aún no ha sacado se
* sustituyen por otros de su hoja que todavía no se han visto.
*/
function realisticWorlds(req) {
	const st = req.state;
	const dex = dexFor(req.format || st.format);
	const sheet = (req.sheet || []).map(s => Object.assign(normSet(s, dex), { _nk: s.natureKnown !== false, _seen: s.seen || null }));
	const usage = require('./usage');
	const rev = p => !!(p.revealed || (p.active !== null && p.active !== undefined) || p.fainted);
	const baseId = s => toID(dex.species.get(s.species).baseSpecies);
	const known = st.sides[1].pokemon.filter(rev).map(p => baseId(p.set));
	const pool = sheet.filter(s => !known.includes(baseId(s)));
	let seed = req.seed || 12345;
	const rnd = () => (seed = (seed * 1103515245 + 12345) % 2147483648) / 2147483648;
	const worlds = [];
	for (let k = 0; k < (req.n || 4); k++) {
		const w = JSON.parse(JSON.stringify(st));
		const bag = pool.slice().sort(() => rnd() - 0.5);
		let u = 0;
		w.sides[1].pokemon = w.sides[1].pokemon.map((p, i) => {
			const unknown = !rev(p) && bag.length > 0;
			const base = unknown ? bag[u++ % bag.length] : p.set;
			// sin hoja abierta se parte sólo de lo visto; con hoja, de la hoja (objeto, habilidad y movimientos)
			const seen = base._seen;
			const ots = seen ? { name: base.name || base.species, species: base.species, item: seen.item || '', ability: seen.ability || '',
				moves: (seen.moves || []).map(toID) }
				: { name: base.name || base.species, species: base.species, item: base.item, ability: base.ability,
					moves: (base.moves || []).map(toID) };
			let set;
			if (usage.has(base.species, dex)) {
				// variantes plausibles según el uso real (SP y, si no se ve, naturaleza)
				set = completeOpponentSet(ots, dex, base._nk ? { nature: base.nature } : null, k + i, seen ? null : base);
			} else {
				const sv = spreadVariant(ots, dex, k + i);
				// la hoja abierta enseña la naturaleza (Stat Alignment): sólo se estiman los SP
				set = completeOpponentSet(ots, dex, { evs: sv.evs, nature: (base._nk && base.nature) || sv.nature });
			}
			const q = Object.assign({}, p, { set });
			const oldMax = calcStats(p.set, dex).hp, newMax = calcStats(set, dex).hp;
			if (unknown) Object.assign(q, { hp: newMax, item: set.item, status: '', boosts: {}, mega: false });
			else q.hp = (p.fainted || p.hp <= 0) ? 0 : Math.max(1, Math.min(newMax, Math.round(p.hp / oldMax * newMax)));
			return q;
		});
		worlds.push(w);
	}
	return { worlds, unknown: st.sides[1].pokemon.filter(p => !rev(p)).length };
}

/** Un equipo rival tal como lo verías: hoja abierta + SP y naturaleza estimados. */
function guessTeam(req) {
	const dex = dexFor(req.format);
	return req.team.map(s => {
		const n = normSet(s, dex);
		const ots = { name: n.name, species: n.species, item: n.item, ability: n.ability, moves: n.moves.map(toID) };
		return completeOpponentSet(ots, dex, { nature: n.nature }, 0, n);
	});
}

function card(set, dex, mine) {
	const sp = dex.species.get(set.species);
	const mega = megaSpecies(set, dex);
	const c = {
		name: sp.name, baseSpecies: sp.baseSpecies, spriteid: sp.spriteid, baseSpriteid: dex.species.get(sp.baseSpecies).spriteid,
		types: sp.types, item: dex.items.get(set.item).name || '', ability: dex.abilities.get(set.ability).name,
		baseSpe: sp.baseStats.spe, mega: mega ? { name: mega.name, types: mega.types, baseSpe: mega.baseStats.spe, ability: Object.values(mega.abilities)[0] } : null,
		moves: set.moves.map(m => { const mv = dex.moves.get(m); return { id: mv.id, name: mv.name, type: mv.type, category: mv.category, basePower: mv.basePower,
			priority: mv.priority, desc: mv.shortDesc || mv.desc || '' }; }),
	};
	const ab0 = toID(set.ability);
	c.fakeout = c.moves.some(m => m.id === 'fakeout');
	c.blocksPriority = TERRAIN_SET[ab0] === 'psychic' ? 'Campo Psíquico' :
		(['armortail', 'queenlymajesty', 'dazzling'].includes(ab0) ? dex.abilities.get(ab0).name : null);
	// la naturaleza (Stat Alignment) sale en la hoja abierta de ambos; los SP sólo en la tuya
	c.nature = set.nature;
	const nat = dex.natures.get(set.nature || 'Serious');
	c.natureUp = nat.plus || null; c.natureDown = nat.minus || null;
	if (mine) c.stats = calcStats(set, dex);
	return c;
}

function leadScore(T, L, nOpp) {
	let s = 0;
	for (let j = 0; j < nOpp; j++) {
		s += Math.min(1, Math.max(...L.map(i => T.off[i][j]))) - 0.3 * L.reduce((a, i) => a + T.thr[i][j], 0) / 2 +
			0.05 * L.reduce((a, i) => a + T.spd[i][j], 0) / 2;
	}
	return s / nOpp;
}

function previewSetup(req) {
	const format = req.format;
	const dex = dexFor(format);
	const ours = req.ours.map(s => normSet(s, dex));
	const theirs = req.theirs.map(s => normSet(s, dex));
	const T = matchupTable(ours, theirs, format);
	const TR = transposeTable(T);
	const fo = ours.map(s => features(s, dex)), ft = theirs.map(s => features(s, dex));
	// leads probables del rival: lo bien que encaja la pareja contra tu equipo + su sinergia
	const rivalPairs = combos(theirs.length, 2).map(L => {
		const syn = pairSynergy(ft[L[0]], ft[L[1]]);
		const sc = leadScore(TR, L, ours.length) + syn.score + 0.3 * L.reduce((a, i) => a + supportBonus(theirs[i]), 0);
		return { pair: L, score: sc, tags: syn.tags, syn: syn.score };
	}).sort((x, y) => y.score - x.score);
	const temp = req.temperature || 0.1;
	const ws = rivalPairs.map(r => Math.exp((r.score - rivalPairs[0].score) / temp));
	const wt = ws.reduce((a, c) => a + c, 0);
	rivalPairs.forEach((r, i) => { r.p = ws[i] / wt; });
	const likely = [];
	let cum = 0;
	for (const r of rivalPairs) {
		if (likely.length >= (req.maxRival || 4) || (likely.length >= 2 && (cum >= 0.85 || r.p < 0.04))) break;
		likely.push(r); cum += r.p;
	}
	likely.forEach(r => { r.p = r.p / cum; });
	for (const r of likely) {
		const tp = teamPreview({ format, ours: theirs, theirs: ours, table: TR, temperature: 0, leads: r.pair });
		r.order = tp.order;
	}
	const myPairs = combos(ours.length, 2).map(L => {
		const syn = pairSynergy(fo[L[0]], fo[L[1]]);
		const tp = teamPreview({ format, ours, theirs, table: T, temperature: 0, leads: L });
		return { pair: L, order: tp.order, tags: syn.tags, syn: +syn.score.toFixed(3) };
	});
	// cada selección de 4 con su lead más natural (para simular partidas enteras con ella)
	const bring = ours.length >= 4 ? combos(ours.length, 4).map(S => ({
		set: S, order: teamPreview({ format, ours, theirs, table: T, temperature: 0, bring: S }).order,
	})) : [];
	return {
		ours: ours.map(s => card(s, dex, true)), theirs: theirs.map(s => card(s, dex, false)), bring,
		rivalLeads: likely.map(r => ({ pair: r.pair, p: +r.p.toFixed(3), tags: r.tags, order: r.order })),
		myPairs, table: { off: T.off, thr: T.thr, spd: T.spd, moves: T.moves, tmoves: T.tmoves },
		sets: { ours, theirs },
	};
}

function leadState(req) {
	const format = req.format;
	const dex = dexFor(format);
	const ours = req.ours.map(s => normSet(s, dex));
	const theirs = req.theirs.map(s => normSet(s, dex));
	const b = new PS.Battle({ formatid: format, seed: '3,5,7,11' });
	b.send = () => {};
	b.setPlayer('p1', { name: 'p1', team: PS.Teams.pack(ours) });
	b.setPlayer('p2', { name: 'p2', team: PS.Teams.pack(theirs) });
	b.choose('p1', 'team ' + req.oursOrder.map(i => i + 1).join(''));
	b.choose('p2', 'team ' + req.theirsOrder.map(i => i + 1).join(''));
	const st = stateFromBattle(b);
	st.format = format;
	return st;
}

function leadValue(req) {
	const st = leadState(req);
	const r = search({ worlds: [st], K: req.K || 6, Kopp: req.Kopp || 6, seeds: req.seeds || 4, megaPolicy: 'asap' });
	if (r.error) return { value: 0 };
	const bi = r.row.indexOf(Math.max(...r.row)), bj = r.col.indexOf(Math.max(...r.col));
	return { value: r.value, ours: r.ours[bi].label, theirs: r.theirs[bj].label, ms: r.ms };
}

/**
 * Detalle de un lead contra un lead rival concreto: el plan del turno 1 (estrategia óptima),
 * lo que suele responder el rival y cómo acaba el turno (simulado con varias semillas).
 */
function leadDetail(req) {
	const st = leadState(req);
	const r = search({ worlds: [st], K: req.K || 6, Kopp: req.Kopp || 6, seeds: req.seeds || 4, megaPolicy: 'asap' });
	if (r.error) return { ok: false };
	const rows = r.row.map((p, i) => ({ label: r.ours[i].label, choice: r.ours[i].choice, p })).filter(x => x.p >= 0.12).sort((a, b) => b.p - a.p);
	const cols = r.col.map((p, j) => ({ label: r.theirs[j].label, choice: r.theirs[j].choice, p })).filter(x => x.p >= 0.08).sort((a, b) => b.p - a.p).slice(0, 3);
	const plan = rows[0] || { label: r.ours[0].label, choice: r.ours[0].choice, p: 1 };
	const out = outcome({ state: st, ours: plan.choice, theirs: cols.map(c => ({ choice: c.choice, w: c.p })), seeds: 6 });
	return { ok: true, value: r.value, plan: plan.label, planP: plan.p, mix: rows.slice(1, 3).map(x => ({ label: x.label, p: x.p })),
		replies: cols.map(c => ({ label: c.label, p: c.p })), out };
}

/**
 * Partidas enteras (rápidas) entre una selección nuestra y las selecciones probables del rival.
 * Ambos bandos juegan con el jugador heurístico (la mejor jugada según el prior, a veces otra de las 3
 * mejores), con las mismas semillas para todas las selecciones: así las diferencias entre selecciones
 * no dependen de la suerte. Devuelve la proporción de victorias contra cada selección rival.
 */
function rngFrom(seed) { let x = seed >>> 0 || 1; return () => { x ^= x << 13; x >>>= 0; x ^= x >> 17; x ^= x << 5; x >>>= 0; return x / 4294967296; }; }
function bringRollouts(req) {
	const { jointCandidates, forcedSwitches } = require('./search');
	const format = req.format, dex = dexFor(format);
	const packA = PS.Teams.pack(req.ours.map(s => normSet(s, dex)));
	const packB = PS.Teams.pack(req.theirs.map(s => normSet(s, dex)));
	const games = req.games || 12, seed0 = req.seed0 || 1;
	const play = (oa, ob, g) => {
		const b = new PS.Battle({ formatid: format, seed: [seed0 + g, 7 + g, 11, 13].join(',') });
		b.send = () => {};
		b.setPlayer('p1', { name: 'p1', team: packA });
		b.setPlayer('p2', { name: 'p2', team: packB });
		b.choose('p1', 'team ' + oa.map(i => i + 1).join(''));
		b.choose('p2', 'team ' + ob.map(i => i + 1).join(''));
		const rnd = rngFrom(seed0 * 7919 + g * 104729);
		for (let guard = 0; !b.ended && guard < 45; guard++) {
			forcedSwitches(b);
			if (b.ended) break;
			for (let s = 0; s < 2; s++) {
				const sd = b.sides[s];
				if (!sd.activeRequest || sd.activeRequest.wait || sd.isChoiceDone()) continue;
				const cs = jointCandidates(b, s, 3);
				const pick = cs.length ? cs[rnd() < 0.7 ? 0 : Math.floor(rnd() * cs.length)] : null;
				if (!pick || !b.choose(s ? 'p2' : 'p1', pick.choice)) b.choose(s ? 'p2' : 'p1', 'default');
			}
		}
		// victoria y margen (lo que le queda a cada bando: menos ruido que sólo ganar/perder)
		const left = s => b.sides[s].pokemon.reduce((a, p) => a + (p.fainted || p.hp <= 0 ? 0 : 0.35 + 0.65 * p.hp / p.maxhp), 0) / 4;
		const margin = left(0) - left(1);
		const win = b.ended ? (b.winner === 'p1' ? 1 : b.winner === 'p2' ? 0 : 0.5) : (margin > 0 ? 1 : margin < 0 ? 0 : 0.5);
		return [win, margin];
	};
	const wr = [], mg = [];
	for (const sel of req.selections) {
		const w1 = [], m1 = [];
		req.rivals.forEach((rv, r) => {
			const n = Array.isArray(games) ? games[r] : games;
			let w = 0, m = 0;
			for (let g = 0; g < n; g++) { const [a, c] = play(sel, rv, g + 1000 * r); w += a; m += c; }
			w1.push(n ? w / n : 0.5); m1.push(n ? m / n : 0);
		});
		wr.push(w1); mg.push(m1);
	}
	return { wr, margin: mg };
}

/** Hoja de equipo (lo que muestra un Open Team Sheet) de un equipo. */
function sheet(req) {
	const dex = dexFor(req.format);
	return req.team.map(s => card(normSet(s, dex), dex, !!req.mine));
}

module.exports = { leadState, bringRollouts, realisticWorlds, guessTeam, outcome, previewSetup, leadValue, leadDetail, pairSynergy, features, sheet };
