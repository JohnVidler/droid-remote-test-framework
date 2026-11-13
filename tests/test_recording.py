"""Offline tests (no device needed) for recording + phone touch parsing."""

from droidtest.phone_input import EventParser, TouchDevice
from droidtest.recording import Recording, Step, classify

TS = TouchDevice("/dev/input/event5", "sec_touchscreen", 1080, 2408)


def ev(ts, typ, code, val, dev="/dev/input/event5"):
    return f"[   {ts:.6f}] {dev}: {typ:<12} {code:<20} {val}"


def touch(x, y, t0, t1, x2=None, y2=None, tid="00000001"):
    """getevent lines for one finger down at (x,y), optionally moving, then up."""
    lines = [ev(t0, "EV_ABS", "ABS_MT_TRACKING_ID", tid),
             ev(t0, "EV_ABS", "ABS_MT_POSITION_X", f"{x:08x}"),
             ev(t0, "EV_ABS", "ABS_MT_POSITION_Y", f"{y:08x}"),
             ev(t0, "EV_KEY", "BTN_TOUCH", "DOWN"),
             ev(t0, "EV_SYN", "SYN_REPORT", "00000000")]
    if x2 is not None:
        lines += [ev((t0 + t1) / 2, "EV_ABS", "ABS_MT_POSITION_X", f"{x2:08x}"),
                  ev((t0 + t1) / 2, "EV_ABS", "ABS_MT_POSITION_Y", f"{y2:08x}"),
                  ev((t0 + t1) / 2, "EV_SYN", "SYN_REPORT", "00000000")]
    lines += [ev(t1, "EV_ABS", "ABS_MT_TRACKING_ID", "ffffffff"),
              ev(t1, "EV_KEY", "BTN_TOUCH", "UP"),
              ev(t1, "EV_SYN", "SYN_REPORT", "00000000")]
    return lines


def parse(lines, orientation=0):
    steps = []
    p = EventParser({TS.path: TS}, (1080, 2408), steps.append, clock=lambda: 0.0,
                    orientation=orientation)
    for line in lines:
        p.feed(line + "\r\n")
    return steps


def test_classify():
    assert classify(100, 100, 105, 103, 80).kind == "tap"
    assert classify(100, 100, 105, 103, 900).kind == "long_press"
    s = classify(100, 1000, 100, 300, 250)
    assert (s.kind, s.x2, s.y2, s.duration_ms) == ("swipe", 100, 300, 250)


def test_phone_tap_long_press_swipe():
    steps = parse(touch(540, 1200, 1.0, 1.08)
                  + touch(200, 300, 2.0, 2.9)
                  + touch(540, 2000, 4.0, 4.3, 540, 600))
    assert [s.kind for s in steps] == ["tap", "long_press", "swipe"]
    assert (steps[0].x, steps[0].y, steps[0].source) == (540, 1200, "phone")
    assert 850 <= steps[1].duration_ms <= 950
    assert (steps[2].x2, steps[2].y2) == (540, 600)


def test_phone_keys_and_other_devices():
    steps = parse([ev(1, "EV_KEY", "KEY_VOLUMEDOWN", "DOWN", "/dev/input/event4"),
                   ev(1.1, "EV_KEY", "KEY_VOLUMEDOWN", "UP", "/dev/input/event4"),
                   ev(1.2, "EV_ABS", "ABS_X", "00000010", "/dev/input/event6")])  # proximity: ignored
    assert [(s.kind, s.key) for s in steps] == [("key", "VOLUME_DOWN")]


def test_multitouch_is_skipped():
    lines = [ev(1, "EV_ABS", "ABS_MT_SLOT", "00000000"),
             ev(1, "EV_ABS", "ABS_MT_TRACKING_ID", "00000005"),
             ev(1, "EV_ABS", "ABS_MT_POSITION_X", "00000100"),
             ev(1, "EV_ABS", "ABS_MT_POSITION_Y", "00000100"),
             ev(1, "EV_SYN", "SYN_REPORT", "00000000"),
             ev(1.1, "EV_ABS", "ABS_MT_SLOT", "00000001"),
             ev(1.1, "EV_ABS", "ABS_MT_TRACKING_ID", "00000006"),
             ev(1.1, "EV_ABS", "ABS_MT_POSITION_X", "00000300"),
             ev(1.1, "EV_ABS", "ABS_MT_POSITION_Y", "00000300"),
             ev(1.1, "EV_SYN", "SYN_REPORT", "00000000"),
             ev(1.5, "EV_ABS", "ABS_MT_TRACKING_ID", "ffffffff"),
             ev(1.5, "EV_ABS", "ABS_MT_SLOT", "00000000"),
             ev(1.5, "EV_ABS", "ABS_MT_TRACKING_ID", "ffffffff"),
             ev(1.5, "EV_SYN", "SYN_REPORT", "00000000")]
    assert parse(lines) == []


def test_landscape_rotation():
    # finger near the natural top-left corner; in landscape (rot 1) that's bottom-left
    (s,) = parse(touch(10, 20, 1.0, 1.05), orientation=1)
    assert (s.x, s.y) == (20, 1080 - 1 - 10)


def test_recording_roundtrip_and_export(tmp_path):
    rec = Recording(1080, 2408, [
        Step("tap", 540, 1204, t=0.0),
        Step("swipe", 540, 2000, 540, 600, duration_ms=300, t=1.0),
        Step("key", key="BACK", t=30.0),
        Step("text", text="hi there", t=31.0),
    ], model="SM-A236B")
    gaps = rec.gaps()
    assert gaps[0] == 0 and abs(gaps[1] - 1.0) < 1e-9 and gaps[2] == 10.0  # capped
    path = rec.save(tmp_path / "r.json")
    assert Recording.load(path) == rec
    py = rec.to_python()
    assert "d.tap(0.5000, 0.5000)" in py and "d.key('BACK')" in py
    compile(py, "rec.py", "exec")


class FakeDevice:
    def __init__(self, size):
        self.size, self.calls = size, []
    def screen_size(self): return self.size
    def __getattr__(self, name):
        return lambda *a: self.calls.append((name, *a))


def test_replay_rescales():
    rec = Recording(1080, 2400, [Step("tap", 540, 1200), Step("swipe", 0, 2400, 1080, 0, duration_ms=200)])
    d = FakeDevice((720, 1600))
    rec.replay(d, fixed_gap=0)
    assert d.calls == [("tap", 360, 800), ("swipe", 0, 1600, 720, 0, 200)]
