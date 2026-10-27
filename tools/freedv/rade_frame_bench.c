/* SPDX-License-Identifier: BSD-2-Clause
 * RADE V1 speech-to-modem / modem-to-speech timing prototype.
 * The speech feature and FARGAN sequencing follows rade_c's rade_tx_wav.c and
 * rade_rx_wav.c, Copyright (C) 2024 David Rowe. See THIRD-PARTY-NOTICES.md.
 * No radio, physical audio device, network or background threads are used.
 */
#define _POSIX_C_SOURCE 200809L
#include <errno.h>
#include <inttypes.h>
#include <math.h>
#include <samplerate.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/resource.h>
#include <time.h>
#include "rade_api.h"
#include "rade_dsp.h"
#include "fargan.h"
#include "lpcnet.h"
#include "cpu_support.h"

static void die(const char *message) { fprintf(stderr, "%s\n", message); exit(1); }
static uint64_t now_ns(void) {
    struct timespec t;
    if (clock_gettime(CLOCK_MONOTONIC, &t)) die("clock_gettime failed");
    return (uint64_t)t.tv_sec * UINT64_C(1000000000) + (uint64_t)t.tv_nsec;
}
static void wait_until(uint64_t ns) {
    struct timespec t = {(time_t)(ns / UINT64_C(1000000000)), (long)(ns % UINT64_C(1000000000))};
    int error;
    do { error = clock_nanosleep(CLOCK_MONOTONIC, TIMER_ABSTIME, &t, NULL); } while (error == EINTR);
    if (error) die("clock_nanosleep failed");
}
static int cmp_double(const void *a, const void *b) {
    double x = *(const double *)a, y = *(const double *)b;
    return (x > y) - (x < y);
}
static long number(const char *text, long low, long high) {
    char *end;
    errno = 0;
    long n = strtol(text, &end, 10);
    if (errno || end == text || *end || n < low || n > high) die("invalid numeric argument");
    return n;
}
static float *load_fixture(const char *path, int rate, size_t *count_out) {
    FILE *f = fopen(path, "rb");
    if (!f) die("cannot open input fixture");
    if (fseek(f, 0, SEEK_END)) die("cannot seek input fixture");
    long bytes = ftell(f);
    if (bytes < 2 || bytes > 32 * 1024 * 1024 || bytes % 2 || fseek(f, 0, SEEK_SET))
        die("fixture must be nonempty signed 16-bit mono PCM, at most 32 MiB");
    size_t n = (size_t)bytes / sizeof(short);
    short *pcm = malloc((size_t)bytes);
    float *source = malloc(n * sizeof(*source));
    size_t capacity = (size_t)ceil((double)n * 44100.0 / rate) + 512;
    float *converted = calloc(capacity, sizeof(*converted));
    if (!pcm || !source || !converted || fread(pcm, sizeof(*pcm), n, f) != n) die("fixture read/allocation failed");
    fclose(f);
    src_short_to_float_array(pcm, source, (int)n);
    SRC_DATA d = {.data_in = source, .input_frames = (long)n, .data_out = converted,
                  .output_frames = (long)capacity, .src_ratio = 44100.0 / rate, .end_of_input = 1};
    int error = src_simple(&d, SRC_SINC_FASTEST, 1);
    if (error || d.input_frames_used != (long)n || d.output_frames_gen <= 0) die("fixture conversion failed");
    free(pcm); free(source);
    *count_out = (size_t)d.output_frames_gen;
    return converted;
}
/* Convert repeated fixture chunks to the native rate without discontinuing the
 * resampler state. Cursors advance by input_frames_used, not requested frames. */
