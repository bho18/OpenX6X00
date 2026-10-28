/*
 *  SPDX-License-Identifier: LGPL-2.1-or-later
 *
 *  OpenX6X00 - a radio made of nothing, behind hw.h: every setting is kept and read back, the
 *  packet stream is noise with one carrier 1 kHz up, the battery sits at 7.8 V. For the GUI on a
 *  desktop (cmake -DHW_BACKEND=stub) and as the template for a new radio's backend: everything
 *  hw.h asks for, in the order of hw.h, with nothing else to read around it.
 *
 *  Copyright (c) 2026 RF.Guru
 */

#define _GNU_SOURCE
#include "../hw.h"

#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#define SAMPLE_RATE     100000      /* the X6100's IQ stream */
#define SAMPLES         512         /* per packet, like the X6100's fp32 packet */
#define NOISE_RMS       0.00004f    /* about -120 dB in the panel: a real base's floor */
#define TONE_AMP        0.002f      /* a loud signal, S9 and some */
#define TONE_HZ         1000.0f

static int32_t  reg_i[HW_PARAM_COUNT];
static float    reg_f[HW_PARAM_COUNT];
static bool     pins[HW_PIN_BB_RESET + 1];

static struct {
    int32_t      freq;
    radio_mode_t mode;
    radio_agc_t  agc;
    bool         att, pre;
} vfo[2] = { { 7062000, radio_mode_usb_dig, radio_agc_auto, false, false },
             { 14074000, radio_mode_usb_dig, radio_agc_auto, false, false } };

static float _Complex samples[SAMPLES];
static uint64_t       next_pack_ns;     /* when the next packet is due */
static float          tone_phase;

/* ---- Init ----------------------------------------------------------------------------- */

bool hw_init(void) {
    pins[HW_PIN_LIGHT] = true;
    return true;
}

bool hw_control_init(void) {
    return true;
}

void hw_bb_reset(void) {
}

void hw_start(void) {
    next_pack_ns = 0;
}

radio_base_ver_t hw_base_ver(void) {
    return (radio_base_ver_t){0, 0, 0, 0};
}

const char *hw_base_ver_str(void) {
    return "stub";
}

uint32_t hw_caps(void) {
    return 0;
}

/* ---- Settings ------------------------------------------------------------------------- */

/* The X6100 keeps these in a register of their own and compares before the write; so does the
 * stub, to give radio.c the same "changed" answers */
static bool has_register(hw_param_t p) {
    switch (p) {
        case HW_TX_I_OFFSET: case HW_TX_Q_OFFSET: case HW_RIT: case HW_XIT: case HW_AGC_TIME:
        case HW_FILTER1_LOW: case HW_FILTER1_HIGH: case HW_FILTER2_LOW: case HW_FILTER2_HIGH:
        case HW_ATU_NETWORK:
            return true;
        default:
            return false;
    }
}

int32_t hw_get_i(hw_param_t p) {
    return (p < HW_PARAM_COUNT && has_register(p)) ? reg_i[p] : 0;
}

bool hw_get_commanded_audio_route(hw_commanded_audio_route_t *route) {
    if (!route) return false;
    route->record = reg_i[HW_RECORD] != 0;
    route->modem = reg_i[HW_MODEM] != 0;
    route->ptt = reg_i[HW_PTT] != 0;
    return true;
}

#ifdef ENABLE_FREEDV_DIAGNOSTICS
bool hw_get_freedv_cached_snapshot(hw_freedv_cached_snapshot_t *s) {
    if (!s) return false;
    /* No X6200 register layout is simulated here; ordinary values are enough
     * to exercise the read-only interface without claiming hardware evidence. */
    float power = reg_f[HW_TXPWR_F];
    uint8_t tenths = !isfinite(power) || power <= 0 ? 0 : power >= 25.5f ? 255 : (uint8_t)(power * 10);
    *s = (hw_freedv_cached_snapshot_t){
        .route = {reg_i[HW_RECORD] != 0, reg_i[HW_MODEM] != 0, reg_i[HW_PTT] != 0},
        .raw_route_bits_available = false,
        .protected_route_bits = (reg_i[HW_SPLIT] ? 2u : 0u) | (reg_i[HW_ATU_TUNE] ? 0x2000u : 0u),
        .vox_enabled = reg_i[HW_VOX] != 0,
        .microphone = reg_i[HW_MIC], .hand_mic_gain = reg_i[HW_HMIC], .internal_mic_gain = reg_i[HW_IMIC],
        .line_in = reg_i[HW_LINE_IN], .line_out = reg_i[HW_LINE_OUT], .speaker_mode = reg_i[HW_SPMODE],
        .speaker_level = reg_i[HW_RXVOL], .monitor_level = reg_i[HW_MONI],
        .requested_power_tenths_watt = tenths, .active_vfo = reg_i[HW_VFO_CUR],
        .vfo_a_hz = vfo[0].freq, .vfo_b_hz = vfo[1].freq,
        .backend_mode_a = vfo[0].mode, .backend_mode_b = vfo[1].mode,
        .rx_filter_low_hz = reg_i[HW_FILTER1_LOW], .rx_filter_high_hz = reg_i[HW_FILTER1_HIGH],
    };
    return true;
}
#endif

