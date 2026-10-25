'use strict';
/**
 * Daño exacto usando el simulador de Showdown.
 * Para cada tirada r = 0..15 forzamos el factor aleatorio (100-r)% y llamamos a
 * battle.actions.getDamage(), así salen las 16 tiradas idénticas al juego,
 * con todas las habilidades/objetos/cambios de Champions aplicados.
 */
const { PS, DEFAULT_FORMAT, dexFor, normSet, toID } = require('./sim');

const DUMMY = { species: 'Audino', ability: 'Klutz', item: '', moves: ['splash'], nature: 'Serious', level: 50 };
const WEATHER = { sun: 'sunnyday', rain: 'raindance', sand: 'sandstorm', snow: 'snowscape', none: '' };
const TERRAIN = { grassy: 'grassyterrain', electric: 'electricterrain', psychic: 'psychicterrain', misty: 'mistyterrain', none: '' };

function setWeather(b, w) {
	if (w === undefined || w === null || w === 'auto') return;
	const id = WEATHER[w] !== undefined ? WEATHER[w] : toID(w);
	if (!id) b.field.clearWeather(); else { b.field.setWeather(id, b.sides[0].active[0], b.dex.conditions.get(id)); if (b.field.weatherState) b.field.weatherState.duration = 5; }
}
function setTerrain(b, t) {
	if (t === undefined || t === null || t === 'auto') return;
	const id = TERRAIN[t] !== undefined ? TERRAIN[t] : toID(t);
	if (!id) b.field.clearTerrain(); else { b.field.setTerrain(id, b.sides[0].active[0], b.dex.conditions.get(id)); if (b.field.terrainState) b.field.terrainState.duration = 5; }
}

/**
 * Crea un combate de dobles con atacante en p1a y defensor en p2a.
 * opts: atkPartner, defPartner (sets), atkMega, defMega, weather, terrain,
 *       atkBoosts, defBoosts, atkStatus, defHpFrac, screens [reflect|lightscreen|auroraveil],
 *       helpingHand, format
 */
function makeDuel(attacker, defender, opts = {}) {
	const format = opts.format || DEFAULT_FORMAT;
	const dex = dexFor(format);
	const a = normSet(attacker, dex), d = normSet(defender, dex);
	const ap = normSet(opts.atkPartner || DUMMY, dex), dp = normSet(opts.defPartner || DUMMY, dex);
	const b = new PS.Battle({ formatid: format, seed: '1,2,3,4' });
	b.setPlayer('p1', { name: 'atk', team: PS.Teams.pack([a, ap]) });
	b.setPlayer('p2', { name: 'def', team: PS.Teams.pack([d, dp]) });
	b.choose('p1', 'team 12');
	b.choose('p2', 'team 12');
	const att = b.sides[0].active[0], def = b.sides[1].active[0];
	if (opts.atkMega && att.canMegaEvo) b.actions.runMegaEvo(att);
	if (opts.defMega && def.canMegaEvo) b.actions.runMegaEvo(def);
	// Las entradas al campo (Intimidación, Sequía...) ya ocurrieron: fijamos el estado pedido.
	att.boosts = Object.assign({ atk: 0, def: 0, spa: 0, spd: 0, spe: 0, accuracy: 0, evasion: 0 }, opts.atkBoosts || {});
	def.boosts = Object.assign({ atk: 0, def: 0, spa: 0, spd: 0, spe: 0, accuracy: 0, evasion: 0 }, opts.defBoosts || {});
	for (const p of [b.sides[0].active[1], b.sides[1].active[1]]) p.boosts = { atk: 0, def: 0, spa: 0, spd: 0, spe: 0, accuracy: 0, evasion: 0 };
	setWeather(b, opts.weather === undefined ? 'auto' : opts.weather);
	setTerrain(b, opts.terrain === undefined ? 'auto' : opts.terrain);
	if (opts.atkStatus) { att.status = ''; att.setStatus(opts.atkStatus, att, null, true); }
	if (opts.defHpFrac !== undefined) def.hp = Math.max(1, Math.round(def.maxhp * opts.defHpFrac));
	for (const sc of opts.screens || []) b.sides[1].addSideCondition(sc, 'debug');
	if (opts.helpingHand) att.addVolatile('helpinghand');
	return { b, att, def };
}

function prepMove(b, att, def, moveId, crit, spread, hit) {
	const move = b.dex.getActiveMove(moveId);
	move.willCrit = !!crit;
	b.setActiveMove(move, att, def);
	b.singleEvent('ModifyType', move, null, att, def, move, move);
	b.singleEvent('ModifyMove', move, null, att, def, move, move);
	let m = b.runEvent('ModifyType', att, def, move, move) || move;
	m = b.runEvent('ModifyMove', att, def, m, m) || m;
	if (spread) m.spreadHit = true;
	m.hit = hit || 1;
	return m;
}

