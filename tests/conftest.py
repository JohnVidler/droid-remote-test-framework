"""Shared pytest fixtures.

Every test gets a fresh `device` / `ui` and, on failure, a screenshot + UI dump +
logcat are saved under artifacts/<test-name>/ for post-mortem debugging.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

from droidtest import Device

ARTIFACTS = Path(__file__).resolve().parent.parent / "artifacts"


@pytest.fixture(scope="session")
def _device_session() -> Device:
    try:
        return Device(os.environ.get("ANDROID_SERIAL"))
    except Exception as exc:  # no device -> skip device tests rather than error
        pytest.skip(f"No usable Android device: {exc}")


@pytest.fixture
def device(_device_session: Device, request):
    d = _device_session
    d.wake()
    d.logcat_clear()
    yield d
    rep = getattr(request.node, "rep_call", None)
    if rep is not None and rep.failed:
        out = ARTIFACTS / re.sub(r"[^\w.-]+", "_", request.node.nodeid)
        out.mkdir(parents=True, exist_ok=True)
        for name, fn in (
            ("screen.png", lambda: d.screenshot(out / "screen.png")),
            ("ui.xml", lambda: (out / "ui.xml").write_text(d.ui()._dump_sync(retries=2))),
            ("logcat.txt", lambda: d.logcat_dump(out / "logcat.txt")),
        ):
            try:
                fn()
            except Exception as exc:  # never mask the real failure
                (out / f"{name}.error").write_text(str(exc))


@pytest.fixture
def ui(device):
    return device.ui()


@pytest.hookimpl(hookwrapper=True, tryfirst=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    rep = outcome.get_result()
    setattr(item, f"rep_{rep.when}", rep)
