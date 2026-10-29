/* SPDX-License-Identifier: BSD-2-Clause
 * Full speech kernels follow rade_c examples, Copyright (C) 2024 David Rowe.
 * See THIRD-PARTY-NOTICES.md. */
#define _POSIX_C_SOURCE 200809L
#include "rade_stream.h"
#include "rade_api.h"
#include "rade_speech.h"
#include "cpu_support.h"
#include "src/freedv/capture_ring.h"
#include <samplerate.h>
#include <pthread.h>
#include <stdatomic.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <math.h>
#include <errno.h>
#define FEATURES (RADE_NZMF * RADE_FRAMES_PER_STEP * NB_TOTAL_FEATURES)
#define NATIVE 4096
#define PCM 24000
#define TIMINGS 2048
struct feature_record { float features[FEATURES]; int count; uint64_t interval_ns; };
struct fd_rade_stream {
    fd_stream_direction direction;
    uint64_t generation;
    unsigned output_limit;
    struct rade *rade;
    LPCNetEncState *encoder;
    fd_rade_speech_decoder speech;
    SRC_STATE *input_src, *output_src;
    freedv_capture_ring input_queue, output_queue;
    pthread_t receiver, renderer;
    bool receiver_started, renderer_started;
    atomic_bool stop, finish_tx, tx_finished;
    atomic_uint failures;
    pthread_mutex_t mutex;
    pthread_cond_t changed;
    struct feature_record features[2];
    unsigned head, count, max_depth;
    fd_stream_stats stats;
    uint64_t receiver_times[TIMINGS], speech_times[TIMINGS];
    uint64_t receiver_intervals[TIMINGS], speech_intervals[TIMINGS];
    size_t receiver_count, speech_count;
    float input[NATIVE], native[NATIVE], converted[PCM], features_tmp[FEATURES];
    float chunk[441], eoo[1024];
    size_t chunk_offset, chunk_count;
    uint64_t input_service_ns;
    short pcm[PCM];
    RADE_COMP iq[NATIVE];
};
static pthread_once_t initialized = PTHREAD_ONCE_INIT;
static void initialize(void) { rade_initialize(); }
static uint64_t nanos(void) {
    struct timespec t; clock_gettime(CLOCK_MONOTONIC, &t);
    return (uint64_t)t.tv_sec * 1000000000u + t.tv_nsec;
}
static void pause_input(void) { struct timespec t = {0, 1000000}; nanosleep(&t, NULL); }
static void failure(fd_rade_stream *s) { atomic_fetch_add(&s->failures, 1); atomic_store(&s->stop, true); }
static bool resample_input(fd_rade_stream *s, size_t wanted, int rate) {
    size_t produced = 0;
    while (produced < wanted && !atomic_load(&s->stop)) {
        if (s->chunk_offset == s->chunk_count) {
            short pcm[441];
            size_t n = freedv_capture_ring_pop(&s->input_queue, pcm, 441);
            if (!n) {
                if (atomic_load(&s->finish_tx)) {
                    if (!produced) return false;
                    pthread_mutex_lock(&s->mutex);s->stats.padded_native_samples+=wanted-produced;pthread_mutex_unlock(&s->mutex);
                    memset(s->input+produced,0,(wanted-produced)*sizeof(float));
                    return true;
                }
                pause_input(); continue;
            }
            uint64_t convert_begin = nanos();
            src_short_to_float_array(pcm, s->chunk, (int)n);
            s->input_service_ns += nanos()-convert_begin;
            s->chunk_count = n; s->chunk_offset = 0;
        }
        SRC_DATA d = {.data_in=s->chunk+s->chunk_offset,
            .input_frames=(long)(s->chunk_count-s->chunk_offset), .data_out=s->input+produced,
            .output_frames=(long)(wanted-produced), .src_ratio=rate/44100.0};
        uint64_t src_begin = nanos();
        if (src_process(s->input_src, &d) || (!d.input_frames_used && !d.output_frames_gen)) { failure(s); return false; }
        s->input_service_ns += nanos()-src_begin;
        s->chunk_offset += (size_t)d.input_frames_used; produced += (size_t)d.output_frames_gen;
        pthread_mutex_lock(&s->mutex); s->stats.consumed_samples += (uint64_t)d.input_frames_used; pthread_mutex_unlock(&s->mutex);
    }
    return produced == wanted && !atomic_load(&s->stop);
}
static bool output_pcm(fd_rade_stream *s, const float *native, size_t produced, int rate, bool final) {
    size_t used = 0;
    while (used < produced && !atomic_load(&s->stop)) {
        SRC_DATA d = {.data_in=native+used, .input_frames=(long)(produced-used),
            .data_out=s->converted, .output_frames=PCM, .src_ratio=44100.0/rate, .end_of_input=final};
        if (src_process(s->output_src, &d) || (!d.input_frames_used && !d.output_frames_gen)) { failure(s); return false; }
        used += (size_t)d.input_frames_used;
        src_float_to_short_array(s->converted, s->pcm, (int)d.output_frames_gen);
        unsigned depth = freedv_capture_ring_stats(&s->output_queue).queued_samples;
        unsigned limit=s->output_limit;
        if ((unsigned long)d.output_frames_gen > limit) { failure(s); return false; }
        /* TX encodes a full modem block at a time. A slow first call can leave
         * input queued, after which the encoder catches up faster than playback.
         * Keep the one-block queue bound and wait for its consumer, rather than
         * treating ordinary backpressure as a codec failure. No radio/device or
         * file lock is held; abort remains an independent atomic fence. RX keeps
         * its existing overflow-fails-measurement contract. */
        uint64_t waiting=0;
        if(s->direction==FD_STREAM_TX) {
            while(!atomic_load(&s->stop) && (depth==UINT_MAX || depth+(unsigned)d.output_frames_gen>limit)) {
                if(!waiting) {
                    waiting=nanos();
                    pthread_mutex_lock(&s->mutex);++s->stats.output_wait_events;pthread_mutex_unlock(&s->mutex);
                }
                pause_input();
                depth=freedv_capture_ring_stats(&s->output_queue).queued_samples;
            }
            if(waiting) {
                pthread_mutex_lock(&s->mutex);s->stats.output_wait_ns+=nanos()-waiting;pthread_mutex_unlock(&s->mutex);
            }
            if(atomic_load(&s->stop))return false;
        }
        if (depth == UINT_MAX || depth + d.output_frames_gen > limit ||
            freedv_capture_ring_push(&s->output_queue, s->pcm, (size_t)d.output_frames_gen) != (size_t)d.output_frames_gen) {
            failure(s); return false;
        }
        pthread_mutex_lock(&s->mutex); s->stats.output_samples += (uint64_t)d.output_frames_gen;
        if(depth+d.output_frames_gen>s->stats.output_max_depth)s->stats.output_max_depth=depth+(unsigned)d.output_frames_gen;
        pthread_mutex_unlock(&s->mutex);
    }
    return !atomic_load(&s->stop);
}
static void timing(uint64_t *values, size_t *count, uint64_t value, fd_rade_stream *s) {
    if (*count == TIMINGS) { failure(s); return; }
    values[(*count)++] = value;
}
static void *render(void *arg) {
    fd_rade_stream *s = arg;
    struct feature_record r;
    float native[NATIVE];
    for (;;) {
        pthread_mutex_lock(&s->mutex);
        while (!s->count && !atomic_load(&s->stop)) {
            struct timespec t; clock_gettime(CLOCK_MONOTONIC, &t);
            t.tv_nsec += 20000000; if (t.tv_nsec >= 1000000000) { ++t.tv_sec; t.tv_nsec -= 1000000000; }
            int rc = pthread_cond_timedwait(&s->changed, &s->mutex, &t);
            if (rc && rc != ETIMEDOUT) failure(s);
        }
        if (atomic_load(&s->stop)) { pthread_mutex_unlock(&s->mutex); break; }
        r = s->features[s->head]; s->head = (s->head+1)%2; --s->count;
        pthread_mutex_unlock(&s->mutex);
        uint64_t begin = nanos();
        int count = fd_rade_speech_render(&s->speech, r.features, r.count, native, NATIVE);
        if (count < 0) { failure(s); break; }
        if (!output_pcm(s, native, (size_t)count, RADE_SPEECH_SAMPLE_RATE, false)) break;
        pthread_mutex_lock(&s->mutex);
        uint64_t service=nanos()-begin;
        if(s->speech_count<TIMINGS)s->speech_intervals[s->speech_count]=r.interval_ns;
        timing(s->speech_times, &s->speech_count, service, s);
        s->stats.speech_service_ns+=service;s->stats.speech_deadline_misses+=service>r.interval_ns;
        ++s->stats.rendered_blocks;
        pthread_mutex_unlock(&s->mutex);
    }
    return NULL;
}
static void *receive_or_encode(void *arg) {
    fd_rade_stream *s = arg;
    const bool tx = s->direction == FD_STREAM_TX;
    const int rate = tx ? RADE_SPEECH_SAMPLE_RATE : RADE_MODEM_SAMPLE_RATE;
    const int feature_n = rade_n_features_in_out(s->rade);
    while (!atomic_load(&s->stop)) {
        s->input_service_ns = 0;
        int wanted = tx ? feature_n/NB_TOTAL_FEATURES*LPCNET_FRAME_SIZE : rade_nin(s->rade);
        if (wanted <= 0 || wanted > NATIVE || !resample_input(s, (size_t)wanted, rate)) break;
        uint64_t begin = nanos();
        if (tx) {
            src_float_to_short_array(s->input, s->pcm, wanted);
            if (!fd_rade_speech_encode(s->encoder, s->pcm, s->features_tmp, feature_n, s->stats.opus_arch)) { failure(s); break; }
            int n = rade_tx(s->rade, s->iq, s->features_tmp);
            if (n < 0 || n > NATIVE) { failure(s); break; }
            for (int i = 0; i < n; ++i)
                s->native[i] = fmaxf(-1.f, fminf(32767.f/32768.f, s->iq[i].real * (RADE_INT16_SCALE/32768.f)));
            if (!output_pcm(s, s->native, (size_t)n, RADE_MODEM_SAMPLE_RATE, false)) break;
        } else {
            for (int i = 0; i < wanted; ++i) {
                s->iq[i].real = s->input[i] * (32768.f*2.f/RADE_INT16_SCALE); s->iq[i].imag = 0;
            }
            int eoo = 0;
            int n = rade_rx(s->rade, s->features_tmp, &eoo, s->eoo, s->iq);
            if (n < 0 || n > FEATURES || n % NB_TOTAL_FEATURES) { failure(s); break; }
            pthread_mutex_lock(&s->mutex);
            s->stats.synchronized = rade_sync(s->rade) != 0;
            s->stats.synchronized_blocks += s->stats.synchronized;
            s->stats.feature_blocks += n != 0;
            if (s->count == 2) { failure(s); pthread_mutex_unlock(&s->mutex); break; }
            struct feature_record *r = &s->features[(s->head+s->count)%2];
            r->count = n;r->interval_ns=(uint64_t)wanted*1000000000u/rate; memcpy(r->features, s->features_tmp, (size_t)n*sizeof(float));
            if (++s->count > s->max_depth) s->max_depth = s->count;
            pthread_cond_signal(&s->changed);
            pthread_mutex_unlock(&s->mutex);
        }
        pthread_mutex_lock(&s->mutex);
        ++s->stats.blocks;
        if(tx) ++s->stats.rendered_blocks;
        uint64_t interval=(uint64_t)wanted*1000000000u/rate;
        uint64_t service=nanos()-begin+s->input_service_ns;
        if(s->receiver_count<TIMINGS)s->receiver_intervals[s->receiver_count]=interval;
        timing(s->receiver_times, &s->receiver_count, service, s);
        s->stats.represented_audio_ns+=interval;s->stats.receiver_service_ns+=service;
        s->stats.receiver_deadline_misses+=service>interval;
        pthread_mutex_unlock(&s->mutex);
    }
    if(tx && atomic_load(&s->finish_tx) && !atomic_load(&s->stop)) {
        while(freedv_capture_ring_stats(&s->output_queue).queued_samples && !atomic_load(&s->stop))pause_input();
        if(!atomic_load(&s->stop)) {
            int n=rade_tx_eoo(s->rade,s->iq);
            if(n<0 || n>NATIVE)failure(s);
            else {
                for(int i=0;i<n;++i)s->native[i]=fmaxf(-1.f,fminf(32767.f/32768.f,s->iq[i].real*(RADE_INT16_SCALE/32768.f)));
                if(output_pcm(s,s->native,(size_t)n,RADE_MODEM_SAMPLE_RATE,true))atomic_store(&s->tx_finished,true);
            }
        }
    }
    return NULL;
}
fd_rade_stream *fd_stream_prepare(fd_stream_direction direction, uint64_t generation) {
    uint64_t init_begin=nanos();
    if ((direction != FD_STREAM_RX && direction != FD_STREAM_TX) || !generation) return NULL;
    fd_rade_stream *s = calloc(1, sizeof(*s)); if (!s) return NULL;
    s->direction = direction; s->generation = generation;
    atomic_init(&s->stop, false); atomic_init(&s->finish_tx,false); atomic_init(&s->tx_finished,false); atomic_init(&s->failures, 0);
    freedv_capture_ring_init(&s->input_queue); freedv_capture_ring_init(&s->output_queue);
    if (pthread_mutex_init(&s->mutex, NULL)) { free(s); return NULL; }
    pthread_condattr_t attr;
    if (pthread_condattr_init(&attr)) { pthread_mutex_destroy(&s->mutex); free(s); return NULL; }
    int rc = pthread_condattr_setclock(&attr, CLOCK_MONOTONIC);
    if (!rc) rc = pthread_cond_init(&s->changed, &attr);
    pthread_condattr_destroy(&attr);
    if (rc) { pthread_mutex_destroy(&s->mutex); free(s); return NULL; }
    pthread_once(&initialized, initialize);
    char model[] = "built-in";
    s->rade = rade_open(model, RADE_VERBOSE_0);
    int error;
    s->input_src = src_new(SRC_SINC_FASTEST, 1, &error);
    s->output_src = src_new(SRC_SINC_FASTEST, 1, &error);
    if (direction == FD_STREAM_TX) s->encoder = lpcnet_encoder_create();
    fd_rade_speech_init(&s->speech);
    if (!s->rade || !s->input_src || !s->output_src || (direction == FD_STREAM_TX && !s->encoder) ||
        rade_n_features_in_out(s->rade) != FEATURES || rade_nin_max(s->rade) > NATIVE || rade_n_tx_eoo_out(s->rade)>NATIVE || rade_n_eoo_bits(s->rade) > 1024 ||
        rade_set_acquisition_workers(s->rade, direction == FD_STREAM_RX ? 3 : 1) ||
        rade_get_acquisition_workers(s->rade) != (unsigned)(direction == FD_STREAM_RX ? 3 : 1)) {
        fd_stream_destroy(s); return NULL;
    }
    int maximum=rade_n_tx_out(s->rade)>rade_n_tx_eoo_out(s->rade)?rade_n_tx_out(s->rade):rade_n_tx_eoo_out(s->rade);
    /* One largest modem/EOO block plus bounded SRC final-filter tail. */
    s->output_limit=direction==FD_STREAM_RX?22050u:(unsigned)(maximum*44100u/8000u+512u);
    s->stats.opus_arch=opus_select_arch();
    s->stats.initialization_ns=nanos()-init_begin;
    return s;
}
bool fd_stream_start(fd_rade_stream *s) {
    if (!s || s->receiver_started || s->renderer_started || atomic_load(&s->stop)) return false;
    if (s->direction == FD_STREAM_RX) {
        if (pthread_create(&s->renderer, NULL, render, s)) return false;
        s->renderer_started = true;
    }
    if (pthread_create(&s->receiver, NULL, receive_or_encode, s)) { fd_stream_abort(s); return false; }
    s->receiver_started = true; return true;
}
size_t fd_stream_submit(fd_rade_stream *s, uint64_t generation, const int16_t *pcm, size_t count) {
    if (!s || generation != s->generation || atomic_load(&s->stop) || atomic_load(&s->finish_tx) || (count && !pcm)) return 0;
    unsigned queued = freedv_capture_ring_stats(&s->input_queue).queued_samples;
    if (queued == UINT_MAX || count > 22050 || queued > 22050-count) { failure(s); return 0; }
    size_t n = freedv_capture_ring_push(&s->input_queue, pcm, count);
    pthread_mutex_lock(&s->mutex); s->stats.submitted_samples += n;
    if(queued+n>s->stats.input_max_depth)s->stats.input_max_depth=queued+(unsigned)n;
    pthread_mutex_unlock(&s->mutex);
    return n;
}
size_t fd_stream_receive(fd_rade_stream *s, uint64_t generation, int16_t *pcm, size_t count) {
    if (!s || generation != s->generation || atomic_load(&s->stop)) return 0;
    size_t n = freedv_capture_ring_pop(&s->output_queue, pcm, count);
    pthread_mutex_lock(&s->mutex); s->stats.delivered_samples += n; pthread_mutex_unlock(&s->mutex);
    return n;
}
void fd_stream_abort(fd_rade_stream *s) { if (s) atomic_store(&s->stop, true); }
bool fd_stream_finish_tx(fd_rade_stream *s) {
    if(!s || s->direction!=FD_STREAM_TX || atomic_load(&s->stop))return false;
    atomic_store(&s->finish_tx,true);return true;
}
bool fd_stream_tx_finished(fd_rade_stream *s) {return s && atomic_load(&s->tx_finished);}
unsigned fd_stream_output_pending(fd_rade_stream *s) {if(!s)return 0;unsigned n=freedv_capture_ring_stats(&s->output_queue).queued_samples;return n==UINT_MAX?0:n;}
static int compare_u64(const void *a, const void *b) { uint64_t x=*(const uint64_t *)a,y=*(const uint64_t *)b; return (x>y)-(x<y); }
static int compare_double(const void *a,const void *b){double x=*(const double *)a,y=*(const double *)b;return(x>y)-(x<y);}
fd_stream_stats fd_stream_observe(fd_rade_stream *s) {
    if (!s) return (fd_stream_stats){0};
    uint64_t values[TIMINGS];
    double ratios[TIMINGS];
    pthread_mutex_lock(&s->mutex);
    fd_stream_stats out = s->stats; out.generation=s->generation;
    out.feature_depth=s->count; out.feature_max_depth=s->max_depth;
    if (s->receiver_count) { memcpy(values,s->receiver_times,s->receiver_count*sizeof(uint64_t)); qsort(values,s->receiver_count,sizeof(uint64_t),compare_u64); out.receiver_p99_ns=values[(s->receiver_count*99+99)/100-1]; out.receiver_max_ns=values[s->receiver_count-1]; }
    if (s->speech_count) { memcpy(values,s->speech_times,s->speech_count*sizeof(uint64_t)); qsort(values,s->speech_count,sizeof(uint64_t),compare_u64); out.speech_p99_ns=values[(s->speech_count*99+99)/100-1]; out.speech_max_ns=values[s->speech_count-1]; }
    if(s->receiver_count){for(size_t i=0;i<s->receiver_count;++i)ratios[i]=(double)s->receiver_times[i]/s->receiver_intervals[i];qsort(ratios,s->receiver_count,sizeof(double),compare_double);out.receiver_p99_frame_ratio=ratios[(s->receiver_count*99+99)/100-1];}
    if(s->speech_count){for(size_t i=0;i<s->speech_count;++i)ratios[i]=(double)s->speech_times[i]/s->speech_intervals[i];qsort(ratios,s->speech_count,sizeof(double),compare_double);out.speech_p99_frame_ratio=ratios[(s->speech_count*99+99)/100-1];}
    pthread_mutex_unlock(&s->mutex);
    out.failures=atomic_load(&s->failures); out.stopped=atomic_load(&s->stop); out.tx_finished=atomic_load(&s->tx_finished);
    out.input_depth=freedv_capture_ring_stats(&s->input_queue).queued_samples;
    out.output_depth=freedv_capture_ring_stats(&s->output_queue).queued_samples;
    return out;
}
bool fd_stream_join(fd_rade_stream *s) {
    if(!s)return false;
    fd_stream_abort(s);
    if(s->receiver_started){if(pthread_join(s->receiver,NULL))return false;s->receiver_started=false;}
    if(s->renderer_started){if(pthread_join(s->renderer,NULL))return false;s->renderer_started=false;}
    return true;
}
void fd_stream_destroy(fd_rade_stream *s) {
    if (!s || !fd_stream_join(s)) return;
    if (s->encoder) lpcnet_encoder_destroy(s->encoder);
    if (s->input_src) src_delete(s->input_src);
    if (s->output_src) src_delete(s->output_src);
    if (s->rade) rade_close(s->rade);
    pthread_cond_destroy(&s->changed); pthread_mutex_destroy(&s->mutex); free(s);
}
