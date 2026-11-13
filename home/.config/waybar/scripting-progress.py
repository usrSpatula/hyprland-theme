#!/usr/bin/env python3
# Waybar module: game scripting progress, read from scripting-progress.json
# No arguments prints the percentage. --image draws the bar. --popup opens the details panel.
# The in-depth notes live in scripting-progress-docs/, and the Lua techniques to
# learn for each script and function live in scripting-progress-docs/techniques/.
import html
import json
import math
import os
import re
import struct
import subprocess
import sys
import tomllib
import zlib
from pathlib import Path

TRACK_WIDTH = 130
TRACK_HEIGHT = 6
TRACK_COLOR = (1, 1, 1, 0.22)
FILL_COLOR = (1, 1, 1, 1)
BAR_HEIGHT = 28
PANEL_WIDTH = 235
LIST_MAX_HEIGHT = 320
DETAILS_WIDTH = 566
DETAILS_GAP = 10
SHADOW_ROOM = 36
DETAILS_MAX_HEIGHT = 720
OPEN_DURATION = 260
CLOSE_DURATION = 170
ROW_DURATION = 170
PAGE_DURATION = 130
FADE_SECONDS = 0.14
SCROLL_STEP = 48
SCROLL_SPEED = 14
GREEN = "#30d158"
ORANGE = "#ff9f0a"
GRAY = "#8e8e93"
INLINE_CODE = "#9cd0ff"
MONO_FONT = "Noto Sans Mono"
TECHNIQUE_COLOR = "#64d2ff"
STATUS_VALUES = {"done": 1, "partial": 0.5, "todo": 0}
STATUS_TEXT = {"done": "Done", "partial": "Needs work", "todo": "To do"}

CSS = b"""
* { font-family: "Inter", "Noto Sans", sans-serif; }
window { background-color: transparent; }
.panel {
    background-color: rgba(36, 36, 38, 0.94);
    border: 1px solid rgba(255, 255, 255, 0.14);
    border-radius: 11px;
    box-shadow: 0 6px 16px rgba(0, 0, 0, 0.4);
    color: #ffffff;
}
.header { padding: 10px 12px 9px 12px; }
.title { font-size: 13px; font-weight: 700; }
.score { font-size: 16px; font-weight: 700; color: #0a84ff; }
.muted { color: rgba(255, 255, 255, 0.55); font-size: 10px; }
progressbar trough {
    min-height: 4px;
    border: none;
    border-radius: 2px;
    background-color: rgba(255, 255, 255, 0.14);
}
progressbar progress {
    min-height: 4px;
    border: none;
    border-radius: 2px;
    background-image: none;
    background-color: #0a84ff;
}
.separator { background-color: rgba(255, 255, 255, 0.09); min-height: 1px; }
scrollbar, scrollbar trough {
    background: transparent;
    border: none;
    min-width: 4px;
    margin: 0;
    padding: 0;
}
scrollbar slider {
    min-width: 4px;
    margin: 0;
    border: none;
    border-radius: 2px;
    background-color: rgba(255, 255, 255, 0.25);
}
.list { padding: 4px 6px; }
.group-row, .script-row, .function-row, .copy-button {
    background-image: none;
    background-color: transparent;
    border: none;
    border-radius: 6px;
    box-shadow: none;
    color: #ffffff;
    outline-width: 0;
    text-shadow: none;
}
.group-row { padding: 5px 6px; }
.script-row { padding: 3px 6px; }
.function-row { padding: 3px 5px; }
.group-row:hover, .script-row:hover, .function-row:hover {
    background-color: rgba(255, 255, 255, 0.09);
}
.group-row.open, .script-row.open { background-color: rgba(255, 255, 255, 0.06); }
.script-row.selected, .function-row.selected, .script-row.selected:hover, .function-row.selected:hover {
    background-color: rgba(10, 132, 255, 0.55);
}
.function-row.selected .function-desc, .script-row.selected .muted {
    color: rgba(255, 255, 255, 0.8);
}
.group-name { font-size: 11px; font-weight: 700; }
.script-name { font-size: 11px; font-weight: 600; }
.chevron { color: rgba(255, 255, 255, 0.45); font-size: 10px; }
.percent { font-size: 10px; font-weight: 600; }
.percent.zero { color: rgba(255, 255, 255, 0.4); }
.scripts { padding: 2px 0 3px 7px; }
.functions { padding: 3px 2px 6px 13px; }
.function-name { font-size: 10px; font-weight: 500; }
.function-desc { color: rgba(255, 255, 255, 0.58); font-size: 9px; }
.status-icon { font-size: 9px; }
.legend { padding: 6px 12px 8px 12px; }
.details-page { padding: 16px 18px 18px 18px; }
.details-title { font-size: 17px; font-weight: 700; }
.details-kind { color: rgba(255, 255, 255, 0.55); font-size: 11px; }
.details-status { font-size: 12px; font-weight: 600; }
.details-summary { color: rgba(255, 255, 255, 0.72); font-size: 12px; }
.section-heading {
    color: rgba(255, 255, 255, 0.5);
    font-size: 11px;
    font-weight: 700;
}
.body-text { color: rgba(255, 255, 255, 0.9); font-size: 12px; }
.bullet { color: rgba(255, 255, 255, 0.45); font-size: 12px; }
.technique {
    background-color: rgba(100, 210, 255, 0.06);
    border: 1px solid rgba(100, 210, 255, 0.22);
    border-radius: 8px;
    padding: 10px 12px;
}
.technique-name { color: #64d2ff; font-size: 13px; font-weight: 700; }
.technique-label {
    color: rgba(255, 255, 255, 0.5);
    font-size: 10px;
    font-weight: 700;
}
.next-steps {
    background-color: rgba(255, 159, 10, 0.1);
    border: 1px solid rgba(255, 159, 10, 0.35);
    border-radius: 8px;
    padding: 10px 12px;
}
.next-heading { color: #ff9f0a; font-size: 11px; font-weight: 700; }
.step-number { color: #ff9f0a; font-size: 12px; font-weight: 700; }
"""


