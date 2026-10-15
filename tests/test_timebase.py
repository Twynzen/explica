from fractions import Fraction

import pytest

from explica.timebase import Fps, length_from_mlt, mlt_out, parse_mlt_time, srt_timestamp


def test_frames_seconds_exact_roundtrip():
    fps = Fps(30000, 1001)
    for n in (0, 1, 29, 30, 1000, 123456):
        assert fps.seconds_to_frames(fps.frames_to_seconds(n)) == n


def test_no_accumulated_rounding_over_many_cuts():
    # A05: 1000 consecutive 7-frame clips end exactly at 7000 frames when converted via MLT in/out
    pos = 0
    for _ in range(1000):
        out = mlt_out(pos, 7)
        pos = out + 1
    assert pos == 7000
    assert length_from_mlt(0, mlt_out(0, 7)) == 7


def test_mlt_out_inclusive():
    assert mlt_out(0, 30) == 29
    assert mlt_out(10, 1) == 10
    with pytest.raises(ValueError):
        mlt_out(0, 0)


def test_srt_timestamp():
    fps = Fps(30, 1)
    assert srt_timestamp(0, fps) == "00:00:00,000"
    assert srt_timestamp(30, fps) == "00:00:01,000"
    assert srt_timestamp(45, fps) == "00:00:01,500"
    assert srt_timestamp(3600 * 30 + 1, fps) == "01:00:00,033"


def test_parse_mlt_time_formats():
    fps = Fps(25, 1)
    assert parse_mlt_time("125", fps) == 125
    assert parse_mlt_time("00:00:04.960", fps) == 124
    assert parse_mlt_time("00:00:05:00", fps) == 125


def test_samples_conversion():
    fps = Fps(30, 1)
    assert fps.frames_to_samples(30, 48000) == 48000
    assert fps.samples_to_frames(48000, 48000) == 30
    assert fps.frames_to_seconds(15) == Fraction(1, 2)
