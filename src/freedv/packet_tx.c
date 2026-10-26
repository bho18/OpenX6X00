// SPDX-License-Identifier: LGPL-2.1-or-later
#include "packet_tx.h"

void freedv_packet_tx_note(freedv_packet_tx_tracker *tracker, bool tx, uint64_t received_ms) {
    pthread_mutex_lock(&tracker->mutex);
    tracker->valid = true;
    tracker->tx = tx;
    tracker->received_ms = received_ms;
    pthread_mutex_unlock(&tracker->mutex);
}

freedv_packet_tx_observation freedv_packet_tx_read(freedv_packet_tx_tracker *tracker, uint64_t now_ms) {
    pthread_mutex_lock(&tracker->mutex);
    freedv_packet_tx_observation out = {0};
    if (tracker->valid && now_ms >= tracker->received_ms) {
        out.known = true;
        out.tx = tracker->tx;
        out.age_ms = now_ms - tracker->received_ms;
    }
    pthread_mutex_unlock(&tracker->mutex);
    return out;
}
