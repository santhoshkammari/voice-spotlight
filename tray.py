"""System tray icon (AppIndicator backend) — mic glyph, idle/recording state, Stop menu.

QSystemTrayIcon (XEmbed) never shows up under Ubuntu's AppIndicator GNOME
extension, which only speaks the StatusNotifierItem/AppIndicator DBus
protocol. pystray's appindicator backend talks that protocol directly.
"""

import os
import sys

sys.path.insert(0, "/usr/lib/python3/dist-packages")  # system gi, not in venv
os.environ["PYSTRAY_BACKEND"] = "appindicator"

from PIL import Image, ImageDraw
import pystray


def _mic_image(color: str, size: int = 64) -> Image.Image:
    """Bold mic glyph, legible down to ~16px tray size."""
    scale = 4  # supersample then downscale for clean edges at tiny sizes
    s = size * scale
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    body_w, body_h = s * 0.34, s * 0.46
    body_x, body_y = (s - body_w) / 2, s * 0.08
    d.rounded_rectangle(
        [body_x, body_y, body_x + body_w, body_y + body_h],
        radius=body_w / 2, fill=color,
    )

    # bracket: thick ring under the capsule, opening at the top (mic stand cage)
    ring_size = s * 0.62
    ring_x, ring_y = (s - ring_size) / 2, body_y + body_h * 0.30
    ring_w = max(3, int(s * 0.09))
    d.arc([ring_x, ring_y, ring_x + ring_size, ring_y + ring_size], 20, 160, fill=color, width=ring_w)

    stem_x = s / 2
    stem_top = ring_y + ring_size * 0.72
    stem_bot = s * 0.88
    w = max(3, int(s * 0.09))
    d.line([(stem_x, stem_top), (stem_x, stem_bot)], fill=color, width=w)
    base_w = s * 0.26
    d.line([(stem_x - base_w / 2, stem_bot), (stem_x + base_w / 2, stem_bot)], fill=color, width=w)

    return img.resize((size, size), Image.LANCZOS)


class Tray:
    def __init__(self, on_quit):
        self._on_quit = on_quit
        self._icon_idle      = _mic_image("#e6e6e6")
        self._icon_recording = _mic_image("#ff5050")

        self._status_item = pystray.MenuItem("● Listening (hold F9 to speak)", None, enabled=False)
        menu = pystray.Menu(
            self._status_item,
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Stop", self._stop),
        )
        self._icon = pystray.Icon("voice-spotlight", self._icon_idle, "Voice Spotlight — hold F9 to speak", menu)

    def _stop(self, icon, item):
        self._on_quit()
        icon.stop()

    def start(self):
        self._icon.run_detached()

    def set_recording(self, is_recording: bool):
        self._icon.icon = self._icon_recording if is_recording else self._icon_idle
        label = "● Recording..." if is_recording else "● Listening (hold F9 to speak)"
        self._status_item = pystray.MenuItem(label, None, enabled=False)
        self._icon.menu = pystray.Menu(
            self._status_item,
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Stop", self._stop),
        )

    def stop(self):
        self._icon.stop()