def script_progress(script):
    functions = script["functions"]
    return sum(STATUS_VALUES[f["status"]] for f in functions) / len(functions)


def load_data():
    path = Path(__file__).resolve().parent / "scripting-progress.json"
    return json.loads(path.read_text())


def load_docs():
    folder = Path(__file__).resolve().parent / "scripting-progress-docs"
    docs = {}
    for path in sorted(folder.glob("*.toml")):
        docs.update(tomllib.loads(path.read_text())["scripts"])

    # Techniques are kept in their own files, then added to each script's and function's notes
    for path in sorted((folder / "techniques").glob("*.toml")):
        for name, entry in tomllib.loads(path.read_text()).items():
            doc = docs.setdefault(name, {})
            doc["techniques"] = entry.get("techniques", [])
            functions = doc.setdefault("functions", {})
            for function, extra in entry.get("functions", {}).items():
                functions.setdefault(function, {})["techniques"] = extra.get("techniques", [])
    return docs


def weighted_progress(scripts):
    total = sum(s["weight"] for s in scripts)
    return sum(s["weight"] * script_progress(s) for s in scripts) / total


def overall_percent(data):
    scripts = [s for group in data["groups"] for s in group["scripts"]]
    return round(100 * weighted_progress(scripts))


def print_percent():
    percent = overall_percent(load_data())
    print(json.dumps({"text": f"{percent}%", "percentage": percent}))


def pill_coverage(x, y, length):
    # How much of the pixel at (x, y) is inside a rounded pill, from 0 to 1
    radius = TRACK_HEIGHT / 2
    px, py = x + 0.5, y + 0.5
    nearest_x = min(max(px, radius), length - radius)
    distance = math.hypot(px - nearest_x, py - radius)
    return min(max(radius - distance + 0.5, 0), 1)


