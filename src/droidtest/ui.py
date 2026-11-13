"""Async UI hierarchy inspection via `uiautomator dump`.

All `UI` methods are coroutines, so screen changes can be awaited:

    await ui.tap(text="Allow")
    await ui.wait_for(resource_id="home_feed", timeout=15)
    idx, el = await ui.wait_any(Selector(text="OK"), Selector(text="Retry"))
    await asyncio.wait_for(ui.wait_change(), 5)

Blocking adb calls run in worker threads (asyncio.to_thread), so other tasks keep
running while a dump is in flight. Dumps are serialised per device.

Works on whatever is currently on screen: your app, another app, the launcher,
system dialogs, the notification shade. Note that some surfaces (games, video,
WebView content, secure/FLAG_SECURE windows) expose little or no hierarchy;
fall back to coordinate taps or screenshots there.
"""

from __future__ import annotations

import asyncio
import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable, Iterable

from .adb import AdbError, Point, Region

if TYPE_CHECKING:
    from .adb import Device

_BOUNDS_RE = re.compile(r"\[(-?\d+),(-?\d+)\]\[(-?\d+),(-?\d+)\]")
_REMOTE_DUMP = "/sdcard/droidtest_dump.xml"


class ElementNotFound(AssertionError):
    pass


@dataclass
class Element:
    text: str
    resource_id: str
    content_desc: str
    class_name: str
    package: str
    bounds: Region
    clickable: bool
    enabled: bool
    checked: bool
    focused: bool
    selected: bool
    scrollable: bool
    raw: dict = field(repr=False, default_factory=dict)

    @property
    def center(self) -> Point:
        return self.bounds.center

    @classmethod
    def from_node(cls, node: ET.Element) -> "Element":
        a = node.attrib
        m = _BOUNDS_RE.match(a.get("bounds", "[0,0][0,0]"))
        bounds = Region(*map(int, m.groups())) if m else Region(0, 0, 0, 0)
        b = lambda k: a.get(k) == "true"  # noqa: E731
        return cls(
            text=a.get("text", ""),
            resource_id=a.get("resource-id", ""),
            content_desc=a.get("content-desc", ""),
            class_name=a.get("class", ""),
            package=a.get("package", ""),
            bounds=bounds,
            clickable=b("clickable"),
            enabled=b("enabled"),
            checked=b("checked"),
            focused=b("focused"),
            selected=b("selected"),
            scrollable=b("scrollable"),
            raw=dict(a),
        )

    def signature(self) -> tuple:
        """Identity used for change detection."""
        return (self.class_name, self.resource_id, self.text, self.content_desc,
                self.bounds, self.checked, self.selected, self.focused)

    def label(self) -> str:
        return self.text or self.content_desc or self.resource_id or self.class_name


@dataclass
class Selector:
    """Match criteria. All given fields must match. `*_contains` / regex forms available."""

    text: str | None = None
    text_contains: str | None = None
    text_regex: str | None = None
    resource_id: str | None = None   # full ("com.app:id/foo") or suffix ("foo")
    desc: str | None = None
    desc_contains: str | None = None
    class_name: str | None = None
    package: str | None = None
    clickable: bool | None = None
    enabled: bool | None = None
    predicate: Callable[[Element], bool] | None = None
    ignore_case: bool = True

    def _eq(self, a: str, b: str) -> bool:
        return a.lower() == b.lower() if self.ignore_case else a == b

    def _in(self, needle: str, hay: str) -> bool:
        return needle.lower() in hay.lower() if self.ignore_case else needle in hay

    def matches(self, e: Element) -> bool:
        if self.text is not None and not self._eq(self.text, e.text):
            return False
        if self.text_contains is not None and not self._in(self.text_contains, e.text):
            return False
        if self.text_regex is not None and not re.search(
            self.text_regex, e.text, re.I if self.ignore_case else 0
        ):
            return False
        if self.resource_id is not None:
            rid = self.resource_id
            if not (e.resource_id == rid or e.resource_id.endswith(":id/" + rid)):
                return False
        if self.desc is not None and not self._eq(self.desc, e.content_desc):
            return False
        if self.desc_contains is not None and not self._in(self.desc_contains, e.content_desc):
            return False
        if self.class_name is not None and not e.class_name.endswith(self.class_name):
            return False
        if self.package is not None and e.package != self.package:
            return False
        if self.clickable is not None and e.clickable != self.clickable:
            return False
        if self.enabled is not None and e.enabled != self.enabled:
            return False
        if self.predicate is not None and not self.predicate(e):
            return False
        return True

    def __str__(self) -> str:
        parts = [f"{k}={v!r}" for k, v in self.__dict__.items()
                 if v is not None and k != "ignore_case"]
        return f"Selector({', '.join(parts)})"


