// SPDX-License-Identifier: LGPL-2.1-or-later
#define _POSIX_C_SOURCE 200809L
#include "capture_dispatch.h"
#include "../audio.h"
#include <string.h>
#include <time.h>

void fd_capture_dispatch_init(fd_capture_dispatch *d) {
    memset(d, 0, sizeof(*d));
    atomic_init(&d->stop, false);
    atomic_init(&d->failure, FD_CAPTURE_OK);
    for (unsigned i = 0; i < FD_CAPTURE_READERS; ++i) freedv_capture_ring_init(&d->queues[i]);
}
static void fail(fd_capture_dispatch *d, fd_capture_failure failure) {
    atomic_store_explicit(&d->failure, failure, memory_order_release);
    audio_freedv_capture_end();
    atomic_store_explicit(&d->stop, true, memory_order_release);
    d->on_failure(d->context, failure); /* request RF stop, never join this worker */
}
static void *dispatch(void *arg) {
    fd_capture_dispatch *d = arg;
    int16_t samples[441];
    while (!atomic_load_explicit(&d->stop, memory_order_acquire)) {
        audio_freedv_capture_stats_t source = audio_freedv_capture_stats();
        if (source.dropped_samples || source.hole_samples || source.capture_errors) {
            fail(d, FD_CAPTURE_SOURCE); break;
        }
        /* Raw storage is larger than the allowed processing backlog. */
        if (source.queued_samples != FREEDV_CAPTURE_QUEUE_UNAVAILABLE &&
            source.queued_samples > AUDIO_CAPTURE_RATE / 2) {
            fail(d, FD_CAPTURE_BACKLOG); break;
        }
        size_t count = audio_freedv_capture_read(samples, 441);
        if (!count) {
            struct timespec delay = {0, 2000000}; nanosleep(&delay, NULL); continue;
        }
        for (unsigned i = 0; i < FD_CAPTURE_READERS; ++i) {
            if (!d->enabled[i]) continue;
            freedv_capture_stats stats = freedv_capture_ring_stats(&d->queues[i]);
            unsigned limit = i == FD_CAPTURE_CODEC ? AUDIO_CAPTURE_RATE / 2 : AUDIO_CAPTURE_RATE;
            /* Only this producer advances write_cursor. A concurrent consumer
             * can reduce depth, so the observed depth is a safe upper bound. */
            if (stats.queued_samples == FREEDV_CAPTURE_QUEUE_UNAVAILABLE ||
                stats.queued_samples + count > limit ||
                freedv_capture_ring_push(&d->queues[i], samples, count) != count) {
                fail(d, FD_CAPTURE_BACKLOG); return NULL;
            }
        }
    }
    return NULL;
}
bool fd_capture_dispatch_start(fd_capture_dispatch *d, bool codec, bool recording,
                               fd_capture_failure_fn failure, void *context) {
    if (!d || d->started || (!codec && !recording) || !failure) return false;
    for (unsigned i = 0; i < FD_CAPTURE_READERS; ++i) freedv_capture_ring_init(&d->queues[i]);
    d->enabled[FD_CAPTURE_CODEC] = codec;
    d->enabled[FD_CAPTURE_RECORDING] = recording;
    d->on_failure = failure; d->context = context;
    atomic_store(&d->failure, FD_CAPTURE_OK); atomic_store(&d->stop, false);
    if (!audio_freedv_capture_begin()) return false;
    if (pthread_create(&d->thread, NULL, dispatch, d)) { audio_freedv_capture_end(); return false; }
    d->started = true;
    return true;
}
void fd_capture_dispatch_cancel(fd_capture_dispatch *d) {
    if (!d) return;
    audio_freedv_capture_end();
    atomic_store_explicit(&d->stop, true, memory_order_release);
}
bool fd_capture_dispatch_join(fd_capture_dispatch *d) {
    if (!d || !d->started) return false;
    fd_capture_dispatch_cancel(d);
    if (pthread_join(d->thread, NULL)) return false;
    d->started = false;
    return true;
}
size_t fd_capture_dispatch_read(fd_capture_dispatch *d, fd_capture_reader reader,
                                int16_t *samples, size_t capacity) {
    if (!d || (unsigned)reader >= FD_CAPTURE_READERS || !d->enabled[reader]) return 0;
    return freedv_capture_ring_pop(&d->queues[reader], samples, capacity);
}
