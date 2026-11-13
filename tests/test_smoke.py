"""Example tests. These exercise system UI only, so they run on any phone.

UI calls are coroutines; pytest-asyncio (asyncio_mode=auto) runs `async def` tests.
Run with:  uv run pytest -v
"""

import asyncio

import pytest

from droidtest import Selector

pytestmark = pytest.mark.device

SETTINGS_PKG = "com.android.settings"
SYSTEMUI_PKG = "com.android.systemui"


def test_device_info(device):
    w, h = device.screen_size()
    assert w > 0 and h > 0
    assert device.sdk >= 21


async def test_home_screen(device, ui):
    device.home()
    await ui.wait_idle()
    assert device.current_package() is not None


async def test_open_settings_and_scroll(device, ui):
    device.stop_app(SETTINGS_PKG)
    device.open_settings()
    await ui.wait_package(SETTINGS_PKG)
    await ui.wait_for(package=SETTINGS_PKG)
    # Wording varies by OEM, hence the regex.
    el = await ui.scroll_to(text_regex=r"about (phone|device)", max_swipes=8)
    assert el.bounds.height > 0
    device.home()


async def test_notification_shade(device, ui):
    """Cross-app / system interaction: pull down the shade and close it again."""
    device.home()
    await ui.wait_idle()
    device.open_notifications()
    await ui.wait_for(package=SYSTEMUI_PKG, timeout=5)
    device.collapse_status_bar()
    await ui.wait_gone(package=SYSTEMUI_PKG, clickable=True, timeout=5)


async def test_swipe_changes_screen(device, ui):
    """Raw gesture with fractional coordinates, then await the resulting change."""
    device.home()
    await ui.wait_idle()
    before = await ui.snapshot()
    device.swipe(0.5, 0.8, 0.5, 0.3)          # opens the app drawer on most launchers
    changed = await ui.wait_change(before, timeout=5)
    assert changed
    device.home()


async def test_wait_any_and_timeouts(device, ui):
    """Race several outcomes, and bound any wait with asyncio.wait_for."""
    device.home()
    i, el = await ui.wait_any(
        Selector(text="Phone"),            # Samsung hotseat
        Selector(desc_contains="phone"),   # other launchers
        Selector(package=device.current_package()),
        timeout=5,
    )
    assert el is not None
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(ui.wait_for(text="This text does not exist"), timeout=1.5)
