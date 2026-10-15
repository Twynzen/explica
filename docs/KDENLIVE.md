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

## Qué NO se ha probado

- **Abrir y guardar en la GUI de Kdenlive 26.08.** No hay modo headless y el escritorio no estaba disponible
  para esta sesión. Un intento con `QT_QPA_PLATFORM=offscreen` se documenta en `docs/LIMITS.md`. Hasta que
  una persona abra el proyecto, lo guarde y ejecute `sync-from-kdenlive`, el round-trip GUI real es **pendiente**
  (A03). Lo que está verificado es el round-trip por archivo con el mismo formato y el mismo motor.
- Proxies, subtítulos nativos de Kdenlive (usamos clips de título en V4 + SRT aparte).

## Flujo recomendado

1. Agente/CLI: `export kdenlive` (toma checkpoint y registra hash + huella del montaje en `sync`).
2. Humano: abre `export/<id>.kdenlive`, corta, mueve, ajusta niveles, edita títulos/captions, guarda.
3. Agente/CLI: `sync-from-kdenlive --dry-run` (ver diff y `unmodelled`) → luego sin `--dry-run`.
4. Cambios de contenido (números, fórmulas, geometría) siempre por `scene patch`; después `export kdenlive`
   regenera los clips de escena y conserva el montaje sincronizado.
