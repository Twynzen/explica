#!/usr/bin/env python
"""A/B source-separation check (acceptance A10): can a free, local separator recover the
voice and music stems of an explica project from their mix?

The project already keeps real stems, so they are the ground truth: the script rebuilds the
voice stem (clips placed on the timeline with their gains), the music stem (with gain and an
optional extra offset to simulate a louder bed), mixes them, runs `audio-separator` with one
concrete MDX-Net model, and measures SDR of each estimated stem against the truth. The
original mix is never modified; outputs land in a work directory for human A/B listening.

Run it inside a separate venv (not the project's), e.g.
    uv venv ~/.cache/explica-separator/venv --python 3.13
    uv pip install --python ~/.cache/explica-separator/venv/bin/python \
        --extra-index-url https://download.pytorch.org/whl/cpu --index-strategy unsafe-best-match \
        "audio-separator[cpu]==0.47.0" audioread
    ~/.cache/explica-separator/venv/bin/python scripts/separation_check.py \
        --project examples/pilot-atencion --work /tmp/a10 --model-dir ~/.cache/explica-separator/models
"""

from __future__ import annotations

import argparse
import hashlib
import json
import resource
import shutil
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import soundfile as sf

SR = 44100


def ffmpeg(*args: str) -> None:
    subprocess.run([shutil.which("ffmpeg") or "ffmpeg", "-v", "error", "-y", *args], check=True)


def build_truth(project: Path, work: Path, music_offset_db: float) -> tuple[Path, Path, Path, float]:
    p = json.loads((project / "project.json").read_text())
    fps = p["fps"]["num"] / p["fps"]["den"]
    tracks = {t["id"]: t for t in p["tracks"]}
    voice = [c for c in tracks["A1"]["clips"] if c.get("enabled", True)]
    music = [c for c in tracks["A2"]["clips"] if c.get("enabled", True)]
    end = max(c["start"] + c["out"] - c["in"] + 1 for c in voice + music)
    dur = end / fps

    def place(clips: list[dict], out: Path, extra_db: float = 0.0) -> None:
        inputs: list[str] = []
        fc: list[str] = []
        for i, c in enumerate(clips):
            asset = p["assets"][c["source"]["ref"]]
            inputs += ["-i", str(project / asset["path"])]
            start_ms = int(round(c["start"] / fps * 1000))
            n = (c["out"] - c["in"] + 1) / fps
            gain = float(c.get("gain_db", 0.0)) + extra_db
            fc.append(
                f"[{i}:a]atrim={c['in'] / fps:.6f}:{c['in'] / fps + n:.6f},asetpts=PTS-STARTPTS,volume={gain:.3f}dB,"
                f"aresample={SR},aformat=channel_layouts=stereo,adelay={start_ms}|{start_ms}[c{i}]"
            )
        fc.append(
            "".join(f"[c{i}]" for i in range(len(clips)))
            + f"amix=inputs={len(clips)}:normalize=0:dropout_transition=0,apad,atrim=0:{dur:.6f}[o]"
        )
        ffmpeg(
            *inputs,
            "-filter_complex",
            ";".join(fc),
            "-map",
            "[o]",
            "-ar",
            str(SR),
            "-c:a",
            "pcm_s16le",
            str(out),
        )

    tv, tm, mix = work / "truth-voice.wav", work / "truth-music.wav", work / "mix.wav"
    place(voice, tv)
    place(music, tm, music_offset_db)
    ffmpeg(
        "-i",
        str(tv),
        "-i",
        str(tm),
        "-filter_complex",
        "[0:a][1:a]amix=inputs=2:normalize=0[m]",
        "-map",
        "[m]",
        "-c:a",
        "pcm_s16le",
        str(mix),
    )
    return tv, tm, mix, dur


def separate(mix: Path, out_dir: Path, model: str, model_dir: Path) -> dict:
    exe = Path(sys.executable).with_name("audio-separator")
    t = time.time()
    r = subprocess.run(
        [
            str(exe),
            "--model_filename",
            model,
            "--model_file_dir",
            str(model_dir),
            "--output_dir",
            str(out_dir),
            "--output_format",
            "WAV",
            "--log_level",
            "WARNING",
            str(mix),
        ],
        capture_output=True,
        text=True,
        timeout=1800,
    )
    if r.returncode != 0:
        raise SystemExit(r.stdout + r.stderr)
    ru = resource.getrusage(resource.RUSAGE_CHILDREN)
    stems = {}
    for f in out_dir.glob(f"{mix.stem}_(*)_*.wav"):
        kind = f.name.split("(")[1].split(")")[0].lower()
        stems[kind] = f
    return {
        "elapsed_s": round(time.time() - t, 1),
        "max_rss_mib": round(ru.ru_maxrss / 1024, 1),
        "stems": stems,
    }


