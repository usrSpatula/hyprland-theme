#!/usr/bin/env python3
# A short white flash over the area that was just captured (see screenshot.sh).
#   screenshot-flash.py "x,y WxH"     the region, as slurp prints it
import re
import sys

import cairo
import gi

gi.require_version("Gdk", "3.0")
gi.require_version("Gtk", "3.0")
gi.require_version("GtkLayerShell", "0.1")
from gi.repository import Gdk, Gtk, GtkLayerShell  # noqa: E402

FLASH_MS = 260
PEAK = 0.4   # how white the flash starts

x, y, width, height = map(int, re.match(r"(-?\d+),(-?\d+) (\d+)x(\d+)", sys.argv[1]).groups())

# The monitor under the middle of the region
display = Gdk.Display.get_default()
monitor = display.get_monitor_at_point(x + width // 2, y + height // 2)
area = monitor.get_geometry()

window = Gtk.Window()
window.set_visual(window.get_screen().get_rgba_visual())
window.set_app_paintable(True)
window.set_size_request(width, height)

GtkLayerShell.init_for_window(window)
GtkLayerShell.set_namespace(window, "screenshot-flash")
GtkLayerShell.set_layer(window, GtkLayerShell.Layer.OVERLAY)
GtkLayerShell.set_monitor(window, monitor)
GtkLayerShell.set_exclusive_zone(window, -1)
GtkLayerShell.set_anchor(window, GtkLayerShell.Edge.LEFT, True)
GtkLayerShell.set_anchor(window, GtkLayerShell.Edge.TOP, True)
GtkLayerShell.set_margin(window, GtkLayerShell.Edge.LEFT, x - area.x)
GtkLayerShell.set_margin(window, GtkLayerShell.Edge.TOP, y - area.y)

state = {"began": None, "alpha": PEAK}


def draw(_window, cr):
    cr.set_operator(cairo.OPERATOR_SOURCE)
    cr.set_source_rgba(1, 1, 1, state["alpha"])
    cr.paint()


def tick(_window, clock):
    now = clock.get_frame_time() / 1000
    if state["began"] is None:
        state["began"] = now
    t = (now - state["began"]) / FLASH_MS
    if t >= 1:
        Gtk.main_quit()
        return False
    state["alpha"] = PEAK * (1 - t) ** 2
    window.queue_draw()
    return True


window.connect("draw", draw)
# Clicks pass straight through
window.connect("realize", lambda *_: window.input_shape_combine_region(cairo.Region()))
window.add_tick_callback(tick)
window.show_all()
Gtk.main()
