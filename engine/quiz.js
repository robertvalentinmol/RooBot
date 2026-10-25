'use strict';
/**
 * Entrenador de velocidades y daños.
 *
 * Genera preguntas como las que te haces en un torneo: del rival conoces su hoja abierta
 * (especie, objeto, habilidad, movimientos y naturaleza) pero NO sus SP. Por eso las respuestas
 * tienen en cuenta todo el rango posible (0..32 SP) y la explicación te dice a partir de cuántos
 * SP cambia el resultado. Al final se enseña también lo que lleva el equipo concreto.
 */
const { dexFor, normSet, toID } = require('./sim');
const damage = require('./damage');

const STATN = { hp: 'PS', atk: 'Atq', def: 'Def', spa: 'AtE', spd: 'DfE', spe: 'Vel' };

function rnd(n) { return Math.floor(Math.random() * n); }
function pick(a) { return a[rnd(a.length)]; }

function megaForm(set, dex) {
	const it = dex.items.get(set.item);
	if (!it.megaStone) return null;
	let name = it.megaStone;
	if (typeof name === 'object') name = name[dex.species.get(set.species).baseSpecies] || Object.values(name)[0];
	const sp = dex.species.get(name);
	return sp.exists ? sp : null;
}

/** Estadística a nivel 50 con la fórmula de Champions. */
function statOf(base, sp, nature, stat, dex) {
	if (stat === 'hp') return base + sp + 75;
	let v = base + sp + 20;
	const n = dex.natures.get(nature || 'Serious');
	if (n.plus === stat) v = Math.floor(v * 1.1);
	if (n.minus === stat) v = Math.floor(v * 0.9);
	return v;
}

function boostMul(stage) { return stage >= 0 ? (2 + stage) / 2 : 2 / (2 - stage); }

/** Velocidad efectiva con objeto, cambios de características y Viento Afín. */
function effSpeed(raw, { scarf = false, stage = 0, tailwind = false } = {}) {
	let s = Math.floor(raw * boostMul(stage));
	if (scarf) s = Math.floor(s * 1.5);
	if (tailwind) s *= 2;
	return s;
}

function card(set, dex, mine) {
	const sp = dex.species.get(set.species);
	const mega = megaForm(set, dex);
	const n = dex.natures.get(set.nature || 'Serious');
	const c = {
		name: sp.name, spriteid: sp.spriteid, baseSpriteid: dex.species.get(sp.baseSpecies).spriteid, types: sp.types,
		item: dex.items.get(set.item).name || '', ability: dex.abilities.get(set.ability).name,
		nature: set.nature, natureUp: n.plus || null, natureDown: n.minus || null,
		moves: set.moves.map(m => { const mv = dex.moves.get(m); return { id: mv.id, name: mv.name, type: mv.type, category: mv.category, basePower: mv.basePower }; }),
		mega: mega ? { name: mega.name, spriteid: mega.spriteid, baseSpe: mega.baseStats.spe } : null,
	};
	if (mine) c.sp = set.evs;
	return c;
}