def blend(base, top, alpha):
    # Draw an RGBA colour over an RGBA pixel, scaled by the given coverage
    top_alpha = top[3] * alpha
    out_alpha = top_alpha + base[3] * (1 - top_alpha)
    if out_alpha == 0:
        return (0, 0, 0, 0)
    rgb = [
        (t * top_alpha + b * base[3] * (1 - top_alpha)) / out_alpha
        for t, b in zip(top[:3], base[:3])
    ]
    return (*rgb, out_alpha)


def write_png(path, width, height, pixels):
    def chunk(kind, body):
        crc = zlib.crc32(kind + body)
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", crc)

    rows = b"".join(
        b"\0" + bytes(
            round(channel * 255) for pixel in pixels[y * width:(y + 1) * width] for channel in pixel
        )
        for y in range(height)
    )
    temp_path = path.with_suffix(".tmp")
    temp_path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(rows))
        + chunk(b"IEND", b"")
    )
    os.replace(temp_path, path)


def draw_bar():
    percent = overall_percent(load_data())
    # Keep any progress at least a full circle wide, so the ends stay round
    fill_length = max(TRACK_WIDTH * percent / 100, TRACK_HEIGHT) if percent > 0 else 0

    pixels = []
    for y in range(TRACK_HEIGHT):
        for x in range(TRACK_WIDTH):
            pixel = blend((0, 0, 0, 0), TRACK_COLOR, pill_coverage(x, y, TRACK_WIDTH))
            if fill_length:
                pixel = blend(pixel, FILL_COLOR, pill_coverage(x, y, fill_length))
            pixels.append(pixel)

    path = Path(__file__).resolve().parent / "scripting-progress.png"
    write_png(path, TRACK_WIDTH, TRACK_HEIGHT, pixels)
    print(path)


def load_gtk():
    import gi
    gi.require_version("Gtk", "3.0")
    from gi.repository import Gtk
    return Gtk


def rich_text(text):
    # Turns `code` and **bold** in the notes into Pango markup
    text = html.escape(text.strip(), quote=False)
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
    return re.sub(
        r"`([^`]+)`",
        rf"<span font_family='{MONO_FONT}' foreground='{INLINE_CODE}'>\1</span>",
        text,
    )


def status_icon(Gtk, status):
    icon, color = {
        "done": ("✔", GREEN),
        "partial": ("●", ORANGE),
        "todo": ("○", GRAY),
    }[status]
    label = Gtk.Label()
    label.set_markup(f"<span foreground='{color}'>{icon}</span>")
    label.set_valign(Gtk.Align.START)
    label.get_style_context().add_class("status-icon")
    return label


def progress_status(Gtk, progress):
    # A green check when finished, otherwise the percentage
    if progress >= 1:
        status = status_icon(Gtk, "done")
        status.set_valign(Gtk.Align.CENTER)
        return status
    status = Gtk.Label(label=f"{round(progress * 100)}%")
    status.get_style_context().add_class("percent")
    if progress == 0:
        status.get_style_context().add_class("zero")
    return status


def build_dropdown(Gtk, row_class, content, body, on_toggle=None):
    button = Gtk.Button()
    button.set_relief(Gtk.ReliefStyle.NONE)
    button.get_style_context().add_class(row_class)

    row = Gtk.Box(spacing=5)
    chevron = Gtk.Label(label="›")
    chevron.set_size_request(9, -1)
    chevron.get_style_context().add_class("chevron")
    row.pack_start(chevron, False, False, 0)
    row.pack_start(content, True, True, 0)
    button.add(row)

    revealer = Gtk.Revealer()
    revealer.set_transition_type(Gtk.RevealerTransitionType.SLIDE_DOWN)
    revealer.set_transition_duration(ROW_DURATION)
    revealer.add(body)

    def toggle(_button=None):
        is_open = not revealer.get_reveal_child()
        revealer.set_reveal_child(is_open)
        chevron.set_text("⌄" if is_open else "›")
        context = button.get_style_context()
        context.add_class("open") if is_open else context.remove_class("open")
        if on_toggle:
            on_toggle(is_open, button)

    button.connect("clicked", toggle)

    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    box.pack_start(button, False, False, 0)
    box.pack_start(revealer, False, False, 0)
    return box, toggle


