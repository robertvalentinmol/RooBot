# RooBot — manual completo (español)

> Resumen del proyecto: [README](../README.md) · English: [README_EN](README_EN.md).

Laboratorio para entrenar **Pokémon VGC en Pokémon Champions (Reg M-C)** en tu propio PC:

| Pieza | Qué hace | Comando |
|---|---|---|
| **La app** | Posiciones, decisiones críticas, lecturas, team preview, partidas contra el bot, análisis de tus repeticiones, fichas de plan y tu progreso. Se abre en el navegador, sólo en tu PC | `app.bat` |
| **Calculadora Koh** | Probabilidad de sobrevivir a combinaciones de ataques (256+ resultados), recursión de desgaste y optimizador de Stat Points | `python -m vgclab.calc ejemplos\koh_incineroar.yaml` |
| **Team preview** | Qué 4 traer y con qué liderar contra un equipo (tabla de daño cruzado) | `python -m vgclab.preview mi.txt rival.txt` |

Todo usa el **simulador real de Showdown** (mod `champions`): daño, Stat Points, Megas (incluidas las Mega Z), cambios de movimientos y habilidades de Champions son exactos. Las 16 tiradas de daño se han contrastado con `@smogon/calc`.

---

## 1. Contexto (lo que se investigó)

**mikumiku37 (Shao, octubre 2026).** Bot que llegó al #1 del ladder `[Gen 9 Champions] VGC 2026 Reg M-C` de Showdown (1857 Elo, 81,2 % GXE). Según su autor: transformer pequeño (~8,7 M parámetros) entrenado desde cero por self-play con PPO contra una liga de versiones pasadas, sin repeticiones humanas y con recompensa sólo al final; ~1.260 equipos públicos de torneo de Reg M-B/M-C; simulador propio en Rust ~650x más rápido que Showdown; 48,3 h en una RTX 5090 (~330 M partidas). En juego añade una búsqueda tipo AlphaZero de **un turno**: en 16 "mundos" muestreados construye la tabla de pagos de sus 8 mejores acciones conjuntas contra las 8 del rival y la resuelve como estrategia mixta (regret matching). No ha liberado ni el motor ni los pesos. Hubo además otro bot (Nessie123) que encabezó el ladder Bo3 con OTS.

**Lo que replica este proyecto:** la *arquitectura* (búsqueda de 1 turno sobre el simulador real con mundos, semillas, top-K x top-K y regret matching; red transformer con acción de la ranura 2 condicionada a la ranura 1; PPO + liga con muestreo PFSP; recompensa terminal). **Lo que no puede replicar en tu equipo:** la escala. Tu simulador es Showdown (no un motor Rust a medida), así que entrenarás muchas menos partidas. Por eso la app usa una función de valor heurística calibrada con repeticiones (sin red entrenada).

**Marcus Koh (Calculus, Singapur).** Estudiante de Matemáticas en Cambridge, finalista del Regional de Birmingham 2026 y clasificado para el Mundial 2026. Lo que cuentan las noticias es que **programó su propio código** (más análisis numérico que "IA" generativa): simula combates y calcula miles de resultados posibles, la probabilidad de sobrevivir a combinaciones de ataques (un doble ataque son 16x16 = 256 resultados de daño, y crece exponencialmente), usa recursión para escenarios de desgaste largos y con eso ajusta las estadísticas de sus Pokémon. `vgclab.calc` + `vgclab.optimizer` replican exactamente esa idea.

**Pokémon Champions y sus regulaciones.** Desde mayo de 2026 Champions es la plataforma oficial de VGC. Reglas base: dobles, nivel 50 automático, cláusula de especie y de objeto, se llevan 6 y se eligen 4. Stat Points en vez de EVs/IVs: **66 SP en total, máximo 32 por estadística**, IVs fijos a 31 (1 SP ≈ 8 EVs). Una Mega por combate. Regulaciones: **M-A** (8 abr – 17 jun 2026), **M-B** (17 jun – 9 sep, la del Mundial 2026), **M-C** (9 sep – 2 dic 2026, añade Mega Absol Z, Mega Lucario Z y Mega Garchomp Z, Rillaboom y Baxcalibur, entre otros).

**Formatos en Showdown:** `gen9championsvgc2026regmc` (Bo1, OTS opcional — el que usaba mikumiku37), `gen9championsvgc2026regmcbo3` (Bo3, OTS forzado), y los equivalentes de Reg M-B. Cuando salga la siguiente regulación, cambia el formato con la variable `VGCLAB_FORMAT` (y actualiza Showdown).

