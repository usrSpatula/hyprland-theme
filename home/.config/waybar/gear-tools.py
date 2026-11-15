#!/usr/bin/env python3
# Gear tools: four small glass panels in the gear drawer, with the same look as the clips panel.
#   gear-tools.py --clipboard     The last things you copied (text and pictures). Click one to
#                                 copy it again, pin the ones you reuse, search, or clear it all.
#                                 History comes from cliphist, which Hyprland starts at login.
#   gear-tools.py --screenshots   Your screenshots, newest first. Click to open, copy or trash one,
#                                 or take a new one.
#   gear-tools.py --notes         A scratchpad that saves as you type.
#   gear-tools.py --wifi          Nearby Wi-Fi networks, strongest first. Click one to connect; it
#                                 asks for the password only if it needs one.
#   gear-tools.py --bluetooth     Bluetooth devices nearby. Click one to pair and connect it.
#   gear-tools.py --sound         The master volume, a volume slider for every app that's playing
#                                 sound, and which speakers and microphone to use.
#   gear-tools.py --power         Lock, sleep, log out, restart and shut down. The last three
#                                 need a second click.
import datetime
import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = Path.home() / ".local" / "share" / "gear-tools"
CACHE = Path.home() / ".cache" / "gear-tools"
PINS = DATA / "pins"
NOTES = DATA / "notes.txt"
SCREENSHOTS = Path.home() / "Pictures" / "screenshot"   # Where ~/.config/hypr/screenshot.sh saves
SCREENSHOT_SCRIPT = Path.home() / ".config" / "hypr" / "screenshot.sh"

SOUND_WIDTH = 270         # the sound panel is kept small
SOUND_APPS_HEIGHT = 170   # its list of apps scrolls past this height
CLIPBOARD_LIMIT = 60
LIST_HEIGHT = 420
SHOT_WIDTH = 124
SHOT_HEIGHT = 70
SHOT_COLUMNS = 3
CLIP_THUMB = 44
NOTES_HEIGHT = 300
SAVE_DELAY = 400   # ms after you stop typing before the note is saved
ARM_SECONDS = 3    # How long a "click again" button waits for the second click

ICONS = {
    "clipboard": "", "images": "", "note": "", "power": "",
    "lock": "", "moon": "", "logout": "", "restart": "",
    "search": "", "pin": "", "trash": "", "copy": "", "check": "",
    "folder": "", "camera": "", "eraser": "",
    "wifi": "\uf1eb", "rescan": "\uf021", "eye": "\uf06e", "settings": "\uf013",
    "bluetooth": "\uf294", "headphones": "\uf025", "speaker": "\uf028", "keyboard": "\uf11c",
    "mouse": "\uf8cc", "gamepad": "\uf11b", "phone": "\uf3cd", "laptop": "\uf109",
    "bell": "\uf0f3", "xmark": "\uf00d",
}

TOOLS_CSS = b"""
.tool-icon { border-radius: 11px; min-width: 40px; min-height: 40px; font-size: 17px; }
.tool-icon.clipboard { background-color: #0a84ff; }
.tool-icon.images { background-color: #30d158; }
.tool-icon.note { background-color: #ffd60a; color: #1c1c1e; }
.tool-icon.power { background-color: #ff453a; }
.tool-icon.wifi { background-color: #0a84ff; }
.tool-icon.bluetooth { background-color: #0a84ff; }
.tool-icon.volume { background-color: #ff375f; }
.sound-section { font-size: 9px; font-weight: 700; color: rgba(255, 255, 255, 0.5); padding: 2px 6px 0 6px; }
.sound-row { padding: 5px 8px; border-radius: 11px; transition: background-color 140ms ease-out; }
.sound-row.hover { background-color: rgba(255, 255, 255, 0.12); }
.sound-app { font-size: 11px; font-weight: 500; }
.sound-app.active { font-weight: 700; }
.bt-kind { font-size: 13px; min-width: 20px; color: rgba(255, 255, 255, 0.85); }
.wifi-name { font-size: 12px; font-weight: 500; }
.wifi-name.active { font-weight: 700; }
.wifi-mark { font-size: 10px; color: rgba(255, 255, 255, 0.55); min-width: 14px; }
.wifi-mark.active { color: #30d158; }
.wifi-detail { padding: 0 8px 8px 8px; }
.wifi-error { font-size: 10px; color: #ff6961; }
button.pill {
    background-image: none;
    background-color: rgba(255, 255, 255, 0.14);
    border: none;
    box-shadow: none;
    text-shadow: none;
    border-radius: 9px;
    padding: 4px 12px;
    min-height: 0;
    font-size: 11px;
    font-weight: 600;
    transition: background-color 140ms ease-out;
}
button.pill:hover { background-color: rgba(255, 255, 255, 0.22); }
button.pill.blue { background-color: #0a84ff; }
button.pill.blue:hover { background-color: #3a9bff; }
switch {
    background-image: none;
    background-color: rgba(255, 255, 255, 0.2);
    border: none;
    box-shadow: none;
    border-radius: 12px;
    min-width: 40px;
    min-height: 22px;
    transition: background-color 200ms ease-out;
}
switch:checked { background-color: #30d158; }
switch image { color: transparent; }
switch slider {
    background-image: none;
    background-color: #ffffff;
    border: none;
    box-shadow: 0 1px 3px rgba(0, 0, 0, 0.35);
    border-radius: 10px;
    min-width: 18px;
    min-height: 18px;
    margin: 2px;
}
.tool-title { font-size: 15px; font-weight: 700; }
entry.search {
    background-color: rgba(255, 255, 255, 0.08);
    border: 1px solid rgba(255, 255, 255, 0.08);
    border-radius: 9px;
    box-shadow: none;
    padding: 4px 8px;
    min-height: 0;
    font-size: 12px;
    color: #ffffff;
    caret-color: #ffffff;
}
entry.search:focus { border-color: #0a84ff; }
.item { padding: 7px 8px; transition: background-color 140ms ease-out; }
.item.hover { background-color: rgba(255, 255, 255, 0.12); }
.item.pressed, .shot.pressed, .power-row.pressed { background-color: rgba(255, 255, 255, 0.05); }
.item-text { font-size: 11px; }
.item-sub { font-size: 10px; color: rgba(255, 255, 255, 0.5); }
button.flat.pinned .trash { color: #ffd60a; }
.shot { padding: 4px; transition: background-color 140ms ease-out; }
.shot.hover { background-color: rgba(255, 255, 255, 0.12); }
.shot-time { font-size: 10px; color: rgba(255, 255, 255, 0.6); }
textview.notes, textview.notes text {
    background-color: transparent;
    color: #ffffff;
    font-size: 12px;
    caret-color: #ffffff;
}
textview.notes text selection { background-color: rgba(10, 132, 255, 0.6); }
.power-row { padding: 8px 10px; transition: background-color 140ms ease-out; }
.power-row.hover { background-color: rgba(255, 255, 255, 0.12); }
.power-row.armed { background-color: rgba(255, 69, 58, 0.85); }
.power-circle {
    background-color: rgba(255, 255, 255, 0.14);
    border-radius: 15px;
    min-width: 30px;
    min-height: 30px;
    font-size: 13px;
}
.power-row.armed .power-circle { background-color: rgba(255, 255, 255, 0.25); }
"""