def build_text(Gtk, markup, css_class, max_chars=70):
    from gi.repository import Pango

    label = Gtk.Label(xalign=0)
    label.set_markup(markup)
    label.set_line_wrap(True)
    label.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
    label.set_max_width_chars(max_chars)
    label.get_style_context().add_class(css_class)
    return label


def build_section(Gtk, heading, content):
    # Prose becomes paragraphs, and a list becomes bullet points
    section = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
    title = Gtk.Label(label=heading.upper(), xalign=0)
    title.get_style_context().add_class("section-heading")
    section.pack_start(title, False, False, 0)

    if isinstance(content, str):
        for paragraph in content.strip().split("\n\n"):
            section.pack_start(build_text(Gtk, rich_text(paragraph), "body-text"), False, False, 0)
        return section

    for item in content:
        row = Gtk.Box(spacing=7)
        bullet = Gtk.Label(label="•")
        bullet.set_valign(Gtk.Align.START)
        bullet.get_style_context().add_class("bullet")
        row.pack_start(bullet, False, False, 0)
        row.pack_start(build_text(Gtk, rich_text(item), "body-text", 66), True, True, 0)
        section.pack_start(row, False, False, 0)
    return section


def build_techniques(Gtk, techniques):
    # One card per technique: the idea, how it fits here, and an exercise to try separately
    section = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
    title = Gtk.Label(label="LUA TECHNIQUES TO LEARN", xalign=0)
    title.get_style_context().add_class("section-heading")
    section.pack_start(title, False, False, 0)

    for technique in techniques:
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        card.get_style_context().add_class("technique")
        name = build_text(Gtk, rich_text(technique["name"]), "technique-name", 64)
        card.pack_start(name, False, False, 0)
        for label, key in (("", "learn"), ("HOW IT FITS HERE", "here"), ("TRY IT YOURSELF", "practice")):
            if not technique.get(key):
                continue
            if label:
                heading = Gtk.Label(label=label, xalign=0)
                heading.get_style_context().add_class("technique-label")
                card.pack_start(heading, False, False, 2)
            for paragraph in technique[key].strip().split("\n\n"):
                card.pack_start(build_text(Gtk, rich_text(paragraph), "body-text", 64), False, False, 0)
        section.pack_start(card, False, False, 0)
    return section


def build_next_steps(Gtk, steps):
    # The specific work left on a function marked "Needs work"
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=7)
    box.get_style_context().add_class("next-steps")
    title = Gtk.Label(label="WHAT TO ADD NEXT", xalign=0)
    title.get_style_context().add_class("next-heading")
    box.pack_start(title, False, False, 0)

    for number, step in enumerate(steps, 1):
        row = Gtk.Box(spacing=7)
        mark = Gtk.Label(label=f"{number}.", xalign=0)
        mark.set_size_request(14, -1)
        mark.set_valign(Gtk.Align.START)
        mark.get_style_context().add_class("step-number")
        row.pack_start(mark, False, False, 0)
        row.pack_start(build_text(Gtk, rich_text(step), "body-text", 66), True, True, 0)
        box.pack_start(row, False, False, 0)
    return box


def build_details_page(Gtk, title, kind, status, summary, sections, techniques, next_steps=None):
    page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=15)
    page.get_style_context().add_class("details-page")

    top = Gtk.Box(spacing=10)
    titles = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
    name = Gtk.Label(label=title, xalign=0)
    name.get_style_context().add_class("details-title")
    titles.pack_start(name, False, False, 0)
    titles.pack_start(build_text(Gtk, html.escape(kind, quote=False), "details-kind", 60), False, False, 0)
    if summary:
        titles.pack_start(build_text(Gtk, rich_text(summary), "details-summary"), False, False, 3)
    top.pack_start(titles, True, True, 0)
    status.set_valign(Gtk.Align.START)
    top.pack_end(status, False, False, 0)
    page.pack_start(top, False, False, 0)

    for heading, content in sections:
        if content:
            page.pack_start(build_section(Gtk, heading, content), False, False, 0)
    if techniques:
        page.pack_start(build_techniques(Gtk, techniques), False, False, 0)
    if next_steps:
        page.pack_start(build_next_steps(Gtk, next_steps), False, False, 0)

    scroller = Gtk.ScrolledWindow()
    scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
    scroller.set_overlay_scrolling(False)
    scroller.set_propagate_natural_height(True)
    scroller.set_max_content_height(DETAILS_MAX_HEIGHT)
    scroller.add(page)
    add_smooth_scrolling(scroller)
    return scroller


