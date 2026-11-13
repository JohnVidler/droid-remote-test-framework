"""Example device test run framed with ucrel_sections.

Run with:  uv run python sections_example.py

How the sections behave:
  * `s.test(name)` is a hard test: a failure halts the enclosing Section.
  * `s.test(name, canFail=True)` is a soft test: the failure is recorded and the run continues.
  * `t.reject(cond, msg)` fails the test when `cond` is TRUE.
  * `s.tryAssert(cond, msg, name=...)` records a pass/fail without raising.
  * droidtest's UI methods are coroutines: `await ui.wait_for(...)` etc. They raise
    ElementNotFound (an AssertionError), so they work directly inside a test block.
    Section/test/timer are ordinary `with` blocks and work fine inside `async def`.
"""

import asyncio
import sys
from pathlib import Path

from ucrel_sections import Section

from droidtest import Device, Region, Selector

ARTIFACTS = Path("artifacts/sections")
SETTINGS_PKG = "com.android.settings"
SYSTEMUI_PKG = "com.android.systemui"

failed = 0  # total failures across all sections, used for the exit code


def finish(s: Section, d: Device | None = None) -> None:
    """Count this section's failures and grab a screenshot if any occurred."""
    global failed
    bad = [r for r in s.tests if r.failed]
    failed += len(bad)
    if bad and d is not None:
        slug = s.title.lower().replace(" ", "_")
        d.screenshot(ARTIFACTS / f"{slug}.png")


async def main() -> None:
    # --------------------------------------------------------------------------- setup
    with Section("Device setup") as s:
        with s.test("device connected") as t:
            d = Device()
            ui = d.ui()
        s.report("device", f"{d.model} ({d.serial}), SDK {d.sdk}, {d.screen_size()[0]}x{d.screen_size()[1]}")

        with s.timer("wake and go home"):
            d.wake()
            d.home()
            await ui.wait_idle()

        s.tryAssert(d.is_screen_on(), "screen is off", name="screen on")
        s.tryAssert(d.current_package() is not None, "no focused window", name="has focus")
    finish(s, d)

    # ---------------------------------------------------------- system UI interaction
    with Section("Notification shade") as s:
        with s.test("shade opens", canFail=True) as t:
            d.open_notifications()
            await ui.wait_for(package=SYSTEMUI_PKG, timeout=5)
            t.report(f"{len(await ui.find_all(package=SYSTEMUI_PKG))} systemui elements visible")

        with s.test("quick settings opens", canFail=True) as t:
            d.open_quick_settings()
            # Either form of the tile counts; one dump checks both.
            await ui.wait_any(Selector(desc_contains="wi-fi"), Selector(text_contains="wi-fi"),
                              timeout=5)

        with s.test("shade closes", canFail=True) as t:
            d.collapse_status_bar()
            await ui.wait_idle()
            t.reject(d.current_package() == SYSTEMUI_PKG, "shade still focused after collapse")
    finish(s, d)

    # ------------------------------------------------------------------ app navigation
    with Section("Settings app") as s:
        with s.timer("cold launch"):
            d.stop_app(SETTINGS_PKG)
            d.open_settings()
            await ui.wait_package(SETTINGS_PKG, timeout=10)

        with s.test("settings in foreground", canFail=True) as t:
            t.reject(d.current_package() != SETTINGS_PKG, f"focus is {d.current_focus()}")

        with s.test("scroll to About phone", canFail=True) as t:
            el = await ui.scroll_to(text_regex=r"about (phone|device)", max_swipes=8)
            t.report(f"found {el.text!r} at {el.center}")
            await ui.tap(text=el.text)
            await ui.wait_for(text_contains="model", timeout=5)

        with s.test("back returns to settings list", canFail=True) as t:
            d.back()
            await ui.wait_gone(text_contains="model name", timeout=5)

        d.home()
    finish(s, d)

    # --------------------------------------------------------- raw coordinate gestures
    with Section("Coordinate gestures") as s:
        with s.test("swipe up opens app drawer", canFail=True) as t:
            d.home()
            await ui.wait_idle()
            before_snap = await ui.snapshot()
            before = len(await ui.find_all(resource_id="icon"))
            d.swipe(0.5, 0.8, 0.5, 0.3)          # fractional coords: works on any screen
            await ui.wait_change(before_snap, timeout=5)
            await ui.wait_idle()
            after = len(await ui.find_all(resource_id="icon"))
            t.report(f"icons visible: {before} -> {after}")
            t.reject(after <= before, "app drawer did not appear to open")

        with s.test("tap a screen region", canFail=True) as t:
            status_bar = Region(0, 0, d.screen_size()[0], 60)   # absolute pixels
            d.tap_region(status_bar)
            t.report(f"tapped {status_bar.center}")

        d.home()
    finish(s, d)


asyncio.run(main())
print(f"{'ALL PASSED' if failed == 0 else f'{failed} FAILURE(S)'}")
sys.exit(1 if failed else 0)
