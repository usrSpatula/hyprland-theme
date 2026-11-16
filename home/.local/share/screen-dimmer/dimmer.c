/* screen-dimmer: a see-through black sheet over every monitor, for the bottom of the
 * brightness slider (see Dimmer in ~/.config/waybar/control-center.py, which starts this).
 *
 * It reads how black to be from standard input, one number from 0 to 1 per line, and fades
 * there in step with the screen. The sheet is a single stretched pixel, so a fade sends the
 * compositor nothing but a new colour each frame. At 0 the sheets are removed entirely.
 */
#include <errno.h>
#include <math.h>
#include <poll.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <wayland-client.h>

#include "single-pixel-buffer-client-protocol.h"
#include "viewporter-client-protocol.h"
#include "wlr-layer-shell-unstable-v1-client-protocol.h"

#define MAX_SHEETS 8
#define FADE_RATE 12.0   /* higher is a quicker fade */

struct sheet {
    struct wl_output *output;
    uint32_t output_name;
    struct wl_surface *surface;
    struct wp_viewport *viewport;
    struct zwlr_layer_surface_v1 *layer;
    struct wl_callback *frame;
    int width, height;
    int configured;
};

static struct wl_compositor *compositor;
static struct wp_viewporter *viewporter;
static struct wp_single_pixel_buffer_manager_v1 *pixels;
static struct zwlr_layer_shell_v1 *layer_shell;
static struct sheet sheets[MAX_SHEETS];

static double shown, target;
static uint32_t last_time;
static int have_time;

static void paint(struct sheet *sheet);

static void frame_done(void *data, struct wl_callback *callback, uint32_t time) {
    struct sheet *sheet = data;
    wl_callback_destroy(callback);
    sheet->frame = NULL;
    /* Every sheet gets its own callback; only the first one in each frame moves the fade on */
    if (!have_time || time != last_time) {
        double elapsed = have_time ? (time - last_time) / 1000.0 : 0;
        if (elapsed > 0.05) elapsed = 0.05;
        last_time = time;
        have_time = 1;
        double part = elapsed * FADE_RATE;
        shown += (target - shown) * (part > 1 ? 1 : part);
        if (fabs(target - shown) < 0.002) shown = target;
    }
    paint(sheet);
}

static const struct wl_callback_listener frame_listener = { frame_done };

static void paint(struct sheet *sheet) {
    if (!sheet->configured) return;
    uint32_t alpha = (uint32_t)(shown * (double)UINT32_MAX);
    struct wl_buffer *buffer =
        wp_single_pixel_buffer_manager_v1_create_u32_rgba_buffer(pixels, 0, 0, 0, alpha);
    wp_viewport_set_destination(sheet->viewport, sheet->width, sheet->height);
    wl_surface_attach(sheet->surface, buffer, 0, 0);
    wl_surface_damage_buffer(sheet->surface, 0, 0, 1, 1);
    if (shown != target && !sheet->frame) {
        sheet->frame = wl_surface_frame(sheet->surface);
        wl_callback_add_listener(sheet->frame, &frame_listener, sheet);
    }
    wl_surface_commit(sheet->surface);
    wl_buffer_destroy(buffer);
}

static void remove_sheet(struct sheet *sheet) {
    if (sheet->frame) wl_callback_destroy(sheet->frame);
    if (sheet->viewport) wp_viewport_destroy(sheet->viewport);
    if (sheet->layer) zwlr_layer_surface_v1_destroy(sheet->layer);
    if (sheet->surface) wl_surface_destroy(sheet->surface);
    sheet->frame = NULL;
    sheet->viewport = NULL;
    sheet->layer = NULL;
    sheet->surface = NULL;
    sheet->configured = 0;
}

static void layer_configure(void *data, struct zwlr_layer_surface_v1 *layer,
                            uint32_t serial, uint32_t width, uint32_t height) {
    struct sheet *sheet = data;
    zwlr_layer_surface_v1_ack_configure(layer, serial);
    sheet->width = width;
    sheet->height = height;
    sheet->configured = width > 0 && height > 0;
    paint(sheet);
}

static void layer_closed(void *data, struct zwlr_layer_surface_v1 *layer) {
    (void)layer;
    remove_sheet(data);
}

static const struct zwlr_layer_surface_v1_listener layer_listener = { layer_configure, layer_closed };