// ---------------------------------------------------------------- velocidad
function speedQuestion(ours, theirs, dex) {
	for (let tries = 0; tries < 60; tries++) {
		const m = pick(ours), t = pick(theirs);
		const mMega = megaForm(m, dex), tMega = megaForm(t, dex);
		const useMMega = !!mMega && Math.random() < 0.6, useTMega = !!tMega && Math.random() < 0.6;
		const mSp = (useMMega ? mMega : dex.species.get(m.species));
		const tSp = (useTMega ? tMega : dex.species.get(t.species));
		const mScarf = toID(m.item) === 'choicescarf', tScarf = toID(t.item) === 'choicescarf';
		// situación de campo
		const r = Math.random();
		const ctx = { tr: false, twMine: false, twTheirs: false, stageMine: 0, stageTheirs: 0, label: '' };
		if (r < 0.12) { ctx.tr = true; ctx.label = 'Con Espacio Raro activo'; }
		else if (r < 0.22) { ctx.twMine = true; ctx.label = 'Con Viento Afín en tu lado'; }
		else if (r < 0.32) { ctx.twTheirs = true; ctx.label = 'Con Viento Afín en su lado'; }
		else if (r < 0.40) { ctx.stageMine = -1; ctx.label = 'Tu Pokémon tiene −1 en Velocidad (p. ej. tras Icy Wind)'; }
		else if (r < 0.46) { ctx.stageTheirs = -1; ctx.label = 'Su Pokémon tiene −1 en Velocidad (p. ej. tras Icy Wind)'; }
		const mineRaw = statOf(mSp.baseStats.spe, (m.evs && m.evs.spe) || 0, m.nature, 'spe', dex);
		const mine = effSpeed(mineRaw, { scarf: mScarf, stage: ctx.stageMine, tailwind: ctx.twMine });
		const theirAt = sp => effSpeed(statOf(tSp.baseStats.spe, sp, t.nature, 'spe', dex), { scarf: tScarf, stage: ctx.stageTheirs, tailwind: ctx.twTheirs });
		const lo = theirAt(0), hi = theirAt(32);
		const real = theirAt((t.evs && t.evs.spe) || 0);
		// interesante: tu velocidad está cerca del rango suyo
		if (mine < lo * 0.82 || mine > hi * 1.18) continue;
		const faster = (a, b) => ctx.tr ? a < b : a > b;          // ¿se mueve antes a que b?
		let answer;
		if (faster(mine, lo) && faster(mine, hi)) answer = 'yo';
		else if (faster(lo, mine) && faster(hi, mine)) answer = 'el';
		else answer = 'depende';
		// umbral: desde cuántos SP te supera (o deja de superarte con Espacio Raro)
		let thr = null;
		for (let sp = 0; sp <= 32; sp++) if (!faster(mine, theirAt(sp))) { thr = sp; break; }
		const mName = useMMega ? mMega.name : m.species, tName = useTMega ? tMega.name : t.species;
		const expl = [];
		expl.push(`Tu **${mName}**: **${mine}** de Velocidad efectiva` + (mScarf ? ' (con Choice Scarf)' : '') + `.`);
		expl.push(`Su **${tName}** (${t.nature}${tScarf ? ', Choice Scarf' : ''}): de **${lo}** (0 SP) a **${hi}** (32 SP).`);
		if (answer === 'depende') {
			if (ctx.tr) expl.push(`Con Espacio Raro se mueve antes el más lento: **depende de sus SP**. Con ${thr} SP o más en Velocidad ya no es más lento que tú.`);
			else if (theirAt(thr) === mine) expl.push(`**Depende de sus SP**: con ${thr} SP empatáis (50 % cada uno) y con ${thr + 1} o más te supera.`);
			else expl.push(`**Depende de sus SP**: te supera si invierte **${thr} SP o más** en Velocidad.`);
		} else if (answer === 'yo') expl.push(ctx.tr ? 'Con Espacio Raro eres más lento que cualquier versión suya: **te mueves tú antes**.' : 'Eres más rápido que cualquier versión suya: **te mueves tú antes**.');
		else expl.push(ctx.tr ? 'Con Espacio Raro es más lento que tú haga lo que haga: **se mueve él antes**.' : 'Es más rápido que tú haga lo que haga con sus SP: **se mueve él antes**.');
		expl.push(`En este equipo concreto lleva ${(t.evs && t.evs.spe) || 0} SP en Velocidad → **${real}**: ` +
			(real === mine ? 'empate.' : (faster(mine, real) ? 'te mueves tú antes.' : 'se mueve él antes.')));
		return {
			type: 'speed', key: `spe|${toID(mName)}|${toID(tName)}|${toID(t.nature)}|${ctx.label}`,
			mine: card(m, dex, true), theirs: card(t, dex, false), mineName: mName, theirName: tName, context: ctx.label,
			question: `${ctx.label ? ctx.label + ', ¿quién' : '¿Quién'} se mueve antes: tu ${mName} o su ${tName}?`,
			options: [{ id: 'yo', label: `Tú (${mName}), siempre` }, { id: 'el', label: `Él (${tName}), siempre` },
				{ id: 'depende', label: 'Depende de sus SP' }],
			answer, explanation: expl,
		};
	}
	return null;
}

// ---------------------------------------------------------------- daño
function pctRange(res, fullHp) {
	const rolls = res.rolls.full.normal;
	if (!rolls || !rolls[0]) return null;
	const sum = idx => rolls.reduce((a, perHit) => a + (perHit ? perHit[idx] : 0), 0);
	return [100 * sum(0) / fullHp, 100 * sum(15) / fullHp];
}

