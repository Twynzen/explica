# Recuperación

- **Checkpoints automáticos**: antes de cada operación mutadora se copia `project.json` + `scenes/` a
  `checkpoints/NNNN-<op>/` con `checkpoint.json` (revisión, hora). `project_checkpoint(label)` crea uno manual.
- **Undo**: `explica undo` / `project_restore()` sin id restaura el snapshot previo a la última operación
  mutadora. Antes de restaurar se toma otro snapshot (`before-restore-…`), así el undo también se puede deshacer.
  La revisión siempre sube.
- **Locks**: `.lock` con pid/propietario/hora. Otro proceso escribiendo → `LockedError`. Si el pid ya no
  existe, el lock se considera obsoleto y se limpia.
- **Escrituras atómicas**: manifiesto y escenas se escriben a `.tmp` y se renombran.
- **Jobs**: `jobs/<id>.json` + `.log`. Si el servidor MCP se reinicia, `job_status` infiere el estado por el
  pid (comparando la línea de comandos) y por el archivo de salida, y lo marca con un aviso.
- **Cache de escenas**: `renders/scenes/<id>-<hash>-<WxH>.*`; se puede borrar `renders/` entero sin perder
  nada editable.
- **Hashes**: cada asset y escena lleva sha256; `validate` detecta archivos reemplazados fuera de explica.
- **Bundle**: `export bundle` calcula sha256 de todos los entregables en `QA-REPORT.json`; una corrección
  posterior invalida ese QA (hay que volver a exportar).
