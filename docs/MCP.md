# Servidor MCP

Transporte: **stdio únicamente** (sin red, sin LAN). Un proyecto por proceso (`--project`). Todas las
rutas se resuelven dentro de esa raíz; `../` y symlinks hacia fuera se rechazan. Los subprocesos reciben
argv (nunca shell) y un entorno mínimo sin credenciales heredadas. No existe ninguna herramienta que ejecute
código: el texto recibido se escribe en JSON y se dibuja como glifos.

```json
{"mcpServers": {"explica": {"command": "/ruta/explica/.venv/bin/explica-mcp",
                             "args": ["--project", "/ruta/explica/examples/pilot-atencion"]}}}
```

Cada llamada queda registrada en `<proyecto>/mcp-server.log` (JSONL: herramienta, argumentos, resumen del
resultado, ms). Es la evidencia del lado servidor.

## Contrato

Toda respuesta es un objeto JSON con, como mínimo, `revision`; las mutaciones añaden `ok`, `op`,
`affected_files`, `warnings`, `diff`, `checkpoint`, `dry_run`. Errores: `{"ok": false, "error": "..."}`;
conflicto de revisión: `{"ok": false, "error": "revision conflict…", "current_revision", "expected_revision"}`.

| Herramienta | Qué devuelve | Notas |
|---|---|---|
| `project_guide` | guía de una página | léela primero |
| `project_inspect(with_capabilities)` | revisión, fps, tamaño, assets, escenas, títulos, pistas, lock, sync, historial, límites | `with_capabilities=true` mide ffmpeg/melt/kdenlive/pycairo/piper |
| `timeline_query(start, end)` | clips por pista en `[start,end)` con `source_info` (ruta + hash de la escena, asset, título), captions, títulos | |
| `scene_inspect(scene_id, frame?)` | JSON de la escena + `bounds` por objeto en ese frame | |
| `media_frame(ref, frame, quality, return_image)` | PNG (ruta) + imagen inline | `ref`: `timeline` (MLT), `scene:<id>` (cairo), `asset:<id>` (ffmpeg) |
| `media_inspect(asset_id)` | ffprobe + entrada del manifiesto | |
| `audio_segment(asset_id, start, end)` | WAV mono PCM16 (ruta) | el servidor **no** puede hacer que el cliente lo oiga |
| `transcript_query(asset_id?, start, end, text?)` | segmentos almacenados con procedencia | nunca genera timestamps |
| `scene_patch(scene_id, ops_json, expected_revision?, dry_run)` | diff, warnings, hash | ops: `set`, `add_object`, `remove_object`, `add_animation`, `remove_animation`, `set_duration` |
| `timeline_patch(ops_json, expected_revision?, dry_run)` | diff, warnings | ops: `move_clip`, `trim_clip`, `split_clip`, `set_gain`, `add_clip`, `remove_clip`, `set_enabled`, `add_caption`, `set_caption`, `remove_caption`, `shift` |
| `title_set(title_id, fields_json, …)` | diff | título nativo (editable en Kdenlive) |
| `project_checkpoint(label)` / `project_restore(checkpoint_id?)` / `project_history()` | snapshots; sin id = **undo** | |
| `render_preview(start, end, quality, wait_seconds)` | job (`id`, `status`, `meta.out_path`) | asíncrono; `quality` `preview` (540×960) o `master` |
| `job_status(job_id, wait_seconds)` / `job_cancel(job_id)` | estado, `progress_percent`, `log_tail` | sondear ≥ 2 s |
| `project_validate()` | errores/warnings (solapes, duraciones, captions vs diagramas, sync) | |
| `project_export(kind, quality, force)` | `kdenlive` / `mlt` / `srt` / `bundle` | export sobre un `.kdenlive` modificado se desvía a `*.agent-rN.kdenlive` |
| `kdenlive_sync(path?, expected_revision?, dry_run, force)` | diff de lo editado en la GUI, `unmodelled` | estado divergente → rechazado sin cambios |
| `capabilities_report()` | capacidades medidas | |

## Capacidades que necesita el cliente y alternativa visible

| Necesidad | Si el cliente la tiene | Si no la tiene |
|---|---|---|
| Ver imágenes | `media_frame` devuelve la imagen inline (probado con Claude Code) | abre el PNG por ruta con otra herramienta o pide revisión humana; el servidor no afirma que el cliente «vio» nada |
| Oír audio | — (ningún cliente MCP probado reproduce audio) | `audio_segment` da la ruta; `transcript_query` da texto con procedencia; reporta «no escuchado» |
| Esperar jobs | `wait_seconds` en `render_preview`/`job_status` | sondeo manual de `job_status` cada ≥ 2 s |
| Concurrencia | `expected_revision` en cada mutación | re-inspecciona tras un conflicto |

## Prueba desde sesión limpia (A18)

Ejecutada con Claude Code como cliente externo (modelo Sonnet, `--strict-mcp-config`, solo herramientas
`mcp__explica__*`), sin contexto previo. Secuencia completada: descubrimiento → `project_guide` →
`project_inspect` (rev 17) → `media_frame` (describió correctamente la imagen) → `scene_inspect` →
`scene_patch` dry-run y real (rev 18) → `timeline_patch` (rev 19) → `render_preview` 90 frames (job done,
2 s) → `project_restore` (undo → rev 20) → `project_inspect`. El cliente declaró explícitamente que no
reprodujo el vídeo. Evidencia: `docs/evidence/A18-*`.

Para repetirla:

```bash
claude -p --mcp-config mcp.example.json --strict-mcp-config --allowedTools "mcp__explica__*" \
  "Lee project_guide, inspecciona, mira el frame 300, cambia objects.title.text de beat1 con dry_run y luego real, renderiza 0-90 y deshaz."
```

También hay una prueba automatizada con el cliente stdio del SDK de Python (`tests/test_mcp.py`).
