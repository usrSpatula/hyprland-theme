#!/usr/bin/env python3
# Control Center: the glass panel that drops down when you click the clock.
# Shows the time and this month's calendar, quick toggles (Wi-Fi, Bluetooth,
# Do Not Disturb, Night Shift), brightness and volume sliders, and what's playing.
# Clicking the clock again, clicking anywhere else, or pressing Esc closes it.
import calendar
import datetime
import math
import re
import subprocess
import sys
import threading
from urllib.parse import unquote, urlparse

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GtkLayerShell", "0.1")
from gi.repository import Gdk, GdkPixbuf, Gio, GLib, Gtk, Pango  # noqa: E402
from gi.repository import GtkLayerShell  # noqa: E402  (after Gtk 3 is loaded)

PANEL_WIDTH = 330
BAR_HEIGHT = 28
EDGE_GAP = 8
SHADOW_ROOM = 34
OPEN_DURATION = 240
CLOSE_DURATION = 160
ART_SIZE = 44
SLIDER_HEIGHT = 22
SCROLL_STEP = 5
NIGHT_TEMPERATURE = 4500
REFRESH_SECONDS = 2
BLUE = "#0a84ff"

ICON_FONT = "Font Awesome 7 Free Solid"
BRAND_FONT = "Font Awesome 7 Brands"
ICONS = {
    "wifi": "",
    "bluetooth": "",
    "moon": "",
    "sun": "",
    "night": "",
    "volume": "",
    "muted": "",
    "previous": "",
    "play": "",
    "pause": "",
    "next": "",
    "music": "",
    "left": "",
    "right": "",
}

CSS = b"""
* { font-family: "Inter", "Noto Sans", sans-serif; color: #ffffff; }
window { background-color: transparent; }
.panel {
    background-color: rgba(30, 30, 34, 0.62);
    border: 1px solid rgba(255, 255, 255, 0.16);
    border-radius: 20px;
    box-shadow: 0 12px 30px rgba(0, 0, 0, 0.40), 0 1px 4px rgba(0, 0, 0, 0.30);
    padding: 10px;
}
.tile {
    background-color: rgba(255, 255, 255, 0.07);
    border: 1px solid rgba(255, 255, 255, 0.06);
    border-radius: 14px;
    padding: 10px;
}
.toggle { transition: background-color 140ms ease-out; }
.toggle.hover { background-color: rgba(255, 255, 255, 0.12); }

.time { font-family: "Inter Display"; font-size: 40px; font-weight: 300; }
.ampm { font-family: "Inter Display"; font-size: 17px; font-weight: 400;
        color: rgba(255, 255, 255, 0.6); }
.date { font-size: 13px; font-weight: 500; color: rgba(255, 255, 255, 0.7); }

.month { font-size: 12px; font-weight: 700; }
.weekday { font-size: 10px; font-weight: 600; color: rgba(255, 255, 255, 0.45); }
.day {
    font-size: 11px;
    font-weight: 500;
    min-width: 30px;
    min-height: 24px;
    border-radius: 12px;
}
.day.other { color: rgba(255, 255, 255, 0.25); }
.day.weekend { color: rgba(255, 255, 255, 0.6); }
.day.today { background-color: #0a84ff; color: #ffffff; font-weight: 700; }

button.flat, button.circle, button.media {
    background-image: none;
    border: none;
    box-shadow: none;
    text-shadow: none;
    outline-width: 0;
    padding: 0;
}
button.flat {
    background-color: transparent;
    min-width: 22px;
    min-height: 22px;
    border-radius: 11px;
    transition: background-color 140ms ease-out;
}
button.flat:hover { background-color: rgba(255, 255, 255, 0.12); }
.arrow { font-size: 9px; color: rgba(255, 255, 255, 0.7); }

.circle {
    background-color: rgba(255, 255, 255, 0.14);
    min-width: 30px;
    min-height: 30px;
    border-radius: 15px;
    font-size: 13px;
    transition: background-color 160ms ease-out;
}
button.circle:hover { background-color: rgba(255, 255, 255, 0.22); }
.circle.on, button.circle.on:hover { background-color: #0a84ff; }
.tile-title { font-size: 12px; font-weight: 600; }
.tile-sub { font-size: 10px; color: rgba(255, 255, 255, 0.55); }

.section { font-size: 12px; font-weight: 600; }
.slider-icon { font-size: 11px; color: rgba(255, 255, 255, 0.75); min-width: 22px; }

.song { font-size: 12px; font-weight: 600; }
.artist { font-size: 11px; color: rgba(255, 255, 255, 0.6); }
button.media {
    background-color: transparent;
    min-width: 28px;
    min-height: 28px;
    border-radius: 14px;
    font-size: 13px;
    transition: background-color 140ms ease-out;
}
button.media:hover { background-color: rgba(255, 255, 255, 0.14); }
"""


