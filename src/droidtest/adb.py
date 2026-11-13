"""Thin, dependency-free wrapper around the `adb` binary.

Everything here operates at the *device* level (not inside a single app), so it
can drive the launcher, notification shade, system dialogs, settings, and any
installed app.
"""

from __future__ import annotations

import os
import re
import shlex
import shutil
import struct
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path


class AdbError(RuntimeError):
    pass


@dataclass(frozen=True)
class Point:
    x: int
    y: int


@dataclass(frozen=True)
class Region:
    """A rectangle in absolute screen pixels (left, top, right, bottom)."""

    left: int
    top: int
    right: int
    bottom: int

    @property
    def center(self) -> Point:
        return Point((self.left + self.right) // 2, (self.top + self.bottom) // 2)

    @property
    def width(self) -> int:
        return self.right - self.left

    @property
    def height(self) -> int:
        return self.bottom - self.top

    def contains(self, p: Point) -> bool:
        return self.left <= p.x < self.right and self.top <= p.y < self.bottom


def list_devices(adb: str = "adb") -> list[str]:
    out = subprocess.run([adb, "devices"], capture_output=True, text=True, check=True).stdout
    return [
        line.split()[0]
        for line in out.splitlines()[1:]
        if line.strip() and line.split()[-1] == "device"
    ]


class Device:
    """A single connected Android device.

    Coordinates accepted by tap/swipe/etc. may be either absolute pixels (ints)
    or fractions of the screen (floats in 0..1), which keeps scripts portable
    across screen sizes: ``d.tap(0.5, 0.9)`` taps bottom-centre on any phone.
    """

    def __init__(self, serial: str | None = None, adb: str | None = None, timeout: float = 30.0):
        self.adb = adb or shutil.which("adb") or "adb"
        self.serial = serial or os.environ.get("ANDROID_SERIAL")
        if self.serial is None:
            devices = list_devices(self.adb)
            if not devices:
                raise AdbError("No connected devices (check `adb devices`).")
            if len(devices) > 1:
                raise AdbError(f"Multiple devices {devices}; pass serial= or set ANDROID_SERIAL.")
            self.serial = devices[0]
        self.timeout = timeout
        self._size: tuple[int, int] | None = None
        # uiautomator can't run concurrently; serialises dumps across threads/tasks.
        self.dump_lock = threading.Lock()
        self._ui: "UI | None" = None

    # ------------------------------------------------------------------ raw adb
    def _run(self, *args: str, binary: bool = False, check: bool = True, timeout: float | None = None):
        cmd = [self.adb, "-s", self.serial, *args]
        proc = subprocess.run(cmd, capture_output=True, timeout=timeout or self.timeout)
        if check and proc.returncode != 0:
            raise AdbError(f"{shlex.join(cmd)} failed ({proc.returncode}): {proc.stderr.decode(errors='replace').strip()}")
        return proc.stdout if binary else proc.stdout.decode(errors="replace")

    def shell(self, command: str, check: bool = True, timeout: float | None = None) -> str:
        """Run a shell command on the device and return stdout."""
        return self._run("shell", command, check=check, timeout=timeout)

    def exec_out(self, command: str) -> bytes:
        """Run a command and return raw binary stdout (no CRLF mangling)."""
        return self._run("exec-out", command, binary=True)

    def push(self, local: str | Path, remote: str) -> None:
        self._run("push", str(local), remote)

    def pull(self, remote: str, local: str | Path) -> None:
        self._run("pull", remote, str(local))

    # ------------------------------------------------------------- device info
    def prop(self, name: str) -> str:
        return self.shell(f"getprop {name}").strip()

    @property
    def model(self) -> str:
        return self.prop("ro.product.model")

    @property
    def sdk(self) -> int:
        return int(self.prop("ro.build.version.sdk"))

    def screen_size(self, refresh: bool = False) -> tuple[int, int]:
        """(width, height) in pixels for the current orientation."""
        if self._size is None or refresh:
            out = self.shell("wm size")
            m = re.findall(r"(\d+)x(\d+)", out)
            if not m:
                raise AdbError(f"Could not parse screen size from: {out!r}")
            w, h = map(int, m[-1])  # last line = override size if set
            if self.orientation() in (1, 3):
                w, h = h, w
            self._size = (w, h)
        return self._size

    def orientation(self) -> int:
        """0=portrait, 1=landscape, 2=reverse portrait, 3=reverse landscape."""
        out = self.shell("dumpsys input | grep -m1 -i 'SurfaceOrientation'", check=False)
        m = re.search(r"SurfaceOrientation:\s*(\d)", out)
        return int(m.group(1)) if m else 0

    def _xy(self, x: float, y: float) -> tuple[int, int]:
        if isinstance(x, float) or isinstance(y, float):
            w, h = self.screen_size()
            if isinstance(x, float) and 0.0 <= x <= 1.0:
                x = x * w
            if isinstance(y, float) and 0.0 <= y <= 1.0:
                y = y * h
        return int(round(x)), int(round(y))

    # ------------------------------------------------------------------ input
    def tap(self, x: float, y: float) -> None:
        x, y = self._xy(x, y)
        self.shell(f"input tap {x} {y}")

    def tap_region(self, region: Region) -> None:
        c = region.center
        self.tap(c.x, c.y)

    def long_press(self, x: float, y: float, duration_ms: int = 800) -> None:
        x, y = self._xy(x, y)
        self.shell(f"input swipe {x} {y} {x} {y} {duration_ms}")

    def swipe(self, x1: float, y1: float, x2: float, y2: float, duration_ms: int = 300) -> None:
        x1, y1 = self._xy(x1, y1)
        x2, y2 = self._xy(x2, y2)
        self.shell(f"input swipe {x1} {y1} {x2} {y2} {duration_ms}")

    def drag(self, x1: float, y1: float, x2: float, y2: float, duration_ms: int = 1000) -> None:
        """Press-hold-move (for drag & drop). Needs Android 7+ (`input draganddrop`)."""
        x1, y1 = self._xy(x1, y1)
        x2, y2 = self._xy(x2, y2)
        self.shell(f"input draganddrop {x1} {y1} {x2} {y2} {duration_ms}")

    def scroll(self, direction: str = "down", amount: float = 0.5, duration_ms: int = 300) -> None:
        """Scroll content by swiping across the middle of the screen.

        direction is where the *content* moves to reveal: 'down' reveals content below.
        """
        half = amount / 2
        moves = {
            "down": (0.5, 0.5 + half, 0.5, 0.5 - half),
            "up": (0.5, 0.5 - half, 0.5, 0.5 + half),
            "right": (0.5 + half, 0.5, 0.5 - half, 0.5),
            "left": (0.5 - half, 0.5, 0.5 + half, 0.5),
        }
        self.swipe(*moves[direction], duration_ms=duration_ms)

    def text(self, value: str) -> None:
        """Type text into the focused field (ASCII only; see README for unicode)."""
        escaped = value.replace("\\", "\\\\").replace(" ", "%s")
        escaped = re.sub(r"([\"'`$&|;<>()*?!#~\[\]{}^])", r"\\\1", escaped)
        self.shell(f"input text {escaped}")

    def key(self, keycode: str | int, long: bool = False) -> None:
        """Send a key event. Accepts ints, 'KEYCODE_HOME', or short names like 'home'."""
        if isinstance(keycode, str) and not keycode.isdigit() and not keycode.upper().startswith("KEYCODE_"):
            keycode = "KEYCODE_" + keycode.upper()
        self.shell(f"input keyevent {'--longpress ' if long else ''}{keycode}")

    # Common system navigation
    def home(self) -> None: self.key("HOME")
    def back(self) -> None: self.key("BACK")
    def recents(self) -> None: self.key("APP_SWITCH")
    def power(self) -> None: self.key("POWER")
    def volume_up(self) -> None: self.key("VOLUME_UP")
    def volume_down(self) -> None: self.key("VOLUME_DOWN")

    def open_notifications(self) -> None:
        self.shell("cmd statusbar expand-notifications")

    def open_quick_settings(self) -> None:
        self.shell("cmd statusbar expand-settings")

    def collapse_status_bar(self) -> None:
        self.shell("cmd statusbar collapse")

    # ------------------------------------------------------------ screen state
    def is_screen_on(self) -> bool:
        out = self.shell("dumpsys power | grep -E 'mWakefulness=|Display Power: state='", check=False)
        return "Awake" in out or "state=ON" in out

    def wake(self) -> None:
        if not self.is_screen_on():
            self.key("WAKEUP")

    def unlock_swipe(self) -> None:
        """Wake and swipe up past a no-security lock screen."""
        self.wake()
        time.sleep(0.3)
        self.swipe(0.5, 0.85, 0.5, 0.2, 250)

    def screenshot(self, path: str | Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(self.exec_out("screencap -p"))
        return path

    def screencap_raw(self) -> tuple[int, int, bytes]:
        """Uncompressed frame as (width, height, RGBA bytes).

        Several times faster than PNG (`screencap -p` spends ~1-2 s encoding on
        the phone); the cost is mostly USB transfer of width*height*4 bytes.
        """
        data = self.exec_out("screencap")
        if len(data) < 16:
            raise AdbError("screencap returned no data")
        w, h, fmt = struct.unpack_from("<III", data, 0)
        if fmt != 1:  # 1 = RGBA_8888; anything else, let the caller fall back to PNG
            raise AdbError(f"unsupported raw pixel format {fmt}")
        n = w * h * 4
        header = len(data) - n           # 12 bytes on old Android, 16 on 9+
        if header not in (12, 16):
            raise AdbError(f"unexpected raw screencap size {len(data)} for {w}x{h}")
        return w, h, data[header:]

    @staticmethod
    def rgba_to_ppm(w: int, h: int, rgba: bytes) -> bytes:
        """Convert RGBA to binary PPM (P6), which Tk's PhotoImage reads natively."""
        rgb = bytearray(w * h * 3)
        rgb[0::3] = rgba[0::4]
        rgb[1::3] = rgba[1::4]
        rgb[2::3] = rgba[2::4]
        return b"P6 %d %d 255\n" % (w, h) + bytes(rgb)

    def start_screenrecord(self, remote: str = "/sdcard/droidtest_rec.mp4", time_limit: int = 180) -> subprocess.Popen:
        """Begin recording; call stop_screenrecord(proc, local_path) to finish."""
        return subprocess.Popen(
            [self.adb, "-s", self.serial, "shell", f"screenrecord --time-limit {time_limit} {remote}"]
        )

    def stop_screenrecord(self, proc: subprocess.Popen, local: str | Path,
                          remote: str = "/sdcard/droidtest_rec.mp4") -> Path:
        self.shell("pkill -INT screenrecord", check=False)
        proc.wait(timeout=10)
        time.sleep(1.0)  # let the mp4 finalise
        self.pull(remote, local)
        self.shell(f"rm -f {remote}", check=False)
        return Path(local)

    # ------------------------------------------------------------------- apps
    def current_focus(self) -> tuple[str, str] | None:
        """(package, activity) of the focused window, or None."""
        out = self.shell("dumpsys window | grep -E 'mCurrentFocus|mFocusedApp'", check=False)
        m = re.search(r"([\w.]+)/([\w.$]+)", out)
        return (m.group(1), m.group(2)) if m else None

    def current_package(self) -> str | None:
        f = self.current_focus()
        return f[0] if f else None

    def launch(self, package: str, activity: str | None = None) -> None:
        if activity:
            self.shell(f"am start -W -n {package}/{activity}")
        else:
            self.shell(f"monkey -p {package} -c android.intent.category.LAUNCHER 1")

    def open_url(self, url: str) -> None:
        self.shell(f"am start -a android.intent.action.VIEW -d {shlex.quote(url)}")

    def open_settings(self, action: str = "android.settings.SETTINGS") -> None:
        self.shell(f"am start -a {action}")

    def stop_app(self, package: str) -> None:
        self.shell(f"am force-stop {package}")

    def clear_app(self, package: str) -> None:
        self.shell(f"pm clear {package}")

    def install(self, apk: str | Path, replace: bool = True, grant_all: bool = True) -> None:
        args = ["install"]
        if replace:
            args.append("-r")
        if grant_all:
            args.append("-g")
        self._run(*args, str(apk), timeout=300)

    def uninstall(self, package: str) -> None:
        self._run("uninstall", package, check=False)

    def is_installed(self, package: str) -> bool:
        return f"package:{package}" in self.shell(f"pm list packages {package}").split()

    def grant(self, package: str, permission: str) -> None:
        self.shell(f"pm grant {package} {permission}")

    def revoke(self, package: str, permission: str) -> None:
        self.shell(f"pm revoke {package} {permission}")

    # -------------------------------------------------------------------- misc
    def logcat_clear(self) -> None:
        self._run("logcat", "-c")

    def logcat_dump(self, path: str | Path | None = None, filter_spec: str = "") -> str:
        out = self._run("logcat", "-d", *filter_spec.split())
        if path:
            Path(path).write_text(out)
        return out

    def ui(self) -> "UI":
        """The (cached) async UI helper for this device."""
        if self._ui is None:
            from .ui import UI
            self._ui = UI(self)
        return self._ui

    def __repr__(self) -> str:
        return f"Device(serial={self.serial!r})"
