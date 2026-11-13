# Command line (`droidtest`)

Use this to poke at the phone while writing tests. Run it with `uv run droidtest ...`,
or use `python -m droidtest`. Add `-s SERIAL` before the command to pick a device.

## Inspecting

| Command | Does |
|---|---|
| `devices` | lists connected serials |
| `info` | model, SDK, screen size, orientation, focused app |
| `focus` | the focused `(package, activity)` |
| `dump` | lists on-screen elements with bounds, flags, id, text and description. `--all` also shows unlabelled layout nodes; `--xml` prints the raw XML |
| `find [selector]` | prints matching elements; exits 1 if nothing matches |
| `shot [path]` | saves a screenshot (default `artifacts/screen.png`) |

In `dump`, the flag column shows `C` clickable, `S` scrollable, `x` checked and
`F` focused.

## Acting

| Command | Does |
|---|---|
| `tap X Y` | ints are pixels, values with a decimal point are fractions: `tap 0.5 0.9` |
| `longpress X Y [--ms 800]` | |
| `swipe X1 Y1 X2 Y2 [--ms 300]` | |
| `text "hello world"` | types into the focused field |
| `key home` | any keycode name |
| `tapel [selector] [--timeout 10]` | waits for a matching element, then taps it |
| `launch PACKAGE [ACTIVITY]` | |
| `notifications`, `quicksettings` | opens the shade |

Selector options for `find` and `tapel`: `--text`, `--text-contains`, `--id`,
`--desc`, `--desc-contains`, `--class`, `--package`.

```sh
uv run droidtest dump
uv run droidtest find --text-contains wi-fi
uv run droidtest tapel --id search_button
```

## Mirror and recordings

| Command | Does |
|---|---|
| `mirror [--scale N] [--interval S] [--load rec.json]` | opens the live window. See [mirror.md](mirror.md) |
| `replay rec.json [--speed 2] [--gap 0.5]` | replays a recording; `--gap` replaces the recorded pauses with a fixed pause |
| `export rec.json [--pixels] > flow.py` | prints a recording as a Python script |
