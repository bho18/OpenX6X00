/*
 *  SPDX-License-Identifier: LGPL-2.1-or-later
 *
 *  OpenX6X00 - a radio made of nothing, behind hw.h: every setting is kept and read back, the
 *  packet stream is noise with one carrier 1 kHz up (or a recording, HOST_STUB_IQ), the battery sits
 *  at 7.8 V. For the GUI on a desktop (cmake -DHW_BACKEND=stub) and as the template for a new radio's
 *  backend: everything hw.h asks for, in the order of hw.h, with nothing else to read around it.
 *
 *  For the scanner and the beacons on a desktop the carrier can be put on some frequencies only:
 *  HOST_STUB_SCAN=118300000,27185000 (Hz; on 15 s, off 15 s) and HOST_STUB_BEACONS=13,0
 *  (the NCDXF beacons, by their place in the round, keyed in their slots). Either set: nothing
 *  anywhere else.
 *
 *  Copyright (c) 2026 RF.Guru
 */

#define _GNU_SOURCE
#include "../../freedv/writer_gate.h"
#include "../../freedv/features.h"
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

#define SCENE_MAX       16

static struct {
    bool    on;
    int32_t carrier[SCENE_MAX];
    int     carriers;
    int     beacon[SCENE_MAX];
    int     beacons;
} scene;

static float _Complex samples[SAMPLES];
static uint64_t       next_pack_ns;     /* when the next packet is due */
static float          tone_phase;

/* ---- Init ----------------------------------------------------------------------------- */

static int list_env(const char *name, int32_t *out) {
    const char *v = getenv(name);
    int         n = 0;

    while (v && *v && n < SCENE_MAX) {
        char *end;
        long  x = strtol(v, &end, 10);
        if (end == v) break;
        out[n++] = (int32_t)x;
        v        = *end == ',' ? end + 1 : end;
    }
    return n;
}

bool hw_init(void) {
    int32_t b[SCENE_MAX];

    pins[HW_PIN_LIGHT] = true;
    scene.carriers     = list_env("HOST_STUB_SCAN", scene.carrier);
    scene.beacons      = list_env("HOST_STUB_BEACONS", b);
    for (int i = 0; i < scene.beacons; i++) scene.beacon[i] = b[i];
    scene.on = scene.carriers || scene.beacons;
    return true;
}

/* The NCDXF schedule (src/beacons.c): on band k in slot s beacon (s - k) mod 18 */
static const int32_t beacon_hz[5] = {14100000, 18110000, 21150000, 24930000, 28200000};

/* Whether the carrier is on the air at the frequency tuned now; the tone's offset into hz */
static bool scene_carrier(int32_t f, float *hz) {
    struct timespec ts;

    clock_gettime(CLOCK_REALTIME, &ts);
    *hz = TONE_HZ;
    if (!scene.on) return true;
    for (int i = 0; i < scene.carriers; i++)
        if (labs((long)f - scene.carrier[i]) < 2000) return ts.tv_sec % 30 < 15;
    for (int k = 0; k < 5; k++) {
        if (labs((long)f - beacon_hz[k]) > 500) continue;
        int on = ((int)(ts.tv_sec % 180) / 10 - k + 18) % 18;
        for (int i = 0; i < scene.beacons; i++)
            if (scene.beacon[i] == on) {
                *hz = 600.0f;     /* at the CW pitch, in the CW filter */
                return (ts.tv_nsec / 250000000) % 2 == 0 || ts.tv_sec % 10 >= 6;   /* the call keyed, then dashes */
            }
    }
    return false;
}

bool hw_control_init(void) {
    return true;
}

void hw_bb_reset(void) {
    FD_HARDWARE_GUARD(return, false);
}

void hw_start(void) {
    next_pack_ns = 0;
}

#ifdef BOARD_X6200
/* No base, no block: the fixed bias */
const uint8_t *hw_cal_block(size_t *len) {
    *len = 0;
    return NULL;
}
#endif

