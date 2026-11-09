/* SPDX-License-Identifier: LGPL-2.1-or-later
 * Ordinary power policy, included by radio.c and its synthetic regression harness.
 * Caller holds radio_lock; cfg_tx_block is re-read on every reconciliation. */
static void set_txpwr(float w) {
    txpwr_asked = w;
    if (atomic_load(&state) == RADIO_SWRSCAN && w > SWRSCAN_PWR_W) w = SWRSCAN_PWR_W;
    /* An app open: its cap, but not over a TUNE's own power (#87), which holds the power to it instead */
    if (digi_cap && tune_cap <= 0.0f && w > TX_PLAYER_MAX_PWR_W) w = TX_PLAYER_MAX_PWR_W;
    if (tune_cap > 0.0f && w > tune_cap) w = tune_cap;     /* PWR turned up, the battery's top, a restore */
    if (xvtr_cap > 0.0f && w > xvtr_cap) w = xvtr_cap;     /* whoever asks: PWR, an app's own, TUNE's, TCI, CAT */
    /* A TUNE keeps its own power: the base is in USB-D for it, and the user asked for that carrier */
    if (atomic_load(&train_hold) && tune_cap <= 0.0f) w = 0.0f;
    if (atomic_load(&band_hold)) w = 0.0f;     /* a TUNE too: none starts there, one moved there is stopped */
    if (cfg_tx_block()) w = 0.0f;
    hw_set_f(HW_TXPWR_F, w);
}

/* Reconcile the band hold without changing the saved VOX or KEY Train settings.
 * The X6200's open TX is inspected even when the cached hold is unchanged. */
static void band_hold_set(bool hold) {
    int32_t f;

    open_tx_look();
    bool changed = hold != atomic_load(&band_hold);
    atomic_store(&band_hold, hold);
    /* A coalesced external-inhibit pulse may have zeroed power without changing
     * this cached hold. Reconcile through every ordinary cap even then. */
    set_txpwr(txpwr_asked);
    if (!changed) return;
    if (hold) {
        atomic_store(&band_said, false);
    }
    hw_set_i(HW_VOX, param_i_get(cfg_vox_on) && !hold);
    hw_set_i(HW_KEY_TRAIN, param_i_get(cfg_key_train) || hold);
    radio_key_refused(&f);  /* the one refused: the dial where the base keys it (split_keys_dial) */
    LV_LOG_USER("Radio TX %s: TX at %i%s", hold ? "held at 0 W, VOX off, KEY Train on" : "hold off", f,
                atomic_load(&off_dial) ? ", Birdie Cal off the dial" : "");
}
