#!/usr/bin/env node
'use strict';
/**
 * Motor RooBot: proceso Node que habla JSON por líneas con Python (stdin -> stdout).
 * Petición:  {"id": 1, "cmd": "calc", ...}
 * Respuesta: {"id": 1, "ok": true, "result": ...}  ó  {"id": 1, "ok": false, "error": "..."}
 *
 * Comandos: ping, vocab, import, validate, complete_set, calc, sweep, teampreview,
 *           search, encode, static_value
 */
const readline = require('readline');
// stdout es el canal del protocolo: cualquier log interno va a stderr
console.log = (...a) => console.error(...a);
console.info = (...a) => console.error(...a);
const { PS, DEFAULT_FORMAT, dexFor, importTeam, normSet, completeOpponentSet, toID } = require('./sim');
const damage = require('./damage');
const { teamPreview } = require('./teampreview');
const { search, staticValue } = require('./search');
const { buildBattle } = require('./inject');
const { encode, vocab, F, G, A, N_OURS, N_THEIRS } = require('./encode');
const { genPosition, describe, positionTags } = require('./positions');
const coach = require('./coach');
const quiz = require('./quiz');
const replay = require('./replay'); const game = require('./game');


const handlers = {
	ping: () => ({ pong: true, format: DEFAULT_FORMAT, showdown: require('./sim').SD }),
	// ids de sprite de una lista de equipos (forma, forma base y Mega si lleva su piedra)
	sprite_ids: (r) => {
		const dex = dexFor(r.format);
		const out = new Set();
		const add = sp => { if (sp && sp.exists) { out.add(sp.spriteid); out.add(dex.species.get(sp.baseSpecies).spriteid); } };
		for (const team of r.teams || []) {
			for (const set of team) {
				const sp = dex.species.get(set.species); add(sp);
				const it = dex.items.get(set.item || '');
				if (it.exists && it.megaStone) {
					const ms = typeof it.megaStone === 'string' ? it.megaStone : Object.values(it.megaStone)[0];
					add(dex.species.get(ms));
				}
				if (sp.exists && sp.otherFormes) for (const f of sp.otherFormes) { const fs = dex.species.get(f); if (fs.isMega) add(fs); }
			}
		}
		return { ids: [...out].filter(Boolean).sort() };
	},
	vocab: (r) => {
		const v = vocab(r.format);
		return { sizes: v.sizes, F, G, A, N_OURS, N_THEIRS,
			species: [...v.species.keys()], moves: [...v.moves.keys()], items: [...v.items.keys()], abilities: [...v.abilities.keys()] };
	},
	import: (r) => importTeam(r.text, dexFor(r.format)),
	validate: (r) => {
		const fmt = r.format || DEFAULT_FORMAT;
		const v = PS.TeamValidator.get(fmt);
		const team = typeof r.team === 'string' ? PS.Teams.import(r.team) : r.team;
		const problems = v.validateTeam(team);
		return { ok: !problems, problems: problems || [] };
	},
	complete_set: (r) => completeOpponentSet(r.set, dexFor(r.format)),
	// Hojas de equipo abiertas (OTS): traen especie, objeto, habilidad y movimientos, pero no los SP.
	// A cada Pokémon sin SP se le pone el reparto del set real más parecido de los equipos guardados
	// (o uno típico por sus stats y movimientos), como hace la app con los sets ocultos del rival.
	// Objeto, habilidad, movimientos y naturaleza (si la hay) no se tocan.
	fill_spreads: (r) => {
		const dex = dexFor(r.format);
		const sets = PS.Teams.import(r.text) || [];
		const filled = [];
		for (const s of sets) {
			const total = Object.values(s.evs || {}).reduce((a, b) => a + (b || 0), 0);
			if (total > 0) continue;
			const partial = { species: s.species, item: s.item || '', ability: s.ability || '', moves: (s.moves || []).map(toID) };
			const full = completeOpponentSet(partial, dex, s.nature ? { nature: s.nature } : null);
			s.evs = Object.assign({ hp: 0, atk: 0, def: 0, spa: 0, spd: 0, spe: 0 }, full.evs);
			if (!s.nature) s.nature = full.nature;
			filled.push(dex.species.get(s.species).name);
		}
		return { text: PS.Teams.export(sets), filled };
	},
	normalize: (r) => {
		// sin apodos ni Tera, nivel 50: texto limpio de exportación de Showdown
		const sets = PS.Teams.import(r.text) || [];
		for (const s of sets) { s.name = ''; delete s.teraType; s.level = 50; delete s.ivs; }
		return PS.Teams.export(sets);
	},
	// categoría de cada movimiento (Physical / Special / Status) para el veredicto prudente
	move_kinds: (r) => { const dex = dexFor(r.format); const out = {}; for (const id of r.ids || []) { const m = dex.moves.get(id); out[id] = m.exists ? m.category : null; } return out; },
	calc: (r) => damage.calc(r),
	stats: (r) => damage.stats(r),
	sweep: (r) => damage.sweep(r),
	teampreview: (r) => teamPreview(r),
	search: (r) => search(r),
	game_new: game.game_new, game_info: game.game_info, game_step: game.game_step, game_end: game.game_end,
	static_value: (r) => ({ value: staticValue(r.state) }),
	gen_position: (r) => genPosition(r),
	position_tags: (r) => positionTags(r),
	describe: (r) => describe(r),
	outcome: (r) => coach.outcome(r),
	preview_setup: (r) => coach.previewSetup(r),
	lead_value: (r) => coach.leadValue(r),
	bring_rollouts: (r) => coach.bringRollouts(r),
	// juez aprendido: rasgos de posiciones reales (de repeticiones) y la valoración heurística, para ajustarlo
	judge_features: (r) => {
		const S = require('./search');
		return { names: S.JUDGE_FEATURES, rows: (r.states || []).map(st => {
			try {
				const built = buildBattle(st);
				if (built.ended) return null;
				S.setEvalVariant(9);
				const h = S.evaluate(built.b, 0); S.setEvalVariant(0);
				return { f: S.judgeFeatures(built.b, 0), h, hz: S.handZ(built.b, 0), fase: S.judgePhase(built.b) };
			} catch (e) { return null; }
		}) };
	},
	judge_reload: () => require('./search').loadJudge(),
	// experiencia: rasgos de decisiones reales (para aprender) y probabilidad de cada jugada conjunta
	policy_table: (r) => {
		const P = require('./policy');
		return { names: P.NAMES, rows: (r.items || []).map(it => {
			try {
				const built = buildBattle(it.state);
				if (built.ended) return null;
				return P.decisionTable(built.b, it.spec);
			} catch (e) { return null; }
		}) };
	},
	policy_logp: (r) => {
		const P = require('./policy');
		if (!P.model()) return { lp: null };
		const built = buildBattle(r.state);
		return { lp: P.jointLogp(built.b, 0, r.choices || []) };
	},
	policy_reload: () => require('./policy').loadModel(),
	endgame_line: (r) => require('./search').endgameLine(r),
	lead_detail: (r) => coach.leadDetail(r),
	realistic_worlds: (r) => coach.realisticWorlds(r),
	guess_team: (r) => coach.guessTeam(r),
	sheet: (r) => coach.sheet(r),
	quiz_question: (r) => quiz.question(r),
	speed_tiers: (r) => quiz.speedTiers(r),
	replay_parse: (r) => replay.parse(r),
	spec_choice: (r) => {
		// convierte una jugada por ranuras ({move,target,mega}|{switch}|{pass}) en la cadena de Showdown
		const built = buildBattle(r.state);
		return require('./search').choiceFromSpec(built.b, 0, r.spec);
	},
	replay_demo: (r) => replay.demoLog(r),
	simulate: (r) => {
		// juega un turno concreto y devuelve un registro resumido (para enseñar qué pasa)
		const built = buildBattle(r.state);
		const b = built.b;
		b.log = [];
		b.prng = new PS.PRNG(r.seed || '7,11,13,17');
		const ok1 = b.choose('p1', r.ours), ok2 = b.choose('p2', r.theirs);
		if (ok1 && ok2) require('./search').finishTurn(b);
		const keep = ['move', '-damage', '-heal', 'faint', '-supereffective', '-resisted', '-immune', '-crit', '-miss', '-fail',
			'-activate', 'switch', '-mega', 'cant', '-boost', '-unboost', '-weather', '-status', '-fieldstart', '-sidestart', '-enditem',
			'detailschange', '-curestatus', '-fieldend', '-sideend', 'turn', 'upkeep', '-terastallize', '-ability', '-item', '-singleturn'];
		const out = [];
		for (let k = 0; k < b.log.length; k++) {
			let line = b.log[k];
			if (line.startsWith('|split|')) { k++; line = b.log[k + 1] || ''; k++; }
			const parts = line.split('|');
			if (keep.includes(parts[1])) out.push(parts.slice(1));
		}
		return { ok: !!(ok1 && ok2), events: out };
	},
	encode: (r) => {
		const built = buildBattle(r.state);
		return encode(built.b, 0, r.state.format, true, r.preview || null);
	},
};

function respond(obj) {
	process.stdout.write(JSON.stringify(obj) + '\n');
}

const rl = readline.createInterface({ input: process.stdin, crlfDelay: Infinity });
rl.on('line', (line) => {
	line = line.trim();
	if (!line) return;
	let req;
	try { req = JSON.parse(line); } catch (e) { respond({ id: null, ok: false, error: 'JSON inválido' }); return; }
	const h = handlers[req.cmd];
	if (!h) { respond({ id: req.id, ok: false, error: `Comando desconocido: ${req.cmd}` }); return; }
	try {
		respond({ id: req.id, ok: true, result: h(req) });
	} catch (e) {
		respond({ id: req.id, ok: false, error: String(e && e.stack || e) });
	}
});
rl.on('close', () => process.exit(0));
