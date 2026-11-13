#!/bin/sh
# Area screenshot with the screen frozen: hyprpicker draws a still copy of every monitor,
# you drag over it with slurp, and grim captures the frozen picture.
# Saves to ~/Pictures/screenshot and copies to the clipboard. Esc cancels.

dir="$HOME/Pictures/screenshot"
file="$dir/Screenshot_$(date +%Y-%m-%d_%H-%M-%S).png"
mkdir -p "$dir"

# -r freezes every monitor, -z/-d hide the zoom lens and colour preview
hyprpicker -r -z -d -q >/dev/null 2>&1 &
freeze=$!
trap 'kill "$freeze" 2>/dev/null' EXIT

# Give the frozen frame time to appear before the selection starts
sleep 0.15

region=$(slurp -b 00000066 -c ffffffcc -s ffffff10 -w 1) || exit 0
grim -g "$region" "$file" || exit 1
wl-copy < "$file"
notify-send -i "$file" "Screenshot" "Saved and copied to clipboard"
