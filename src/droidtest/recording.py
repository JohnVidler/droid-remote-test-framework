"""Recorded sequences of input steps: save/load as JSON, replay, export to Python."""

from __future__ import annotations

import json
import math
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .adb import Device

TAP_SLOP_PX = 24          # movement below this counts as "didn't move"
LONG_PRESS_MS = 500       # press held at least this long without moving = long press
MAX_GAP_S = 10.0          # cap on the pause between steps (stops idle time being replayed)


@dataclass
class Step:
    """One input action. `kind` is one of: tap, long_press, swipe, key, text, wait.

    Coordinates are absolute pixels on the recording device's screen.
    `t` is seconds since the recording started; `source` is 'window' or 'phone'.
    """

    kind: str
    x: int = 0
    y: int = 0
    x2: int = 0
    y2: int = 0
    duration_ms: int = 0
    key: str = ""
    text: str = ""
    t: float = 0.0
    source: str = "window"

    def describe(self) -> str:
        match self.kind:
            case "tap": return f"tap ({self.x}, {self.y})"
            case "long_press": return f"long press ({self.x}, {self.y}) {self.duration_ms}ms"
            case "swipe": return f"swipe ({self.x}, {self.y}) -> ({self.x2}, {self.y2}) {self.duration_ms}ms"
            case "key": return f"key {self.key}"
            case "text": return f"text {self.text!r}"
            case "wait": return f"wait {self.duration_ms}ms"
        return self.kind


def classify(x1: int, y1: int, x2: int, y2: int, duration_ms: int, t: float = 0.0,
             source: str = "window") -> Step:
    """Turn a press/release pair into a tap, long press or swipe."""
    duration_ms = max(int(duration_ms), 1)
    if math.hypot(x2 - x1, y2 - y1) < TAP_SLOP_PX:
        if duration_ms >= LONG_PRESS_MS:
            return Step("long_press", x1, y1, duration_ms=duration_ms, t=t, source=source)
        return Step("tap", x1, y1, t=t, source=source)
    return Step("swipe", x1, y1, x2, y2, duration_ms=max(duration_ms, 50), t=t, source=source)


@dataclass
class Recording:
    width: int
    height: int
    steps: list[Step] = field(default_factory=list)
    model: str = ""

    # ------------------------------------------------------------ persistence
    def save(self, path: str | Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {"width": self.width, "height": self.height, "model": self.model,
                "steps": [asdict(s) for s in self.steps]}
        path.write_text(json.dumps(data, indent=2))
        return path

    @classmethod
    def load(cls, path: str | Path) -> "Recording":
        data = json.loads(Path(path).read_text())
        return cls(data["width"], data["height"], [Step(**s) for s in data["steps"]],
                   data.get("model", ""))

    # ---------------------------------------------------------------- timing
    def gaps(self) -> list[float]:
        """Pause before each step, in seconds (first step = 0, capped at MAX_GAP_S)."""
        out, prev_end = [], None
        for s in self.steps:
            if prev_end is None:
                out.append(0.0)
            else:
                out.append(min(max(s.t - prev_end, 0.0), MAX_GAP_S))
            prev_end = s.t + (s.duration_ms / 1000 if s.kind in ("swipe", "long_press") else 0)
        return out

    # ---------------------------------------------------------------- replay
    def replay(self, device: "Device", speed: float = 1.0, fixed_gap: float | None = None,
               on_step=None) -> None:
        """Replay on `device`, rescaling coordinates if its screen size differs.

        speed > 1 shortens pauses; fixed_gap overrides recorded pauses entirely.
        """
        w, h = device.screen_size()
        sx, sy = w / self.width, h / self.height
        for i, (step, gap) in enumerate(zip(self.steps, self.gaps())):
            pause = fixed_gap if (fixed_gap is not None and i) else gap / speed
            if pause > 0:
                time.sleep(pause)
            if on_step:
                on_step(i, step)
            X = lambda v: int(round(v * sx))  # noqa: E731
            Y = lambda v: int(round(v * sy))  # noqa: E731
            match step.kind:
                case "tap": device.tap(X(step.x), Y(step.y))
                case "long_press": device.long_press(X(step.x), Y(step.y), step.duration_ms)
                case "swipe": device.swipe(X(step.x), Y(step.y), X(step.x2), Y(step.y2), step.duration_ms)
                case "key": device.key(step.key)
                case "text": device.text(step.text)
                case "wait": time.sleep(step.duration_ms / 1000)

    # ---------------------------------------------------------------- export
    def to_python(self, fractional: bool = True) -> str:
        """Generate a standalone droidtest script for this recording.

        fractional=True writes coordinates as screen fractions so the script
        replays on phones with other resolutions.
        """
        def fx(v): return f"{v / self.width:.4f}" if fractional else str(v)
        def fy(v): return f"{v / self.height:.4f}" if fractional else str(v)

        lines = [
            '"""Recorded with `droidtest mirror`'
            + (f" on {self.model}" if self.model else "") + f" ({self.width}x{self.height})."
            + '"""',
            "",
            "import time",
            "",
            "from droidtest import Device",
            "",
            "d = Device()",
            "",
        ]
        for step, gap in zip(self.steps, self.gaps()):
            if gap >= 0.05:
                lines.append(f"time.sleep({gap:.2f})")
            match step.kind:
                case "tap": lines.append(f"d.tap({fx(step.x)}, {fy(step.y)})")
                case "long_press": lines.append(f"d.long_press({fx(step.x)}, {fy(step.y)}, {step.duration_ms})")
                case "swipe": lines.append(
                    f"d.swipe({fx(step.x)}, {fy(step.y)}, {fx(step.x2)}, {fy(step.y2)}, {step.duration_ms})")
                case "key": lines.append(f"d.key({step.key!r})")
                case "text": lines.append(f"d.text({step.text!r})")
                case "wait": lines.append(f"time.sleep({step.duration_ms / 1000:.2f})")
        return "\n".join(lines) + "\n"
