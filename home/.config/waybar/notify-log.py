#!/usr/bin/env python3
"""Keeps a history of notifications, for the bell in the bar (gear-tools.py --notifications).

Notifications are shown by mako and vanish after a few seconds. This listens to the same
messages mako gets and writes each one down, so the ones you missed (in a game, away from
the desk, with Do Not Disturb on) can be read later. Hyprland starts it at login.
"""
import fcntl
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import gi
gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

STATE = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state")
LOG = STATE / "notifications.json"
UNSEEN = STATE / "notifications-unseen"
KEEP = 100
# The volume popup and "Clip saved" are feedback for something you just did, not news
SKIP_APPS = {"osd", "clips", "screenshot"}
WATCH = "type='method_call',interface='org.freedesktop.Notifications',member='Notify'"


def record(app, summary, body):
    try:
        entries = json.loads(LOG.read_text())
    except (OSError, ValueError):
        entries = []
    entries.append({"time": time.time(), "app": app, "summary": summary, "body": body})
    LOG.write_text(json.dumps(entries[-KEEP:]))
    UNSEEN.touch()
    # Waybar redraws the bell when it gets this signal
    subprocess.Popen(["pkill", "-RTMIN+8", "-x", "waybar"],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def on_message(_connection, message, incoming):
    if not incoming or message.get_member() != "Notify":
        return message   # this connection's own traffic passes through untouched
    if message.get_body():
        try:
            app, _replaces, _icon, summary, body, _actions, hints, _timeout = message.get_body().unpack()
            # A "synchronous" notification is a display that updates in place, like a volume bar
            if app not in SKIP_APPS and "x-canonical-private-synchronous" not in hints \
                    and not hints.get("transient") and (summary or body):
                record(app, summary, body)
        except (ValueError, TypeError, OSError):
            pass
    return None   # only listening: a notification is never passed on or answered


def main():
    STATE.mkdir(parents=True, exist_ok=True)
    lock = open(Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp")) / "notify-log.lock", "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        sys.exit(0)   # already running

    # Its own connection to the session bus, switched into listen-only ("monitor") mode
    connection = Gio.DBusConnection.new_for_address_sync(
        Gio.dbus_address_get_for_bus_sync(Gio.BusType.SESSION),
        Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION,
        None, None)
    connection.add_filter(on_message)
    connection.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus.Monitoring",
                         "BecomeMonitor", GLib.Variant("(asu)", ([WATCH], 0)), None,
                         Gio.DBusCallFlags.NONE, -1, None)
    connection.connect("closed", lambda *_: os._exit(1))
    GLib.MainLoop().run()


if __name__ == "__main__":
    main()