function damageQuestion(ours, theirs, dex, format) {
	for (let tries = 0; tries < 40; tries++) {
		const theyAttack = Math.random() < 0.6;
		const A = theyAttack ? pick(theirs) : pick(ours);
		const D = theyAttack ? pick(ours) : pick(theirs);
		const moves = A.moves.map(id => dex.moves.get(id)).filter(mv => mv.category !== 'Status' && mv.basePower > 0 &&
			!(Array.isArray(mv.multihit)) && mv.id !== 'fakeout');
		if (!moves.length) continue;
		const aMega = !!megaForm(A, dex), dMega = !!megaForm(D, dex);
		const base = (atk, def) => ({ attacker: atk, defender: def, atkMega: aMega, defMega: dMega, weather: 'none', terrain: 'none', format });
		// el ataque más fuerte contra ese defensor (con la inversión real)
		let best = null;
		for (const mv of moves) {
			let r;
			try { r = damage.calc(Object.assign(base(A, D), { move: mv.id })); } catch (e) { continue; }
			const p = pctRange(r, r.defender.maxhp);
			if (p && (!best || p[1] > best.p[1])) best = { mv, p };
		}
		if (!best) continue;
		const mv = best.mv;
		const stat = mv.category === 'Physical' ? 'atk' : 'spa';
		const dstat = mv.overrideDefensiveStat || (mv.category === 'Physical' ? 'def' : 'spd');
		// rango según lo que NO sabes: su inversión (en ataque si ataca él; en PS/defensa si atacas tú)
		const variant = v => {
			if (theyAttack) {
				const a = JSON.parse(JSON.stringify(A)); a.evs = { hp: 0, atk: 0, def: 0, spa: 0, spd: 0, spe: 0 }; a.evs[stat] = v;
				return damage.calc(Object.assign(base(a, D), { move: mv.id }));
			}
			const d = JSON.parse(JSON.stringify(D)); d.evs = { hp: 0, atk: 0, def: 0, spa: 0, spd: 0, spe: 0 }; d.evs.hp = v; d.evs[dstat] = v;
			return damage.calc(Object.assign(base(A, d), { move: mv.id }));
		};
		const at = v => { const r = variant(v); return pctRange(r, r.defender.maxhp); };
		const pMin = theyAttack ? at(0) : at(32);       // caso más favorable para el defensor
		const pMax = theyAttack ? at(32) : at(0);
		if (!pMin || !pMax) continue;
		const lo = pMin[0], hi = pMax[1];
		if (hi < 60 || lo > 175) continue;           // demasiado obvio
		let answer;
		if (lo >= 100) answer = 'ko';
		else if (hi >= 100) answer = 'puede';
		else answer = 'no';
		const aName = (aMega ? megaForm(A, dex).name : A.species), dName = (dMega ? megaForm(D, dex).name : D.species);
		const realR = damage.calc(Object.assign(base(A, D), { move: mv.id }));
		const realP = pctRange(realR, realR.defender.maxhp);
		const expl = [];
		const spread = ['allAdjacentFoes', 'allAdjacent'].includes(mv.target) ? ' (ataque a varios: ×0,75)' : '';
		if (theyAttack) {
			expl.push(`**${mv.name}** de su ${aName} (${A.nature}, ${dex.items.get(A.item).name || 'sin objeto'}) a tu ${dName}${spread}: ` +
				`**${lo.toFixed(0)}–${pMin[1].toFixed(0)} %** sin invertir en ${STATN[stat]} y **${pMax[0].toFixed(0)}–${hi.toFixed(0)} %** con 32 SP.`);
			if (answer === 'puede') {
				let first = null, sure = null;
				for (let v = 0; v <= 32; v += 2) { const p = at(v); if (first === null && p[1] >= 100) first = v; if (sure === null && p[0] >= 100) sure = v; }
				expl.push(`Puede dejarte KO: con **${first} SP o más** en ${STATN[stat]} ya lo consigue con una tirada alta` +
					(sure !== null ? `, y con ${sure}+ SP lo hace siempre.` : '; nunca es seguro sin crítico.'));
			}
		} else {
			expl.push(`Tu **${mv.name}** (${aName}) a su ${dName} (${D.nature})${spread}: **${pMax[0].toFixed(0)}–${hi.toFixed(0)} %** si no invierte en defensa ` +
				`y **${lo.toFixed(0)}–${pMin[1].toFixed(0)} %** si lleva 32 SP en PS y en ${STATN[dstat]}.`);
			if (answer === 'puede') expl.push('Puede dejarlo KO según la tirada y lo que haya invertido en aguantar.');
		}
		if (answer === 'ko') expl.push('Es **KO seguro** aunque haya invertido todo en lo que más le conviene.');
		if (answer === 'no') expl.push(hi >= 50 ? 'No llega al KO de un golpe, ni siquiera en el peor caso para el defensor.' : 'Ni siquiera quita la mitad en el peor caso para el defensor.');
		const realSp = theyAttack ? (A.evs && A.evs[stat]) || 0 : `${(D.evs && D.evs.hp) || 0} PS / ${(D.evs && D.evs[dstat]) || 0} ${STATN[dstat]}`;
		if (realP) expl.push(`Con el set de este equipo (${theyAttack ? realSp + ' SP en ' + STATN[stat] : realSp}): **${realP[0].toFixed(0)}–${realP[1].toFixed(0)} %**.`);
		expl.push('Sin críticos, sin clima ni campos y sin Intimidación.' + (aMega || dMega ? ' Megas ya evolucionadas.' : ''));
		const who = theyAttack ? `su ${aName}` : `tu ${aName}`, whom = theyAttack ? `tu ${dName}` : `su ${dName}`;
		return {
			type: 'damage', key: `dmg|${theyAttack ? 'in' : 'out'}|${toID(aName)}|${mv.id}|${toID(dName)}`,
			mine: card(theyAttack ? D : A, dex, true), theirs: card(theyAttack ? A : D, dex, false),
			mineName: theyAttack ? dName : aName, theirName: theyAttack ? aName : dName,
			context: theyAttack ? `Ataca su ${aName}` : `Atacas tú`, move: { name: mv.name, type: mv.type, category: mv.category },
			question: `¿El ${mv.name} de ${who} deja KO a ${whom} de un golpe?`,
			options: [{ id: 'ko', label: 'Sí, KO seguro' }, { id: 'puede', label: 'Puede (según tirada o su inversión)' },
				{ id: 'no', label: 'No hace KO' }],
			answer, explanation: expl,
		};
	}
	return null;
}

