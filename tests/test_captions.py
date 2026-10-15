from explica.captions import parse_srt, to_srt, validate_captions
from explica.timebase import Fps


def test_srt_roundtrip():
    fps = Fps(30, 1)
    caps = [{"id": "c1", "start": 0, "end": 45, "text": "Hola"}, {"id": "c2", "start": 45, "end": 90, "text": "Mundo\nsegunda línea"}]
    text = to_srt(caps, fps)
    back = parse_srt(text, fps)
    assert [(c["start"], c["end"], c["text"]) for c in back] == [(0, 45, "Hola"), (45, 90, "Mundo\nsegunda línea")]


def test_validate_captions():
    caps = [{"id": "a", "start": 0, "end": 10, "text": "x"}, {"id": "b", "start": 5, "end": 12, "text": "y"}, {"id": "c", "start": 20, "end": 15, "text": "z"}]
    problems = validate_captions(caps, duration_frames=12)
    assert any("overlaps" in p for p in problems)
    assert any("invalid interval" in p for p in problems)
    assert validate_captions([{"id": "ok", "start": 0, "end": 3, "text": "fine"}]) == []
