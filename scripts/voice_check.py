#!/usr/bin/env python
"""Machine intelligibility check of narration clips: transcribe each voice asset locally and compare with its script.

Not a dependency of explica. Needs `faster-whisper` (MIT) in any venv, e.g.
    uv venv /tmp/asr && uv pip install --python /tmp/asr/bin/python faster-whisper
    /tmp/asr/bin/python scripts/voice_check.py examples/pilot-atencion
The first run downloads the Whisper `base` weights (~145 MB) from Hugging Face; say so before running it
in a setting where implicit downloads are not allowed. CPU only. Output: voice-check.json next to the project
and a table on stdout. A similarity below ~0.85 usually means a mispronounced term (letters, symbols).
This is evidence of *machine* intelligibility, not a substitute for a human listening.
"""

from __future__ import annotations

import difflib
import json
import subprocess
import sys
import wave
from pathlib import Path


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    root = Path(sys.argv[1]).resolve()
    manifest = json.loads((root / "project.json").read_text(encoding="utf-8"))
    import numpy as np
    from faster_whisper import WhisperModel

    model = WhisperModel("base", device="cpu", compute_type="int8", cpu_threads=2)
    rows = []
    tmp = root / "renders" / "preview" / "voice-check-16k.wav"
    tmp.parent.mkdir(parents=True, exist_ok=True)
    for aid, a in sorted(manifest["assets"].items()):
        if a.get("kind") != "audio" or not a.get("text"):
            continue
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(root / a["path"]), "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(tmp)], check=True)
        with wave.open(str(tmp), "rb") as fh:
            audio = np.frombuffer(fh.readframes(fh.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0
        segs, _info = model.transcribe(audio, language="es", beam_size=5)
        heard = " ".join(s.text.strip() for s in segs)
        ratio = difflib.SequenceMatcher(None, a["text"].lower(), heard.lower()).ratio()
        rows.append({"asset": aid, "script": a["text"], "tts_text": a.get("tts_text"), "asr": heard, "similarity": round(ratio, 3)})
        print(f"{aid} sim={ratio:.3f}\n  script: {a['text']}\n  asr:    {heard}")
    (root / "voice-check.json").write_text(json.dumps(rows, indent=2, ensure_ascii=False))
    low = [r for r in rows if r["similarity"] < 0.85]
    print(f"\n{len(rows)} clips, {len(low)} below 0.85 similarity")
    return 1 if low else 0


if __name__ == "__main__":
    raise SystemExit(main())
