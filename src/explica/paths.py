"""Path confinement: every file the core touches must live inside the project root.

- Paths are resolved (symlinks followed) and must stay under the root.
- Symlinks that point outside the root are rejected, even if the lexical
  path is inside.
- Names coming from media, transcripts or MCP clients are data, never
  shell input; subprocesses always receive argv lists.
"""

from __future__ import annotations

import os
import re
from pathlib import Path


class PathError(ValueError):
    pass


_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")


def check_id(value: str, what: str = "id") -> str:
    if not isinstance(value, str) or not _ID_RE.match(value) or ".." in value:
        raise PathError(f"invalid {what}: {value!r} (allowed: letters, digits, '_', '-', '.', max 64)")
    return value


def resolve_inside(root: Path, relative: str | os.PathLike, must_exist: bool = False) -> Path:
    """Resolve `relative` against `root` and verify confinement."""
    root_r = Path(root).resolve()
    rel = Path(relative)
    if rel.is_absolute():
        candidate = rel
    else:
        candidate = root_r / rel
    # Lexical check first (rejects ../ escapes before touching the filesystem)
    lexical = Path(os.path.normpath(str(candidate)))
    try:
        lexical.relative_to(root_r)
    except ValueError as exc:
        raise PathError(f"path escapes project root: {relative!r}") from exc
    # Then physical check (rejects symlinks pointing outside)
    resolved = candidate.resolve()
    try:
        resolved.relative_to(root_r)
    except ValueError as exc:
        raise PathError(f"path resolves outside project root (symlink?): {relative!r}") from exc
    if must_exist and not resolved.exists():
        raise PathError(f"path does not exist: {relative!r}")
    return resolved


def relpath(root: Path, path: Path) -> str:
    return str(Path(path).resolve().relative_to(Path(root).resolve())).replace(os.sep, "/")
