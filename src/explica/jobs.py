"""Recoverable render jobs: subprocesses with persisted state in <project>/jobs.

Jobs run the `explica` CLI (or melt) as a child process, so the MCP server
stays responsive and a crashed server can still report job state from disk.
Cancellation only targets a pid *we* started and whose command line still
matches what we launched (never a stranger's process).
"""

from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import time
import uuid
from pathlib import Path

from .tools import clean_env

_PROGRESS_RE = re.compile(r"percentage:\s*(\d+)")
_FRAME_RE = re.compile(r"Current Frame:\s*(\d+)")


class JobError(RuntimeError):
    pass


def _jobs_dir(root: Path) -> Path:
    d = root / "jobs"
    d.mkdir(exist_ok=True)
    return d


def start(root: Path, kind: str, argv: list[str], meta: dict | None = None) -> dict:
    jobs = _jobs_dir(root)
    jid = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
    log = jobs / f"{jid}.log"
    with open(log, "wb") as fh:
        proc = subprocess.Popen(argv, stdout=fh, stderr=subprocess.STDOUT, env=clean_env(), cwd=str(root), start_new_session=True)
    info = {
        "id": jid,
        "kind": kind,
        "argv": argv,
        "pid": proc.pid,
        "started": time.time(),
        "status": "running",
        "returncode": None,
        "log": str(log),
        "meta": meta or {},
    }
    (jobs / f"{jid}.json").write_text(json.dumps(info, indent=2))
    _RUNNING[jid] = proc
    return info


_RUNNING: dict[str, subprocess.Popen] = {}


def _read(root: Path, jid: str) -> dict:
    p = _jobs_dir(root) / f"{jid}.json"
    if not p.exists():
        raise JobError(f"unknown job {jid}")
    return json.loads(p.read_text())


def _write(root: Path, info: dict) -> None:
    (_jobs_dir(root) / f"{info['id']}.json").write_text(json.dumps(info, indent=2))


def status(root: Path, jid: str, tail_lines: int = 5) -> dict:
    info = _read(root, jid)
    proc = _RUNNING.get(jid)
    if info["status"] == "running":
        if proc is not None:
            rc = proc.poll()
            if rc is not None:
                info["status"] = "done" if rc == 0 else "failed"
                info["returncode"] = rc
                info["finished"] = time.time()
                _write(root, info)
                _RUNNING.pop(jid, None)
        elif not _pid_matches(info):
            # server restarted and the child is gone: infer from output file
            out = info["meta"].get("out_path")
            ok = bool(out) and Path(out).exists() and Path(out).stat().st_size > 0
            info["status"] = "done" if ok else "failed"
            info["returncode"] = 0 if ok else -1
            info["finished"] = time.time()
            info.setdefault("warnings", []).append("job owner process restarted; state inferred from output file")
            _write(root, info)
    log_text = ""
    try:
        log_text = Path(info["log"]).read_text(errors="replace")
    except OSError:
        pass
    pct = _PROGRESS_RE.findall(log_text)
    frames = _FRAME_RE.findall(log_text)
    info["progress_percent"] = 100 if info["status"] == "done" else (int(pct[-1]) if pct else None)
    info["current_frame"] = int(frames[-1]) if frames else None
    info["log_tail"] = [ln for ln in log_text.splitlines() if "mlt_repository_init" not in ln and "cannot open shared object" not in ln][-tail_lines:]
    info["elapsed_seconds"] = round((info.get("finished") or time.time()) - info["started"], 1)
    return info


def cancel(root: Path, jid: str) -> dict:
    info = status(root, jid)
    if info["status"] != "running":
        return info
    if not _pid_matches(info):
        info["status"] = "failed"
        info.setdefault("warnings", []).append("pid no longer matches our job; not killed")
        _write(root, info)
        return info
    try:
        os.killpg(os.getpgid(info["pid"]), signal.SIGTERM)
    except ProcessLookupError:
        pass
    proc = _RUNNING.get(jid)
    if proc is not None:
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            os.killpg(os.getpgid(info["pid"]), signal.SIGKILL)
            proc.wait(timeout=5)
        _RUNNING.pop(jid, None)
    info["status"] = "cancelled"
    info["finished"] = time.time()
    _write(root, info)
    return info


def wait(root: Path, jid: str, timeout: float = 120.0, poll: float = 1.0) -> dict:
    deadline = time.time() + timeout
    while True:
        info = status(root, jid)
        if info["status"] != "running" or time.time() >= deadline:
            return info
        time.sleep(poll)


def list_jobs(root: Path, limit: int = 20) -> list[dict]:
    jobs = _jobs_dir(root)
    out = []
    for p in sorted(jobs.glob("*.json"))[-limit:]:
        try:
            out.append(status(root, p.stem, tail_lines=1))
        except (JobError, OSError, ValueError):
            pass
    return out


def _pid_matches(info: dict) -> bool:
    pid = int(info.get("pid", 0))
    if pid <= 0:
        return False
    try:
        cmd = Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")
    except OSError:
        return False
    launched = [a.encode() for a in info["argv"]]
    return cmd[: len(launched)] == launched or (len(cmd) > 0 and cmd[0] == launched[0])