radio_base_ver_t hw_base_ver(void) {
    return (radio_base_ver_t){0, 0, 0, 0};
}

const char *hw_base_ver_str(void) {
    return "stub";
}

/* None: the X6100's ways. HOST_STUB_CW=x6200: the X6200's CW, its spectrum on the dial and the tone rising as the
 * dial goes up in CW too (#115), to see its picture on a PC: the carrier keeps its place in the stream, 1 kHz off
 * the dial in SSB, in every mode (the S-meter from the stream still takes the X6100's CW band) */
uint32_t hw_caps(void) {
    static int cw = -1;

    if (cw < 0) {
        const char *e = getenv("HOST_STUB_CW");
        cw = e && !strcmp(e, "x6200");
    }
    return cw ? HW_CAP_CW_SPECTRUM_ON_DIAL | HW_CAP_CW_TONE_RISES : 0;
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

#ifdef ENABLE_FREEDV_CONTROL
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
    FD_HARDWARE_GUARD(return false, (p == HW_PTT || p == HW_MODEM || p == HW_ATU_TUNE || p == HW_SWRSCAN || p == HW_HMIC || p == HW_IMIC || p == HW_VOX) && !v);
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
    FD_HARDWARE_GUARD(return false, p == HW_TXPWR_F && v <= 0);
    if (p >= HW_PARAM_COUNT) {
        fprintf(stderr, "hw_stub: hw_set_f(%d) is not a setting\n", p);
        return false;
    }
    reg_f[p] = v;
    return true;
}

bool hw_vfo_set_freq(radio_vfo_t v, int32_t hz) {
    FD_HARDWARE_GUARD(return false, false);
    if (vfo[v].freq == hz) {
        return false;
    }
    vfo[v].freq = hz;
    return true;
}

bool hw_vfo_set_mode(radio_vfo_t v, radio_mode_t mode) {
    FD_HARDWARE_GUARD(return false, false);
    if (vfo[v].mode == mode) {
        return false;
    }
    vfo[v].mode = mode;
    return true;
}

void hw_vfo_set_agc(radio_vfo_t v, radio_agc_t agc) {
    FD_HARDWARE_GUARD(return, false);
    vfo[v].agc = agc;
}

void hw_vfo_set_att(radio_vfo_t v, bool on) {
    FD_HARDWARE_GUARD(return, false);
    vfo[v].att = on;
}

void hw_vfo_set_pre(radio_vfo_t v, bool on) {
    FD_HARDWARE_GUARD(return, false);
    vfo[v].pre = on;
}

void hw_poweroff(void) {
    FD_HARDWARE_GUARD(return, false);
    fprintf(stderr, "hw_stub: power off\n");     /* the PC build stays on: its log says it was asked */
}

bool hw_resend_all(void) {
    return true;
}

bool hw_tx_keyed_by_gui(void) {
    return reg_i[HW_PTT] || reg_i[HW_MODEM] || reg_i[HW_ATU_TUNE] || reg_i[HW_SWRSCAN];
}

