#!/bin/sh
# Builds screen-dimmer into ~/.local/bin. Needs gcc, wayland and wayland-protocols.
set -e
cd "$(dirname "$0")"
protocols=$(pkg-config --variable=pkgdatadir wayland-protocols)
build=$(mktemp -d)
trap 'rm -rf "$build"' EXIT
for xml in stable/viewporter/viewporter staging/single-pixel-buffer/single-pixel-buffer-v1 stable/xdg-shell/xdg-shell; do
    name=$(basename "$xml" | sed 's/-v1$//')
    wayland-scanner client-header "$protocols/$xml.xml" "$build/$name-client-protocol.h"
    wayland-scanner private-code  "$protocols/$xml.xml" "$build/$name-protocol.c"
done
mkdir -p "$HOME/.local/bin"
gcc -O2 -Wall -I"$build" -I. -o "$HOME/.local/bin/screen-dimmer" \
    dimmer.c wlr-layer-shell-unstable-v1-protocol.c "$build"/*-protocol.c \
    $(pkg-config --cflags --libs wayland-client) -lm
