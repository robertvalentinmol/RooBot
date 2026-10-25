'use strict';
/**
 * Partidas completas contra el bot dentro de la app.
 * El motor guarda el combate (PS.Battle) entre peticiones; Python decide la jugada del bot
 * (búsqueda sobre el estado visto desde el bot) y la del humano viene de la interfaz.
 *
 *  game_new   {ours, theirs, oursOrder:[i,i,i,i], theirsOrder?, seed?} -> info
 *  game_info  {id}                                                   -> info (sin eventos nuevos)
 *  game_step  {id, ours?, theirs?}                                   -> info con los eventos del turno
 *  game_end   {id}                                                   -> libera el combate
 *
 * info: { id, turn, ended, winner, need: 'move'|'switch'|'wait'|'end', botNeeds: bool,
 *         forceSwitch:[bool,bool], switchOptions:[[...],[...]], state (visto por p1), botState (visto por p2),
 *         events:[...], log:[...] }
 */
const { PS, dexFor } = require('./sim');
const { teamPreview } = require('./teampreview');
const { stateFromBattle } = require('./positions');

const games = new Map();
let counter = 0;
const KEEP = ['move', '-damage', '-heal', 'faint', '-supereffective', '-resisted', '-immune', '-crit', '-miss', '-fail',
	'-activate', 'switch', 'drag', '-mega', 'cant', '-boost', '-unboost', '-weather', '-status', '-fieldstart', '-sidestart', '-enditem',
	'detailschange', '-curestatus', '-fieldend', '-sideend', 'turn', 'upkeep', '-ability', '-item', '-singleturn', 'win', 'tie'];

function eventsFrom(lines) {
	const out = [];
	for (let k = 0; k < lines.length; k++) {
		let line = lines[k];
		// |split|pX: la línea siguiente es la privada (PS exactos) y la otra la pública; nos quedamos con la pública
		if (line.startsWith('|split|')) { line = lines[k + 2] || ''; k += 2; }
		const parts = line.split('|');
		if (KEEP.includes(parts[1])) out.push(parts.slice(1));
	}
	return out;
}
function alive(p) { return p && !p.fainted && p.hp > 0; }

function needOf(b, s) {
	const side = b.sides[s], req = side.activeRequest;
	if (b.ended) return 'end';
	if (!req || req.wait || side.isChoiceDone()) return 'wait';
	if (req.forceSwitch) return 'switch';
	return 'move';
}

/** Resuelve solo los cambios forzados del bot (heurística) hasta que el humano tenga que decidir algo. */
function settle(b) {
	for (let guard = 0; guard < 6 && !b.ended; guard++) {
		const n1 = needOf(b, 1);
		if (n1 !== 'switch') break;
		// cambia sólo el bando del bot: forcedSwitches mira ambos, así que se aplica a una copia del criterio
		const side = b.sides[1], req = side.activeRequest;
		const bench = side.pokemon.filter(p => alive(p) && !p.isActive);
		const parts = [];
		const used = new Set();
		for (let slot = 0; slot < req.forceSwitch.length; slot++) {
			if (!req.forceSwitch[slot]) { parts.push('pass'); continue; }
			let best = null, bv = -Infinity;
			for (const p of bench) {
				if (used.has(p)) continue;
				const v = switchScore(b, 1, p);
				if (v > bv) { bv = v; best = p; }
			}
			if (best) { used.add(best); parts.push(`switch ${side.pokemon.indexOf(best) + 1}`); } else parts.push('pass');
		}
		if (!b.choose('p2', parts.join(', '))) b.choose('p2', 'default');
	}
}
function switchScore(b, s, p) {
	const { estDamage } = require('./search');
	let hurt = 0, hit = 0;
	for (const f of b.sides[1 - s].active) {
		if (!alive(f)) continue;
		for (const ms of f.moveSlots) {
			if (b.dex.moves.get(ms.id).category === 'Status') continue;
			try { hurt = Math.max(hurt, Math.min(1, estDamage(b, f, p, ms.id, false) / Math.max(1, p.hp))); } catch (e) {}
		}
		for (const ms of p.moveSlots) {
			if (b.dex.moves.get(ms.id).category === 'Status') continue;
			try { hit = Math.max(hit, Math.min(1, estDamage(b, p, f, ms.id, false) / Math.max(1, f.hp))); } catch (e) {}
		}
	}
	return p.hp / p.maxhp - 0.6 * hurt + 0.4 * hit;
}