def build_script_page(Gtk, group, script, doc):
    progress = script_progress(script)
    status = Gtk.Label(label="✔ Finished" if progress >= 1 else f"{round(progress * 100)}%")
    status.get_style_context().add_class("details-status")
    kind = f"{doc.get('type', 'Script')} in {doc.get('location', group['name'])}"
    sections = [
        ("What this script does", doc.get("about")),
        ("What it needs", doc.get("needs")),
        ("How it connects", doc.get("connects")),
    ]
    return build_details_page(
        Gtk, script["name"], kind, status, None, sections, doc.get("techniques"),
    )


def build_function_page(Gtk, script, function, doc):
    color = {"done": GREEN, "partial": ORANGE, "todo": GRAY}[function["status"]]
    status = Gtk.Label()
    status.set_markup(f"<span foreground='{color}'>{STATUS_TEXT[function['status']]}</span>")
    status.get_style_context().add_class("details-status")
    sections = [
        ("What it does", doc.get("details")),
        ("What it needs", doc.get("needs")),
        ("Watch out for", doc.get("watch")),
    ]
    next_steps = function.get("next") if function["status"] == "partial" else None
    return build_details_page(
        Gtk, function["name"], f"Part of {script['name']}", status,
        function["desc"], sections, doc.get("techniques"), next_steps,
    )


def build_details_pane(Gtk):
    # The wider panel beside the menu, showing the notes for one script or function
    stack = Gtk.Stack()
    stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
    stack.set_transition_duration(PAGE_DURATION)
    stack.set_vhomogeneous(False)
    stack.set_interpolate_size(True)

    frame = Gtk.Box()
    frame.get_style_context().add_class("panel")
    frame.pack_start(stack, True, True, 0)

    area = Gtk.EventBox()
    area.set_visible_window(False)
    area.set_size_request(DETAILS_WIDTH, -1)
    area.set_halign(Gtk.Align.START)
    area.set_valign(Gtk.Align.START)
    area.set_margin_start(DETAILS_GAP)
    area.set_no_show_all(True)
    area.set_opacity(0)
    area.add(frame)
    area.connect("button-press-event", lambda *_: True)

    state = {"key": None, "row": None, "pages": {}, "tick": None, "last": None, "target": 0}

    def step(_widget, clock):
        now = clock.get_frame_time()
        elapsed = (now - (state["last"] or now)) / 1_000_000
        state["last"] = now
        change = elapsed / FADE_SECONDS
        opacity = area.get_opacity()
        if state["target"] > opacity:
            opacity = min(opacity + change, state["target"])
        else:
            opacity = max(opacity - change, state["target"])
        area.set_opacity(opacity)
        if opacity != state["target"]:
            return True
        if state["target"] == 0:
            area.hide()
        state["tick"] = None
        return False

    def fade_to(target):
        state["target"] = target
        if state["tick"] is None:
            state["last"] = None
            state["tick"] = area.add_tick_callback(step)

    def mark_row(row):
        if state["row"]:
            state["row"].get_style_context().remove_class("selected")
        if row:
            row.get_style_context().add_class("selected")
        state["row"] = row

    def show(key, build_page, row):
        if key not in state["pages"]:
            page = build_page()
            page.show_all()
            state["pages"][key] = page
            stack.add_named(page, key)
        frame.show_all()
        area.show()
        stack.set_visible_child_name(key)
        state["key"] = key
        mark_row(row)
        fade_to(1)

    def hide():
        state["key"] = None
        mark_row(None)
        fade_to(0)

    def toggle(key, build_page, row):
        if state["key"] == key:
            hide()
        else:
            show(key, build_page, row)

    def hide_within(prefix):
        if state["key"] and state["key"].startswith(prefix):
            hide()

    return {
        "area": area,
        "show": show,
        "toggle": toggle,
        "hide_within": hide_within,
        "state": state,
    }


