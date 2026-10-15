# Kdenlive: exportación nativa y reconciliación

`explica export kdenlive` escribe `export/<id>.kdenlive` con la estructura que produce Kdenlive ≥ 23.04
(documento 1.1): productores `chain` con `kdenlive:id`, `kdenlive:file_hash` (mismo algoritmo que Kdenlive:
md5 del archivo o de su primer y último MB), tractores por pista con dos playlists, tractor de secuencia con
`kdenlive:uuid` y `sequenceproperties`, `main_bin` con `docproperties` (perfil `vertical_hd_30` cuando aplica),
y tractor de proyecto. Los títulos y los captions son productores `kdenlivetitle` con `xmldata`: se abren en
el editor de títulos de Kdenlive. Las guías (`guides`) marcan el inicio de cada escena.

Cada productor y entrada lleva propiedades `explica:*` (`scene_id`, `scene_hash`, `asset_id`, `title_id`,
`caption_id`, `clip_id`, `revision`) para reconocer los clips al volver.

## Qué se ha probado (headless)

- `melt` abre y renderiza el `.kdenlive` exportado (frame 600 del piloto, 1080×1920) — mismo motor que usa Kdenlive.
- Lectura de vuelta (`read_kdenlive`) y `sync_from_kdenlive` con ediciones simuladas tal como las guarda
  Kdenlive: mover clip (cambia `blank`), recortar `in/out`, editar texto de título y de caption, ganancia
  (`volume.level`). Tests: `tests/test_kdenlive.py`.
- Divergencia (A19): si el `.kdenlive` cambió desde la última sincronización, `export` **no** lo sobrescribe:
  escribe `<id>.agent-r<rev>.kdenlive` y avisa. Si el manifiesto cambió su montaje después de exportar,
  `sync` se rechaza con un mensaje claro (nada se toca) hasta restaurar el checkpoint o usar `force`.
- Efectos, keyframes, transiciones entre clips y mezclas creados en la GUI no se modelan en el manifiesto:
  se listan en `unmodelled` al sincronizar, y permanecen en el `.kdenlive` del humano (que no se sobrescribe).

## Prueba con la GUI real de Kdenlive 26.08 (headless, reproducible)

Sin pantalla disponible para el agente, la prueba se hizo con un **compositor privado**: Weston 16 compilado en
el usuario (`~/.cache/explica-weston-build`, backend headless, sin sudo), un Xwayland rootful (`:2`) dentro de
él, un bus D-Bus de sesión privado (`dbus-run-session`) y directorios XDG aislados. Nada toca el escritorio
ni la configuración del usuario. Kdenlive se controla con eventos de entrada reales (XTEST, `scripts/xtest.py`)
y sus propios diálogos; las capturas se toman con `ffmpeg -f x11grab`.

```bash
# compositor (una vez)
uv venv ~/.cache/explica-weston-build/tools && uv pip install --python ~/.cache/explica-weston-build/tools/bin/python meson ninja
curl -LO https://gitlab.freedesktop.org/wayland/weston/-/archive/16.0.0/weston-16.0.0.tar.gz && tar xzf weston-16.0.0.tar.gz
PATH=~/.cache/explica-weston-build/tools/bin:$PATH meson setup build weston-16.0.0 --prefix=$HOME/.cache/explica-weston-build/prefix \
  -Dbackend-drm=false -Dbackend-rdp=false -Dbackend-vnc=false -Dbackend-pipewire=false -Dbackend-wayland=false -Dbackend-x11=false \
  -Dbackend-headless=true -Dbackend-default=headless -Drenderer-vulkan=false -Dxwayland=true -Dsystemd=false -Ddeprecated-remoting=false \
  -Ddeprecated-pipewire=false -Dshell-ivi=false -Dshell-kiosk=false -Dshell-lua=false -Dcolor-management-lcms=false -Dimage-webp=false \
  -Dtools=[] -Ddemo-clients=false -Dsimple-clients=[] -Dtests=false -Ddoc=false -Dperfetto=false
PATH=~/.cache/explica-weston-build/tools/bin:$PATH ninja -C build install
# prueba
scripts/kdenlive_headless_roundtrip.sh examples/pilot-atencion/export/pilot-atencion.kdenlive /tmp/kdgui
```

Resultado (2026-10-15, Kdenlive 26.08.0, evidencia en `docs/evidence/A03-kdenlive-gui/`):

- Kdenlive **abre** el proyecto exportado: bin con la secuencia «Atención: un paso» (00:00:29:11), 24 clips,
  guías beat1–beat5, monitor de proyecto renderizando títulos, escena y caption (`shot-01-loaded.png`).
- Edición humana simulada con ratón/teclado reales: clic en la regla (mueve el cursor), `G` + diálogo →
  guía «Marker» (`shot-02-guide-added.png`); `Ctrl+A` y `Shift+R` (cortar en el cursor) (`shot-03-after-cut.png`).
- **Guardar** con el diálogo de cierre de Kdenlive («Save») y su aviso de copia de seguridad (`shot-06-after-ok.png`);
  Kdenlive reescribió el documento con su propio serializador (`kdenliveversion` 26.08.0, doc 1.1, tiempos en
  formato reloj) y salió limpiamente.
- Releído por explica: **todos los clips de las 7 pistas idénticos** (posición, in/out, ganancia), captions y
  títulos idénticos, guía nueva presente; `sync-from-kdenlive --dry-run` sin diferencias de montaje.
- Render antes/después: frame 600 del archivo guardado por Kdenlive vs. nuestro export, SSIM 1,000 / PSNR ∞.
- Kdenlive conserva las propiedades `explica:*` de los productores (bin) y de `main_bin`, pero descarta las de
  entradas y tractores; por eso el lector identifica pistas por nombre/posición y clips por productor + posición.

Lo que no cubre esta prueba: el corte con `Shift+R` no siempre se aplicó (dependía de qué quedó seleccionado) y
no se verificó la edición de un título en el editor de títulos de la GUI; el formato de ambos está cubierto por
las pruebas de lectura con archivos modificados tal como los guarda Kdenlive. Proxies y la pista de subtítulos
nativa no se usan.

## Flujo recomendado

1. Agente/CLI: `export kdenlive` (toma checkpoint y registra hash + huella del montaje en `sync`).
2. Humano: abre `export/<id>.kdenlive`, corta, mueve, ajusta niveles, edita títulos/captions, guarda.
3. Agente/CLI: `sync-from-kdenlive --dry-run` (ver diff y `unmodelled`) → luego sin `--dry-run`.
4. Cambios de contenido (números, fórmulas, geometría) siempre por `scene patch`; después `export kdenlive`
   regenera los clips de escena y conserva el montaje sincronizado.
