/*
 *  SPDX-License-Identifier: LGPL-2.1-or-later
 *
 *  OpenX6X00 - the radio hardware as radio.c drives it: the base's settings, its packets,
 *  the board's pins. One implementation per radio (hw/x6100/hw_x6100.c for the X6100,
 *  hw/x6200/hw_x6200.c for the X6200, hw/stub/hw_stub.c for none);
 *  radio.c, the GUI and the apps know only this file and radio_types.h.
 *
 *  Threads: radio.c holds its lock around every call except hw_flow_read(), hw_flow_restart()
 *  and hw_pin_set(), which the radio thread (the flow) and the GUI thread use on their own.
 *
 *  Copyright (c) 2026 RF.Guru
 */

#pragma once

#include <complex.h>
#include <stdbool.h>
#include <stdint.h>

#include "../radio_types.h"

#ifdef __cplusplus
extern "C" {
#endif

/* A packet from the base as radio.c reads it: the samples decoded, the meters, the supply */
typedef struct {
    const float _Complex *samples;
    uint16_t    n_samples;
    bool        tx;             /* the base transmits */
    bool        atu_status;     /* the tuner has finished */
    bool        charging;
    bool        vext_flag;      /* on external power, as the base sees it */
    uint8_t     tx_power;       /* 0.1 W */
    uint8_t     vswr;           /* 0.1 */
    uint8_t     alc_level;      /* 0.1: 0 the ALC at rest, up with its action (the X6100's own count turned round) */
    uint8_t     vext;           /* 0.1 V */
    uint8_t     vbat;           /* 0.1 V */
    uint8_t     batcap;         /* % */
    uint32_t    atu_params;     /* the tuner's network */
    uint32_t    lo_freq;        /* the frequency the samples are centred on, 0 when the base does not say */
    uint8_t     fft_dec;        /* the base's own spectrum decimation (1, 2, 4 ...), 0 when it does not say */
    bool        vary_freq;
    uint32_t    hkey;           /* the hand mic's key (RADIO_HKEY_*), 0 for none */
    /* A base that computes the spectrum itself (HW_CAP_PSD) sends it in place of the samples
     * (samples NULL): n_psd bins in dB, the lowest frequency first, over the span of the zoom */
    const float *psd;
    uint16_t    n_psd;
    uint32_t    psd_span;       /* Hz the bins cover (the base's own zoom steps) */
    int16_t     smeter_dbm;     /* its own S-meter reading, dBm; 0 with the samples */
    bool        power_key;      /* the POWER key, where the base reports it instead of an input device */
} hw_pack_t;

/* The board's switches */
typedef enum {
    HW_PIN_WIFI,
    HW_PIN_LIGHT,       /* the panel's backlight supply */
    HW_PIN_USB_CAT,     /* the USB port's serial lines to the CAT port */
    HW_PIN_MORSE_KEY,   /* the GUI's CW key into the base */
    HW_PIN_BB_RESET,    /* the base's reset */
} hw_pin_t;

/* The base's scalar settings, hw_set_i() / hw_set_f() (the ones marked f take a float) */
typedef enum {
    HW_VFO_CUR,         /* radio_vfo_t */
    HW_SPLIT,
    HW_RFG,
    HW_SQL,
    HW_RXVOL,
    HW_TXPWR_F,         /* W */
    HW_ADC_DAC_GAIN_F,  /* dB */
    HW_DAC_GAIN_F,      /* dB */
    HW_TX_I_OFFSET,
    HW_TX_Q_OFFSET,
    HW_ATU_ENABLED,
    HW_ATU_NETWORK,
    HW_ATU_TUNE,
    HW_SWRSCAN,
    HW_PTT,
    HW_MODEM,           /* the GUI's audio on the air */
    HW_RECORD,          /* the base plays the GUI's audio to the speaker */
    HW_MIC,             /* radio_mic_sel_t */
    HW_HMIC,
    HW_IMIC,
    HW_MONI,
    HW_LINE_IN,
    HW_LINE_OUT,
    HW_SPMODE,
    HW_CHARGER,
    HW_BIAS_DRIVE,
    HW_BIAS_FINAL,
    HW_RIT,
    HW_XIT,
    HW_IF_SHIFT_ON,
    HW_IF_SHIFT_HZ,
    HW_FFT_DEC,         /* log2 of the base's spectrum decimation */
    HW_FM_EMP,
    HW_TX_FILTER_LOW,
    HW_TX_FILTER_HIGH,
    HW_FILTER1_LOW,
    HW_FILTER1_HIGH,
    HW_FILTER2_LOW,
    HW_FILTER2_HIGH,
    HW_COMP_ON,
    HW_COMP_LEVEL,
    HW_COMP_THRESHOLD_F,
    HW_COMP_MAKEUP_F,
    HW_CESSB,
    HW_CESSB_POWER_UP_F,
    HW_VOX,
    HW_VOX_GAIN,
    HW_VOX_AG,
    HW_VOX_DELAY,
    HW_KEY_TONE,
    HW_KEY_SPEED,
    HW_KEY_MODE,        /* radio_key_mode_t */
    HW_IAMBIC_MODE,     /* radio_iambic_mode_t */
    HW_KEY_VOL,
    HW_KEY_TRAIN,
    HW_QSK_TIME,
    HW_KEY_RATIO_F,
    HW_CW_PEAK,
    HW_CW_PEAK_Q,
    HW_AGC_HANG,
    HW_AGC_KNEE,
    HW_AGC_SLOPE,
    HW_AGC_TIME,
    HW_DNF,
    HW_DNF_CENTER,
    HW_DNF_WIDTH,
    HW_DNF_AUTO,
    HW_NB,
    HW_NB_LEVEL,
    HW_NB_WIDTH,
    HW_NR,
    HW_NR_LEVEL,
    HW_PARAM_COUNT
} hw_param_t;

/* The pins and the packet stream; false: this is not the radio */
bool hw_init(void);
/* One attempt to reach the base; radio.c retries and pulses the reset */
bool hw_control_init(void);
void hw_bb_reset(void);
/* The base answered: what it needs once (its sample format, the IF shift at rest) */
void hw_start(void);

radio_base_ver_t hw_base_ver(void);
const char      *hw_base_ver_str(void);
/* HW_CAP_* bits (radio_types.h); valid once hw_start() ran */
uint32_t         hw_caps(void);

/* A setting to the base. True when it changed (the base got it); the settings the base holds in
 * a register of their own are compared first, the packed ones are always sent */
bool hw_set_i(hw_param_t p, int32_t v);
bool hw_set_f(hw_param_t p, float v);
/* The last value sent, for the register-sized settings (HW_TX_I_OFFSET, HW_TX_Q_OFFSET,
 * HW_RIT, HW_XIT, HW_AGC_TIME, HW_FILTER*, HW_ATU_NETWORK); 0 for the others */
int32_t hw_get_i(hw_param_t p);

/* Last commanded shared-register bits, not an independent baseband readback.
 * The caller must hold the radio control lock. False means unsupported. */
typedef struct {
    bool record;
    bool modem;
    bool ptt;
} hw_commanded_audio_route_t;
bool hw_get_commanded_audio_route(hw_commanded_audio_route_t *route);

#ifdef ENABLE_FREEDV_DIAGNOSTICS
/* Cached commands only. Values describe the backend's register representation;
 * they are not measured hardware state. In particular, shared_route_bits may
 * contain tune, split and power-off bits: it must never be written back whole. */
typedef struct {
    hw_commanded_audio_route_t route;
    bool raw_route_bits_available;
    uint32_t shared_route_bits;
    uint32_t protected_route_bits; /* same word, excluding only RECORD/MODEM/software PTT */
    uint8_t microphone, hand_mic_gain, internal_mic_gain;
    bool vox_enabled;
    uint8_t line_in, line_out, speaker_mode, speaker_level, monitor_level;
    uint8_t requested_power_tenths_watt, active_vfo;
    uint32_t vfo_a_hz, vfo_b_hz, backend_mode_a, backend_mode_b;
    int16_t rx_filter_low_hz, rx_filter_high_hz;
} hw_freedv_cached_snapshot_t;
/* Caller holds the radio control lock. False means this backend is unsupported. */
bool hw_get_freedv_cached_snapshot(hw_freedv_cached_snapshot_t *snapshot);
/* Exact cached comparison of unchanged tuning and unowned register bits. */
static inline bool hw_freedv_tuning_matches(const hw_freedv_cached_snapshot_t *a,
                                            const hw_freedv_cached_snapshot_t *b) {
    return a && b && a->active_vfo == b->active_vfo && a->vfo_a_hz == b->vfo_a_hz &&
        a->vfo_b_hz == b->vfo_b_hz && a->backend_mode_a == b->backend_mode_a &&
        a->backend_mode_b == b->backend_mode_b && a->rx_filter_low_hz == b->rx_filter_low_hz &&
        a->rx_filter_high_hz == b->rx_filter_high_hz &&
        a->raw_route_bits_available == b->raw_route_bits_available &&
        a->protected_route_bits == b->protected_route_bits;
}
static inline bool hw_freedv_snapshot_matches(const hw_freedv_cached_snapshot_t *a,
                                              const hw_freedv_cached_snapshot_t *b) {
    return hw_freedv_tuning_matches(a, b) && a->route.record == b->route.record &&
        a->route.modem == b->route.modem && a->route.ptt == b->route.ptt &&
        a->microphone == b->microphone && a->hand_mic_gain == b->hand_mic_gain &&
        a->internal_mic_gain == b->internal_mic_gain && a->vox_enabled == b->vox_enabled &&
        a->line_in == b->line_in && a->line_out == b->line_out &&
        a->speaker_mode == b->speaker_mode && a->speaker_level == b->speaker_level &&
        a->monitor_level == b->monitor_level &&
        a->requested_power_tenths_watt == b->requested_power_tenths_watt;
}
/* Restore only fields the diagnostic owns. Caller has independently confirmed
 * fresh post-clear RX and must separately check transport and cached readback.
 * No full packed register is ever written back. */
static inline void hw_freedv_restore_owned(const hw_freedv_cached_snapshot_t *s) {
    hw_set_i(HW_MODEM, s->route.modem);
    hw_set_i(HW_PTT, s->route.ptt);
    hw_set_i(HW_MIC, s->microphone);
    hw_set_i(HW_HMIC, s->hand_mic_gain);
    hw_set_i(HW_IMIC, s->internal_mic_gain);
    hw_set_i(HW_RECORD, s->route.record);
    hw_set_i(HW_LINE_IN, s->line_in);
    hw_set_i(HW_LINE_OUT, s->line_out);
    hw_set_i(HW_SPMODE, s->speaker_mode);
    hw_set_i(HW_RXVOL, s->speaker_level);
    hw_set_i(HW_MONI, s->monitor_level);
    hw_set_f(HW_TXPWR_F, s->requested_power_tenths_watt / 10.0f);
    hw_set_i(HW_VOX, s->vox_enabled);
}

#endif

/* The two VFOs. True when the value changed */
bool hw_vfo_set_freq(radio_vfo_t vfo, int32_t hz);
bool hw_vfo_set_mode(radio_vfo_t vfo, radio_mode_t mode);
void hw_vfo_set_agc(radio_vfo_t vfo, radio_agc_t agc);
void hw_vfo_set_att(radio_vfo_t vfo, bool on);
void hw_vfo_set_pre(radio_vfo_t vfo, bool on);

void hw_poweroff(void);
/* The whole settings block to the base again; false: it did not get through */
bool hw_resend_all(void);
/* A TX bit the GUI set (PTT, modem, tune, SWR scan, calibration) is on in the settings block */
bool hw_tx_keyed_by_gui(void);

void hw_pin_set(hw_pin_t pin, bool on);

/* The next packet from the base, decoded; false: none yet */
bool hw_flow_read(hw_pack_t *pack);
/* The stream stopped: open it again */
void hw_flow_restart(void);

#ifdef __cplusplus
}
#endif