def build_function(Gtk, function):
    row = Gtk.Box(spacing=6)
    row.pack_start(status_icon(Gtk, function["status"]), False, False, 0)

    text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    name = Gtk.Label(label=function["name"], xalign=0)
    name.get_style_context().add_class("function-name")
    desc = Gtk.Label(label=function["desc"], xalign=0)
    desc.set_line_wrap(True)
    desc.set_max_width_chars(28)
    desc.get_style_context().add_class("function-desc")
    text.pack_start(name, False, False, 0)
    text.pack_start(desc, False, False, 0)
    row.pack_start(text, True, True, 0)

    button = Gtk.Button()
    button.set_relief(Gtk.ReliefStyle.NONE)
    button.get_style_context().add_class("function-row")
    button.add(row)
    return button


def build_script(Gtk, group, script, doc, pane, toggles):
    functions = script["functions"]
    finished = sum(f["status"] == "done" for f in functions)
    key = f"{group['name']}/{script['name']}"

    content = Gtk.Box(spacing=5)
    text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    name = Gtk.Label(label=script["name"], xalign=0)
    name.get_style_context().add_class("script-name")
    count = Gtk.Label(label=f"{finished} of {len(functions)} complete", xalign=0)
    count.get_style_context().add_class("muted")
    text.pack_start(name, False, False, 0)
    text.pack_start(count, False, False, 0)
    content.pack_start(text, True, True, 0)
    content.pack_end(progress_status(Gtk, script_progress(script)), False, False, 0)

    body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
    body.get_style_context().add_class("functions")
    for function in functions:
        function_key = f"{key}/{function['name']}"
        function_doc = doc.get("functions", {}).get(function["name"], {})

        def on_click(button, f=function, d=function_doc, k=function_key):
            pane["toggle"](k, lambda: build_function_page(Gtk, script, f, d), button)

        button = build_function(Gtk, function)
        button.connect("clicked", on_click)
        toggles[function_key] = button.clicked
        body.pack_start(button, False, False, 0)

    def on_toggle(is_open, button):
        if is_open:
            pane["show"](key, lambda: build_script_page(Gtk, group, script, doc), button)
        else:
            pane["hide_within"](key)

    return build_dropdown(Gtk, "script-row", content, body, on_toggle)


def build_group(Gtk, group, docs, pane, toggles):
    content = Gtk.Box(spacing=5)
    name = Gtk.Label(label=group["name"], xalign=0)
    name.get_style_context().add_class("group-name")
    content.pack_start(name, True, True, 0)
    content.pack_end(progress_status(Gtk, weighted_progress(group["scripts"])), False, False, 0)

    body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    body.get_style_context().add_class("scripts")
    for script in group["scripts"]:
        doc = docs.get(script["name"], {})
        row, toggles[script["name"]] = build_script(Gtk, group, script, doc, pane, toggles)
        body.pack_start(row, False, False, 0)

    def on_toggle(is_open, _button):
        if not is_open:
            pane["hide_within"](f"{group['name']}/")

    box, toggles[group["name"]] = build_dropdown(Gtk, "group-row", content, body, on_toggle)
    return box


