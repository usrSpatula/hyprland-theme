#!/usr/bin/env python3
"""Overview: every desktop and every window on one screen (Super+Tab).

Each desktop is drawn as a small copy of itself, with its windows where they really are and
showing what they really look like, including the ones on desktops you can't see right now.
The pictures are live: windows keep updating while the overview is open.

Click a window to go to it, click a desktop's empty space to go to that desktop, or press Esc.
Arrow keys move between windows, Enter goes to the highlighted one, and 1-9 jump to a desktop.
The desktops fly in one after another, and the one under the mouse comes forward, leans with
the mouse and catches the light, while the others step back.

It stays running between uses (see ~/.local/bin/panel), so it opens at once and remembers
the last pictures of your windows while it takes new ones.
"""
import importlib.util
import json
import math
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent


def load_control_center():
    # For the shared pieces: staying resident, and the interface sounds
    spec = importlib.util.spec_from_file_location("control_center", HERE / "control-center.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


cc = load_control_center()
import cairo  # noqa: E402
import gi  # noqa: E402
gi.require_version("PangoCairo", "1.0")
from gi.repository import Gdk, GdkPixbuf, GLib, Gtk, GtkLayerShell, Pango, PangoCairo  # noqa: E402

WALLPAPER = Path.home() / ".config" / "hypr" / "wallpaper.jpg"
GAP = 34              # between desktops, and around the whole grid
LABEL_HEIGHT = 34     # room under each desktop for its name
MAX_COLUMNS = 3
CARD_RADIUS = 18
WINDOW_RADIUS = 8
ENTER_SECONDS = 0.5   # how long a desktop takes to fly in
ENTER_STAGGER = 0.04  # and how long each one waits for the one before it
LIVE_SECONDS = 0.07   # how often a window you can see (or are pointing at) is pictured again
IDLE_SECONDS = 1.5    # and one on a hidden desktop, which isn't drawing anything new anyway
CAMERAS = 3           # how many windows are pictured at the same time
STILL_MARGIN = 72     # room around a desktop's kept picture for its shadow

# Kept between openings, so the overview has something to show the instant it opens
pictures = {}         # window address -> (width, height, its picture at that size)
versions = {}         # window address -> a number that goes up with each new picture
stills = {}           # desktop number -> (what it showed, a picture of that desktop at rest)
shades = {}           # (width, height, radius, blur) -> a soft shadow of that shape
wallpaper = {}        # (width, height) -> the wallpaper scaled to a desktop card
lights = {}           # the light that follows the mouse, at the size it was last drawn
backdrops = {}        # (width, height) -> the dark backdrop behind everything


def hyprctl(*arguments):
    try:
        return json.loads(subprocess.run(["hyprctl", "-j", *arguments], capture_output=True, text=True).stdout)
    except (OSError, ValueError):
        return []


def dispatch(action):
    subprocess.Popen(["hyprctl", "dispatch", action], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def rounded(cr, x, y, width, height, radius):
    radius = max(0, min(radius, width / 2, height / 2))
    cr.new_sub_path()
    cr.arc(x + width - radius, y + radius, radius, -math.pi / 2, 0)
    cr.arc(x + width - radius, y + height - radius, radius, 0, math.pi / 2)
    cr.arc(x + radius, y + height - radius, radius, math.pi / 2, math.pi)
    cr.arc(x + radius, y + radius, radius, math.pi, 3 * math.pi / 2)
    cr.close_path()


def text(cr, words, x, y, width, size, weight=500, alpha=1.0):
    layout = PangoCairo.create_layout(cr)
    font = Pango.FontDescription("Inter")
    font.set_absolute_size(size * Pango.SCALE)
    font.set_weight(weight)
    layout.set_font_description(font)
    layout.set_width(int(width * Pango.SCALE))
    layout.set_alignment(Pango.Alignment.CENTER)
    layout.set_ellipsize(Pango.EllipsizeMode.END)
    layout.set_text(words, -1)
    cr.set_source_rgba(1, 1, 1, alpha)
    cr.move_to(x, y)
    PangoCairo.show_layout(cr, layout)


def shade(width, height, radius, blur):
    """A soft shadow for a rounded box of this size, drawn once and kept. It's built from many
    faint layers that shrink towards the box, so it fades out smoothly with no visible steps."""
    key = (round(width), round(height), radius, blur)
    if key not in shades:
        if len(shades) > 60:
            shades.clear()
        reach = blur * 2
        picture = cairo.ImageSurface(cairo.FORMAT_ARGB32, key[0] + reach * 2, key[1] + reach * 2)
        pen = cairo.Context(picture)
        layers = 40
        for layer in range(layers):
            # Wide and barely there at first, then tighter and tighter around the box
            grow = reach * (1 - layer / (layers - 1)) ** 1.7
            rounded(pen, reach - grow, reach - grow, key[0] + grow * 2, key[1] + grow * 2, radius + grow)
            pen.set_source_rgba(0, 0, 0, 0.034)
            pen.fill()
        shades[key] = (reach, picture)
    return shades[key]


def cast(cr, x, y, width, height, radius, blur, strength, drop, quick=False):
    """Stamps a shadow under a box. drop is how far below the box it falls."""
    reach, picture = shade(width, height, radius, blur)
    cr.set_source_surface(picture, round(x) - reach, round(y) - reach + drop)
    if quick:
        cr.get_source().set_filter(cairo.FILTER_FAST)
    cr.paint_with_alpha(min(1.0, strength))


def shrink(data, width, height):
    """Turns a full-size picture of a window (as grim's PPM output) into a small cairo surface
    of the given size. Runs in a camera thread, so the screen never waits for it."""
    try:
        marker, size, _depth, pixels = data.split(b"\n", 3)
        full_width, full_height = map(int, size.split())
    except ValueError:
        return None
    if marker != b"P6" or len(pixels) < full_width * full_height * 3:
        return None
    picture = GdkPixbuf.Pixbuf.new_from_bytes(GLib.Bytes.new(pixels), GdkPixbuf.Colorspace.RGB, False, 8,
                                              full_width, full_height, full_width * 3)
    small = picture.scale_simple(max(1, round(width)), max(1, round(height)), GdkPixbuf.InterpType.BILINEAR)
    return Gdk.cairo_surface_create_from_pixbuf(small, 1, None)


def read_desktops():
    """The desktops that exist, in order, each with its monitor and its windows (back to
    front); the desktop you're on; and the desktops showing on a monitor right now."""
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
    showing = {monitor["activeWorkspace"]["id"] for monitor in monitors.values()}
    return [desktops[number] for number in sorted(desktops)], active, showing


def open_overview(app, on_closed):
    desktops, active, showing = read_desktops()
    state = {"spots": [], "cards": [], "current": None, "closing": False, "size": None}
    # Everything that moves has a number that chases a target a little each frame:
    #   lift[key]   how far a desktop or window has come forward (0 at rest, 1 under the mouse);
    #               it's on a spring, so it eases in and settles
    #   tilt        which way the desktop under the mouse leans, from where the mouse is on it
    motion = {"lift": {}, "speed": {}, "tilt": [0.0, 0.0], "tilt_speed": [0.0, 0.0], "pointer": None,
              "began": None, "last": None, "ticking": False, "ticked": 0.0}
    cameras = ThreadPoolExecutor(max_workers=CAMERAS)
    filming = set()

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
            cameras.shutdown(wait=False, cancel_futures=True)
            cc.sound("close")
            window.destroy()

    def go(spot):
        """Goes to a window, or to a desktop if its empty space was picked."""
        if spot.get("window"):
            dispatch(f'hl.dsp.focus({{ window = "address:{spot["window"]["address"]}" }})')
        else:
            dispatch(f'hl.dsp.focus({{ workspace = {spot["desktop"]} }})')
        close()

    # ---- Live pictures ----

    def film(spot):
        """Takes one picture of a window and shrinks it to the size it's drawn at."""
        item = spot["window"]
        address = item["address"]
        small = None
        try:
            # At full size grim is quick and the shrinking is done here; asked for a smaller
            # picture it shrinks carefully itself, which takes several times as long
            shot = subprocess.run(["grim", "-T", str(item["stableId"]), "-s", "1", "-t", "ppm", "-"],
                                  capture_output=True, timeout=5).stdout
            if not state["closing"]:
                small = shrink(shot, spot["w"], spot["h"])
        except (OSError, subprocess.TimeoutExpired, ValueError, GLib.Error):
            pass

        def keep():
            filming.discard(address)
            if small is not None:
                pictures[address] = (small.get_width(), small.get_height(), small)
                versions[address] = versions.get(address, 0) + 1
                if not state["closing"] and not motion["ticking"]:
                    area.queue_draw()
            return False

        GLib.idle_add(keep)

    def direct():
        """Keeps the cameras busy for as long as the overview is open: windows you can see, or
        whose desktop you're pointing at, many times a second; the rest now and then."""
        due = {}
        while not state["closing"]:
            now = time.monotonic()
            current = state["current"]
            pointed = current["desktop"] if current else None
            for spot in list(state["spots"]):
                if "window" not in spot:
                    continue
                address = spot["window"]["address"]
                if address in filming or now < due.get(address, 0):
                    continue
                lively = spot["desktop"] in showing or spot["desktop"] == pointed
                due[address] = now + (LIVE_SECONDS if lively else IDLE_SECONDS)
                filming.add(address)
                try:
                    cameras.submit(film, spot)
                except RuntimeError:
                    return   # closed in the meantime
            time.sleep(0.015)

    # ---- Where things go ----

    def layout(width, height):
        """Works out where each desktop card and each window in it goes."""
        count = max(len(desktops), 1)
        columns = min(count, MAX_COLUMNS)
        lines = math.ceil(count / columns)
        card_width = min((width - GAP * (columns + 1)) / columns,
                         ((height - GAP * 2) / lines - LABEL_HEIGHT - GAP) * 16 / 9)
        # Whole pixels: a picture stamped on a half pixel has to be blended, which is far slower
        card_width = math.floor(card_width / 16) * 16
        card_height = card_width * 9 // 16
        grid_height = lines * (card_height + LABEL_HEIGHT) + (lines - 1) * GAP
        top = (height - grid_height) / 2
        cards, spots = [], []
        for index, desktop in enumerate(desktops):
            line, column = divmod(index, columns)
            in_line = min(columns, count - line * columns)   # a short last line is centred
            left = (width - in_line * card_width - (in_line - 1) * GAP) / 2
            x = round(left + column * (card_width + GAP))
            y = round(top + line * (card_height + LABEL_HEIGHT + GAP))
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
                    spots.append({"desktop": desktop["id"], "window": item,
                                  "x": round(wx), "y": round(wy), "w": round(ww), "h": round(wh)})
        state["cards"], state["spots"] = cards, spots

    def windows_of(desktop):
        return [spot for spot in state["spots"] if spot["desktop"] == desktop["id"] and "window" in spot]

    # ---- Motion ----

    def lift_target(key):
        current = state["current"]
        if current is None:
            return 0.0
        if isinstance(key, int):
            return 1.0 if current["desktop"] == key else 0.0
        return 1.0 if current.get("window") and current["window"]["address"] == key else 0.0

    def step(_area, clock):
        now = clock.get_frame_time() / 1e6
        if motion["began"] is None:
            motion["began"] = now
        elapsed = min(now - (motion["last"] or now), 0.03)
        motion["last"] = now
        moving = now - motion["began"] < ENTER_SECONDS + ENTER_STAGGER * len(desktops)

        keys = [desktop["id"] for desktop in desktops] + [
            spot["window"]["address"] for spot in state["spots"] if "window" in spot]
        for key in keys:
            value, speed = motion["lift"].get(key, 0.0), motion["speed"].get(key, 0.0)
            target = lift_target(key)
            speed += ((target - value) * 300 - speed * 27) * elapsed
            value += speed * elapsed
            if abs(target - value) < 0.002 and abs(speed) < 0.02:
                value, speed = target, 0.0
            else:
                moving = True
            motion["lift"][key], motion["speed"][key] = value, speed

        # The lean glides after the mouse, and eases back to flat when it leaves
        wanted = [0.0, 0.0]
        current, pointer = state["current"], motion["pointer"]
        if current is not None and pointer:
            card = next((c for c in state["cards"] if c["desktop"]["id"] == current["desktop"]), None)
            if card:
                wanted = [max(-1.0, min(1.0, (pointer[0] - card["x"]) / card["w"] * 2 - 1)),
                          max(-1.0, min(1.0, (pointer[1] - card["y"]) / card["h"] * 2 - 1))]
        for axis in (0, 1):
            gap = wanted[axis] - motion["tilt"][axis]
            motion["tilt_speed"][axis] += (gap * 190 - motion["tilt_speed"][axis] * 26) * elapsed
            motion["tilt"][axis] += motion["tilt_speed"][axis] * elapsed
            if abs(gap) > 0.003 or abs(motion["tilt_speed"][axis]) > 0.02:
                moving = True

        if not moving:
            # The frame it comes to rest on is drawn the careful way (see quick)
            motion["ticking"], motion["last"] = False, None
        area.queue_draw()
        return moving

    def animate():
        if not motion["ticking"] and not state["closing"]:
            motion["ticking"] = True
            area.add_tick_callback(step)

    def arrival(index):
        """How far a desktop has flown in: (place 0..1 with a small bounce, opacity 0..1)."""
        if motion["began"] is None:
            return 0.0, 0.0
        if motion["last"] is None:
            return 1.0, 1.0
        t = (motion["last"] - motion["began"] - index * ENTER_STAGGER) / ENTER_SECONDS
        if t <= 0:
            return 0.0, 0.0
        if t >= 1:
            return 1.0, 1.0
        return 1 - math.exp(-7 * t) * math.cos(7 * t), min(1.0, t * 4)

    def quick(cr):
        # While things are moving, pictures are stretched the fast way; the frame they come to
        # rest on is drawn the careful way, so it ends sharp
        if motion["ticking"]:
            cr.get_source().set_filter(cairo.FILTER_FAST)

    # ---- Drawing ----

    def backdrop(cr, width, height):
        """Darkness behind everything, a little lighter in the middle so the edges fall away."""
        if (width, height) not in backdrops:
            backdrops.clear()
            picture = cairo.ImageSurface(cairo.FORMAT_ARGB32, width, height)
            pen = cairo.Context(picture)
            fade = cairo.RadialGradient(width / 2, height / 2, 0, width / 2, height / 2,
                                        math.hypot(width, height) / 2)
            fade.add_color_stop_rgba(0, 0.05, 0.05, 0.08, 0.50)
            fade.add_color_stop_rgba(1, 0.0, 0.0, 0.02, 0.82)
            pen.set_source(fade)
            pen.paint()
            backdrops[(width, height)] = picture
        cr.set_source_surface(backdrops[(width, height)], 0, 0)
        cr.paint()

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

    def draw_window(cr, spot, rise, lean=(0.0, 0.0)):
        """Draws one window. rise is how far it has come forward (0 at rest, 1 under the mouse):
        a risen window is bigger, casts a shadow, slides against the lean and has a bright edge."""
        item = spot["window"]
        cr.save()
        cr.translate(-lean[0] * 8 * rise, -lean[1] * 6 * rise)
        centre_x, centre_y = spot["x"] + spot["w"] / 2, spot["y"] + spot["h"] / 2
        grow = 1 + 0.06 * rise
        cr.translate(centre_x, centre_y)
        cr.scale(grow, grow)
        cr.translate(-centre_x, -centre_y)
        if rise > 0.02:
            cast(cr, spot["x"], spot["y"], spot["w"], spot["h"], WINDOW_RADIUS, 16,
                 0.9 * rise, round(3 + 8 * rise), motion["ticking"])
        cr.save()
        rounded(cr, spot["x"], spot["y"], spot["w"], spot["h"], WINDOW_RADIUS)
        cr.clip()
        kept = pictures.get(item["address"])
        if kept:
            if (kept[0], kept[1]) != (spot["w"], spot["h"]):
                # A picture from a differently sized layout: stretched until a new one arrives
                cr.translate(spot["x"], spot["y"])
                cr.scale(spot["w"] / kept[0], spot["h"] / kept[1])
                cr.set_source_surface(kept[2], 0, 0)
            else:
                cr.set_source_surface(kept[2], spot["x"], spot["y"])
            quick(cr)
            cr.paint()
        else:
            # Until its picture arrives: a plain panel with the app's name
            cr.set_source_rgba(0.16, 0.16, 0.19, 0.96)
            cr.paint()
            text(cr, item["class"], spot["x"] + 6, spot["y"] + spot["h"] / 2 - 8, spot["w"] - 12, 12, 600, 0.8)
        cr.restore()
        rounded(cr, spot["x"] + 0.5, spot["y"] + 0.5, spot["w"] - 1, spot["h"] - 1, WINDOW_RADIUS)
        cr.set_source_rgba(1, 1, 1, 0.14 + 0.86 * min(rise, 1.0))
        cr.set_line_width(1 + 1.5 * min(rise, 1.0))
        cr.stroke()
        cr.restore()

    def resting(card):
        """A desktop at rest (its shadow, wallpaper, windows and glass edge), drawn once and
        kept as a picture until one of its windows changes. Each frame the desktops are stamped
        from these, and only what's moving (the window under the mouse, the light, the glow)
        is drawn on top. That's what keeps the motion smooth."""
        desktop = card["desktop"]
        key = (card["x"], card["y"], card["w"],
               tuple(versions.get(item["address"], 0) for item in desktop["windows"]))
        kept = stills.get(desktop["id"])
        if not kept or kept[0] != key:
            still = cairo.ImageSurface(cairo.FORMAT_ARGB32, card["w"] + 2 * STILL_MARGIN,
                                       card["h"] + 2 * STILL_MARGIN)
            pen = cairo.Context(still)
            pen.translate(STILL_MARGIN - card["x"], STILL_MARGIN - card["y"])
            cast(pen, card["x"], card["y"], card["w"], card["h"], CARD_RADIUS, 30, 0.9, 12)
            pen.save()
            rounded(pen, card["x"], card["y"], card["w"], card["h"], CARD_RADIUS)
            pen.clip()
            card_wallpaper(pen, card)
            was, motion["ticking"] = motion["ticking"], False   # the kept picture is drawn carefully
            for spot in windows_of(desktop):
                draw_window(pen, spot, 0.0)
            motion["ticking"] = was
            pen.restore()
            # A glass edge: bright along the top where light would catch it, fading down the sides
            edge = cairo.LinearGradient(0, card["y"], 0, card["y"] + card["h"])
            here = desktop["id"] in active
            edge.add_color_stop_rgba(0, 1, 1, 1, 0.95 if here else 0.5)
            edge.add_color_stop_rgba(0.5, 1, 1, 1, 0.6 if here else 0.14)
            edge.add_color_stop_rgba(1, 1, 1, 1, 0.45 if here else 0.08)
            rounded(pen, card["x"] + 0.75, card["y"] + 0.75, card["w"] - 1.5, card["h"] - 1.5, CARD_RADIUS)
            pen.set_source(edge)
            pen.set_line_width(2 if here else 1.5)
            pen.stroke()
            kept = stills[desktop["id"]] = (key, still)
        return kept[1]

    def glow(reach):
        """The light that follows the mouse: a soft white spot, drawn once and then stamped."""
        if lights.get("reach") != reach:
            spot = cairo.ImageSurface(cairo.FORMAT_ARGB32, reach * 2, reach * 2)
            pen = cairo.Context(spot)
            light = cairo.RadialGradient(reach, reach, 0, reach, reach, reach)
            light.add_color_stop_rgba(0, 1, 1, 1, 0.22)
            light.add_color_stop_rgba(0.5, 1, 1, 1, 0.07)
            light.add_color_stop_rgba(1, 1, 1, 1, 0)
            pen.set_source(light)
            pen.paint()
            lights.update(reach=reach, spot=spot)
        return lights["spot"]

    def draw_card(cr, card, index, focus):
        desktop = card["desktop"]
        place, opacity = arrival(index)
        if opacity <= 0:
            return
        rise = motion["lift"].get(desktop["id"], 0.0)
        held = max(0.0, min(rise, 1.0))
        lean = motion["tilt"] if held > 0.003 else (0.0, 0.0)
        centre_x, centre_y = card["x"] + card["w"] / 2, card["y"] + card["h"] / 2
        current = state["current"]

        cr.save()
        # Flying in from below and slightly small; then, under the mouse, forward and leaning.
        # The lean is a slant of the whole card, which reads as it turning away from the mouse
        cr.translate(centre_x, centre_y + (1 - place) * 54)
        cr.transform(cairo.Matrix(1, lean[0] * 0.03 * held, lean[1] * 0.03 * held, 1, 0, 0))
        grow = (0.84 + 0.16 * place) * (1 + 0.06 * rise)
        cr.scale(grow, grow)
        cr.translate(-centre_x, -centre_y)

        if held > 0.02:
            # Coming forward, its shadow deepens, spreads and slips away from the mouse
            cast(cr, card["x"] - lean[0] * 16 * held, card["y"], card["w"], card["h"], CARD_RADIUS, 36,
                 0.85 * held, round(10 + 22 * held - lean[1] * 8 * held), motion["ticking"])
        cr.set_source_surface(resting(card), card["x"] - STILL_MARGIN, card["y"] - STILL_MARGIN)
        quick(cr)
        if opacity < 1:
            cr.paint_with_alpha(opacity)
        else:
            cr.paint()

        if held > 0.01:
            cr.save()
            rounded(cr, card["x"], card["y"], card["w"], card["h"], CARD_RADIUS)
            cr.clip()
            for spot in windows_of(desktop):
                lifted = motion["lift"].get(spot["window"]["address"], 0.0)
                if lifted > 0.01:
                    draw_window(cr, spot, lifted, lean)
            if motion["pointer"]:
                # A soft light that follows the mouse across the desktop, like a reflection
                reach = round(card["w"] * 0.7)
                cr.set_source_surface(glow(reach), round(centre_x + lean[0] * card["w"] / 2) - reach,
                                      round(centre_y + lean[1] * card["h"] / 2) - reach)
                cr.paint_with_alpha(held)
            cr.restore()
            for width, alpha in ((10, 0.08 * held), (6, 0.18 * held), (2.5, 0.95 * held)):
                rounded(cr, card["x"], card["y"], card["w"], card["h"], CARD_RADIUS)
                cr.set_source_rgba(1, 1, 1, alpha)
                cr.set_line_width(width)
                cr.stroke()
        if focus > 0.01 and held < 0.99:
            # While one desktop is forward, the others step back into the dark
            rounded(cr, card["x"], card["y"], card["w"], card["h"], CARD_RADIUS)
            cr.set_source_rgba(0, 0, 0, 0.36 * focus * (1 - held))
            cr.fill()

        here = desktop["id"] in active
        name = f"Desktop {desktop['id']}"
        if current is not None and current["desktop"] == desktop["id"] and "window" in current:
            name += f"   ·   {current['window']['title'] or current['window']['class']}"
        elif not desktop["windows"]:
            name += "   ·   empty"
        text(cr, name, card["x"], card["y"] + card["h"] + 12, card["w"], 14, 600,
             (0.6 + 0.4 * max(held, 1.0 * here)) * opacity)
        cr.restore()

    def draw(_area, cr):
        width, height = area.get_allocated_width(), area.get_allocated_height()
        if state["size"] != (width, height):
            state["size"] = (width, height)
            layout(width, height)
            if len(state["spots"]) and not state.get("directing"):
                state["directing"] = True
                threading.Thread(target=direct, daemon=True).start()
        backdrop(cr, width, height)
        # How strongly any desktop is being held forward, which dims the rest
        focus = max([0.0] + [min(motion["lift"].get(card["desktop"]["id"], 0.0), 1.0) for card in state["cards"]])
        # The one under the mouse is drawn last, on top of its neighbours
        order = sorted(enumerate(state["cards"]),
                       key=lambda pair: motion["lift"].get(pair[1]["desktop"]["id"], 0.0))
        for index, card in order:
            draw_card(cr, card, index, focus)
        text(cr, "Click a window to go to it   ·   arrows and Enter work too   ·   Esc closes",
             0, height - 34, width, 12, 500, 0.5)
        return True

    # ---- Mouse and keyboard ----

    def spot_at(x, y):
        # The last match is the one drawn on top
        found = None
        for spot in state["spots"]:
            if spot["x"] <= x <= spot["x"] + spot["w"] and spot["y"] <= y <= spot["y"] + spot["h"]:
                found = spot
        return found

    def select(spot):
        if spot is not state["current"]:
            # A soft tick as the highlight moves on to another window
            now = time.monotonic()
            if spot and spot.get("window") and now - motion["ticked"] > 0.09:
                motion["ticked"] = now
                cc.sound("volume")
            state["current"] = spot
        animate()

    def on_motion(_area, event):
        motion["pointer"] = (event.x, event.y)
        select(spot_at(event.x, event.y))
        return True

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
        elif key in (Gdk.KEY_Right, Gdk.KEY_Down, Gdk.KEY_Tab, Gdk.KEY_Left, Gdk.KEY_Up,
                     Gdk.KEY_ISO_Left_Tab) and windows:
            motion["pointer"] = None   # the keyboard has no position to lean towards
            step_by = 1 if key in (Gdk.KEY_Right, Gdk.KEY_Down, Gdk.KEY_Tab) else -1
            now = windows.index(state["current"]) if state["current"] in windows else (-1 if step_by > 0 else 0)
            select(windows[(now + step_by) % len(windows)])
        elif Gdk.KEY_1 <= key <= Gdk.KEY_9:
            number = key - Gdk.KEY_0
            if any(desktop["id"] == number for desktop in desktops):
                go({"desktop": number})
        return True

    area.add_events(Gdk.EventMask.POINTER_MOTION_MASK | Gdk.EventMask.BUTTON_PRESS_MASK)
    area.connect("draw", draw)
    area.connect("motion-notify-event", on_motion)
    area.connect("button-press-event", on_click)
    window.connect("key-press-event", on_key)
    window.connect("destroy", lambda *_: (state.update(closing=True), on_closed()))

    # Pictures of windows that have closed since last time are dropped
    alive = {item["address"] for desktop in desktops for item in desktop["windows"]}
    for address in list(pictures):
        if address not in alive:
            del pictures[address]
            versions.pop(address, None)

    cc.sound("open")
    window.connect("map-event", lambda *_: animate())
    window.show_all()
    return close


if __name__ == "__main__":
    cc.run_resident("local.waybar.Overview", open_overview)
