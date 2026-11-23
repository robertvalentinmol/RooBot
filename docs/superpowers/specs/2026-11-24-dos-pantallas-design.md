# RooBot en las dos pantallas de la AYN Thor — diseño

Fecha: 24-11-2026 · Estado: pendiente de aprobación

## Para qué

En la Thor, RooBot se ve entero en la pantalla de arriba y hay que deslizar hacia abajo para elegir la jugada.
Robert quiere usarlo como un juego de Pokémon de doble pantalla: **arriba lo que se mira (el combate), abajo
lo que se toca (movimientos, objetivos, veredicto, menú)**, sin deslizar para jugar.

## Objetivos

- Un modo de dos pantallas: una ventana **arriba** con el campo de combate a pantalla completa y una ventana
  **abajo** con los controles, el veredicto, la explicación, el registro, las hojas y el menú.
- En la pantalla de abajo, elegir los movimientos y objetivos de los dos Pokémon y confirmar **sin desplazar**,
  en todos los modos que usan el campo: posiciones (mixto, lead, medio, final), decisiones críticas, lecturas,
  repasar errores, mi punto débil, partida contra el bot y la posición elegida de «Analizar partida».
- Team preview: los dos equipos arriba; tus 4 se pueden marcar tocando arriba o abajo.
- Lo que se toca en el campo de arriba funciona igual que si se tocara en la ventana de abajo.

## No objetivos

- Reescribir la lógica de la interfaz ni el motor.
- Colocar las ventanas en cada pantalla desde RooBot: eso lo hace Robert con la Thor (Focus Mode, Thor
  Wayfinder o Mjolnir). RooBot solo indica qué abrir en cada una.
- Rediseñar las páginas sin combate (Inicio, Mi progreso, plan del día, fichas de plan, analizar partida antes de
  elegir posición, velocidades y daños): se ven en la ventana de abajo con la disposición estrecha que ya existe.

## Anti-objetivos (sería un fracaso aunque funcione)

- Que el modo de siempre (PC, una sola ventana) cambie en algo.
- Que arriba se vea un estado distinto del de abajo durante más de un instante, o que un toque se pierda o
  se aplique dos veces.
- Un retraso que se note (más de ~150 ms) entre tocar abajo y verlo arriba.
- Parpadeos o animaciones que se repiten en el campo de arriba cada vez que cambia algo pequeño.

## Restricciones (comprobadas)

- Android trata cada pantalla como una ventana distinta: ninguna página se extiende por las dos. Probado en la
  Thor de Robert: la app RooBot instalada desde Chrome arriba y Chrome normal abajo, abiertas y táctiles a la vez.
- Toda la interfaz está en `web/index.html` (~250 KB, ~130 funciones), con el estado en memoria de una sola página.
- El campo es DOM, sin canvas: `#fieldCard` contiene `#field` → `#stage` (640×381, escalado por `fitStage()`),
  `#mons`, `#tp` (team preview), `#overlay`, y debajo `#statusbar` y `#teambar`. Los sprites los sirve el servidor
  local. `#fieldCard` se muestra u oculta según el modo (`style.display`).
- Los controles están en `#controlsCard` (`#controls`), el veredicto en `#resultCard` (`#result`), el registro y
  las hojas en `aside #sidepanel` (pestañas `showPane()`), el menú en `nav#rail`.
- Servidor: `ThreadingHTTPServer` de Python en 127.0.0.1:8765 (`vgclab/app.py`), sin dependencias nuevas.
- Pantallas: arriba 1920×1080 (16:9), abajo 1240×1080 (casi cuadrada). El tamaño en píxeles CSS depende de la
  densidad de Android y no se conoce: el diseño no puede depender de una resolución concreta.

## Enfoques

**A. Dos ventanas sincronizadas — elegido por Robert.** Arriba el combate, abajo los controles. Es lo que pidió;
cuesta más que B y exige abrir dos ventanas.

**B. Una ventana que cabe sin deslizar** (campo a la izquierda, controles a la derecha, todo arriba). Más barato y
sin sincronizar nada, pero no usa la pantalla de abajo. Descartado.

Dentro de A, cómo se reparte el trabajo (decisión de Claude):

- **A1. «Abajo manda, arriba refleja» — elegido.** La ventana de abajo ejecuta la app de siempre (estado, llamadas
  al servidor, lógica). La de arriba solo muestra una copia en vivo del campo y devuelve abajo los toques que se
  hagan en ella. No toca la lógica existente.
- A2. Repartir el estado entre las dos ventanas. Exige reescribir buena parte de `index.html`. Descartado.

Canal entre ventanas (decisión de Claude): **a través del servidor local** (un buzón con eventos SSE), no
`BroadcastChannel`. Funciona aunque cada ventana sea de un navegador o proceso distinto, se puede probar con
pytest y en 127.0.0.1 el retraso es de milisegundos.

## Diseño

### Tres formas de abrir RooBot

| Dirección | Qué es |
| --- | --- |
| `/` | El modo de siempre. Sin ningún cambio visible salvo una opción «Doble pantalla» en el menú «Más». |
| `/abajo` | Ventana de controles: la app completa, con el campo oculto y la disposición para la pantalla de abajo. |
| `/arriba` | Ventana de vista: solo el campo, a pantalla completa. |

