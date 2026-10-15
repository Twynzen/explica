# Contribuir

- `uv venv .venv && uv pip install --python .venv/bin/python -e ".[dev]" anyio pytest-anyio`
- `.venv/bin/ruff check src tests` y `.venv/bin/python -m pytest -q` deben pasar antes de un PR.
- Cada capacidad nueva necesita: una prueba, una línea en `docs/ACCEPTANCE.md` o `docs/LIMITS.md`, y nada
  que descargue o instale implícitamente.
- No subas vídeo/audio de terceros ni modelos; documenta la descarga manual con hash y licencia.
- Mantén el contrato: cada operación mutadora pasa por `Project._mutate` (lock → checkpoint → aplicar →
  validar → revisión). CLI y MCP solo adaptan.
