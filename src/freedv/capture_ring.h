// SPDX-License-Identifier: LGPL-2.1-or-later
#pragma once

#include <stdatomic.h>
#include <stddef.h>
#include <stdint.h>
#include <limits.h>

/* One PulseAudio producer and one background consumer. The capacity is a
 * power of two and must remain well below half the unsigned cursor range. */
#define FREEDV_CAPTURE_CAPACITY 65536u
#define FREEDV_CAPTURE_QUEUE_UNAVAILABLE UINT_MAX
#if ATOMIC_INT_LOCK_FREE != 2
#error "The diagnostic audio callback requires lock-free unsigned atomics"
#endif

typedef struct {
    int16_t data[FREEDV_CAPTURE_CAPACITY];
    atomic_uint read_cursor;
    atomic_uint write_cursor;
    atomic_uint dropped_samples;
    atomic_uint hole_samples;
} freedv_capture_ring;

typedef struct {
    unsigned queued_samples; /* FREEDV_CAPTURE_QUEUE_UNAVAILABLE if sampled during an inconsistent transition */
    unsigned dropped_samples;
    unsigned hole_samples;
} freedv_capture_stats;

/* Call init only while both producer and consumer are stopped. */
void freedv_capture_ring_init(freedv_capture_ring *ring);
/* Producer: returns stored samples; excess is counted as dropped. NULL input
 * means a PulseAudio hole, represented by silence and separately counted. */
size_t freedv_capture_ring_push(freedv_capture_ring *ring, const int16_t *samples, size_t count);
/* Exactly one consumer; returns immediately, without file or device I/O. */
size_t freedv_capture_ring_pop(freedv_capture_ring *ring, int16_t *out, size_t capacity);
freedv_capture_stats freedv_capture_ring_stats(const freedv_capture_ring *ring);
