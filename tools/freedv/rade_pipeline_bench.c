/* SPDX-License-Identifier: BSD-2-Clause
 * RADE V1 speech-to-modem / modem-to-speech timing prototype.
 * The speech feature and FARGAN sequencing follows rade_c's rade_tx_wav.c and
 * rade_rx_wav.c, Copyright (C) 2024 David Rowe. See THIRD-PARTY-NOTICES.md.
 * Experimental offline RX pipeline; no radio or physical audio devices.
 */
#define _POSIX_C_SOURCE 200809L
#include <errno.h>
#include <pthread.h>
#include <signal.h>
#include <stdatomic.h>
#include <unistd.h>
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
/* Separate experimental executable: serial is the default comparison model;
 * two-worker execution is selected explicitly by the first argument. Hashing is
 * deliberately included in service timing. These FNV hashes detect regression;
 * they are not cryptographic attestations or a proof of arbitrary-input equality. */
#define FEATURE_CAP (RADE_NZMF * RADE_FRAMES_PER_STEP * RADE_NB_TOTAL_FEATURES)
#define QUEUE_CAP 2
#define HASH_INIT UINT64_C(14695981039346656037)
_Static_assert(ATOMIC_INT_LOCK_FREE == 2, "signal flag requires lock-free atomic int");
static atomic_int interrupted = ATOMIC_VAR_INIT(0);
static void on_signal(int signo) {
    (void)signo;
    atomic_store_explicit(&interrupted, 1, memory_order_relaxed);
}
static uint64_t cpu_ns(void) {
    struct timespec t;
    if (clock_gettime(CLOCK_THREAD_CPUTIME_ID, &t)) die("thread CPU clock failed");
    return (uint64_t)t.tv_sec * UINT64_C(1000000000) + (uint64_t)t.tv_nsec;
}
static uint64_t hash_bytes(uint64_t h, const void *data, size_t bytes) {
    const unsigned char *p = data;
    for (size_t i = 0; i < bytes; ++i) h = (h ^ p[i]) * UINT64_C(1099511628211);
    return h;
}
static uint64_t hash_word(uint64_t h, uint64_t value) {
    unsigned char b[8];
    for (int i = 0; i < 8; ++i) b[i] = (unsigned char)(value >> (i * 8));
    return hash_bytes(h, b, sizeof b);
}
struct record {
    uint64_t sequence, sample_start, interval_ns, release_ns, a_start_ns, a_end_ns;
    uint64_t a_cpu_ns, publish_ns, b_start_ns, b_end_ns, b_cpu_ns;
    uint64_t input_ns, rade_ns, speech_ns, output_ns, producer_wait_ns;
    uint64_t muted, nonzero, native_count, converted_count;
    uint64_t feature_hash, native_hash, pcm_hash, eoo_hash;
    size_t stat_index;
    int nin, nfeatures, synced, has_eoo;
    bool warming;
    float *features;
};
struct counters {
    uint64_t frames, audio_ns, native_count, converted_count, sync_blocks;
    uint64_t feature_blocks, nonzero, muted, sync_after_interrupt, eoo_blocks;
    uint64_t feature_hash, native_hash, pcm_hash, metadata_hash;
};
struct queue {
    pthread_mutex_t mutex;
    pthread_cond_t changed;
    struct record slots[QUEUE_CAP];
    float features[QUEUE_CAP][FEATURE_CAP];
    unsigned head, count, max_depth;
    uint64_t full_events, producer_wait_ns, consumer_wait_ns, dropped, abandoned;
    bool done, failed, hold_b;
    const char *failure;
};
struct context {
    bool parallel, paced, interrupt;
    int fail_b_after, delay_a_ms;
    size_t max_n, converted_cap, stat_cap, fixture_count, cursor;
    int feature_n, eoo_n;
    struct rade *rade;
    SRC_STATE *input_src, *output_src;
    FARGANState fargan;
    float *fixture, *input, *eoo, *native_out, *converted;
    short *pcm_out;
    RADE_COMP *iq;
    float cont[5 * NB_TOTAL_FEATURES];
    int cont_frames;
    bool fargan_ready;
    struct queue q;
    struct counters full, measured;
    struct record *stats;
    uint64_t first_measured_start, last_measured_finish, final_finish;
    uint64_t attempts, processed, submitted, completed, ingress_late_max, ingress_deadline_misses;
    uint64_t maximum_virtual_input_backlog_ns;
};
static void init_counters(struct counters *c) {
    memset(c, 0, sizeof *c);
    c->feature_hash = c->native_hash = c->pcm_hash = c->metadata_hash = HASH_INIT;
}
static void account(struct counters *c, const struct record *r, bool interrupt) {
    ++c->frames; c->audio_ns += r->interval_ns;
    c->native_count += r->native_count; c->converted_count += r->converted_count;
    c->sync_blocks += r->synced != 0; c->feature_blocks += r->nfeatures != 0;
    c->nonzero += r->nonzero; c->muted += r->muted; c->eoo_blocks += r->has_eoo != 0;
    if (interrupt && r->synced && r->sample_start >= RADE_MODEM_SAMPLE_RATE * 12) ++c->sync_after_interrupt;
    c->feature_hash = hash_word(hash_word(c->feature_hash, (uint64_t)r->nfeatures), r->feature_hash);
    c->native_hash = hash_word(hash_word(c->native_hash, r->native_count), r->native_hash);
    c->pcm_hash = hash_word(hash_word(c->pcm_hash, r->converted_count), r->pcm_hash);
    const uint64_t words[] = {r->sequence, r->sample_start, (uint64_t)r->nin,
        (uint64_t)r->nfeatures, (uint64_t)r->synced, (uint64_t)r->has_eoo,
        r->eoo_hash, r->warming, r->muted};
    for (size_t i = 0; i < sizeof words / sizeof words[0]; ++i)
        c->metadata_hash = hash_word(c->metadata_hash, words[i]);
}
static void set_failure_locked(struct queue *q, const char *reason) {
    q->failed = true;
    if (!q->failure) q->failure = reason;
    pthread_cond_broadcast(&q->changed);
}
static bool queue_failed(struct context *c) {
    pthread_mutex_lock(&c->q.mutex);
    if (atomic_load_explicit(&interrupted, memory_order_relaxed)) set_failure_locked(&c->q, "interrupted");
    bool value = c->q.failed;
    pthread_mutex_unlock(&c->q.mutex);
    return value;
}
static void cond_wait(struct queue *q) {
    uint64_t target = now_ns() + UINT64_C(100000000);
    struct timespec t = {(time_t)(target / UINT64_C(1000000000)), (long)(target % UINT64_C(1000000000))};
    int rc = pthread_cond_timedwait(&q->changed, &q->mutex, &t);
    if (rc && rc != ETIMEDOUT) set_failure_locked(q, "condition_wait_failed");
    if (atomic_load_explicit(&interrupted, memory_order_relaxed)) set_failure_locked(q, "interrupted");
}
/* Worker A exclusively owns input resampling, fixture cursor, IQ conversion,
 * rade_nin/rade_rx/rade_sync and all receiver/random state. */
