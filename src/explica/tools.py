"""External tool discovery and safe subprocess execution (argv lists, no shell)."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass
class ToolInfo:
    name: str
    path: str | None
    version: str | None

    @property
    def available(self) -> bool:
        return self.path is not None

    def to_dict(self) -> dict:
        return {"name": self.name, "path": self.path, "version": self.version, "available": self.available}


_VERSION_RE = re.compile(r"(\d+\.\d+(?:\.\d+)?)")


def _probe(name: str, args: list[str]) -> ToolInfo:
    path = shutil.which(name)
    if not path:
        return ToolInfo(name, None, None)
    try:
        out = subprocess.run([path, *args], capture_output=True, text=True, timeout=20, env=clean_env())
        text = (out.stdout or "") + (out.stderr or "")
        m = _VERSION_RE.search(text.splitlines()[0] if text.strip() else "")
        return ToolInfo(name, path, m.group(1) if m else None)
    except (OSError, subprocess.SubprocessError):
        return ToolInfo(name, path, None)


def clean_env() -> dict:
    """Minimal environment for subprocesses: no inherited credentials, C numeric locale."""
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": os.environ.get("HOME", "/"),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "LC_NUMERIC": "C",
        "FONTCONFIG_PATH": os.environ.get("FONTCONFIG_PATH", "/etc/fonts"),
        "XDG_RUNTIME_DIR": os.environ.get("XDG_RUNTIME_DIR", ""),
        "QT_QPA_PLATFORM": "offscreen",  # kdenlivetitle producer uses Qt; never pop a window
    }
    for key in ("DISPLAY", "WAYLAND_DISPLAY", "XDG_DATA_DIRS", "MLT_REPOSITORY", "MLT_DATA", "MLT_PROFILES_PATH"):
        if key in os.environ:
            env[key] = os.environ[key]
    return env


def ffmpeg() -> ToolInfo:
    return _probe("ffmpeg", ["-version"])


def ffprobe() -> ToolInfo:
    return _probe("ffprobe", ["-version"])


def melt() -> ToolInfo:
    return _probe("melt", ["-version"])


def kdenlive() -> ToolInfo:
    return _probe("kdenlive", ["--version"])


def capabilities() -> dict:
    """Effective capabilities on this machine (measured, not assumed)."""
    caps = {t.name: t.to_dict() for t in (ffmpeg(), ffprobe(), melt(), kdenlive())}
    melt_info = caps["melt"]
    melt_info["services"] = {}
    if melt_info["available"]:
        melt_info["services"] = melt_services(["producers", "transitions", "filters"])
        melt_info["warnings"] = melt_init_warnings()
    try:
        import cairo  # noqa: F401

        caps["pycairo"] = {"available": True, "version": cairo.version}
    except Exception:  # pragma: no cover
        caps["pycairo"] = {"available": False}
    try:
        import piper  # noqa: F401

        caps["piper"] = {"available": True}
    except Exception:
        caps["piper"] = {"available": False}
    return caps


def melt_init_warnings() -> list[str]:
    path = shutil.which("melt")
    if not path:
        return []
    out = subprocess.run([path, "-query", "consumers"], capture_output=True, text=True, timeout=30, env=clean_env())
    return [ln.strip() for ln in out.stderr.splitlines() if "failed to dlopen" in ln or "cannot open shared" in ln]


def melt_services(kinds: list[str]) -> dict[str, list[str]]:
    path = shutil.which("melt")
    result: dict[str, list[str]] = {}
    if not path:
        return result
    for kind in kinds:
        out = subprocess.run([path, "-query", kind], capture_output=True, text=True, timeout=30, env=clean_env())
        names = []
        for ln in out.stdout.splitlines():
            ln = ln.strip()
            if ln.startswith("- "):
                names.append(ln[2:].strip())
        result[kind] = names
    return result


def run(argv: list[str], cwd: Path | None = None, timeout: float | None = None, check: bool = True) -> subprocess.CompletedProcess:
    """Run an external tool with argv (never a shell string)."""
    if not argv or not isinstance(argv, list):
        raise ValueError("argv must be a non-empty list")
    proc = subprocess.run(
        argv,
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=clean_env(),
    )
    if check and proc.returncode != 0:
        raise ToolError(argv[0], proc.returncode, proc.stderr[-4000:])
    return proc


class ToolError(RuntimeError):
    def __init__(self, tool: str, code: int, stderr: str):
        super().__init__(f"{tool} exited with {code}: {stderr.strip()[-800:]}")
        self.tool = tool
        self.code = code
        self.stderr = stderr
