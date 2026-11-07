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
