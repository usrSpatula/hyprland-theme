#!/usr/bin/env python3
"""Overview: every desktop and every window on one screen (Super+Tab).

Each desktop is drawn as a small copy of itself, with its windows where they really are and
showing what they really look like, including the ones on desktops you can't see right now.
Click a window to go to it, click a desktop's empty space to go to that desktop, or press Esc.
Arrow keys move between windows, Enter goes to the highlighted one, and 1-9 jump to a desktop.

It stays running between uses (see ~/.local/bin/panel), so it opens at once and remembers
the last pictures of your windows while it takes new ones.
"""
import importlib.util
import json
import math
import subprocess
import threading
from pathlib import Path

HERE = Path(__file__).resolve().parent


def load_control_center():
    # For the shared pieces: staying resident, and the interface sounds
    spec = importlib.util.spec_from_file_location("control_center", HERE / "control-center.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


cc = load_control_center()
import gi  # noqa: E402
gi.require_version("PangoCairo", "1.0")
from gi.repository import Gdk, GdkPixbuf, GLib, Gtk, GtkLayerShell, Pango, PangoCairo  # noqa: E402

WALLPAPER = Path.home() / ".config" / "hypr" / "wallpaper.jpg"
GAP = 34              # between desktops, and around the whole grid
LABEL_HEIGHT = 34     # room under each desktop for its name
MAX_COLUMNS = 3
CAPTURE_SCALE = 0.5   # window pictures are taken at half size, which is plenty for a thumbnail
CAPTURES_AT_ONCE = 4

pictures = {}         # window address -> its last picture (a cairo surface), kept between openings
wallpaper = {}        # (width, height) -> the wallpaper scaled to a desktop card


def hyprctl(*arguments):
    try:
        return json.loads(subprocess.run(["hyprctl", "-j", *arguments], capture_output=True, text=True).stdout)
    except (OSError, ValueError):
        return []


def dispatch(action):
    subprocess.run(["hyprctl", "dispatch", action], capture_output=True)


def rounded(cr, x, y, width, height, radius):
    radius = min(radius, width / 2, height / 2)
    cr.new_sub_path()
    cr.arc(x + width - radius, y + radius, radius, -math.pi / 2, 0)
    cr.arc(x + width - radius, y + height - radius, radius, 0, math.pi / 2)
    cr.arc(x + radius, y + height - radius, radius, math.pi / 2, math.pi)
    cr.arc(x + radius, y + radius, radius, math.pi, 3 * math.pi / 2)
    cr.close_path()


def text(cr, words, x, y, width, size, weight=500, alpha=1.0, align=Pango.Alignment.CENTER):
    layout = PangoCairo.create_layout(cr)
    font = Pango.FontDescription("Inter")
    font.set_absolute_size(size * Pango.SCALE)
    font.set_weight(weight)
    layout.set_font_description(font)
    layout.set_width(int(width * Pango.SCALE))
    layout.set_alignment(align)
    layout.set_ellipsize(Pango.EllipsizeMode.END)
    layout.set_text(words, -1)
    cr.set_source_rgba(1, 1, 1, alpha)
    cr.move_to(x, y)
    PangoCairo.show_layout(cr, layout)


def read_desktops():
    """The desktops that exist, in order, each with its monitor and its windows (back to front)."""
    monitors = {monitor["id"]: monitor for monitor in hyprctl("monitors")}
    desktops = {}
    for workspace in hyprctl("workspaces"):
        if workspace["id"] > 0 and workspace["monitorID"] in monitors:
            desktops[workspace["id"]] = {"id": workspace["id"], "monitor": monitors[workspace["monitorID"]],
                                         "windows": []}
    for window in hyprctl("clients"):
        desktop = desktops.get(window["workspace"]["id"])
        if desktop and window["mapped"] and not window["hidden"]:
            desktop["windows"].append(window)
    for desktop in desktops.values():
        # The window used most recently is drawn last, on top; floating ones sit above tiled ones
        desktop["windows"].sort(key=lambda w: (w["floating"], -w["focusHistoryID"]))
    active = {monitor["activeWorkspace"]["id"] for monitor in monitors.values() if monitor["focused"]}
    return [desktops[number] for number in sorted(desktops)], active


def open_overview(app, on_closed):
    desktops, active = read_desktops()
    state = {"spots": [], "cards": [], "current": None, "closing": False, "stale": False}
    slots = threading.Semaphore(CAPTURES_AT_ONCE)

    window = Gtk.ApplicationWindow(application=app)
    window.set_visual(window.get_screen().get_rgba_visual())
    window.set_app_paintable(True)
    GtkLayerShell.init_for_window(window)
    GtkLayerShell.set_namespace(window, "overview")
    GtkLayerShell.set_layer(window, GtkLayerShell.Layer.OVERLAY)
    GtkLayerShell.set_exclusive_zone(window, -1)
    GtkLayerShell.set_keyboard_mode(window, GtkLayerShell.KeyboardMode.EXCLUSIVE)
    for edge in (GtkLayerShell.Edge.TOP, GtkLayerShell.Edge.BOTTOM,
                 GtkLayerShell.Edge.LEFT, GtkLayerShell.Edge.RIGHT):
        GtkLayerShell.set_anchor(window, edge, True)
    area = Gtk.DrawingArea()
    window.add(area)

    def close(*_):
        if not state["closing"]:
            state["closing"] = True
            cc.sound("close")
            window.destroy()

    def go(spot):
        """Goes to a window, or to a desktop if its empty space was picked."""
        if spot.get("window"):
            dispatch(f'hl.dsp.focus({{ window = "address:{spot["window"]["address"]}" }})')
        else:
            dispatch(f'hl.dsp.focus({{ workspace = {spot["desktop"]} }})')
        close()

    def capture(address, stable_id):
        with slots:
            if state["closing"]:
                return
            try:
                png = subprocess.run(["grim", "-T", str(stable_id), "-s", str(CAPTURE_SCALE), "-l", "0", "-"],
                                     capture_output=True, timeout=5).stdout
                loader = GdkPixbuf.PixbufLoader()
                loader.write(png)
                loader.close()
                pixbuf = loader.get_pixbuf()
            except (OSError, GLib.Error, subprocess.TimeoutExpired):
                return
        if pixbuf:
            def keep():
                pictures[address] = Gdk.cairo_surface_create_from_pixbuf(pixbuf, 1, None)
                if not state["closing"]:
                    area.queue_draw()
                return False
            GLib.idle_add(keep)

    def layout(width, height):
        """Works out where each desktop card and each window in it goes."""
        count = max(len(desktops), 1)
        columns = min(count, MAX_COLUMNS)
        lines = math.ceil(count / columns)
        card_width = min((width - GAP * (columns + 1)) / columns,
                         ((height - GAP * 2) / lines - LABEL_HEIGHT - GAP) * 16 / 9)
        card_height = card_width * 9 / 16
        grid_height = lines * (card_height + LABEL_HEIGHT) + (lines - 1) * GAP
        top = (height - grid_height) / 2
        cards, spots = [], []
        for index, desktop in enumerate(desktops):
            line, column = divmod(index, columns)
            in_line = min(columns, count - line * columns)   # a short last line is centred
            left = (width - in_line * card_width - (in_line - 1) * GAP) / 2
            x = left + column * (card_width + GAP)
            y = top + line * (card_height + LABEL_HEIGHT + GAP)
            monitor = desktop["monitor"]
            scale = card_width / monitor["width"]
            cards.append({"desktop": desktop, "x": x, "y": y, "w": card_width, "h": card_height})
            spots.append({"desktop": desktop["id"], "x": x, "y": y, "w": card_width, "h": card_height})
            for item in desktop["windows"]:
                wx = x + (item["at"][0] - monitor["x"]) * scale
                wy = y + (item["at"][1] - monitor["y"]) * scale
                ww, wh = item["size"][0] * scale, item["size"][1] * scale
                # Kept inside its desktop, in case a window hangs off the edge of the screen
                wx, wy = max(wx, x), max(wy, y)
                ww, wh = min(ww, x + card_width - wx), min(wh, y + card_height - wy)
                if ww > 4 and wh > 4:
                    spots.append({"desktop": desktop["id"], "window": item, "x": wx, "y": wy, "w": ww, "h": wh})
        state["cards"], state["spots"] = cards, spots

    def card_wallpaper(cr, card):
        size = (round(card["w"]), round(card["h"]))
        if size not in wallpaper:
            try:
                picture = GdkPixbuf.Pixbuf.new_from_file_at_scale(str(WALLPAPER), size[0] * 2, size[1] * 2, False)
                wallpaper.clear()
                wallpaper[size] = Gdk.cairo_surface_create_from_pixbuf(
                    picture.scale_simple(size[0], size[1], GdkPixbuf.InterpType.BILINEAR), 1, None)
            except GLib.Error:
                wallpaper[size] = None
        if wallpaper[size]:
            cr.set_source_surface(wallpaper[size], card["x"], card["y"])
            cr.paint()
            cr.set_source_rgba(0, 0, 0, 0.25)
        else:
            cr.set_source_rgba(1, 1, 1, 0.07)
        cr.paint()

    def draw(_area, cr):
        width, height = area.get_allocated_width(), area.get_allocated_height()
        layout(width, height)
        cr.set_source_rgba(0.03, 0.03, 0.05, 0.55)
        cr.paint()
        current = state["current"]

        for card in state["cards"]:
            desktop = card["desktop"]
            cr.save()
            rounded(cr, card["x"], card["y"], card["w"], card["h"], 14)
            cr.clip()
            card_wallpaper(cr, card)
            for spot in state["spots"]:
                if spot["desktop"] != desktop["id"] or "window" not in spot:
                    continue
                cr.save()
                rounded(cr, spot["x"], spot["y"], spot["w"], spot["h"], 6)
                cr.clip()
                picture = pictures.get(spot["window"]["address"])
                if picture:
                    cr.translate(spot["x"], spot["y"])
                    cr.scale(spot["w"] / picture.get_width(), spot["h"] / picture.get_height())
                    cr.set_source_surface(picture, 0, 0)
                    cr.paint()
                else:
                    # Until its picture arrives: a plain panel with the app's name
                    cr.set_source_rgba(0.16, 0.16, 0.19, 0.96)
                    cr.paint()
                    text(cr, spot["window"]["class"], spot["x"] + 6, spot["y"] + spot["h"] / 2 - 8,
                         spot["w"] - 12, 12, 600, 0.8)
                cr.restore()
                rounded(cr, spot["x"] + 0.5, spot["y"] + 0.5, spot["w"] - 1, spot["h"] - 1, 6)
                if spot is current:
                    cr.set_source_rgba(1, 1, 1, 1)
                    cr.set_line_width(3)
                else:
                    cr.set_source_rgba(1, 1, 1, 0.16)
                    cr.set_line_width(1)
                cr.stroke()
            cr.restore()

            here = desktop["id"] in active
            chosen = current is not None and current["desktop"] == desktop["id"] and "window" not in current
            rounded(cr, card["x"], card["y"], card["w"], card["h"], 14)
            cr.set_source_rgba(1, 1, 1, 1 if chosen else 0.75 if here else 0.2)
            cr.set_line_width(3 if chosen else 2 if here else 1)
            cr.stroke()

            name = f"Desktop {desktop['id']}"
            if current is not None and current["desktop"] == desktop["id"] and "window" in current:
                name += f"   ·   {current['window']['title'] or current['window']['class']}"
            elif not desktop["windows"]:
                name += "   ·   empty"
            text(cr, name, card["x"], card["y"] + card["h"] + 9, card["w"], 14, 600,
                 1 if here or (current is not None and current["desktop"] == desktop["id"]) else 0.7)

        text(cr, "Click a window to go to it   ·   arrows and Enter work too   ·   Esc closes",
             0, height - 34, width, 12, 500, 0.5)
        return True

    def spot_at(x, y):
        # The last match is the one drawn on top
        found = None
        for spot in state["spots"]:
            if spot["x"] <= x <= spot["x"] + spot["w"] and spot["y"] <= y <= spot["y"] + spot["h"]:
                found = spot
        return found

    def select(spot):
        if spot is not state["current"]:
            state["current"] = spot
            area.queue_draw()

    def on_click(_area, event):
        spot = spot_at(event.x, event.y)
        if spot and event.button == 1:
            go(spot)
        else:
            close()
        return True

    def on_key(_window, event):
        key = event.keyval
        windows = [spot for spot in state["spots"] if "window" in spot]
        if key == Gdk.KEY_Escape:
            close()
        elif key in (Gdk.KEY_Return, Gdk.KEY_KP_Enter, Gdk.KEY_space) and state["current"]:
            go(state["current"])
        elif key in (Gdk.KEY_Right, Gdk.KEY_Down, Gdk.KEY_Tab, Gdk.KEY_Left, Gdk.KEY_Up, Gdk.KEY_ISO_Left_Tab) and windows:
            step = 1 if key in (Gdk.KEY_Right, Gdk.KEY_Down, Gdk.KEY_Tab) else -1
            now = windows.index(state["current"]) if state["current"] in windows else (-1 if step > 0 else 0)
            select(windows[(now + step) % len(windows)])
        elif Gdk.KEY_1 <= key <= Gdk.KEY_9:
            number = key - Gdk.KEY_0
            if any(desktop["id"] == number for desktop in desktops):
                go({"desktop": number})
        return True

    area.add_events(Gdk.EventMask.POINTER_MOTION_MASK | Gdk.EventMask.BUTTON_PRESS_MASK)
    area.connect("draw", draw)
    area.connect("motion-notify-event", lambda _a, event: select(spot_at(event.x, event.y)))
    area.connect("button-press-event", on_click)
    window.connect("key-press-event", on_key)
    window.connect("destroy", lambda *_: on_closed())

    # Pictures of windows that have closed since last time are dropped
    alive = {item["address"]: item for desktop in desktops for item in desktop["windows"]}
    for address in list(pictures):
        if address not in alive:
            del pictures[address]
    for address, item in alive.items():
        threading.Thread(target=capture, args=(address, item["stableId"]), daemon=True).start()

    cc.sound("open")
    window.show_all()
    return close


if __name__ == "__main__":
    cc.run_resident("local.waybar.Overview", open_overview)
