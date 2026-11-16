#!/usr/bin/env python3
# The black notch: it swells a little under the mouse, and a click grows it into a square menu of audio controls.
# Waybar starts this once per bar, so extra copies wait on a lock and only one runs.
import fcntl
import math
import os
import signal
import subprocess
import threading
import urllib.parse
import urllib.request
from pathlib import Path

RUNTIME_DIR = Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp"))

if __name__ == "__main__":
    # The extra copies wait here, before GTK is loaded, so a waiting copy costs next to nothing
    LOCK = (RUNTIME_DIR / "waybar-notch.lock").open("w")
    fcntl.flock(LOCK, fcntl.LOCK_EX)

import gi

gi.require_version("Gdk", "3.0")
gi.require_version("Gtk", "3.0")
gi.require_version("GtkLayerShell", "0.1")
from gi.repository import Gdk, GLib, Gtk, GtkLayerShell

IDLE_SIZE = (142, 28)
OPEN_SIZE = (232, 236)
SHOULDER_WIDTH = 10
SHADOW_ROOM = 28
SPRING_SPEED = 30
CLOSE_DELAY_MS = 120
FRAME_SECONDS = 1 / 60
MAX_STEP_SECONDS = 1 / 30
CONTENT_FADE_START = 0.4
PULSE_GROW = (26, 7)    # px the closed notch swells by when something happens
PULSE_SECONDS = 0.75
HOVER_GROW = (14, 4)    # px the closed notch swells by under the mouse, to show it can be clicked
MAX_ART_BYTES = 5_000_000
MUSIC_ICON = ""
PLAYER = ["playerctl", "-p", "playerctld"]
PREVIOUS_ICON = ""
PLAY_ICON = ""
PAUSE_ICON = ""
NEXT_ICON = ""

CSS = b"""
* { font-family: "Inter", "Noto Sans", sans-serif; }
window { background-color: transparent; }
.notch {
    background-color: #000000;
    border-radius: 0 0 11px 11px;
    color: #ffffff;
    transition: border-radius 120ms ease;
}
scrolledwindow, viewport {
    background-color: transparent;
    border: none;
}
.notch.open {
    border-radius: 0 0 26px 26px;
    box-shadow: 0 6px 16px rgba(0, 0, 0, 0.4);
}
.shoulder {
    min-width: 10px;
    min-height: 10px;
    background-repeat: no-repeat;
    background-size: 10px 10px;
}
.shoulder.left {
    background-image: radial-gradient(circle 10px at 0% 100%, transparent 92%, #000000 100%);
}
.shoulder.right {
    background-image: radial-gradient(circle 10px at 100% 100%, transparent 92%, #000000 100%);
}
.track-art {
    min-width: 128px;
    min-height: 128px;
    border-radius: 64px;
    background-color: #2c2c2e;
    background-size: cover;
    background-position: center;
    background-repeat: no-repeat;
}
.art-icon {
    color: rgba(255, 255, 255, 0.55);
    font-family: "Font Awesome 7 Free Solid";
    font-weight: 900;
    font-size: 38px;
}
button {
    background-image: none;
    background-color: transparent;
    border: none;
    box-shadow: none;
    outline-width: 0;
    text-shadow: none;
    color: #ffffff;
    min-width: 40px;
    min-height: 40px;
    padding: 0;
    border-radius: 20px;
    font-family: "Font Awesome 7 Free Solid";
    font-weight: 900;
    font-size: 15px;
}
button:hover { background-color: rgba(255, 255, 255, 0.14); }
button.play {
    background-color: #ffffff;
    color: #000000;
    min-width: 48px;
    min-height: 48px;
    border-radius: 24px;
    font-size: 17px;
}
button.play:hover { background-color: rgba(255, 255, 255, 0.82); }
"""

notches = []
art = {"url": None, "provider": None}


