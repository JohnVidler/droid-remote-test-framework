"""Command-line helper for poking at a device while writing tests.

    droidtest info
    droidtest tap 540 1200          # pixels
    droidtest tap 0.5 0.9           # fractions of screen
    droidtest swipe 0.5 0.8 0.5 0.2
    droidtest text "hello world"
    droidtest key home
    droidtest dump                  # list on-screen elements
    droidtest find --text Settings
    droidtest tapel --text Settings # tap element by selector
    droidtest shot artifacts/s.png
    droidtest mirror                # live window: click/drag to control, record steps
    droidtest replay artifacts/recordings/rec.json --speed 2
    droidtest export artifacts/recordings/rec.json > my_test.py
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from .adb import Device, list_devices
from .ui import Selector


def _num(s: str) -> float | int:
    return float(s) if "." in s else int(s)


def _add_selector_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--text")
    p.add_argument("--text-contains")
    p.add_argument("--id", dest="resource_id")
    p.add_argument("--desc")
    p.add_argument("--desc-contains")
    p.add_argument("--class", dest="class_name")
    p.add_argument("--package")


def _selector(a: argparse.Namespace) -> Selector:
    return Selector(text=a.text, text_contains=a.text_contains, resource_id=a.resource_id,
                    desc=a.desc, desc_contains=a.desc_contains,
                    class_name=a.class_name, package=a.package)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="droidtest")
    ap.add_argument("-s", "--serial", help="device serial (default: ANDROID_SERIAL or only device)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("devices")
    sub.add_parser("info")
    p = sub.add_parser("tap"); p.add_argument("x", type=_num); p.add_argument("y", type=_num)
    p = sub.add_parser("longpress"); p.add_argument("x", type=_num); p.add_argument("y", type=_num)
    p.add_argument("--ms", type=int, default=800)
    p = sub.add_parser("swipe")
    for n in ("x1", "y1", "x2", "y2"):
        p.add_argument(n, type=_num)
    p.add_argument("--ms", type=int, default=300)
    p = sub.add_parser("text"); p.add_argument("value")
    p = sub.add_parser("key"); p.add_argument("keycode")
    p = sub.add_parser("shot"); p.add_argument("path", nargs="?", default="artifacts/screen.png")
    p = sub.add_parser("dump"); p.add_argument("--all", action="store_true")
    p.add_argument("--xml", action="store_true", help="print raw XML")
    p = sub.add_parser("find"); _add_selector_args(p)
    p = sub.add_parser("tapel"); _add_selector_args(p); p.add_argument("--timeout", type=float, default=10)
    p = sub.add_parser("launch"); p.add_argument("package"); p.add_argument("activity", nargs="?")
    sub.add_parser("focus")
    sub.add_parser("notifications")
    sub.add_parser("quicksettings")
    p = sub.add_parser("mirror", help="live screen window with recording")
    p.add_argument("--scale", type=int, help="integer downscale factor (default: fit screen)")
    p.add_argument("--interval", type=float, default=0.0, help="extra seconds between frames")
    p.add_argument("--load", help="recording .json to open")
    p = sub.add_parser("replay", help="replay a recording .json")
    p.add_argument("path"); p.add_argument("--speed", type=float, default=1.0)
    p.add_argument("--gap", type=float, help="fixed pause between steps (overrides recorded timing)")
    p = sub.add_parser("export", help="print a recording as a droidtest script")
    p.add_argument("path"); p.add_argument("--pixels", action="store_true", help="absolute coords")

    a = ap.parse_args(argv)

    if a.cmd == "devices":
        print("\n".join(list_devices()))
        return 0
    if a.cmd == "export":
        from .recording import Recording
        print(Recording.load(a.path).to_python(fractional=not a.pixels), end="")
        return 0

    d = Device(a.serial)
    ui = d.ui()

    match a.cmd:
        case "info":
            w, h = d.screen_size()
            print(f"serial={d.serial} model={d.model} sdk={d.sdk} size={w}x{h} "
                  f"orientation={d.orientation()} focus={d.current_focus()}")
        case "tap": d.tap(a.x, a.y)
        case "longpress": d.long_press(a.x, a.y, a.ms)
        case "swipe": d.swipe(a.x1, a.y1, a.x2, a.y2, a.ms)
        case "text": d.text(a.value)
        case "key": d.key(a.keycode)
        case "shot": print(d.screenshot(a.path))
        case "dump":
            if a.xml:
                print(asyncio.run(ui.dump_xml()))
            else:
                print("\n".join(asyncio.run(ui.describe(only_interesting=not a.all))))
        case "find":
            found = asyncio.run(ui.find_all(_selector(a)))
            for e in found:
                print(e.center, e.bounds, repr(e.label()))
            return 0 if found else 1
        case "tapel":
            e = asyncio.run(ui.tap(_selector(a), timeout=a.timeout))
            print("tapped", e.center, repr(e.label()))
        case "launch": d.launch(a.package, a.activity)
        case "focus": print(d.current_focus())
        case "notifications": d.open_notifications()
        case "quicksettings": d.open_quick_settings()
        case "mirror":
            from .mirror import run_mirror
            run_mirror(d, scale=a.scale, interval=a.interval, load=a.load)
        case "replay":
            from .recording import Recording
            rec = Recording.load(a.path)
            rec.replay(d, speed=a.speed, fixed_gap=a.gap,
                       on_step=lambda i, s: print(f"{i + 1:>3}. {s.describe()}"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
