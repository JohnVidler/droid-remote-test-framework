# UI: finding elements and awaiting screen changes (`droidtest.UI`)

`UI` reads what's on screen with `uiautomator dump`. This works on any app and on
system UI such as permission dialogs and the notification shade. **Every method
is a coroutine**, so call it with `await` inside `async def`:

```python
import asyncio
from droidtest import Device, Selector

async def main():
    d = Device()
    ui = d.ui()
    d.launch("com.example.app")
    await ui.wait_package("com.example.app")
    await ui.tap(text="Allow")                       # system permission dialog
    await ui.set_text("user@example.com", resource_id="email")
    await ui.tap(resource_id="login_button")
    await ui.wait_for(text_contains="Welcome", timeout=15)

asyncio.run(main())
```

While a method waits on adb, other asyncio tasks keep running. Dumps are
serialised per device, so concurrent tasks can't clash on the phone.

## Selectors

Pass criteria as keywords or build a `Selector`. An element matches only if
**every** given field matches. Matching ignores case unless you pass
`ignore_case=False`.

| Field | Matches |
|---|---|
| `text`, `text_contains`, `text_regex` | visible text |
| `resource_id` | the full id (`com.app:id/login`) or just the last part (`login`) |
| `desc`, `desc_contains` | content-description (accessibility label) |
| `class_name` | class-name suffix, e.g. `"Button"`, `"EditText"` |
| `package` | the app that owns the element |
| `clickable`, `enabled` | `True` / `False` |
| `predicate` | any `Callable[[Element], bool]` |

```python
await ui.tap(text="Settings")
await ui.tap(Selector(class_name="Switch", text_contains="wi-fi"))
await ui.find_all(predicate=lambda e: e.bounds.top > 2000)
```

To work out selectors, run `uv run droidtest dump` with the screen open ([CLI](cli.md)).

## Querying

| Method | Returns |
|---|---|
| `elements()` | every `Element` on screen (one dump) |
| `find_all(sel)`, `find(sel)`, `exists(sel)` | matches / first match or `None` / `bool` |
| `UI.match(sel, elements)` | filters an element list you already have, without asking the phone again |
| `dump_xml()` | the raw hierarchy XML |
| `describe()` | readable one-line summaries of the elements |

An `Element` has `text`, `resource_id`, `content_desc`, `class_name`, `package`,
`bounds` (a `Region`), `center`, `clickable`, `enabled`, `checked`, `focused`,
`selected`, `scrollable` and `raw` (every XML attribute).

## Waiting for the screen

The wait methods check the screen about every `interval` (default 0.3 s) until
the condition holds. If it doesn't hold before `timeout`, they raise
**`ElementNotFound`**. That's a subclass of `AssertionError`, so it fails a pytest
test or a `ucrel_sections` test block like any other assertion.

| Method | Finishes when |
|---|---|
| `wait_for(sel)` | a match appears, and returns it |
| `wait_gone(sel)` | nothing matches |
| `wait_any(sel_a, sel_b, ...)` | the first selector to match, and returns `(index, element)` |
| `wait_change(baseline=None)` | the screen differs from `baseline`, a value from `snapshot()` |
| `wait_idle(stable_dumps=2)` | the screen has stopped changing (animations finished) |
| `wait_package(pkg)` | that app owns the focused window (uses `dumpsys`, faster than a dump) |

```python
# Branch on whichever outcome happens first
i, el = await ui.wait_any(Selector(text="Allow"), Selector(text="Home"), timeout=8)
if i == 0:
    await ui.tap(text="Allow")

# Take the snapshot BEFORE acting, so a fast transition isn't missed
before = await ui.snapshot()
d.tap(0.5, 0.8)
await ui.wait_change(before)

# Set an overall deadline with plain asyncio
await asyncio.wait_for(ui.wait_for(text="Done"), timeout=30)
```

`wait_idle` never finishes on a screen that keeps changing, such as the lock
screen clock, a video or a progress spinner. Use `wait_for` a specific element there.

## Actions

Each action first waits for its element, then acts on its centre.

| Method | Notes |
|---|---|
| `tap(sel, timeout=10)` | returns the element it tapped |
| `long_press(sel, duration_ms=800)` | |
| `set_text(value, sel, clear=True)` | taps the field, deletes the existing text, then types |
| `scroll_to(sel, direction="down", max_swipes=10)` | scrolls until the element appears |

## Limits

- One dump takes about 0.5 to 1.5 s. If you're checking several things on the
  same screen, call `elements()` once and use `UI.match`.
- Games, video, many WebViews and secure windows (`FLAG_SECURE`, e.g. banking
  apps) show little or no hierarchy. Use [coordinate input](device.md) there.
