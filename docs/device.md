# Device: low-level control (`droidtest.Device`)

`Device` wraps the `adb` binary. It works at the device level, not inside one
app, so it can drive the launcher, notification shade, system dialogs, Settings
and any installed app. All methods are synchronous: they return as soon as adb
does. To wait for the screen to react, use [`UI`](ui.md).

```python
from droidtest import Device, Region

d = Device()                 # the only connected device, or $ANDROID_SERIAL
d = Device("R5CX20ZZKQM")    # a specific serial
```

The constructor raises `AdbError` if there's no device, or if several are
connected and no serial is given.

## Coordinates

Every method that takes a position accepts either:

| Value | Meaning | Example |
|---|---|---|
| `int` | absolute pixels | `d.tap(540, 1200)` |
| `float` in `0..1` | fraction of the screen | `d.tap(0.5, 0.9)`: bottom centre on any phone |

`1.0` counts as a fraction (the far edge), not pixel 1. A float above 1, such as
`540.0`, is treated as pixels. Fractions follow the current orientation.

`Region(left, top, right, bottom)` is a pixel rectangle with `.center`,
`.width`, `.height` and `.contains(point)`. `d.tap_region(r)` taps its centre.

## Input

| Method | Notes |
|---|---|
| `tap(x, y)` | |
| `tap_region(region)` | taps the centre |
| `long_press(x, y, duration_ms=800)` | |
| `swipe(x1, y1, x2, y2, duration_ms=300)` | |
| `drag(x1, y1, x2, y2, duration_ms=1000)` | press, hold and move (drag and drop); Android 7+ |
| `scroll(direction="down", amount=0.5)` | swipes across the screen centre; `direction` is where the content moves (`down` shows content further down) |
| `text(value)` | types into the focused field; ASCII only |
| `key(code, long=False)` | `"home"`, `"KEYCODE_HOME"` or `3` all work |
| `home()`, `back()`, `recents()`, `power()`, `volume_up()`, `volume_down()` | shortcuts for `key` |

## System UI and screen state

| Method | Notes |
|---|---|
| `open_notifications()`, `open_quick_settings()`, `collapse_status_bar()` | uses `cmd statusbar` |
| `is_screen_on()`, `wake()` | |
| `unlock_swipe()` | wakes the phone and swipes up; only works with no PIN or pattern set |
| `screen_size()` | `(width, height)` in the current orientation (cached; pass `refresh=True` after rotating) |
| `orientation()` | 0 portrait, 1 landscape, 2 reverse portrait, 3 reverse landscape |
| `current_focus()`, `current_package()` | the app/activity that owns the focused window |

## Capture

| Method | Notes |
|---|---|
| `screenshot(path)` | saves a PNG; takes about 2 s on a Galaxy A23 |
| `screencap_raw()` | `(w, h, rgba_bytes)`, about 1 s; used by the mirror |
| `start_screenrecord()` / `stop_screenrecord(proc, local_path)` | records an MP4 on the phone, then pulls it |
| `logcat_clear()`, `logcat_dump(path=None, filter_spec="")` | |

## Apps and permissions

| Method | Notes |
|---|---|
| `launch(package, activity=None)` | without an activity, starts the launcher entry |
| `open_url(url)`, `open_settings(action=...)` | `action` takes intents such as `android.settings.WIFI_SETTINGS` |
| `stop_app(pkg)`, `clear_app(pkg)` | force-stop / wipe the app's data |
| `install(apk, replace=True, grant_all=True)`, `uninstall(pkg)`, `is_installed(pkg)` | |
| `grant(pkg, perm)`, `revoke(pkg, perm)` | runtime permissions, e.g. `android.permission.CAMERA` |

## Escape hatches

| Method | Notes |
|---|---|
| `shell(cmd, check=True)` | runs a shell command and returns its output |
| `exec_out(cmd)` | returns raw binary output |
| `push(local, remote)`, `pull(remote, local)`, `prop(name)` | |
| `model`, `sdk` | properties |
| `ui()` | the shared async [`UI`](ui.md) helper for this device |

A failing adb command raises `AdbError`, which includes adb's stderr.
