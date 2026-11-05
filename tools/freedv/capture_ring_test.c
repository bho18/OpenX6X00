// SPDX-License-Identifier: LGPL-2.1-or-later
#include "src/freedv/capture_ring.h"
#include <assert.h>
#include <limits.h>
#include <pthread.h>
#include <sched.h>
#include <stdio.h>
#include <stdlib.h>

static freedv_capture_ring ring;
#define TOTAL 1000000u

static void *produce(void *unused) {
    (void)unused;
    int16_t chunk[173];
    unsigned sequence = 0;
    while (sequence < TOTAL) {
        size_t n = TOTAL - sequence < 173 ? TOTAL - sequence : 173;
        for (size_t i = 0; i < n; ++i) chunk[i] = (int16_t)((sequence + i) % 32768);
        size_t accepted = freedv_capture_ring_push(&ring, chunk, n);
        sequence += (unsigned)accepted;
        if (!accepted) sched_yield();
    }
    return NULL;
}

static void *consume(void *unused) {
    (void)unused;
    int16_t chunk[211];
    unsigned sequence = 0;
    while (sequence < TOTAL) {
        size_t count = freedv_capture_ring_pop(&ring, chunk, 211);
        for (size_t i = 0; i < count; ++i) {
            assert(chunk[i] == (int16_t)(sequence % 32768));
            ++sequence;
        }
        if (!count) sched_yield();
    }
    return NULL;
}

int main(void) {
    freedv_capture_ring_init(&ring);
    int16_t *data = malloc((FREEDV_CAPTURE_CAPACITY + 17u) * sizeof(*data));
    int16_t *out = malloc((FREEDV_CAPTURE_CAPACITY + 17u) * sizeof(*out));
    assert(data && out);
    for (unsigned i = 0; i < FREEDV_CAPTURE_CAPACITY + 17u; ++i) data[i] = (int16_t)(i % 32768);
    assert(freedv_capture_ring_pop(&ring, out, 1) == 0);
    assert(freedv_capture_ring_push(&ring, data, FREEDV_CAPTURE_CAPACITY + 17u) == FREEDV_CAPTURE_CAPACITY);
    freedv_capture_stats s = freedv_capture_ring_stats(&ring);
    assert(s.queued_samples == FREEDV_CAPTURE_CAPACITY && s.dropped_samples == 17 && !s.hole_samples);
    assert(freedv_capture_ring_pop(&ring, out, FREEDV_CAPTURE_CAPACITY) == FREEDV_CAPTURE_CAPACITY);
    for (unsigned i = 0; i < FREEDV_CAPTURE_CAPACITY; ++i) assert(out[i] == data[i]);
    assert(freedv_capture_ring_push(&ring, NULL, 25) == 25);
    assert(freedv_capture_ring_pop(&ring, out, 25) == 25);
    for (unsigned i = 0; i < 25; ++i) assert(out[i] == 0);
    s = freedv_capture_ring_stats(&ring);
    assert(s.queued_samples == 0 && s.dropped_samples == 17 && s.hole_samples == 25);

    /* Cross both the physical ring boundary and unsigned cursor wrap. */
    freedv_capture_ring_init(&ring);
    atomic_store(&ring.read_cursor, UINT_MAX - 20u);
    atomic_store(&ring.write_cursor, UINT_MAX - 20u);
    assert(freedv_capture_ring_push(&ring, data, 100) == 100);
    assert(freedv_capture_ring_pop(&ring, out, 100) == 100);
    for (unsigned i = 0; i < 100; ++i) assert(out[i] == data[i]);
    assert(freedv_capture_ring_stats(&ring).queued_samples == 0);

    freedv_capture_ring_init(&ring);
    pthread_t producer, consumer;
    assert(!pthread_create(&producer, NULL, produce, NULL));
    assert(!pthread_create(&consumer, NULL, consume, NULL));
    assert(!pthread_join(producer, NULL));
    assert(!pthread_join(consumer, NULL));
    assert(freedv_capture_ring_stats(&ring).queued_samples == 0);
    free(data); free(out);
    puts("PASS: bounded capture, overflow, holes, cursor wrap and concurrent ordered transfer");
    return 0;
}
