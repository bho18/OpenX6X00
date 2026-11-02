// SPDX-License-Identifier: LGPL-2.1-or-later
#define _POSIX_C_SOURCE 200809L
#include "src/freedv/capture_dispatch.h"
#include "src/audio.h"
#include <assert.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <stdio.h>
static freedv_capture_ring source;
static atomic_bool active;
static atomic_uint failures;
bool audio_freedv_capture_begin(void) { freedv_capture_ring_init(&source); return !atomic_exchange(&active, true); }
void audio_freedv_capture_end(void) { atomic_store(&active, false); }
size_t audio_freedv_capture_read(int16_t *out, size_t n) { return freedv_capture_ring_pop(&source, out, n); }
audio_freedv_capture_stats_t audio_freedv_capture_stats(void) {
    freedv_capture_stats s = freedv_capture_ring_stats(&source);
    return (audio_freedv_capture_stats_t){s.queued_samples, s.dropped_samples, s.hole_samples, 0};
}
static void failure(void *context, fd_capture_failure reason) {
    assert(context == &source && reason != FD_CAPTURE_OK);
    atomic_fetch_add(&failures, 1);
}
static void pause_short(void) { struct timespec t = {0, 2000000}; nanosleep(&t, NULL); }
int main(void) {
    fd_capture_dispatch *d = calloc(1, sizeof(*d)); assert(d);
    fd_capture_dispatch_init(d);
    assert(!fd_capture_dispatch_start(d, false, false, failure, &source));
    assert(fd_capture_dispatch_start(d, true, true, failure, &source));
    assert(!fd_capture_dispatch_start(d, true, true, failure, &source));
    int16_t input[4096], codec[4096], recording[4096];
    for (int i = 0; i < 4096; ++i) input[i] = (int16_t)(i * 17);
    assert(freedv_capture_ring_push(&source, input, 4096) == 4096);
    size_t a = 0, b = 0;
    for (unsigned retry = 0; retry < 2500 && (a < 4096 || b < 4096); ++retry) {
        a += fd_capture_dispatch_read(d, FD_CAPTURE_CODEC, codec + a, 4096 - a);
        b += fd_capture_dispatch_read(d, FD_CAPTURE_RECORDING, recording + b, 4096 - b);
        pause_short();
    }
    assert(a == 4096 && b == 4096 && !memcmp(input, codec, sizeof(input)) && !memcmp(input, recording, sizeof(input)));
    assert(!atomic_load(&failures));
    /* Stall both consumers. The codec's half-second limit must abort rather
     * than overwrite samples or wait for a blocked disk consumer. */
    for (unsigned n = 0; n < 60 && atomic_load(&active); ++n) {
        assert(freedv_capture_ring_push(&source, input, 441) == 441);
        pause_short();
    }
    for (unsigned retry = 0; retry < 2500 && !atomic_load(&failures); ++retry) pause_short();
    assert(atomic_load(&failures) == 1 && atomic_load(&d->failure) == FD_CAPTURE_BACKLOG);
    assert(fd_capture_dispatch_join(d) && !atomic_load(&active));
    assert(!fd_capture_dispatch_join(d));
    assert(fd_capture_dispatch_start(d, true, false, failure, &source));
    freedv_capture_ring_push(&source, NULL, 1);
    for (unsigned retry = 0; retry < 2500 && atomic_load(&failures) < 2; ++retry) pause_short();
    assert(atomic_load(&failures) == 2 && atomic_load(&d->failure) == FD_CAPTURE_SOURCE);
    assert(fd_capture_dispatch_join(d));
    free(d);
    puts("PASS: identical codec/recording streams, bounded stalled consumers, source holes and restart");
}
