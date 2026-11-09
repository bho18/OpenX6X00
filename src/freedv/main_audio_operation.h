// SPDX-License-Identifier: LGPL-2.1-or-later
#pragma once
#include "../audio.h"
#include <pulse/pulseaudio.h>
#include <stdatomic.h>
#include <time.h>

/* This is shared by the production wrapper and the real-PulseAudio fixture.
 * The caller owns stream lifetime and runs it on its one quiet worker, never
 * the GUI, radio or PA callback thread. A stuck mainloop lock is handled by the
 * outer coordinator deadline; its live worker/context must not be freed. */
static inline uint64_t fd_main_audio_ns(void) {
    struct timespec now;
    clock_gettime(CLOCK_MONOTONIC, &now);
    return (uint64_t)now.tv_sec * 1000000000u + (uint64_t)now.tv_nsec;
}
static inline void fd_main_audio_operation_done(pa_stream *stream, int success, void *context) {
    (void)stream;
    atomic_store_explicit((atomic_int *)context, success ? 1 : -1, memory_order_release);
}
static inline audio_main_quiet_result_t fd_main_audio_operation(pa_threaded_mainloop *loop,
                                                               pa_stream *stream, bool quiet) {
    if (!loop || !stream) return AUDIO_MAIN_LOST;
    uint64_t start = fd_main_audio_ns();
    atomic_int cork_status, flush_status;
    atomic_init(&cork_status, 0);
    atomic_init(&flush_status, quiet ? 0 : 1);
    pa_operation *cork = NULL, *flush = NULL;
    audio_main_quiet_result_t result = AUDIO_MAIN_ACKNOWLEDGED;
    pa_threaded_mainloop_lock(loop);
    if (pa_stream_get_state(stream) != PA_STREAM_READY) result = AUDIO_MAIN_LOST;
    else if (fd_main_audio_ns() - start >= 500000000u) result = AUDIO_MAIN_TIMED_OUT;
    else {
        cork = pa_stream_cork(stream, quiet, fd_main_audio_operation_done, &cork_status);
        if (quiet) flush = pa_stream_flush(stream, fd_main_audio_operation_done, &flush_status);
        if (!cork || (quiet && !flush)) result = AUDIO_MAIN_FAILED;
    }
    pa_threaded_mainloop_unlock(loop);
    while (result == AUDIO_MAIN_ACKNOWLEDGED &&
           (!atomic_load_explicit(&cork_status, memory_order_acquire) ||
            !atomic_load_explicit(&flush_status, memory_order_acquire))) {
        if (atomic_load(&cork_status) < 0 || atomic_load(&flush_status) < 0) {
            result = AUDIO_MAIN_FAILED;
            break;
        }
        if (fd_main_audio_ns() - start >= 500000000u) {
            result = AUDIO_MAIN_TIMED_OUT;
            break;
        }
        struct timespec pause = {0, 2000000};
        nanosleep(&pause, NULL);
    }
    /* Cancellation and callback lifetime fencing share PA's lock. No callback
     * can retain a pointer to these stack atomics after this function returns. */
    pa_threaded_mainloop_lock(loop);
    if (pa_stream_get_state(stream) != PA_STREAM_READY) result = AUDIO_MAIN_LOST;
    else if (result == AUDIO_MAIN_ACKNOWLEDGED && fd_main_audio_ns() - start >= 500000000u)
        result = AUDIO_MAIN_TIMED_OUT;
    if (result == AUDIO_MAIN_ACKNOWLEDGED &&
        (atomic_load(&cork_status) != 1 || atomic_load(&flush_status) != 1)) result = AUDIO_MAIN_FAILED;
    if (cork) { pa_operation_cancel(cork); pa_operation_unref(cork); }
    if (flush) { pa_operation_cancel(flush); pa_operation_unref(flush); }
    pa_threaded_mainloop_unlock(loop);
    return result;
}
