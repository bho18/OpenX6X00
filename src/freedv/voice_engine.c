/* SPDX-License-Identifier: BSD-2-Clause
 * Speech sequencing and normalization follow the pinned rade_c examples,
 * Copyright (C) 2024 David Rowe. See tools/freedv/THIRD-PARTY-NOTICES.md. */
#define _POSIX_C_SOURCE 200809L
#include "voice_engine.h"
#include "rade_api.h"
#include "tools/freedv/rade_speech.h"
#include "cpu_support.h"
#include <samplerate.h>
#include <pthread.h>
#include <stdatomic.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <math.h>
#define FEATURES (RADE_NZMF * RADE_FRAMES_PER_STEP * NB_TOTAL_FEATURES)
#define NATIVE 4096
#define PCM 72000
#define INPUT 24000
#ifdef FREEDV_VOICE_TEST_HOOKS
#define injected(stage) fd_voice_test_fail(stage)
#else
#define injected(stage) false
#endif
struct feature { float data[FEATURES]; int n; };
struct fd_voice_engine {
    pthread_mutex_t lock;
    pthread_cond_t changed;
    pthread_t processor, synthesizer;
    bool processor_started, synthesizer_started, shutdown, processing, synthesizing;
    atomic_bool cancelled;
    fd_voice_direction direction;
    uint64_t generation;
    uint64_t processor_notified, synthesis_notified;
    unsigned rate, input_n, output_n, output_limit, feature_head, feature_n;
    int16_t input[INPUT], output[PCM];
    struct feature features[2];
    bool finish, finished, reserve_ready;
    struct rade *rade;
    LPCNetEncState *encoder;
    fd_rade_speech_decoder decoder;
    SRC_STATE *rx_in, *rx_out, *tx_in, *tx_out;
    SRC_STATE *input_src, *output_src;
    float chunk[480], native[NATIVE], tx_features[FEATURES];
    unsigned chunk_n, chunk_offset, native_n, tx_feature_n;
    bool src_ended;
    int arch;
    fd_voice_stats stats;
};
static pthread_once_t init_once = PTHREAD_ONCE_INIT;
static pthread_mutex_t warm_lock = PTHREAD_MUTEX_INITIALIZER;
static bool warmed;
static unsigned successful_warmups;
static void initialize(void) { rade_initialize(); }
static uint64_t ns(void) {
    struct timespec t; clock_gettime(CLOCK_MONOTONIC, &t);
    return (uint64_t)t.tv_sec * 1000000000u + t.tv_nsec;
}
/* The caller holds lock. Timed waits observe even the lock-free cancel fence. */
static void wait_changed(fd_voice_engine *e) {
    struct timespec t; clock_gettime(CLOCK_MONOTONIC, &t);
    t.tv_nsec += 10000000; if (t.tv_nsec >= 1000000000) { ++t.tv_sec; t.tv_nsec -= 1000000000; }
    pthread_cond_timedwait(&e->changed, &e->lock, &t);
}
static bool live(fd_voice_engine *e) { return !e->shutdown && !atomic_load(&e->cancelled); }
static void notify_work(fd_voice_engine *e) {
    if(!e->processing && live(e) && e->direction!=FD_VOICE_IDLE && !e->finished &&
        (e->direction==FD_VOICE_RX ? e->input_n!=0 : e->reserve_ready || e->finish) && !e->processor_notified)e->processor_notified=ns();
    if(!e->synthesizing && live(e) && e->feature_n && !e->synthesis_notified)e->synthesis_notified=ns();
    pthread_cond_broadcast(&e->changed);
}
static void resumed(fd_voice_engine *e,uint64_t *notified) {
    if(!*notified)return;
    uint64_t delay=ns()-*notified;*notified=0;
    e->stats.scheduling_delay_ns+=delay;++e->stats.scheduling_events;
    if(delay>e->stats.scheduling_max_ns)e->stats.scheduling_max_ns=delay;
}
static void fail_locked(fd_voice_engine *e) {
    ++e->stats.failures; atomic_store(&e->cancelled, true); pthread_cond_broadcast(&e->changed);
}
static void fail(fd_voice_engine *e) { pthread_mutex_lock(&e->lock); fail_locked(e); pthread_mutex_unlock(&e->lock); }
static void record_time(fd_voice_engine *e, uint64_t duration, bool speech) {
    pthread_mutex_lock(&e->lock);
    if (speech) {
        e->stats.synthesis_ns += duration;
        if (duration > e->stats.synthesis_max_ns) e->stats.synthesis_max_ns = duration;
    } else {
        e->stats.processing_ns += duration;
        if (duration > e->stats.processing_max_ns) e->stats.processing_max_ns = duration;
    }
    unsigned bucket = 0; uint64_t us = duration / 1000;
    while (us > 1 && bucket < 31) { us >>= 1; ++bucket; }
    ++e->stats.duration_histogram[bucket];
    pthread_mutex_unlock(&e->lock);
}
/* Output resampling can block only on bounded, cancellable queue pressure.
 * Both workers are fenced before output_src or queue contents change. */
