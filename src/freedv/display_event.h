// SPDX-License-Identifier: LGPL-2.1-or-later
#pragma once
#include <stdbool.h>
#include <stdint.h>

/* Copied observations only: screen blanking never owns audio or TX policy. */
typedef enum {
    FD_USB_DISPLAY_SNAPSHOT, FD_USB_DISPLAY_BOOT, FD_USB_DISPLAY_IDLE_DIM,
    FD_USB_DISPLAY_IDLE_BLANK, FD_USB_DISPLAY_INPUT_WAKE,
    FD_USB_DISPLAY_MANUAL_POWER, FD_USB_DISPLAY_SETTINGS,
    FD_USB_DISPLAY_VIEWERS, FD_USB_DISPLAY_BRIGHTNESS
} fd_usb_display_reason;
typedef struct {
    uint64_t monotonic_ms, generation;
    fd_usb_display_reason reason;
    bool dark, viewers, drawing_enabled;
    int32_t invalidation_count;
    int16_t brightness;
} fd_usb_display_event;