static void run_a(struct context *c, struct record *r) {
    uint64_t cpu = cpu_ns();
    if (c->delay_a_ms) wait_until(now_ns() + (uint64_t)c->delay_a_ms * UINT64_C(1000000));
    uint64_t t = now_ns();
    input_samples(c->input_src, c->fixture, c->fixture_count, &c->cursor,
                  c->input, (size_t)r->nin, RADE_MODEM_SAMPLE_RATE);
    r->input_ns = now_ns() - t;
    if (c->interrupt) {
        for (int i = 0; i < r->nin; ++i)
            if (((r->sample_start + (uint64_t)i) / RADE_MODEM_SAMPLE_RATE) % 12 >= 10) {
                c->input[i] = 0; ++r->muted;
            }
    }
    for (int i = 0; i < r->nin; ++i) {
        c->iq[i].real = c->input[i] * (32768.0f * 2.0f / RADE_INT16_SCALE);
        c->iq[i].imag = 0;
    }
    t = now_ns();
    r->nfeatures = rade_rx(c->rade, r->features, &r->has_eoo, c->eoo, c->iq);
    r->rade_ns = now_ns() - t;
    if (r->nfeatures < 0 || r->nfeatures > c->feature_n || r->nfeatures % NB_TOTAL_FEATURES)
        die("invalid RADE RX output");
    r->synced = rade_sync(c->rade) != 0;
    r->feature_hash = hash_bytes(HASH_INIT, r->features, (size_t)r->nfeatures * sizeof(float));
    r->eoo_hash = hash_bytes(HASH_INIT, c->eoo, r->has_eoo ? (size_t)c->eoo_n * sizeof(float) : 0);
    r->a_cpu_ns = cpu_ns() - cpu;
}
/* Worker B exclusively owns continuation, vocoder, output SRC and all output
 * counters/statistics. No state is reset at warm-up or reacquisition. */