static bool output(fd_voice_engine *e, const float *data, unsigned count, unsigned rate, bool last) {
    unsigned used = 0;
    bool tail = last;
    do {
        float converted[24000]; int16_t pcm[24000];
        SRC_DATA d = {.data_in=data+used, .input_frames=count-used, .data_out=converted,
            .output_frames=24000, .src_ratio=(double)e->rate/rate, .end_of_input=last};
        uint64_t begin = ns();
        if (src_process(e->output_src, &d)) { fail(e); return false; }
        record_time(e, ns()-begin, e->direction == FD_VOICE_RX);
        used += (unsigned)d.input_frames_used;
        src_float_to_short_array(converted, pcm, (int)d.output_frames_gen);
        pthread_mutex_lock(&e->lock);
        uint64_t waiting = 0;
        while (live(e) && e->output_n + d.output_frames_gen > e->output_limit) {
            if (!waiting) waiting = ns();
            wait_changed(e);
        }
        if (waiting) e->stats.queue_wait_ns += ns()-waiting;
        if (!live(e)) { pthread_mutex_unlock(&e->lock); return false; }
        memcpy(e->output+e->output_n, pcm, (size_t)d.output_frames_gen*sizeof(*pcm));
        e->output_n += (unsigned)d.output_frames_gen;
        e->stats.output_samples += (unsigned)d.output_frames_gen;
        if (e->output_n > e->stats.output_high_water) e->stats.output_high_water = e->output_n;
        pthread_cond_broadcast(&e->changed);
        pthread_mutex_unlock(&e->lock);
        tail = last && d.output_frames_gen != 0;
        if (!d.input_frames_used && !d.output_frames_gen && used < count) { fail(e); return false; }
    } while (used < count || tail);
    return true;
}
/* Gather a native block, with 10 ms input conversion increments. Only the final
 * native frame may be padded; SRC filter delay is flushed before declaring EOF. */