bool hw_set_i(hw_param_t p, int32_t v) {
    if (p >= HW_PARAM_COUNT) {
        fprintf(stderr, "hw_stub: hw_set_i(%d) is not a setting\n", p);
        return false;
    }
    if (has_register(p) && reg_i[p] == v) {
        return false;
    }
    reg_i[p] = v;
    return true;
}

bool hw_set_f(hw_param_t p, float v) {
    if (p >= HW_PARAM_COUNT) {
        fprintf(stderr, "hw_stub: hw_set_f(%d) is not a setting\n", p);
        return false;
    }
    reg_f[p] = v;
    return true;
}

bool hw_vfo_set_freq(radio_vfo_t v, int32_t hz) {
    if (vfo[v].freq == hz) {
        return false;
    }
    vfo[v].freq = hz;
    return true;
}

bool hw_vfo_set_mode(radio_vfo_t v, radio_mode_t mode) {
    if (vfo[v].mode == mode) {
        return false;
    }
    vfo[v].mode = mode;
    return true;
}

void hw_vfo_set_agc(radio_vfo_t v, radio_agc_t agc) {
    vfo[v].agc = agc;
}

void hw_vfo_set_att(radio_vfo_t v, bool on) {
    vfo[v].att = on;
}

void hw_vfo_set_pre(radio_vfo_t v, bool on) {
    vfo[v].pre = on;
}

void hw_poweroff(void) {
}

bool hw_resend_all(void) {
    return true;
}

bool hw_tx_keyed_by_gui(void) {
    return reg_i[HW_PTT] || reg_i[HW_MODEM] || reg_i[HW_ATU_TUNE] || reg_i[HW_SWRSCAN];
}

void hw_pin_set(hw_pin_t pin, bool on) {
    if (pin <= HW_PIN_BB_RESET) {
        pins[pin] = on;
    }
}

/* ---- The packet stream -------------------------------------------------------------------- */

static uint64_t now_ns(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (uint64_t)ts.tv_sec * 1000000000ULL + (uint64_t)ts.tv_nsec;
}

/* Gaussian noise, two values at a time (Box-Muller on the C library's generator) */
static float gauss(void) {
    static bool  have;
    static float spare;
    if (have) {
        have = false;
        return spare;
    }
    float u1 = ((float)rand() + 1.0f) / ((float)RAND_MAX + 2.0f);
    float u2 = (float)rand() / (float)RAND_MAX;
    float r  = sqrtf(-2.0f * logf(u1));
    spare = r * sinf(2.0f * (float)M_PI * u2);
    have  = true;
    return r * cosf(2.0f * (float)M_PI * u2);
}

bool hw_flow_read(hw_pack_t *p) {
    uint64_t now = now_ns();
    const uint64_t period = (uint64_t)SAMPLES * 1000000000ULL / SAMPLE_RATE;   /* 5.12 ms */

    if (next_pack_ns == 0) {
        next_pack_ns = now;
    }
    if (now < next_pack_ns) {
        return false;
    }
    next_pack_ns += period;
    if ((int64_t)(now - next_pack_ns) > (int64_t)(50 * period)) {   /* far behind (a debugger stop): afresh */
        next_pack_ns = now;
    }

    bool  tx   = hw_tx_keyed_by_gui();
    float gain = (vfo[reg_i[HW_VFO_CUR] & 1].pre ? 2.0f : 1.0f) * (vfo[reg_i[HW_VFO_CUR] & 1].att ? 0.5f : 1.0f);
    float step = 2.0f * (float)M_PI * TONE_HZ / SAMPLE_RATE;

    for (int i = 0; i < SAMPLES; i++) {
        float re = NOISE_RMS * gauss(), im = NOISE_RMS * gauss();
        if (!tx) {
            re += TONE_AMP * cosf(tone_phase);
            im += TONE_AMP * sinf(tone_phase);
        }
        tone_phase += step;
        if (tone_phase > 2.0f * (float)M_PI) {
            tone_phase -= 2.0f * (float)M_PI;
        }
        samples[i] = (gain * re) + (gain * im) * I;
    }

    memset(p, 0, sizeof(*p));
    p->samples    = samples;
    p->n_samples  = SAMPLES;
    p->tx         = tx;
    p->tx_power   = tx ? (uint8_t)(reg_f[HW_TXPWR_F] * 10.0f + 0.5f) : 0;
    p->vswr       = tx ? 11 : 0;
    p->alc_level  = 0;
    p->vext       = 0;
    p->vbat       = 78;
    p->batcap     = 80;
    p->atu_params = (uint32_t)reg_i[HW_ATU_NETWORK];
    p->atu_status = reg_i[HW_ATU_TUNE] != 0;
    return true;
}

void hw_flow_restart(void) {
    next_pack_ns = 0;
}