static void add_sheet(struct sheet *sheet) {
    sheet->surface = wl_compositor_create_surface(compositor);
    /* Clicks fall through to whatever is underneath */
    struct wl_region *nothing = wl_compositor_create_region(compositor);
    wl_surface_set_input_region(sheet->surface, nothing);
    wl_region_destroy(nothing);
    sheet->viewport = wp_viewporter_get_viewport(viewporter, sheet->surface);
    sheet->layer = zwlr_layer_shell_v1_get_layer_surface(
        layer_shell, sheet->surface, sheet->output, ZWLR_LAYER_SHELL_V1_LAYER_OVERLAY, "dimmer");
    zwlr_layer_surface_v1_set_anchor(sheet->layer,
        ZWLR_LAYER_SURFACE_V1_ANCHOR_TOP | ZWLR_LAYER_SURFACE_V1_ANCHOR_BOTTOM |
        ZWLR_LAYER_SURFACE_V1_ANCHOR_LEFT | ZWLR_LAYER_SURFACE_V1_ANCHOR_RIGHT);
    zwlr_layer_surface_v1_set_exclusive_zone(sheet->layer, -1);   /* over the bar too */
    zwlr_layer_surface_v1_add_listener(sheet->layer, &layer_listener, sheet);
    wl_surface_commit(sheet->surface);
}

/* Called whenever the target changes: puts sheets up, restarts the fade, or takes them down */
static void update(void) {
    for (int i = 0; i < MAX_SHEETS; i++) {
        struct sheet *sheet = &sheets[i];
        if (!sheet->output) continue;
        if (shown == 0 && target == 0) {
            remove_sheet(sheet);
        } else if (!sheet->surface) {
            add_sheet(sheet);
        } else if (!sheet->frame) {
            have_time = 0;
            paint(sheet);
        }
    }
}

static void global_added(void *data, struct wl_registry *registry, uint32_t name,
                         const char *interface, uint32_t version) {
    (void)data; (void)version;
    if (!strcmp(interface, wl_compositor_interface.name)) {
        compositor = wl_registry_bind(registry, name, &wl_compositor_interface, 4);
    } else if (!strcmp(interface, wp_viewporter_interface.name)) {
        viewporter = wl_registry_bind(registry, name, &wp_viewporter_interface, 1);
    } else if (!strcmp(interface, wp_single_pixel_buffer_manager_v1_interface.name)) {
        pixels = wl_registry_bind(registry, name, &wp_single_pixel_buffer_manager_v1_interface, 1);
    } else if (!strcmp(interface, zwlr_layer_shell_v1_interface.name)) {
        layer_shell = wl_registry_bind(registry, name, &zwlr_layer_shell_v1_interface, 1);
    } else if (!strcmp(interface, wl_output_interface.name)) {
        for (int i = 0; i < MAX_SHEETS; i++) {
            if (sheets[i].output) continue;
            sheets[i].output = wl_registry_bind(registry, name, &wl_output_interface, 1);
            sheets[i].output_name = name;
            /* A monitor plugged in while dimmed gets its sheet straight away */
            if (layer_shell && (shown > 0 || target > 0)) add_sheet(&sheets[i]);
            break;
        }
    }
}

static void global_removed(void *data, struct wl_registry *registry, uint32_t name) {
    (void)data; (void)registry;
    for (int i = 0; i < MAX_SHEETS; i++) {
        if (sheets[i].output && sheets[i].output_name == name) {
            remove_sheet(&sheets[i]);
            wl_output_destroy(sheets[i].output);
            sheets[i].output = NULL;
        }
    }
}

static const struct wl_registry_listener registry_listener = { global_added, global_removed };

int main(void) {
    struct wl_display *display = wl_display_connect(NULL);
    if (!display) {
        fprintf(stderr, "screen-dimmer: no Wayland display\n");
        return 1;
    }
    struct wl_registry *registry = wl_display_get_registry(display);
    wl_registry_add_listener(registry, &registry_listener, NULL);
    wl_display_roundtrip(display);
    if (!compositor || !viewporter || !pixels || !layer_shell) {
        fprintf(stderr, "screen-dimmer: the compositor is missing something this needs\n");
        return 2;
    }

    char line[64];
    size_t used = 0;
    struct pollfd fds[2] = {
        { .fd = wl_display_get_fd(display), .events = POLLIN },
        { .fd = STDIN_FILENO, .events = POLLIN },
    };
    for (;;) {
        while (wl_display_prepare_read(display) != 0) wl_display_dispatch_pending(display);
        if (wl_display_flush(display) < 0 && errno != EAGAIN) return 0;
        if (poll(fds, 2, -1) < 0 && errno != EINTR) return 0;
        if (fds[0].revents & POLLIN) wl_display_read_events(display);
        else wl_display_cancel_read(display);
        if (fds[0].revents & (POLLERR | POLLHUP)) return 0;
        wl_display_dispatch_pending(display);

        if (fds[1].revents & (POLLIN | POLLHUP)) {
            ssize_t got = read(STDIN_FILENO, line + used, sizeof line - 1 - used);
            if (got <= 0) return 0;   /* the control center went away */
            used += got;
            line[used] = 0;
            char *end;
            while ((end = strchr(line, '\n'))) {
                double value = atof(line);
                target = value < 0 ? 0 : value > 0.97 ? 0.97 : value;
                used -= end + 1 - line;
                memmove(line, end + 1, used + 1);
                update();
            }
            if (used >= sizeof line - 1) used = 0;
        }
        /* Faded all the way out: take the sheets down so they cost nothing */
        if (shown == 0 && target == 0) update();
    }
}