`/abajo` y `/arriba` sirven el mismo `index.html`; el papel se lee de la dirección y se marca con una clase en
`<body>` (`pantalla-abajo` / `pantalla-arriba`). La opción «Doble pantalla» lleva la ventana actual a `/arriba` y
explica en una línea qué abrir abajo («En la pantalla de abajo, abre Chrome en 127.0.0.1:8765/abajo»).

*Decisión de Claude:* la elección se recuerda (`localStorage`). La app instalada abre `/`, así que si la última
vez estaba en doble pantalla, `/` pasa sola a `/arriba`; «Usar solo esta pantalla» lo olvida. `/abajo` y `/arriba`
escritos a mano mandan siempre.

*Decisión de Claude:* si hay más de una ventana `abajo` abierta, la última que mandó el campo es la que se ve
arriba, y los toques de arriba van a esa.

### Sincronización (servidor, `vgclab/app.py`)

Un buzón en memoria con dos sentidos:

- `POST /api/pantallas/campo` — la ventana de abajo manda la copia del campo (`{"v": n, "html": …, "visible": bool}`).
  El servidor guarda la última.
- `POST /api/pantallas/toque` — la ventana de arriba manda un toque (`{"ruta": …}`).
- `GET /api/pantallas/eventos?rol=arriba|abajo` — flujo SSE. A `arriba` le llegan las copias del campo (la última
  nada más conectarse); a `abajo`, los toques y un aviso de si hay una ventana arriba conectada.
- Latido: cada ventana se considera conectada mientras su flujo SSE siga abierto. Si se corta, el navegador
  reconecta solo (`EventSource`).

### Ventana de abajo

- Observa `#fieldCard` (un `MutationObserver` sobre él y sus hijos, y su `display`). Como mucho una vez por
  fotograma, si algo cambió, manda su HTML al buzón. Si `#fieldCard` está oculto, manda `visible:false`.
- Recibe los toques: busca el elemento por su ruta dentro de `#fieldCard` y le hace `click()`.
- Disposición para una pantalla casi cuadrada: arriba del todo una barra mínima (modo y «Nuevo»); debajo, la
  jugada (`#controls`: los dos Pokémon con sus movimientos y objetivos y el botón de confirmar) sin desplazar;
  pestañas para **Jugada · Por qué · Registro · Hojas · Menú**. Al confirmar, el veredicto (`#result`) aparece en
  «Jugada» con su botón de seguir; la explicación larga va en «Por qué».
- Las páginas sin combate se ven como hoy en ventana estrecha, con desplazamiento normal.

### Ventana de arriba

- Al cargar, conecta su flujo SSE y muestra la última copia del campo. Cada copia nueva se aplica con un «morph»
  mínimo: solo se sustituyen los nodos que cambiaron, para que no se reinicien las animaciones ni parpadee.
- `fitStage()` escala el campo para llenar la pantalla manteniendo la proporción; la barra de estado y los
  equipos (`#statusbar`, `#teambar`) se colocan donde quepan (en 16:9 sobra algo de ancho a los lados).
- Cualquier toque dentro del campo se captura (no se ejecuta arriba) y se manda como ruta: los índices de cada
  nodo desde `#fieldCard` hasta el elemento tocado, más su `id` o `data-*` si los tiene, para comprobar abajo que
  la ruta apunta al mismo elemento. Si no coincide, el toque se descarta.
- Sin copia del campo (`visible:false` o sin ventana abajo): pantalla de espera con el logo y una línea:
  «Abre RooBot en la pantalla de abajo (Chrome → 127.0.0.1:8765/abajo)» o, si abajo está en una página sin
  combate, el último campo atenuado.
- Botón discreto «Usar solo esta pantalla» → vuelve a `/`.

### Cómo se usa en la Thor

1. Arriba: la app RooBot → Más → Doble pantalla (o directamente `127.0.0.1:8765/arriba`).
2. Abajo: Chrome → `127.0.0.1:8765/abajo`.
3. Opcional: Mjolnir puede abrir las dos a la vez con el botón Home.

## Pruebas

- Servidor (pytest, sin simulador): el buzón guarda la última copia, `arriba` la recibe al conectarse y en orden,
  los toques solo llegan a `abajo`, y una ventana que se desconecta deja de contar como conectada.
- Interfaz: el modo `/` idéntico al actual (comparar el DOM inicial con y sin el cambio); pruebas manuales en un PC
  con dos ventanas de Chrome redimensionadas a la proporción de cada pantalla (16:9 y ~1,15:1) y, al final, en la
  Thor: posiciones, decisiones críticas, team preview (marcar 4 arriba y abajo), partida contra el bot, repasar
  errores y la posición de «Analizar partida».
- Medir el retraso abajo → arriba (marca de tiempo en la copia) y comprobar que no pasa de ~150 ms.

## Lo que decide quien lo construya

- El esquema exacto de la ruta de los toques y del «morph».
- Los tamaños de letra y los cortes CSS para que la jugada quepa sin desplazar en la pantalla de abajo.
- Dónde colocar `#statusbar` y `#teambar` arriba.
- Si los tooltips del campo (`#tip`) se muestran arriba o se omiten.
