#!/usr/bin/env python3
# Game mod menu: the gamepad button in the gear drawer.
#   game-menu.py --watch   Waybar runs this. It shows the game you're playing, and while a
#                          game is focused it applies that game's settings, then undoes them
#                          when you tab out. Waybar starts one per bar; only one applies.
#   game-menu.py --menu    Opens the menu for the focused (or most recently played) game.
# Settings are saved per game in game-profiles.json. Everything here is desktop-side
# (Hyprland, notifications, audio, a screen colour shader) and never touches the game.
import fcntl
import hashlib
import importlib.util
import json
import os
import re
import socket
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROFILES = HERE / "game-profiles.json"
RUNTIME = Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp")) / "game-menu"
STATE = RUNTIME / "state.json"
PREVIEW = RUNTIME / "preview"
LOCK = RUNTIME / "apply.lock"
STEAM = Path.home() / ".local/share/Steam"
SOBER_CONFIG = Path.home() / ".var/app/org.vinegarhq.Sober/config/sober/config.json"
ROBLOX_CLASSES = ("org.vinegarhq.sober", "robloxplayerbeta.exe")
WATCH_EVENTS = {b"activewindow", b"openwindow", b"closewindow", b"configreloaded", b"movewindow",
                b"fullscreen", b"changefloatingmode", b"workspace", b"focusedmon"}
POLL_SECONDS = 0.5

GAMEPAD = ""
ICON_SPAN = "<span font_family='Font Awesome 7 Free Solid' font_weight='900'>{}</span>"
DEFAULTS = {"silence": False, "max_fps": False, "vibrance": 0, "shadow_lift": 0, "sensitivity": None}
# Hyprland settings the profiles change, and put back afterwards
BASELINE_OPTIONS = {
    "blur": "decoration:blur:enabled",
    "shadow": "decoration:shadow:enabled",
    "animations": "animations:enabled",
    "sensitivity": "input:sensitivity",
    "shader": "decoration:screen_shader",
}

# Hyprland only runs shaders over a whole monitor, so this one checks which monitor it's
# drawing (wl_output) and where on it, and only changes pixels inside the game's window.
# Colour boost raises saturation, more for dull colours than already vivid ones.
# Brighten shadows is a gamma curve: dark areas lift a lot, highlights barely move.
SHADER = """#version 300 es
precision highp float;
in vec2 v_texcoord;
uniform sampler2D tex;
uniform int wl_output;
out vec4 fragColor;

void main() {{
    vec4 color = texture(tex, v_texcoord);
    if (wl_output != {monitor} || v_texcoord.x < {left:.5f} || v_texcoord.x > {right:.5f}
            || v_texcoord.y < {top:.5f} || v_texcoord.y > {bottom:.5f}) {{
        fragColor = color;
        return;
    }}
    color.rgb = pow(color.rgb, vec3({gamma:.3f}));
    float luma = dot(color.rgb, vec3(0.2126, 0.7152, 0.0722));
    float saturation = max(color.r, max(color.g, color.b)) - min(color.r, min(color.g, color.b));
    color.rgb = mix(vec3(luma), color.rgb, 1.0 + {vibrance:.3f} * (1.0 - saturation));
    fragColor = vec4(clamp(color.rgb, 0.0, 1.0), color.a);
}}
"""


