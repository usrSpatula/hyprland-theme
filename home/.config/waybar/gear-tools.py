#!/usr/bin/env python3
# Gear tools: four small glass panels in the gear drawer, with the same look as the clips panel.
#   gear-tools.py --clipboard     The last things you copied (text and pictures). Click one to
#                                 copy it again, pin the ones you reuse, search, or clear it all.
#                                 History comes from cliphist, which Hyprland starts at login.
#   gear-tools.py --screenshots   Your screenshots, newest first. Click to open, copy or trash one,
#                                 or take a new one.
#   gear-tools.py --notes         A scratchpad that saves as you type.
#   gear-tools.py --power         Lock, sleep, log out, restart and shut down. The last three
#                                 need a second click.
import datetime
import hashlib
import importlib.util
import subprocess
import sys
import threading
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = Path.home() / ".local" / "share" / "gear-tools"
CACHE = Path.home() / ".cache" / "gear-tools"
PINS = DATA / "pins"
NOTES = DATA / "notes.txt"
SCREENSHOTS = Path.home() / "Pictures" / "screenshot"   # Where ~/.config/hypr/screenshot.sh saves
SCREENSHOT_SCRIPT = Path.home() / ".config" / "hypr" / "screenshot.sh"

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
}

TOOLS_CSS = b"""
.tool-icon { border-radius: 11px; min-width: 40px; min-height: 40px; font-size: 17px; }
.tool-icon.clipboard { background-color: #0a84ff; }
.tool-icon.images { background-color: #30d158; }
.tool-icon.note { background-color: #ffd60a; color: #1c1c1e; }
.tool-icon.power { background-color: #ff453a; }
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

    def header(self, icon, title):
        """The tile at the top: a coloured icon, a title, a line under it, and room for buttons."""
        Gtk = self.Gtk
        header = self.cc.css_classes(Gtk.Box(spacing=11), "tile")
        badge = self.cc.icon_label(icon, "tool-icon")
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
    threading.Thread(target=load, daemon=True).start()
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
    "--power": ("local.waybar.Power", build_power, 240),
}

if __name__ == "__main__":
    for flag, (app_id, build, width) in PANELS.items():
        if flag in sys.argv:
            clips.run_panel(app_id, build, width, clips.BROWSER_CSS + TOOLS_CSS)
            break
