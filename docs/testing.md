# Writing tests

There are two ways to structure test runs: **pytest**, or scripts framed with
**[ucrel_sections](https://github.com/UCREL/python-sections)**. Both use the same
[`Device`](device.md) and [`UI`](ui.md) classes.

## pytest

```sh
uv run pytest -v
uv run pytest tests/test_smoke.py -k settings
```

`asyncio_mode = "auto"` is set (via pytest-asyncio), so `async def` tests just
work. [`tests/conftest.py`](../tests/conftest.py) provides two fixtures:

| Fixture | Gives you |
|---|---|
| `device` | a `Device`, already woken, with logcat cleared |
| `ui` | `device.ui()` |

If no device is connected, tests that use these fixtures are **skipped**. When a
test fails, these files are saved to `artifacts/<test id>/`:
`screen.png`, `ui.xml` and `logcat.txt`.

```python
import pytest
from droidtest import Selector

pytestmark = pytest.mark.device   # tags the test as needing a phone

async def test_login(device, ui):
    device.clear_app("com.example.app")
    device.launch("com.example.app")
    await ui.wait_package("com.example.app")
    i, _ = await ui.wait_any(Selector(text="Allow"), Selector(resource_id="email"))
    if i == 0:
        await ui.tap(text="Allow")
    await ui.set_text("user@example.com", resource_id="email")
    await ui.tap(resource_id="login_button")
    await ui.wait_for(text_contains="Welcome", timeout=15)
```

[`tests/test_recording.py`](../tests/test_recording.py) holds tests for the
recorder and touch decoding that run without a phone.

## ucrel_sections

[`sections_example.py`](../sections_example.py) is a full worked example:

```sh
uv run python sections_example.py
```

How the two libraries fit together:

- `Section`, `s.test()` and `s.timer()` are ordinary `with` blocks, so they work
  inside `async def main()`. You `await` the `UI` calls inside them.
- `UI` waits raise `ElementNotFound`, an `AssertionError`. In a
  `s.test(name, canFail=True)` block, a failure is recorded and the run moves on.
  In a plain `s.test(name)` block, the failure stops the whole section.
- `t.reject(cond, msg)` fails when `cond` is **true**. `s.tryAssert(cond, ...)`
  records the result without raising. `t.report(msg)` / `s.report(name, msg)`
  add notes to the results.
- The example's `finish(s, d)` helper counts each section's failures and saves a
  screenshot to `artifacts/sections/` if any failed. The script exits with code 1
  if anything failed.

```python
async def main():
    d = Device(); ui = d.ui()
    with Section("Settings app") as s:
        with s.timer("cold launch"):
            d.stop_app("com.android.settings"); d.open_settings()
            await ui.wait_package("com.android.settings")
        with s.test("About phone reachable", canFail=True) as t:
            el = await ui.scroll_to(text_regex=r"about (phone|device)")
            t.report(f"found at {el.center}")
    finish(s, d)

asyncio.run(main())
```

### Installing ucrel_sections

It isn't on PyPI, so `pyproject.toml` installs it from GitHub. If your global git
config rewrites `https://github.com/` to SSH, `uv sync` needs a shell where your
SSH key is available. Otherwise run:

```sh
GIT_CONFIG_GLOBAL=/dev/null uv sync --extra dev
```