def run(*command):
    try:
        return subprocess.run(command, capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def hyprctl_json(*args):
    try:
        return json.loads(run("hyprctl", "-j", *args))
    except ValueError:
        return None


# ---- Finding games ----

def steam_game_name(app_id):
    try:
        text = (STEAM / "steamapps" / f"appmanifest_{app_id}.acf").read_text(errors="replace")
        return re.search(r'"name"\s+"([^"]+)"', text).group(1)
    except (OSError, AttributeError):
        return f"Steam game {app_id}"


def identify(window_class):
    """(game id, name) for a game's window class, or None for anything else."""
    window_class = window_class or ""
    match = re.fullmatch(r"steam_app_(\d+)", window_class)
    if match:
        return f"steam:{match.group(1)}", steam_game_name(match.group(1))
    if window_class.lower() in ROBLOX_CLASSES:
        return "roblox", "Roblox"
    return None


def running_games():
    """Open games, most recently focused first, and the id of the focused one (or None)."""
    clients = hyprctl_json("clients") or []
    games = {}
    for client in sorted(clients, key=lambda c: c.get("focusHistoryID", 99)):
        found = identify(client.get("class"))
        if found and found[0] not in games:
            games[found[0]] = {"id": found[0], "name": found[1], "pid": client.get("pid"), "window": client}
    focused = identify((hyprctl_json("activewindow") or {}).get("class"))
    return list(games.values()), focused[0] if focused else None


def window_region(window):
    """Where a window sits on its monitor: [monitor id, left, top, right, bottom], the edges
    as fractions of the screen. None when it isn't on screen (its workspace is hidden)."""
    monitor = next((m for m in hyprctl_json("monitors") or [] if m["id"] == window.get("monitor")), None)
    if monitor is None:
        return None
    visible = {monitor["activeWorkspace"]["id"], monitor.get("specialWorkspace", {}).get("id")}
    if window.get("workspace", {}).get("id") not in visible:
        return None
    if window.get("fullscreen"):
        return [monitor["id"], 0.0, 0.0, 1.0, 1.0]
    scale = monitor.get("scale") or 1
    width, height = monitor["width"] / scale, monitor["height"] / scale
    (x, y), (w, h) = window["at"], window["size"]

    def fraction(value, total):
        return round(min(max(value / total, 0.0), 1.0), 5)
    return [monitor["id"],
            fraction(x - monitor["x"], width), fraction(y - monitor["y"], height),
            fraction(x - monitor["x"] + w, width), fraction(y - monitor["y"] + h, height)]


# ---- Profiles ----

def load_profiles():
    try:
        return json.loads(PROFILES.read_text())
    except (OSError, ValueError):
        return {}


def profile_for(game_id):
    return {**DEFAULTS, **load_profiles().get(game_id, {})}


def save_profile(game_id, profile):
    profiles = load_profiles()
    profiles[game_id] = profile
    temp = PROFILES.with_suffix(".tmp")
    temp.write_text(json.dumps(profiles, indent=2) + "\n")
    os.replace(temp, PROFILES)


def read_preview():
    """The game the menu is showing, while it's open, so changes show up as you make them."""
    try:
        game_id, pid = PREVIEW.read_text().split()
        os.kill(int(pid), 0)
        return game_id
    except (OSError, ValueError):
        return None


# ---- Applying settings ----

def read_option(name):
    kind, _, value = run("hyprctl", "getoption", name).split("\n")[0].partition(": ")
    if kind == "bool":
        return value == "true"
    if kind == "float":
        return float(value)
    if kind == "str":
        return "" if value == "[[EMPTY]]" else value
    return value


def lua(value):
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return f"{value:.3f}"
    return json.dumps(value)


def write_shader(vibrance, shadow_lift, region):
    if not (vibrance or shadow_lift) or region is None:
        return ""
    monitor, left, top, right, bottom = region
    source = SHADER.format(vibrance=1.5 * vibrance / 100, gamma=1 / (1 + 0.8 * shadow_lift / 100),
                           monitor=monitor, left=left, top=top, right=right, bottom=bottom)
    # A new file name per version, since Hyprland won't reload a path it already has
    path = RUNTIME / f"shader-{hashlib.sha1(source.encode()).hexdigest()[:12]}.frag"
    for old in RUNTIME.glob("shader-*.frag"):
        if old != path:
            old.unlink(missing_ok=True)
    path.write_text(source)
    return str(path)


class Applier:
    """Puts a game's settings on top of the desktop's own ones (the baseline), and puts
    the baseline back afterwards. Both are saved in the runtime folder, so if Waybar
    restarts mid-game, the next watcher carries on from where this one was."""

    def __init__(self):
        try:
            state = json.loads(STATE.read_text())
        except (OSError, ValueError):
            state = {}
        self.baseline = state.get("baseline")
        self.applied = state.get("applied")

    def save(self):
        STATE.write_text(json.dumps({"baseline": self.baseline, "applied": self.applied}))

    def forget(self):
        # A config reload has already put Hyprland back to the baseline
        self.baseline = None
        self.applied = None
        self.save()

    @property
    def tracking(self):
        """True while the colour shader is on, so the game window's position needs following."""
        profile = (self.applied or {}).get("profile") or {}
        return bool(profile.get("vibrance") or profile.get("shadow_lift"))

    def apply(self, profile, region=None):
        if {"profile": profile, "region": region} == self.applied or (profile is None and not self.applied):
            return
        if profile is None:
            if self.baseline:
                self.set_hyprland(**self.baseline)
            run("makoctl", "mode", "-r", "game")
            self.baseline = None
            self.applied = None
            self.save()
            return

        if self.baseline is None:
            self.baseline = {key: read_option(option) for key, option in BASELINE_OPTIONS.items()}
        base = self.baseline
        fast = profile["max_fps"]
        sensitivity = profile["sensitivity"]
        self.set_hyprland(
            blur=base["blur"] and not fast,
            shadow=base["shadow"] and not fast,
            animations=base["animations"] and not fast,
            sensitivity=base["sensitivity"] if sensitivity is None else float(sensitivity),
            shader=write_shader(profile["vibrance"], profile["shadow_lift"], region) or base["shader"],
        )
        run("makoctl", "mode", "-a" if profile["silence"] else "-r", "game")
        self.applied = {"profile": profile, "region": region}
        self.save()

    @staticmethod
    def set_hyprland(blur, shadow, animations, sensitivity, shader):
        run("hyprctl", "eval",
            f"hl.config({{ decoration = {{ blur = {{ enabled = {lua(blur)} }}, "
            f"shadow = {{ enabled = {lua(shadow)} }}, screen_shader = {lua(shader)} }}, "
            f"animations = {{ enabled = {lua(animations)} }}, "
            f"input = {{ sensitivity = {lua(float(sensitivity))} }} }})")


# ---- The bar button (--watch) ----

def watch():
    RUNTIME.mkdir(parents=True, exist_ok=True)
    lock_file = open(LOCK, "w")
    state = {"applier": None, "output": None}

    def applier():
        # The first watcher to grab the lock does the applying, for as long as it runs
        if state["applier"] is None:
            try:
                fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
                state["applier"] = Applier()
            except OSError:
                pass
        return state["applier"]

    def evaluate():
        games, focused = running_games()
        ids = {game["id"] for game in games}
        preview = read_preview()
        target = preview if preview in ids else focused

        shown = next((g for g in games if g["id"] == focused), games[0] if games else None)
        # Just the icon: dim with no game open, bright while one is
        if shown:
            output = {"text": ICON_SPAN.format(GAMEPAD), "class": "playing" if focused else "running"}
        else:
            output = {"text": ICON_SPAN.format(GAMEPAD), "class": "idle"}
        if output != state["output"]:
            state["output"] = output
            print(json.dumps(output), flush=True)

        if applier():
            game = next((g for g in games if g["id"] == target), None)
            if game is None:
                state["applier"].apply(None)
            else:
                profile = profile_for(target)
                region = window_region(game["window"]) if profile["vibrance"] or profile["shadow_lift"] else None
                state["applier"].apply(profile, region)

    def mtimes():
        result = []
        for path in (PROFILES, PREVIEW):
            try:
                result.append(path.stat().st_mtime_ns)
            except OSError:
                result.append(None)
        return result

    socket_path = (Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp")) / "hypr"
                   / os.environ.get("HYPRLAND_INSTANCE_SIGNATURE", "") / ".socket2.sock")
    while True:
        try:
            events = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            events.connect(str(socket_path))
            events.settimeout(POLL_SECONDS)
        except OSError:
            time.sleep(2)
            continue
        evaluate()
        seen = mtimes()
        buffer = b""
        while True:
            changed = False
            try:
                data = events.recv(4096)
                if not data:
                    break
                buffer += data
                *lines, buffer = buffer.split(b"\n")
                for line in lines:
                    event = line.split(b">>", 1)[0]
                    if event == b"configreloaded" and state["applier"]:
                        state["applier"].forget()
                    changed |= event in WATCH_EVENTS
            except socket.timeout:
                # Hyprland sends no event while a window is being resized, so check
                # every half second while the colour shader is following the game window
                changed = bool(state["applier"] and state["applier"].tracking)
            except OSError:
                break
            # Also re-check when the menu saves a change or opens/closes
            now = mtimes()
            if now != seen:
                seen = now
                changed = True
            if changed:
                evaluate()
        events.close()


# ---- Things the menu reads and changes ----

def audio_streams():
    try:
        return json.loads(run("pactl", "-f", "json", "list", "sink-inputs"))
    except ValueError:
        return []


def stream_names(stream):
    props = stream.get("properties", {})
    return f"{props.get('application.name', '')} {props.get('application.process.binary', '')}".lower()


def game_streams(game, streams):
    def matches(stream):
        props = stream.get("properties", {})
        if str(game["pid"]) == props.get("application.process.id"):
            return True
        # Sober runs in a Flatpak, so its audio reports a sandboxed process id
        return game["id"] == "roblox" and "sober" in stream_names(stream)
    return [s for s in streams if matches(s)]


def discord_streams(streams):
    return [s for s in streams if "discord" in stream_names(s)]


def stream_volume(streams):
    for stream in streams:
        for channel in stream.get("volume", {}).values():
            return int(channel.get("value_percent", "100%").rstrip("%"))
    return None


def set_stream_volume(streams, percent):
    for stream in streams:
        run("pactl", "set-sink-input-volume", str(stream["index"]), f"{percent}%")


def playing_for(pid):
    try:
        start_ticks = int(Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[19])
        uptime = float(Path("/proc/uptime").read_text().split()[0])
    except (OSError, ValueError, IndexError):
        return None
    minutes = int((uptime - start_ticks / os.sysconf("SC_CLK_TCK")) // 60)
    return f"{minutes // 60}h {minutes % 60}m" if minutes >= 60 else f"{minutes}m"


def hwmon(name):
    for folder in Path("/sys/class/hwmon").glob("hwmon*"):
        try:
            if (folder / "name").read_text().strip() == name:
                return folder
        except OSError:
            pass
    return None


def read_number(path, scale=1):
    try:
        return int(Path(path).read_text().strip()) / scale
    except (OSError, ValueError, TypeError):
        return None


class Stats:
    """CPU, GPU, RAM and VRAM use, plus temperatures, read straight from the kernel."""

    def __init__(self):
        self.cpu_sensor = hwmon("k10temp") or hwmon("coretemp")
        self.gpu_sensor = hwmon("amdgpu")
        self.gpu = next(Path("/sys/class/drm").glob("card*/device/gpu_busy_percent"), None)
        self.last_cpu = self.cpu_times()

    @staticmethod
    def cpu_times():
        values = [int(v) for v in Path("/proc/stat").read_text().split("\n")[0].split()[1:]]
        return values[3] + values[4], sum(values)

    def read(self):
        idle, total = self.cpu_times()
        last_idle, last_total = self.last_cpu
        self.last_cpu = (idle, total)
        cpu = 100 * (1 - (idle - last_idle) / max(total - last_total, 1))

        memory = dict(line.split(":", 1) for line in Path("/proc/meminfo").read_text().splitlines())
        memory_total = int(memory["MemTotal"].split()[0])
        ram = 100 * (1 - int(memory["MemAvailable"].split()[0]) / memory_total)

        device = self.gpu.parent if self.gpu else None
        vram_used = read_number(device / "mem_info_vram_used") if device else None
        vram_total = read_number(device / "mem_info_vram_total") if device else None
        return {
            "CPU": (cpu, read_number(self.cpu_sensor / "temp1_input", 1000) if self.cpu_sensor else None),
            "GPU": (read_number(self.gpu), read_number(self.gpu_sensor / "temp1_input", 1000)
                    if self.gpu_sensor else None),
            "RAM": (ram, f"{memory_total / 1024 ** 2:.0f} GB"),
            "VRAM": (100 * vram_used / vram_total if vram_used and vram_total else None,
                     f"{vram_total / 1024 ** 3:.0f} GB" if vram_total else None),
        }


def read_sober_config():
    """Sober's settings file: comment lines on top, then JSON. Returns (header, settings)."""
    try:
        text = SOBER_CONFIG.read_text()
    except OSError:
        return None, None
    start = text.find("{")
    try:
        return text[:start], json.loads(text[start:])
    except ValueError:
        return None, None


def write_sober_setting(key, value):
    header, settings = read_sober_config()
    if settings is None:
        return
    settings[key] = value
    temp = SOBER_CONFIG.with_suffix(".tmp")
    temp.write_text(header + json.dumps(settings, indent=4) + "\n")
    os.replace(temp, SOBER_CONFIG)


# ---- The menu (--menu) ----

MENU_WIDTH = 340
FORCE_QUIT_SECONDS = 3

MENU_CSS = b"""
.game-icon {
    background-color: #0a84ff;
    border-radius: 11px;
    min-width: 40px;
    min-height: 40px;
    font-size: 18px;
}
.game-name { font-size: 15px; font-weight: 700; }
.heading {
    font-size: 10px;
    font-weight: 700;
    color: rgba(255, 255, 255, 0.45);
    margin: 4px 4px 0 4px;
}
.row-label { font-size: 11px; color: rgba(255, 255, 255, 0.8); }
.row-value { font-size: 11px; color: rgba(255, 255, 255, 0.5); }
.note { font-size: 10px; color: rgba(255, 255, 255, 0.45); }
.stat-name { font-size: 10px; font-weight: 700; color: rgba(255, 255, 255, 0.5); }
.stat-value { font-size: 16px; font-weight: 700; }
.stat-extra { font-size: 10px; color: rgba(255, 255, 255, 0.5); }
.stat-extra.hot { color: #ff9f0a; }
button.pill, button.action {
    background-image: none;
    background-color: rgba(255, 255, 255, 0.08);
    border: none;
    box-shadow: none;
    text-shadow: none;
    outline-width: 0;
    transition: background-color 140ms ease-out;
}
button.pill { border-radius: 10px; padding: 2px 10px; min-height: 0; font-size: 11px; }
button.action { border-radius: 9px; padding: 7px 10px; font-size: 11px; font-weight: 600; }
button.pill:hover, button.action:hover { background-color: rgba(255, 255, 255, 0.15); }
button.pill.selected, button.pill.selected:hover { background-color: #0a84ff; }
button.danger label { color: #ff453a; }
button.danger.armed, button.danger.armed:hover { background-color: #ff453a; }
button.danger.armed label { color: #ffffff; }
"""


def build_menu(cc, Gtk, GLib, close):
    """Builds the panel. cc is the Control Center module, for its widgets and look."""
    from gi.repository import Pango

    cc.ICONS.update({"gamepad": GAMEPAD, "bell": "", "bolt": "", "reset": "",
                     "headset": "", "discord": "", "region": ""})
    games, _ = running_games()
    panel = cc.css_classes(Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8), "panel")
    panel.set_size_request(MENU_WIDTH, -1)
    timers = []

    def label(text, css_class, xalign=0):
        widget = cc.css_classes(Gtk.Label(label=text, xalign=xalign), css_class)
        return widget

    def heading(text):
        panel.pack_start(label(text.upper(), "heading"), False, False, 0)

    def tile(spacing=8, vertical=True):
        orientation = Gtk.Orientation.VERTICAL if vertical else Gtk.Orientation.HORIZONTAL
        return cc.css_classes(Gtk.Box(orientation=orientation, spacing=spacing), "tile")

    def slider_row(container, title, value_text, on_change, extra=None):
        top = Gtk.Box(spacing=6)
        top.pack_start(label(title, "row-label"), True, True, 0)
        value = label(value_text, "row-value", 1)
        top.pack_start(value, False, False, 0)
        if extra:
            top.pack_start(extra, False, False, 0)
        slider = cc.Slider(on_change)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.pack_start(top, False, False, 0)
        box.pack_start(slider, False, False, 0)
        container.pack_start(box, False, False, 0)
        return slider, value

    # Header: which game, and how long you've been playing
    header = tile(spacing=11, vertical=False)
    icon = cc.icon_label("gamepad", "game-icon")
    icon.set_valign(Gtk.Align.CENTER)
    header.pack_start(icon, False, False, 0)
    titles = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
    titles.set_valign(Gtk.Align.CENTER)
    name = label(games[0]["name"] if games else "No game running", "game-name")
    name.set_ellipsize(Pango.EllipsizeMode.END)
    titles.pack_start(name, False, False, 0)
    subtitle = label("", "tile-sub")
    titles.pack_start(subtitle, False, False, 0)
    header.pack_start(titles, True, True, 0)
    panel.pack_start(header, False, False, 0)

    if not games:
        subtitle.set_text("Open a game and its settings show up here")
        build_stats(cc, Gtk, GLib, panel, label, heading, tile, timers)
        return panel, timers, lambda: None

    game = games[0]

    def write_preview():
        PREVIEW.write_text(f"{game['id']} {os.getpid()}")

    write_preview()

    def update_subtitle():
        _, focused = running_games()
        played = playing_for(game["pid"])
        where = "Focused" if focused == game["id"] else "In the background"
        subtitle.set_text(f"Playing for {played}  ·  {where}" if played else where)
        return True

    update_subtitle()
    timers.append(GLib.timeout_add_seconds(5, update_subtitle))

    # More than one game open: pills to pick which one to set up
    if len(games) > 1:
        pills = Gtk.Box(spacing=6)
        buttons = []
        for index, other in enumerate(games):
            pill = cc.css_classes(Gtk.Button(label=other["name"]), "pill")
            if index == 0:
                cc.css_classes(pill, "selected")
            buttons.append(pill)
            pills.pack_start(pill, False, False, 0)

            def pick(_button, picked=other, chosen=pill):
                nonlocal game
                for button in buttons:
                    cc.set_class(button, "selected", button is chosen)
                game = picked
                name.set_text(picked["name"])
                write_preview()
                update_subtitle()
                refresh_game_widgets()
            pill.connect("clicked", pick)
        header_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        panel.remove(header)
        header_box.pack_start(header, False, False, 0)
        header_box.pack_start(pills, False, False, 0)
        panel.pack_start(header_box, False, False, 0)

    # ---- Settings that switch on whenever this game is focused ----
    heading("While you're in this game")
    profile = {}

    def update(key, value):
        profile[key] = value
        save_profile(game["id"], dict(profile))

    toggles = Gtk.Grid(row_spacing=8, column_spacing=8, column_homogeneous=True)
    toggle_widgets = []
    for column, (key, glyph, title) in enumerate((("silence", "bell", "No Alerts"),
                                                   ("max_fps", "bolt", "Max FPS"))):
        def read(key=key):
            on = profile.get(key, False)
            return on, "On" if on else "Off"

        def write(on, key=key):
            update(key, on)

        widget, toggle = cc.toggle_tile(glyph, title, read, write)
        toggles.attach(widget, column, 0, 1, 1)
        toggle_widgets.append(toggle)
    panel.pack_start(toggles, False, False, 0)
    note = label("No Alerts hides notifications. Max FPS turns off blur, shadows and "
                 "animations. Both switch back when you leave the game.", "note")
    note.set_line_wrap(True)
    note.set_max_width_chars(48)
    panel.pack_start(note, False, False, 0)

    look = tile(spacing=12)

    def percent_text(value):
        return f"{value}%" if value else "Off"

    def on_vibrance(value):
        vibrance_value.set_text(percent_text(value))
        update("vibrance", value)

    def on_shadows(value):
        shadows_value.set_text(percent_text(value))
        update("shadow_lift", value)

    def sensitivity_text(value):
        return "Default" if value is None else f"{value:+.2f}"

    def on_sensitivity(value):
        sensitivity = round((value - 50) / 50, 2)
        sensitivity_value.set_text(sensitivity_text(sensitivity))
        update("sensitivity", sensitivity)

    def reset_sensitivity(*_):
        update("sensitivity", None)
        refresh_game_widgets()

    reset = cc.css_classes(Gtk.Button(), "flat")
    reset.add(cc.icon_label("reset", "arrow"))
    reset.set_tooltip_text("Back to your normal sensitivity")
    reset.connect("clicked", reset_sensitivity)
    vibrance_slider, vibrance_value = slider_row(look, "Colour boost", "Off", on_vibrance)
    shadows_slider, shadows_value = slider_row(look, "Brighten shadows", "Off", on_shadows)
    sensitivity_slider, sensitivity_value = slider_row(
        look, "Mouse sensitivity", "Default", on_sensitivity, reset)
    panel.pack_start(look, False, False, 0)

    # ---- Sound: live, not saved ----
    heading("Sound")
    sound = tile(spacing=12)
    volume_sliders = {}
    for key, title in (("game", "Game"), ("discord", "Discord")):
        def on_volume(value, key=key):
            volume_sliders[key][1].set_text(f"{value}%")
            streams = audio_streams()
            set_stream_volume(game_streams(game, streams) if key == "game" else discord_streams(streams),
                              value)
        volume_sliders[key] = slider_row(sound, title, "", on_volume)
    panel.pack_start(sound, False, False, 0)

    def refresh_volumes():
        streams = audio_streams()
        for key, found in (("game", game_streams(game, streams)), ("discord", discord_streams(streams))):
            slider, value = volume_sliders[key]
            volume = stream_volume(found)
            slider.set_enabled(volume is not None)
            if volume is None:
                value.set_text("No sound playing")
            else:
                slider.set_value(volume)
                value.set_text(f"{volume}%")

    # ---- Per-game extras ----
    extras = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
    panel.pack_start(extras, False, False, 0)

    def action(text, callback, *css):
        button = cc.css_classes(Gtk.Button(label=text), "action", *css)
        button.connect("clicked", lambda *_: callback())
        return button

    def open_path(path):
        subprocess.Popen(["xdg-open", str(path)], start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        close()

    def build_extras():
        for child in extras.get_children():
            extras.remove(child)
        if game["id"].startswith("steam:"):
            app_id = game["id"].split(":", 1)[1]
            heading_label = label("STEAM", "heading")
            extras.pack_start(heading_label, False, False, 0)
            row = Gtk.Box(spacing=8, homogeneous=True)
            manifest = STEAM / "steamapps" / f"appmanifest_{app_id}.acf"
            try:
                install = re.search(r'"installdir"\s+"([^"]+)"', manifest.read_text()).group(1)
                game_folder = STEAM / "steamapps" / "common" / install
            except (OSError, AttributeError):
                game_folder = None
            prefix = STEAM / "steamapps" / "compatdata" / app_id / "pfx" / "drive_c" / "users" / "steamuser"
            if game_folder and game_folder.exists():
                row.pack_start(action("Game Files", lambda: open_path(game_folder)), True, True, 0)
            if prefix.exists():
                row.pack_start(action("Saves (Proton)", lambda: open_path(prefix)), True, True, 0)
            row.pack_start(action("Steam Page", lambda: open_path(f"steam://nav/games/details/{app_id}")),
                           True, True, 0)
            extras.pack_start(row, False, False, 0)
        elif game["id"] == "roblox":
            _, settings = read_sober_config()
            if settings is not None:
                build_roblox(settings)
        extras.show_all()

    def build_roblox(settings):
        extras.pack_start(label("ROBLOX (SOBER)", "heading"), False, False, 0)
        box = tile(spacing=10)
        top = Gtk.Box(spacing=6)
        top.pack_start(label("Graphics", "row-label"), True, True, 0)
        modes = []
        for mode in ("performance", "balanced", "quality"):
            pill = cc.css_classes(Gtk.Button(label=mode.title()), "pill")
            cc.set_class(pill, "selected", settings.get("graphics_optimization_mode") == mode)
            modes.append(pill)

            def choose(_button, mode=mode, chosen=pill):
                write_sober_setting("graphics_optimization_mode", mode)
                for other in modes:
                    cc.set_class(other, "selected", other is chosen)
            pill.connect("clicked", choose)
            top.pack_start(pill, False, False, 0)
        box.pack_start(top, False, False, 0)
        extras.pack_start(box, False, False, 0)

        grid = Gtk.Grid(row_spacing=8, column_spacing=8, column_homogeneous=True)
        for column, (key, glyph, title, font) in enumerate((
                ("discord_rpc_enabled", "discord", "Discord Status", cc.BRAND_FONT),
                ("server_location_indicator_enabled", "region", "Server Region", cc.ICON_FONT))):
            def read(key=key):
                on = bool((read_sober_config()[1] or {}).get(key))
                return on, "On" if on else "Off"

            def write(on, key=key):
                write_sober_setting(key, on)

            widget, toggle = cc.toggle_tile(glyph, title, read, write, font=font)
            toggle.refresh()
            grid.attach(widget, column, 0, 1, 1)
        extras.pack_start(grid, False, False, 0)
        note = label("Roblox picks these up the next time it starts.", "note")
        extras.pack_start(note, False, False, 0)

    def refresh_game_widgets():
        profile.clear()
        profile.update(profile_for(game["id"]))
        for toggle in toggle_widgets:
            toggle.refresh()
        vibrance_slider.set_value(profile["vibrance"])
        vibrance_value.set_text(percent_text(profile["vibrance"]))
        shadows_slider.set_value(profile["shadow_lift"])
        shadows_value.set_text(percent_text(profile["shadow_lift"]))
        sensitivity = profile["sensitivity"]
        if sensitivity is None:
            sensitivity = read_option("input:sensitivity")
        sensitivity_slider.set_value(50 + 50 * float(sensitivity or 0))
        sensitivity_value.set_text(sensitivity_text(profile["sensitivity"]))
        refresh_volumes()
        build_extras()

    refresh_game_widgets()
    build_stats(cc, Gtk, GLib, panel, label, heading, tile, timers)

    # Force quit needs a second click within a few seconds, so it can't happen by accident
    quit_button = action("Force Quit Game", lambda: None, "danger")
    armed = {"timer": None}

    def disarm():
        armed["timer"] = None
        quit_button.set_label("Force Quit Game")
        cc.set_class(quit_button, "armed", False)
        return False

    def force_quit(*_):
        if armed["timer"] is None:
            quit_button.set_label("Click again to quit")
            cc.set_class(quit_button, "armed", True)
            armed["timer"] = GLib.timeout_add_seconds(FORCE_QUIT_SECONDS, disarm)
            return
        GLib.source_remove(armed["timer"])
        disarm()
        pid = game["pid"]
        run("kill", str(pid))
        # Still there after two seconds (a frozen game ignores the polite request): kill it outright
        subprocess.Popen(["sh", "-c", f"sleep 2; kill -0 {pid} 2>/dev/null && kill -9 {pid}"],
                         start_new_session=True)
        close()

    quit_button.connect("clicked", force_quit)
    panel.pack_start(quit_button, False, False, 0)

    def cleanup():
        try:
            if read_preview() == game["id"]:
                PREVIEW.unlink()
        except OSError:
            pass
    return panel, timers, cleanup


def build_stats(cc, Gtk, GLib, panel, label, heading, tile, timers):
    heading("Performance")
    box = tile(spacing=6)
    grid = Gtk.Grid(row_spacing=10, column_spacing=8, column_homogeneous=True)
    stats = Stats()
    cells = {}
    for index, key in enumerate(("CPU", "GPU", "RAM", "VRAM")):
        cell = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        cell.pack_start(label(key, "stat-name"), False, False, 0)
        value = label("–", "stat-value")
        extra = label("", "stat-extra")
        cell.pack_start(value, False, False, 0)
        cell.pack_start(extra, False, False, 0)
        cells[key] = (value, extra)
        grid.attach(cell, index, 0, 1, 1)
    box.pack_start(grid, False, False, 0)
    panel.pack_start(box, False, False, 0)

    def update():
        for key, (percent, extra) in stats.read().items():
            value, extra_label = cells[key]
            value.set_text("–" if percent is None else f"{percent:.0f}%")
            if isinstance(extra, float):
                extra_label.set_text(f"{extra:.0f}°C")
                cc.set_class(extra_label, "hot", extra >= 85)
            else:
                extra_label.set_text(extra or "")
        return True

    update()
    timers.append(GLib.timeout_add_seconds(1, update))


def load_control_center():
    # The menu reuses the Control Center's widgets and glass look
    spec = importlib.util.spec_from_file_location("control_center", HERE / "control-center.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def menu_left_edge():
    # Lines the menu up under the click, on whichever monitor it was on
    try:
        x, y = map(int, run("hyprctl", "cursorpos").split(","))
    except ValueError:
        return 12
    for monitor in hyprctl_json("monitors") or []:
        if (monitor["x"] <= x < monitor["x"] + monitor["width"]
                and monitor["y"] <= y < monitor["y"] + monitor["height"]):
            x -= monitor["x"]
    return max(12, x - MENU_WIDTH // 2)


def open_menu(app, cc):
    from gi.repository import Gdk, GLib, Gtk, GtkLayerShell

    for css in (cc.CSS, MENU_CSS):
        provider = Gtk.CssProvider()
        provider.load_from_data(css)
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

    window = Gtk.ApplicationWindow(application=app)
    window.set_visual(window.get_screen().get_rgba_visual())
    window.set_app_paintable(True)

    # A transparent full-screen layer, so a click anywhere else closes the menu.
    # It uses the Control Center's layer name, so it gets the same frosted blur.
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
        for timer in timers:
            GLib.source_remove(timer)
        cleanup()
        revealer.set_transition_duration(cc.CLOSE_DURATION)
        revealer.set_reveal_child(False)
        GLib.timeout_add(cc.CLOSE_DURATION + 30, window.destroy)

    panel, timers, cleanup = build_menu(cc, Gtk, GLib, close)
    panel_area = Gtk.EventBox()
    panel_area.set_visible_window(False)
    panel_area.add(panel)
    panel_area.connect("button-press-event", lambda *_: True)
    left = menu_left_edge()
    panel_area.set_margin_start(min(left, cc.SHADOW_ROOM))
    panel_area.set_margin_end(cc.SHADOW_ROOM)
    panel_area.set_margin_top(6)
    panel_area.set_margin_bottom(cc.SHADOW_ROOM + 10)

    revealer.set_transition_type(Gtk.RevealerTransitionType.SLIDE_DOWN)
    revealer.set_transition_duration(cc.OPEN_DURATION)
    revealer.set_halign(Gtk.Align.START)
    revealer.set_valign(Gtk.Align.START)
    revealer.set_margin_start(max(left - cc.SHADOW_ROOM, 0))
    revealer.set_margin_top(cc.BAR_HEIGHT)
    revealer.add(panel_area)

    window.add(revealer)
    window.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
    window.connect("button-press-event", close)
    window.connect("key-press-event", lambda _w, e: e.keyval == Gdk.KEY_Escape and close())
    window.connect("map-event", lambda *_: revealer.set_reveal_child(True))
    window.show_all()
    return close


def menu():
    RUNTIME.mkdir(parents=True, exist_ok=True)
    cc = load_control_center()
    from gi.repository import Gio, Gtk

    # One copy at a time: clicking the button while it's open closes it
    app = Gtk.Application(application_id="local.waybar.GameMenu", flags=Gio.ApplicationFlags.FLAGS_NONE)
    state = {"close": None}

    def activate(application):
        if state["close"]:
            state["close"]()
        else:
            state["close"] = open_menu(application, cc)

    app.connect("activate", activate)
    app.run([sys.argv[0]])


if __name__ == "__main__":
    if "--watch" in sys.argv:
        watch()
    elif "--menu" in sys.argv:
        menu()