def run_player(command):
    subprocess.Popen(PLAYER + [command], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def follow_player(arguments, on_line):
    # Reads playerctl's live updates in the background and hands them to the main thread
    def read():
        process = subprocess.Popen(
            PLAYER + ["-F"] + arguments, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True
        )
        for line in process.stdout:
            GLib.idle_add(on_line, line.rstrip("\n"))

    threading.Thread(target=read, daemon=True).start()


def apply_art(path):
    # A screen-wide style sets the cover, because CSS is what can clip an image to a circle
    screen = Gdk.Screen.get_default()
    if art["provider"]:
        Gtk.StyleContext.remove_provider_for_screen(screen, art["provider"])
        art["provider"] = None

    if path:
        provider = Gtk.CssProvider()
        try:
            uri = Path(path).as_uri()
            provider.load_from_data(f'.track-art {{ background-image: url("{uri}"); }}'.encode())
        except (GLib.Error, ValueError):
            path = None
        else:
            Gtk.StyleContext.add_provider_for_screen(
                screen, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1
            )
            art["provider"] = provider

    for notch in notches:
        notch.art_icon.set_visible(not path)


def download_art(url):
    target = RUNTIME_DIR / "waybar-notch-art"
    try:
        with urllib.request.urlopen(url, timeout=5) as response:
            data = response.read(MAX_ART_BYTES)
        temporary = target.with_suffix(".tmp")
        temporary.write_bytes(data)
        os.replace(temporary, target)
    except (OSError, ValueError):
        return
    # Skips the result if the song has already changed
    GLib.idle_add(lambda: art["url"] == url and apply_art(target) and False)


def show_art(url):
    if url == art["url"]:
        return
    art["url"] = url
    parts = urllib.parse.urlparse(url)
    if parts.scheme == "file":
        path = urllib.parse.unquote(parts.path)
        apply_art(path if os.path.isfile(path) else None)
    else:
        apply_art(None)
        if parts.scheme in ("http", "https"):
            threading.Thread(target=download_art, args=(url,), daemon=True).start()


def show_status(status):
    for notch in notches:
        notch.play_button.set_label(PAUSE_ICON if status == "Playing" else PLAY_ICON)


class Notch:
    def __init__(self, monitor):
        self.is_open = False
        self.fraction = 0
        self.velocity = 0
        self.tick = None
        self.last_frame = None
        self.close_timer = None
        self.pulsing = False
        self.hovering = False
        self.hover_fraction = 0
        self.hover_velocity = 0
        self.hover_tick = None
        self.hover_last = None

        self.window = Gtk.Window()
        self.window.set_visual(self.window.get_screen().get_rgba_visual())
        self.window.set_app_paintable(True)
        GtkLayerShell.init_for_window(self.window)
        GtkLayerShell.set_namespace(self.window, "waybar-notch")
        GtkLayerShell.set_monitor(self.window, monitor)
        GtkLayerShell.set_layer(self.window, GtkLayerShell.Layer.TOP)
        GtkLayerShell.set_exclusive_zone(self.window, -1)
        GtkLayerShell.set_anchor(self.window, GtkLayerShell.Edge.TOP, True)

        # The content keeps its full size and gets clipped, so it can't jolt the notch as it grows
        full_size = Gtk.Box()
        full_size.set_size_request(*OPEN_SIZE)
        full_size.pack_start(self.build_content(), True, True, 0)
        self.content = Gtk.ScrolledWindow()
        self.content.set_policy(Gtk.PolicyType.EXTERNAL, Gtk.PolicyType.EXTERNAL)
        self.content.add(full_size)

        self.panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.panel.get_style_context().add_class("notch")
        self.panel.set_size_request(*IDLE_SIZE)
        self.panel.pack_start(self.content, True, True, 0)

        row = Gtk.Box()
        row.set_halign(Gtk.Align.CENTER)
        row.set_valign(Gtk.Align.START)
        row.pack_start(self.build_shoulder("left"), False, False, 0)
        row.pack_start(self.panel, False, False, 0)
        row.pack_start(self.build_shoulder("right"), False, False, 0)

        self.root = Gtk.Box()
        self.root.pack_start(row, True, True, 0)
        self.window.add(self.root)
        self.set_surface_size(False)

        self.window.add_events(Gdk.EventMask.ENTER_NOTIFY_MASK | Gdk.EventMask.LEAVE_NOTIFY_MASK
                               | Gdk.EventMask.BUTTON_PRESS_MASK)
        self.window.connect("button-press-event", self.on_click)
        self.window.connect("enter-notify-event", self.on_enter)
        self.window.connect("leave-notify-event", self.on_leave)
        self.window.show_all()
        self.content.hide()

    def build_shoulder(self, side):
        shoulder = Gtk.Box()
        shoulder.set_valign(Gtk.Align.START)
        shoulder.get_style_context().add_class("shoulder")
        shoulder.get_style_context().add_class(side)
        return shoulder

    def build_button(self, icon, command, css_class=None):
        button = Gtk.Button(label=icon)
        button.set_relief(Gtk.ReliefStyle.NONE)
        button.set_valign(Gtk.Align.CENTER)
        if css_class:
            button.get_style_context().add_class(css_class)
        button.connect("clicked", lambda _: run_player(command))
        return button

    def build_content(self):
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        content.set_valign(Gtk.Align.START)
        content.set_margin_top(IDLE_SIZE[1] + 4)

        cover = Gtk.Box()
        cover.set_halign(Gtk.Align.CENTER)
        cover.get_style_context().add_class("track-art")
        self.art_icon = Gtk.Label(label=MUSIC_ICON)
        self.art_icon.get_style_context().add_class("art-icon")
        cover.pack_start(self.art_icon, True, True, 0)
        content.pack_start(cover, False, False, 0)

        controls = Gtk.Box(spacing=12)
        controls.set_halign(Gtk.Align.CENTER)
        controls.set_margin_top(14)
        self.play_button = self.build_button(PLAY_ICON, "play-pause", "play")
        controls.pack_start(self.build_button(PREVIOUS_ICON, "previous"), False, False, 0)
        controls.pack_start(self.play_button, False, False, 0)
        controls.pack_start(self.build_button(NEXT_ICON, "next"), False, False, 0)
        content.pack_start(controls, False, False, 0)
        return content

    def set_surface_size(self, is_open):
        # The window stays small when closed, so it never blocks clicks on the bar
        if is_open:
            width = OPEN_SIZE[0] + 2 * (SHOULDER_WIDTH + SHADOW_ROOM)
            height = OPEN_SIZE[1] + SHADOW_ROOM
        else:
            width = IDLE_SIZE[0] + 2 * SHOULDER_WIDTH
            height = IDLE_SIZE[1]
        self.root.set_size_request(width, height)
        self.window.resize(width, height)

    def pulse(self):
        """Swells the closed notch and lets it settle with a bounce: a new song started,
        or a clip was saved."""
        if self.is_open or self.tick is not None or self.pulsing or self.hover_tick is not None:
            return
        self.pulsing = True
        # The window is only as big as the closed notch, so it needs room to swell into
        width = IDLE_SIZE[0] + PULSE_GROW[0] + 2 * SHOULDER_WIDTH
        height = IDLE_SIZE[1] + PULSE_GROW[1]
        self.root.set_size_request(width, height)
        self.window.resize(width, height)
        began = [None]

        def step(_widget, clock):
            now = clock.get_frame_time() / 1_000_000
            began[0] = began[0] or now
            t = now - began[0]
            if t >= PULSE_SECONDS or self.is_open:
                self.pulsing = False
                if not self.is_open:
                    self.panel.set_size_request(*IDLE_SIZE)
                    self.set_surface_size(False)
                return False
            swell = min(1.0, 1.5 * math.exp(-5 * t) * math.sin(13 * t))
            self.panel.set_size_request(round(IDLE_SIZE[0] + PULSE_GROW[0] * swell),
                                        round(IDLE_SIZE[1] + PULSE_GROW[1] * max(swell, 0)))
            return True

        self.panel.add_tick_callback(step)

    def set_open(self, is_open):
        if is_open == self.is_open:
            return
        self.is_open = is_open
        context = self.panel.get_style_context()
        if is_open:
            self.set_surface_size(True)
            context.add_class("open")
        else:
            context.remove_class("open")
        if self.tick is None:
            self.last_frame = None
            self.tick = self.panel.add_tick_callback(self.step)

    def step(self, _widget, clock):
        now = clock.get_frame_time()
        # The first frame counts as a full one, so movement starts straight away
        elapsed = (now - self.last_frame) / 1_000_000 if self.last_frame else FRAME_SECONDS
        self.last_frame = now
        target = 1 if self.is_open else 0
        # A spring that can't overshoot: it keeps its speed when you change direction mid-way
        elapsed = min(elapsed, MAX_STEP_SECONDS)
        pull = SPRING_SPEED ** 2 * (target - self.fraction) - 2 * SPRING_SPEED * self.velocity
        self.velocity += pull * elapsed
        self.fraction = min(max(self.fraction + self.velocity * elapsed, 0), 1)
        if abs(target - self.fraction) < 0.004 and abs(self.velocity) < 0.1:
            self.fraction = target
            self.velocity = 0

        idle = self.idle_size()
        self.panel.set_size_request(
            round(idle[0] + (OPEN_SIZE[0] - idle[0]) * self.fraction),
            round(idle[1] + (OPEN_SIZE[1] - idle[1]) * self.fraction),
        )
        # The cover and buttons fade with the size, in both directions
        if self.fraction > CONTENT_FADE_START:
            self.content.show()
            self.content.set_opacity((self.fraction - CONTENT_FADE_START) / (1 - CONTENT_FADE_START))
            horizontal = self.content.get_hadjustment()
            horizontal.set_value((horizontal.get_upper() - horizontal.get_page_size()) / 2)
        else:
            self.content.hide()

        if self.fraction != target:
            return True
        if not self.is_open and self.hover_tick is None:
            self.set_surface_size(False)
        self.tick = None
        return False

    def idle_size(self):
        """The closed notch's size: a little bigger while the mouse is on it."""
        return (IDLE_SIZE[0] + HOVER_GROW[0] * self.hover_fraction,
                IDLE_SIZE[1] + HOVER_GROW[1] * max(self.hover_fraction, 0))

    def set_hover(self, hovering):
        self.hovering = hovering
        if hovering and not self.is_open and self.tick is None:
            # The window is only as big as the closed notch, so it needs room to swell into
            width = IDLE_SIZE[0] + PULSE_GROW[0] + 2 * SHOULDER_WIDTH
            height = IDLE_SIZE[1] + PULSE_GROW[1]
            self.root.set_size_request(width, height)
            self.window.resize(width, height)
        if self.hover_tick is None:
            self.hover_last = None
            self.hover_tick = self.panel.add_tick_callback(self.hover_step)

    def hover_step(self, _widget, clock):
        now = clock.get_frame_time()
        elapsed = (now - self.hover_last) / 1_000_000 if self.hover_last else FRAME_SECONDS
        self.hover_last = now
        elapsed = min(elapsed, MAX_STEP_SECONDS)
        # Once the notch is open the swell eases away underneath it
        target = 1 if self.hovering and not self.is_open else 0
        # A springy swell: it goes a touch too far and settles
        pull = 700 * (target - self.hover_fraction) - 26 * self.hover_velocity
        self.hover_velocity += pull * elapsed
        self.hover_fraction += self.hover_velocity * elapsed
        settled = abs(target - self.hover_fraction) < 0.01 and abs(self.hover_velocity) < 0.1
        if settled:
            self.hover_fraction, self.hover_velocity = target, 0
        if not self.is_open and self.tick is None and not self.pulsing:
            width, height = self.idle_size()
            self.panel.set_size_request(round(width), round(height))
        if not settled:
            return True
        self.hover_tick = None
        if target == 0 and not self.is_open and self.tick is None and not self.pulsing:
            self.set_surface_size(False)
        return False

    def on_click(self, _window, event):
        # A click opens the notch. Once open, a click on its background closes it again
        # (the buttons inside keep their own clicks)
        if event.button == 1:
            self.set_open(not self.is_open)
        return True

    def on_enter(self, _window, event):
        if event.detail == Gdk.NotifyType.INFERIOR:
            return
        if self.close_timer:
            GLib.source_remove(self.close_timer)
            self.close_timer = None
        self.set_hover(True)

    def on_leave(self, _window, event):
        if event.detail == Gdk.NotifyType.INFERIOR:
            return
        self.set_hover(False)
        if self.is_open and not self.close_timer:
            self.close_timer = GLib.timeout_add(CLOSE_DELAY_MS, self.close_after_delay)

    def close_after_delay(self):
        self.close_timer = None
        self.set_open(False)
        return False


def apply_css():
    provider = Gtk.CssProvider()
    provider.load_from_data(CSS)
    Gtk.StyleContext.add_provider_for_screen(
        Gdk.Screen.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
    )


def pulse_notches(*_):
    for notch in notches:
        notch.pulse()
    return True


song = {"title": None}


def show_title(title):
    # The first line is the song that was already playing when this started
    if song["title"] is not None and title and title != song["title"]:
        pulse_notches()
    song["title"] = title


def create_notches():
    display = Gdk.Display.get_default()
    for index in range(display.get_n_monitors()):
        notches.append(Notch(display.get_monitor(index)))


def main():
    # clips.py reads this to make the notch pulse when a clip is saved
    (RUNTIME_DIR / "waybar-notch.pid").write_text(str(os.getpid()))
    GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGUSR1, pulse_notches)

    apply_css()
    create_notches()
    follow_player(["metadata", "--format", "{{mpris:artUrl}}"], show_art)
    follow_player(["status"], show_status)
    follow_player(["metadata", "--format", "{{title}}"], show_title)

    # Waybar restarts this script, which rebuilds the notches for the new monitors
    display = Gdk.Display.get_default()
    display.connect("monitor-added", lambda *_: Gtk.main_quit())
    display.connect("monitor-removed", lambda *_: Gtk.main_quit())
    Gtk.main()


if __name__ == "__main__":
    main()
