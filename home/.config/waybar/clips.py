#!/usr/bin/env python3
# Clips: while switched on, keeps the last 30 seconds (or whatever length is picked in the panel)
# of your game monitor (CLIP_MONITOR) in memory (gpu-screen-recorder's replay buffer, encoded on
# the GPU) and saves it when you press Insert. It starts switched off, so nothing is recorded
# until you turn it on.
#   clips.py --daemon    Hyprland starts this at login (switched off). Runs the buffers, saves clips.
#   clips.py --toggle    Super+Insert: switch recording on or off (also a switch in the clips panel).
#   clips.py --save      Insert key: save the clip.
#   clips.py --browser   The film button in the gear drawer: the recording switch, the clip
#                        length, then every clip, newest first, grouped by day.
# Clips go to ~/Videos/Clips, named "<date> <time> <game>.mp4".
import datetime
import fcntl
import hashlib
import importlib.util
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
CLIPS = Path.home() / "Videos" / "Clips"
INCOMING = CLIPS / ".incoming"
CACHE = Path.home() / ".cache" / "clips"
RUNTIME = Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp")) / "clips"
PID = RUNTIME / "daemon.pid"
STATUS = RUNTIME / "status.json"
LOCK = RUNTIME / "daemon.lock"
LOG = RUNTIME / "recorder.log"
# Tells ~/.config/hypr/share-picker.sh which monitor the next portal request is for
PORTAL_REQUEST = RUNTIME / "portal-request"
PORTAL_ANSWER_SECONDS = 4
# The clip length you picked in the panel
SETTINGS = Path.home() / ".config" / "clips" / "settings.json"

RECORDER = "gpu-screen-recorder"
# The monitor that's recorded (your games are on the bottom one). Hyprland's screen-share
# portal can only feed the recorder one monitor at a time, so it's one or the other.
CLIP_MONITOR = "DP-3"
DEFAULT_SECONDS = 30
# The lengths offered in the panel, and the signal that asks gpu-screen-recorder for each.
# It always holds the longest one, so changing the length is instant and never restarts it
# (restarting a portal capture sometimes fails). The buffer lives in memory: about 15 MB a
# minute on the desktop, up to ~100 MB a minute in a busy game.
SAVE_SIGNALS = {10: 1, 30: 2, 60: 3, 300: 4}   # seconds -> SIGRTMIN + n
LENGTHS = tuple(SAVE_SIGNALS)
BUFFER_SECONDS = max(LENGTHS)
FPS = 60
RESTART_SECONDS = 3
STAMP = "%Y-%m-%d %H-%M-%S"
VIDEO_TYPES = (".mp4", ".mkv", ".mov", ".webm")

CLIP_ICON = ""
FILM_ICON = ""
TURN_ON_HINT = "Turn on Clip Recording in the clips panel (film button), or press Super+Insert."


def run(*command, timeout=5):
    try:
        return subprocess.run(command, capture_output=True, text=True, timeout=timeout).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def hyprctl_json(*args):
    try:
        return json.loads(run("hyprctl", "-j", *args))
    except ValueError:
        return None


def notify(title, body, icon=None):
    command = ["notify-send", "-a", "clips", "-t", "2500", title, body]
    if icon:
        command[1:1] = ["-i", str(icon)]
    subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def daemon_pid():
    try:
        pid = int(PID.read_text())
        os.kill(pid, 0)
        return pid
    except (OSError, ValueError):
        return None


