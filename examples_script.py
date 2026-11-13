"""Ad-hoc scripting example (not a pytest test). Run: uv run python examples_script.py"""

import asyncio
import time

from droidtest import Device, Region

d = Device()
ui = d.ui()
print(d, d.model, d.screen_size(), d.current_focus())

d.home()
d.open_quick_settings()
time.sleep(1)
for line in asyncio.run(ui.describe()):
    print(line)
d.screenshot("artifacts/quick_settings.png")
d.collapse_status_bar()

# Tap an explicit region (absolute pixels) or a fractional point:
d.tap_region(Region(0, 0, 200, 200))
# d.tap(0.5, 0.5)
d.home()