def run(*command):
    try:
        return subprocess.run(command, capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def spawn(*command):
    subprocess.Popen(command, start_new_session=True,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def icon_label(name, css_class=None, font=ICON_FONT):
    label = Gtk.Label()
    weight = 400 if font == BRAND_FONT else 900
    label.set_markup(f"<span font_family='{font}' font_weight='{weight}'>{ICONS[name]}</span>")
    if css_class:
        label.get_style_context().add_class(css_class)
    return label


def css_classes(widget, *names):
    context = widget.get_style_context()
    for name in names:
        context.add_class(name)
    return widget


def set_class(widget, name, enabled):
    context = widget.get_style_context()
    context.add_class(name) if enabled else context.remove_class(name)


# ---- Quick toggles: each reads its state and returns (on, subtitle) ----

def wifi_state():
    on = run("nmcli", "radio", "wifi").strip() == "enabled"
    for line in run("nmcli", "-t", "-f", "TYPE,STATE,CONNECTION", "device").splitlines():
        kind, state, connection = (line.split(":", 2) + ["", ""])[:3]
        if kind == "wifi" and state == "connected":
            return on, connection
    return on, "Not connected" if on else "Off"


def set_wifi(on):
    run("nmcli", "radio", "wifi", "on" if on else "off")


def bluetooth_state():
    on = "Powered: yes" in run("bluetoothctl", "show")
    names = [line.split(" ", 2)[2] for line in run("bluetoothctl", "devices", "Connected").splitlines()
             if line.startswith("Device ") and line.count(" ") >= 2]
    if not on:
        return False, "Off"
    if names:
        return True, names[0] + (f" +{len(names) - 1}" if len(names) > 1 else "")
    return True, "On"


def set_bluetooth(on):
    run("bluetoothctl", "power", "on" if on else "off")


def dnd_state():
    on = "do-not-disturb" in run("makoctl", "mode").split()
    return on, "On" if on else "Off"


def set_dnd(on):
    run("makoctl", "mode", "-a" if on else "-r", "do-not-disturb")


def night_state():
    on = bool(run("pgrep", "-x", "hyprsunset").strip())
    return on, "Warm screen" if on else "Off"


def set_night(on):
    if on:
        spawn("hyprsunset", "-t", str(NIGHT_TEMPERATURE))
    else:
        run("pkill", "-x", "hyprsunset")


class Toggle:
    """A round icon that turns blue when on. Flipping it runs off the main thread,
    since nmcli and bluetoothctl can take a moment, then re-reads the real state."""

    def __init__(self, button, icon_holder, subtitle, read, write):
        self.button, self.icon_holder, self.subtitle = button, icon_holder, subtitle
        self.read, self.write = read, write
        self.on = False
        self.flipping = False

    def show(self, on, text):
        self.on = on
        set_class(self.icon_holder, "on", on)
        self.subtitle.set_text(text)

    def refresh(self):
        # Skipped mid-flip, so a slow read can't briefly show the old state
        if self.flipping:
            return

        def work():
            on, text = self.read()
            GLib.idle_add(self.show, on, text)
        threading.Thread(target=work, daemon=True).start()

    def flip(self, *_):
        target = not self.on
        set_class(self.icon_holder, "on", target)
        self.on = target
        self.flipping = True

        def done(on, text):
            self.flipping = False
            self.show(on, text)

        def work():
            self.write(target)
            on, text = self.read()
            GLib.idle_add(done, on, text)
        threading.Thread(target=work, daemon=True).start()


# ---- Brightness and volume ----

class Brightness:
    """Sets both monitors over DDC/CI. Each ddcutil call takes about 0.3s, so while
    you drag, a worker applies the newest slider value and skips the ones in between."""

    def __init__(self, on_read):
        self.buses = []
        self.target = None
        self.busy = False
        self.lock = threading.Lock()
        threading.Thread(target=self.read, args=(on_read,), daemon=True).start()

    def read(self, on_read):
        self.buses = re.findall(r"I2C bus:\s+/dev/i2c-(\d+)", run("ddcutil", "detect", "--brief"))
        if not self.buses:
            return
        parts = run("ddcutil", "--bus", self.buses[0], "getvcp", "10", "--brief").split()
        if len(parts) >= 5 and parts[3].isdigit() and parts[4].isdigit():
            GLib.idle_add(on_read, 100 * int(parts[3]) / max(int(parts[4]), 1))

    def set(self, percent):
        with self.lock:
            self.target = round(percent)
            if self.busy:
                return
            self.busy = True
        # Not a daemon thread, so closing the panel mid-drag still applies the last value
        threading.Thread(target=self.apply).start()

    def apply(self):
        while True:
            with self.lock:
                value = self.target
            processes = [
                subprocess.Popen(["ddcutil", "--bus", bus, "--noverify", "setvcp", "10", str(value)],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                for bus in self.buses
            ]
            for process in processes:
                process.wait()
            with self.lock:
                if self.target == value:
                    self.busy = False
                    return


def read_volume():
    text = run("wpctl", "get-volume", "@DEFAULT_AUDIO_SINK@")
    match = re.search(r"([\d.]+)", text)
    return (float(match.group(1)) * 100 if match else 0), "MUTED" in text


def rounded_rect(cr, x, y, width, height, radius):
    cr.new_sub_path()
    cr.arc(x + width - radius, y + radius, radius, -math.pi / 2, 0)
    cr.arc(x + width - radius, y + height - radius, radius, 0, math.pi / 2)
    cr.arc(x + radius, y + height - radius, radius, math.pi / 2, math.pi)
    cr.arc(x + radius, y + radius, radius, math.pi, 3 * math.pi / 2)
    cr.close_path()


class Slider(Gtk.DrawingArea):
    """A macOS-style slider, drawn by hand so the knob always sits inside the track:
    a rounded track that fills white up to a round knob of the same height."""

    def __init__(self, on_change):
        super().__init__()
        self.value = 0
        self.enabled = True
        self.on_change = on_change
        self.set_size_request(-1, SLIDER_HEIGHT)
        self.set_hexpand(True)
        self.set_valign(Gtk.Align.CENTER)
        self.add_events(Gdk.EventMask.BUTTON_PRESS_MASK | Gdk.EventMask.BUTTON_RELEASE_MASK
                        | Gdk.EventMask.BUTTON1_MOTION_MASK | Gdk.EventMask.SCROLL_MASK
                        | Gdk.EventMask.SMOOTH_SCROLL_MASK)
        self.connect("draw", self.draw)
        self.connect("button-press-event", self.on_drag)
        self.connect("motion-notify-event", self.on_drag)
        self.connect("scroll-event", self.on_scroll)

    def set_value(self, value):
        self.value = min(max(value, 0), 100)
        self.queue_draw()

    def set_enabled(self, enabled):
        self.enabled = enabled
        self.queue_draw()

    def change(self, value):
        value = round(min(max(value, 0), 100))
        if value != round(self.value):
            self.set_value(value)
            self.on_change(value)

    def on_drag(self, _widget, event):
        if self.enabled:
            travel = self.get_allocated_width() - SLIDER_HEIGHT
            self.change(100 * (event.x - SLIDER_HEIGHT / 2) / max(travel, 1))
        return True

    def on_scroll(self, _widget, event):
        if not self.enabled:
            return True
        has_deltas, _, delta_y = event.get_scroll_deltas()
        if not has_deltas:
            _, direction = event.get_scroll_direction()
            delta_y = -1 if direction == Gdk.ScrollDirection.UP else 1
        self.change(self.value - delta_y * SCROLL_STEP)
        return True

    def draw(self, _widget, cr):
        width = self.get_allocated_width()
        height = SLIDER_HEIGHT
        radius = height / 2
        knob_x = radius + (width - height) * self.value / 100

        rounded_rect(cr, 0, 0, width, height, radius)
        cr.set_source_rgba(1, 1, 1, 0.14 if self.enabled else 0.07)
        cr.fill()

        rounded_rect(cr, 0, 0, knob_x + radius, height, radius)
        cr.set_source_rgba(1, 1, 1, 1 if self.enabled else 0.35)
        cr.fill()

        # The knob: a soft shadow ring so it reads against the white fill
        cr.arc(knob_x, radius + 0.5, radius, 0, 2 * math.pi)
        cr.set_source_rgba(0, 0, 0, 0.22 if self.enabled else 0)
        cr.fill()
        cr.arc(knob_x, radius, radius - 1, 0, 2 * math.pi)
        cr.set_source_rgba(1, 1, 1, 1 if self.enabled else 0.35)
        cr.fill()
        return True


def build_slider(icon_widget, on_change):
    slider = Slider(on_change)
    row = Gtk.Box(spacing=8)
    icon_widget.set_valign(Gtk.Align.CENTER)
    row.pack_start(icon_widget, False, False, 0)
    row.pack_start(slider, True, True, 0)
    return row, slider


def slider_tile(title, row):
    tile = css_classes(Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8), "tile")
    tile.pack_start(css_classes(Gtk.Label(label=title, xalign=0), "section"), False, False, 0)
    tile.pack_start(row, False, False, 0)
    return tile


# ---- Clock and calendar ----

def build_clock():
    tile = css_classes(Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2), "tile")
    line = Gtk.Box(spacing=6)
    time = css_classes(Gtk.Label(xalign=0), "time")
    ampm = css_classes(Gtk.Label(xalign=0), "ampm")
    ampm.set_valign(Gtk.Align.BASELINE)
    time.set_valign(Gtk.Align.BASELINE)
    line.pack_start(time, False, False, 0)
    line.pack_start(ampm, False, False, 0)
    date = css_classes(Gtk.Label(xalign=0), "date")
    tile.pack_start(line, False, False, 0)
    tile.pack_start(date, False, False, 0)

    def update():
        now = datetime.datetime.now()
        time.set_text(now.strftime("%-I:%M"))
        ampm.set_text(now.strftime("%p"))
        date.set_text(now.strftime("%A, %-d %B %Y"))
        return True

    update()
    return tile, update


def build_calendar():
    tile = css_classes(Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6), "tile")
    header = Gtk.Box()
    month = css_classes(Gtk.Label(xalign=0), "month")
    header.pack_start(month, True, True, 0)
    buttons = {}
    for name in ("left", "right"):
        button = css_classes(Gtk.Button(), "flat")
        button.add(icon_label(name, "arrow"))
        buttons[name] = button
        header.pack_start(button, False, False, 0)
    tile.pack_start(header, False, False, 0)

    grid = Gtk.Grid(column_homogeneous=True, row_spacing=1)
    tile.pack_start(grid, False, False, 0)
    weeks = calendar.Calendar(firstweekday=calendar.SUNDAY)
    state = {"year": datetime.date.today().year, "month": datetime.date.today().month}

    def draw():
        for child in grid.get_children():
            grid.remove(child)
        month.set_text(datetime.date(state["year"], state["month"], 1).strftime("%B %Y"))
        for column, name in enumerate("SMTWTFS"):
            grid.attach(css_classes(Gtk.Label(label=name), "weekday"), column, 0, 1, 1)
        today = datetime.date.today()
        days = list(weeks.itermonthdates(state["year"], state["month"]))
        # Always six rows, so the panel doesn't change height between months
        while len(days) < 42:
            days.append(days[-1] + datetime.timedelta(days=1))
        for index, day in enumerate(days):
            label = css_classes(Gtk.Label(label=str(day.day)), "day")
            label.set_halign(Gtk.Align.CENTER)
            if day.month != state["month"]:
                css_classes(label, "other")
            elif day == today:
                css_classes(label, "today")
            elif index % 7 in (0, 6):
                css_classes(label, "weekend")
            grid.attach(label, index % 7, 1 + index // 7, 1, 1)
        grid.show_all()

    def move(step):
        total = state["year"] * 12 + state["month"] - 1 + step
        state["year"], state["month"] = divmod(total, 12)
        state["month"] += 1
        draw()

    buttons["left"].connect("clicked", lambda *_: move(-1))
    buttons["right"].connect("clicked", lambda *_: move(1))
    draw()
    return tile


# ---- Toggle tiles ----

def toggle_tile(icon, title, read, write, on_tile_click=None, font=ICON_FONT):
    """One of the four quick toggles. They all share this layout, so the icons and
    text line up: a round icon that flips the setting, beside a title and status.
    Clicking the rest of the tile flips it too, or runs on_tile_click if given."""
    tile = css_classes(Gtk.Box(spacing=9), "tile", "toggle")
    circle = css_classes(Gtk.Button(), "circle")
    circle.add(icon_label(icon, font=font))
    circle.set_valign(Gtk.Align.CENTER)
    tile.pack_start(circle, False, False, 0)

    text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    text.set_valign(Gtk.Align.CENTER)
    text.pack_start(css_classes(Gtk.Label(label=title, xalign=0), "tile-title"), False, False, 0)
    subtitle = css_classes(Gtk.Label(xalign=0), "tile-sub")
    subtitle.set_ellipsize(Pango.EllipsizeMode.END)
    subtitle.set_max_width_chars(12)
    text.pack_start(subtitle, False, False, 0)
    tile.pack_start(text, True, True, 0)

    area = Gtk.EventBox()
    area.set_visible_window(False)
    area.add_events(Gdk.EventMask.BUTTON_RELEASE_MASK | Gdk.EventMask.ENTER_NOTIFY_MASK
                    | Gdk.EventMask.LEAVE_NOTIFY_MASK)
    area.add(tile)
    toggle = Toggle(area, circle, subtitle, read, write)
    circle.connect("clicked", toggle.flip)

    def on_click(*_):
        (on_tile_click or toggle.flip)()
        return True

    def on_hover(_area, event, inside):
        # Moving onto the round button inside still counts as hovering the tile
        if event.detail != Gdk.NotifyType.INFERIOR:
            set_class(tile, "hover", inside)

    area.connect("button-release-event", on_click)
    area.connect("enter-notify-event", on_hover, True)
    area.connect("leave-notify-event", on_hover, False)
    return area, toggle


# ---- Now Playing ----

def build_media():
    tile = css_classes(Gtk.Box(spacing=10), "tile")
    art = Gtk.DrawingArea()
    art.set_size_request(ART_SIZE, ART_SIZE)
    art.set_valign(Gtk.Align.CENTER)
    tile.pack_start(art, False, False, 0)

    text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    text.set_valign(Gtk.Align.CENTER)
    song = css_classes(Gtk.Label(xalign=0), "song")
    artist = css_classes(Gtk.Label(xalign=0), "artist")
    for label in (song, artist):
        label.set_ellipsize(Pango.EllipsizeMode.END)
        label.set_max_width_chars(16)
        text.pack_start(label, False, False, 0)
    tile.pack_start(text, True, True, 0)

    play = None
    for name, command in (("previous", "previous"), ("play", "play-pause"), ("next", "next")):
        button = css_classes(Gtk.Button(), "media")
        button.add(icon_label(name))
        button.set_valign(Gtk.Align.CENTER)
        button.connect("clicked", lambda _b, c=command: (run("playerctl", c), update()))
        tile.pack_start(button, False, False, 0)
        if name == "play":
            play = button

    state = {"art_url": None, "pixbuf": None}

    def draw_art(_area, cr):
        radius = 8
        cr.new_sub_path()
        for x, y, start in ((ART_SIZE - radius, radius, -90), (ART_SIZE - radius, ART_SIZE - radius, 0),
                            (radius, ART_SIZE - radius, 90), (radius, radius, 180)):
            cr.arc(x, y, radius, start * 3.14159 / 180, (start + 90) * 3.14159 / 180)
        cr.close_path()
        cr.clip()
        if state["pixbuf"]:
            Gdk.cairo_set_source_pixbuf(cr, state["pixbuf"], 0, 0)
            cr.paint()
            return
        cr.set_source_rgba(1, 1, 1, 0.12)
        cr.paint()
        cr.set_source_rgba(1, 1, 1, 0.6)
        cr.select_font_face(ICON_FONT)
        cr.set_font_size(17)
        extents = cr.text_extents(ICONS["music"])
        cr.move_to((ART_SIZE - extents.width) / 2 - extents.x_bearing,
                   (ART_SIZE - extents.height) / 2 - extents.y_bearing)
        cr.show_text(ICONS["music"])

    art.connect("draw", draw_art)

    def load_art(url):
        # Only local cover files (what Firefox and most players hand out); no downloads
        state["pixbuf"] = None
        if url.startswith("file://"):
            try:
                pixbuf = GdkPixbuf.Pixbuf.new_from_file(unquote(urlparse(url).path))
                side = min(pixbuf.get_width(), pixbuf.get_height())
                square = pixbuf.new_subpixbuf((pixbuf.get_width() - side) // 2,
                                              (pixbuf.get_height() - side) // 2, side, side)
                state["pixbuf"] = square.scale_simple(ART_SIZE, ART_SIZE, GdkPixbuf.InterpType.BILINEAR)
            except GLib.Error:
                pass
        art.queue_draw()

    def update():
        fields = run("playerctl", "metadata", "--format",
                     "{{status}}\t{{title}}\t{{artist}}\t{{mpris:artUrl}}").rstrip("\n").split("\t")
        if len(fields) < 4 or not fields[1]:
            tile.hide()
            return True
        status, title, who, art_url = fields[:4]
        song.set_text(title)
        artist.set_text(who or "Unknown artist")
        play.get_child().set_markup(
            f"<span font_family='{ICON_FONT}' font_weight='900'>"
            f"{ICONS['pause' if status == 'Playing' else 'play']}</span>")
        if art_url != state["art_url"]:
            state["art_url"] = art_url
            load_art(art_url)
        tile.show_all()
        return True

    tile.set_no_show_all(True)
    return tile, update


# ---- The panel ----

def build_panel(close):
    panel = css_classes(Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8), "panel")
    panel.set_size_request(PANEL_WIDTH, -1)

    clock, update_clock = build_clock()
    panel.pack_start(clock, False, False, 0)

    def opener(app):
        def open_settings():
            spawn(app)
            close()
        return open_settings

    # Four equal tiles in a 2x2 grid. Wi-Fi and Bluetooth open their settings when
    # you click the tile itself; the round icon still flips them.
    toggles = []
    grid = Gtk.Grid(row_spacing=8, column_spacing=8, row_homogeneous=True, column_homogeneous=True)
    for index, (icon, title, read, write, on_tile_click, font) in enumerate((
            ("wifi", "Wi-Fi", wifi_state, set_wifi, opener("nm-connection-editor"), ICON_FONT),
            ("moon", "Do Not Disturb", dnd_state, set_dnd, None, ICON_FONT),
            ("bluetooth", "Bluetooth", bluetooth_state, set_bluetooth, opener("blueman-manager"), BRAND_FONT),
            ("night", "Night Shift", night_state, set_night, None, ICON_FONT))):
        tile, toggle = toggle_tile(icon, title, read, write, on_tile_click, font)
        grid.attach(tile, index % 2, index // 2, 1, 1)
        toggles.append(toggle)
    panel.pack_start(grid, False, False, 0)

    # Display: both monitors move together
    monitors = None
    row, brightness_slider = build_slider(icon_label("sun", "slider-icon"),
                                          lambda value: monitors.set(value))
    brightness_slider.set_enabled(False)
    panel.pack_start(slider_tile("Display", row), False, False, 0)

    def on_brightness_read(percent):
        brightness_slider.set_value(percent)
        brightness_slider.set_enabled(True)

    monitors = Brightness(on_brightness_read)

    # Sound: the speaker mutes, the slider sets the level (and unmutes)
    speaker = css_classes(Gtk.Button(), "flat")
    speaker_icon = icon_label("volume", "slider-icon")
    speaker.add(speaker_icon)

    def show_speaker(muted):
        speaker_icon.set_markup(f"<span font_family='{ICON_FONT}' font_weight='900'>"
                                f"{ICONS['muted' if muted else 'volume']}</span>")

    def on_volume(value):
        run("wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", f"{value / 100:.2f}")
        run("wpctl", "set-mute", "@DEFAULT_AUDIO_SINK@", "0")
        show_speaker(False)

    def show_volume():
        level, muted = read_volume()
        volume_slider.set_value(level)
        show_speaker(muted)

    row, volume_slider = build_slider(speaker, on_volume)
    panel.pack_start(slider_tile("Sound", row), False, False, 0)
    speaker.connect("clicked", lambda *_: (run("wpctl", "set-mute", "@DEFAULT_AUDIO_SINK@", "toggle"),
                                           show_volume()))
    show_volume()

    media, update_media = build_media()
    panel.pack_start(media, False, False, 0)
    panel.pack_start(build_calendar(), False, False, 0)

    def refresh_toggles():
        for toggle in toggles:
            toggle.refresh()
        return True

    def tick():
        update_clock()
        update_media()
        return True

    refresh_toggles()
    tick()
    GLib.timeout_add_seconds(1, tick)
    GLib.timeout_add_seconds(REFRESH_SECONDS, refresh_toggles)
    return panel


def open_window(app):
    provider = Gtk.CssProvider()
    provider.load_from_data(CSS)
    Gtk.StyleContext.add_provider_for_screen(
        Gdk.Screen.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

    window = Gtk.ApplicationWindow(application=app)
    window.set_visual(window.get_screen().get_rgba_visual())
    window.set_app_paintable(True)

    # A transparent full-screen layer, so a click anywhere else closes the panel
    GtkLayerShell.init_for_window(window)
    GtkLayerShell.set_namespace(window, "control-center")
    GtkLayerShell.set_layer(window, GtkLayerShell.Layer.OVERLAY)
    GtkLayerShell.set_exclusive_zone(window, -1)
    GtkLayerShell.set_keyboard_mode(window, GtkLayerShell.KeyboardMode.EXCLUSIVE)
    for edge in (GtkLayerShell.Edge.TOP, GtkLayerShell.Edge.BOTTOM,
                 GtkLayerShell.Edge.LEFT, GtkLayerShell.Edge.RIGHT):
        GtkLayerShell.set_anchor(window, edge, True)

    state = {"closing": False}
    revealer = Gtk.Revealer()

    def close(*_):
        if state["closing"]:
            return
        state["closing"] = True
        revealer.set_transition_duration(CLOSE_DURATION)
        revealer.set_reveal_child(False)
        GLib.timeout_add(CLOSE_DURATION + 30, window.destroy)

    panel_area = Gtk.EventBox()
    panel_area.set_visible_window(False)
    panel_area.add(build_panel(close))
    panel_area.connect("button-press-event", lambda *_: True)
    panel_area.set_margin_start(SHADOW_ROOM)
    panel_area.set_margin_end(EDGE_GAP)
    panel_area.set_margin_top(6)
    panel_area.set_margin_bottom(SHADOW_ROOM + 10)

    revealer.set_transition_type(Gtk.RevealerTransitionType.SLIDE_DOWN)
    revealer.set_transition_duration(OPEN_DURATION)
    revealer.set_halign(Gtk.Align.END)
    revealer.set_valign(Gtk.Align.START)
    revealer.set_margin_top(BAR_HEIGHT)
    revealer.add(panel_area)

    window.add(revealer)
    window.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
    window.connect("button-press-event", close)
    window.connect("key-press-event", lambda _w, e: e.keyval == Gdk.KEY_Escape and close())
    window.connect("map-event", lambda *_: revealer.set_reveal_child(True))
    window.show_all()
    return close


def main():
    # One copy at a time: clicking the clock while it's open closes it
    app = Gtk.Application(application_id="local.waybar.ControlCenter",
                          flags=Gio.ApplicationFlags.FLAGS_NONE)
    state = {"close": None}

    def activate(application):
        if state["close"]:
            state["close"]()
        else:
            state["close"] = open_window(application)

    app.connect("activate", activate)
    app.run([sys.argv[0]])


if __name__ == "__main__":
    main()
