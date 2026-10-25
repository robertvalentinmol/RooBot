'use strict';
/**
 * Lector de repeticiones de Showdown (log del protocolo).
 *
 * Reconstruye, al principio de cada turno, la posición tal como la veía un jugador y extrae lo
 * que hizo ese jugador en ese turno (movimiento + objetivo + Mega, o cambio). Con eso la app
 * analiza cada decisión igual que un ejercicio.
 *
 * parse(req): { log, side?: 'p1'|'p2', ourTeam?: [sets], format? }
 *   → { players, format, side, winner, sheets: {ours, theirs}, decisions: [{ turn, state, spec, label, known, reason }] }
 */
const { PS, dexFor, normSet, toID, completeOpponentSet, guessSpread } = require('./sim');

const PROTECT = new Set(['protect', 'detect', 'spikyshield', 'kingsshield', 'banefulbunker', 'silktrap', 'burningbulwark', 'obstruct', 'maxguard']);
const WEATHER = { raindance: 'raindance', sunnyday: 'sunnyday', sandstorm: 'sandstorm', snowscape: 'snowscape', snow: 'snowscape', hail: 'snowscape',
	desolateland: 'desolateland', primordialsea: 'primordialsea', deltastream: 'deltastream' };
const TERRAIN = { psychicterrain: 'psychicterrain', grassyterrain: 'grassyterrain', electricterrain: 'electricterrain', mistyterrain: 'mistyterrain' };
const SIDE = { tailwind: 4, reflect: 5, lightscreen: 5, auroraveil: 5, safeguard: 5, mist: 5 };
const BOOSTS = ['atk', 'def', 'spa', 'spd', 'spe', 'accuracy', 'evasion'];

