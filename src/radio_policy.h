/* SPDX-License-Identifier: LGPL-2.1-or-later */
#pragma once
#include "radio_types.h"
#include <stdbool.h>

/* Upstream's effective application policy. Desired settings stay unchanged.
 * This does not claim that a particular MCU distinguishes FAST from AUTO. */
static inline radio_agc_t radio_effective_agc(radio_agc_t desired, radio_mode_t mode, bool cw_decoder) {
    return desired == radio_agc_auto && cw_decoder && (mode == radio_mode_cw || mode == radio_mode_cwr)
        ? radio_agc_fast : desired;
}

static inline uint16_t radio_effective_agc_time(radio_agc_t desired, radio_mode_t mode, bool cw_decoder) {
    switch (radio_effective_agc(desired, mode, cw_decoder)) {
    case radio_agc_fast: return 100;
    case radio_agc_off:
    case radio_agc_slow: return 1000;
    case radio_agc_auto:
        switch (mode) {
        case radio_mode_cw:
        case radio_mode_cwr: return 100;
        case radio_mode_am:
        case radio_mode_sam:
        case radio_mode_nfm:
        case radio_mode_wfm: return 1000;
        default: return 500;
        }
    }
    return 500;
}