def sdr(ref: np.ndarray, est: np.ndarray) -> float:
    return float(10 * np.log10(np.sum(ref**2) / max(np.sum((ref - est) ** 2), 1e-12)))


def scaled_sdr(ref: np.ndarray, est: np.ndarray) -> float:
    a = float(np.sum(ref * est) / max(np.sum(ref**2), 1e-12))
    return sdr(ref * a, est) if a > 0 else float("nan")


def measure(tv: Path, tm: Path, mix: Path, est_v: Path, est_m: Path) -> dict:
    arrs = [sf.read(p, dtype="float64")[0] for p in (tv, tm, mix, est_v, est_m)]
    n = min(len(a) for a in arrs)
    tv_a, tm_a, mix_a, ev, em = (a[:n] for a in arrs)
    gap = np.sqrt(np.mean(tv_a**2, axis=1)) < 1e-4  # samples where the true voice is silent
    bleed = (
        10 * np.log10(np.mean(ev[gap] ** 2) + 1e-12) - 10 * np.log10(np.mean(mix_a**2)) if gap.any() else None
    )
    music_rel = 10 * np.log10(np.sum(tm_a**2) / np.sum(tv_a**2))
    return {
        "music_level_rel_voice_db": round(float(music_rel), 1),
        "baseline_mix_as_voice_sdr_db": round(sdr(tv_a, mix_a), 2),
        "baseline_mix_as_music_sdr_db": round(sdr(tm_a, mix_a), 2),
        "voice_sdr_db": round(sdr(tv_a, ev), 2),
        "voice_scaled_sdr_db": round(scaled_sdr(tv_a, ev), 2),
        "music_sdr_db": round(sdr(tm_a, em), 2),
        "music_scaled_sdr_db": round(scaled_sdr(tm_a, em), 2),
        "sum_consistency_db": round(sdr(mix_a, ev + em), 2),
        "voice_gap_fraction": round(float(gap.mean()), 3),
        "vocal_bleed_in_gaps_db_rel_mix": None if bleed is None else round(float(bleed), 1),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--project", type=Path, required=True)
    ap.add_argument("--work", type=Path, required=True)
    ap.add_argument("--model", default="UVR-MDX-NET-Voc_FT.onnx")
    ap.add_argument("--model-dir", type=Path, required=True)
    ap.add_argument(
        "--music-offset-db",
        type=float,
        default=0.0,
        help="extra gain on the music stem to simulate a louder bed",
    )
    ap.add_argument(
        "--out", type=Path, help="write the JSON report here (default: <work>/separation-check.json)"
    )
    a = ap.parse_args()
    a.work.mkdir(parents=True, exist_ok=True)
    tv, tm, mix, dur = build_truth(a.project, a.work, a.music_offset_db)
    sep = separate(mix, a.work / "separated", a.model, a.model_dir)
    model_path = a.model_dir / a.model
    report = {
        "project": a.project.name,
        "seconds": round(dur, 2),
        "music_offset_db": a.music_offset_db,
        "separator": {
            "package": "audio-separator",
            "model": a.model,
            "model_sha256": hashlib.sha256(model_path.read_bytes()).hexdigest(),
            "model_bytes": model_path.stat().st_size,
            "device": "cpu",
        },
        "run": {k: v for k, v in sep.items() if k != "stems"},
        "metrics": measure(tv, tm, mix, sep["stems"]["vocals"], sep["stems"]["instrumental"]),
        "ab_files": {
            "mix": str(mix),
            "truth_voice": str(tv),
            "truth_music": str(tm),
            "est_voice": str(sep["stems"]["vocals"]),
            "est_music": str(sep["stems"]["instrumental"]),
        },
    }
    out = a.out or a.work / "separation-check.json"
    out.write_text(json.dumps(report, indent=1, ensure_ascii=False) + "\n")
    print(json.dumps(report["metrics"], indent=1))
    print(f"report: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