def load_clips():
    # The glass panel, its opener and shared helpers live in clips.py
    spec = importlib.util.spec_from_file_location("clips", HERE / "clips.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


clips = load_clips()
run = clips.run


def spawn(*command, stdin=None):
    subprocess.Popen(command, start_new_session=True, stdin=stdin,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def copy_file(path, mime):
    with open(path, "rb") as source:
        spawn("wl-copy", "--type", mime, stdin=source)


# ---- Shared panel pieces ----

class Parts:
    def __init__(self, cc, Gtk, GLib):
        self.cc, self.Gtk, self.GLib = cc, Gtk, GLib
        cc.ICONS.update(ICONS)

    def label(self, text, css_class, xalign=0):
        return self.cc.css_classes(self.Gtk.Label(label=text, xalign=xalign), css_class)

    def panel(self, width):
        panel = self.cc.css_classes(self.Gtk.Box(orientation=self.Gtk.Orientation.VERTICAL, spacing=8), "panel")
        panel.set_size_request(width, -1)
        return panel

    def header(self, icon, title, font=None):
        """The tile at the top: a coloured icon, a title, a line under it, and room for buttons."""
        Gtk = self.Gtk
        header = self.cc.css_classes(Gtk.Box(spacing=11), "tile")
        badge = self.cc.icon_label(icon, "tool-icon", font=font or self.cc.ICON_FONT)
        self.cc.css_classes(badge, icon)
        badge.set_valign(Gtk.Align.CENTER)
        header.pack_start(badge, False, False, 0)
        titles = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        titles.set_valign(Gtk.Align.CENTER)
        titles.pack_start(self.label(title, "tool-title"), False, False, 0)
        sub = self.label("", "tile-sub")
        titles.pack_start(sub, False, False, 0)
        header.pack_start(titles, True, True, 0)
        return header, sub

    def button(self, icon, tooltip, on_click):
        button = self.cc.css_classes(self.Gtk.Button(), "flat")
        button.add(self.cc.icon_label(icon, "trash"))
        button.set_valign(self.Gtk.Align.CENTER)
        button.set_tooltip_text(tooltip)
        button.connect("clicked", lambda *_: on_click())
        return button

    def armed_button(self, icon, tooltip, again, on_confirm):
        """A button that needs a second click within a few seconds, like the clips' trash."""
        state = {"timer": None}
        button = self.button(icon, tooltip, lambda: None)

        def disarm():
            state["timer"] = None
            self.cc.set_class(button, "armed", False)
            button.set_tooltip_text(tooltip)
            return False

        def click(*_):
            if state["timer"] is None:
                self.cc.set_class(button, "armed", True)
                button.set_tooltip_text(again)
                state["timer"] = self.GLib.timeout_add_seconds(ARM_SECONDS, disarm)
                return
            self.GLib.source_remove(state["timer"])
            disarm()
            on_confirm()

        button.connect("clicked", click)
        return button

    def flash_copied(self, button):
        holder = button.get_child()
        old, tooltip = holder.get_text(), button.get_tooltip_text()
        holder.set_text(ICONS["check"])
        self.cc.set_class(button, "copied", True)
        button.set_tooltip_text("Copied")

        def reset():
            holder.set_text(old)
            self.cc.set_class(button, "copied", False)
            button.set_tooltip_text(tooltip)
            return False
        self.GLib.timeout_add(1500, reset)

    def hover_row(self, content, css_class, on_click=None):
        """Wraps a row so it lights up under the mouse and can be clicked."""
        Gdk = self.cc.Gdk
        area = self.Gtk.EventBox()
        area.set_visible_window(False)
        area.add(content)

        def on_hover(_area, event, inside):
            if event.detail != Gdk.NotifyType.INFERIOR:
                self.cc.set_class(content, "hover", inside)

        area.connect("enter-notify-event", on_hover, True)
        area.connect("leave-notify-event", on_hover, False)
        self.cc.press_feedback(area, content)
        if on_click:
            area.connect("button-release-event", lambda _a, e: e.button == 1 and on_click())
        return area

    def scroller(self, child, max_height):
        Gtk = self.Gtk
        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.set_propagate_natural_height(True)
        scroller.set_max_content_height(max_height)
        scroller.add(child)
        return scroller

    @staticmethod
    def fit_height(scroller, child, max_height):
        # Tall enough for everything, up to max_height, then it scrolls
        child.show_all()
        scroller.set_min_content_height(min(child.get_preferred_height()[1], max_height))


# ---- Clipboard history ----

def cliphist_entries():
    """(cliphist line, id, preview) for each history entry, newest first."""
    entries = []
    for line in run("cliphist", "list").splitlines()[:CLIPBOARD_LIMIT]:
        if "\t" in line:
            number, preview = line.split("\t", 1)
            entries.append((line, number, preview))
    return entries


def is_image(preview):
    return preview.startswith("[[ binary data") and any(kind in preview for kind in ("png", "jpeg", "jpg", "bmp", "webp"))


def cliphist_image(line, number):
    """Caches a copied picture as a small PNG, for its thumbnail."""
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / f"clip-{number}.png"
    if not path.exists():
        data = subprocess.run(["cliphist", "decode"], input=line.encode(), capture_output=True).stdout
        if data:
            path.write_bytes(data)
    return path if path.exists() else None


def load_pins():
    PINS.mkdir(parents=True, exist_ok=True)
    return sorted(PINS.glob("*"), reverse=True)


def build_clipboard(cc, Gtk, GLib, close):
    from gi.repository import GdkPixbuf, Pango
    parts = Parts(cc, Gtk, GLib)
    panel = parts.panel(360)
    timers = []
    running = bool(run("pgrep", "-f", "cliphist store"))

    header, summary = parts.header("clipboard", "Clipboard")

    def wipe():
        run("cliphist", "wipe")
        close()

    header.pack_start(parts.armed_button("eraser", "Clear history", "Click again to clear the history", wipe),
                      False, False, 0)
    panel.pack_start(header, False, False, 0)

    search = cc.css_classes(Gtk.Entry(), "search")
    search.set_placeholder_text("Search")
    panel.pack_start(search, False, False, 0)

    rows = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
    scroller = parts.scroller(rows, LIST_HEIGHT)
    panel.pack_start(scroller, False, False, 0)
    searchable = []    # (row widget, lowercase text, heading it belongs under)

    def heading(text):
        widget = parts.label(text, "heading")
        rows.pack_start(widget, False, False, 0)
        return widget

    def item_row(text, sub, thumbnail, buttons, on_click):
        box = cc.css_classes(Gtk.Box(spacing=9), "tile", "item")
        if thumbnail:
            try:
                pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(str(thumbnail), CLIP_THUMB * 2, CLIP_THUMB, True)
                image = Gtk.Image.new_from_pixbuf(pixbuf)
                image.set_valign(Gtk.Align.CENTER)
                box.pack_start(image, False, False, 0)
            except Exception:
                pass
        words = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        words.set_valign(Gtk.Align.CENTER)
        main = parts.label(text, "item-text")
        main.set_ellipsize(Pango.EllipsizeMode.END)
        main.set_lines(2)
        main.set_line_wrap(True)
        main.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
        main.set_max_width_chars(34)
        words.pack_start(main, False, False, 0)
        if sub:
            words.pack_start(parts.label(sub, "item-sub"), False, False, 0)
        box.pack_start(words, True, True, 0)
        for button in buttons:
            box.pack_start(button, False, False, 0)
        return parts.hover_row(box, "item", on_click)

    def copy_and_close(action):
        action()
        close()

    # Pinned: kept as files in ~/.local/share/gear-tools/pins, so they survive clearing the history
    pins = load_pins()
    pinned_heading = heading("PINNED") if pins else None

    def pin_row(path):
        picture = path.suffix == ".png"
        text = "Picture" if picture else path.read_text(errors="replace").strip() or "(blank)"
        mime = "image/png" if picture else "text/plain"
        row_holder = {}

        def unpin():
            path.unlink(missing_ok=True)
            row_holder["row"].destroy()

        unpin_button = parts.button("pin", "Unpin", unpin)
        cc.set_class(unpin_button, "pinned", True)
        row = item_row(" ".join(text.split()), None, path if picture else None, [unpin_button],
                       lambda: copy_and_close(lambda: copy_file(path, mime)))
        row_holder["row"] = row
        rows.pack_start(row, False, False, 0)
        searchable.append((row, text.lower(), pinned_heading))

    for path in pins:
        pin_row(path)

    entries = cliphist_entries()
    recent_heading = heading("RECENT") if entries else None
    count = len(entries)
    summary.set_text(f"{count} item{'s' * (count != 1)}" + (f"  ·  {len(pins)} pinned" if pins else "")
                     if running else "History is starting. Copy something")

    def entry_row(line, number, preview):
        picture = is_image(preview)
        row_holder = {}

        def copy():
            data = subprocess.run(["cliphist", "decode"], input=line.encode(), capture_output=True).stdout
            subprocess.Popen(["wl-copy"], stdin=subprocess.PIPE, start_new_session=True,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).communicate(data)

        def pin():
            PINS.mkdir(parents=True, exist_ok=True)
            data = subprocess.run(["cliphist", "decode"], input=line.encode(), capture_output=True).stdout
            stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
            (PINS / f"{stamp}{'.png' if picture else '.txt'}").write_bytes(data)
            pin_button.get_child().set_text(ICONS["check"])
            cc.set_class(pin_button, "pinned", True)

        def delete():
            subprocess.run(["cliphist", "delete"], input=line.encode())
            row_holder["row"].destroy()

        pin_button = parts.button("pin", "Pin", pin)
        buttons = [pin_button, parts.button("trash", "Remove", delete)]
        if picture:
            size = preview.split()[-2] if preview.endswith("]]") else ""
            text, thumb = f"Picture  {size}", cliphist_image(line, number)
        else:
            text, thumb = " ".join(preview.split()), None
        row = item_row(text, None, thumb, buttons, lambda: copy_and_close(copy))
        row_holder["row"] = row
        rows.pack_start(row, False, False, 0)
        searchable.append((row, preview.lower(), recent_heading))

    for line, number, preview in entries:
        entry_row(line, number, preview)

    if not pins and not entries:
        rows.pack_start(parts.label("Things you copy will show up here.", "empty", 0.5), False, False, 0)

    def on_search(*_):
        words = search.get_text().lower().split()
        shown = set()
        for row, text, under in searchable:
            visible = all(word in text for word in words)
            row.set_visible(visible)
            if visible:
                shown.add(under)
        for under in (pinned_heading, recent_heading):
            if under:
                under.set_visible(under in shown)

    search.connect("changed", on_search)
    parts.fit_height(scroller, rows, LIST_HEIGHT)
    GLib.idle_add(search.grab_focus)
    return panel, timers, lambda: None


# ---- Screenshots ----

def list_screenshots():
    if not SCREENSHOTS.exists():
        return []
    shots = [p for p in SCREENSHOTS.iterdir() if p.suffix.lower() in (".png", ".jpg", ".jpeg") and p.is_file()]
    return sorted(shots, key=lambda p: p.stat().st_mtime, reverse=True)


def shot_thumbnail(path):
    CACHE.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha1(f"{path.name}:{path.stat().st_mtime_ns}".encode()).hexdigest()[:16]
    thumb = CACHE / f"shot-{key}.png"
    if not thumb.exists():
        from gi.repository import GdkPixbuf
        pixbuf = GdkPixbuf.Pixbuf.new_from_file(str(path))
        scale = max(SHOT_WIDTH * 2 / pixbuf.get_width(), SHOT_HEIGHT * 2 / pixbuf.get_height())
        width, height = max(1, round(pixbuf.get_width() * scale)), max(1, round(pixbuf.get_height() * scale))
        pixbuf.scale_simple(width, height, GdkPixbuf.InterpType.BILINEAR).savev(str(thumb), "png", [], [])
    return thumb


def build_screenshots(cc, Gtk, GLib, close):
    from gi.repository import Gdk, GdkPixbuf
    parts = Parts(cc, Gtk, GLib)
    width = SHOT_COLUMNS * (SHOT_WIDTH + 14) + 30
    panel = parts.panel(width)
    shots = list_screenshots()
    stopped = threading.Event()

    header, summary = parts.header("images", "Screenshots")

    def take():
        close()
        # After the panel has slid away, so it isn't in the picture
        GLib.timeout_add(350, lambda: spawn("sh", str(SCREENSHOT_SCRIPT)) or False)

    header.pack_start(parts.button("camera", "Take a screenshot (Super+Shift+S)", take), False, False, 0)

    def open_folder():
        SCREENSHOTS.mkdir(parents=True, exist_ok=True)
        spawn("xdg-open", str(SCREENSHOTS))
        close()

    header.pack_start(parts.button("folder", "Open the screenshots folder", open_folder), False, False, 0)
    panel.pack_start(header, False, False, 0)

    def update_summary():
        alive = [p for p in shots if p.exists()]
        size = sum(p.stat().st_size for p in alive)
        summary.set_text(f"{len(alive)} screenshot{'s' * (len(alive) != 1)}  ·  {clips.file_size(size)}"
                         if alive else "No screenshots yet")

    update_summary()
    if not shots:
        panel.pack_start(parts.label("Press Super+Shift+S, or the camera above.", "empty", 0.5), False, False, 0)
        return panel, [], stopped.set

    grid = Gtk.FlowBox()
    grid.set_selection_mode(Gtk.SelectionMode.NONE)
    grid.set_max_children_per_line(SHOT_COLUMNS)
    grid.set_min_children_per_line(SHOT_COLUMNS)
    grid.set_homogeneous(True)
    grid.set_row_spacing(4)
    grid.set_column_spacing(4)
    scroller = parts.scroller(grid, LIST_HEIGHT)
    panel.pack_start(scroller, False, False, 0)
    pictures = {}

    def draw(area, cr, path):
        radius = 6
        cr.new_sub_path()
        for x, y, start in ((SHOT_WIDTH - radius, radius, -90), (SHOT_WIDTH - radius, SHOT_HEIGHT - radius, 0),
                            (radius, SHOT_HEIGHT - radius, 90), (radius, radius, 180)):
            cr.arc(x, y, radius, start * 3.14159 / 180, (start + 90) * 3.14159 / 180)
        cr.close_path()
        cr.clip()
        pixbuf = pictures.get(path)
        if pixbuf:
            cr.scale(0.5, 0.5)
            Gdk.cairo_set_source_pixbuf(cr, pixbuf, (SHOT_WIDTH * 2 - pixbuf.get_width()) / 2,
                                        (SHOT_HEIGHT * 2 - pixbuf.get_height()) / 2)
        else:
            cr.set_source_rgba(1, 1, 1, 0.1)
        cr.paint()

    areas = {}
    for path in shots:
        tile = cc.css_classes(Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3), "tile", "shot")
        area = Gtk.DrawingArea()
        area.set_size_request(SHOT_WIDTH, SHOT_HEIGHT)
        area.connect("draw", draw, path)
        areas[path] = area
        tile.pack_start(area, False, False, 0)
        line = Gtk.Box(spacing=2)
        when = datetime.datetime.fromtimestamp(path.stat().st_mtime)
        stamp = when.strftime("%-I:%M %p") if when.date() == datetime.date.today() else when.strftime("%-d %b")
        line.pack_start(parts.label(stamp, "shot-time"), True, True, 2)
        copy = parts.button("copy", "Copy", lambda: None)
        copy.connect("clicked", lambda button, p=path: (copy_file(p, "image/png"), parts.flash_copied(button)))
        line.pack_start(copy, False, False, 0)
        holder = {}

        def trash(p=path, h=holder):
            run("gio", "trash", str(p))
            if not p.exists():
                h["child"].destroy()
                update_summary()

        line.pack_start(parts.armed_button("trash", "Move to Trash", "Click again to move to Trash", trash),
                        False, False, 0)
        tile.pack_start(line, False, False, 0)
        row = parts.hover_row(tile, "shot", lambda p=path: (spawn("xdg-open", str(p)), close()))
        grid.add(row)
        holder["child"] = row

    def load():
        for path in shots:
            if stopped.is_set():
                return
            try:
                thumb = shot_thumbnail(path)
            except Exception:
                continue
            GLib.idle_add(show, path, thumb)

    def show(path, thumb):
        try:
            pictures[path] = GdkPixbuf.Pixbuf.new_from_file(str(thumb))
        except GLib.Error:
            return False
        areas[path].queue_draw()
        return False

    parts.fit_height(scroller, grid, LIST_HEIGHT)
    # The pictures load once the panel has finished dropping in, so they don't stutter it
    GLib.timeout_add(450, lambda: threading.Thread(target=load, daemon=True).start() and False)
    return panel, [], stopped.set


# ---- Notes ----

def build_notes(cc, Gtk, GLib, close):
    parts = Parts(cc, Gtk, GLib)
    panel = parts.panel(360)
    header, status = parts.header("note", "Notes")
    DATA.mkdir(parents=True, exist_ok=True)

    view = cc.css_classes(Gtk.TextView(), "notes")
    view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
    view.set_left_margin(2)
    view.set_right_margin(2)
    text = view.get_buffer()
    text.set_text(NOTES.read_text() if NOTES.exists() else "")
    text.place_cursor(text.get_end_iter())
    state = {"timer": None}

    def contents():
        return text.get_text(text.get_start_iter(), text.get_end_iter(), True)

    def save():
        state["timer"] = None
        NOTES.write_text(contents())
        status.set_text("Saved")
        return False

    def on_change(*_):
        status.set_text("Editing…")
        if state["timer"]:
            GLib.source_remove(state["timer"])
        state["timer"] = GLib.timeout_add(SAVE_DELAY, save)

    def copy_all():
        subprocess.Popen(["wl-copy"], stdin=subprocess.PIPE, start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).communicate(contents().encode())
        parts.flash_copied(copy_button)

    copy_button = parts.button("copy", "Copy all", copy_all)
    header.pack_start(copy_button, False, False, 0)
    header.pack_start(parts.armed_button("eraser", "Clear", "Click again to clear the note",
                                         lambda: text.set_text("")), False, False, 0)
    panel.pack_start(header, False, False, 0)

    words = len(contents().split())
    status.set_text(f"{words} word{'s' * (words != 1)}  ·  saves as you type" if words else "Saves as you type")
    text.connect("changed", on_change)

    box = cc.css_classes(Gtk.Box(), "tile")
    scroller = Gtk.ScrolledWindow()
    scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
    scroller.set_min_content_height(NOTES_HEIGHT)
    scroller.add(view)
    box.pack_start(scroller, True, True, 0)
    panel.pack_start(box, False, False, 0)
    GLib.idle_add(view.grab_focus)

    def cleanup():
        # Clicking away closes the panel: save straight away instead of waiting
        if state["timer"]:
            GLib.source_remove(state["timer"])
            save()

    return panel, [], cleanup


# ---- Power ----

POWER_ACTIONS = [
    # (icon, title, command, needs a second click)
    ("lock", "Lock", ["sh", "-c", "pidof hyprlock || hyprlock"], False),
    ("moon", "Sleep", ["sh", "-c", "(pidof hyprlock || hyprlock) & sleep 1; systemctl suspend"], False),
    ("logout", "Log Out", ["hyprctl", "dispatch", "exit"], True),
    ("restart", "Restart", ["systemctl", "reboot"], True),
    ("power", "Shut Down", ["systemctl", "poweroff"], True),
]


# ---- Wi-Fi ----

def nmcli(*arguments):
    """Runs nmcli. Returns (worked, what it printed)."""
    try:
        result = subprocess.run(["nmcli", *arguments], capture_output=True, text=True, timeout=45)
    except (OSError, subprocess.TimeoutExpired):
        return False, ""
    return result.returncode == 0, (result.stdout + result.stderr).strip()


def nmcli_fields(line, count):
    # nmcli -t separates fields with ":" and puts a backslash before any ":" inside one
    return [re.sub(r"\\(.)", r"\1", field) for field in re.split(r"(?<!\\):", line, count - 1)]


def wifi_networks(rescan):
    """Nearby networks, the connected one first and then strongest first. A network heard
    from several access points is listed once."""
    _, output = nmcli("-t", "-f", "IN-USE,SIGNAL,SECURITY,SSID", "device", "wifi", "list",
                      "--rescan", "yes" if rescan else "no")
    best = {}
    for line in output.splitlines():
        fields = nmcli_fields(line, 4)
        if len(fields) < 4 or not fields[3]:
            continue
        network = {"name": fields[3], "signal": int(fields[1] or 0),
                   "secure": fields[2] not in ("", "--"), "active": fields[0] == "*"}
        old = best.get(network["name"])
        if not old or network["active"] or (not old["active"] and network["signal"] > old["signal"]):
            best[network["name"]] = network
    return sorted(best.values(), key=lambda network: (not network["active"], -network["signal"]))


def saved_wifi():
    """Names of the networks this PC already has the password for."""
    _, output = nmcli("-t", "-f", "TYPE,NAME", "connection", "show")
    return {fields[1] for fields in (nmcli_fields(line, 2) for line in output.splitlines())
            if len(fields) == 2 and fields[0] == "802-11-wireless"}


def wifi_error(output):
    text = output.lower()
    if "secrets were required" in text or "psk" in text or "password" in text:
        return "That password didn't work. Try again."
    if "no network with ssid" in text:
        return "That network is out of range now."
    return "Couldn't connect. Try again."


def build_wifi(cc, Gtk, GLib, close):
    parts = Parts(cc, Gtk, GLib)
    panel = parts.panel(320)
    state = {"networks": [], "saved": set(), "on": True, "open": None, "busy": None,
             "error": None, "loading": False, "closed": False}

    header, summary = parts.header("wifi", "Wi-Fi")
    rescan_button = parts.button("rescan", "Look for networks again", lambda: load(True))
    header.pack_start(rescan_button, False, False, 0)
    switch = Gtk.Switch()
    switch.set_valign(Gtk.Align.CENTER)
    switch.set_tooltip_text("Turn Wi-Fi on or off")
    header.pack_start(switch, False, False, 0)
    panel.pack_start(header, False, False, 0)

    rows = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
    scroller = parts.scroller(rows, LIST_HEIGHT)
    panel.pack_start(scroller, False, False, 0)

    def in_background(work, then):
        def body():
            result = work()
            GLib.idle_add(lambda: (None if state["closed"] else then(result)) and False)
        threading.Thread(target=body, daemon=True).start()

    def draw_signal(area, cr, network):
        # Four bars, lit up to the signal strength
        lit = 1 + min(3, network["signal"] // 25)
        height = area.get_allocated_height()
        for bar in range(4):
            tall = 4 + bar * 3
            cr.rectangle(bar * 5, height - tall - 1, 3, tall)
            cr.set_source_rgba(1, 1, 1, 0.95 if bar < lit else 0.25)
            cr.fill()

    def pill(text, on_click, blue=False):
        button = cc.css_classes(Gtk.Button(label=text), "pill", *(["blue"] if blue else []))
        button.connect("clicked", lambda *_: on_click())
        return button

    def connect(network, password=None):
        name = network["name"]
        if password is not None and len(password) < 8:
            state["error"] = "Wi-Fi passwords are at least 8 characters."
            return show()
        state["busy"], state["error"] = name, None
        show()

        def work():
            if password is None and name in state["saved"]:
                return nmcli("-w", "25", "connection", "up", "id", name)
            if name in state["saved"]:
                # A saved password that stopped working is replaced by the new one
                nmcli("connection", "delete", "id", name)
            worked, output = nmcli("-w", "25", "device", "wifi", "connect", name,
                                   *(["password", password] if password else []))
            if not worked:
                # nmcli leaves a half-made entry behind when the password is wrong
                nmcli("connection", "delete", "id", name)
            return worked, output

        def done(result):
            worked, output = result
            state["busy"] = None
            if worked:
                cc.sound("on")
                state["open"] = None
            else:
                state["error"] = wifi_error(output)
                # Opens the row with the reason. A secured network asks for its password
                # again there, since a saved one may be what's wrong
                state["open"] = name
                state["retry"] = name
            load(False)

        in_background(work, done)

    def act(network, *command):
        state["busy"], state["open"] = network["name"], None
        show()

        def done(_result):
            state["busy"] = None
            load(False)

        in_background(lambda: nmcli(*command), done)

    def on_row(network):
        name = network["name"]
        if state["busy"]:
            return
        state["error"] = None
        if network["active"] or (network["secure"] and name not in state["saved"]) \
                or state.get("retry") == name:
            # Opens the row: Disconnect and Forget, or the password box
            state["open"] = None if state["open"] == name else name
            show()
        else:
            connect(network)

    def build_row(network):
        name = network["name"]
        known = name in state["saved"]
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        line = cc.css_classes(Gtk.Box(spacing=9), "tile", "item")
        bars = Gtk.DrawingArea()
        bars.set_size_request(19, 15)
        bars.set_valign(Gtk.Align.CENTER)
        bars.connect("draw", draw_signal, network)
        line.pack_start(bars, False, False, 0)
        titles = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        titles.set_valign(Gtk.Align.CENTER)
        title = parts.label(name, "wifi-name")
        title.set_ellipsize(3)
        cc.set_class(title, "active", network["active"])
        titles.pack_start(title, False, False, 0)
        note = ("Connecting…" if state["busy"] == name else "Connected" if network["active"]
                else "Saved" if known else "")
        if note:
            titles.pack_start(parts.label(note, "item-sub"), False, False, 0)
        line.pack_start(titles, True, True, 0)
        if state["busy"] == name:
            spinner = Gtk.Spinner()
            spinner.start()
            line.pack_start(spinner, False, False, 0)
        elif network["active"]:
            line.pack_start(cc.css_classes(cc.icon_label("check", "wifi-mark"), "active"), False, False, 0)
        elif network["secure"]:
            line.pack_start(cc.icon_label("lock", "wifi-mark"), False, False, 0)
        box.pack_start(parts.hover_row(line, "item", lambda: on_row(network)), False, False, 0)

        if state["open"] != name or state["busy"]:
            return box, None
        detail = cc.css_classes(Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5), "wifi-detail")
        detail.set_margin_top(5)
        focus = None
        if network["active"]:
            buttons = Gtk.Box(spacing=6)
            buttons.pack_start(pill("Disconnect", lambda: act(network, "connection", "down", "id", name)),
                               False, False, 0)
            buttons.pack_start(pill("Forget", lambda: act(network, "connection", "delete", "id", name)),
                               False, False, 0)
            detail.pack_start(buttons, False, False, 0)
        elif network["secure"]:
            entry_row = Gtk.Box(spacing=6)
            entry = cc.css_classes(Gtk.Entry(), "search")
            entry.set_visibility(False)
            entry.set_input_purpose(Gtk.InputPurpose.PASSWORD)
            entry.set_placeholder_text("Password")
            entry.connect("activate", lambda *_: connect(network, entry.get_text()))
            entry_row.pack_start(entry, True, True, 0)
            entry_row.pack_start(parts.button("eye", "Show the password",
                                              lambda: entry.set_visibility(not entry.get_visibility())),
                                 False, False, 0)
            entry_row.pack_start(pill("Join", lambda: connect(network, entry.get_text()), blue=True),
                                 False, False, 0)
            detail.pack_start(entry_row, False, False, 0)
            focus = entry
        else:
            detail.pack_start(pill("Try again", lambda: connect(network), blue=True), False, False, 0)
        if state["error"]:
            detail.pack_start(parts.label(state["error"], "wifi-error"), False, False, 0)
        box.pack_start(detail, False, False, 0)
        return box, focus

    def show():
        for child in rows.get_children():
            child.destroy()
        active = next((n["name"] for n in state["networks"] if n["active"]), None)
        summary.set_text("Off" if not state["on"] else f"Connected to {active}" if active
                         else "Looking for networks…" if state["loading"] and not state["networks"]
                         else "Not connected")
        rescan_button.set_sensitive(state["on"] and not state["loading"])
        focus = None
        if not state["on"]:
            rows.pack_start(parts.label("Wi-Fi is off.", "empty", 0.5), False, False, 0)
        elif not state["networks"] and not state["loading"]:
            rows.pack_start(parts.label("No networks found.", "empty", 0.5), False, False, 0)
        for network in state["networks"] if state["on"] else []:
            row, wants_focus = build_row(network)
            rows.pack_start(row, False, False, 0)
            focus = focus or wants_focus
        settings = cc.css_classes(Gtk.Box(spacing=9), "tile", "item")
        settings.pack_start(cc.icon_label("settings", "wifi-mark"), False, False, 0)
        settings.pack_start(parts.label("Network settings…", "wifi-name"), True, True, 0)
        rows.pack_start(parts.hover_row(settings, "item", lambda: (spawn("nm-connection-editor"), close())),
                        False, False, 0)
        rows.show_all()
        parts.fit_height(scroller, rows, LIST_HEIGHT)
        if focus:
            focus.grab_focus()

    def load(rescan):
        if state["loading"]:
            return
        state["loading"] = True
        show()

        def work():
            on = nmcli("radio", "wifi")[1] == "enabled"
            return on, wifi_networks(rescan) if on else [], saved_wifi()

        def done(result):
            state["on"], state["networks"], state["saved"] = result
            state["loading"] = False
            set_switch(state["on"])
            show()

        in_background(work, done)

    def on_switch(_switch, _value):
        turn_on = switch.get_active()
        cc.sound("on" if turn_on else "off")
        state["on"], state["open"], state["error"] = turn_on, None, None
        if not turn_on:
            state["networks"] = []
        show()

        def work():
            nmcli("radio", "wifi", "on" if turn_on else "off")
            if turn_on:
                # The card needs a moment before it has heard anything
                threading.Event().wait(2.5)

        in_background(work, lambda _result: load(turn_on))

    switch_handler = switch.connect("notify::active", on_switch)

    def set_switch(on):
        switch.handler_block(switch_handler)
        switch.set_active(on)
        switch.handler_unblock(switch_handler)

    def refresh():
        # Keeps the list fresh, but never while you're typing a password or connecting
        if not (state["open"] or state["busy"]):
            load(True)
        return True

    def cancel_edit():
        if state["open"] and not state["busy"]:
            state["open"], state["error"] = None, None
            show()
            return True
        return False

    panel.cancel_edit = cancel_edit
    # What the card already knows shows at once; a fresh scan follows
    state["networks"], state["saved"] = wifi_networks(False), saved_wifi()
    state["on"] = nmcli("radio", "wifi")[1] == "enabled"
    set_switch(state["on"])
    show()
    timers = [GLib.timeout_add(700, lambda: load(True) and False),
              GLib.timeout_add_seconds(12, refresh)]
    return panel, timers, lambda: state.update(closed=True)


# ---- Bluetooth ----

BLUETOOTH_KINDS = {"audio-headset": "headphones", "audio-headphones": "headphones", "audio-card": "speaker",
                   "input-keyboard": "keyboard", "input-mouse": "mouse", "input-gaming": "gamepad",
                   "phone": "phone", "computer": "laptop"}


def bluetoothctl(*arguments, timeout=30):
    """Runs bluetoothctl. Returns (worked, what it printed)."""
    try:
        result = subprocess.run(["bluetoothctl", *arguments], capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return False, ""
    output = result.stdout + result.stderr
    return result.returncode == 0 and "Failed" not in output and "not available" not in output, output


def bluetooth_devices():
    """Every device Bluetooth knows or can hear: connected first, then paired, then the rest.
    Ones that only announce an address, with no name, are left out."""
    devices = []
    for line in bluetoothctl("devices")[1].splitlines():
        parts = line.split(" ", 2)
        if len(parts) < 3 or parts[0] != "Device":
            continue
        address, name = parts[1], parts[2]
        info = bluetoothctl("info", address)[1]

        def field(key):
            found = re.search(rf"^\s*{key}: (.*)$", info, re.MULTILINE)
            return found.group(1).strip() if found else ""

        if name.replace("-", ":").upper() == address.upper():
            continue
        battery = re.search(r"\((\d+)\)", field("Battery Percentage"))
        devices.append({"address": address, "name": field("Alias") or name,
                        "paired": field("Paired") == "yes", "connected": field("Connected") == "yes",
                        "kind": BLUETOOTH_KINDS.get(field("Icon"), "bluetooth"),
                        "battery": int(battery.group(1)) if battery else None})
    return sorted(devices, key=lambda d: (not d["connected"], not d["paired"], d["name"].lower()))


def build_bluetooth(cc, Gtk, GLib, close):
    parts = Parts(cc, Gtk, GLib)
    panel = parts.panel(320)
    state = {"devices": [], "on": True, "open": None, "busy": None, "error": None,
             "loading": False, "closed": False}

    header, summary = parts.header("bluetooth", "Bluetooth", cc.BRAND_FONT)
    switch = Gtk.Switch()
    switch.set_valign(Gtk.Align.CENTER)
    switch.set_tooltip_text("Turn Bluetooth on or off")
    header.pack_start(switch, False, False, 0)
    panel.pack_start(header, False, False, 0)

    rows = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
    scroller = parts.scroller(rows, LIST_HEIGHT)
    panel.pack_start(scroller, False, False, 0)

    def in_background(work, then):
        def body():
            result = work()
            GLib.idle_add(lambda: (None if state["closed"] else then(result)) and False)
        threading.Thread(target=body, daemon=True).start()

    def pill(text, on_click, blue=False):
        button = cc.css_classes(Gtk.Button(label=text), "pill", *(["blue"] if blue else []))
        button.connect("clicked", lambda *_: on_click())
        return button

    def act(device, work, sound=None):
        state["busy"], state["open"], state["error"] = device["address"], None, None
        show()

        def done(worked):
            state["busy"] = None
            if worked and sound:
                cc.sound(sound)
            if not worked:
                state["open"] = device["address"]
                state["error"] = ("Couldn't connect. Make sure it's switched on and in pairing mode."
                                  if sound == "on" else "That didn't work. Try again.")
            load()

        in_background(work, done)

    def connect(device):
        address = device["address"]

        def work():
            if not device["paired"]:
                if not bluetoothctl("pair", address, timeout=40)[0]:
                    return False
                bluetoothctl("trust", address)   # so it reconnects by itself next time
            return bluetoothctl("connect", address, timeout=40)[0]

        act(device, work, "on")

    def on_row(device):
        if state["busy"]:
            return
        state["error"] = None
        if device["connected"] or device["paired"]:
            # Opens the row: Connect or Disconnect, and Forget
            state["open"] = None if state["open"] == device["address"] else device["address"]
            show()
        else:
            connect(device)

    def build_row(device):
        address = device["address"]
        busy = state["busy"] == address
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        line = cc.css_classes(Gtk.Box(spacing=9), "tile", "item")
        font = cc.BRAND_FONT if device["kind"] == "bluetooth" else cc.ICON_FONT
        line.pack_start(cc.icon_label(device["kind"], "bt-kind", font=font), False, False, 0)
        titles = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        titles.set_valign(Gtk.Align.CENTER)
        title = parts.label(device["name"], "wifi-name")
        title.set_ellipsize(3)
        cc.set_class(title, "active", device["connected"])
        titles.pack_start(title, False, False, 0)
        note = ("Connecting…" if busy else "Connected" if device["connected"]
                else "Paired" if device["paired"] else "")
        if device["connected"] and device["battery"] is not None and not busy:
            note += f"  ·  {device['battery']}% battery"
        if note:
            titles.pack_start(parts.label(note, "item-sub"), False, False, 0)
        line.pack_start(titles, True, True, 0)
        if busy:
            spinner = Gtk.Spinner()
            spinner.start()
            line.pack_start(spinner, False, False, 0)
        elif device["connected"]:
            line.pack_start(cc.css_classes(cc.icon_label("check", "wifi-mark"), "active"), False, False, 0)
        box.pack_start(parts.hover_row(line, "item", lambda: on_row(device)), False, False, 0)

        if state["open"] != address or busy:
            return box
        detail = cc.css_classes(Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5), "wifi-detail")
        detail.set_margin_top(5)
        buttons = Gtk.Box(spacing=6)
        if device["connected"]:
            buttons.pack_start(pill("Disconnect", lambda: act(
                device, lambda: bluetoothctl("disconnect", address)[0], "off")), False, False, 0)
        else:
            buttons.pack_start(pill("Connect", lambda: connect(device), blue=True), False, False, 0)
        if device["paired"]:
            buttons.pack_start(pill("Forget", lambda: act(
                device, lambda: bluetoothctl("remove", address)[0])), False, False, 0)
        detail.pack_start(buttons, False, False, 0)
        if state["error"]:
            error = parts.label(state["error"], "wifi-error")
            error.set_line_wrap(True)
            error.set_max_width_chars(40)
            detail.pack_start(error, False, False, 0)
        box.pack_start(detail, False, False, 0)
        return box

    def show():
        for child in rows.get_children():
            child.destroy()
        connected = [d["name"] for d in state["devices"] if d["connected"]]
        summary.set_text("Off" if not state["on"] else f"Connected to {', '.join(connected)}" if connected
                         else "Looking for devices…")
        if not state["on"]:
            rows.pack_start(parts.label("Bluetooth is off.", "empty", 0.5), False, False, 0)
        elif not state["devices"]:
            rows.pack_start(parts.label("Put a device in pairing mode and it will show up here.",
                                        "empty", 0.5), False, False, 0)
        for device in state["devices"] if state["on"] else []:
            rows.pack_start(build_row(device), False, False, 0)
        settings = cc.css_classes(Gtk.Box(spacing=9), "tile", "item")
        settings.pack_start(cc.icon_label("settings", "wifi-mark"), False, False, 0)
        settings.pack_start(parts.label("Bluetooth settings…", "wifi-name"), True, True, 0)
        rows.pack_start(parts.hover_row(settings, "item", lambda: (spawn("blueman-manager"), close())),
                        False, False, 0)
        rows.show_all()
        parts.fit_height(scroller, rows, LIST_HEIGHT)

    def load():
        if state["loading"]:
            return
        state["loading"] = True

        def work():
            on = "Powered: yes" in bluetoothctl("show")[1]
            return on, bluetooth_devices() if on else []

        def done(result):
            state["on"], state["devices"] = result
            state["loading"] = False
            set_switch(state["on"])
            show()

        in_background(work, done)

    def listen():
        # Searches for new devices for as long as the panel is open (each search lasts 8 seconds)
        while not state["closed"]:
            if state["on"]:
                bluetoothctl("--timeout", "8", "scan", "on", timeout=12)
            else:
                threading.Event().wait(2)

    def on_switch(_switch, _value):
        turn_on = switch.get_active()
        cc.sound("on" if turn_on else "off")
        state["on"], state["open"], state["error"] = turn_on, None, None
        if not turn_on:
            state["devices"] = []
        show()
        in_background(lambda: bluetoothctl("power", "on" if turn_on else "off"), lambda _result: load())

    switch_handler = switch.connect("notify::active", on_switch)

    def set_switch(on):
        switch.handler_block(switch_handler)
        switch.set_active(on)
        switch.handler_unblock(switch_handler)

    def refresh():
        # Never while a row is open or connecting, so nothing moves under the mouse
        if not (state["open"] or state["busy"]):
            load()
        return True

    def cancel_edit():
        if state["open"] and not state["busy"]:
            state["open"], state["error"] = None, None
            show()
            return True
        return False

    panel.cancel_edit = cancel_edit
    state["on"] = "Powered: yes" in bluetoothctl("show")[1]
    state["devices"] = bluetooth_devices() if state["on"] else []
    set_switch(state["on"])
    show()
    threading.Thread(target=listen, daemon=True).start()
    return panel, [GLib.timeout_add_seconds(3, refresh)], lambda: state.update(closed=True)


# ---- Sound ----

def pactl(*arguments):
    try:
        return subprocess.run(["pactl", *arguments], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.TimeoutExpired):
        return ""


def pactl_list(kind):
    try:
        return json.loads(pactl("-f", "json", "list", kind))
    except ValueError:
        return []


def percent_of(item):
    levels = [int(channel["value_percent"].rstrip("%")) for channel in item.get("volume", {}).values()]
    return max(levels) if levels else 0


def sound_apps():
    """Everything playing sound right now, one entry per app (an app's separate streams, like
    a browser's tabs, move together): {name: {"ids": [...], "level": 0-100, "muted": bool}}."""
    apps = {}
    for stream in pactl_list("sink-inputs"):
        properties = stream.get("properties", {})
        name = properties.get("application.name") or properties.get("media.name") or "Unknown"
        # Voice chat reports its audio engine's name, not the app's
        if "WEBRTC" in name or name.lower().startswith("audio"):
            name = properties.get("application.process.binary") or name
        name = re.sub(r"\.exe$", "", name)
        app = apps.setdefault(name, {"ids": [], "level": 0, "muted": True})
        app["ids"].append(str(stream["index"]))
        app["level"] = max(app["level"], min(percent_of(stream), 100))
        app["muted"] = app["muted"] and bool(stream.get("mute"))
    return apps


def sound_devices(kind):
    """Speakers and headsets ("sinks") or microphones ("sources"): (name, title, is the one in use)."""
    current = pactl("get-default-sink" if kind == "sinks" else "get-default-source").strip()
    devices = []
    for device in pactl_list(kind):
        if device["name"].endswith(".monitor"):
            continue
        title = re.sub(r"\s*(Analog|Digital) Stereo.*$|\s*Mono$", "", device.get("description", device["name"]))
        devices.append((device["name"], title.strip() or device["name"], device["name"] == current))
    return devices


def build_sound(cc, Gtk, GLib, close):
    parts = Parts(cc, Gtk, GLib)
    panel = parts.panel(SOUND_WIDTH)
    state = {"rows": {}, "closed": False, "names": None, "devices": None}

    def quiet(slider):
        # The mouse wheel scrolls the list here; it never changes a volume by accident
        slider.handler_block_by_func(slider.on_scroll)
        return slider

    panel.set_spacing(5)
    summary = parts.label("", "sound-section")
    panel.pack_start(summary, False, False, 0)

    def speaker_button(muted, on_click):
        button = cc.css_classes(Gtk.Button(), "flat")
        icon = cc.icon_label("muted" if muted else "volume", "slider-icon")
        button.add(icon)
        button.connect("clicked", lambda *_: on_click())

        def show(is_muted):
            icon.set_markup(f"<span font_family='{cc.ICON_FONT}' font_weight='900'>"
                            f"{cc.ICONS['muted' if is_muted else 'volume']}</span>")
        return button, show

    # Master volume: everything, on whichever speakers or headset is in use
    master = {"muted": False}

    def set_master(value):
        run("wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", f"{value / 100:.2f}")
        run("wpctl", "set-mute", "@DEFAULT_AUDIO_SINK@", "0")
        master["muted"] = False
        show_master(False)

    def mute_master():
        run("wpctl", "set-mute", "@DEFAULT_AUDIO_SINK@", "toggle")
        read_master()

    master_button, show_master = speaker_button(False, mute_master)
    master_row, master_slider = cc.build_slider(master_button, set_master)
    quiet(master_slider)
    master_tile = cc.css_classes(Gtk.Box(), "tile", "sound-row")
    master_tile.pack_start(master_row, True, True, 0)
    panel.pack_start(master_tile, False, False, 0)

    def read_master():
        level, muted = cc.read_volume()
        master["muted"] = muted
        master_slider.set_value(level)
        show_master(muted)

    panel.pack_start(parts.label("APPS", "sound-section"), False, False, 0)
    apps_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
    scroller = parts.scroller(apps_box, SOUND_APPS_HEIGHT)
    panel.pack_start(scroller, False, False, 0)
    devices_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
    panel.pack_start(devices_box, False, False, 0)

    def build_app(name, app):
        # One line per app: mute, its name, and its slider
        tile = cc.css_classes(Gtk.Box(spacing=6), "tile", "sound-row")
        title = parts.label(name, "sound-app")
        title.set_ellipsize(3)
        title.set_width_chars(11)
        title.set_max_width_chars(11)
        entry = {"app": app}

        def set_level(value):
            # Not waited for, so the slider keeps up with the mouse however many streams there are
            for stream in entry["app"]["ids"]:
                spawn("pactl", "set-sink-input-volume", stream, f"{round(value)}%")
                if entry["app"]["muted"]:
                    spawn("pactl", "set-sink-input-mute", stream, "0")
            entry["app"]["muted"] = False
            entry["app"]["level"] = round(value)
            entry["show"](False)

        def mute():
            muted = not entry["app"]["muted"]
            for stream in entry["app"]["ids"]:
                run("pactl", "set-sink-input-mute", stream, "1" if muted else "0")
            entry["app"]["muted"] = muted
            entry["show"](muted)

        button, entry["show"] = speaker_button(app["muted"], mute)
        button.set_valign(Gtk.Align.CENTER)
        tile.pack_start(button, False, False, 0)
        tile.pack_start(title, False, False, 0)
        entry["slider"] = quiet(cc.Slider(set_level))
        entry["slider"].set_value(app["level"])
        tile.pack_start(entry["slider"], True, True, 0)
        entry["tile"] = tile
        return entry

    def show_apps(apps):
        # The list is only rebuilt when an app starts or stops playing, so a slider you're
        # dragging is never pulled out from under the mouse
        if list(apps) != state["names"]:
            state["names"] = list(apps)
            for child in apps_box.get_children():
                child.destroy()
            state["rows"] = {}
            for name in sorted(apps, key=str.lower):
                state["rows"][name] = build_app(name, apps[name])
                apps_box.pack_start(state["rows"][name]["tile"], False, False, 0)
            if not apps:
                apps_box.pack_start(parts.label("Nothing is playing sound right now.", "empty", 0.5),
                                    False, False, 0)
            apps_box.show_all()
            parts.fit_height(scroller, apps_box, SOUND_APPS_HEIGHT)
        else:
            for name, app in apps.items():
                row = state["rows"][name]
                row["app"] = app
                if abs(row["slider"].value - app["level"]) >= 1:
                    row["slider"].set_value(app["level"])
                row["show"](app["muted"])

    def choose(kind, name):
        cc.sound("on")
        run("pactl", "set-default-sink" if kind == "sinks" else "set-default-source", name)
        if kind == "sinks":
            # Sound that's already playing moves over too, not just what starts afterwards
            for stream in pactl_list("sink-inputs"):
                run("pactl", "move-sink-input", str(stream["index"]), name)
        state["devices"] = None
        refresh()

    def show_devices(devices):
        if devices == state["devices"]:
            return
        state["devices"] = devices
        for child in devices_box.get_children():
            child.destroy()
        for kind, title in (("sinks", "OUTPUT"), ("sources", "MICROPHONE")):
            if len(devices[kind]) < 2:
                continue   # nothing to choose between
            devices_box.pack_start(parts.label(title, "sound-section"), False, False, 0)
            for name, label, current in devices[kind]:
                row = cc.css_classes(Gtk.Box(spacing=9), "tile", "sound-row")
                text = parts.label(label, "sound-app")
                text.set_ellipsize(3)
                cc.set_class(text, "active", current)
                row.pack_start(text, True, True, 0)
                if current:
                    row.pack_start(cc.css_classes(cc.icon_label("check", "wifi-mark"), "active"), False, False, 0)
                devices_box.pack_start(parts.hover_row(
                    row, "sound-row", lambda kind=kind, name=name: choose(kind, name)), False, False, 0)
        using = next((label for _name, label, current in devices["sinks"] if current), "")
        summary.set_text(using.upper() or "VOLUME")
        devices_box.show_all()

    def refresh():
        def work():
            return sound_apps(), {"sinks": sound_devices("sinks"), "sources": sound_devices("sources")}

        def done(result):
            if not state["closed"]:
                show_apps(result[0])
                show_devices(result[1])
                read_master()
            return False

        threading.Thread(target=lambda: GLib.idle_add(done, work()), daemon=True).start()
        return True

    show_apps(sound_apps())
    show_devices({"sinks": sound_devices("sinks"), "sources": sound_devices("sources")})
    read_master()
    return panel, [GLib.timeout_add(1500, refresh)], lambda: state.update(closed=True)


def build_power(cc, Gtk, GLib, close):
    parts = Parts(cc, Gtk, GLib)
    panel = parts.panel(240)
    rows = []

    for icon, title, command, confirm in POWER_ACTIONS:
        row = cc.css_classes(Gtk.Box(spacing=10), "tile", "power-row")
        circle = cc.icon_label(icon, "power-circle")
        circle.set_valign(Gtk.Align.CENTER)
        row.pack_start(circle, False, False, 0)
        name = parts.label(title, "tile-title")
        name.set_valign(Gtk.Align.CENTER)
        row.pack_start(name, True, True, 0)
        armed = {"timer": None}

        def disarm(row=row, name=name, title=title, armed=armed):
            armed["timer"] = None
            cc.set_class(row, "armed", False)
            name.set_text(title)
            return False

        def act(row=row, name=name, title=title, command=command, confirm=confirm, armed=armed, disarm=disarm):
            if confirm and armed["timer"] is None:
                # Only one armed at a time
                for other in rows:
                    other["disarm"]()
                cc.set_class(row, "armed", True)
                name.set_text(f"Click again to {title.lower()}")
                armed["timer"] = GLib.timeout_add_seconds(ARM_SECONDS, disarm)
                return
            if armed["timer"]:
                GLib.source_remove(armed["timer"])
            close()
            GLib.timeout_add(250, lambda: spawn(*command) or False)

        rows.append({"disarm": lambda armed=armed, disarm=disarm: armed["timer"] and (
            GLib.source_remove(armed["timer"]), disarm())})
        panel.pack_start(parts.hover_row(row, "power-row", act), False, False, 0)

    return panel, [], lambda: None


PANELS = {
    "--clipboard": ("local.waybar.Clipboard", build_clipboard, 360),
    "--screenshots": ("local.waybar.Screenshots", build_screenshots, SHOT_COLUMNS * (SHOT_WIDTH + 14) + 30),
    "--notes": ("local.waybar.Notes", build_notes, 360),
    "--wifi": ("local.waybar.Wifi", build_wifi, 320),
    "--bluetooth": ("local.waybar.Bluetooth", build_bluetooth, 320),
    "--sound": ("local.waybar.Sound", build_sound, SOUND_WIDTH),
    "--power": ("local.waybar.Power", build_power, 240),
}

if __name__ == "__main__":
    for flag, (app_id, build, width) in PANELS.items():
        if flag in sys.argv:
            clips.run_panel(app_id, build, width, clips.BROWSER_CSS + TOOLS_CSS)
            break