static void run_b(struct context *c, struct record *r) {
    uint64_t cpu = cpu_ns(), t = now_ns();
    size_t produced = 0;
    if (r->nfeatures) {
        for (int j = 0; j < r->nfeatures / NB_TOTAL_FEATURES; ++j) {
            const float *feat = r->features + j * NB_TOTAL_FEATURES;
            if (!c->fargan_ready) {
                memcpy(c->cont + c->cont_frames * NB_TOTAL_FEATURES, feat, NB_TOTAL_FEATURES * sizeof(float));
                if (++c->cont_frames == 5) {
                    float packed[5 * NB_FEATURES], zero[FARGAN_CONT_SAMPLES] = {0};
                    for (int k = 0; k < 5; ++k)
                        memcpy(packed + k * NB_FEATURES, c->cont + k * NB_TOTAL_FEATURES, NB_FEATURES * sizeof(float));
                    fargan_cont(&c->fargan, zero, packed);
                    c->fargan_ready = true;
                }
                continue;
            }
            if (produced + LPCNET_FRAME_SIZE > c->max_n) die("speech output buffer too small");
            fargan_synthesize(&c->fargan, c->native_out + produced, feat);
            produced += LPCNET_FRAME_SIZE;
        }
        r->speech_ns = now_ns() - t;
    }
    r->native_count = produced;
    r->native_hash = hash_bytes(HASH_INIT, c->native_out, produced * sizeof(float));
    r->pcm_hash = HASH_INIT;
    if (produced) {
        t = now_ns();
        size_t used = 0;
        while (used < produced) {
            SRC_DATA d = {.data_in = c->native_out + used, .input_frames = (long)(produced - used),
                .data_out = c->converted, .output_frames = (long)c->converted_cap,
                .src_ratio = 44100.0 / RADE_SPEECH_SAMPLE_RATE};
            int error = src_process(c->output_src, &d);
            if (error || (!d.input_frames_used && !d.output_frames_gen)) die("output resampling failed");
            used += (size_t)d.input_frames_used;
            src_float_to_short_array(c->converted, c->pcm_out, (int)d.output_frames_gen);
            for (long i = 0; i < d.output_frames_gen; ++i) r->nonzero += c->pcm_out[i] != 0;
            r->pcm_hash = hash_bytes(r->pcm_hash, c->pcm_out, (size_t)d.output_frames_gen * sizeof(short));
            r->converted_count += (uint64_t)d.output_frames_gen;
        }
        r->output_ns = now_ns() - t;
    }
    r->b_cpu_ns = cpu_ns() - cpu;
    r->b_end_ns = now_ns();
    ++c->completed;
    account(&c->full, r, c->interrupt);
    if (!r->warming) {
        if (r->stat_index >= c->stat_cap) die("statistics capacity exceeded");
        c->stats[r->stat_index] = *r;
        c->stats[r->stat_index].features = NULL;
        account(&c->measured, r, c->interrupt);
        c->last_measured_finish = r->b_end_ns;
    }
    c->final_finish = r->b_end_ns;
}
static bool publish(struct context *c, struct record *r) {
    struct queue *q = &c->q;
    uint64_t wait = 0;
    pthread_mutex_lock(&q->mutex);
    while (q->count == QUEUE_CAP && !q->failed) {
        ++q->full_events;
        if (c->paced || q->hold_b) {
            set_failure_locked(q, "queue_full");
            break;
        }
        uint64_t start = now_ns();
        cond_wait(q);
        wait += now_ns() - start;
    }
    q->producer_wait_ns += wait;
    if (q->failed) {
        ++q->dropped;
        pthread_mutex_unlock(&q->mutex);
        return false;
    }
    unsigned slot_index = (q->head + q->count) % QUEUE_CAP;
    struct record *slot = &q->slots[slot_index];
    *slot = *r;
    memcpy(q->features[slot_index], r->features, (size_t)r->nfeatures * sizeof(float));
    slot->features = q->features[slot_index];
    slot->producer_wait_ns = wait;
    slot->a_end_ns = slot->publish_ns = now_ns();
    ++q->count;
    if (q->count > q->max_depth) q->max_depth = q->count;
    ++c->submitted;
    pthread_cond_broadcast(&q->changed);
    pthread_mutex_unlock(&q->mutex);
    return true;
}
static void *worker_b(void *argument) {
    struct context *c = argument;
    struct queue *q = &c->q;
    uint64_t consumed = 0;
    float private_features[FEATURE_CAP];
    for (;;) {
        struct record r;
        pthread_mutex_lock(&q->mutex);
        uint64_t waiting = now_ns();
        while (((q->hold_b && !q->failed) || !q->count) && !q->done && !q->failed)
            cond_wait(q);
        q->consumer_wait_ns += now_ns() - waiting;
        if (!q->count && (q->done || q->failed)) { pthread_mutex_unlock(&q->mutex); break; }
        if (c->fail_b_after >= 0 && consumed >= (uint64_t)c->fail_b_after) {
            q->abandoned += q->count;
            q->count = 0;
            set_failure_locked(q, "injected_worker_b_failure");
            pthread_mutex_unlock(&q->mutex); break;
        }
        uint64_t begin = now_ns();
        r = q->slots[q->head];
        memcpy(private_features, r.features, (size_t)r.nfeatures * sizeof(float));
        r.features = private_features;
        r.b_start_ns = begin;
        q->head = (q->head + 1) % QUEUE_CAP;
        --q->count;
        pthread_cond_broadcast(&q->changed);
        pthread_mutex_unlock(&q->mutex);
        ++consumed;
        run_b(c, &r);
    }
    return NULL;
}
static void print_counters(const struct counters *c) {
    printf("{\"frames\":%" PRIu64 ",\"audio_ns\":%" PRIu64 ",\"output_samples\":%" PRIu64
           ",\"resampled_output_samples\":%" PRIu64 ",\"sync_blocks\":%" PRIu64
           ",\"feature_blocks\":%" PRIu64 ",\"nonzero_output_samples\":%" PRIu64
           ",\"interrupted_input_samples\":%" PRIu64 ",\"sync_blocks_after_first_interrupt\":%" PRIu64
           ",\"eoo_blocks\":%" PRIu64 ",\"feature_hash\":\"%016" PRIx64
           "\",\"native_float_hash\":\"%016" PRIx64 "\",\"pcm_s16_hash\":\"%016" PRIx64
           "\",\"metadata_hash\":\"%016" PRIx64 "\"}",
           c->frames, c->audio_ns, c->native_count, c->converted_count, c->sync_blocks,
           c->feature_blocks, c->nonzero, c->muted, c->sync_after_interrupt, c->eoo_blocks,
           c->feature_hash, c->native_hash, c->pcm_hash, c->metadata_hash);
}
static void distribution(const char *name, double *values, size_t count) {
    printf("\"%s\":{\"count\":%zu", name, count);
    if (count) {
        double sum = 0;
        for (size_t i = 0; i < count; ++i) sum += values[i];
        qsort(values, count, sizeof(*values), cmp_double);
        size_t p99 = (count * 99 + 99) / 100 - 1;
        printf(",\"mean\":%.9f,\"p99\":%.9f,\"max\":%.9f", sum / count, values[p99], values[count - 1]);
    } else printf(",\"mean\":null,\"p99\":null,\"max\":null");
    printf("}");
}
int main(int argc, char **argv) {
    if (argc < 7 || (strcmp(argv[1], "serial") && strcmp(argv[1], "parallel")) ||
        (strcmp(argv[4], "paced") && strcmp(argv[4], "unpaced")) ||
        (strcmp(argv[6], "continuous") && strcmp(argv[6], "interrupt"))) {
        fprintf(stderr, "usage: %s serial|parallel fixture.raw seconds paced|unpaced warmup-seconds continuous|interrupt [--test-hold-b-until-full] [--test-fail-b-after N] [--test-delay-a-ms N]\n", argv[0]);
        return 2;
    }
    struct context c = {.parallel = !strcmp(argv[1], "parallel"), .paced = !strcmp(argv[4], "paced"),
        .interrupt = !strcmp(argv[6], "interrupt"), .fail_b_after = -1};
    for (int arg = 7; arg < argc; ++arg) {
        if (!strcmp(argv[arg], "--test-hold-b-until-full")) c.q.hold_b = true;
        else if (!strcmp(argv[arg], "--test-fail-b-after") && arg + 1 < argc)
            c.fail_b_after = (int)number(argv[++arg], 0, 1000);
        else if (!strcmp(argv[arg], "--test-delay-a-ms") && arg + 1 < argc)
            c.delay_a_ms = (int)number(argv[++arg], 0, 1000);
        else die("unknown argument");
    }
    if (!c.parallel && (c.q.hold_b || c.fail_b_after >= 0)) die("failure hooks require parallel mode");
    long seconds = number(argv[3], 1, 1800), warmup = number(argv[5], 0, 60);
    signal(SIGINT, on_signal); signal(SIGTERM, on_signal);
    init_counters(&c.full); init_counters(&c.measured);
    pthread_condattr_t attr;
    if (pthread_mutex_init(&c.q.mutex, NULL) || pthread_condattr_init(&attr) ||
        pthread_condattr_setclock(&attr, CLOCK_MONOTONIC) || pthread_cond_init(&c.q.changed, &attr))
        die("queue initialization failed");
    pthread_condattr_destroy(&attr);
    uint64_t t = now_ns();
    c.fixture = load_fixture(argv[2], RADE_MODEM_SAMPLE_RATE, &c.fixture_count);
    uint64_t fixture_ns = now_ns() - t;
    int error;
    c.input_src = src_new(SRC_SINC_FASTEST, 1, &error);
    c.output_src = src_new(SRC_SINC_FASTEST, 1, &error);
    if (!c.input_src || !c.output_src) die("resampler initialization failed");
    t = now_ns();
    rade_initialize(); char model[] = "built-in";
    c.rade = rade_open(model, RADE_VERBOSE_0);
    if (!c.rade) die("rade_open failed");
    fargan_init(&c.fargan);
    uint64_t initialization_ns = now_ns() - t;
    c.feature_n = rade_n_features_in_out(c.rade);
    c.eoo_n = rade_n_eoo_bits(c.rade);
    int speech_n = c.feature_n / NB_TOTAL_FEATURES * LPCNET_FRAME_SIZE;
    int nin_max = rade_nin_max(c.rade);
    if (c.feature_n != FEATURE_CAP || NB_TOTAL_FEATURES != RADE_NB_TOTAL_FEATURES ||
        nin_max <= 0 || c.eoo_n < 0) die("unexpected RADE sizes");
    c.max_n = (size_t)(speech_n > nin_max ? speech_n : nin_max);
    c.converted_cap = c.max_n * 7 + 512;
    c.stat_cap = (size_t)seconds * RADE_MODEM_SAMPLE_RATE / (RADE_NMF - RADE_M) + 2;
    c.input = calloc(c.max_n, sizeof(float)); c.eoo = calloc((size_t)(c.eoo_n ? c.eoo_n : 1), sizeof(float));
    c.native_out = calloc(c.max_n, sizeof(float)); c.converted = calloc(c.converted_cap, sizeof(float));
    c.pcm_out = calloc(c.converted_cap, sizeof(short)); c.iq = calloc(c.max_n, sizeof(RADE_COMP));
    c.stats = calloc(c.stat_cap, sizeof(struct record));
    double *values = calloc(c.stat_cap, sizeof(double));
    if (!c.input || !c.eoo || !c.native_out || !c.converted || !c.pcm_out || !c.iq || !c.stats || !values)
        die("preallocation failed");
    pthread_t thread;
    if (c.parallel && pthread_create(&thread, NULL, worker_b, &c)) die("worker creation failed");
    uint64_t epoch = now_ns(), release = epoch, warm_ns = 0, audio_ns = 0, sample_start = 0;
    size_t index = 0;
    float features[FEATURE_CAP];
    while (audio_ns < (uint64_t)seconds * UINT64_C(1000000000) && !queue_failed(&c)) {
        struct record r = {.features = features};
        r.nin = rade_nin(c.rade);
        if (r.nin < RADE_NMF - RADE_M || r.nin > nin_max) die("invalid RADE input size");
        r.interval_ns = (uint64_t)r.nin * UINT64_C(1000000000) / RADE_MODEM_SAMPLE_RATE;
        r.warming = warm_ns < (uint64_t)warmup * UINT64_C(1000000000);
        r.sequence = c.attempts++; r.sample_start = sample_start; r.stat_index = index;
        /* The last input sample is available at this fixed virtual release.
         * Consumer/producer delay never resets or slows this source clock. */
        release += r.interval_ns; r.release_ns = release;
        if (c.paced && now_ns() < release) wait_until(release);
        if (queue_failed(&c)) break;
        r.a_start_ns = now_ns();
        if (!r.warming && !c.first_measured_start) c.first_measured_start = r.a_start_ns;
        if (c.paced) {
            uint64_t late = r.a_start_ns > release ? r.a_start_ns - release : 0;
            if (!r.warming && late > c.ingress_late_max) c.ingress_late_max = late;
            if (!r.warming && late > r.interval_ns) ++c.ingress_deadline_misses;
            if (late > c.maximum_virtual_input_backlog_ns) c.maximum_virtual_input_backlog_ns = late;
        }
        run_a(&c, &r);
        ++c.processed;
        if (c.parallel) {
            if (!publish(&c, &r)) break;
        } else {
            r.a_end_ns = r.publish_ns = now_ns();
            r.b_start_ns = now_ns();
            ++c.submitted;
            run_b(&c, &r);
        }
        sample_start += (uint64_t)r.nin;
        if (r.warming) warm_ns += r.interval_ns;
        else { audio_ns += r.interval_ns; ++index; }
    }
    pthread_mutex_lock(&c.q.mutex);
    c.q.done = true; pthread_cond_broadcast(&c.q.changed);
    pthread_mutex_unlock(&c.q.mutex);
    if (c.parallel && pthread_join(thread, NULL)) die("worker join failed");
    bool complete = !c.q.failed && c.completed == c.submitted && audio_ns >= (uint64_t)seconds * UINT64_C(1000000000);
    struct rusage usage;
    if (getrusage(RUSAGE_SELF, &usage)) die("getrusage failed");
    size_t frames = (size_t)c.measured.frames;
    uint64_t fullpath_misses = 0, nominal_misses = 0, busy_total = 0, a_total = 0, b_total = 0;
    uint64_t a_cpu_total = 0, b_cpu_total = 0;
    for (size_t i = 0; i < frames; ++i) {
        const struct record *r = &c.stats[i];
        if (r->b_end_ns - r->a_start_ns > r->interval_ns) ++fullpath_misses;
        if (c.paced && r->b_end_ns > r->release_ns + r->interval_ns) ++nominal_misses;
        uint64_t a = r->a_end_ns - r->a_start_ns - r->producer_wait_ns;
        uint64_t b = r->b_end_ns - r->b_start_ns;
        a_total += a; b_total += b; busy_total += a + b;
        a_cpu_total += r->a_cpu_ns; b_cpu_total += r->b_cpu_ns;
    }
    uint64_t wall_ns = frames ? c.last_measured_finish - c.first_measured_start : 0;
    printf("{\"schema_version\":1,\"experiment\":\"rade-rx-two-worker\",\"execution\":\"%s\","
           "\"pacing\":\"%s\",\"workload\":\"%s\",\"execution_complete\":%s,\"failure_reason\":",
           argv[1], argv[4], argv[6], complete ? "true" : "false");
    if (c.q.failure) printf("\"%s\"", c.q.failure); else printf("null");
    printf(",\"test_hooks_used\":%s", (c.q.hold_b || c.fail_b_after >= 0 || c.delay_a_ms) ? "true" : "false");
    printf(",\"radio_io\":false,\"full_pipeline_qualified\":false,\"workers_joined\":true,"
           "\"original_70_percent_assessment\":\"unchanged_serial_reference_required\","
           "\"checksum_algorithm\":\"fnv1a64-noncryptographic\",\"checksum_cost_in_service_timing\":true,"
           "\"release_semantics\":\"last_input_sample_available_fixed_cumulative_nin\","
           "\"queue_policy\":\"%s\",\"warmup_audio_ns\":%" PRIu64
           ",\"fixture_preparation_ms\":%.3f,\"initialization_ms\":%.3f,\"peak_rss_kib_linux\":%ld,"
           "\"attempted_blocks\":%" PRIu64 ",\"processed_input_blocks\":%" PRIu64 ",\"submitted_blocks\":%" PRIu64 ",\"completed_blocks\":%" PRIu64
           ",\"queue\":{\"capacity\":2,\"capacity_excludes_b_private_record\":true,\"max_depth\":%u,"
           "\"full_events\":%" PRIu64 ",\"producer_wait_ns\":%" PRIu64 ",\"consumer_wait_ns\":%" PRIu64
           ",\"dropped_blocks\":%" PRIu64 ",\"abandoned_blocks\":%" PRIu64 "},\"full\":",
           c.paced || c.q.hold_b ? "fail-on-full" : "block-unpaced-only", warm_ns,
           fixture_ns / 1e6, initialization_ns / 1e6, usage.ru_maxrss,
           c.attempts, c.processed, c.submitted, c.completed, c.q.max_depth, c.q.full_events,
           c.q.producer_wait_ns, c.q.consumer_wait_ns, c.q.dropped, c.q.abandoned);
    print_counters(&c.full); printf(",\"measured\":"); print_counters(&c.measured);
    printf(",\"timing\":{\"wall_ns_including_drain\":%" PRIu64 ",\"summed_service_ns\":%" PRIu64
           ",\"a_service_ns\":%" PRIu64 ",\"b_service_ns\":%" PRIu64
           ",\"a_cpu_ns\":%" PRIu64 ",\"b_cpu_ns\":%" PRIu64
           ",\"fullpath_one_input_interval_misses\":%" PRIu64 ",\"nominal_release_one_interval_misses\":",
           wall_ns, busy_total, a_total, b_total, a_cpu_total, b_cpu_total, fullpath_misses);
    if (c.paced) printf("%" PRIu64, nominal_misses); else printf("null");
    printf(",\"ingress_late_more_than_one_interval\":%" PRIu64 ",\"ingress_late_max_ns\":%" PRIu64
           ",\"maximum_virtual_input_backlog_ns_including_warmup\":%" PRIu64 ",\"offline_output_samples_per_wall_second\":",
           c.ingress_deadline_misses, c.ingress_late_max, c.maximum_virtual_input_backlog_ns);
    if (wall_ns) printf("%.6f", c.measured.converted_count * 1e9 / wall_ns); else printf("null");
    const char *names[] = {"a_service_ms", "b_service_ms", "full_path_ms", "queue_residence_ms", "a_service_input_ratio",
        "b_service_feature_ratio", "full_path_input_ratio", "nominal_release_to_completion_ms", "input_resampling_ms",
        "rade_rx_ms", "fargan_ms", "output_resampling_ms", "output_completion_period_ms"};
    for (int metric = 0; metric < 13; ++metric) {
        size_t nvalues = 0; uint64_t previous_output = 0;
        for (size_t i = 0; i < frames; ++i) {
            const struct record *r = &c.stats[i];
            double a = (double)(r->a_end_ns - r->a_start_ns - r->producer_wait_ns);
            double b = (double)(r->b_end_ns - r->b_start_ns);
            double full = (double)(r->b_end_ns - r->a_start_ns), value;
            switch (metric) {
            case 0: value = a / 1e6; break;
            case 1: value = b / 1e6; break;
            case 2: value = full / 1e6; break;
            case 3: value = (r->b_start_ns - r->publish_ns) / 1e6; break;
            case 4: value = a / r->interval_ns; break;
            case 5:
                if (!r->nfeatures) continue;
                value = b / ((double)(r->nfeatures / NB_TOTAL_FEATURES) * LPCNET_FRAME_SIZE * 1e9 / RADE_SPEECH_SAMPLE_RATE);
                break;
            case 6: value = full / r->interval_ns; break;
            case 7:
                if (!c.paced) continue;
                value = (r->b_end_ns - r->release_ns) / 1e6; break;
            case 8: value = r->input_ns / 1e6; break;
            case 9: value = r->rade_ns / 1e6; break;
            case 10: value = r->speech_ns / 1e6; break;
            case 11: value = r->output_ns / 1e6; break;
            default:
                if (!r->converted_count) continue;
                if (!previous_output) { previous_output = r->b_end_ns; continue; }
                value = (r->b_end_ns - previous_output) / 1e6; previous_output = r->b_end_ns;
            }
            values[nvalues++] = value;
        }
        printf(","); distribution(names[metric], values, nvalues);
    }
    printf("}}\n");
    free(c.fixture); free(c.input); free(c.eoo); free(c.native_out); free(c.converted);
    free(c.pcm_out); free(c.iq); free(c.stats); free(values);
    src_delete(c.input_src); src_delete(c.output_src); rade_close(c.rade); rade_finalize();
    pthread_cond_destroy(&c.q.changed); pthread_mutex_destroy(&c.q.mutex);
    return complete ? 0 : 3;
}