static int gather(fd_voice_engine *e, unsigned wanted, unsigned rate, bool tx) {
    while (e->native_n < wanted) {
        bool ending;
        pthread_mutex_lock(&e->lock);
        while (live(e) && !e->input_n && e->chunk_offset == e->chunk_n && !(tx && e->finish))
            wait_changed(e);
        if (!live(e)) { pthread_mutex_unlock(&e->lock); return -1; }
        if (e->chunk_offset == e->chunk_n) {
            unsigned n = e->input_n < e->rate/100 ? e->input_n : e->rate/100;
            for (unsigned i=0; i<n; ++i) e->chunk[i] = e->input[i] / 32768.f;
            memmove(e->input, e->input+n, (e->input_n-n)*sizeof(*e->input));
            e->input_n -= n; e->chunk_n = n; e->chunk_offset = 0;
        }
        ending = tx && e->finish && !e->input_n;
        pthread_mutex_unlock(&e->lock);
        SRC_DATA d = {.data_in=e->chunk+e->chunk_offset, .input_frames=e->chunk_n-e->chunk_offset,
            .data_out=e->native+e->native_n, .output_frames=wanted-e->native_n,
            .src_ratio=(double)rate/e->rate, .end_of_input=ending};
        uint64_t begin = ns();
        if (src_process(e->input_src, &d)) { fail(e); return -1; }
        record_time(e, ns()-begin, false);
        e->chunk_offset += (unsigned)d.input_frames_used;
        e->native_n += (unsigned)d.output_frames_gen;
        if (!d.input_frames_used && !d.output_frames_gen && ending) {
            e->src_ended = true;
            if (!e->native_n) return 0;
            pthread_mutex_lock(&e->lock);
            e->stats.padded_samples += wanted-e->native_n;
            pthread_mutex_unlock(&e->lock);
            memset(e->native+e->native_n, 0, (wanted-e->native_n)*sizeof(float));
            e->native_n = wanted;
        }
        if (e->src_ended || e->native_n == wanted) break;
    }
    e->native_n = 0;
    return 1;
}
static bool modem_output(fd_voice_engine *e, RADE_COMP *iq, int n, bool last) {
    if (n <= 0 || n > NATIVE) { fail(e); return false; }
    float pcm[NATIVE];
    for (int i=0; i<n; ++i) pcm[i] = fmaxf(-1.f, fminf(32767.f/32768.f, iq[i].real*(RADE_INT16_SCALE/32768.f)));
    return output(e, pcm, (unsigned)n, RADE_MODEM_SAMPLE_RATE, last);
}
static bool tx_block(fd_voice_engine *e) {
    RADE_COMP iq[NATIVE]; uint64_t begin = ns();
    int n = rade_tx(e->rade, iq, e->tx_features);
    record_time(e, ns()-begin, false);
    pthread_mutex_lock(&e->lock); ++e->stats.modem_blocks; pthread_mutex_unlock(&e->lock);
    e->tx_feature_n = 0;
    return modem_output(e, iq, n, false);
}
static void tx_step(fd_voice_engine *e) {
    int got = e->src_ended ? 0 : gather(e, LPCNET_FRAME_SIZE, RADE_SPEECH_SAMPLE_RATE, true);
    if (got < 0) return;
    if (got) {
        short pcm[LPCNET_FRAME_SIZE]; src_float_to_short_array(e->native, pcm, LPCNET_FRAME_SIZE);
        uint64_t begin = ns();
        bool ok = !injected(FD_VOICE_TEST_ENCODE) && fd_rade_speech_encode(e->encoder, pcm, e->tx_features+e->tx_feature_n, NB_TOTAL_FEATURES, e->arch);
        record_time(e, ns()-begin, false);
        if (!ok) { fail(e); return; }
        e->tx_feature_n += NB_TOTAL_FEATURES;
        pthread_mutex_lock(&e->lock); ++e->stats.speech_frames; pthread_mutex_unlock(&e->lock);
        if (e->tx_feature_n == FEATURES && !tx_block(e)) return;
        return;
    }
    /* Pad the final modem frame by extracting features from zero speech. */
    if (e->tx_feature_n) {
        short zero[LPCNET_FRAME_SIZE] = {0};
        while (e->tx_feature_n < FEATURES) {
            if (!fd_rade_speech_encode(e->encoder, zero, e->tx_features+e->tx_feature_n, NB_TOTAL_FEATURES, e->arch)) { fail(e); return; }
            e->tx_feature_n += NB_TOTAL_FEATURES;
        }
        if (!tx_block(e)) return;
    }
    RADE_COMP iq[NATIVE]; int n = rade_tx_eoo(e->rade, iq);
    if (!modem_output(e, iq, n, true)) return;
    /* GUI-style 200 ms silence follows the SRC tail, at the device rate. */
    pthread_mutex_lock(&e->lock);
    unsigned silence = e->rate/5;
    while (live(e) && e->output_n+silence > e->output_limit) wait_changed(e);
    if (live(e)) {
        memset(e->output+e->output_n, 0, silence*sizeof(*e->output));
        e->output_n += silence; e->stats.output_samples += silence;
        if (e->output_n > e->stats.output_high_water) e->stats.output_high_water = e->output_n;
        ++e->stats.eoo_count; e->finished = true;
    }
    pthread_cond_broadcast(&e->changed); pthread_mutex_unlock(&e->lock);
}
static void rx_step(fd_voice_engine *e) {
    int wanted = rade_nin(e->rade);
    if (wanted <= 0 || wanted > NATIVE) { fail(e); return; }
    if (gather(e, (unsigned)wanted, RADE_MODEM_SAMPLE_RATE, false) <= 0) return;
    RADE_COMP iq[NATIVE]; float features[FEATURES], eoo[1024]; int has_eoo = 0;
    for (int i=0; i<wanted; ++i) iq[i] = (RADE_COMP){e->native[i]*(32768.f*2.f/RADE_INT16_SCALE), 0};
    uint64_t begin = ns(); int n = injected(FD_VOICE_TEST_DECODE)?-1:rade_rx(e->rade, features, &has_eoo, eoo, iq);
    record_time(e, ns()-begin, false);
    pthread_mutex_lock(&e->lock);
    ++e->stats.modem_blocks;
    bool synchronized=rade_sync(e->rade)!=0;
    if(synchronized && !e->stats.synchronized)++e->stats.acquisitions;
    e->stats.synchronized=synchronized;
    if(synchronized)++e->stats.synchronized_blocks;
    if (n < 0 || n > FEATURES || n % NB_TOTAL_FEATURES) fail_locked(e);
    else if (n) {
        while (live(e) && e->feature_n == 2) wait_changed(e);
        if (live(e)) {
            struct feature *f = &e->features[(e->feature_head+e->feature_n)%2];
            f->n = n; memcpy(f->data, features, n*sizeof(float)); ++e->feature_n;
        }
    }
    notify_work(e); pthread_mutex_unlock(&e->lock);
}
static void *process(void *arg) {
    fd_voice_engine *e = arg;
    pthread_mutex_lock(&e->lock);
    while (!e->shutdown) {
        while (!e->shutdown && (!live(e) || e->direction == FD_VOICE_IDLE || e->finished ||
              (e->direction == FD_VOICE_TX && !e->reserve_ready && !e->finish))) pthread_cond_wait(&e->changed,&e->lock);
        if (e->shutdown) break;
        fd_voice_direction direction = e->direction;
        resumed(e,&e->processor_notified);
        e->processing = true; pthread_mutex_unlock(&e->lock);
        if (direction == FD_VOICE_TX) tx_step(e); else rx_step(e);
        pthread_mutex_lock(&e->lock); e->processing = false; pthread_cond_broadcast(&e->changed);
    }
    pthread_mutex_unlock(&e->lock); return NULL;
}
static void *synthesize(void *arg) {
    fd_voice_engine *e = arg; pthread_mutex_lock(&e->lock);
    while (!e->shutdown) {
        while (!e->shutdown && (!live(e) || e->direction != FD_VOICE_RX || !e->feature_n)) pthread_cond_wait(&e->changed,&e->lock);
        if (e->shutdown) break;
        struct feature f = e->features[e->feature_head]; e->feature_head = (e->feature_head+1)%2; --e->feature_n;
        resumed(e,&e->synthesis_notified);
        e->synthesizing = true; pthread_cond_broadcast(&e->changed); pthread_mutex_unlock(&e->lock);
        float pcm[NATIVE]; uint64_t begin = ns();
        int n = injected(FD_VOICE_TEST_SYNTHESIS)?-1:fd_rade_speech_render(&e->decoder, f.data, f.n, pcm, NATIVE);
        record_time(e, ns()-begin, true);
        if(n>0){pthread_mutex_lock(&e->lock);++e->stats.synthesis_blocks;pthread_mutex_unlock(&e->lock);}
        if (n < 0) fail(e); else if (n) output(e, pcm, (unsigned)n, RADE_SPEECH_SAMPLE_RATE, false);
        pthread_mutex_lock(&e->lock); e->synthesizing = false; pthread_cond_broadcast(&e->changed);
    }
    pthread_mutex_unlock(&e->lock); return NULL;
}
static bool warm(void) {
    char model[] = "built-in"; struct rade *r = rade_open(model, RADE_VERBOSE_0);
    LPCNetEncState *enc = lpcnet_encoder_create(); fd_rade_speech_decoder dec;
    float features[FEATURES] = {0}, speech[NATIVE], eoo[1024]; short zero[NATIVE] = {0}; RADE_COMP iq[NATIVE] = {0};
    bool ok = r && enc && rade_n_features_in_out(r) == FEATURES && rade_nin_max(r) <= NATIVE;
    if (ok) {
        fd_rade_speech_init(&dec);
        ok = fd_rade_speech_encode(enc, zero, features, FEATURES, opus_select_arch()) &&
             rade_tx(r, iq, features) > 0 && fd_rade_speech_render(&dec, features, FEATURES, speech, NATIVE) >= 0;
        /* Warm the pinned V1 neural decoder explicitly: unsynchronized zero
         * input alone only exercises acquisition. This is a disposable RX. */
        float latent[RADE_LATENT_DIM] = {0};
        float decoded[RADE_FRAMES_PER_STEP * RADE_NUM_FEATURES_AUX];
        rade_core_decoder(&r->rx.dec_state, &r->rx.dec_model, decoded, latent, 0);
        int has_eoo; memset(iq, 0, sizeof(iq)); rade_rx(r, features, &has_eoo, eoo, iq);
    }
    if (enc) lpcnet_encoder_destroy(enc);
    if (r) rade_close(r);
    return ok;
}
/* Only cache successful preparation of the immutable code/model paths. Live
 * encoder/decoder histories are always separate and are never shared. A failed
 * preparation remains retryable rather than poisoning pthread_once forever. */
