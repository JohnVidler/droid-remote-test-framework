"""Capture *real* touches and hardware key presses made on the phone itself.

Uses `adb shell getevent -lt`, which reports raw kernel input events. Input that
droidtest injects with `adb shell input` does not go through the kernel, so it
never shows up here: there's no double-recording when the mirror window is used.

Only single-finger gestures are reconstructed; multi-touch gestures are reported
via `on_ignored` and skipped.
"""

from __future__ import annotations

import re
import subprocess
import threading
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable

from .recording import Step, classify

if TYPE_CHECKING:
    from .adb import Device

_LINE_RE = re.compile(
    r"\[\s*(?P<ts>\d+\.\d+)\]\s+(?P<dev>/dev/input/event\d+):\s+(?P<type>\S+)\s+(?P<code>\S+)\s+(?P<val>\S+)"
)
_RELEASED = "ffffffff"

# kernel key name -> Android keyevent name
KEY_MAP = {
    "KEY_VOLUMEUP": "VOLUME_UP",
    "KEY_VOLUMEDOWN": "VOLUME_DOWN",
    "KEY_POWER": "POWER",
    "KEY_BACK": "BACK",
    "KEY_HOMEPAGE": "HOME",
    "KEY_HOME": "HOME",
    "KEY_APPSELECT": "APP_SWITCH",
    "KEY_MENU": "MENU",
}


@dataclass
class TouchDevice:
    path: str
    name: str
    max_x: int
    max_y: int


def find_touch_devices(device: "Device") -> list[TouchDevice]:
    """Input devices that report multi-touch positions, with their raw ranges."""
    out = device.shell("getevent -pl")
    found, path, name, mx = [], None, "", None
    for line in out.splitlines():
        if m := re.match(r"add device \d+:\s+(\S+)", line):
            path, name, mx = m.group(1), "", None
        elif m := re.match(r'\s+name:\s+"(.*)"', line):
            name = m.group(1)
        elif m := re.search(r"ABS_MT_POSITION_([XY])\s*:.*max (\d+)", line):
            if m.group(1) == "X":
                mx = int(m.group(2))
            elif mx is not None and path:
                found.append(TouchDevice(path, name, mx, int(m.group(2))))
    return found


@dataclass
class _Slot:
    active: bool = False
    x: int = 0
    y: int = 0


@dataclass
class _Gesture:
    slot: int
    t_kernel: float
    t_host: float
    x0: int
    y0: int
    x: int = 0
    y: int = 0
    multitouch: bool = False


@dataclass
class EventParser:
    """Turns getevent lines into Steps. Separate from I/O so it can be unit-tested."""

    touch: dict[str, TouchDevice]
    natural_size: tuple[int, int]               # portrait (width, height) in pixels
    on_step: Callable[[Step], None]
    clock: Callable[[], float] = time.monotonic
    on_touch: Callable[[int, int, bool], None] | None = None   # live (x, y, down) feedback
    on_ignored: Callable[[str], None] | None = None
    orientation: int = 0
    _slots: dict[int, _Slot] = field(default_factory=dict)
    _slot: int = 0
    _gesture: _Gesture | None = None
    _key_down: dict[str, float] = field(default_factory=dict)

    def _to_screen(self, td: TouchDevice, rx: int, ry: int) -> tuple[int, int]:
        wn, hn = self.natural_size
        x = min(int(rx * wn / max(td.max_x, 1)), wn - 1)
        y = min(int(ry * hn / max(td.max_y, 1)), hn - 1)
        match self.orientation:   # raw coords are always in the natural orientation
            case 1: return y, wn - 1 - x
            case 2: return wn - 1 - x, hn - 1 - y
            case 3: return hn - 1 - y, x
        return x, y

    def feed(self, line: str) -> None:
        m = _LINE_RE.search(line)
        if not m:
            return
        ts, dev, etype, code, val = (float(m["ts"]), m["dev"], m["type"], m["code"], m["val"])

        if etype == "EV_KEY" and code in KEY_MAP:
            if val == "DOWN":
                self._key_down[code] = ts
            elif val == "UP":
                self._key_down.pop(code, None)
                self.on_step(Step("key", key=KEY_MAP[code], t=self.clock(), source="phone"))
            return

        td = self.touch.get(dev)
        if td is None:
            return
        if etype == "EV_ABS":
            slot = self._slots.setdefault(self._slot, _Slot())
            if code == "ABS_MT_SLOT":
                self._slot = int(val, 16)
            elif code == "ABS_MT_TRACKING_ID":
                slot.active = val != _RELEASED
            elif code == "ABS_MT_POSITION_X":
                slot.x = int(val, 16)
            elif code == "ABS_MT_POSITION_Y":
                slot.y = int(val, 16)
        elif etype == "EV_SYN" and code == "SYN_REPORT":
            self._commit(td, ts)

    def _commit(self, td: TouchDevice, ts: float) -> None:
        active = [i for i, s in self._slots.items() if s.active]
        g = self._gesture
        if g is None:
            if active:
                i = active[0]
                x, y = self._to_screen(td, self._slots[i].x, self._slots[i].y)
                self._gesture = _Gesture(i, ts, self.clock(), x, y, x, y, len(active) > 1)
                if self.on_touch:
                    self.on_touch(x, y, True)
            return
        if len(active) > 1:
            g.multitouch = True
        s = self._slots.get(g.slot)
        if s and s.active:
            g.x, g.y = self._to_screen(td, s.x, s.y)
            if self.on_touch:
                self.on_touch(g.x, g.y, True)
        if not active:
            self._gesture = None
            if self.on_touch:
                self.on_touch(g.x, g.y, False)
            if g.multitouch:
                if self.on_ignored:
                    self.on_ignored("multi-touch gesture skipped")
                return
            ms = int((ts - g.t_kernel) * 1000)
            self.on_step(classify(g.x0, g.y0, g.x, g.y, ms, t=g.t_host, source="phone"))


class PhoneInputListener:
    """Background thread streaming `getevent` from the phone into an EventParser."""

    def __init__(self, device: "Device", on_step: Callable[[Step], None],
                 clock: Callable[[], float] = time.monotonic,
                 on_touch: Callable[[int, int, bool], None] | None = None,
                 on_ignored: Callable[[str], None] | None = None):
        self.d = device
        touch = find_touch_devices(device)
        if not touch:
            raise RuntimeError("No multi-touch input device found via `getevent -pl`.")
        sizes = re.findall(r"(\d+)x(\d+)", device.shell("wm size"))
        w, h = map(int, sizes[-1])
        self.parser = EventParser({t.path: t for t in touch}, (min(w, h), max(w, h)),
                                  on_step, clock, on_touch, on_ignored,
                                  orientation=device.orientation())
        self.touch_devices = touch
        self._proc: subprocess.Popen | None = None
        self._thread: threading.Thread | None = None

    def set_orientation(self, orientation: int) -> None:
        self.parser.orientation = orientation

    def start(self) -> None:
        # -tt forces a pty so getevent's output is line-buffered (otherwise it
        # arrives in 4 KB chunks, seconds late).
        self._proc = subprocess.Popen(
            [self.d.adb, "-s", self.d.serial, "shell", "-tt", "getevent", "-lt"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
            text=True, bufsize=1,
        )
        self._thread = threading.Thread(target=self._pump, daemon=True)
        self._thread.start()

    def _pump(self) -> None:
        assert self._proc and self._proc.stdout
        for line in self._proc.stdout:
            self.parser.feed(line)

    def stop(self) -> None:
        if self._proc:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self._proc.kill()
            self._proc = None

    @property
    def running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None