def add_smooth_scrolling(scroller):
    # Eases the list toward where the wheel asked it to go, instead of jumping there
    from gi.repository import Gdk

    adjustment = scroller.get_vadjustment()
    state = {"target": 0, "tick": None, "last": 0}

    def clamp(value):
        return min(max(value, adjustment.get_lower()),
                   adjustment.get_upper() - adjustment.get_page_size())

    def step(_widget, clock):
        now = clock.get_frame_time()
        elapsed = (now - state["last"]) / 1_000_000
        state["last"] = now
        state["target"] = clamp(state["target"])
        value = adjustment.get_value()
        value += (state["target"] - value) * (1 - math.exp(-elapsed * SCROLL_SPEED))
        if abs(state["target"] - value) < 0.5:
            adjustment.set_value(state["target"])
            state["tick"] = None
            return False
        adjustment.set_value(value)
        return True

    def scroll_by(distance):
        start = state["target"] if state["tick"] is not None else adjustment.get_value()
        state["target"] = clamp(start + distance)
        if state["tick"] is None:
            state["last"] = scroller.get_frame_clock().get_frame_time()
            state["tick"] = scroller.add_tick_callback(step)

    def on_scroll(_widget, event):
        has_deltas, _, delta = event.get_scroll_deltas()
        if not has_deltas:
            _, direction = event.get_scroll_direction()
            if direction == Gdk.ScrollDirection.UP:
                delta = -1
            elif direction == Gdk.ScrollDirection.DOWN:
                delta = 1
            else:
                return False
        scroll_by(delta * SCROLL_STEP)
        return True

    scroller.connect("scroll-event", on_scroll)
    return scroll_by


def separator(Gtk):
    line = Gtk.Box()
    line.get_style_context().add_class("separator")
    return line


def build_panel(Gtk, data, docs, pane):
    percent = overall_percent(data)
    panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    panel.set_size_request(PANEL_WIDTH, -1)
    panel.get_style_context().add_class("panel")

    header = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=7)
    header.get_style_context().add_class("header")
    top = Gtk.Box()
    titles = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    title = Gtk.Label(label="Game Scripting", xalign=0)
    title.get_style_context().add_class("title")
    checked = Gtk.Label(label=f"Last checked {data['checked']}", xalign=0)
    checked.get_style_context().add_class("muted")
    titles.pack_start(title, False, False, 0)
    titles.pack_start(checked, False, False, 0)
    score = Gtk.Label(label=f"{percent}%")
    score.get_style_context().add_class("score")
    top.pack_start(titles, True, True, 0)
    top.pack_end(score, False, False, 0)
    bar = Gtk.ProgressBar()
    bar.set_fraction(percent / 100)
    header.pack_start(top, False, False, 0)
    header.pack_start(bar, False, False, 0)
    panel.pack_start(header, False, False, 0)

    panel.pack_start(separator(Gtk), False, False, 0)

    toggles = {}
    groups = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
    groups.get_style_context().add_class("list")
    for group in data["groups"]:
        groups.pack_start(build_group(Gtk, group, docs, pane, toggles), False, False, 0)

    scroller = Gtk.ScrolledWindow()
    scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
    scroller.set_overlay_scrolling(False)
    scroller.set_propagate_natural_height(True)
    scroller.set_max_content_height(LIST_MAX_HEIGHT)
    scroller.add(groups)
    scroll_by = add_smooth_scrolling(scroller)
    panel.pack_start(scroller, True, True, 0)

    panel.pack_start(separator(Gtk), False, False, 0)

    legend = Gtk.Label(xalign=0)
    legend.set_markup(
        f"<span foreground='{GREEN}'>✔</span> Done   "
        f"<span foreground='{ORANGE}'>●</span> Needs work   ○ To do"
    )
    legend.get_style_context().add_class("muted")
    legend.get_style_context().add_class("legend")
    panel.pack_start(legend, False, False, 0)
    return panel, toggles, scroll_by


