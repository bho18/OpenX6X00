// SPDX-License-Identifier: LGPL-2.1-or-later
#pragma once
#include <stdbool.h>
#include <stdint.h>
#include <pthread.h>

/* A packet observation, independent of software PTT commands and radio state.
 * Never treat an unknown or stale observation as evidence that RF is off. */
typedef struct {
    pthread_mutex_t mutex;
    bool valid;
    bool tx;
    uint64_t received_ms;
} freedv_packet_tx_tracker;
#define FREEDV_PACKET_TX_TRACKER_INIT { PTHREAD_MUTEX_INITIALIZER, false, false, 0 }

typedef struct {
    bool known;
    bool tx;
    uint64_t age_ms;
} freedv_packet_tx_observation;

void freedv_packet_tx_note(freedv_packet_tx_tracker *tracker, bool tx, uint64_t received_ms);
/* now_ms must use the same monotonic clock as received_ms. The caller decides
 * its maximum acceptable age; this function does not declare RF safe. */
freedv_packet_tx_observation freedv_packet_tx_read(freedv_packet_tx_tracker *tracker, uint64_t now_ms);
