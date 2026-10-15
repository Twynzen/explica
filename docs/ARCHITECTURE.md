# Arquitectura

```
fuentes científicas → guion → escenas declarativas (JSON, IDs)   ← scene_patch (agente) / editor de texto (humano)
                                   │ pycairo → FFmpeg (cache por hash)
                                   ▼
manifiesto project.json: assets · pistas V1–V4/A1–A3 · clips · captions · títulos · revisión
                                   │ MLT XML (melt)                    │ .kdenlive nativo
                                   ▼                                   ▼
          preview 540×960 / master 1080×1920 / frames PNG        Kdenlive (humano) → kdenlive_sync
                                   │
                                   ▼
             bundle: master normalizado + fuentes + stems + captions + licencias + QA (hashes)
```

Un único núcleo (`explica.project`, `explica.ops`) es usado por los dos adaptadores: `explica.cli` y
`explica.mcp_server`. Ninguno reimplementa lógica de edición.

## Fuentes de verdad

| Contenido | Fuente de verdad | Derivado |
|---|---|---|
| Geometría, texto, números, fórmulas, animación interna | `scenes/<id>.json` | `renders/scenes/<id>-<hash>-<WxH>.mp4` |
| Cortes, posición, niveles de audio, títulos nativos, captions | `project.json` ⇄ `.kdenlive` (tras `kdenlive_sync`) | MLT XML de render |
| Audio (voz, música, efectos) | archivos en `assets/` indexados con sha256 y licencia | mezcla del render |

Una fórmula rasterizada en un clip no se vuelve vector en Kdenlive: el agente cambia la fuente, el clip se
regenera (la cache está indexada por hash de la escena + tamaño + fps) y el derivado se reemplaza.

## Tiempo

- Frames enteros; fps racional `{num, den}`; intervalos `[start, end)`.
- MLT usa `in/out` inclusivos: `out = start + length − 1` (`timebase.mlt_out`), y al leer `length = out − in + 1`.
- Audio conserva su sample rate; `frames_to_samples` redondea explícitamente. SRT se escribe en ms a partir
  de frames (`srt_timestamp`).
- Test A05: 1000 cortes consecutivos de 7 frames terminan exactamente en 7000.

## Escena (`explica/scene@1`)

```json
{"schema":"explica/scene@1","id":"beat1","duration_frames":204,"background":"#000000","width":1080,"height":1920,
 "font":"Liberation Sans","decimal_separator":",","camera":{"x":540,"y":960,"zoom":1},
 "objects":[{"id":"title","type":"text","text":"Atención: un paso","x":540,"y":180,"size":68,"weight":"bold"},
            {"id":"tok1","type":"token","text":"El","x":240,"y":620,"size":60},
            {"id":"arc1","type":"arc","from":{"ref":"tok3"},"to":{"ref":"tok1"},"lift":170,"weight":0.29}],
 "animations":[{"id":"a_title","target":"title","property":"opacity","from":0,"to":1,"start":0,"end":12,"easing":"ease_out"}]}
```

Tipos: `text`, `formula` (markup `_{}` `^{}` `√{}` `\b{}`), `token`, `vector`, `matrix`, `circle`, `rect`,
`line`, `arrow`, `arc` (entre objetos por `ref`), `bar`, `number`. Propiedades comunes: `x, y, opacity,
color, visible, rotation, scale`. Animaciones: cualquier propiedad numérica/lista/color, con `from`
opcional (si se da, vale antes de `start`), easing `linear|ease_in|ease_out|ease_in_out|smooth|step`.
`camera` es un target especial (`x, y, zoom`).

`scene_inspect(id, frame)` devuelve el bounding box de cada objeto en coordenadas lógicas: es la
evidencia que usa el validador para comprobar que los captions no tapan diagramas (A08/A22). `validate`
también avisa de cualquier texto por debajo del tamaño mínimo móvil (`min_text_px`, por defecto 3,5 % del ancho).

Render: `background: null` produce `.mov` QuickTime RLE con alfa recto (`unpremultiply` tras cairo), probado
en composición con `qtblend`; opaco produce H.264 yuv420p. 1080×1920 ≈ 1 s por 60 frames en este i3.

## Manifiesto (`explica/project@1`)

Clip: `{"id","source":{"kind":"scene|asset|title|color","ref"},"start","in","out","gain_db","enabled"}`.
Pistas por defecto: V1 Fondo, V2 Escenas, V3 Títulos, V4 Captions, A1 Voz, A2 Música, A3 Efectos.
Captions: `{"id","start","end","text"}` sin solapes; se exportan a SRT y, en Kdenlive, como clips de título
en V4 (editables en el editor de títulos).

Cada operación mutadora: lock (`.lock` con pid; locks de procesos muertos se limpian), comprobación de
`expected_revision`, checkpoint automático (`checkpoints/NNNN-<op>/`), aplicación, validación, `revision+1`,
escritura atómica. `project.restore(id)` o `undo()` restauran manifiesto + escenas y **suben** la revisión
(nunca retrocede, para que los clientes no confundan estados).

## Render y jobs

`ops.build_timeline_xml` genera un MLT XML plano (un playlist por pista, `qtblend` para vídeo, `mix` para
audio, títulos y captions como productores `kdenlivetitle`). `render_preview` lanza `melt` como subproceso
con estado persistido en `jobs/<id>.json` + log; `job_status` lee progreso de `-progress2`; `job_cancel`
solo mata pids que lanzamos nosotros y cuya línea de comandos coincide.

## Benchmark de motor de escenas (decisión)

Manim Community 0.22 se instaló en un venv (410 MB) y renderizó una escena vertical de 8 s a 540×960 en
2,4 s con 316 MiB RSS, **pero** `MathTex`/`Matrix` requieren LaTeX (`latex` no existe y no se instalan
paquetes de sistema sin aprobación), el encuadre vertical es incómodo y las escenas son código Python, no
patchables por esquema. Se eligió un motor declarativo propio con pycairo (dependencia única, determinista,
IDs por objeto, parche JSON validado). Manim queda como posible adaptador futuro, no como dependencia.
