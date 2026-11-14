#!/usr/bin/env bash
# Installs the theme into your home folder. Running it again updates it.
#
#   ./install.sh                 install the packages, then the theme's files
#   ./install.sh --no-packages   only the files
#
# Anything it replaces is copied to ~/.local/share/hyprland-theme/backup first.
set -euo pipefail

repo=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
theme=$repo/home
backup=$HOME/.local/share/hyprland-theme/backup/$(date +%Y-%m-%d_%H-%M-%S)
packages=1

say()  { printf '\n\033[1m%s\033[0m\n' "$*"; }
note() { printf '  %s\n' "$*"; }
die()  { printf 'install.sh: %s\n' "$*" >&2; exit 1; }
# The lines of a list file, without comments and blank lines
list() { grep -vE '^[[:space:]]*(#|$)' "$1"; }

for arg in "$@"; do
    case $arg in
        --no-packages) packages=0 ;;
        -h|--help)     sed -n '2,7s/^# \{0,1\}//p' "${BASH_SOURCE[0]}"; exit 0 ;;
        *)             die "unknown option: $arg (see ./install.sh --help)" ;;
    esac
done

[[ $EUID -ne 0 ]] || die "run this as yourself, not as root. It asks for your password when it installs packages."
[[ -d $theme ]] || die "$theme is missing. Run this from a full copy of the repo."


# ---- Packages ----

if ((packages)); then
    command -v pacman >/dev/null || die "pacman not found. On another distro, install what's in packages.txt yourself, then run ./install.sh --no-packages"
    say "Installing packages"
    mapfile -t wanted < <(list "$repo/packages.txt")
    # -Syu: Arch doesn't support installing new packages onto an out-of-date system
    sudo pacman -Syu --needed "${wanted[@]}"
fi


# ---- Files ----

replaced=0

same() {
    if [[ -d $theme/$1 ]]; then
        diff -rq "$theme/$1" "$HOME/$1" >/dev/null 2>&1
    else
        cmp -s "$theme/$1" "$HOME/$1"
    fi
}

# Copies what's in your home folder now into the backup
keep() {
    [[ -e $HOME/$1 ]] || return 0
    mkdir -p "$backup/$(dirname "$1")"
    cp -a "$HOME/$1" "$backup/$1"
    replaced=1
}

say "Installing the theme"
while read -r entry; do
    path=${entry%/}
    [[ -e $theme/$path ]] || die "files.txt lists $path, but it isn't in the repo"
    if same "$path"; then
        continue
    fi
    keep "$path"
    mkdir -p "$HOME/$(dirname "$path")"
    if [[ -d $theme/$path ]]; then
        mkdir -p "$HOME/$path"
        cp -a "$theme/$path/." "$HOME/$path/"
    else
        cp -a "$theme/$path" "$HOME/$path"
    fi
    note "~/$path"
done < <(list "$repo/files.txt")

chmod +x "$HOME/.local/bin/mac-osd" "$HOME/.local/bin/spotlight-launcher" "$HOME/.local/bin/ui-sound" \
         "$HOME/.config/hypr/screenshot.sh" "$HOME/.config/hypr/share-picker.sh" \
         "$HOME/.config/waybar/osd.py"

# The screen-share portal wants the picker's full path, which has your user name in it,
# so this file is written here instead of copied
xdph=.config/hypr/xdph.conf
xdph_wanted="# Screen sharing (xdg-desktop-portal-hyprland)
screencopy {
    # Lets the clip recorder start without a dialog; everyone else still gets the picker
    custom_picker_binary = $HOME/.config/hypr/share-picker.sh
}"
if [[ ! -f $HOME/$xdph || $(<"$HOME/$xdph") != "$xdph_wanted" ]]; then
    keep "$xdph"
    printf '%s\n' "$xdph_wanted" > "$HOME/$xdph"
    note "~/$xdph"
fi

# This theme's settings are in hyprland.lua. An older hyprland.conf would compete with it.
if [[ -e $HOME/.config/hypr/hyprland.conf ]]; then
    keep .config/hypr/hyprland.conf
    rm "$HOME/.config/hypr/hyprland.conf"
    note "moved your old hyprland.conf into the backup"
fi


# ---- Looks ----

# The panels are GTK windows: dark, Breeze, in Inter
for version in 3.0 4.0; do
    ini=$HOME/.config/gtk-$version/settings.ini
    if [[ ! -f $ini ]]; then
        mkdir -p "$(dirname "$ini")"
        printf '[Settings]\ngtk-application-prefer-dark-theme=true\n' > "$ini"
    fi
done

looks_failed=0
look() { gsettings set org.gnome.desktop.interface "$1" "$2" 2>/dev/null || looks_failed=1; }
if command -v gsettings >/dev/null; then
    look color-scheme prefer-dark
    look gtk-theme    Breeze
    look icon-theme   breeze
    look cursor-theme breeze_cursors
    look font-name    'Inter 10'
else
    looks_failed=1
fi


# ---- Finish ----

if [[ -n ${HYPRLAND_INSTANCE_SIGNATURE:-} ]] && command -v hyprctl >/dev/null; then
    hyprctl reload >/dev/null 2>&1 || true
    pkill -SIGUSR2 -x waybar 2>/dev/null || true
    makoctl reload >/dev/null 2>&1 || true
fi

say "Done"
if ((replaced)); then
    note "What was there before is in $backup"
fi
if ((looks_failed)); then
    note "Couldn't set the font and dark mode from here. Run ./install.sh --no-packages again once you're in Hyprland."
fi
if [[ ! -f $HOME/.config/hypr/monitors.lua ]]; then
    note "Monitors are set up automatically. To choose the refresh rate or layout, see \"Monitors\" in the README."
fi
if command -v systemctl >/dev/null && ! systemctl is-enabled --quiet bluetooth 2>/dev/null; then
    note "Bluetooth is off. To turn it on: sudo systemctl enable --now bluetooth"
fi
note "Log out, then pick Hyprland on the login screen (or run start-hyprland from a text console)."
note "If you're already in Hyprland, log out and back in so the bar and everything else start fresh."
