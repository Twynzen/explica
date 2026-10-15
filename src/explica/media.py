"""Media inspection and evidence extraction via ffprobe/ffmpeg (argv only)."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from .timebase import Fps
from .tools import run


def probe(path: Path) -> dict:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        raise RuntimeError("ffprobe not found")
    proc = run([ffprobe, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)])
    data = json.loads(proc.stdout)
    streams = []
    for s in data.get("streams", []):
        streams.append({k: s.get(k) for k in ("index", "codec_type", "codec_name", "width", "height", "pix_fmt", "r_frame_rate", "avg_frame_rate", "nb_frames", "sample_rate", "channels", "duration", "time_base")})
    fmt = data.get("format", {})
    return {
        "path": str(path),
        "format": fmt.get("format_name"),
        "duration_seconds": float(fmt["duration"]) if fmt.get("duration") else None,
        "size_bytes": int(fmt["size"]) if fmt.get("size") else None,
        "streams": streams,
    }


def duration_frames(path: Path, fps: Fps, mode: str = "round") -> int:
    info = probe(path)
    dur = info["duration_seconds"]
    if dur is None:
        raise RuntimeError(f"cannot determine duration of {path}")
    return fps.seconds_to_frames(dur, mode)


def extract_frame(path: Path, frame: int, fps: Fps, out_png: Path, width: int | None = None) -> Path:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg not found")
    seconds = fps.frames_to_seconds(frame)
    out_png.parent.mkdir(parents=True, exist_ok=True)
    vf = f"select=eq(n\\,{frame})" if frame < 300 else None
    argv = [ffmpeg, "-v", "error", "-y"]
    if vf:
        argv += ["-i", str(path), "-vf", vf, "-vsync", "0", "-frames:v", "1"]
    else:
        argv += ["-ss", f"{float(seconds):.6f}", "-i", str(path), "-frames:v", "1"]
    if width:
        argv += ["-vf", (vf + "," if vf else "") + f"scale={width}:-2"] if not vf else ["-vf", f"{vf},scale={width}:-2"]
    argv += [str(out_png)]
    run(argv)
    return out_png


def audio_segment(path: Path, start_frame: int, end_frame: int, fps: Fps, out_wav: Path) -> dict:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg not found")
    if end_frame <= start_frame:
        raise ValueError("end_frame must be > start_frame")
    start = fps.frames_to_seconds(start_frame)
    dur = fps.frames_to_seconds(end_frame - start_frame)
    out_wav.parent.mkdir(parents=True, exist_ok=True)
    run([ffmpeg, "-v", "error", "-y", "-ss", f"{float(start):.6f}", "-t", f"{float(dur):.6f}", "-i", str(path), "-vn", "-ac", "1", "-c:a", "pcm_s16le", str(out_wav)])
    return {"path": str(out_wav), "start_frame": start_frame, "end_frame": end_frame, "seconds": [float(start), float(start + dur)], "mono_pcm16": True}


def loudness(path: Path) -> dict:
    """Integrated loudness (LUFS), loudness range and true peak via ffmpeg ebur128."""
    ffmpeg = shutil.which("ffmpeg")
    proc = run([ffmpeg, "-v", "info", "-nostats", "-i", str(path), "-vn", "-af", "ebur128=peak=true", "-f", "null", "-"], check=False)
    text = proc.stderr
    out = {"integrated_lufs": None, "lra_lu": None, "true_peak_dbtp": None}
    tail = text[text.rfind("Summary:"):] if "Summary:" in text else ""
    for line in tail.splitlines():
        line = line.strip()
        if line.startswith("I:") and "LUFS" in line:
            out["integrated_lufs"] = float(line.split()[1])
        elif line.startswith("LRA:") and "LU" in line:
            out["lra_lu"] = float(line.split()[1])
        elif line.startswith("Peak:") and "dBFS" in line:
            out["true_peak_dbtp"] = float(line.split()[1])
    return out


def detect_black_silence(path: Path) -> dict:
    """Technical detectors (A11): black video intervals and silence intervals, reported, not auto-removed."""
    ffmpeg = shutil.which("ffmpeg")
    proc = run([ffmpeg, "-v", "info", "-nostats", "-i", str(path), "-vf", "blackdetect=d=0.5:pix_th=0.10", "-af", "silencedetect=n=-50dB:d=1.0", "-f", "null", "-"], check=False)
    black, silence = [], []
    for line in proc.stderr.splitlines():
        if "blackdetect" in line and "black_start" in line:
            parts = dict(p.split(":") for p in line.split("]")[-1].split() if ":" in p)
            black.append({k: float(v) for k, v in parts.items()})
        if "silencedetect" in line and "silence_end" in line:
            seg = line.split("]")[-1].strip()
            try:
                end = float(seg.split("silence_end:")[1].split("|")[0])
                dur = float(seg.split("silence_duration:")[1])
                silence.append({"end": end, "duration": dur, "start": end - dur})
            except (IndexError, ValueError):
                pass
    return {"black": black, "silence": silence}