def menu_left_edge():
    # Centers the menu under the click, since the bar's position depends on the app name
    try:
        x, y = map(int, subprocess.check_output(["hyprctl", "cursorpos"], text=True).split(","))
        monitors = json.loads(subprocess.check_output(["hyprctl", "monitors", "-j"], text=True))
    except (OSError, ValueError, subprocess.CalledProcessError):
        return SHADOW_ROOM
    for monitor in monitors:
        if (monitor["x"] <= x < monitor["x"] + monitor["width"]
                and monitor["y"] <= y < monitor["y"] + monitor["height"]):
            x -= monitor["x"]
    return max(12, x - PANEL_WIDTH // 2)


def build_drop_area(Gtk, data, docs, left=None):
    # The menu and details slide down out of the bar, with room around them for their shadows
    left = SHADOW_ROOM if left is None else left
    pane = build_details_pane(Gtk)
    panel, toggles, scroll_by = build_panel(Gtk, data, docs, pane)

    menu_area = Gtk.EventBox()
    menu_area.set_visible_window(False)
    menu_area.set_valign(Gtk.Align.START)
    menu_area.add(panel)
    menu_area.connect("button-press-event", lambda *_: True)

    row = Gtk.Box()
    row.set_margin_start(min(left, SHADOW_ROOM))
    row.set_margin_end(SHADOW_ROOM)
    row.set_margin_bottom(44)
    row.pack_start(menu_area, False, False, 0)
    row.pack_start(pane["area"], False, False, 0)

    revealer = Gtk.Revealer()
    revealer.set_transition_type(Gtk.RevealerTransitionType.SLIDE_DOWN)
    revealer.set_transition_duration(OPEN_DURATION)
    revealer.set_margin_start(max(left - SHADOW_ROOM, 0))
    revealer.set_halign(Gtk.Align.START)
    revealer.set_valign(Gtk.Align.START)
    revealer.add(row)
    return revealer, toggles, scroll_by, pane


def apply_css(Gtk):
    from gi.repository import Gdk
    provider = Gtk.CssProvider()
    provider.load_from_data(CSS)
    Gtk.StyleContext.add_provider_for_screen(
        Gdk.Screen.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
    )


def open_popup(app, Gtk):
    import gi
    gi.require_version("GtkLayerShell", "0.1")
    from gi.repository import Gdk, GLib, GtkLayerShell

    apply_css(Gtk)
    window = Gtk.ApplicationWindow(application=app)
    screen = window.get_screen()
    window.set_visual(screen.get_rgba_visual())
    window.set_app_paintable(True)

    # A transparent full-screen layer, so a click anywhere else closes the panel
    GtkLayerShell.init_for_window(window)
    GtkLayerShell.set_layer(window, GtkLayerShell.Layer.OVERLAY)
    GtkLayerShell.set_exclusive_zone(window, -1)
    GtkLayerShell.set_keyboard_mode(window, GtkLayerShell.KeyboardMode.EXCLUSIVE)
    for edge in (GtkLayerShell.Edge.TOP, GtkLayerShell.Edge.BOTTOM,
                 GtkLayerShell.Edge.LEFT, GtkLayerShell.Edge.RIGHT):
        GtkLayerShell.set_anchor(window, edge, True)

    drop_area, _, _, _ = build_drop_area(Gtk, load_data(), load_docs(), menu_left_edge())
    drop_area.set_margin_top(BAR_HEIGHT)

    state = {"closing": False}

    def close_panel(*_):
        if state["closing"]:
            return
        state["closing"] = True
        drop_area.set_transition_duration(CLOSE_DURATION)
        drop_area.set_reveal_child(False)
        GLib.timeout_add(CLOSE_DURATION + 30, window.destroy)

    def on_key(_window, event):
        if event.keyval == Gdk.KEY_Escape:
            close_panel()

    window.add(drop_area)
    window.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
    window.connect("button-press-event", close_panel)
    window.connect("key-press-event", on_key)
    window.connect("map-event", lambda *_: drop_area.set_reveal_child(True))
    window.show_all()
    return window, close_panel


def run_popup():
    Gtk = load_gtk()
    from gi.repository import Gio

    app = Gtk.Application(application_id="local.waybar.ScriptingProgress",
                          flags=Gio.ApplicationFlags.FLAGS_NONE)
    state = {"close": None}

    def activate(application):
        if state["close"]:
            state["close"]()
        else:
            _, state["close"] = open_popup(application, Gtk)

    app.connect("activate", activate)
    app.run([sys.argv[0]])


if __name__ == "__main__":
    if "--popup" in sys.argv:
        run_popup()
    elif "--image" in sys.argv:
        draw_bar()
    else:
        print_percent()
