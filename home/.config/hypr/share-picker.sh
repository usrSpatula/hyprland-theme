#!/bin/sh
# Screen-share picker for xdg-desktop-portal-hyprland (set in xdph.conf).
# The clip recorder (~/.config/waybar/clips.py) leaves a one-time request naming the monitor
# it wants, and that request is answered without a dialog. Every other app (Discord, browsers)
# gets the normal picker.
request="$XDG_RUNTIME_DIR/clips/portal-request"

if [ -f "$request" ] && [ $(( $(date +%s) - $(stat -c %Y "$request") )) -lt 10 ]; then
    monitor=$(cat "$request")
    rm -f "$request"
    echo "[SELECTION]/screen:$monitor"
    exit 0
fi

exec hyprland-share-picker "$@"
