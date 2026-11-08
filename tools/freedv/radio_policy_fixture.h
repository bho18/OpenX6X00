// SPDX-License-Identifier: LGPL-2.1-or-later
/* Application/configuration endpoints for fixtures that include the production
 * radio.c. Radio admission and base freshness remain production code; the band
 * table/check below is also the real upstream implementation. No RF hardware
 * or GUI work is performed by these deterministic endpoints. */
#include "src/cfg/ham_bands.h"

static const char *fixture_tx_block;
static bool fixture_tx_outside_bands;
static bool fixture_tx_player_busy;
bool tx_player_busy(void) { return fixture_tx_player_busy; }
bool cfg_wr12_freq(int32_t freq) { (void)freq; return false; }
bool wr12_ready(void) { return false; }
const char *cfg_tx_block(void) { return fixture_tx_block; }
const char *cfg_tx_refusal(int32_t freq) {
    const char *block = cfg_tx_block();
    if (block) return block;
    switch (ham_tx_check(freq, cfg_is_tx_freq(freq), false, fixture_tx_outside_bands)) {
    case HAM_TX_OK: return NULL;
    case HAM_TX_RX_ONLY: return "Receive only";
    case HAM_TX_OFF_BAND: return "Outside amateur bands";
    }
    assert(false); return "Invalid policy";
}
const char *cfg_tx_mhz(int32_t freq, char *buf, size_t size) {
    snprintf(buf, size, "%.6f MHz", freq / 1000000.0);
    return buf;
}
float cfg_transverter_pwr_for(int32_t freq) { (void)freq; return 0.0f; }
float cfg_transverter_pwr_cap(int32_t rx, int32_t tx) { (void)rx; (void)tx; return 0.0f; }
pwr_xvtr_t cfg_pwr_xvtr_set(float watts, bool on_xvtr) {
    assert(watts == 0.0f && !on_xvtr); return PWR_XVTR_SAME;
}
void cfg_transverter_name(int32_t freq, char *buf, size_t size) {
    (void)freq; snprintf(buf, size, "No transverter");
}
bool cw_trainer_on(void) { return false; }
void cw_encoder_stop(void) { assert(cw_encoder_state() == CW_ENCODER_IDLE); }
void paddle_keyer_stop(void) {}
void dialog_msg_voice_ptt_pressed(void) {}
void dialog_swrscan_stop(int32_t freq) { (void)freq; }
bool scheduler_on_gui_thread(void) { return true; }
/* These tests do not exercise deferred cosmetic/transverter UI notifications.
 * A successful enqueue is a bounded endpoint, not an immediate recursive call. */
bool scheduler_put_coalesced(scheduler_fn_t callback) { assert(callback); return true; }