/** Extrae las líneas del protocolo de: un log, el JSON de replay.pokemonshowdown.com o el HTML de una repetición. */
function extractLog(text) {
	text = String(text || '');
	const t = text.trim();
	if (t.startsWith('{')) { try { const j = JSON.parse(t); if (j.log) return j.log; } catch (e) { /* no era JSON */ } }
	const m = text.match(/<script[^>]*class="battle-log-data"[^>]*>([\s\S]*?)<\/script>/);
	if (m) return m[1].replace(/\\\//g, '/');
	return text;
}

/** Quita las líneas privadas (|split|): se queda con la versión pública, como un espectador. */
function publicLines(text) {
	const raw = extractLog(text).split(/\r?\n/);
	const out = [];
	for (let i = 0; i < raw.length; i++) {
		const l = raw[i];
		if (l.startsWith('|split|')) { out.push(raw[i + 2] || ''); i += 2; continue; }
		if (l.startsWith('|')) out.push(l);
	}
	return out;
}

function hpOf(s) {
	// "55/100", "0 fnt", "123/180 brn"
	const parts = String(s || '').trim().split(' ');
	const [a, b] = parts[0].split('/');
	const frac = b ? Math.max(0, Math.min(1, (+a) / (+b))) : (+a === 0 ? 0 : 1);
	const st = parts[1] && parts[1] !== 'fnt' ? parts[1] : '';
	return { frac, fnt: parts[1] === 'fnt' || frac === 0, status: st };
}

function sideOf(ident) { return ident.slice(0, 2); }
function posOf(ident) { return ident[2] === 'b' ? 1 : 0; }
function nickOf(ident) { return ident.split(': ').slice(1).join(': '); }

function parse(req) {
	const lines = publicLines(req.log);
	let format = req.format || null;
	const players = {};
	const preview = { p1: [], p2: [] };
	const showteam = { p1: null, p2: null };
	const mons = { p1: {}, p2: {} };            // por apodo
	const active = { p1: [null, null], p2: [null, null] };
	const megaUsed = { p1: false, p2: false };
	const conds = { p1: {}, p2: {} };            // id → turno en que se puso
	const field = {};                            // weather/terrain/trickroom → {id, set, dur}
	let turn = 0, phase = 'between', winner = null;
	const snaps = [];
	let cur = null;                              // acciones del turno en curso por lado/posición

	const setTurn = () => (phase === 'turn' ? turn : turn + 1);
	const mon = (ident, details) => {
		const s = sideOf(ident), nick = nickOf(ident);
		if (!mons[s][nick]) {
			mons[s][nick] = { nick, species: '', hp: 1, fainted: false, status: '', boosts: {}, mega: false, item: null,
				appeared: false, moved: false, protect: 0, lastMove: null, vol: {}, firstSeenTurn: null };
		}
		const m = mons[s][nick];
		if (details) m.species = details.split(',')[0].trim();
		return m;
	};
	const snapshot = () => {
		const copy = JSON.parse(JSON.stringify({ mons, active, megaUsed, conds, field }));
		snaps.push({ turn, ...copy, actions: { p1: [null, null], p2: [null, null] } });
		cur = snaps[snaps.length - 1].actions;
	};

	for (const line of lines) {
		const p = line.split('|');
		const t = p[1];
		// habilidades que se ven en el combate (Intimidate, Drought, Levitate…)
		try {
			if (t === '-ability' && /^p[12][ab]?: /.test(p[2] || '') && !(p[4] || '').startsWith('[from]')) mon(p[2]).knownAbility = toID(p[3]);
			else if (t === '-activate' && (p[3] || '').startsWith('ability: ') && /^p[12][ab]?: /.test(p[2] || '')) mon(p[2]).knownAbility = toID(p[3].slice(9));
			else {
				const fr = p.find(x => x && x.startsWith('[from] ability: '));
				if (fr) {
					const of = p.find(x => x && x.startsWith('[of] '));
					const who = of ? of.slice(5) : p[2];
					if (/^p[12][ab]?: /.test(who || '')) mon(who).knownAbility = toID(fr.slice(16));
				}
			}
		} catch (e) { /* línea rara */ }
		switch (t) {
		case 'player': if (p[2] && p[3]) players[p[2]] = p[3]; break;
		case 'gametype': break;
		case 'tier': if (!format) format = toID(p[2]); break;
		case 'poke': preview[p[2]].push(p[3].split(',')[0].trim()); break;
		case 'showteam': try { showteam[p[2]] = PS.Teams.unpack(p.slice(3).join('|')); } catch (e) { /* formato raro */ } break;
		case 'turn':
			turn = +p[2]; phase = 'turn';
			snapshot();
			break;
		case 'upkeep': phase = 'between'; break;
		case 'win': winner = p[2]; break;
		case 'switch': case 'drag': {
			const s = sideOf(p[2]), pos = posOf(p[2]);
			const prev = active[s][pos];
			if (prev && mons[s][prev]) { const o = mons[s][prev]; o.boosts = {}; o.vol = {}; o.protect = 0; o.moved = false; }
			const m = mon(p[2], p[3]);
			const h = hpOf(p[4]);
			m.hp = h.frac; m.fainted = h.fnt; if (h.status) m.status = h.status;
			m.appeared = true; m.moved = false; m.protect = 0; m.vol = {}; m.boosts = {};
			if (m.firstSeenTurn === null) m.firstSeenTurn = turn;
			// cambio voluntario: en mitad del turno, antes de que ese hueco haya actuado
			if (t === 'switch' && phase === 'turn' && cur && prev && prev !== m.nick && !cur[s][pos]) {
				const pm = mons[s][prev];
				if (pm && !pm.fainted) cur[s][pos] = { kind: 'switch', to: m.species };
			}
			active[s][pos] = m.nick;
			break;
		}
		case 'replace': break;
		case 'detailschange': mon(p[2], p[3]); break;
		case '-mega': {
			const s = sideOf(p[2]);
			const m = mon(p[2]); m.mega = true; megaUsed[s] = true;
			if (phase === 'turn' && cur) cur[s]['mega' + posOf(p[2])] = true;
			break;
		}
		case 'move': {
			const s = sideOf(p[2]), pos = posOf(p[2]);
			const m = mon(p[2]);
			const fromOther = line.includes('[from]');
			const mv = toID(p[3]);
			if (!fromOther) {
				m.moved = true; m.lastMove = mv;
				m.moveset = m.moveset || []; if (!m.moveset.includes(mv)) m.moveset.push(mv);
				m.protect = PROTECT.has(mv) ? m.protect + 1 : 0;
				if (phase === 'turn' && cur && !cur[s][pos]) {
					let target = null;
					const tgt = p[4] || '';
					if (tgt && /^p[12][ab]/.test(tgt) && !line.includes('[spread]')) {
						const ts = sideOf(tgt), tp = posOf(tgt);
						target = ts === s ? -(tp + 1) : tp + 1;
						if (ts === s && tp === pos) target = null;   // a sí mismo
					}
					cur[s][pos] = { kind: 'move', move: mv, target };
				}
			}
			break;
		}
		case 'cant': {
			const s = sideOf(p[2]), pos = posOf(p[2]);
			if (phase === 'turn' && cur && !cur[s][pos] && !(p[3] || '').startsWith('ability:')) cur[s][pos] = { kind: 'unknown', reason: p[3] };
			break;
		}
		case '-damage': case '-heal': case '-sethp': {
			const m = mon(p[2]); const h = hpOf(p[3]);
			m.hp = h.frac; if (h.fnt) m.fainted = true;
			break;
		}
		case 'faint': { const m = mon(p[2]); m.fainted = true; m.hp = 0; break; }
		case '-status': mon(p[2]).status = p[3]; break;
		case '-curestatus': mon(p[2]).status = ''; break;
		case '-cureteam': for (const n in mons[sideOf(p[2])]) mons[sideOf(p[2])][n].status = ''; break;
		case '-boost': case '-unboost': {
			const m = mon(p[2]); const k = p[3]; const v = (+p[4] || 0) * (t === '-boost' ? 1 : -1);
			m.boosts[k] = Math.max(-6, Math.min(6, (m.boosts[k] || 0) + v));
			break;
		}
		case '-setboost': mon(p[2]).boosts[p[3]] = +p[4]; break;
		case '-clearboost': case '-clearallboost': {
			if (t === '-clearallboost') { for (const s of ['p1', 'p2']) for (const n in mons[s]) mons[s][n].boosts = {}; } else mon(p[2]).boosts = {};
			break;
		}
		case '-clearnegativeboost': { const m = mon(p[2]); for (const k in m.boosts) if (m.boosts[k] < 0) m.boosts[k] = 0; break; }
		case '-enditem': {
			const m = mon(p[2]);
			m.knownItem = m.knownItem || toID(p[3]);
			m.item = '';
			break;
		}
		case '-item': {
			const m = mon(p[2]);
			if (/move: (Trick|Switcheroo|Bestow|Thief|Covet)/.test(line)) m.item = toID(p[3]);
			else m.knownItem = toID(p[3]);
			break;
		}
		case '-start': {
			const m = mon(p[2]); const e = toID((p[3] || '').replace('move: ', ''));
			if (e === 'substitute') m.vol.substitute = true;
			if (e === 'taunt') m.vol.taunt = 3;
			if (e === 'encore') m.vol.encore = m.lastMove;
			if (e === 'confusion') m.vol.confusion = 2;
			if (/^perish(\d)/.test(e)) m.vol.perish = +e.slice(6);
			break;
		}
		case '-end': {
			const m = mon(p[2]); const e = toID((p[3] || '').replace('move: ', ''));
			if (e === 'substitute') delete m.vol.substitute;
			if (e === 'taunt') delete m.vol.taunt;
			if (e === 'encore') delete m.vol.encore;
			if (e === 'confusion') delete m.vol.confusion;
			break;
		}
		case '-weather': {
			const w = toID(p[2]);
			if (line.includes('[upkeep]')) break;
			if (w === 'none' || !WEATHER[w]) delete field.weather;
			else field.weather = { id: WEATHER[w], set: setTurn(), dur: 5 };
			break;
		}
		case '-fieldstart': {
			const e = toID((p[2] || '').replace('move: ', ''));
			if (TERRAIN[e]) field.terrain = { id: TERRAIN[e], set: setTurn(), dur: 5 };
			if (e === 'trickroom') field.trickroom = { id: 'trickroom', set: setTurn(), dur: 5 };
			if (e === 'gravity') field.gravity = { id: 'gravity', set: setTurn(), dur: 5 };
			break;
		}
		case '-fieldend': {
			const e = toID((p[2] || '').replace('move: ', ''));
			if (TERRAIN[e] && field.terrain && field.terrain.id === TERRAIN[e]) delete field.terrain;
			if (e === 'trickroom') delete field.trickroom;
			if (e === 'gravity') delete field.gravity;
			break;
		}
		case '-sidestart': {
			const s = p[2].slice(0, 2); const e = toID((p[3] || '').replace('move: ', ''));
			if (SIDE[e]) conds[s][e] = setTurn();
			break;
		}
		case '-sideend': {
			const s = p[2].slice(0, 2); const e = toID((p[3] || '').replace('move: ', ''));
			delete conds[s][e];
			break;
		}
		default: break;
		}
	}

	format = format || 'gen9championsvgc2026regmc';
	if (!PS.Dex.formats.get(format).exists) format = 'gen9championsvgc2026regmc';
	const dex = dexFor(format);
	const baseId = name => toID(dex.species.get(name).baseSpecies);

	// ¿Qué lado es el jugador? El que más coincide con su equipo (o el indicado)
	let side = req.side;
	const ourTeam = (req.ourTeam || []).map(s => normSet(s, dex));
	const ourIds = new Set(ourTeam.map(s => baseId(s.species)));
	const speciesOf = s => (showteam[s] ? showteam[s].map(x => x.species) : preview[s]).concat(Object.values(mons[s]).map(m => m.species));
	const overlap = s => new Set(speciesOf(s).map(baseId).filter(x => ourIds.has(x))).size;
	if (side !== 'p1' && side !== 'p2') side = overlap('p2') > overlap('p1') ? 'p2' : 'p1';
	const opp = side === 'p1' ? 'p2' : 'p1';
	const useOurFile = ourTeam.length && overlap(side) >= Math.min(4, new Set(speciesOf(side).map(baseId)).size);

	// Hojas: la nuestra del archivo (SP reales) si coincide; si no, la hoja abierta con SP estimados
	const guess = s => completeOpponentSet({ name: s.species, species: s.species, item: s.item, ability: s.ability,
		moves: (s.moves || []).map(toID) }, dex, s.nature ? { nature: s.nature } : null);
	const revealed = side2 => {
		// sin hoja abierta: especie de la vista previa + lo que se vio en el combate
		return preview[side2].map(sp => {
			const ms = Object.values(mons[side2]).filter(m => baseId(m.species) === baseId(sp));
			const moves = [...new Set(ms.flatMap(m => m.moveset || []))];
			const item = (ms.find(m => m.knownItem) || {}).knownItem || '';
			// la habilidad vista, si es de la forma base (la de la Mega no cuenta)
			const spd = dex.species.get(sp);
			const legal = Object.values(spd.abilities || {}).map(toID);
			const ab = ms.map(m => m.knownAbility).find(a => a && legal.includes(a)) || '';
			return { species: sp, item, ability: ab, moves };
		});
	};
	const sheetFor = s => {
		if (s === side && useOurFile) return ourTeam;
		if (showteam[s]) return showteam[s].map(x => Object.assign(guess(x), { natureKnown: !!x.nature }));
		// sin hoja: lo visto + el set real más parecido; los mundos posibles parten de lo visto
		return revealed(s).map(x => Object.assign(guess(x), { natureKnown: false,
			seen: { item: x.item, ability: x.ability, moves: x.moves } }));
	};
	const ourSheet = sheetFor(side), theirSheet = sheetFor(opp);
	const setFor = (sheet, species) => sheet.find(x => baseId(x.species) === baseId(species));

	// los 4 que trajo cada uno: los que salieron (el resto, completando con la hoja)
	const brought = s => {
		const seen = Object.values(mons[s]).filter(m => m.appeared).map(m => m.species);
		const out = [...new Set(seen.map(baseId))];
		return out;
	};
	const ourBrought = brought(side);
	const fillOurs = ourSheet.map(x => baseId(x.species)).filter(id => !ourBrought.includes(id)).slice(0, Math.max(0, 4 - ourBrought.length));

	const remaining = (rec, D, N) => Math.max(1, D - (N - rec));
	const toState = snap => {
		const sideState = (s, isOurs) => {
			const sheet = isOurs ? ourSheet : theirSheet;
			const list = [];
			for (const nick in snap.mons[s]) {
				const m = snap.mons[s][nick];
				if (!m.appeared) continue;
				const set = setFor(sheet, m.species);
				if (!set) continue;
				const slot = snap.active[s].indexOf(nick);
				const st = {
					set, active: slot >= 0 && !m.fainted ? slot : null, fainted: !!m.fainted, hpFrac: m.fainted ? 0 : m.hp,
					status: m.status && m.status !== 'fnt' ? m.status : '', boosts: m.boosts, mega: !!m.mega,
					item: m.item === null ? null : m.item, activeTurns: m.moved ? 1 : 0, revealed: true,
					protectCount: slot >= 0 ? m.protect : 0,
				};
				if (slot >= 0) {
					if (m.vol.substitute) st.substitute = 1;
					if (m.vol.taunt) st.taunt = m.vol.taunt;
					if (m.vol.encore) st.encore = { move: m.vol.encore, turns: 2 };
					if (m.vol.confusion) st.confusion = 2;
					if (m.vol.perish) st.perish = m.vol.perish;
					const it = toID(set.item);
					if (m.moved && m.lastMove && ['choicescarf', 'choiceband', 'choicespecs'].includes(it) && m.item !== '') st.choiceLock = m.lastMove;
				}
				list.push(st);
			}
			if (isOurs) {
				// los nuestros que aún no habían salido (los conocemos: salen más tarde o los completamos)
				for (const id of ourBrought.concat(fillOurs)) {
					if (list.some(x => baseId(x.set.species) === id)) continue;
					const set = sheet.find(x => baseId(x.species) === id);
					if (set) list.push({ set, active: null, fainted: false, hpFrac: 1, boosts: {}, revealed: true });
				}
			} else {
				// rival: los no vistos son desconocidos (el modo realista los muestrea de su hoja)
				const seen = new Set(list.map(x => baseId(x.set.species)));
				for (const set of sheet) {
					if (list.length >= 4) break;
					if (!seen.has(baseId(set.species))) { list.push({ set, active: null, fainted: false, hpFrac: 1, boosts: {}, revealed: false }); seen.add(baseId(set.species)); }
				}
			}
			const c = {};
			for (const k in snap.conds[s]) c[k] = remaining(snap.conds[s][k], SIDE[k], snap.turn);
			return { megaUsed: !!snap.megaUsed[s], totalFainted: list.filter(x => x.fainted).length, conditions: c, pokemon: list };
		};
		const f = {};
		if (snap.field.weather) { f.weather = snap.field.weather.id; f.weatherTurns = remaining(snap.field.weather.set, 5, snap.turn); }
		if (snap.field.terrain) { f.terrain = snap.field.terrain.id; f.terrainTurns = remaining(snap.field.terrain.set, 5, snap.turn); }
		if (snap.field.trickroom) f.trickroom = remaining(snap.field.trickroom.set, 5, snap.turn);
		if (snap.field.gravity) f.gravity = remaining(snap.field.gravity.set, 5, snap.turn);
		return { format, turn: snap.turn, field: f, sides: [sideState(side, true), sideState(opp, false)] };
	};

	const decisions = [];
	for (const snap of snaps) {
		const acts = snap.actions[side];
		const state = toState(snap);
		const spec = [], labels = [];
		let known = true, reason = '';
		for (let pos = 0; pos < 2; pos++) {
			const nick = snap.active[side][pos];
			const m = nick ? snap.mons[side][nick] : null;
			if (!m || m.fainted) { spec.push({ pass: true }); continue; }
			const a = acts[pos];
			const name = m.species;
			if (!a || a.kind === 'unknown') {
				known = false;
				reason = a && a.reason ? `${name} no pudo moverse (${a.reason})` : `${name} no llegó a actuar (cayó antes o no se ve su elección)`;
				spec.push({ pass: true });
				continue;
			}
			if (a.kind === 'switch') { spec.push({ switch: a.to }); labels.push(`${name} ⇄ ${a.to}`); continue; }
			const mega = !!acts['mega' + pos];
			spec.push({ move: a.move, target: a.target, mega });
			const mvName = dex.moves.get(a.move).name;
			labels.push(`${name}: ${mvName}${mega ? ' (Mega)' : ''}`);
		}
		decisions.push({ turn: snap.turn, state, spec, label: labels.join(' | '), known, reason });
	}
	const card = s => ({ species: s.species, item: dex.items.get(s.item).name || '', ability: dex.abilities.get(s.ability).name,
		nature: s.nature, moves: s.moves.map(m => dex.moves.get(m).name) });
	return {
		format, players, side, opp, winner, ourName: players[side] || side, oppName: players[opp] || opp,
		usedOurFile: !!useOurFile, openSheets: !!showteam[opp],
		sheets: { ours: ourSheet, theirs: theirSheet }, cards: { ours: ourSheet.map(card), theirs: theirSheet.map(card) },
		preview: { ours: ourSheet.map(s => s.species), theirs: theirSheet.map(s => s.species) },
		decisions,
	};
}

/** Genera el log público de una partida completa entre dos equipos (para probar el lector). */
function demoLog(req) {
	const { genPosition } = require('./positions');
	const format = req.format;
	const dex = dexFor(format);
	const ours = req.ours.map(s => normSet(s, dex)), theirs = req.theirs.map(s => normSet(s, dex));
	const b = new PS.Battle({ formatid: format });
	b.send = () => {};
	b.setPlayer('p1', { name: req.p1 || 'Tú', team: PS.Teams.pack(ours) });
	b.setPlayer('p2', { name: req.p2 || 'Rival', team: PS.Teams.pack(theirs) });
	b.showOpenTeamSheets && b.showOpenTeamSheets();
	const { teamPreview } = require('./teampreview');
	b.choose('p1', teamPreview({ format, ours, theirs, temperature: 0.1 }).choice);
	b.choose('p2', teamPreview({ format, ours: theirs, theirs: ours, temperature: 0.1 }).choice);
	const { jointCandidates } = require('./search');
	const { actionMasks } = require('./encode');
	let guard = 0;
	while (!b.ended && guard++ < 400) {
		for (let s = 0; s < 2; s++) {
			const side = b.sides[s], id = s ? 'p2' : 'p1';
			if (!side.activeRequest || side.activeRequest.wait || side.isChoiceDone()) continue;
			if (!side.activeRequest.forceSwitch) {
				const c = jointCandidates(b, s, 3);
				if (c.length && b.choose(id, c[Math.random() < 0.7 ? 0 : Math.floor(Math.random() * c.length)].choice)) continue;
			}
			if (!b.choose(id, 'default')) b.choose(id, 'pass');
		}
	}
	return { log: b.log.join('\n'), winner: b.winner };
}

module.exports = { parse, publicLines, extractLog, demoLog };