static void input_samples(SRC_STATE *state, const float *fixture, size_t count,
                          size_t *cursor, float *out, size_t wanted, int rate) {
    float chunk[441];
    size_t produced = 0;
    while (produced < wanted) {
        for (size_t i = 0; i < 441; ++i) chunk[i] = fixture[(*cursor + i) % count];
        SRC_DATA d = {.data_in = chunk, .input_frames = 441, .data_out = out + produced,
                      .output_frames = (long)(wanted - produced), .src_ratio = rate / 44100.0};
        int error = src_process(state, &d);
        if (error || (!d.input_frames_used && !d.output_frames_gen)) die("input resampling failed");
        *cursor = (*cursor + (size_t)d.input_frames_used) % count;
        produced += (size_t)d.output_frames_gen;
    }
}
static size_t output_samples(SRC_STATE *state, const float *in, size_t count, int rate,
                             float *out, short *pcm, size_t capacity, uint64_t *nonzero) {
    size_t used = 0, generated = 0;
    while (used < count) {
        SRC_DATA d = {.data_in = in + used, .input_frames = (long)(count - used),
                      .data_out = out, .output_frames = (long)capacity, .src_ratio = 44100.0 / rate};
        int error = src_process(state, &d);
        if (error || (!d.input_frames_used && !d.output_frames_gen)) die("output resampling failed");
        used += (size_t)d.input_frames_used;
        src_float_to_short_array(out, pcm, (int)d.output_frames_gen);
        for (long i = 0; i < d.output_frames_gen; ++i) *nonzero += pcm[i] != 0;
        generated += (size_t)d.output_frames_gen;
    }
    return generated;
}
int main(int argc, char **argv) {
    if (argc != 7 || (strcmp(argv[1], "tx") && strcmp(argv[1], "rx")) ||
        (strcmp(argv[4], "paced") && strcmp(argv[4], "unpaced")) ||
        (strcmp(argv[6], "continuous") && strcmp(argv[6], "interrupt"))) {
        fprintf(stderr, "usage: %s tx|rx fixture-s16le.raw seconds paced|unpaced warmup-seconds continuous|interrupt\n", argv[0]);
        return 2;
    }
    bool tx = !strcmp(argv[1], "tx"), paced = !strcmp(argv[4], "paced");
    bool interrupt = !strcmp(argv[6], "interrupt");
    if (tx && interrupt) die("interrupt workload is only defined for RX");
    long seconds = number(argv[3], 1, 1800), warmup_seconds = number(argv[5], 0, 60);
    int in_rate = tx ? RADE_SPEECH_SAMPLE_RATE : RADE_MODEM_SAMPLE_RATE;
    int out_rate = tx ? RADE_MODEM_SAMPLE_RATE : RADE_SPEECH_SAMPLE_RATE;
    size_t fixture_count = 0, cursor = 0;
    float *fixture = load_fixture(argv[2], in_rate, &fixture_count);
    int error;
    SRC_STATE *input_src = src_new(SRC_SINC_FASTEST, 1, &error);
    SRC_STATE *output_src = src_new(SRC_SINC_FASTEST, 1, &error);
    if (!input_src || !output_src) die("resampler initialization failed");
    uint64_t init_start = now_ns();
    rade_initialize();
    char unused[] = "built-in";
    struct rade *r = rade_open(unused, RADE_VERBOSE_0);
    LPCNetEncState *encoder = tx ? lpcnet_encoder_create() : NULL;
    FARGANState *fargan = tx ? NULL : calloc(1, sizeof(*fargan));
    if (!r || (tx && !encoder) || (!tx && !fargan)) die("RADE or speech initialization failed");
    int arch = opus_select_arch();
    if (fargan) fargan_init(fargan);
    uint64_t init_ns = now_ns() - init_start;
    int feature_n = rade_n_features_in_out(r), nin_max = rade_nin_max(r), tx_n = rade_n_tx_out(r);
    int eoo_n = rade_n_eoo_bits(r);
    if (feature_n <= 0 || feature_n % RADE_NB_TOTAL_FEATURES || nin_max <= 0 || tx_n <= 0 || eoo_n < 0 ||
        NB_TOTAL_FEATURES != RADE_NB_TOTAL_FEATURES) die("unexpected RADE frame sizes");
    int speech_n = feature_n / RADE_NB_TOTAL_FEATURES * LPCNET_FRAME_SIZE;
    size_t max_n = (size_t)(speech_n > nin_max ? speech_n : nin_max);
    if ((size_t)tx_n > max_n) max_n = (size_t)tx_n;
    size_t converted_cap = max_n * 7 + 512;
    float *input = calloc(max_n, sizeof(*input)), *features = calloc((size_t)feature_n, sizeof(*features));
    float *eoo = calloc((size_t)(eoo_n ? eoo_n : 1), sizeof(*eoo));
    float *native_out = calloc(max_n, sizeof(*native_out)), *converted = calloc(converted_cap, sizeof(*converted));
    short *pcm_in = calloc(max_n, sizeof(*pcm_in)), *pcm_out = calloc(converted_cap, sizeof(*pcm_out));
    RADE_COMP *iq = calloc(max_n, sizeof(*iq));
    /* RADE V1's shortest input is RADE_NMF - RADE_M samples; validate it on
     * every iteration so the preallocated statistics array cannot overflow. */
    size_t min_input = tx ? (size_t)speech_n : (size_t)(RADE_NMF - RADE_M);
    size_t stat_cap = (size_t)seconds * (size_t)in_rate / min_input + 2;
    double *ratios = calloc(stat_cap, sizeof(*ratios)), *milliseconds = calloc(stat_cap, sizeof(*milliseconds));
    if (!input || !features || !eoo || !native_out || !converted || !pcm_in || !pcm_out || !iq || !ratios || !milliseconds)
        die("buffer allocation failed");
    float cont[5 * NB_TOTAL_FEATURES] = {0};
    int cont_frames = 0;
    bool fargan_ready = false;
    uint64_t warm_ns = 0, audio_ns = 0, compute_ns = 0, deadline = now_ns();
    uint64_t misses = 0, out_native = 0, out_converted = 0, sync_blocks = 0, feature_blocks = 0, nonzero = 0, muted = 0;
    uint64_t sync_after_interrupt = 0;
    uint64_t stream_samples = 0;
    size_t frames = 0;
    while (audio_ns < (uint64_t)seconds * UINT64_C(1000000000)) {
        int n = tx ? speech_n : rade_nin(r);
        if (n < (int)min_input || (size_t)n > max_n) die("unexpected RADE input size");
        uint64_t interval = (uint64_t)n * UINT64_C(1000000000) / (uint64_t)in_rate;
        bool warming = warm_ns < (uint64_t)warmup_seconds * UINT64_C(1000000000);
        if (!warming && frames == 0) deadline = now_ns();
        uint64_t start = now_ns();
        uint64_t block_muted = 0, block_nonzero = 0;
        input_samples(input_src, fixture, fixture_count, &cursor, input, (size_t)n, in_rate);
        if (interrupt) {
            /* Two seconds of silence every twelve seconds, including block edges. */
            for (int i = 0; i < n; ++i)
                if (((stream_samples + (uint64_t)i) / (uint64_t)in_rate) % 12 >= 10) { input[i] = 0; ++block_muted; }
        }
        stream_samples += (uint64_t)n;
        int produced = 0, synced = 0, valid = 0;
        if (tx) {
            src_float_to_short_array(input, pcm_in, n);
            int feature_frames = feature_n / RADE_NB_TOTAL_FEATURES;
            for (int j = 0; j < feature_frames; ++j)
                lpcnet_compute_single_frame_features(encoder, pcm_in + j * LPCNET_FRAME_SIZE,
                    features + j * RADE_NB_TOTAL_FEATURES, arch);
            produced = rade_tx(r, iq, features);
            if (produced < 0 || (size_t)produced > max_n) die("invalid RADE TX output");
            for (int i = 0; i < produced; ++i) {
                float value = iq[i].real * (RADE_INT16_SCALE / 32768.0f);
                native_out[i] = fmaxf(-1.0f, fminf(32767.0f / 32768.0f, value));
            }
        } else {
            for (int i = 0; i < n; ++i) {
                iq[i].real = input[i] * (32768.0f * 2.0f / RADE_INT16_SCALE);
                iq[i].imag = 0;
            }
            int has_eoo = 0;
            int n_features = rade_rx(r, features, &has_eoo, eoo, iq);
            if (n_features < 0 || n_features > feature_n || n_features % RADE_NB_TOTAL_FEATURES) die("invalid RADE RX output");
            synced = rade_sync(r) != 0;
            valid = n_features > 0;
            for (int j = 0; j < n_features / RADE_NB_TOTAL_FEATURES; ++j) {
                const float *feat = features + j * RADE_NB_TOTAL_FEATURES;
                if (!fargan_ready) {
                    memcpy(cont + cont_frames * NB_TOTAL_FEATURES, feat, NB_TOTAL_FEATURES * sizeof(float));
                    if (++cont_frames == 5) {
                        float packed[5 * NB_FEATURES], zero[FARGAN_CONT_SAMPLES] = {0};
                        for (int k = 0; k < 5; ++k)
                            memcpy(packed + k * NB_FEATURES, cont + k * NB_TOTAL_FEATURES, NB_FEATURES * sizeof(float));
                        fargan_cont(fargan, zero, packed);
                        fargan_ready = true;
                    }
                    continue;
                }
                if ((size_t)produced + LPCNET_FRAME_SIZE > max_n) die("speech output buffer too small");
                fargan_synthesize(fargan, native_out + produced, feat);
                produced += LPCNET_FRAME_SIZE;
            }
        }
        size_t out_n = produced ? output_samples(output_src, native_out, (size_t)produced, out_rate,
                                                  converted, pcm_out, converted_cap, &block_nonzero) : 0;
        uint64_t elapsed = now_ns() - start;
        if (warming) warm_ns += interval;
        else {
            if (frames >= stat_cap) die("statistics buffer exhausted");
            ratios[frames] = (double)elapsed / (double)interval;
            milliseconds[frames++] = (double)elapsed / 1e6;
            audio_ns += interval; compute_ns += elapsed;
            out_native += (uint64_t)produced; out_converted += (uint64_t)out_n;
            sync_blocks += (uint64_t)synced; feature_blocks += (uint64_t)valid;
            nonzero += block_nonzero; muted += block_muted;
            if (interrupt && synced && stream_samples - (uint64_t)n >= (uint64_t)in_rate * 12) ++sync_after_interrupt;
        }
        deadline += interval;
        if (paced) {
            if (now_ns() > deadline) { if (!warming) ++misses; }
            else wait_until(deadline);
        }
    }
    qsort(ratios, frames, sizeof(*ratios), cmp_double);
    qsort(milliseconds, frames, sizeof(*milliseconds), cmp_double);
    size_t p99 = (frames * 99 + 99) / 100 - 1;
    struct rusage usage;
    if (getrusage(RUSAGE_SELF, &usage)) die("getrusage failed");
    char misses_json[32];
    if (paced) snprintf(misses_json, sizeof(misses_json), "%" PRIu64, misses);
    else strcpy(misses_json, "null");
    printf("{\"schema_version\":1,\"mode\":\"RADE-V1\",\"direction\":\"%s\",\"pacing\":\"%s\","
           "\"workload\":\"%s\",\"frames\":%zu,\"audio_seconds\":%.6f,\"warmup_audio_seconds\":%.6f,"
           "\"initialization_ms\":%.3f,\"processing_realtime_factor\":%.6f,\"p99_frame_ratio\":%.6f,"
           "\"max_frame_ratio\":%.6f,\"p99_block_ms\":%.3f,\"max_block_ms\":%.3f,"
           "\"deadline_misses\":%s,\"output_samples\":%" PRIu64 ",\"resampled_output_samples\":%" PRIu64 ","
           "\"sync_blocks\":%" PRIu64 ",\"feature_blocks\":%" PRIu64 ",\"nonzero_output_samples\":%" PRIu64 ","
           "\"interrupted_input_samples\":%" PRIu64 ","
           "\"sync_blocks_after_first_interrupt\":%" PRIu64 ","
           "\"peak_rss_kib_linux\":%ld,\"opus_arch\":%d,\"resampling_included\":true,\"radio_io\":false,\"full_pipeline_qualified\":false}\n",
           argv[1], argv[4], argv[6], frames, (double)audio_ns / 1e9, (double)warm_ns / 1e9,
           (double)init_ns / 1e6, (double)compute_ns / (double)audio_ns, ratios[p99], ratios[frames-1],
           milliseconds[p99], milliseconds[frames-1], misses_json, out_native, out_converted,
           sync_blocks, feature_blocks, nonzero, muted, sync_after_interrupt, usage.ru_maxrss, arch);
    if (encoder) lpcnet_encoder_destroy(encoder);
    free(fargan); rade_close(r); rade_finalize(); src_delete(input_src); src_delete(output_src);
    free(fixture); free(input); free(features); free(eoo); free(native_out); free(converted); free(pcm_in); free(pcm_out); free(iq);
    free(ratios); free(milliseconds);
    return 0;
}
