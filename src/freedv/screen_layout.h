// SPDX-License-Identifier: LGPL-2.1-or-later
#pragma once
#include <stdbool.h>
#include <stdint.h>

/* Shared by the real main-screen placement and its exhaustive geometry test. */
typedef struct {
    int32_t row_y, row_height, spectrum_y, spectrum_height, axis_y, waterfall_y;
} fd_main_layout;
static inline fd_main_layout fd_main_layout_compute(int32_t header_bottom, bool show_spectrum,
    int32_t spectrum_height, bool deck, int32_t axis_height, bool reserve_row, int32_t font_height) {
    int32_t row_height=reserve_row ? (font_height+8 > 28 ? font_height+8 : 28) : 0;
    int32_t after_row=header_bottom+row_height;
    fd_main_layout value={.row_y=header_bottom,.row_height=row_height,
        .spectrum_y=show_spectrum?after_row:0,
        .spectrum_height=show_spectrum?spectrum_height:after_row};
    int32_t bottom=value.spectrum_y+value.spectrum_height;
    value.axis_y=deck ? (show_spectrum?value.spectrum_y:reserve_row?after_row:0) : bottom;
    value.waterfall_y=bottom+axis_height;
    return value;
}

/* Amber's segment LCD owns the top of the display. Reserve the voice row
 * below its glass and shorten the spectrum strip, keeping its lower edge and
 * the waterfall fixed. Leaving FreeDV restores the theme's original strip. */
static inline fd_main_layout fd_main_layout_amber(int32_t lcd_bottom, int32_t strip_y,
    int32_t strip_height, int32_t axis_height, bool reserve_row, int32_t font_height) {
    int32_t row_height = reserve_row ? (font_height + 8 > 28 ? font_height + 8 : 28) : 0;
    int32_t row_y = lcd_bottom > strip_y ? lcd_bottom : strip_y;
    int32_t spectrum_y = reserve_row ? row_y + row_height : strip_y;
    int32_t strip_bottom = strip_y + strip_height;
    return (fd_main_layout){.row_y=row_y, .row_height=row_height,
        .spectrum_y=spectrum_y, .spectrum_height=strip_bottom-spectrum_y,
        .axis_y=spectrum_y, .waterfall_y=strip_bottom+axis_height};
}