/** 16 tiradas (o null si inmune / sin daño). */
function rollsFor(b, att, def, moveId, { crit = false, spread = false, hit = 1 } = {}) {
	const out = [];
	const savedRandomizer = b.randomizer;
	// Las bayas de resistencia (Caoca, etc.) se consumen dentro de getDamage: guardamos y
	// restauramos el objeto de ambos para que las 16 tiradas sean coherentes.
	const snapItem = p => ({ item: p.item, itemState: Object.assign({}, p.itemState), lastItem: p.lastItem, ate: p.ateBerry, used: p.usedItemThisTurn });
	const restoreItem = (p, s) => { p.item = s.item; p.itemState = s.itemState; p.lastItem = s.lastItem; p.ateBerry = s.ate; p.usedItemThisTurn = s.used; };
	const sa = snapItem(att), sd = snapItem(def);
	for (let r = 0; r < 16; r++) {
		b.randomizer = function (d) { return this.trunc(this.trunc(d * (100 - r)) / 100); };
		const m = prepMove(b, att, def, moveId, crit, spread, hit);
		const dmg = b.actions.getDamage(att, def, m, true);
		restoreItem(att, sa); restoreItem(def, Object.assign({}, sd, { itemState: Object.assign({}, sd.itemState) }));
		if (dmg === false || dmg === null || dmg === undefined) { b.randomizer = savedRandomizer; return null; }
		out.push(dmg);
	}
	b.randomizer = savedRandomizer;
	b.clearActiveMove(true);
	return out.reverse(); // de menor (85%) a mayor (100%)
}

function moveInfo(b, att, def, moveId) {
	const m = prepMove(b, att, def, moveId, false, false, 1);
	let hits = [1, 1];
	if (Array.isArray(m.multihit)) hits = m.multihit;
	else if (typeof m.multihit === 'number') hits = [m.multihit, m.multihit];
	const critRatio = b.runEvent('ModifyCritRatio', att, def, m, m.critRatio || 0);
	const critMult = [0, 24, 8, 2, 1];
	const cr = Math.max(0, Math.min(4, critRatio));
	return {
		id: m.id, name: m.name, type: m.type, category: m.category, basePower: m.basePower,
		accuracy: m.accuracy === true ? 100 : m.accuracy, target: m.target,
		spreadByDefault: ['allAdjacentFoes', 'allAdjacent'].includes(m.target),
		hits, multihitType: m.multihitType || null,
		critChance: m.willCrit ? 1 : (cr ? 1 / critMult[cr] : 0),
		contact: !!m.flags.contact,
	};
}

/**
 * Cálculo completo para la calculadora tipo Koh.
 * req: {attacker, defender, move, opts..., spread?: bool|null}
 * Devuelve tiradas normales y con crítico, por golpe, y las variantes "a vida completa"
 * y "dañado" (importa para Multiscale / Shadow Shield / Banda Focus).
 */
function calc(req) {
	const { b, att, def } = makeDuel(req.attacker, req.defender, req);
	const info = moveInfo(b, att, def, req.move);
	const spread = req.spread === undefined || req.spread === null ? info.spreadByDefault : !!req.spread;
	const maxHits = info.hits[1];
	const variants = {};
	const fullHp = def.hp;
	for (const state of ['full', 'damaged']) {
		def.hp = state === 'full' ? fullHp : Math.max(1, def.maxhp - 1);
		const perHit = [], perHitCrit = [];
		for (let h = 1; h <= maxHits; h++) {
			perHit.push(rollsFor(b, att, def, req.move, { crit: false, spread, hit: h }));
			perHitCrit.push(rollsFor(b, att, def, req.move, { crit: true, spread, hit: h }));
		}
		variants[state] = { normal: perHit, crit: perHitCrit };
	}
	def.hp = fullHp;
	return {
		move: info, spread,
		attacker: { species: att.species.name, stats: att.storedStats, item: att.item, ability: att.ability },
		defender: {
			species: def.species.name, maxhp: def.maxhp, hp: def.hp,
			stats: def.storedStats, item: def.item, ability: def.ability,
			types: def.getTypes(),
		},
		weather: b.field.weather, terrain: b.field.terrain,
		rolls: variants,
	};
}

/**
 * Barrido para el optimizador: tiradas para cada valor de SP (0..32) en la defensa relevante
 * (def o spd según la categoría), con la naturaleza del defensor fija.
 */
function sweep(req) {
	const dex = dexFor(req.format);
	const def0 = normSet(req.defender, dex);
	const mv = dex.moves.get(req.move);
	let stat = mv.category === 'Physical' ? 'def' : 'spd';
	if (mv.overrideDefensiveStat) stat = mv.overrideDefensiveStat;
	const results = [];
	for (let v = 0; v <= 32; v++) {
		const d = JSON.parse(JSON.stringify(def0));
		d.evs[stat] = v;
		const r = calc(Object.assign({}, req, { defender: d }));
		results.push({ sp: v, maxhp: r.defender.maxhp, rolls: r.rolls });
	}
	return { stat, move: results.length ? calc(Object.assign({}, req, { defender: def0 })).move : null, results };
}

/** Estadísticas finales (nivel 50, fórmula de Champions con Stat Points) de un set, con o sin Mega. */
function stats(req) {
	const { b, att } = makeDuel(req.set, { species: 'Audino', ability: 'Klutz', moves: ['splash'] }, { format: req.format, atkMega: !!req.mega, weather: 'none', terrain: 'none' });
	return { species: att.species.name, baseStats: att.species.baseStats, stats: Object.assign({ hp: att.maxhp }, att.storedStats), types: att.getTypes(), ability: att.ability };
}

module.exports = { makeDuel, rollsFor, moveInfo, calc, sweep, stats, setWeather, setTerrain, WEATHER, TERRAIN };
