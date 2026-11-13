# Mirror and recorder (`droidtest mirror`)

A desktop window that shows the phone screen live. You can control the phone
from it, and record what you do, either in the window or with your finger on
the phone, as a sequence you can replay or turn into a script.

```sh
uv run droidtest mirror                 # fits the window to your screen
uv run droidtest mirror --scale 3       # show at 1/3 size
uv run droidtest mirror --load artifacts/recordings/login.json
```

## Controlling the phone from the window

| In the window | Sends |
|---|---|
| click | tap |
| click and hold for over 0.5 s | long press |
| click and drag | swipe (a red arrow shows the drag) |
| right-click | Back |
| scroll wheel | a scroll swipe |
| side panel buttons | Back, Home, Recents, Vol +/−, Power, Notifications, Collapse |
| **Type text** box + Enter | types the text into the focused field on the phone |

The status bar shows the pointer position in pixels and as screen fractions,
the frame rate and the step count.

**Frame rate:** about 1 frame per second over USB, plus an immediate refresh
after each action. Each frame is a full screenshot, mostly limited by USB
transfer, so this isn't smooth video. Use [scrcpy](https://github.com/Genymobile/scrcpy)
if you need that.

## Recording

1. Tick **● Record**. Every action from the window is added to the step list.
2. Optionally tick **Record phone touches**. Taps, long presses and swipes made
   with your finger on the phone are recorded too, along with the hardware buttons
   (volume, power). These steps are marked 📱, and a blue circle on the mirror shows
   where your finger is.
3. Edit the list: **▲ / ▼** reorder, **Delete**, **+ Wait 1s** inserts a pause
   after the selected step, **Clear** removes everything.
4. **▶ Replay** plays the list on the phone at the chosen **speed**. Actions aren't
   recorded during replay.
5. **Save…** writes `NAME.json` and a matching `NAME.py` script to
   `artifacts/recordings/` by default. **Load…** opens a saved `.json`.

How a press is classified, for both the window and the phone: movement under 24 px
is a **tap**, or a **long press** if held 0.5 s or more. Anything that moves further
is a **swipe** with its real duration. Pauses between steps are kept as recorded,
capped at 10 s each.

### How phone touches are captured

The listener runs `adb shell -tt getevent -lt` and rebuilds gestures from the raw
touchscreen events. The touchscreen is found automatically: any input device that
reports `ABS_MT_POSITION_X/Y`, which is `sec_touchscreen` on the Galaxy A23. Taps
sent from the window are injected with `adb input`, which never reaches `getevent`,
so nothing gets recorded twice.

Limits:
- Only one-finger gestures are recorded. Pinches and other multi-touch gestures
  are skipped, with a message in the status bar.
- On-screen navigation buttons and gestures are recorded as touches, not as key steps.
- The listener re-checks orientation every 3 s. Landscape mapping hasn't been tested
  on a real phone yet.

## Using recordings

```sh
uv run droidtest replay artifacts/recordings/login.json            # recorded timing
uv run droidtest replay artifacts/recordings/login.json --speed 2  # twice as fast
uv run droidtest replay artifacts/recordings/login.json --gap 0.5  # fixed 0.5 s between steps
uv run droidtest export artifacts/recordings/login.json > tests/login_flow.py
```

From Python:

```python
from droidtest import Device, Recording, Step

rec = Recording.load("artifacts/recordings/login.json")
rec.replay(Device(), speed=1.5)

rec.steps.append(Step("key", key="HOME"))
rec.save("artifacts/recordings/login_then_home.json")
print(rec.to_python(fractional=True))
```

Replay rescales coordinates when the target phone's resolution differs from the
one used to record. Exported scripts use screen fractions by default; pass
`--pixels` or `fractional=False` for absolute pixels.

### The `.json` format

```json
{
  "width": 1080, "height": 2408, "model": "SM-A236B",
  "steps": [
    {"kind": "tap", "x": 540, "y": 1204, "x2": 0, "y2": 0, "duration_ms": 0,
     "key": "", "text": "", "t": 0.0, "source": "window"},
    {"kind": "swipe", "x": 540, "y": 2000, "x2": 540, "y2": 600, "duration_ms": 300,
     "key": "", "text": "", "t": 1.2, "source": "phone"}
  ]
}
```

`kind` is one of `tap`, `long_press`, `swipe`, `key`, `text` or `wait`.
Coordinates are pixels on the recording phone. `t` is seconds since recording
started. `source` is `window` or `phone`.

## From recording to a reliable test

Recordings store screen positions, so they break when a layout moves, the text
changes or a dialog shows up. Treat a recording as a first draft. Export it, then
replace the important taps with selectors and the fixed `time.sleep` pauses with waits:

```python
# exported
d.tap(0.5000, 0.7820)
time.sleep(1.40)

# hardened
await ui.tap(text="Sign in")
await ui.wait_for(resource_id="home_feed")
```

Run `droidtest dump` while the mirror is open to find the element under a
recorded tap.
