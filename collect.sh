#!/usr/bin/env bash
# Copies the theme from this PC into the repo, so you can commit and push your changes.
# Run it on the PC where you edit the theme:
#
#   ./collect.sh && git add -A && git commit -m "Update theme" && git push
#
# files.txt says what gets copied. Backups (*.pre-*, *.bak) and your course progress stay out.
set -euo pipefail

repo=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
theme=$repo/home

command -v rsync >/dev/null || { echo "collect.sh: rsync is needed (sudo pacman -S rsync)" >&2; exit 1; }

rm -rf "$theme"
while read -r entry; do
    if [[ ! -e $HOME/$entry ]]; then
        echo "Not on this PC, left out: ~/$entry" >&2
        continue
    fi
    mkdir -p "$theme/$(dirname "$entry")"
    rsync -a --exclude-from="$repo/.gitignore" "$HOME/$entry" "$theme/$entry"
done < <(grep -vE '^[[:space:]]*(#|$)' "$repo/files.txt")

# A course that's still being written has no course.json yet
for course in "$theme"/.config/waybar/learning/*/; do
    [[ -f $course/course.json ]] || rm -rf "$course"
done

# The repo is shared, so nothing in it should name this PC's user
if named=$(grep -rIl -e "/home/$USER" -e "$USER@" "$theme"); then
    echo "These files mention your user name. Check them before you push:" >&2
    echo "$named" >&2
fi

if git -C "$repo" rev-parse --git-dir >/dev/null 2>&1; then
    git -C "$repo" status --short
fi