**Bots y Showdown.** La comunidad está debatiendo en serio el impacto de estos bots. Smogon exige registrar los bots que se conectan al servidor público, y en el código actual de Showdown un bot registrado sólo se empareja en ladder con jugadores que lo han permitido expresamente (opción desactivada por defecto). Este proyecto funciona sólo en tu PC: no se conecta a ningún servidor de Showdown y nunca entra en ladder. Úsalo para entrenar, no para jugar por ti en torneos.

---

## 2. Instalación (Windows)

Necesitas: **Node.js 20+** (nodejs.org), **Git** (git-scm.com) y **Python 3.10+** (python.org, marca "Add python to PATH").

```bat
instalar.bat
```

El instalador descarga el simulador de Showdown (fija la versión probada), lo compila (lo usa el motor de la app para simular) e instala numpy y pyyaml. No hace falta tener ningún servidor abierto: sólo `app.bat`.

En Linux/macOS: `./instalar.sh`.

### Mantenimiento

Todo el mantenimiento está en **`mantenimiento.bat`** (doble clic y eliges una opción). Cierra la app antes: usa todo el procesador.

| Opción | Qué hace | Cuándo |
|---|---|---|
| 1 · Rutina mensual | Importa lo que hayas pegado en `equipos_nuevos.txt` y sus sprites, descarga repeticiones nuevas, actualiza el juez y lo mide frente al de siempre | 1 vez al mes (~1 h, internet) |
| 2 · Importar equipos | Equipos de `equipos_nuevos.txt` (enlaces de pokepast.es o export de Showdown) + sprites | Tras cada torneo |
| 3 · Actualizar el juez | Repeticiones nuevas de 1500+ y nuevo aprendizaje (sólo se activa si mejora) | Si no haces la rutina |
| 4 · Comprobar el juez | Vuelve a aprender con lo guardado y mide los dos jueces con el banco fijo | Para comparar |
| 5 · Medir la app | Calibración con el banco fijo con hojas abiertas | Cuando quieras |
| 6 · Experimentos | Prueba ajustes del criterio (sección 6c) | Cada 2-3 meses, de noche |
| 7 · Experiencia | Vuelve a aprender «lo que suelen jugar los fuertes» (sección 6d) | Cada 2-3 meses |
| 8 · Acceso directo | Icono de RooBot en el escritorio | Una vez |
| 9 · Banco limpio | La primera vez aparta 100 partidas con hojas abiertas de 1500+ que nunca se usan para aprender; después mide siempre esas mismas. De ahí salen los casos de «Revisar la app» (sección 6b) | Una vez, y después de cada cambio del juez (~40 min) |

Las opciones avanzadas siguen disponibles desde una consola: `python -m vgclab.calibrar --help` (y lo mismo con `importar`, `experimentos`, `experiencia` y `sprites`).

---

## 3. Ejercicios de posición (línea de comandos)

```bat
python -m vgclab.ejercicios --equipo teams\mi_equipo.txt --rivales teams\mc
python -m vgclab.ejercicios --equipo teams\mi_equipo.txt --tipo lead --n 15
python -m vgclab.ejercicios --equipo teams\mi_equipo.txt --tipo final
```

Como los problemas de táctica del ajedrez. Genera posiciones reales jugando partidas internas con tu equipo contra equipos al azar de `--rivales`:
- **lead**: turno 1 tras el team preview.
- **medio**: un turno intermedio.
- **final**: quedan como mucho 2 Pokémon por bando.

Te enseña el campo, los dos lados (del rival lo que verías con hoja abierta) y una lista de jugadas. Mientras piensas, el motor resuelve la posición. Después te da:
- el veredicto y una puntuación;
- el valor de cada opción y su peso en el equilibrio;
- lo que esperaba el rival;
- una simulación del turno con tu jugada contra su respuesta más probable.

Tu historial queda en `logs\ejercicios.csv`.

---

### Versión con interfaz gráfica (estilo Showdown)

```bat
app.bat
```

