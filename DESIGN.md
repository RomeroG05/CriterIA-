# Sistema visual de CriterIA

Interfaz institucional y profesional para una herramienta de consulta: lo primero es que se pueda trabajar con ella. La identidad está en tres colores con funciones claras, dos tipografías y un gesto propio: **la evidencia se ve**.

Todo vive en [static/css/app.css](static/css/app.css). No hay framework CSS ni paso de compilación.

## Paleta

| Papel | Token | Claro | Oscuro | Uso |
|---|---|---|---|---|
| Estructura | `--rail` | `#0e2742` | `#08121e` | Barra lateral, columna del acceso, insignias de fuente |
| Lectura | `--bg` / `--surface` | `#f3f5f8` / `#fff` | `#0c1623` / `#122031` | Fondo y tarjetas |
| Texto | `--ink` / `--ink-2` / `--ink-3` | `#142132` / `#4e5b6d` / `#66727f` | `#e7edf5` / `#a3b1c3` / `#8a99ad` | Texto principal, secundario y terciario |
| Evidencia y acción | `--gold` | `#c9a46b` | `#d6b783` | Botón principal, enviar, citas, resaltado del PDF |
| Texto dorado | `--gold-ink` | `#7d5b29` | `#e0c592` | Dorado legible sobre fondos claros (o en oscuro) |
| Mensaje propio | `--user` | `#173e6b` | `#1f4a7d` | Burbuja del usuario |

Colores de estado en los avisos: ambigua en ámbar, fuera de dominio en acero, sin evidencia en pizarra, error en rojo. Solo se usan donde comunican algo.

Reglas:

- El dorado es para **evidencia y acción principal**. No lo uses como decoración.
- El texto dorado sobre fondo claro siempre es `--gold-ink`; `--gold` no tiene contraste suficiente para texto pequeño.
- Nunca escribas un color en un componente: usa los tokens. El modo oscuro los redefine en dos sitios (`prefers-color-scheme` y `[data-theme="dark"]`); si añades un token, añádelo en ambos.

Los recursos generados (infografía, mapa conceptual, Ishikawa, tabla y PDF exportado) usan la misma paleta, en [chat/infographic.py](chat/infographic.py), [chat/visuals.py](chat/visuals.py) y [chat/export.py](chat/export.py).

## Marca

- **Emblema.** Es el óvalo azul con anillo dorado, balanza, libro abierto, onda de voz y red. Da la identidad institucional y aparece en:
  - el favicon;
  - la barra lateral;
  - el acceso;
  - las páginas de lectura;
  - la cabecera de infografías y diagramas;
  - el PDF exportado.

  Archivos:
  - `static/img/criteria-emblema.png`: interfaz, 160 px de alto;
  - `criteria-emblema-hd.png`: lo leen [chat/infographic.py](chat/infographic.py) y [chat/export.py](chat/export.py);
  - `favicon.ico`, `favicon-32.png`, `icon-192.png` y `apple-touch-icon.png`.
- **Mascota.** El búho es la cara del asistente:
  - saluda en la bienvenida y en el acceso (`criteria-mascota.webp`);
  - su rostro en un círculo champán es el avatar de cada respuesta (`criteria-avatar.png`).

  No se usa como decoración en otras pantallas.
- **Nombre.** «Criter» va en el color del texto e «IA» en dorado, en Source Serif 4. El lema «Asistente ético institucional» va en versalitas espaciadas y solo en el bloque completo de marca, en la columna del acceso.
- **Dorado.** El dorado de la interfaz es el champán del logo. Si cambias uno, cambia el otro.
- **Origen.** Los PNG se recortaron de una lámina de 1024 px. Si llega una versión vectorial o de alta resolución, reemplázalos con los mismos nombres.

## Tipografía

- **Source Serif 4**: títulos (bienvenida, encabezados de respuesta, visor, diálogos, guía). Da el tono académico.
- **Public Sans**: toda la interfaz y el texto de las respuestas. Es la tipografía del sistema de diseño del gobierno de EE. UU., pensada para trámites y lectura larga.
- **JetBrains Mono**: solo para código y formatos BibTeX/RIS.

Las fuentes son locales, en `static/fonts/`, con licencia SIL OFL. Las cifras que cambian (páginas, similitud, contadores) usan `font-variant-numeric: tabular-nums`.

## El gesto propio: la evidencia se ve

1. **Cita ↔ fuente.** Al pasar el cursor o enfocar una cita `[n]`, se ilumina su tarjeta en «Fuentes», y al revés. Lo hacen `linkSource()` en [static/js/app.js](static/js/app.js) y la clase `.is-linked`.
2. **Resaltador.** En el visor, cada fragmento del PDF se subraya de izquierda a derecha, uno tras otro, como un marcador (`.v-mark`, retraso según `--i`).

## Movimiento

- Curvas: `--ease-out` para entradas e interacciones, `--ease-drawer` para cajón y hojas.
- Duración: menos de 300 ms en la interfaz. La única excepción es el cambio de tema (380 ms), que es poco frecuente.
- Al pulsar, los botones bajan a escala 0.92–0.98. Nada aparece desde `scale(0)`.
- Solo se anima lo nuevo:
  - un mensaje que llega en la sesión (`.is-new`);
  - la bienvenida al cargar;
  - el visor, los diálogos, el menú visual y los avisos al abrirse (`@starting-style`).

  El historial cargado no se anima.
- El cambio de tema se revela en círculo desde el botón (View Transitions). Sin soporte o con movimiento reducido, cambia al instante.
- `prefers-reduced-motion: reduce` desactiva todas las animaciones.

## Pantallas

| Ancho | Comportamiento |
|---|---|
| ≥ 1180 px | Barra lateral fija, conversación y visor de fuentes en tres columnas |
| ≤ 1179 px | El visor pasa a ser una hoja lateral sobre la conversación |
| ≤ 1023 px | La barra lateral pasa a ser un cajón (`isMobile()` en el JS usa el mismo corte) |
| ≤ 719 px | Celular: el visor y el diálogo de cita se abren como hojas desde abajo, los modos se apilan y el estado y «Exportar» quedan solo con icono. El acceso se apila. |

Detalles para celular:

- `100dvh`;
- márgenes seguros (`env(safe-area-inset-*)`);
- campos de 16 px para que iOS no haga zoom;
- sin destello al tocar;
- `hover` solo con puntero fino;
- `theme-color` según la página y el tema.

## Reglas que no se rompen

- No hay etiquetas pequeñas encima de los títulos.
- No hay bordes laterales de color en avisos o citas.
- No hay texto con degradado.
- No se usa vidrio decorativo.
- No se cambian IDs ni clases que use [static/js/app.js](static/js/app.js): el aspecto se cambia en el CSS.
- Todo control visible tiene `:focus-visible`.
