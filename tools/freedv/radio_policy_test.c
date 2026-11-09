/* SPDX-License-Identifier: LGPL-2.1-or-later
 * Real ordinary power reconciliation with synthetic hardware and WR12 input. */
#undef NDEBUG
#include "src/radio_policy.h"
#include <assert.h>
#include <math.h>
#include <stdatomic.h>
#include <stdio.h>

enum { RADIO_RX, RADIO_SWRSCAN, HW_TXPWR_F, HW_VOX, HW_KEY_TRAIN, cfg_vox_on, cfg_key_train };
static atomic_int state;
static atomic_bool train_hold, band_hold, band_said;
static bool digi_cap, blocked;
static float tune_cap, xvtr_cap, txpwr_asked, hardware_power;
static unsigned power_writes, open_looks;
#define SWRSCAN_PWR_W 1.0f
#define TX_PLAYER_MAX_PWR_W 5.0f
#define LV_LOG_USER(...) ((void)0)
static const char *cfg_tx_block(void) { return blocked ? "WR12 not in bypass" : NULL; }
static void hw_set_f(int field, float value) { assert(field == HW_TXPWR_F); hardware_power = value; ++power_writes; }
static void hw_set_i(int field, int value) { (void)field; (void)value; }
static int param_i_get(int key) { (void)key; return 0; }
static void open_tx_look(void) { ++open_looks; }
static bool radio_key_refused(int32_t *frequency) { if (frequency) *frequency = 7074000; return blocked; }
#include "src/radio_power.inc"

static void expect(float value) { assert(fabsf(hardware_power - value) < 0.0001f); }

int main(void) {
    // Both VFO policies, including the inactive VFO, keep desired settings intact.
    const radio_mode_t modes[] = {radio_mode_lsb, radio_mode_usb_dig, radio_mode_cw, radio_mode_cwr,
        radio_mode_am, radio_mode_nfm, radio_mode_sam, radio_mode_wfm};
    for (unsigned decoder = 0; decoder < 2; ++decoder) {
        for (unsigned i = 0; i < sizeof(modes) / sizeof(modes[0]); ++i) {
            for (unsigned vfo = 0; vfo < 2; ++vfo) {
                radio_agc_t desired[2] = {radio_agc_auto, radio_agc_auto};
                bool cw = modes[i] == radio_mode_cw || modes[i] == radio_mode_cwr;
                assert(radio_effective_agc(desired[vfo], modes[i], decoder) ==
                    (decoder && cw ? radio_agc_fast : radio_agc_auto));
                assert(desired[vfo] == radio_agc_auto);
                assert(radio_effective_agc(radio_agc_off, modes[i], decoder) == radio_agc_off);
                assert(radio_effective_agc(radio_agc_slow, modes[i], decoder) == radio_agc_slow);
                assert(radio_effective_agc(radio_agc_fast, modes[i], decoder) == radio_agc_fast);
            }
        }
    }
    assert(radio_effective_agc_time(radio_agc_auto, radio_mode_usb, true) == 500);
    assert(radio_effective_agc_time(radio_agc_auto, radio_mode_cw, true) == 100);
    assert(radio_effective_agc_time(radio_agc_auto, radio_mode_am, false) == 1000);

    state = RADIO_RX;
    set_txpwr(8); expect(8);
    // Simulate WR12's short block/unblock pulse coalescing to one GUI callback.
    // The first phase zeroed power; the final cached band hold is still false.
    blocked = true; set_txpwr(txpwr_asked); expect(0);
    blocked = false;
    unsigned before = power_writes;
    band_hold_set(false); expect(8);
    assert(power_writes == before + 1 && open_looks == 1 && txpwr_asked == 8);

    digi_cap = true; band_hold_set(false); expect(5);
    tune_cap = 3; band_hold_set(false); expect(3);
    xvtr_cap = 2; band_hold_set(false); expect(2);
    state = RADIO_SWRSCAN; band_hold_set(false); expect(1);
    state = RADIO_RX; tune_cap = 0; train_hold = true; band_hold_set(false); expect(0);
    train_hold = false; band_hold_set(true); expect(0);
    band_hold_set(false); expect(2);
    blocked = true; band_hold_set(false); expect(0);
    blocked = false; band_hold_set(false); expect(2);
    assert(txpwr_asked == 8); // Reconciliation never rewrites the user's request.
    puts("PASS: effective AGC policy and coalesced WR12 power reconciliation retain every cap and inhibit");
    return 0;
}