void hw_pin_set(hw_pin_t pin, bool on) {
    FD_HARDWARE_GUARD(return, pin != HW_PIN_BB_RESET && (pin != HW_PIN_MORSE_KEY || !on));
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

/* What PRE and ATT do to the stream: twice and half the level, or HOST_STUB_FE in dB ("16,51": a preamp of 16 dB
 * and an attenuator of 51 dB, an X6200 on 80 m in #125), for the S-meter's corrections and their Measure */
static float front_end_gain(bool pre, bool att) {
    static float pre_gain = -1.0f, att_gain = 0.5f;

    if (pre_gain < 0.0f) {
        const char *e = getenv("HOST_STUB_FE");
        float       p, a;

        pre_gain = 2.0f;
        if (e && sscanf(e, "%f,%f", &p, &a) == 2 && p >= 0.0f && p <= 60.0f && a >= 0.0f && a <= 80.0f) {
            pre_gain = powf(10.0f, p / 20.0f);
            att_gain = powf(10.0f, -a / 20.0f);
        }
    }
    return (pre ? pre_gain : 1.0f) * (att ? att_gain : 1.0f);
}

/* HOST_STUB_CARRIERS=<Hz>: also a weaker carrier on every multiple of that many Hz, which keeps its frequency as
 * the dial turns (the 1 kHz one goes along with the dial), to see Waterfall Mode Fixed on a PC (#10). The stream
 * is drawn the other way up (the 1 kHz carrier shows 1 kHz below the dial in SSB): a carrier f above the centre
 * goes in at -f. Four levels in turn, so they can be told apart. Up to 1 kHz from the stream's edges: one left out
 * further in, the Fixed waterfall took it for gone there and kept nothing of it on the side tuned away from */
static void add_band(float _Complex *s, int32_t center, float gain) {
    const int64_t   reach = SAMPLE_RATE / 2 - 1000;
    static int      step = -1;
    static uint64_t n;          /* samples since the start: each carrier's phase goes on across the packets */

    if (step < 0) {
        const char *e = getenv("HOST_STUB_CARRIERS");
        step = e && atoi(e) >= 1000 && atoi(e) <= 50000 ? atoi(e) : 0;
    }
    if (!step) {
        return;
    }
    for (int64_t f = ((int64_t)center - reach) / step * step + step; f < (int64_t)center + reach; f += step) {
        double         w   = -2.0 * M_PI * (double)(f - center) / SAMPLE_RATE;
        float          amp = gain * TONE_AMP * (0.04f + 0.06f * (float)((f / step) % 4));
        float _Complex ph  = amp * cexpf(I * (float)fmod(w * (double)n, 2.0 * M_PI));
        float _Complex rot = cexpf(I * (float)w);

        for (int i = 0; i < SAMPLES; i++) {
            s[i] += ph;
            ph *= rot;
        }
    }
    n += SAMPLES;
}

/* HOST_STUB_SILENT="<from>,<to>,...": no packets between those ms after the first read, as from a base whose packets
 * do not decode (#152: an X6200 base older than 1.0.7); a last <from> without its <to> holds for good, "0" none at all */
static bool silent(uint64_t now) {
    static int64_t  edge[8];
    static int      edges = -1;
    static uint64_t first;

    if (edges < 0) {
        const char *e = getenv("HOST_STUB_SILENT");
        char       *end;

        edges = 0;
        first = now;
        while (e && *e && edges < 8) {
            edge[edges++] = strtoll(e, &end, 10);
            e = *end == ',' ? end + 1 : "";
        }
    }
    bool    in = false;
    int64_t ms = (int64_t)((now - first) / 1000000);

    for (int i = 0; i < edges && ms >= edge[i]; i++) {
        in = !in;
    }
    return in;
}

/* HOST_STUB_IQ=<file>: the stream from a recording instead of the noise and the carrier, to see real signals on a
 * PC: 100 kS/s complex float32 (I and Q interleaved, the host's byte order), centred on the dial, drawn the other
 * way up as the stream is (a signal f above the dial at -f), played round and round. Read whole at the first
 * packet; NULL: none, or it could not be read (then the noise) */
static float _Complex *iq_rec;
static size_t          iq_len, iq_pos;

static void iq_load(void) {
    static bool tried;
    const char *e = getenv("HOST_STUB_IQ");
    FILE       *f;
    long        n;

    if (tried || !e) {
        return;
    }
    tried = true;
    if (!(f = fopen(e, "rb"))) {
        fprintf(stderr, "HOST_STUB_IQ: %s: cannot open\n", e);
        return;
    }
    fseek(f, 0, SEEK_END);
    n = ftell(f) / (long) sizeof(float _Complex);
    fseek(f, 0, SEEK_SET);
    if (n > 0 && (iq_rec = malloc((size_t) n * sizeof(float _Complex)))) {
        iq_len = fread(iq_rec, sizeof(float _Complex), (size_t) n, f);
    }
    fclose(f);
    if (!iq_len) {
        free(iq_rec);
        iq_rec = NULL;
        fprintf(stderr, "HOST_STUB_IQ: %s: nothing read\n", e);
    }
}

bool hw_flow_read(hw_pack_t *p) {
    uint64_t now = now_ns();
    const uint64_t period = (uint64_t)SAMPLES * 1000000000ULL / SAMPLE_RATE;   /* 5.12 ms */

    if (silent(now)) {
        return false;
    }
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
    float gain = front_end_gain(vfo[reg_i[HW_VFO_CUR] & 1].pre, vfo[reg_i[HW_VFO_CUR] & 1].att);
    float tone_hz;
    bool  carrier = scene_carrier(vfo[reg_i[HW_VFO_CUR] & 1].freq, &tone_hz);
    float step    = 2.0f * (float)M_PI * tone_hz / SAMPLE_RATE;
    radio_mode_t m = vfo[reg_i[HW_VFO_CUR] & 1].mode;

    /* The test signals where the receiver's filter is: the tone as generated lands below the
     * dial (LSB's side, where the panel starts); USB's is above */
    if (scene.on && (m == radio_mode_usb || m == radio_mode_usb_dig)) step = -step;

    iq_load();
    for (int i = 0; i < SAMPLES && iq_rec; i++) {
        samples[i] = gain * (tx ? 0.0f : iq_rec[iq_pos]) + gain * NOISE_RMS * 0.1f * (gauss() + gauss() * I);
        iq_pos = (iq_pos + 1) % iq_len;
    }
    for (int i = 0; i < SAMPLES && !iq_rec; i++) {
        float re = NOISE_RMS * gauss(), im = NOISE_RMS * gauss();
        if (!tx && carrier) {
            re += TONE_AMP * cosf(tone_phase);
            im += TONE_AMP * sinf(tone_phase);
        }
        tone_phase += step;
        if (tone_phase > 2.0f * (float)M_PI) {
            tone_phase -= 2.0f * (float)M_PI;
        }
        samples[i] = (gain * re) + (gain * im) * I;
    }
    if (!tx) {
        add_band(samples, vfo[reg_i[HW_VFO_CUR] & 1].freq, gain);
    }

    memset(p, 0, sizeof(*p));
    p->samples    = samples;
    p->n_samples  = SAMPLES;
    p->tx         = tx;
    /* The SWR it reads in TX: 1.1, or HOST_STUB_SWR (tenths: 34 is an antenna at 3.4 that a tuner still has to match) */
    static int swr = -1;
    if (swr < 0) {
        const char *e = getenv("HOST_STUB_SWR");
        swr = e && atoi(e) >= 10 && atoi(e) <= 255 ? atoi(e) : 11;
    }
    p->tx_power   = tx ? (uint8_t)(reg_f[HW_TXPWR_F] * 10.0f + 0.5f) : 0;
    p->vswr       = tx ? (uint8_t)swr : 0;
    p->alc_level  = 0;
    /* On battery: 7.8 V and the base's 80 %, or HOST_STUB_BAT ("82,0": 8.2 V with the base at 0 %, #49's
     * full pack that its base never saw charged). A supply after them, and the charging flag: "79,66,138,1" a pack
     * at 7.9 V and 66 % charging from 13.8 V, "79,66,138,0" the same with the charger off (#108) */
    static int vbat = -1, batcap = 80, vext, charging;
    if (vbat < 0) {
        const char *e = getenv("HOST_STUB_BAT");
        int         v, c, x = 0, g = 0;

        vbat = 78;
        if (e && sscanf(e, "%d,%d,%d,%d", &v, &c, &x, &g) >= 2 && v >= 0 && v <= 255 && c >= 0 && c <= 100 &&
            x >= 0 && x <= 255) {
            vbat = v;
            batcap = c;
            vext = x;
            charging = x > 0 && g;
        }
    }
    p->vext       = (uint8_t)vext;
    p->vext_flag  = vext > 0;
    p->charging   = charging;
    p->vbat       = (uint8_t)vbat;
    p->batcap     = (uint8_t)batcap;
    p->atu_params = (uint32_t)reg_i[HW_ATU_NETWORK];
    p->atu_status = reg_i[HW_ATU_TUNE] != 0;
    return true;
}

void hw_flow_restart(void) {
    next_pack_ns = 0;
}

#ifdef FREEDV_HOST_TEST_PROFILE
#include <stdatomic.h>
#include <pthread.h>
/* Low bit is the held state; upper bits retain physical release edges. One
 * atomic snapshot cannot combine a new pressed level with an old counter. */
static atomic_uint_fast64_t test_ptt[2];
static atomic_uint_fast64_t test_ptt_sequence;
/* Changes to either raw source share the release admission boundary. Reading
 * two independently changing levels cannot prove both were released at once. */
static pthread_mutex_t native_mutex=PTHREAD_MUTEX_INITIALIZER;
void hw_stub_ptt_set(hw_ptt_source_t source, bool pressed) {
    if(source<HW_PTT_SOURCE_RADIO || source>HW_PTT_SOURCE_HAND)return;
    pthread_mutex_lock(&native_mutex);
    uint_fast64_t old=atomic_load(&test_ptt[source]),next;
    do {
        next=(old&~(uint_fast64_t)1)|(pressed?1u:0u);
        if(!pressed && (old&1u))next+=2;
    } while(!atomic_compare_exchange_weak(&test_ptt[source],&old,next));
    pthread_mutex_unlock(&native_mutex);
}
/* Explicit simulation of a separate device lease; no ordinary command cache
 * is promoted to an applied acknowledgement. observe never renews the lease. */
static hw_native_voice_state_t native_state={.valid=true,.boot_id=1,
    .applied_route=HW_NATIVE_ROUTE_QUIET,.contract_id="host-native-v2",
    .physical_keying_inhibited=true,.autonomous_tx_inhibited=true,
    .direct_mic_rf_inhibited=true,.raw_rx_speaker_inhibited=true};
static uint64_t native_now(void) {
    struct timespec t;clock_gettime(CLOCK_MONOTONIC,&t);
    return (uint64_t)t.tv_sec*1000+t.tv_nsec/1000000;
}
static void native_expire_locked(uint64_t time) {
    if(native_state.lease_token && time>=native_state.lease_deadline_ms){
        native_state.lease_token=0;native_state.lease_deadline_ms=0;
        native_state.applied_route=HW_NATIVE_ROUTE_QUIET;
        reg_i[HW_MODEM]=reg_i[HW_PTT]=0;
        ++native_state.sequence;
    }
}
bool hw_native_voice_acquire(uint64_t token) {
    pthread_mutex_lock(&native_mutex);uint64_t time=native_now();native_expire_locked(time);
    bool ok=token && !native_state.lease_token;
    if(ok){native_state.lease_token=token;native_state.lease_deadline_ms=time+HW_NATIVE_LEASE_MAX_MS;
        native_state.route_generation=0;native_state.applied_route=HW_NATIVE_ROUTE_QUIET;
        native_state.physical_keying_inhibited=native_state.autonomous_tx_inhibited=true;
        native_state.direct_mic_rf_inhibited=native_state.raw_rx_speaker_inhibited=true;
        native_state.monotonic_ms=time;++native_state.sequence;
        reg_i[HW_MODEM]=reg_i[HW_PTT]=0;}
    pthread_mutex_unlock(&native_mutex);return ok;
}
bool hw_native_voice_apply(uint64_t token,uint64_t generation,hw_native_voice_route_t route) {
    pthread_mutex_lock(&native_mutex);uint64_t time=native_now();native_expire_locked(time);
    bool ok=token && token==native_state.lease_token && generation>native_state.route_generation &&
        route>=HW_NATIVE_ROUTE_QUIET && route<=HW_NATIVE_ROUTE_TX;
    if(ok){native_state.route_generation=generation;native_state.applied_route=route;
        native_state.monotonic_ms=time;++native_state.sequence;
        reg_i[HW_MODEM]=reg_i[HW_PTT]=route==HW_NATIVE_ROUTE_TX;}
    pthread_mutex_unlock(&native_mutex);return ok;
}
bool hw_native_voice_renew(uint64_t token) {
    pthread_mutex_lock(&native_mutex);uint64_t time=native_now();native_expire_locked(time);
    bool ok=token && token==native_state.lease_token;
    if(ok){native_state.lease_deadline_ms=time+HW_NATIVE_LEASE_MAX_MS;
        native_state.monotonic_ms=time;++native_state.sequence;}
    pthread_mutex_unlock(&native_mutex);return ok;
}
bool hw_native_voice_observe(hw_native_voice_state_t *out) {
    if(!out)return false;
    pthread_mutex_lock(&native_mutex);uint64_t time=native_now();native_expire_locked(time);
    /* This model samples its applied state anew; never extends the deadline. */
    native_state.monotonic_ms=time;++native_state.sequence;*out=native_state;
    pthread_mutex_unlock(&native_mutex);return true;
}
bool hw_native_voice_release(uint64_t token) {
    pthread_mutex_lock(&native_mutex);native_expire_locked(native_now());
    bool ok=token && token==native_state.lease_token && native_state.applied_route==HW_NATIVE_ROUTE_QUIET &&
        !(atomic_load(&test_ptt[0])&1u) && !(atomic_load(&test_ptt[1])&1u);
    if(ok){native_state.lease_token=0;native_state.lease_deadline_ms=0;++native_state.sequence;}
    pthread_mutex_unlock(&native_mutex);return ok;
}
uint32_t hw_independent_ptt_sources(void) { return 3; }
hw_native_voice_capabilities_t hw_native_voice_capabilities(void) {
    return (hw_native_voice_capabilities_t){.abi_version=HW_NATIVE_VOICE_ABI,
        .known=HW_NATIVE_ALL, .supported=HW_NATIVE_ALL, .simulation=true,
        .evidence="Explicit host simulation; not hardware evidence",
        .contract_id="host-native-v2"};
}
bool hw_ptt_observe(hw_ptt_source_t source, hw_ptt_observation_t *out) {
    if (!out || source<HW_PTT_SOURCE_RADIO || source > HW_PTT_SOURCE_HAND) return false;
    struct timespec t; clock_gettime(CLOCK_MONOTONIC,&t);
    uint_fast64_t ptt=atomic_load(&test_ptt[source]);
    *out=(hw_ptt_observation_t){true,(ptt&1u)!=0,source,
        atomic_fetch_add(&test_ptt_sequence,1)+1,(uint64_t)t.tv_sec*1000+t.tv_nsec/1000000,ptt>>1};
    return true;
}
#else
bool hw_native_voice_acquire(uint64_t token) { (void)token;return false; }
bool hw_native_voice_apply(uint64_t token,uint64_t generation,hw_native_voice_route_t route) {
    (void)token;(void)generation;(void)route;return false;
}
bool hw_native_voice_renew(uint64_t token) { (void)token;return false; }
bool hw_native_voice_observe(hw_native_voice_state_t *out) {
    if(out)*out=(hw_native_voice_state_t){0};return false;
}
bool hw_native_voice_release(uint64_t token) { (void)token;return false; }
uint32_t hw_independent_ptt_sources(void) { return 0; }
hw_native_voice_capabilities_t hw_native_voice_capabilities(void) {
    return (hw_native_voice_capabilities_t){.abi_version=HW_NATIVE_VOICE_ABI,
        .simulation=true, .evidence="Ordinary stub has no native voice capabilities"};
}
bool hw_ptt_observe(hw_ptt_source_t source, hw_ptt_observation_t *out) {
    if (out) *out=(hw_ptt_observation_t){.source=source};
    return false;
}
#endif

bool hw_vfo_mode_matches(radio_vfo_t selected,radio_mode_t mode) { return selected<=RADIO_VFO_B && vfo[selected].mode==mode; }