def load_game_menu():
    # Borrow the game menu's game detection, so clips are named after the game
    spec = importlib.util.spec_from_file_location("game_menu", HERE / "game-menu.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def window_label(window, identify):
    """What to call a clip: the game's name, else the app's, else Desktop."""
    window_class = (window or {}).get("class") or ""
    game = identify(window_class)
    if game:
        return game[1]
    if not window_class:
        return "Desktop"
    name = window_class.rsplit(".", 1)[-1].removesuffix(".exe")
    return name[:1].upper() + name[1:]


def shown_window(monitor):
    """The window showing on a monitor (its fullscreen one, else the last one focused there)."""
    workspace = next((m["activeWorkspace"]["id"] for m in hyprctl_json("monitors") or []
                      if m["name"] == monitor), None)
    windows = [c for c in hyprctl_json("clients") or [] if c["workspace"]["id"] == workspace]
    windows.sort(key=lambda c: (not c.get("fullscreen"), c.get("focusHistoryID", 99)))
    return windows[0] if windows else None


def clip_seconds():
    """How long a saved clip is, as picked in the panel."""
    try:
        seconds = int(json.loads(SETTINGS.read_text())["seconds"])
    except (OSError, ValueError, KeyError, TypeError):
        return DEFAULT_SECONDS
    return seconds if seconds in LENGTHS else DEFAULT_SECONDS


def length_name(seconds):
    if seconds < 60:
        return f"{seconds} seconds"
    minutes = seconds // 60
    return f"{minutes} minute{'s' * (minutes != 1)}"


def safe_name(text):
    return re.sub(r"[/\\:*?\"<>|\x00-\x1f]", "", text).strip()[:48] or "Clip"


# ---- Thumbnails and lengths, cached in ~/.cache/clips ----

def clip_info(path):
    """(thumbnail path or None, length in seconds or None), making them the first time."""
    CACHE.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha1(f"{path.name}:{path.stat().st_mtime_ns}".encode()).hexdigest()[:16]
    thumb, info = CACHE / f"{key}.jpg", CACHE / f"{key}.json"
    if not thumb.exists():
        run("ffmpeg", "-v", "error", "-y", "-ss", "1", "-i", str(path), "-frames:v", "1",
            "-vf", "scale=192:-2", str(thumb), timeout=20)
    try:
        length = json.loads(info.read_text())["length"]
    except (OSError, ValueError, KeyError):
        try:
            length = float(run("ffprobe", "-v", "error", "-show_entries", "format=duration",
                               "-of", "csv=p=0", str(path), timeout=10))
            info.write_text(json.dumps({"length": length}))
        except ValueError:
            length = None
    return (thumb if thumb.exists() else None), length


# ---- The replay buffers (--daemon) ----

class Recorders:
    def __init__(self, enabled=False):
        self.enabled = enabled
        self.running = {}        # monitor name -> Popen
        self.failed_at = {}      # monitor name -> time it last died
        self.waiting = {}        # monitor name -> (label, time) for a save in progress
        self.lock = threading.Lock()
        self.games = load_game_menu()

    def command(self):
        # Portal capture: Hyprland hands over each frame. Capturing straight from the GPU
        # (-w <monitor>, KMS) made the desktop stutter while recording. "-fm content" only
        # encodes when the screen actually changes. The monitor comes from share-picker.sh.
        return [RECORDER, "-w", "portal", "-fm", "content", "-f", str(FPS), "-r", str(BUFFER_SECONDS),
                "-c", "mp4", "-k", "h264", "-ac", "aac", "-q", "very_high", "-a", "default_output",
                "-v", "no", "-o", str(INCOMING)]

    def start(self, monitor):
        """Starts the buffer, answering its portal request with the monitor to record."""
        INCOMING.mkdir(parents=True, exist_ok=True)
        PORTAL_REQUEST.write_text(monitor)
        # Without nodcc the recorder asks Hyprland for compressed (DCC) frames it then can't read,
        # and portal capture fails. This only affects the recorder, not games or the desktop.
        env = {**os.environ, "AMD_DEBUG": "nodcc"}
        with open(LOG, "a") as log:
            process = subprocess.Popen(self.command(), stdout=subprocess.PIPE, stderr=log, text=True, env=env)
        deadline = time.time() + PORTAL_ANSWER_SECONDS
        while PORTAL_REQUEST.exists() and time.time() < deadline and process.poll() is None:
            time.sleep(0.1)
        # Never leave a request lying around for some other app's screen share to pick up
        PORTAL_REQUEST.unlink(missing_ok=True)
        return process

    @staticmethod
    def clip_monitor():
        """CLIP_MONITOR, or whichever monitor is plugged in if it isn't."""
        names = [m["name"] for m in hyprctl_json("monitors") or []]
        return CLIP_MONITOR if CLIP_MONITOR in names else (names[0] if names else None)

    def keep_running(self):
        """While switched on, keeps the clip monitor's buffer running (restarting it if it
        stops). Stops it when switched off."""
        if not shutil.which(RECORDER):
            return
        monitors = {self.clip_monitor()} - {None} if self.enabled else set()
        for name, process in list(self.running.items()):
            if process.poll() is not None:
                self.failed_at[name] = time.time()
                del self.running[name]
            elif name not in monitors:
                process.terminate()
                del self.running[name]
        for name in monitors - self.running.keys():
            if time.time() - self.failed_at.get(name, 0) < RESTART_SECONDS:
                continue
            process = self.start(name)
            self.running[name] = process
            threading.Thread(target=self.read_saves, args=(name, process), daemon=True).start()

    def save(self):
        """Saves the last clip_seconds() of the clip monitor, wherever your mouse is."""
        if not shutil.which(RECORDER):
            notify("Clips need gpu-screen-recorder", "Install it with: sudo pacman -S gpu-screen-recorder")
            return
        if not self.enabled:
            notify("Clip recording is off", TURN_ON_HINT)
            return
        monitor = self.clip_monitor()
        process = self.running.get(monitor)
        if not process or process.poll() is not None:
            notify("Clip not saved", "The recorder is still starting. Try again in a moment.")
            return
        label = window_label(shown_window(monitor), self.games.identify)
        seconds = clip_seconds()
        with self.lock:
            self.waiting[monitor] = (label, seconds, datetime.datetime.now())
        process.send_signal(signal.SIGRTMIN + SAVE_SIGNALS[seconds])

    def toggle(self):
        self.enabled = not self.enabled
        self.keep_running()
        if self.enabled:
            notify("Clip recording on", f"Insert saves the last {length_name(clip_seconds())}.")
        else:
            notify("Clip recording off", "Nothing is being recorded.")

    def read_saves(self, monitor, process):
        # gpu-screen-recorder prints each saved replay's path on its own line
        for line in process.stdout:
            found = re.search(r"(/\S.*\.(?:mp4|mkv))", line)
            if found and Path(found.group(1)).exists():
                self.finish(monitor, Path(found.group(1)))
        process.wait()  # Reaps the recorder once it has stopped

    def finish(self, monitor, saved):
        with self.lock:
            label, seconds, when = self.waiting.pop(monitor, ("Clip", clip_seconds(), datetime.datetime.now()))
        target = CLIPS / f"{when.strftime(STAMP)} {safe_name(label)}{saved.suffix}"
        number = 2
        while target.exists():
            target = CLIPS / f"{when.strftime(STAMP)} {safe_name(label)} {number}{saved.suffix}"
            number += 1
        saved.rename(target)
        thumb, _ = clip_info(target)
        notify("Clip saved", f"{label}  ·  last {length_name(seconds)}", thumb)
        subprocess.Popen([str(Path.home() / ".local/bin/ui-sound"), "clip"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def write_status(self):
        status = {"installed": bool(shutil.which(RECORDER)),
                  "enabled": self.enabled,
                  "monitors": sorted(n for n, p in self.running.items() if p.poll() is None)}
        temporary = STATUS.with_suffix(".tmp")
        temporary.write_text(json.dumps(status))
        temporary.replace(STATUS)

    def stop(self):
        for process in self.running.values():
            process.terminate()
        for process in self.running.values():
            try:
                process.wait(3)
            except subprocess.TimeoutExpired:
                process.kill()


def daemon():
    RUNTIME.mkdir(parents=True, exist_ok=True)
    CLIPS.mkdir(parents=True, exist_ok=True)
    lock = open(LOCK, "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return  # Already running
    # Buffers and a portal request left behind by a daemon that was killed
    run("pkill", "-f", f"{RECORDER} .*-o {INCOMING}")
    PORTAL_REQUEST.unlink(missing_ok=True)
    PID.write_text(str(os.getpid()))

    recorders = Recorders(enabled="--enabled" in sys.argv)
    requests = []
    signal.signal(signal.SIGUSR1, lambda *_: requests.append("save"))
    signal.signal(signal.SIGUSR2, lambda *_: requests.append("toggle"))
    signal.signal(signal.SIGTERM, lambda *_: (recorders.stop(), sys.exit(0)))

    while True:
        recorders.keep_running()
        while requests:
            if requests.pop(0) == "toggle":
                recorders.toggle()
            else:
                recorders.save()
        recorders.write_status()
        time.sleep(0.25 if requests else 1)


def start_daemon(*extra):
    subprocess.Popen([sys.executable, __file__, "--daemon", *extra], start_new_session=True,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def save():
    pid = daemon_pid()
    if pid:
        os.kill(pid, signal.SIGUSR1)
        return
    # Not running (e.g. before the first login after setup): start it, switched off
    start_daemon()
    notify("Clip recording is off", TURN_ON_HINT)


def toggle():
    pid = daemon_pid()
    if pid:
        os.kill(pid, signal.SIGUSR2)
        return
    start_daemon("--enabled")
    notify("Clip recording on", f"Insert saves the last {length_name(clip_seconds())}.")


def set_length(seconds):
    # Read at each save, so it takes effect straight away
    SETTINGS.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS.write_text(json.dumps({"seconds": seconds}))


def read_status():
    try:
        status = json.loads(STATUS.read_text()) if daemon_pid() else {}
    except (OSError, ValueError):
        status = {}
    status.setdefault("installed", bool(shutil.which(RECORDER)))
    return status


# ---- The clips panel (--browser) ----

PANEL_WIDTH = 380
LIST_HEIGHT = 470
THUMB_WIDTH = 96
THUMB_HEIGHT = 54
DELETE_SECONDS = 3
PREVIEW_DELAY = 250   # ms of hovering a thumbnail before its clip starts playing
PREVIEW_SCALE = 2     # Decodes the preview at twice the thumbnail size, then shrinks it

BROWSER_CSS = b"""
.clips-icon {
    background-color: #ff375f;
    border-radius: 11px;
    min-width: 40px;
    min-height: 40px;
    font-size: 17px;
}
.clips-title { font-size: 15px; font-weight: 700; }
.heading {
    font-size: 10px;
    font-weight: 700;
    color: rgba(255, 255, 255, 0.45);
    margin: 6px 4px 0 4px;
}
.note { font-size: 10px; color: rgba(255, 255, 255, 0.45); margin: 0 4px; }
.clip-row { padding: 7px; transition: background-color 140ms ease-out; }
.clip-row.hover { background-color: rgba(255, 255, 255, 0.12); }
.clip-row.pressed { background-color: rgba(255, 255, 255, 0.05); }
.clip-name { font-size: 12px; font-weight: 600; }
.clip-sub { font-size: 10px; color: rgba(255, 255, 255, 0.55); }
.trash { font-size: 11px; color: rgba(255, 255, 255, 0.55); }
button.flat.armed, button.flat.armed:hover { background-color: #ff453a; }
button.flat.armed .trash { color: #ffffff; }
button.flat.copied .trash { color: #30d158; }
entry.rename {
    background-color: rgba(255, 255, 255, 0.1);
    border: 1px solid #0a84ff;
    border-radius: 6px;
    box-shadow: none;
    padding: 1px 6px;
    min-height: 0;
    font-size: 12px;
    font-weight: 600;
    color: #ffffff;
    caret-color: #ffffff;
}
entry.rename selection { background-color: rgba(10, 132, 255, 0.6); }
.empty { font-size: 12px; color: rgba(255, 255, 255, 0.55); padding: 18px 6px; }
.length-icon { font-size: 13px; color: rgba(255, 255, 255, 0.75); min-width: 30px; }
button.pill {
    background-image: none;
    background-color: rgba(255, 255, 255, 0.08);
    border: none;
    box-shadow: none;
    text-shadow: none;
    outline-width: 0;
    border-radius: 10px;
    padding: 2px 9px;
    min-height: 0;
    font-size: 11px;
    transition: background-color 140ms ease-out;
}
button.pill:hover { background-color: rgba(255, 255, 255, 0.15); }
button.pill.selected, button.pill.selected:hover { background-color: #0a84ff; }
scrolledwindow, viewport { background-color: transparent; border: none; }
scrollbar { background-color: transparent; border: none; }
scrollbar slider {
    background-color: rgba(255, 255, 255, 0.25);
    min-width: 4px;
    border-radius: 2px;
    border: none;
}
"""


def list_clips():
    """Every clip, newest first, as (when, title, path)."""
    clips = []
    for path in CLIPS.glob("*"):
        if path.suffix.lower() not in VIDEO_TYPES or not path.is_file():
            continue
        found = re.search(r"(\d{4}-\d{2}-\d{2})[ _](\d{2}-\d{2}-\d{2})", path.stem)
        try:
            when = datetime.datetime.strptime(f"{found.group(1)} {found.group(2)}", STAMP)
            title = (path.stem[:found.start()] + path.stem[found.end():]).strip(" _-") or "Clip"
        except (AttributeError, ValueError):
            when = datetime.datetime.fromtimestamp(path.stat().st_mtime)
            title = path.stem
        if title.lower() == "replay":
            title = "Clip"
        clips.append((when, title, path))
    return sorted(clips, key=lambda clip: clip[0], reverse=True)


def day_heading(day):
    today = datetime.date.today()
    if day == today:
        return "Today"
    if day == today - datetime.timedelta(days=1):
        return "Yesterday"
    text = day.strftime("%A %-d %B")
    return text if day.year == today.year else f"{text} {day.year}"


def file_size(size):
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1000 or unit == "GB":
            return f"{size:.0f} {unit}" if unit in ("B", "KB", "MB") else f"{size:.1f} {unit}"
        size /= 1000


def length_text(seconds):
    seconds = round(seconds or 0)
    return f"{seconds // 60}:{seconds % 60:02d}"


def build_browser(cc, Gtk, GLib, close):
    from gi.repository import Gdk, GdkPixbuf, Pango

    cc.ICONS.update({"film": FILM_ICON, "folder": "", "trash": "", "clip": CLIP_ICON, "length": "\uf2f2",
                     "pencil": "\uf303", "copy": "\uf0c5", "check": "\uf00c"})
    panel = cc.css_classes(Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8), "panel")
    panel.set_size_request(PANEL_WIDTH, -1)
    clips = list_clips()
    timers = []

    def label(text, css_class, xalign=0):
        return cc.css_classes(Gtk.Label(label=text, xalign=xalign), css_class)

    # Header: how many clips, how much space, and a button for the folder
    header = cc.css_classes(Gtk.Box(spacing=11), "tile")
    icon = cc.icon_label("film", "clips-icon")
    icon.set_valign(Gtk.Align.CENTER)
    header.pack_start(icon, False, False, 0)
    titles = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
    titles.set_valign(Gtk.Align.CENTER)
    titles.pack_start(label("Clips", "clips-title"), False, False, 0)
    summary = label("", "tile-sub")
    titles.pack_start(summary, False, False, 0)
    header.pack_start(titles, True, True, 0)
    folder = cc.css_classes(Gtk.Button(), "flat")
    folder.add(cc.icon_label("folder", "arrow"))
    folder.set_valign(Gtk.Align.CENTER)
    folder.set_tooltip_text("Open the Clips folder")
    folder.connect("clicked", lambda *_: (CLIPS.mkdir(parents=True, exist_ok=True),
                                          cc.spawn("xdg-open", str(CLIPS)), close()))
    header.pack_start(folder, False, False, 0)
    panel.pack_start(header, False, False, 0)

    # Recording switch: nothing is recorded until it's on, then Insert saves a clip
    if read_status()["installed"]:
        def read_recording():
            return bool(read_status().get("enabled")), ""

        def write_recording(on):
            if read_recording()[0] == on:
                return
            toggle()
            # The recorder answers within a second; wait so the switch shows the real state
            for _ in range(30):
                time.sleep(0.1)
                if read_recording()[0] == on:
                    return

        recording, switch = cc.toggle_tile("clip", "Clip Recording", read_recording, write_recording)
        # Just the title, centred beside the switch
        switch.subtitle.set_no_show_all(True)
        switch.subtitle.hide()
        switch.refresh()
        panel.pack_start(recording, False, False, 0)

        # Clip length: how far back Insert reaches
        length = cc.css_classes(Gtk.Box(spacing=9), "tile")
        length.pack_start(cc.icon_label("length", "length-icon"), False, False, 0)
        length.pack_start(label("Clip Length", "tile-title"), True, True, 0)
        pills = []
        for seconds in LENGTHS:
            pill = cc.css_classes(Gtk.Button(label=f"{seconds}s" if seconds < 60 else f"{seconds // 60}m"), "pill")
            pill.set_valign(Gtk.Align.CENTER)
            pill.set_tooltip_text(f"Save the last {length_name(seconds)}")
            cc.set_class(pill, "selected", seconds == clip_seconds())
            pills.append(pill)

            def choose(_button, seconds=seconds, chosen=pill):
                if seconds != clip_seconds():
                    set_length(seconds)
                for other in pills:
                    cc.set_class(other, "selected", other is chosen)
            pill.connect("clicked", choose)
            length.pack_start(pill, False, False, 0)
        panel.pack_start(length, False, False, 0)
    else:
        note = label("Clips need gpu-screen-recorder: sudo pacman -S gpu-screen-recorder", "note")
        note.set_line_wrap(True)
        panel.pack_start(note, False, False, 0)

    # One entry per clip. Its path changes when it's renamed
    entries = [{"when": when, "title": title, "path": path, "pixbuf": None, "length": None,
                "frame": None, "player": None} for when, title, path in clips]
    editing = {"cancel": None}
    panel.cancel_edit = lambda: bool(editing["cancel"]) and (editing["cancel"](), True)[1]

    def update_summary():
        alive = [entry for entry in entries if entry["path"].exists()]
        total = sum(entry["path"].stat().st_size for entry in alive)
        count = len(alive)
        summary.set_text(f"{count} clip{'s' * (count != 1)}  ·  {file_size(total)}" if count else "No clips yet")

    update_summary()

    if not clips:
        panel.pack_start(label("Your clips will show up here, newest first.", "empty", 0.5), False, False, 0)
        return panel, timers, lambda: None

    scroller = Gtk.ScrolledWindow()
    scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
    scroller.set_propagate_natural_height(True)
    scroller.set_max_content_height(LIST_HEIGHT)
    rows = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
    scroller.add(rows)
    panel.pack_start(scroller, False, False, 0)

    headings = {}     # day -> (heading label, [row widgets])

    def draw_thumb(area, cr, entry):
        radius = 6
        cr.new_sub_path()
        for x, y, start in ((THUMB_WIDTH - radius, radius, -90), (THUMB_WIDTH - radius, THUMB_HEIGHT - radius, 0),
                            (radius, THUMB_HEIGHT - radius, 90), (radius, radius, 180)):
            cr.arc(x, y, radius, start * 3.14159 / 180, (start + 90) * 3.14159 / 180)
        cr.close_path()
        cr.clip()
        picture = entry["frame"] or entry["pixbuf"]
        if picture:
            Gdk.cairo_set_source_pixbuf(cr, picture, 0, 0)
            cr.paint()
        else:
            cr.set_source_rgba(1, 1, 1, 0.1)
            cr.paint()
        # Length badge in the bottom-right corner, like a video player's thumbnail
        if entry["length"] and not entry["frame"]:
            text = length_text(entry["length"])
            cr.select_font_face("Inter")
            cr.set_font_size(9)
            extents = cr.text_extents(text)
            width, height = extents.x_advance + 8, 13
            cc.rounded_rect(cr, THUMB_WIDTH - width - 4, THUMB_HEIGHT - height - 4, width, height, 4)
            cr.set_source_rgba(0, 0, 0, 0.6)
            cr.fill()
            cr.set_source_rgba(1, 1, 1, 0.95)
            cr.move_to(THUMB_WIDTH - width, THUMB_HEIGHT - 4 - (height - extents.height) / 2 - 1)
            cr.show_text(text)

    # ---- Hover preview: the clip plays silently inside its thumbnail ----

    def start_preview(entry):
        entry["hover_timer"] = None
        if entry["player"] or not entry["path"].exists():
            return False
        try:
            gi_require_gst()
            from gi.repository import Gst
        except (ImportError, ValueError):
            return False
        player = Gst.parse_launch(
            f"filesrc name=source ! decodebin ! videoconvert ! videoscale ! "
            f"video/x-raw,width={THUMB_WIDTH * PREVIEW_SCALE},height={THUMB_HEIGHT * PREVIEW_SCALE},"
            f"pixel-aspect-ratio=1/1 ! gdkpixbufsink name=sink")
        player.get_by_name("source").set_property("location", str(entry["path"]))
        bus = player.get_bus()
        bus.add_signal_watch()

        def on_message(_bus, message):
            if entry["player"] is not player:
                return
            if message.type == Gst.MessageType.ELEMENT and message.get_structure().get_name() == "pixbuf":
                frame = message.get_structure().get_value("pixbuf")
                entry["frame"] = frame.scale_simple(THUMB_WIDTH, THUMB_HEIGHT, GdkPixbuf.InterpType.BILINEAR)
                entry["area"].queue_draw()
            elif message.type == Gst.MessageType.EOS:
                player.seek_simple(Gst.Format.TIME, Gst.SeekFlags.FLUSH, 0)  # Loop
            elif message.type == Gst.MessageType.ERROR:
                stop_preview(entry)

        bus.connect("message", on_message)
        entry["player"] = player
        player.set_state(Gst.State.PLAYING)
        return False

    def stop_preview(entry):
        if entry.get("hover_timer"):
            GLib.source_remove(entry["hover_timer"])
            entry["hover_timer"] = None
        player, entry["player"] = entry["player"], None
        if player:
            from gi.repository import Gst
            player.get_bus().remove_signal_watch()
            player.set_state(Gst.State.NULL)
        entry["frame"] = None
        entry["area"].queue_draw()

    def remove_row(entry, row):
        day_label, day_rows = headings[entry["when"].date()]
        day_rows.remove(row)
        row.destroy()
        entries.remove(entry)
        if not day_rows:
            day_label.destroy()
        update_summary()

    def action_button(icon, tooltip):
        button = cc.css_classes(Gtk.Button(), "flat")
        button.add(cc.icon_label(icon, "trash"))
        button.set_valign(Gtk.Align.CENTER)
        button.set_tooltip_text(tooltip)
        return button

    def build_row(entry):
        area = Gtk.EventBox()
        area.set_visible_window(False)
        row = cc.css_classes(Gtk.Box(spacing=10), "tile", "clip-row")
        area.add(row)

        thumb_box = Gtk.EventBox()
        thumb_box.set_visible_window(False)
        thumb = Gtk.DrawingArea()
        thumb.set_size_request(THUMB_WIDTH, THUMB_HEIGHT)
        thumb_box.set_valign(Gtk.Align.CENTER)
        thumb_box.add(thumb)
        entry["area"] = thumb
        thumb.connect("draw", draw_thumb, entry)
        row.pack_start(thumb_box, False, False, 0)

        def on_thumb_hover(_box, event, inside):
            if event.detail == Gdk.NotifyType.INFERIOR:
                return
            if inside and not entry["player"] and not entry.get("hover_timer"):
                entry["hover_timer"] = GLib.timeout_add(PREVIEW_DELAY, start_preview, entry)
            elif not inside:
                stop_preview(entry)

        thumb_box.connect("enter-notify-event", on_thumb_hover, True)
        thumb_box.connect("leave-notify-event", on_thumb_hover, False)

        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        text.set_valign(Gtk.Align.CENTER)
        name = label(entry["title"], "clip-name")
        name.set_ellipsize(Pango.EllipsizeMode.END)
        text.pack_start(name, False, False, 0)
        sub = f"{entry['when'].strftime('%-I:%M %p')}  ·  {file_size(entry['path'].stat().st_size)}"
        text.pack_start(label(sub, "clip-sub"), False, False, 0)
        row.pack_start(text, True, True, 0)

        # Rename: the title turns into a text box. Enter saves, Esc cancels
        rename = action_button("pencil", "Rename")

        def on_rename(*_):
            if editing["cancel"]:
                editing["cancel"]()
            entry_box = cc.css_classes(Gtk.Entry(text=entry["title"]), "rename")
            entry_box.set_width_chars(1)
            name.hide()
            text.pack_start(entry_box, False, False, 0)
            text.reorder_child(entry_box, 0)
            entry_box.show()
            entry_box.grab_focus()
            entry_box.select_region(0, -1)
            done = {"finished": False}

            def finish(save):
                if done["finished"]:
                    return
                done["finished"] = True
                editing["cancel"] = None
                new_title = safe_name(entry_box.get_text()) if save else entry["title"]
                if save and new_title != entry["title"]:
                    target = rename_clip(entry["path"], entry["when"], new_title)
                    if target:
                        entry["path"], entry["title"] = target, new_title
                name.set_text(entry["title"])
                entry_box.destroy()
                name.show()

            entry_box.connect("activate", lambda *_: finish(True))
            entry_box.connect("focus-out-event", lambda *_: finish(True) and False)
            editing["cancel"] = lambda: finish(False)

        rename.connect("clicked", on_rename)
        row.pack_start(rename, False, False, 0)

        # Copy: puts the clip file on the clipboard, ready to paste into Discord or a browser
        copy = action_button("copy", "Copy to clipboard")

        def on_copy(*_):
            cc.spawn("wl-copy", "--type", "text/uri-list", entry["path"].as_uri())
            holder = copy.get_child()
            holder.set_text(cc.ICONS["check"])
            cc.set_class(copy, "copied", True)
            copy.set_tooltip_text("Copied. Paste it into a chat")

            def reset():
                holder.set_text(cc.ICONS["copy"])
                cc.set_class(copy, "copied", False)
                copy.set_tooltip_text("Copy to clipboard")
                return False
            GLib.timeout_add(1500, reset)

        copy.connect("clicked", on_copy)
        row.pack_start(copy, False, False, 0)

        # Delete needs a second click within a few seconds, and moves the clip to the Trash
        trash = action_button("trash", "Move to Trash")
        armed = {"timer": None}

        def disarm():
            armed["timer"] = None
            cc.set_class(trash, "armed", False)
            trash.set_tooltip_text("Move to Trash")
            return False

        def on_trash(*_):
            if armed["timer"] is None:
                cc.set_class(trash, "armed", True)
                trash.set_tooltip_text("Click again to move to Trash")
                armed["timer"] = GLib.timeout_add_seconds(DELETE_SECONDS, disarm)
                return
            GLib.source_remove(armed["timer"])
            armed["timer"] = None
            stop_preview(entry)
            run("gio", "trash", str(entry["path"]))
            if not entry["path"].exists():
                remove_row(entry, area)

        trash.connect("clicked", on_trash)
        row.pack_start(trash, False, False, 0)

        def on_hover(_area, event, inside):
            if event.detail != Gdk.NotifyType.INFERIOR:
                cc.set_class(row, "hover", inside)

        def on_click(_area, event):
            if event.button == 1 and not editing["cancel"]:
                stop_preview(entry)
                cc.spawn("xdg-open", str(entry["path"]))
                close()

        area.connect("enter-notify-event", on_hover, True)
        area.connect("leave-notify-event", on_hover, False)
        area.connect("button-release-event", on_click)
        return area

    for entry in entries:
        day = entry["when"].date()
        if day not in headings:
            heading = label(day_heading(day).upper(), "heading")
            rows.pack_start(heading, False, False, 0)
            headings[day] = (heading, [])
        row = build_row(entry)
        headings[day][1].append(row)
        rows.pack_start(row, False, False, 0)

    # Thumbnails and lengths load in the background, newest first
    stopped = threading.Event()

    def load_thumbs():
        for entry in list(entries):
            if stopped.is_set():
                return
            try:
                thumb, length = clip_info(entry["path"])
            except OSError:
                continue
            GLib.idle_add(show_thumb, entry, thumb, length)

    def show_thumb(entry, thumb, length):
        entry["length"] = length
        if thumb:
            try:
                pixbuf = GdkPixbuf.Pixbuf.new_from_file(str(thumb))
                # Fill the frame, cropping whatever doesn't fit
                scale = max(THUMB_WIDTH / pixbuf.get_width(), THUMB_HEIGHT / pixbuf.get_height())
                width, height = round(pixbuf.get_width() * scale), round(pixbuf.get_height() * scale)
                scaled = pixbuf.scale_simple(width, height, GdkPixbuf.InterpType.BILINEAR)
                entry["pixbuf"] = scaled.new_subpixbuf((width - THUMB_WIDTH) // 2, (height - THUMB_HEIGHT) // 2,
                                                       THUMB_WIDTH, THUMB_HEIGHT)
            except GLib.Error:
                pass
        entry["area"].queue_draw()
        return False

    def cleanup():
        stopped.set()
        for entry in entries:
            stop_preview(entry)

    # Tall enough for every clip, up to LIST_HEIGHT, then it scrolls
    rows.show_all()
    scroller.set_min_content_height(min(rows.get_preferred_height()[1], LIST_HEIGHT))

    threading.Thread(target=load_thumbs, daemon=True).start()
    return panel, timers, cleanup


def gi_require_gst():
    import gi
    gi.require_version("Gst", "1.0")
    from gi.repository import Gst
    if not Gst.is_initialized():
        Gst.init(None)


def rename_clip(path, when, title):
    """Renames a clip to "<date> <time> <title>", keeping its date. Returns the new path."""
    target = path.with_name(f"{when.strftime(STAMP)} {title}{path.suffix}")
    number = 2
    while target.exists() and target != path:
        target = path.with_name(f"{when.strftime(STAMP)} {title} {number}{path.suffix}")
        number += 1
    try:
        path.rename(target)
    except OSError:
        return None
    return target


def load_control_center():
    # The panel reuses the Control Center's widgets and glass look
    spec = importlib.util.spec_from_file_location("control_center", HERE / "control-center.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def panel_left_edge(width=PANEL_WIDTH):
    # Lines the panel up under the click, on whichever monitor it was on
    try:
        x, y = map(int, run("hyprctl", "cursorpos").split(","))
    except ValueError:
        return 12
    for monitor in hyprctl_json("monitors") or []:
        if (monitor["x"] <= x < monitor["x"] + monitor["width"]
                and monitor["y"] <= y < monitor["y"] + monitor["height"]):
            x -= monitor["x"]
    return max(12, x - width // 2)


def open_panel(app, cc, build, width, extra_css):
    """Opens a glass panel under the click. build(cc, Gtk, GLib, close) returns
    (panel, timers, cleanup). Also used by gear-tools.py."""
    from gi.repository import Gdk, GLib, Gtk, GtkLayerShell

    for css in (cc.CSS, extra_css):
        provider = Gtk.CssProvider()
        provider.load_from_data(css)
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

    window = Gtk.ApplicationWindow(application=app)
    window.set_visual(window.get_screen().get_rgba_visual())
    window.set_app_paintable(True)

    # A transparent full-screen layer, so a click anywhere else closes the panel.
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
    revealer.set_opacity(0)

    def close(*_):
        if state["closing"]:
            return
        state["closing"] = True
        for timer in timers:
            GLib.source_remove(timer)
        cleanup()
        cc.sound("close")
        cc.fade_out(revealer)
        revealer.set_transition_duration(cc.CLOSE_DURATION)
        revealer.set_reveal_child(False)
        GLib.timeout_add(cc.CLOSE_DURATION + 30, window.destroy)

    panel, timers, cleanup = build(cc, Gtk, GLib, close)
    panel_area = Gtk.EventBox()
    panel_area.set_visible_window(False)
    panel_area.add(panel)
    panel_area.connect("button-press-event", lambda *_: True)
    left = panel_left_edge(width)
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
    # Esc cancels an edit first (like a rename), then closes the panel
    window.connect("key-press-event", lambda _w, e: e.keyval == Gdk.KEY_Escape
                   and not getattr(panel, "cancel_edit", lambda: False)() and close())
    window.connect("map-event", lambda *_: (revealer.set_reveal_child(True),
                                            cc.spring_in(revealer, cc.BAR_HEIGHT), cc.sound("open")))
    window.show_all()
    return close


def run_panel(app_id, build, width, extra_css):
    cc = load_control_center()
    from gi.repository import Gio, Gtk

    # One copy at a time: clicking the button while it's open closes it
    app = Gtk.Application(application_id=app_id, flags=Gio.ApplicationFlags.FLAGS_NONE)
    state = {"close": None}

    def activate(application):
        if state["close"]:
            state["close"]()
        else:
            state["close"] = open_panel(application, cc, build, width, extra_css)

    app.connect("activate", activate)
    app.run([sys.argv[0]])


def browser():
    run_panel("local.waybar.Clips", build_browser, PANEL_WIDTH, BROWSER_CSS)


if __name__ == "__main__":
    if "--daemon" in sys.argv:
        daemon()
    elif "--toggle" in sys.argv:
        toggle()
    elif "--save" in sys.argv:
        save()
    elif "--browser" in sys.argv:
        browser()