Se abre en tu navegador (http://127.0.0.1:8765, sólo accesible desde tu PC). Arriba eliges tu equipo, el rival (o aleatorio) y el tipo de ejercicio. Ves el combate con los sprites de Showdown y eliges la jugada como en Showdown: movimiento, objetivo, Mega o cambio para cada Pokémon. Al confirmar te da el veredicto, el valor de cada opción, lo que esperaba el rival y reproduce el turno en el registro con las barras de PS. Pasa el ratón por un Pokémon para ver su hoja. Opciones: `app.bat --workers 3` (más rápido al analizar), `--puerto 8800`, `--rivales teams\mc`. Los sprites se guardan en tu PC (`web\sprites`): la app los busca ahí y, si falta alguno y hay internet, lo baja de play.pokemonshowdown.com y lo guarda, así que la carpeta se llena sola. **Para usarla sin internet** (p. ej. en un torneo), ejecuta antes una vez `mantenimiento.bat` (opción 2) con conexión: descarga los de todos tus equipos (estáticos, animados y de espaldas), los tipos y las categorías (unos 20-40 MB). Vuelve a ejecutarlo cuando importes equipos nuevos: sólo baja lo que falta. Si alguno no existe (Megas nuevas de Champions) se usa el de la forma base.

**Inicio**: al abrir la app ves todas las opciones en tarjetas (con tu rating, tus errores por repasar y tu punto débil) y la sesión guiada «Entrenar hoy». Lo que se usa menos (Lecturas, Velocidades y daños, Fichas de plan y Partida vs bot) está en «Más», plegado al final del menú.

**Posiciones** (menú «Entrenar»): arriba eliges la fase: **Mixta** (cualquiera), **Lead** (turno 1), **Medio** (turnos 2-5) o **Final** (2 o menos por bando, calculado hasta el final). La app recuerda la última.

Tras cada decisión, el apartado **¿Por qué?** explica la jugada: cuál era la mejor, qué cambia en cada Pokémon (Mega, objetivo, Protect, cambio), qué pasa en el turno con tu jugada y con la mejor (KO, PS que quedan, ataques bloqueados o que no afectan) y qué respuesta del rival castiga más tu elección. Bajo el campo ves el banquillo de los dos equipos (en los leads, la hoja de 6 del rival).

**Team preview (elige leads)**: en el menú elige *Team preview*. Ves los 6 de cada lado y eliges tus 2 leads pulsando sobre ellos. Los leads del rival no son al azar: se estiman las parejas que más se usan, por sinergia (Fake Out + Viento Afín, clima + quien lo aprovecha, Espacio Raro + Pokémon lentos, redirección…) y por lo bien que encajan contra tu equipo. Mientras piensas, la app simula el primer turno de cada pareja tuya contra cada lead probable suyo. Al confirmar ves el ranking de tus 15 parejas posibles, los golpes graves (≥ 50 % de PS) que recibe cada una, los leads probables del rival y la explicación.

**Modo** (arriba; se recuerda):
- **Realista**: como en un combate de verdad. Del rival sólo conoces su hoja abierta (con su naturaleza, que en Champions sale como «Stat Alignment»): sus SP se estiman (con varias variantes) y los Pokémon que aún no ha sacado no se sabe si los trajo (se prueban distintas posibilidades). La reproducción del turno y el «¿Por qué?» usan su equipo real.

**📋 Fichas de plan** (en el menú): elige tu equipo y un rival concreto arriba y pulsa «Abrir / generar ficha» (tarda lo mismo que un team preview). La ficha recoge: lead recomendado con su back y su plan del turno 1 (y cómo suele acabar), tu mejor lead contra cada lead probable suyo, sus amenazas (quién te hace más daño y a cuántos de los tuyos), tus respuestas a cada uno de los suyos, la tabla de velocidades como en un torneo (lo tuyo exacto; lo suyo, el rango según su naturaleza) y tus notas, que se guardan solas. Las fichas quedan guardadas en `logs\fichas` (se abren al instante desde «Fichas guardadas»), «↻ Recalcular» las rehace conservando tus notas y «🖨 Imprimir» saca una versión en blanco para papel o PDF.

**Mi punto débil** (menú «Mejorar»): cada decisión que evalúa la app (ejercicios, repasos y turnos de tus partidas analizadas) se clasifica por lo que interviene: cuándo proteger, Fake Out, elegir objetivo (foco), momento de la Mega, cambiar o quedarse, control de velocidad, movimientos de apoyo, velocidad y prioridad, y elegir ataque. Cuenta como acierto si tu jugada era buena o coincidía con la mejor en esa parte. Ahí ves tu porcentaje por categoría (de peor a mejor, con tendencia); pulsa «Entrenar» en cualquiera (o «Entrenar el más débil» arriba) y la app genera posiciones en las que esa categoría importa. Se guarda en `logs\categorias.csv`.

**📼 Analizar partida** (en el menú): pega el enlace de una repetición (replay.pokemonshowdown.com/…), su log o el archivo .html descargado (también se puede arrastrar). La app reconstruye la posición al empezar cada turno y analiza lo que hiciste: precisión media de la partida, cada turno marcado como ✓ buena, ?! imprecisión o ✗ error, y el turno que más te costó. Pulsa un turno para ver la posición, el análisis y el «¿Por qué?», y «🎯 Practicar este turno» para jugarlo como ejercicio. Tu equipo se toma de «Mi equipo» (SP reales); si no coincide, de la hoja abierta de la repetición. Del rival se usa sólo lo que veías (modo realista). Si uno de tus Pokémon cayó antes de moverse, no se sabe qué eligió: se evalúa tu otra acción con la mejor opción posible para él (marcado «parcial»). Los errores pasan a «Repasar mis errores» y cada partida queda en `logs\partidas.csv`.

**Entrenar hoy** (arriba del menú): una sesión guiada en el orden que más rinde: repasos de tus errores pendientes, decisiones críticas, lecturas, team preview completo y cálculo rápido. Eliges corta (~15 min), normal (~30) o larga (~45); una barra muestra el bloque y el avance, «Siguiente ▸» te lleva al siguiente ejercicio y al final tienes el informe (puntos por bloque, cambio de rating, fallos guardados). Las sesiones quedan en «Mi progreso» (`logs\sesiones.csv`).

**Teclado** (para ir rápido; los atajos funcionan aunque no se muestren): `1`–`4` movimiento del Pokémon que toca, `Q`/`W` objetivo izquierdo/derecho, `A` aliado, `M` Mega, `5`–`8` cambio, `Intro` confirmar, `Retroceso` deshacer. En los team preview: `1`–`6` eliges en orden e `Intro` confirma. En el quiz: `1`–`5` y `Intro`.

**Enseñar al juez** (`mantenimiento.bat`, opción 3, con internet; o `python -m vgclab.calibrar --aprender --solo-guardadas` para usar sólo las ya descargadas): descarga repeticiones públicas de Champions (se guardan en `logs\calibracion\replays` y se suman cada vez), toma la posición de cada turno y quién ganó, y ajusta cómo valora la app una posición. El juez nuevo **parte del juez de siempre y sólo lo corrige donde los datos lo justifican**: z = a·(juez de siempre) + correcciones con 26 rasgos (vida, Pokémon vivos, cambios de stats, Viento Afín, Espacio Raro, Mega por usar, quién es más rápido y quién amenaza KO, y además atacante físico quemado, parálisis, sueño, veneno, pantallas útiles según los atacantes rivales, Banda Focus intacta, baya sin usar, Intimidate y Fake Out en el banquillo, redirección, clima/campo a favor, amenazas de KO desde el banquillo y la vida del más sano). Lo hace **por fase** (inicio, medio juego, final), con **signos obligatorios** (estar quemado nunca puede sumar) y eligiendo cuánto se fía de los datos por validación cruzada. Usa las partidas de 1500+ con peso 1 y también las de 1400 (½) y 1300 (¼) (`--rating-min-peso`); las posiciones del final de cada partida pesan más que las del principio. Los rasgos de cada repetición se guardan en `logs\calibracion\rasgos.json`, así que volver a aprender tarda poco. Las repeticiones del banco de prueba nunca se usan para aprender. Se valida con un 20 % de partidas de 1500+ que no usó y **sólo se activa si predice quién gana mejor que el juez de siempre**; si no, el juez aprendido que hubiera sigue activo (el anterior queda en `logs\calibracion\juez_anterior.json`). Queda en `engine\juez.json`; reinicia `app.bat` para usarlo. Para volver al de siempre, borra `engine\juez.json`. La prueba de verdad es `mantenimiento.bat`, opción 4: mide con el banco fijo el acuerdo de los dos jueces.

**Calibración** (`mantenimiento.bat`, opción 5): analiza cada decisión de los dos jugadores en repeticiones de jugadores fuertes y te dice en qué % la app las da por buenas, por tipo de decisión, con las mayores discrepancias enlazadas para revisarlas. No es para imitar a nadie: es el control de calidad del análisis. Por defecto usa un **banco de prueba fijo con hojas abiertas** (`logs\calibracion\banco_hoja.json`: las 30 repeticiones de más rating con las hojas de los dos jugadores, como en torneo), así que dos calibraciones con jueces distintos se pueden comparar de verdad. `python -m vgclab.calibrar --banco-ladder` usa el banco de ladder sin hojas (`banco.json`), `python -m vgclab.calibrar --juez manual` mide con el juez de siempre (la referencia), `--nuevas --replays 30` con repeticiones recientes, `--carpeta ...` con las de una carpeta, `--rehacer-banco` elige otro banco. Cada calibración guarda además el detalle de todas las decisiones (`calibracion_..._detalle.json`). El resumen sale en «Mi progreso» y el informe completo en `logs\calibracion\`. Tarda unos 10-20 min con 30 repeticiones.

**Decisiones críticas** (menú «Entrenar»): sólo posiciones en las que equivocarse cuesta. La app genera varias y se queda con una en la que la jugada que más tienta (la favorita a primera vista) pierde bastante, o en la que casi todo es claramente peor que la mejor; los finales ya decididos se descartan. Cada una lleva su **dificultad** (1000-2000).

**Lecturas (50/50)**: posiciones donde ninguna jugada gana siempre y lo correcto depende de lo que haga el rival. Tras confirmar verás **la matriz de la decisión**: tus jugadas (y con qué frecuencia las juega la estrategia óptima) contra sus respuestas probables, con cómo te va en cada caso. Si ninguna fila es la mejor en todas las columnas, hay que leerle o mezclar en esas proporciones. Esta matriz también sale en cualquier ejercicio cuando la estrategia óptima mezcla.

**Finales calculados hasta el final**: cuando a cada bando le quedan 2 Pokémon o menos, la app no se queda en el turno: juega la partida hasta que alguien gana (en cada turno los dos bandos eligen según el equilibrio de un mini-juego) y te dice **qué % de las veces ganas** con tu jugada y con la mejor, y la línea esperada turno a turno. Es automático en «Final» y en los turnos finales de «Analizar partida».

**Rating táctico y de team preview**: cada ejercicio nuevo (no los repasos) cambia tu rating, tipo Elo: sube más si aciertas uno difícil y baja más si fallas uno fácil. Sale en la barra lateral y en cada resultado (`logs\rating.json`).

**Mi progreso** (menú «Mejorar»): tus ratings, puntos medios por semana (posiciones y team preview), acierto por tipo de decisión en las últimas 4 semanas frente a las 4 anteriores, el quiz, las series contra el bot y las partidas analizadas. Al final, plegado, **Estado de la app**: la calibración, el juez aprendido, la experiencia, los experimentos y tu revisión de la app.

**Tras cada decisión**: arriba, el veredicto, la mejor jugada y las 3 razones principales («Ver las N razones» para el resto). Debajo, plegados, «Tus opciones» (el valor de cada jugada), la matriz si la decisión es un 50/50, lo que suelen jugar los fuertes y lo que esperaba el rival.

**Team preview completo**: eliges tus **4** en orden (los 2 primeros de lead), como en un torneo. Mientras piensas, la app simula el turno 1 de cada lead y **partidas enteras** con cada una de tus 15 selecciones contra lo que suele traer el rival. Al confirmar: el ranking de las 90 opciones (4 de 6 × lead) con turno 1, % de victorias y cobertura; la **matriz «quién gana a quién»** (golpes para el KO de cada lado y velocidad, tus 6 contra sus 6) y las **amenazas que tu selección deja sin respuesta**. Las fichas de plan incluyen ahora la selección de 4 recomendada.

**Orden de acción** (en «Velocidades y daños»): 4 Pokémon en el campo con su movimiento, a veces con Espacio Raro, Viento Afín, Campo de Hierba o parálisis: ¿quién actúa primero? Se resuelve con el simulador (prioridad, habilidades como Prankster, velocidad), con el rango de SP del rival.

**⚔ Partida vs bot (Bo3)** (en el menú): una partida completa (o una serie al mejor de 3) contra el bot, con «Mi equipo» contra el rival elegido arriba (o uno al azar). Eliges formato (Bo3/Bo1) y nivel del bot y pulsas «Empezar serie». En el team preview ves las dos hojas y marcas tus 4 en orden (los 2 primeros salen de lead); el bot elige los suyos sin ver los tuyos. Luego juegas turno a turno como en Showdown, incluidos los cambios tras un KO o un U-turn/Parting Shot. El bot juega con información de torneo: tu hoja abierta, pero no tus SP ni qué trajiste hasta que lo sacas. Niveles: **fácil** (pocas simulaciones, se equivoca a menudo), **normal** (búsqueda de un turno con estrategia mixta: no es predecible) y **difícil** (más jugadas y simulaciones). Al acabar cada partida, **📊 Informe turno a turno**: igual que «Analizar partida» (precisión, ✓/?!/✗ por turno, el turno que más te costó, «¿Por qué?» y «🎯 Practicar este turno»); los errores van a «Repasar mis errores». Entre partidas del Bo3 puedes cambiar tus 4 y tus leads. Las series quedan en `logs\series_bot.csv` y el resumen (ganadas/jugadas por nivel) sale en la tarjeta.

**⚡ Velocidades y daños** (en el menú): preguntas rápidas con tu equipo contra equipos del metajuego (con preferencia por los más recientes). Como en un torneo, del rival ves su hoja abierta (con naturaleza) pero no sus SP, así que las respuestas tienen en cuenta todo su rango: «¿quién se mueve antes?» (tú siempre / él siempre / depende de sus SP, a veces con Viento Afín, Espacio Raro o −1 Vel) y «¿te deja KO?» (KO seguro / puede / no). La explicación da los números exactos, a partir de cuántos SP cambia el resultado y lo que lleva ese equipo concreto. Teclas 1-2-3 para responder y Enter para seguir. Las falladas vuelven en 1, 3 y 7 días (`logs\velocidades_repaso.json`).


**Repasar mis errores**: cada ejercicio que fallas (veredicto «Aceptable» o «Error») se guarda solo en `logs\repaso.json`. En el menú elige *Repasar mis errores* (o pulsa la etiqueta «🔁 N por repasar»): te vuelve a plantear la posición o el team preview sin decirte qué elegiste; al confirmar te recuerda tu elección anterior. Repetición espaciada: si aciertas vuelve a los 3, 7 y 21 días y después se da por dominado; si fallas, vuelve al día siguiente.

---

## 5. Calculadora estilo Marcus Koh

```bat
python -m vgclab.calc ejemplos\koh_incineroar.yaml
python -m vgclab.calc ejemplos\koh_kingambit.yaml
```

Un escenario es un `.yaml` con tu defensor, los sets atacantes y los ataques **en orden de velocidad**:

```yaml
defender: ../teams/floette_balance.txt:Incineroar     # o texto de Showdown pegado
sets:
  chomp: ../teams/koh_mc_inspired.txt:Garchomp
  rilla: ../teams/rain_offense.txt:Rillaboom
scenarios:
  - name: "Terremoto + Mazazo en Campo de Hierba"
    weight: 2
    attacks:
      - {attacker: chomp, move: Earthquake, terrain: grassy}
      - {attacker: rilla, move: Wood Hammer, terrain: grassy}
recursion:                       # desgaste turno a turno (el "stalemate" de Koh)
  - name: "Recibir X cada turno con Restos"
    attacks_per_turn: [{attacker: chomp, move: Dragon Claw}]
    heal_per_turn: 0.0625
    turns: 6
optimize:
  fixed: {atk: 2}                # SP que no quieres tocar; el resto va a PS/Def/DefEsp
```

Opciones por ataque: `weather` (sun/rain/sand/snow/none), `terrain`, `atkMega`, `defMega`, `atkBoosts`, `defBoosts` (`{atk: -1}`), `helpingHand`, `screens` (`[reflect]`), `spread` (true/false), `crit` (false para ignorar críticos).

Qué calcula: tiradas exactas, críticos (1/24), precisión, golpes múltiples (2-5 golpes con 35/35/15/15 %), Baya Zidra entre golpes, Banda Focus, Multiescamas; la probabilidad de sobrevivir combinando todo; y con `optimize`, el reparto de SP que maximiza la supervivencia media ponderada con el límite 66/32. Ejemplo real del incluido: moviendo SP de DefEsp a Defensa, Incineroar pasa del 98,3 % al 100 % contra Terremoto + Mazazo.

Desde Python:

```python
from vgclab.engine_client import Engine
from vgclab.koh import Attack, scenario
with Engine() as e:
    r = scenario(e, open("teams/floette_balance.txt").read().split("\n\n")[1],
                 [Attack(attacker="Garchomp @ Life Orb\nEVs: 32 Atk / 32 Spe\nJolly Nature\n- Earthquake", move="Earthquake")])
    print(r["survive"])
```

---

## 6. Team preview

```bat
python -m vgclab.preview teams\koh_mc_inspired.txt teams\trick_room.txt
```

Muestra el daño estimado cruzado (tus 6 contra sus 6, con Megas activadas) y las 5 mejores combinaciones de leads + banquillo.

---

## 6b. Revisar la app (tú juzgas)

«Revisar la app» (menú Mejorar) te enseña, de una en una, las jugadas de jugadores de 1500+ que la app marcó como **error** en la última medida del **banco limpio** (`mantenimiento.bat`, opción 9), en orden al azar; si aún no lo has creado o ya no quedan casos, salen los del banco con hojas abiertas, empezando por los de mayor pérdida. Ves la posición, lo que jugó, lo que prefiere la app y su explicación, y decides: **el jugador**, **la app**, **las dos valen** o **no lo sé** (con una nota opcional). Se guarda en `logs\revision.csv`. «Mi progreso» muestra cuántas veces tenía razón la app cuando dijo «error», por tipo de decisión, y una estimación de su precisión real. Además de medir la app, es un entrenamiento de primer nivel: posiciones difíciles de jugadores fuertes, analizadas.

**Lo que cambió con tu revisión (v33).** En los primeros 55 desacuerdos, la app sólo tenía razón 3 veces; cuando el jugador hizo una jugada de plan (proteger, cambiar, estado, preparar, control de velocidad) en una ranura donde la app prefería atacar, acertó 1 de 28. Por eso ahora, en ese caso, el veredicto es **«Discutible: jugada de plan que el cálculo de un turno infravalora»** en vez de «Error»: no baja tu rating, no va a «Repasar mis errores» y cuenta como acierto en «Mi punto débil». Si la app prefería otra jugada de plan, o las dos son ataques, el veredicto sigue igual.

**La prueba con tu revisión.** `python -m vgclab.calibrar --prueba-revision` (también al final de las opciones 1 y 4 de `mantenimiento.bat`, y dentro de los experimentos) vuelve a analizar todas las decisiones que juzgaste y cuenta los **errores falsos** (le diste la razón al jugador o a los dos y la app aún dice «Error») y los **aciertos** que mantiene (le diste la razón a la app). Un experimento que suba los errores falsos o pierda aciertos se descarta aunque mejore el acuerdo. El resultado sale en «Mi progreso → Estado de la app».

**Examen, ajuste y octubre (v34).** La regla «Discutible» se diseñó mirando los 55 casos de octubre, así que su cifra de errores falsos es optimista. Para tener una cifra honrada, los casos nuevos salen del banco limpio y se reparten en dos mitades fijas, por pareja de jugadores (una serie al mejor de tres cae entera en la misma):

- **Examen**: sólo se cuenta. Nunca se mira para diseñar o elegir reglas, y los experimentos no la usan para decidir. Es la cifra que vale.
- **Ajuste**: los casos que sí se pueden estudiar para mejorar el criterio.
- **Octubre**: las revisiones anteriores al banco limpio (orientativa).

Lo que revisas cuenta al momento en «Mi progreso → Estado de la app»; la prueba completa (rutina mensual, opción 4 y experimentos) vuelve a analizar todos los casos con el criterio de ese día. El reparto y las cuentas están en `vgclab/revision.py`.

## 6c. Experimentos automáticos

`mantenimiento.bat`, opción 6 (déjalo de noche: tarda varias horas) prueba variantes del criterio de la app —cuánto vale dormir o paralizar al rival, subir stats, la vida que queda, Viento Afín, las pantallas— y **sólo se queda con las que mejoran el acuerdo con jugadores de 1500+ en los dos bancos de prueba** (primero el de hojas abiertas, jugada a jugada frente a la versión actual; luego se confirma con el de ladder, que son partidas distintas). Si varias mejoran, prueba también su combinación. Lo que pasa las dos pruebas se guarda en `engine\parametros.json` (la app lo usa al arrancar; bórralo para volver a lo de antes), el juez aprendido vuelve a aprender con la nueva valoración y se hace una calibración final. El informe queda en `logs\calibracion\experimentos\` y el resumen en «Mi progreso». `python -m vgclab.experimentos --solo dormir,vida` prueba sólo esas variantes.

## 6d. Experiencia (aprendida de miles de decisiones)

`mantenimiento.bat`, opción 7 (una hora u hora y media; sin internet) aprende de las decisiones de jugadores de 1300+ que tienes guardadas (unas 40.000) **qué tipo de jugada suelen elegir los jugadores fuertes en cada situación**: atacar, proteger, cambiar, Fake Out, preparar, poner estados… según el daño que hacen, el peligro que corre cada Pokémon, cómo le va al que entra si cambias, etc. Después analiza 160 partidas apartadas (los dos bancos de prueba y un banco de evaluación de 100 partidas que nunca se usa para aprender) y prueba cuánto peso darle al combinarla con el cálculo. Mide dos cosas: el acuerdo con jugadores de 1500+ en cada banco y **si los errores que detecta la app predicen quién pierde la partida** (esto no depende de que el jugador acierte). Sólo se activa si mejora el acuerdo en todos los bancos sin empeorar la predicción del resultado. En cualquier caso, cada análisis muestra «Lo que suelen jugar los jugadores fuertes aquí». El modelo queda en `engine\experiencia.json`, el informe en `logs\calibracion\experiencia\` y el resumen en «Mi progreso».

## 7. Juez aprendido de repeticiones

(Sustituye al antiguo kit de red neuronal, que se retiró porque se estancó y la app no lo usaba.) El análisis valora cada posición con un «juez»: cuánto vale la vida que queda, los cambios de stats, el control de velocidad, la Mega por usar… `mantenimiento.bat` (opciones 1 y 3) puede ajustar esos pesos con repeticiones reales para que la valoración prediga mejor quién gana; ver el apartado de calibración.

**Sets que no se ven (uso real).** Cuando falta información de un set (repeticiones sin hoja abierta, hojas abiertas sin SP, el bot que juega con lo que vería un humano), la app lo completa con el **set real más parecido** de los equipos de la carpeta `teams` (los tuyos y los importados de torneos): habilidad (Pixilate, Intimidate…), objeto (incluida la piedra Mega), movimientos que faltan, SP y naturaleza. Lo que se ha visto en el combate manda (movimientos usados, objeto revelado, habilidades que salen en el registro). Cuantos más equipos importes, mejor adivina. Tu propio set exacto nunca cuenta como «uso» para adivinarte a ti.

---

## 8. Estructura

```
engine/            Motor Node sobre el simulador de Showdown
  sim.js           carga de Showdown, sets, estimación de SP del rival
  damage.js        16 tiradas exactas, críticos, multigolpe, barrido de SP
  inject.js        reconstruye una posición real a mitad de combate
  search.js        prior heurístico, top-K, simulación de celdas, valor, regret matching
  teampreview.js   4 de 6 + leads
  encode.js        acciones legales por ranura (máscaras)
  coach.js quiz.js replay.js game.js positions.js   ejercicios, quiz, repeticiones, partidas, posiciones
  engine.js        protocolo JSON por líneas con Python
vgclab/            Python
  calc.py koh.py optimizer.py   calculadora Koh
  preview.py                    team preview
  app.py explicar.py            la app de entrenamiento (interfaz web)
  calibrar.py experimentos.py experiencia.py   calibración y mejora del criterio
  sprites.py importar.py rutas.py   sprites sin internet, importar equipos, rutas comunes
teams/             4 equipos de ejemplo válidos en Reg M-C (validados con Showdown)
ejemplos/          escenarios de la calculadora
```

---

## 9. Problemas frecuentes

- **"No encuentro Showdown compilado"**: ejecuta `instalar.bat` (o define `VGCLAB_SHOWDOWN` con la ruta de tu Showdown).
- **La app dice que es una versión antigua**: cierra todas las ventanas negras de `app.bat` y vuelve a abrirlo.
- **Va lento**: `app.bat --workers 3` (o hasta tus núcleos - 1).
- **Nueva regulación (M-D…)**: actualiza Showdown (`git pull` en `server\pokemon-showdown`, `node build`) y define `VGCLAB_FORMAT` con el nuevo id.

## 10. Uso responsable

Usa la app para practicar y analizar. No la uses para que juegue por ti en ladder ni en torneos (online o presenciales): va contra las normas, y la comunidad está precisamente intentando proteger esos espacios.
