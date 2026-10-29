// SPDX-License-Identifier: LGPL-2.1-or-later
#include "src/hw/hw.h"
#include <aether_radio/x6200_control/low/control.h>
#include <assert.h>
#include <stdio.h>

static uint32_t registers[x6200_last + 1];
static bool fake_control_cmd(x6200_cmd_enum_t command, uint32_t value) {
    (void)command; (void)value;
    return true;
}
/* The backend's constructor resolves the real library through dlsym. The
 * test never writes a radio: provide the constructor a harmless stand-in. */
void *__wrap_dlsym(void *handle, const char *symbol) {
    (void)handle; (void)symbol;
    return (void *)fake_control_cmd;
}
uint32_t x6200_control_get(x6200_cmd_enum_t command) {
    assert(command >= 0 && command <= x6200_last);
    return registers[command];
}

int main(void) {
    hw_commanded_audio_route_t route;
    assert(!hw_get_commanded_audio_route(NULL));
    for (unsigned i = 0; i < 8; ++i) {
        registers[x6200_sple_atue_trx] = (i & 1 ? x6200_voice_rec : 0) |
                                         (i & 2 ? x6200_modem_trx : 0) |
                                         (i & 4 ? x6200_iptt : 0) | x6200_atu_tune;
        assert(hw_get_commanded_audio_route(&route));
        assert(route.record == !!(i & 1));
        assert(route.modem == !!(i & 2));
        assert(route.ptt == !!(i & 4));
#ifdef ENABLE_FREEDV_DIAGNOSTICS
        hw_freedv_cached_snapshot_t snapshot;
        assert(hw_get_freedv_cached_snapshot(&snapshot));
        assert(snapshot.raw_route_bits_available && snapshot.shared_route_bits == registers[x6200_sple_atue_trx]);
        assert(snapshot.route.record == route.record && snapshot.route.modem == route.modem && snapshot.route.ptt == route.ptt);
#endif
    }
#ifdef ENABLE_FREEDV_DIAGNOSTICS
    registers[x6200_voxg_voxag_voxdly_voxe] = (1u << 26) | 0x12345u;
    registers[x6200_micsel_pttmode_chge_spmode_auxiqgen_sqlthr] = (1u << 5) | 2u | (0x55u << 8);
    registers[x6200_ling_loutg_imicg_hmicg] = 0x44332211u;
    registers[x6200_rxvol] = 79;
    registers[x6200_monilevel_fftdec_fftzoomcw] = 0x5678004bu;
    registers[x6200_rfg_txpwr] = 0xa5a50a44u;
    registers[x6200_vi_vm] = 0x10001u;
    registers[x6200_vfoa_freq] = 7074000;
    registers[x6200_vfob_freq] = 14236000;
    registers[x6200_vfoa_mode] = x6200_mode_usb_dig;
    registers[x6200_vfob_mode] = x6200_mode_lsb;
    registers[x6200_rxfilter] = ((uint32_t)(uint16_t)-300 << 16) | 2700u;
    hw_freedv_cached_snapshot_t s;
    assert(!hw_get_freedv_cached_snapshot(NULL));
    assert(hw_get_freedv_cached_snapshot(&s));
    assert(s.vox_enabled);
    assert(s.protected_route_bits == (s.shared_route_bits & ~(x6200_voice_rec | x6200_modem_trx | x6200_iptt)));
    assert(s.microphone == 2 && s.speaker_mode == 1 && s.hand_mic_gain == 0x44 && s.internal_mic_gain == 0x33);
    assert(s.line_in == 0x11 && s.line_out == 0x22 && s.speaker_level == 79 && s.monitor_level == 75);
    assert(s.requested_power_tenths_watt == 10 && s.active_vfo == 1);
    assert(s.vfo_a_hz == 7074000 && s.vfo_b_hz == 14236000);
    assert(s.backend_mode_a == x6200_mode_usb_dig && s.backend_mode_b == x6200_mode_lsb);
    assert(s.rx_filter_low_hz == -300 && s.rx_filter_high_hz == 2700);
    puts("PASS: X6200 cached route bits and diagnostic register fields");
#else
    puts("PASS: X6200 cached route bits for all eight combinations");
#endif
}