class UI:
    """Async view of what's on screen. Get one via ``device.ui()``."""

    def __init__(self, device: "Device"):
        self.d = device

    # -------------------------------------------------------------- dumping
    def _dump_sync(self, retries: int) -> str:
        last = ""
        with self.d.dump_lock:
            for attempt in range(retries):
                out = self.d.shell(f"uiautomator dump --compressed {_REMOTE_DUMP}", check=False)
                if "dumped to" in out.lower():
                    xml = self.d.exec_out(f"cat {_REMOTE_DUMP}").decode("utf-8", errors="replace")
                    if xml.lstrip().startswith("<?xml"):
                        return xml
                last = out
                time.sleep(0.5 * (attempt + 1))
        raise AdbError(f"uiautomator dump failed: {last.strip()}")

    async def dump_xml(self, retries: int = 3) -> str:
        """Current window hierarchy as XML (retries while the screen is animating)."""
        return await asyncio.to_thread(self._dump_sync, retries)

    async def elements(self) -> list[Element]:
        root = ET.fromstring(await self.dump_xml())
        return [Element.from_node(n) for n in root.iter("node")]

    # ------------------------------------------------------------- querying
    @staticmethod
    def _sel(selector: Selector | None, kw: dict) -> Selector:
        if selector is not None and kw:
            raise TypeError("Pass either a Selector or keyword criteria, not both.")
        return selector or Selector(**kw)

    @staticmethod
    def match(sel: Selector, elements: Iterable[Element]) -> list[Element]:
        """Filter an already-fetched element list (no device round-trip)."""
        return [e for e in elements if sel.matches(e)]

    async def find_all(self, selector: Selector | None = None, **kw) -> list[Element]:
        return self.match(self._sel(selector, kw), await self.elements())

    async def find(self, selector: Selector | None = None, **kw) -> Element | None:
        found = await self.find_all(selector, **kw)
        return found[0] if found else None

    async def exists(self, selector: Selector | None = None, **kw) -> bool:
        return await self.find(selector, **kw) is not None

    # -------------------------------------------------------------- waiting
    async def _poll(self, check: Callable[[list[Element]], object], timeout: float,
                    interval: float, what: str):
        """Dump repeatedly until check(elements) returns a non-None value."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while True:
            result = check(await self.elements())
            if result is not None:
                return result
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise ElementNotFound(f"{what} not satisfied within {timeout}s")
            await asyncio.sleep(min(interval, remaining))

    async def wait_for(self, selector: Selector | None = None, timeout: float = 10.0,
                       interval: float = 0.3, **kw) -> Element:
        """Wait until an element matching the selector is on screen."""
        sel = self._sel(selector, kw)
        return await self._poll(lambda els: next(iter(self.match(sel, els)), None),
                                timeout, interval, str(sel))

    async def wait_gone(self, selector: Selector | None = None, timeout: float = 10.0,
                        interval: float = 0.3, **kw) -> None:
        """Wait until no element matches the selector."""
        sel = self._sel(selector, kw)
        await self._poll(lambda els: True if not self.match(sel, els) else None,
                         timeout, interval, f"absence of {sel}")

    async def wait_any(self, *selectors: Selector, timeout: float = 10.0,
                       interval: float = 0.3) -> tuple[int, Element]:
        """Wait for the first of several possible outcomes, checked against one dump.

        Returns (index_of_matching_selector, element), e.g.::

            i, el = await ui.wait_any(Selector(text="Allow"), Selector(text="Home"))
        """
        def check(els):
            for i, sel in enumerate(selectors):
                hit = self.match(sel, els)
                if hit:
                    return i, hit[0]
            return None
        return await self._poll(check, timeout, interval, " | ".join(map(str, selectors)))

    async def snapshot(self) -> frozenset:
        """Opaque fingerprint of the current screen, for use with wait_change."""
        return frozenset(e.signature() for e in await self.elements())

    async def wait_change(self, baseline: frozenset | None = None, timeout: float = 10.0,
                          interval: float = 0.3) -> list[Element]:
        """Wait until the screen differs from `baseline` (default: the screen right now).

        Take the baseline *before* acting to avoid missing a fast transition::

            before = await ui.snapshot()
            device.tap(0.5, 0.5)
            await ui.wait_change(before)
        """
        if baseline is None:
            baseline = await self.snapshot()
        return await self._poll(
            lambda els: els if frozenset(e.signature() for e in els) != baseline else None,
            timeout, interval, "screen change")

    async def wait_idle(self, stable_dumps: int = 2, timeout: float = 10.0,
                        interval: float = 0.3) -> list[Element]:
        """Wait until the hierarchy is unchanged for `stable_dumps` consecutive dumps."""
        state = {"last": None, "count": 0}

        def check(els):
            sig = frozenset(e.signature() for e in els)
            state["count"] = state["count"] + 1 if sig == state["last"] else 1
            state["last"] = sig
            return els if state["count"] >= stable_dumps else None
        return await self._poll(check, timeout, interval, "screen idle")

    async def wait_package(self, package: str, timeout: float = 10.0,
                           interval: float = 0.3) -> None:
        """Wait until `package` owns the focused window (cheaper than a UI dump)."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while (await asyncio.to_thread(self.d.current_package)) != package:
            if loop.time() >= deadline:
                raise ElementNotFound(f"package {package!r} not focused within {timeout}s")
            await asyncio.sleep(interval)

    # -------------------------------------------------------------- actions
    async def _input(self, fn: Callable, *args) -> None:
        await asyncio.to_thread(fn, *args)

    async def tap(self, selector: Selector | None = None, timeout: float = 10.0, **kw) -> Element:
        e = await self.wait_for(selector, timeout=timeout, **kw)
        await self._input(self.d.tap, e.center.x, e.center.y)
        return e

    async def long_press(self, selector: Selector | None = None, timeout: float = 10.0,
                         duration_ms: int = 800, **kw) -> Element:
        e = await self.wait_for(selector, timeout=timeout, **kw)
        await self._input(self.d.long_press, e.center.x, e.center.y, duration_ms)
        return e

    async def set_text(self, value: str, selector: Selector | None = None,
                       clear: bool = True, timeout: float = 10.0, **kw) -> Element:
        """Tap a field, optionally clear it, then type."""
        e = await self.tap(selector, timeout=timeout, **kw)
        if clear:
            await self._input(self.d.key, "MOVE_END")
            for _ in range(len(e.text) or 1):
                await self._input(self.d.key, "DEL")
        await self._input(self.d.text, value)
        return e

    async def scroll_to(self, selector: Selector | None = None, direction: str = "down",
                        max_swipes: int = 10, **kw) -> Element:
        sel = self._sel(selector, kw)
        for _ in range(max_swipes + 1):
            hit = self.match(sel, await self.elements())
            if hit:
                return hit[0]
            await self._input(self.d.scroll, direction)
            await asyncio.sleep(0.4)
        raise ElementNotFound(f"{sel} not found after {max_swipes} swipes {direction}")

    # ----------------------------------------------------------- debugging
    async def describe(self, only_interesting: bool = True) -> list[str]:
        """Human-readable lines for each element (useful for writing selectors)."""
        lines = []
        for e in await self.elements():
            if only_interesting and not (e.text or e.content_desc or e.resource_id or e.clickable):
                continue
            flags = "".join(c for c, on in (("C", e.clickable), ("S", e.scrollable),
                                             ("x", e.checked), ("F", e.focused)) if on)
            b = e.bounds
            lines.append(f"[{b.left},{b.top}][{b.right},{b.bottom}] {flags:<3} "
                         f"{e.class_name.rsplit('.', 1)[-1]:<18} "
                         f"id={e.resource_id.split('/')[-1]!r} text={e.text!r} desc={e.content_desc!r}")
        return lines