// ---------------------------------------------------------------- orden de acción
const PROTECTS = new Set(['protect', 'detect', 'spikyshield', 'kingsshield', 'banefulbunker', 'silktrap', 'burningbulwark', 'obstruct', 'wideguard', 'quickguard', 'endure']);
function orderQuestion(ours, theirs, dex, format) {
	const { buildBattle } = require('./inject');
	const { leadState } = require('./coach');
	const { choiceFromSpec } = require('./search');
	const shuffle = a => { const b = a.slice(); for (let i = b.length - 1; i > 0; i--) { const j = rnd(i + 1); [b[i], b[j]] = [b[j], b[i]]; } return b; };
	const order4 = n => { const idx = shuffle([...Array(n).keys()]); return idx.slice(0, Math.min(4, n)); };
	for (let tries = 0; tries < 40; tries++) {
		const oo = order4(ours.length), to = order4(theirs.length);
		const r = Math.random();
		const ctx = { label: '', tr: false, tw: null, grassy: false, par: null };
		if (r < 0.22) { ctx.tr = true; ctx.label = 'Con Espacio Raro activo'; }
		else if (r < 0.36) { ctx.tw = 0; ctx.label = 'Con Viento Afín en tu lado'; }
		else if (r < 0.50) { ctx.tw = 1; ctx.label = 'Con Viento Afín en su lado'; }
		else if (r < 0.60) { ctx.grassy = true; ctx.label = 'Con Campo de Hierba'; }
		else if (r < 0.70) { ctx.par = [rnd(2), rnd(2)]; }
		// movimientos de cada uno (nada de Protect: siempre irían primero)
		const pickMove = set => {
			const mvs = set.moves.map(m => dex.moves.get(m)).filter(mv => mv.exists && !PROTECTS.has(mv.id) && mv.id !== 'fakeout');
			if (!mvs.length) return null;
			const pri = mvs.filter(mv => mv.priority !== 0 || (mv.category === 'Status' && toID(set.ability) === 'prankster') || mv.id === 'grassyglide');
			return (pri.length && Math.random() < 0.45 ? pick(pri) : pick(mvs)).id;
		};
		const mvO = oo.slice(0, 2).map(i => pickMove(ours[i])), mvT = to.slice(0, 2).map(i => pickMove(theirs[i]));
		if (mvO.includes(null) || mvT.includes(null)) continue;
		const run = (speSP, seed) => {
			const th = theirs.map(s => Object.assign({}, s, { evs: Object.assign({}, s.evs, { spe: speSP }) }));
			const st = leadState({ format, ours, theirs: th, oursOrder: oo, theirsOrder: to });
			if (ctx.tr) st.field.trickroom = 3;
			if (ctx.grassy) { st.field.terrain = 'grassyterrain'; st.field.terrainTurns = 3; }
			if (ctx.tw !== null) st.sides[ctx.tw].conditions = Object.assign({}, st.sides[ctx.tw].conditions, { tailwind: 3 });
			if (ctx.par) { const side = st.sides[ctx.par[0]]; const mon = side.pokemon.find(p => p.active === ctx.par[1]); if (mon) mon.status = 'par'; }
			const built = buildBattle(st), b = built.b;
			b.prng = new (require('./sim').PS.PRNG)(seed);
			const spec = (mvs, s) => mvs.map(m => ({ move: m, target: 1 + rnd(2) }));
			const c0 = choiceFromSpec(b, 0, spec(mvO)), c1 = choiceFromSpec(b, 1, spec(mvT));
			if (!c0 || !c1) return null;
			// datos para explicar: prioridad (con habilidades/campo) y velocidad de cada uno
			const info = {};
			for (let s = 0; s < 2; s++) b.sides[s].active.forEach((pk, k) => {
				if (!pk) return;
				const mv = b.dex.getActiveMove(s === 0 ? mvO[k] : mvT[k]);
				let pr = b.singleEvent('ModifyPriority', mv, null, pk, null, null, mv.priority);
				pr = b.runEvent('ModifyPriority', pk, null, mv, pr);
				info[`p${s + 1}${'ab'[k]}`] = { name: pk.species.name, move: mv.name, priority: pr, spe: pk.getStat('spe', false, false) };
			});
			b.log = [];
			if (!b.choose('p1', c0.choice) || !b.choose('p2', c1.choice)) return null;
			const order = [];
			for (const line of b.log) {
				const p = line.split('|');
				if ((p[1] === 'move' || p[1] === 'cant') && p[2]) { const id = p[2].slice(0, 3); if (!order.includes(id)) order.push(id); }
			}
			return { order, info };
		};
		const a1 = run(0, '1,2,3,4'), a2 = run(0, '9,8,7,6'), b1 = run(32, '1,2,3,4'), b2 = run(32, '9,8,7,6');
		if (!a1 || !a2 || !b1 || !b2 || a1.order.length < 4 || b1.order.length < 4) continue;
		const firstA = a1.order[0], firstB = b1.order[0];
		const tie = a1.order[0] !== a2.order[0] || b1.order[0] !== b2.order[0];
		const info = b1.info;
		// descartar lo trivial: el primero sólo por tener más prioridad base sin nada especial
		const f = info[firstB];
		const others = Object.keys(info).filter(k => k !== firstB);
		const baseP = id => dex.moves.get(toID(info[id].move)).priority;
		const special = f.priority !== baseP(firstB);
		if (!tie && firstA === firstB && others.every(k => info[k].priority < f.priority) && !special && Math.random() < 0.85) continue;
		let answer;
		if (tie) answer = 'depende';
		else if (firstA !== firstB) answer = 'depende';
		else answer = firstA;
		const nm = id => `${id.startsWith('p1') ? 'tu' : 'su'} ${info[id].name}`;
		const expl = [];
		const ordStr = o => o.map((id, n) => `${n + 1}) ${nm(id)}`).join(' · ');
		if (answer === 'depende') {
			if (tie && firstA === firstB) expl.push(`**Empate de velocidad**: el orden lo decide la suerte (50 %).`);
			else expl.push(`**Depende de sus SP en Velocidad**: sin invertir actúa primero ${nm(firstA)}; con 32 SP, ${nm(firstB)}.`);
		} else expl.push(`Actúa primero **${nm(answer)}**.`);
		expl.push(`Orden completo (con 32 SP del rival en Velocidad): ${ordStr(b1.order)}.` + (a1.order.join() !== b1.order.join() ? ` Sin invertir: ${ordStr(a1.order)}.` : ''));
		const lines = Object.keys(info).sort((x, y) => b1.order.indexOf(x) - b1.order.indexOf(y)).map(id => {
			const i = info[id], base = baseP(id);
			const pr = i.priority === base ? (i.priority ? `prioridad ${i.priority > 0 ? '+' : ''}${i.priority}` : 'prioridad 0')
				: `prioridad ${i.priority > 0 ? '+' : ''}${i.priority} (base ${base > 0 ? '+' : ''}${base}, sube por habilidad o campo)`;
			const spe = id.startsWith('p2') ? `Vel ${a1.info[id].spe}–${i.spe} según sus SP` : `Vel ${i.spe}`;
			return `${nm(id)} — ${i.move}: ${pr}, ${spe}`;
		});
		expl.push('Primero manda la prioridad; a igual prioridad, la Velocidad' + (ctx.tr ? ' al revés (Espacio Raro: el más lento primero, salvo con prioridad)' : '') +
			(ctx.tw !== null ? ' (Viento Afín la duplica)' : '') + (ctx.par ? ' (la parálisis la reduce a la mitad)' : '') + ': ' + lines.join(' · ') + '.');
		const opts = Object.keys(info).map(id => ({ id, label: `${id.startsWith('p1') ? 'Tu' : 'Su'} ${info[id].name} (${info[id].move})` }));
		opts.push({ id: 'depende', label: 'Depende de sus SP (o empate)' });
		const parTxt = ctx.par ? `${ctx.par[0] === 0 ? 'Tu' : 'Su'} ${info[`p${ctx.par[0] + 1}${'ab'[ctx.par[1]]}`].name} está paralizado` : '';
		const context = [ctx.label, parTxt].filter(Boolean).join(' · ');
		return {
			type: 'orden', key: `ord|${Object.values(info).map(i => toID(i.name) + ':' + toID(i.move)).join('|')}|${context}`,
			pairs: {
				mine: oo.slice(0, 2).map((i, k) => Object.assign(card(ours[i], dex, true), { action: info[`p1${'ab'[k]}`].move })),
				theirs: to.slice(0, 2).map((i, k) => Object.assign(card(theirs[i], dex, false), { action: info[`p2${'ab'[k]}`].move })),
			},
			mine: card(ours[oo[0]], dex, true), theirs: card(theirs[to[0]], dex, false), mineName: '', theirName: '',
			context, question: `${context ? context + '. ' : ''}¿Quién actúa primero este turno?`,
			options: opts, answer, explanation: expl,
		};
	}
	return null;
}

