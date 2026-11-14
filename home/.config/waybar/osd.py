#!/usr/bin/env python3
"""The volume popup (GTK3 + gtk-layer-shell), shown by ~/.local/bin/mac-osd.

It pops in under the bar, and its level springs to the new volume with a small bounce.
mac-osd writes "PERCENT MUTED" to the state file and sends SIGUSR1; this stays running
in the background between presses so the popup appears at once.
"""
import math
import os
import signal
import warnings
from pathlib import Path

warnings.filterwarnings("ignore", category=DeprecationWarning)

import gi  # noqa: E402
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GtkLayerShell", "0.1")
from gi.repository import Gdk, GLib, Gtk  # noqa: E402
from gi.repository import GtkLayerShell  # noqa: E402  (after Gtk 3 is loaded)

RUNTIME = Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp"))
STATE = RUNTIME / "mac-osd.state"
PIDFILE = RUNTIME / "mac-osd.pid"

WIDTH = 240
BAR_HEIGHT = 6
BUMP = 5            # px the bar thickens by for a moment on each press
SHOW_MS = 1400
ICON_FONT = "Font Awesome 7 Free Solid"
ICONS = {"volume": "", "low": "", "muted": ""}

CSS = b"""
* { font-family: "Inter", "Noto Sans", sans-serif; color: #ffffff; }
window { background-color: transparent; }
.osd {
    background-color: rgba(42, 42, 46, 0.72);
    border: 1px solid rgba(255, 255, 255, 0.18);
    border-radius: 18px;
    padding: 14px 16px;
}
.icon { font-size: 15px; min-width: 22px; }
.percent { font-size: 12px; font-weight: 600; min-width: 34px; }
"""


def rounded_bar(cr, x, y, width, height):
    radius = height / 2
    width = max(width, height)
    cr.new_sub_path()
    cr.arc(x + width - radius, y + radius, radius, -math.pi / 2, math.pi / 2)
    cr.arc(x + radius, y + radius, radius, math.pi / 2, 3 * math.pi / 2)
    cr.close_path()


class Osd:
    def __init__(self):
        self.target = 0     # the real volume
        self.shown = 0      # where the bar is drawn; it springs after target
        self.speed = 0
        self.bump = 0
        self.animating = False
        self.hide_timer = 0

        self.window = Gtk.Window()
        GtkLayerShell.init_for_window(self.window)
        GtkLayerShell.set_namespace(self.window, "osd")
        GtkLayerShell.set_layer(self.window, GtkLayerShell.Layer.OVERLAY)
        GtkLayerShell.set_anchor(self.window, GtkLayerShell.Edge.TOP, True)
        GtkLayerShell.set_anchor(self.window, GtkLayerShell.Edge.RIGHT, True)
        GtkLayerShell.set_margin(self.window, GtkLayerShell.Edge.TOP, 6)
        GtkLayerShell.set_margin(self.window, GtkLayerShell.Edge.RIGHT, 14)

        box = Gtk.Box(spacing=10)
        box.get_style_context().add_class("osd")
        box.set_size_request(WIDTH, -1)
        self.icon = Gtk.Label()
        self.icon.get_style_context().add_class("icon")
        self.bar = Gtk.DrawingArea()
        self.bar.set_size_request(-1, BAR_HEIGHT + BUMP)
        self.bar.set_valign(Gtk.Align.CENTER)
        self.bar.connect("draw", self.draw)
        self.percent = Gtk.Label(xalign=1)
        self.percent.get_style_context().add_class("percent")
        box.pack_start(self.icon, False, False, 0)
        box.pack_start(self.bar, True, True, 0)
        box.pack_start(self.percent, False, False, 0)
        self.window.add(box)
        box.show_all()
        # Clicks fall through to whatever is underneath
        self.window.connect("realize", lambda w: w.get_window().input_shape_combine_region(
            __import__("cairo").Region(), 0, 0))

    def update(self):
        try:
            percent, muted = STATE.read_text().split()
            percent, muted = int(percent), muted == "1"
        except (OSError, ValueError):
            return True
        self.target = 0 if muted else min(max(percent, 0), 100)
        name = "muted" if muted else "low" if percent < 34 else "volume"
        self.icon.set_markup(f"<span font_family='{ICON_FONT}' font_weight='900'>{ICONS[name]}</span>")
        self.percent.set_text("Muted" if muted else f"{percent}%")

        if not self.window.get_visible():
            self.window.show()
        self.bump = 1
        if not self.animating:
            self.animating = True
            self.last = None
            self.bar.add_tick_callback(self.spring)
        if self.hide_timer:
            GLib.source_remove(self.hide_timer)
        self.hide_timer = GLib.timeout_add(SHOW_MS, self.hide)
        return True

    def hide(self):
        self.hide_timer = 0
        self.window.hide()
        return False

    def spring(self, _widget, clock):
        now = clock.get_frame_time() / 1e6
        step = min(now - (self.last or now), 0.03)
        self.last = now
        self.speed += ((self.target - self.shown) * 420 - self.speed * 20) * step
        self.shown += self.speed * step
        self.bump = max(0.0, self.bump - step * 5)
        self.bar.queue_draw()
        settled = abs(self.target - self.shown) < 0.1 and abs(self.speed) < 1 and self.bump == 0
        if settled:
            self.shown, self.speed, self.animating = self.target, 0, False
        return self.animating

    def draw(self, widget, cr):
        width = widget.get_allocated_width()
        # The bump swells and shrinks back: a half sine over its short life
        height = BAR_HEIGHT + BUMP * math.sin(math.pi * self.bump)
        y = (widget.get_allocated_height() - height) / 2
        rounded_bar(cr, 0, y, width, height)
        cr.set_source_rgba(1, 1, 1, 0.22)
        cr.fill()
        level = min(max(self.shown, 0), 103) / 100
        if level > 0.005:
            rounded_bar(cr, 0, y, min(width * level, width), height)
            cr.set_source_rgba(1, 1, 1, 0.95)
            cr.fill()
        return True


def main():
    provider = Gtk.CssProvider()
    provider.load_from_data(CSS)
    Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), provider,
                                             Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
    osd = Osd()
    PIDFILE.write_text(str(os.getpid()))
    GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGUSR1, osd.update)
    osd.update()
    try:
        Gtk.main()
    finally:
        PIDFILE.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