function switchOptions(b, s) {
	const side = b.sides[s], req = side.activeRequest;
	if (!req || !req.forceSwitch) return [[], []];
	const bench = side.pokemon.filter(p => alive(p) && !p.isActive);
	const forced = req.forceSwitch.filter(Boolean).length;
	return [0, 1].map(slot => {
		if (!req.forceSwitch[slot]) return [{ choice: 'pass', kind: 'pass', label: 'pass' }];
		// más huecos que Pokémon en el banquillo: uno de los huecos se queda vacío
		const extra = bench.length < forced ? [{ choice: 'pass', kind: 'pass', label: 'dejar vacío' }] : [];
		const pk = side.active[slot];
		const opts = bench.map(p => ({ choice: `switch ${p.name}`, kind: 'switch', switchTo: p.name,
			label: `${pk ? pk.species.name : '—'} ⇄ ${p.species.name}`, hpPct: Math.round(100 * p.hp / p.maxhp), species: p.species.name }));
		return opts.length ? opts.concat(extra) : [{ choice: 'pass', kind: 'pass', label: 'pass' }];
	});
}

function view(b, s) {
	const st = stateFromBattle(b);
	st.format = b.format.id;
	if (s === 1) st.sides = [st.sides[1], st.sides[0]];
	return st;
}

function info(id, g, withEvents) {
	const b = g.b;
	const out = {
		id, turn: b.turn, ended: !!b.ended, winner: b.ended ? (b.winner === b.sides[0].name ? 'p1' : b.winner === b.sides[1].name ? 'p2' : 'tie') : null,
		need: needOf(b, 0), botNeeds: needOf(b, 1) === 'move',
		// movimientos bloqueados según la petición real (para no ofrecerlos)
		disabled: [0, 1].map(slot => {
			const r = b.sides[0].activeRequest;
			const a = r && r.active && r.active[slot];
			return a ? a.moves.filter(m => m.disabled).map(m => m.id) : [];
		}),
		forceSwitch: (b.sides[0].activeRequest && b.sides[0].activeRequest.forceSwitch) || null,
		switchOptions: switchOptions(b, 0),
		state: b.ended ? null : view(b, 0), botState: b.ended ? null : view(b, 1),
		brought: [b.sides[0].pokemon.map(p => p.species.name), b.sides[1].pokemon.map(p => p.species.name)],
		events: [],
	};
	if (withEvents) {
		out.events = eventsFrom(b.log.slice(g.logPos));
		g.logPos = b.log.length;
	}
	return out;
}

function newGame(req) {
	const format = req.format || 'gen9championsvgc2026regmc';
	const dex = dexFor(format);
	const b = new PS.Battle({ formatid: format, seed: req.seed || undefined });
	b.send = () => {};
	b.setPlayer('p1', { name: req.ourName || 'Tú', team: PS.Teams.pack(req.ours) });
	b.setPlayer('p2', { name: req.theirName || 'Bot', team: PS.Teams.pack(req.theirs) });
	const order = req.oursOrder.map(i => i + 1);
	if (!b.choose('p1', 'team ' + order.join(''))) throw new Error('Orden de equipo no válido');
	let tc;
	if (req.theirsOrder) {
		const o2 = req.theirsOrder.map(i => i + 1);
		tc = 'team ' + o2.join('');
	} else {
		tc = teamPreview({ format, ours: req.theirs, theirs: req.ours, temperature: req.botTemperature === undefined ? 0.25 : req.botTemperature }).choice;
	}
	if (!b.choose('p2', tc)) b.choose('p2', 'default');
	const id = 'g' + (++counter);
	const g = { b, logPos: 0, created: Date.now() };
	games.set(id, g);
	if (games.size > 20) games.delete(games.keys().next().value);
	settle(b);
	return info(id, g, true);
}

function get(id) {
	const g = games.get(id);
	if (!g) throw new Error('Partida caducada: empieza otra');
	return g;
}

function step(req) {
	const g = get(req.id), b = g.b;
	if (b.ended) return info(req.id, g, true);
	const n0 = needOf(b, 0), n1 = needOf(b, 1);
	if (n0 !== 'wait' && n0 !== 'end') {
		if (!req.ours) throw new Error('Falta tu jugada');
		if (!b.choose('p1', req.ours)) {
			const err = (b.sides[0].choice && b.sides[0].choice.error) || '';
			throw new Error('Jugada no válida' + (err ? ': ' + err : ''));
		}
	}
	if (n1 === 'move' && !b.ended) {
		// la jugada del humano ya está validada: Python calcula la del bot y vuelve a llamar con ella
		if (req.ours && !req.theirs) return Object.assign(info(req.id, g, false), { awaitingBot: true });
		if (!req.theirs || !b.choose('p2', req.theirs)) b.choose('p2', 'default');
	}
	settle(b);
	return info(req.id, g, true);
}

module.exports = {
	game_new: newGame,
	game_info: (r) => info(r.id, get(r.id), false),
	game_step: step,
	game_end: (r) => { games.delete(r.id); return { ok: true }; },
};