function question(req) {
	const dex = dexFor(req.format);
	const ours = req.ours.map(s => normSet(s, dex));
	const theirs = req.theirs.map(s => normSet(s, dex));
	const kind = ['speed', 'damage', 'orden'].includes(req.kind) ? req.kind : pick(['speed', 'damage', 'orden']);
	const gen = { speed: () => speedQuestion(ours, theirs, dex), damage: () => damageQuestion(ours, theirs, dex, req.format),
		orden: () => orderQuestion(ours, theirs, dex, req.format) };
	return gen[kind]() || gen.speed() || gen.damage();
}

/** Tabla de velocidades de un cruce: lo tuyo exacto; lo suyo como rango según su naturaleza (0-32 SP). */
function speedTiers(req) {
	const dex = dexFor(req.format);
	const rows = [];
	const add = (set, mine) => {
		const forms = [dex.species.get(set.species)];
		const mega = megaForm(set, dex);
		if (mega) forms.push(mega);
		const scarf = toID(set.item) === 'choicescarf';
		for (const sp of forms) {
			const at = v => effSpeed(statOf(sp.baseStats.spe, v, set.nature, 'spe', dex), { scarf });
			const exact = mine ? at((set.evs && set.evs.spe) || 0) : null;
			rows.push({ side: mine ? 'ours' : 'theirs', name: sp.name, nature: set.nature, scarf, mega: !!sp.isMega,
				min: mine ? exact : at(0), max: mine ? exact : at(32), exact });
		}
	};
	req.ours.map(s => normSet(s, dex)).forEach(s => add(s, true));
	req.theirs.map(s => normSet(s, dex)).forEach(s => add(s, false));
	rows.sort((a, b) => b.max - a.max || b.min - a.min);
	return { rows };
}

module.exports = { question, statOf, effSpeed, speedTiers };
