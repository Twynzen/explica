"""Canonical time handling.

Rules (see docs/ARCHITECTURE.md):
- Edit time is integer frames at a rational fps (num/den).
- Intervals are half-open [start, end): a clip that starts at frame 0 and has
  length 30 covers frames 0..29.
- MLT uses inclusive in/out: out = start + length - 1. Converting back and
  forth must be exact, never through floating seconds.
- Audio keeps its own sample rate; conversion helpers round explicitly.
"""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction


@dataclass(frozen=True)
class Fps:
    num: int
    den: int = 1

    def __post_init__(self) -> None:
        if self.num <= 0 or self.den <= 0:
            raise ValueError("fps numerator and denominator must be positive")

    @property
    def fraction(self) -> Fraction:
        return Fraction(self.num, self.den)

    @property
    def value(self) -> float:
        return self.num / self.den

    def to_dict(self) -> dict:
        return {"num": self.num, "den": self.den}

    @classmethod
    def from_dict(cls, d: dict) -> "Fps":
        return cls(int(d["num"]), int(d.get("den", 1)))

    # --- conversions -------------------------------------------------
    def frames_to_seconds(self, frames: int) -> Fraction:
        return Fraction(frames) / self.fraction

    def seconds_to_frames(self, seconds: float | Fraction, mode: str = "round") -> int:
        """Convert seconds to a frame count. `mode` is 'round', 'floor' or 'ceil'."""
        x = Fraction(seconds) * self.fraction
        if mode == "floor":
            return int(x // 1)
        if mode == "ceil":
            return -int((-x) // 1)
        return int(round(x))

    def frames_to_samples(self, frames: int, sample_rate: int) -> int:
        return int(round(Fraction(frames) * sample_rate / self.fraction))

    def samples_to_frames(self, samples: int, sample_rate: int, mode: str = "round") -> int:
        return self.seconds_to_frames(Fraction(samples, sample_rate), mode)

    def frames_to_ms(self, frames: int) -> int:
        return int(round(self.frames_to_seconds(frames) * 1000))


def mlt_out(start: int, length: int) -> int:
    """Inclusive MLT `out` for a half-open interval of `length` frames."""
    if length <= 0:
        raise ValueError("length must be >= 1 frame")
    return start + length - 1


def length_from_mlt(in_: int, out: int) -> int:
    """Half-open length from inclusive MLT in/out."""
    if out < in_:
        raise ValueError(f"MLT out ({out}) < in ({in_})")
    return out - in_ + 1


def srt_timestamp(frames: int, fps: Fps) -> str:
    ms = fps.frames_to_ms(frames)
    h, rem = divmod(ms, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def parse_mlt_time(value: str | int | float, fps: Fps) -> int:
    """Parse an MLT time value: frames ('123'), clock ('00:00:04.960') or timecode ('00:00:04:24')."""
    s = str(value).strip()
    if s.isdigit() or (s.startswith("-") and s[1:].isdigit()):
        return int(s)
    parts = s.split(":")
    if len(parts) == 4:  # timecode HH:MM:SS:FF
        h, m, sec, ff = (int(p) for p in parts)
        return ((h * 60 + m) * 60 + sec) * fps.num // fps.den + ff
    if len(parts) == 3:  # clock HH:MM:SS.mmm
        h, m = int(parts[0]), int(parts[1])
        sec = Fraction(parts[2])
        total = Fraction(h * 3600 + m * 60) + sec
        # MLT converts clock → frames by rounding to the nearest frame.
        return int(round(total * fps.fraction))
    try:
        return int(round(Fraction(s) * fps.fraction))
    except (ValueError, ZeroDivisionError) as exc:
        raise ValueError(f"unparseable MLT time: {value!r}") from exc
