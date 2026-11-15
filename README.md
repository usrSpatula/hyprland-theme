# hyprland-theme

A macOS-style Hyprland desktop for Arch Linux: a glass top bar with a notch, a Control Center,
a Spotlight-style launcher and a clip recorder.

## Install

```bash
git clone https://github.com/usrSpatula/hyprland-theme.git
cd hyprland-theme
./install.sh
```

The installer:

1. Installs the packages in [packages.txt](packages.txt) with `pacman` (it asks for your password).
   They are all in the official Arch repos.
2. Copies the theme into your home folder. Anything it replaces is saved first in
   `~/.local/share/hyprland-theme/backup/`.
3. Sets the font (Inter), dark mode, and the Breeze GTK, icon and cursor themes.

Then log out and pick **Hyprland** on the login screen.

`./install.sh --no-packages` copies the files without installing anything.

## Update

On a PC that already has the theme:

```bash
git pull
./install.sh
```

## Monitors

Each PC keeps its own monitor setup in `~/.config/hypr/monitors.lua`. The installer never touches
that file. Without it, every monitor runs at its preferred mode and is placed automatically.

To choose the refresh rate or the layout, create the file. For example:

```lua
hl.monitor({ output = "DP-1", mode = "1920x1080@200", position = "0x0",    scale = "auto" })
hl.monitor({ output = "DP-3", mode = "1920x1080@200", position = "0x1080", scale = "auto" })
```

`hyprctl monitors` lists your monitors' names and modes.

The clip recorder records the monitor named in `CLIP_MONITOR` at the top of
`~/.config/waybar/clips.py` (`DP-3`). If there's no monitor with that name, it records the first one.

## Keys

| Keys | What it does |
| --- | --- |
| Super + Return | Terminal (kitty) |
| Super + D | Launcher |
| Super + E | Files (Dolphin) |
| Super + Q | Close the window |
| Super + V | Float the window |
| Super + 1 to 0 | Go to desktop 1 to 10 |
| Super + Shift + 1 to 0 | Move the window to that desktop |
| Super + scroll | Next or previous desktop |
| Super + S | Show or hide the scratchpad |
| Super + Ctrl + S | Move the window to the scratchpad |
| Super + Shift + S | Screenshot an area (saved to `~/Pictures/screenshot` and copied) |
| Insert | Save a clip of the last moments (to `~/Videos/Clips`) |
| Super + Insert | Turn clip recording on or off |
| Super + M | Log out |

On the bar: the clock opens the Control Center, and the gear slides out clips, clipboard
history, screenshots and the power menu.
The Wi-Fi icon (and the Wi-Fi tile in the Control Center) lists the networks nearby: click one
to join it, and it asks for a password only when it needs one. Right-click the icon for the
full network settings.

**Super+Tab** opens the overview: every desktop drawn small with its real windows in it. Click a
window to go to it (arrow keys and Enter work too), or press Esc.

The volume icon opens the sound panel: the master volume, a slider for every app that's playing
sound, and which speakers and microphone to use.

The Bluetooth icon lists devices nearby and pairs and connects them with a click.

**RGB Controller** (in the launcher, or `rgb-controller`) finds every light it can control (the
motherboard, graphics card, memory, lighting headers, headsets and more, through OpenRGB) and sets
them all to one colour, effect, brightness and speed. It also lists what it found but can't
control, and why. Your choice comes back at every login.

The theme has soft interface sounds (panels, switches, screenshots, clips, notifications, volume).
The **Sounds** switch in the Control Center turns them off. To change a sound, edit
`~/.config/waybar/sounds/make-sounds.py` and run it.

Everything moves with a bounce: windows pop open, desktops spring into place, panels drop in and
their rows rise one after another, switches pop, sliders and the volume popup spring to their
level, bar icons swell under the mouse, and the notch pulses when a song changes or a clip is
saved. The focused window has a slowly turning glow in the wallpaper's colours; to change them,
edit `active_border` in `~/.config/hypr/hyprland.lua`.

## What's in here

| Path | What it is |
| --- | --- |
| `home/` | The theme's files, laid out as they sit in your home folder |
| `files.txt` | The list of files and folders that make up the theme |
| `packages.txt` | The Arch packages it needs |
| `install.sh` | Puts the theme on a PC |
| `collect.sh` | Copies the theme from a PC back into this repo |

## Changing the theme

Edit the files in your home folder as usual (`~/.config/hypr`, `~/.config/waybar` and so on).
When you're happy, copy them into the repo and push:

```bash
./collect.sh
git add -A
git commit -m "Update theme"
git push
```

`collect.sh` leaves out backup files (`*.pre-*`, `*.bak`).
If you add a new file to the theme, add its path to `files.txt` first.

## Extras

- Bluetooth in the Control Center needs the service running: `sudo systemctl enable --now bluetooth`.
- Brightness for external monitors uses `ddcutil`, which needs monitors that support DDC/CI.
