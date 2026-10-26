// SPDX-License-Identifier: LGPL-2.1-or-later
#include "src/hw/hw.h"
#include <assert.h>
#include <stdio.h>

int main(void) {
    hw_freedv_cached_snapshot_t s;
    assert(!hw_get_freedv_cached_snapshot(NULL));
    hw_set_i(HW_RECORD, 1);
    hw_set_i(HW_MODEM, 1);
    hw_set_i(HW_PTT, 0);
    hw_set_i(HW_MIC, 1);
    hw_set_i(HW_HMIC, 28);
    hw_set_i(HW_RXVOL, 25);
    hw_set_f(HW_TXPWR_F, 1.0f);
    hw_vfo_set_freq(RADIO_VFO_A, 7074000);
    assert(hw_get_freedv_cached_snapshot(&s));
    assert(s.route.record && s.route.modem && !s.route.ptt && !s.raw_route_bits_available);
    assert(s.microphone == 1 && s.hand_mic_gain == 28 && s.speaker_level == 25);
    assert(s.requested_power_tenths_watt == 10 && s.vfo_a_hz == 7074000);
    puts("PASS: host stub exposes simulated cached state without claiming raw X6200 registers");
}