static bool ensure_warm(bool *reused,unsigned *runs) {
    pthread_mutex_lock(&warm_lock);
    *reused=warmed;
    if(!warmed && warm()){warmed=true;++successful_warmups;}
    *runs=successful_warmups;
    bool ok=warmed;
    pthread_mutex_unlock(&warm_lock);
    return ok;
}
fd_voice_engine *fd_voice_create(unsigned rate) {
    if (rate != 8000 && rate != 44100 && rate != 48000) return NULL;
    pthread_once(&init_once, initialize);
    bool warmup_reused;unsigned warmup_runs;
    if (!ensure_warm(&warmup_reused,&warmup_runs)) return NULL;
    fd_voice_engine *e = calloc(1, sizeof(*e)); if (!e) return NULL;
    if (pthread_mutex_init(&e->lock, NULL)) { free(e); return NULL; }
    pthread_condattr_t attr; pthread_condattr_init(&attr); pthread_condattr_setclock(&attr, CLOCK_MONOTONIC);
    int rc = pthread_cond_init(&e->changed, &attr); pthread_condattr_destroy(&attr);
    if (rc) { pthread_mutex_destroy(&e->lock); free(e); return NULL; }
    atomic_init(&e->cancelled, true); e->rate = rate; e->arch = opus_select_arch();
    char model[] = "built-in"; int error;
    e->rade = rade_open(model, RADE_VERBOSE_0); e->encoder = lpcnet_encoder_create(); fd_rade_speech_init(&e->decoder);
    e->rx_in = src_new(SRC_SINC_FASTEST, 1, &error); e->rx_out = src_new(SRC_SINC_FASTEST, 1, &error);
    e->tx_in = src_new(SRC_SINC_FASTEST, 1, &error); e->tx_out = src_new(SRC_SINC_FASTEST, 1, &error);
    if (!e->rade || !e->encoder || !e->rx_in || !e->rx_out || !e->tx_in || !e->tx_out ||
        rade_n_features_in_out(e->rade) != FEATURES || rade_nin_max(e->rade) > NATIVE ||
        rade_n_eoo_bits(e->rade) > 1024 || rade_n_tx_out(e->rade) > NATIVE || rade_n_tx_eoo_out(e->rade) > NATIVE ||
        rade_set_acquisition_workers(e->rade, 3)) { fd_voice_destroy(e); return NULL; }
    unsigned maximum = rade_n_tx_out(e->rade) > rade_n_tx_eoo_out(e->rade) ? rade_n_tx_out(e->rade) : rade_n_tx_eoo_out(e->rade);
    e->output_limit = 3*((maximum*rate+7999)/8000);
    if (e->output_limit > PCM || e->output_limit < rate/5) { fd_voice_destroy(e); return NULL; }
    e->stats.rade_creations = e->stats.encoder_creations = e->stats.decoder_creations = 1; e->stats.resampler_creations = 4;
    e->stats.warmup_runs=warmup_runs;e->stats.warmup_reused=warmup_reused;
    if (pthread_create(&e->processor, NULL, process, e)) { fd_voice_destroy(e); return NULL; }
    e->processor_started = true;
    if (pthread_create(&e->synthesizer, NULL, synthesize, e)) { fd_voice_destroy(e); return NULL; }
    e->synthesizer_started = true; e->stats.worker_creations = 2;
    return e;
}
bool fd_voice_transition(fd_voice_engine *e, fd_voice_direction direction, uint64_t generation) {
    if (!e || !generation || direction < FD_VOICE_IDLE || direction > FD_VOICE_TX) return false;
    pthread_mutex_lock(&e->lock);
    if (generation <= e->generation) { pthread_mutex_unlock(&e->lock); return false; }
    fd_voice_cancel(e);
    while (e->processing || e->synthesizing) wait_changed(e);
    if (generation <= e->generation || e->shutdown) { pthread_mutex_unlock(&e->lock); return false; }
    e->generation = generation; e->direction = direction;
    e->input_n = e->output_n = e->feature_n = e->feature_head = 0;
    e->chunk_n = e->chunk_offset = e->native_n = e->tx_feature_n = 0;
    e->finish = e->finished = e->reserve_ready = e->src_ended = false;
    e->processor_notified=e->synthesis_notified=0;
    e->stats.synchronized = false; e->stats.input_samples = 0; ++e->stats.turns;
    src_reset(e->rx_in); src_reset(e->rx_out); src_reset(e->tx_in); src_reset(e->tx_out);
    e->input_src = direction == FD_VOICE_TX ? e->tx_in : e->rx_in;
    e->output_src = direction == FD_VOICE_TX ? e->tx_out : e->rx_out;
    if (direction == FD_VOICE_RX) rade_rx_reset(&e->rade->rx);
    atomic_store(&e->cancelled, false); pthread_cond_broadcast(&e->changed); pthread_mutex_unlock(&e->lock);
    return true;
}
size_t fd_voice_submit(fd_voice_engine *e, uint64_t generation, const int16_t *pcm, size_t count) {
    if (!e || !count || !pcm) return 0;
    pthread_mutex_lock(&e->lock);
    if (!live(e) || generation != e->generation || e->finish || e->direction == FD_VOICE_IDLE) { pthread_mutex_unlock(&e->lock); return 0; }
    if (count > e->rate/2 || e->input_n > e->rate/2-count) { fail_locked(e); pthread_mutex_unlock(&e->lock); return 0; }
    memcpy(e->input+e->input_n, pcm, count*sizeof(*pcm)); e->input_n += (unsigned)count; e->stats.input_samples += count;
    if (e->stats.input_samples >= e->rate/5) e->reserve_ready = true;
    if (e->input_n > e->stats.input_high_water) e->stats.input_high_water = e->input_n;
    notify_work(e); pthread_mutex_unlock(&e->lock); return count;
}
size_t fd_voice_receive(fd_voice_engine *e, uint64_t generation, int16_t *pcm, size_t count) {
    if (!e || !count || !pcm) return 0;
    pthread_mutex_lock(&e->lock);
    if (!live(e) || generation != e->generation) { pthread_mutex_unlock(&e->lock); return 0; }
    if (count > e->output_n) count = e->output_n;
    memcpy(pcm, e->output, count*sizeof(*pcm)); e->output_n -= (unsigned)count;
    memmove(e->output, e->output+count, e->output_n*sizeof(*pcm)); e->stats.delivered_samples += count;
    pthread_cond_broadcast(&e->changed); pthread_mutex_unlock(&e->lock); return count;
}
bool fd_voice_finish(fd_voice_engine *e, uint64_t generation) {
    if (!e) return false;
    pthread_mutex_lock(&e->lock);
    bool ok = live(e) && generation == e->generation && e->direction == FD_VOICE_TX;
    if (ok) { e->finish = true; notify_work(e); }
    pthread_mutex_unlock(&e->lock); return ok;
}
void fd_voice_cancel(fd_voice_engine *e) { if (e) atomic_store(&e->cancelled, true); }
fd_voice_stats fd_voice_observe(fd_voice_engine *e) {
    if (!e) return (fd_voice_stats){0};
    pthread_mutex_lock(&e->lock); fd_voice_stats s = e->stats;
    s.generation = e->generation; s.input_depth = e->input_n; s.output_depth = e->output_n;
    s.finished = e->finished; s.cancelled = atomic_load(&e->cancelled); pthread_mutex_unlock(&e->lock); return s;
}
void fd_voice_destroy(fd_voice_engine *e) {
    if (!e) return;
    fd_voice_cancel(e); pthread_mutex_lock(&e->lock); e->shutdown = true; pthread_cond_broadcast(&e->changed); pthread_mutex_unlock(&e->lock);
    if (e->processor_started) pthread_join(e->processor, NULL);
    if (e->synthesizer_started) pthread_join(e->synthesizer, NULL);
    if (e->rade) rade_close(e->rade);
    if (e->encoder) lpcnet_encoder_destroy(e->encoder);
    SRC_STATE *src[] = {e->rx_in, e->rx_out, e->tx_in, e->tx_out};
    for (unsigned i=0; i<4; ++i) if (src[i]) src_delete(src[i]);
    pthread_cond_destroy(&e->changed); pthread_mutex_destroy(&e->lock); free(e);
}
