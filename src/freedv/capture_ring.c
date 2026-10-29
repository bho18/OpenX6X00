// SPDX-License-Identifier: LGPL-2.1-or-later
#include "capture_ring.h"
#include <limits.h>
#include <string.h>

_Static_assert((FREEDV_CAPTURE_CAPACITY & (FREEDV_CAPTURE_CAPACITY - 1)) == 0, "capacity must be a power of two");
_Static_assert(FREEDV_CAPTURE_CAPACITY < UINT_MAX / 2, "capacity exceeds cursor range");

void freedv_capture_ring_init(freedv_capture_ring *ring) {
    atomic_init(&ring->read_cursor, 0);
    atomic_init(&ring->write_cursor, 0);
    atomic_init(&ring->dropped_samples, 0);
    atomic_init(&ring->hole_samples, 0);
}

/* Only the producer updates these counters; readers may inspect them at any
 * time. Saturation avoids making a long-running overflow look like zero. */
static void add_saturated(atomic_uint *counter, size_t amount) {
    unsigned old = atomic_load_explicit(counter, memory_order_relaxed);
    unsigned next = amount > UINT_MAX - old ? UINT_MAX : old + (unsigned)amount;
    atomic_store_explicit(counter, next, memory_order_relaxed);
}

size_t freedv_capture_ring_push(freedv_capture_ring *ring, const int16_t *samples, size_t count) {
    unsigned write = atomic_load_explicit(&ring->write_cursor, memory_order_relaxed);
    unsigned read = atomic_load_explicit(&ring->read_cursor, memory_order_acquire);
    unsigned used = write - read;
    if (used > FREEDV_CAPTURE_CAPACITY) return 0;  /* caller violated the one-producer/one-consumer contract */
    size_t space = FREEDV_CAPTURE_CAPACITY - used;
    size_t stored = count < space ? count : space;
    size_t index = write & (FREEDV_CAPTURE_CAPACITY - 1);
    size_t first = stored < FREEDV_CAPTURE_CAPACITY - index ? stored : FREEDV_CAPTURE_CAPACITY - index;
    if (samples) {
        if (first) memcpy(ring->data + index, samples, first * sizeof(*samples));
        if (stored > first) memcpy(ring->data, samples + first, (stored - first) * sizeof(*samples));
    } else {
        if (first) memset(ring->data + index, 0, first * sizeof(*ring->data));
        if (stored > first) memset(ring->data, 0, (stored - first) * sizeof(*ring->data));
        add_saturated(&ring->hole_samples, count);
    }
    add_saturated(&ring->dropped_samples, count - stored);
    atomic_store_explicit(&ring->write_cursor, write + (unsigned)stored, memory_order_release);
    return stored;
}

size_t freedv_capture_ring_pop(freedv_capture_ring *ring, int16_t *out, size_t capacity) {
    unsigned read = atomic_load_explicit(&ring->read_cursor, memory_order_relaxed);
    unsigned write = atomic_load_explicit(&ring->write_cursor, memory_order_acquire);
    unsigned available = write - read;
    if (available > FREEDV_CAPTURE_CAPACITY || (!out && capacity)) return 0;
    size_t count = capacity < available ? capacity : available;
    size_t index = read & (FREEDV_CAPTURE_CAPACITY - 1);
    size_t first = count < FREEDV_CAPTURE_CAPACITY - index ? count : FREEDV_CAPTURE_CAPACITY - index;
    if (first) memcpy(out, ring->data + index, first * sizeof(*out));
    if (count > first) memcpy(out + first, ring->data, (count - first) * sizeof(*out));
    atomic_store_explicit(&ring->read_cursor, read + (unsigned)count, memory_order_release);
    return count;
}

freedv_capture_stats freedv_capture_ring_stats(const freedv_capture_ring *ring) {
    /* A simultaneous consumer may move the read cursor between these loads.
     * This is an observation, not a synchronization primitive. */
    unsigned read = atomic_load_explicit(&ring->read_cursor, memory_order_acquire);
    unsigned write = atomic_load_explicit(&ring->write_cursor, memory_order_acquire);
    unsigned queued = write - read;
    if (queued > FREEDV_CAPTURE_CAPACITY) queued = FREEDV_CAPTURE_QUEUE_UNAVAILABLE;
    return (freedv_capture_stats){queued,
        atomic_load_explicit(&ring->dropped_samples, memory_order_relaxed),
        atomic_load_explicit(&ring->hole_samples, memory_order_relaxed)};
}
