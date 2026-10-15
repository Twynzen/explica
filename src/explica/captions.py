"""Captions: SRT import/export, validation and native-title rendering helpers."""

from __future__ import annotations

import re
from pathlib import Path

from .timebase import Fps, srt_timestamp

_SRT_TIME = re.compile(r"(\d+):(\d+):(\d+)[,.](\d+)")


def to_srt(captions: list[dict], fps: Fps) -> str:
    lines = []
    for i, c in enumerate(sorted(captions, key=lambda c: int(c["start"])), start=1):
        lines.append(str(i))
        lines.append(f"{srt_timestamp(int(c['start']), fps)} --> {srt_timestamp(int(c['end']), fps)}")
        lines.append(str(c["text"]).strip())
        lines.append("")
    return "\n".join(lines)


def parse_srt(text: str, fps: Fps, id_prefix: str = "c") -> list[dict]:
    blocks = re.split(r"\n\s*\n", text.strip().replace("\r\n", "\n"))
    out = []
    for n, block in enumerate(blocks, start=1):
        rows = [r for r in block.split("\n") if r.strip()]
        if len(rows) < 2:
            continue
        times = rows[1] if "-->" in rows[1] else rows[0]
        m = _SRT_TIME.findall(times)
        if len(m) < 2:
            continue
        start_ms = _ms(m[0])
        end_ms = _ms(m[1])
        text_rows = rows[2:] if "-->" in rows[1] else rows[1:]
        out.append({
            "id": f"{id_prefix}{n}",
            "start": fps.seconds_to_frames(start_ms / 1000, "round"),
            "end": fps.seconds_to_frames(end_ms / 1000, "round"),
            "text": "\n".join(text_rows).strip(),
        })
    return out


def _ms(parts: tuple[str, str, str, str]) -> int:
    h, m, s, frac = parts
    frac = (frac + "000")[:3]
    return ((int(h) * 60 + int(m)) * 60 + int(s)) * 1000 + int(frac)


def validate_captions(captions: list[dict], duration_frames: int | None = None) -> list[str]:
    problems = []
    last_end = 0
    for c in sorted(captions, key=lambda c: int(c["start"])):
        s, e = int(c["start"]), int(c["end"])
        if s < 0 or e <= s:
            problems.append(f"{c.get('id')}: invalid interval [{s}, {e})")
        if s < last_end:
            problems.append(f"{c.get('id')}: overlaps previous caption")
        if duration_frames is not None and e > duration_frames:
            problems.append(f"{c.get('id')}: ends after duration {duration_frames}")
        if len(str(c.get("text", ""))) > 120:
            problems.append(f"{c.get('id')}: longer than 120 characters; split it for mobile legibility")
        last_end = max(last_end, e)
    return problems


def write_srt(captions: list[dict], fps: Fps, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(to_srt(captions, fps), encoding="utf-8")
    return path
