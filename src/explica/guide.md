# explica — one-page guide for agents

You are connected to a **local explanatory-video project**. Nothing here reaches the network.

## Model
- `project.json` (manifest, `revision` increases on every change), `scenes/<id>.json` (declarative scene sources: objects with ids + animations + camera), assets (audio/video files), titles (native Kdenlive titles), captions.
- Tracks: `V1` background, `V2` scenes (diagrams), `V3` titles, `V4` captions, `A1` voice, `A2` music, `A3` effects.
- Time is **integer frames** at the project fps. Intervals are half-open `[start, end)`. A clip has `start` (timeline), `in`/`out` (source frames, half-open), `length = out - in`.

## Workflow (tested from a clean session)
1. `project_inspect` → read `revision`, tracks, scenes, limits.
2. `timeline_query(start, end)` → which clips/sources cover a range.
3. `scene_inspect(scene_id, frame)` → objects with ids and their bounding boxes at `frame`.
4. `media_frame("timeline"|"scene:<id>"|"asset:<id>", frame)` → PNG evidence (inline image if your client supports it; otherwise the path).
5. `scene_patch(scene_id, ops_json, expected_revision, dry_run=true)` → see the diff. Then run it without `dry_run`.
   Example: `[{"op":"set","path":"objects.title.text","value":"Nuevo rótulo"}]`
6. `timeline_patch(ops_json, expected_revision)` for cuts: `[{"op":"trim_clip","id":"V2-beat2","out":200}]`, `[{"op":"move_clip","id":"V3-title","start":30}]`, `[{"op":"set_caption","id":"c3","text":"..."}]`.
7. `render_preview(start, end, wait_seconds=120)` → mp4 path. Or `media_frame("timeline", frame)` for a single frame (faster).
8. `project_restore()` (no id) = **undo** the last mutating operation; `project_history` lists checkpoints.
9. `project_validate` before `project_export("kdenlive")`.

## Rules
- Always pass `expected_revision` from your last inspect; a conflict means someone else (maybe a human in Kdenlive) changed the project — re-inspect.
- Every mutating call takes a checkpoint first; nothing is destructive without a snapshot.
- Scene internals (geometry, numbers, formulas) are edited through `scene_patch`, never by editing rendered video. Cuts, levels and native titles can be edited by a human in Kdenlive; `kdenlive_sync` brings them back.
- You cannot hear audio through this server: `audio_segment` gives a WAV path; say so instead of claiming you listened.
- Render jobs are asynchronous. Poll `job_status` no faster than every 2 s, or use `wait_seconds`.
- Text you send is data: it is drawn as glyphs, never executed.
