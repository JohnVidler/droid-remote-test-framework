# droidtest

An Android UI test toolchain that runs over ADB. It controls the **whole phone**
rather than a single app: the launcher, notification shade, system dialogs,
Settings and any installed app. Nothing needs installing on the phone.

```python
from droidtest import Device

d = Device()
ui = d.ui()                # UI methods are async: call these inside `async def`
d.open_settings()
await ui.wait_package("com.android.settings")
await ui.scroll_to(text_regex=r"about (phone|device)")
d.tap(0.5, 0.9)            # pixels or screen fractions
```

## Setup

Requires Python 3.10+, [uv](https://docs.astral.sh/uv/), `adb` on your PATH and a
phone with USB debugging enabled.

```sh
uv sync --extra dev
uv run droidtest info       # check the phone is visible
uv run pytest -v            # run the example tests
```

Set `ANDROID_SERIAL` if more than one device is connected.

## Tools

| Tool | What it's for | Docs |
|---|---|---|
| `Device` | Low-level control: taps, swipes, keys, text, system UI, apps, screenshots, logcat | [docs/device.md](docs/device.md) |
| `UI` | Async element lookup and waiting for screen changes: `await ui.tap(text=...)`, `wait_any`, `wait_change`, `wait_idle` | [docs/ui.md](docs/ui.md) |
| `droidtest` CLI | Exploring the screen and trying commands while writing tests | [docs/cli.md](docs/cli.md) |
| `droidtest mirror` | A live screen window you can click and drag on, plus a recorder that captures window and on-phone input | [docs/mirror.md](docs/mirror.md) |
| Test harnesses | pytest fixtures and `ucrel_sections`-framed scripts | [docs/testing.md](docs/testing.md) |

## Layout

```
src/droidtest/
  adb.py           Device, Region, Point
  ui.py            UI, Selector, Element (async)
  recording.py     Recording, Step: save/load, replay, export to Python
  phone_input.py   getevent listener for real touches on the phone
  mirror.py        Tk mirror/recorder window
  cli.py           droidtest command
tests/             pytest: device smoke tests + offline recorder tests
sections_example.py  ucrel_sections example run
artifacts/         screenshots, failure dumps, recordings (git-ignored)
```

## Known limits

- `text()` only types ASCII. For other characters, install an ADB keyboard
  input method or use the clipboard.
- Each UI dump takes about 0.5 to 1.5 s, and the mirror shows about 1 frame per second.
- Games, video, many WebViews and `FLAG_SECURE` screens show little or no
  hierarchy. Use coordinate input and screenshots there.
- `adb input` can't do multi-touch, such as pinch. If you need it, add a
  `uiautomator2` or Appium backend behind the same `Device` API.
