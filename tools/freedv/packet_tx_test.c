// SPDX-License-Identifier: LGPL-2.1-or-later
#include "src/freedv/packet_tx.h"
#include <assert.h>
#include <stdio.h>

int main(void) {
    freedv_packet_tx_tracker tracker = FREEDV_PACKET_TX_TRACKER_INIT;
    freedv_packet_tx_observation x = freedv_packet_tx_read(&tracker, 1000);
    assert(!x.known); /* No packet is not a received RX packet. */
    freedv_packet_tx_note(&tracker, true, 1000);
    x = freedv_packet_tx_read(&tracker, 1100);
    assert(x.known && x.tx && x.age_ms == 100); /* hardware TX can outlive software PTT */
    x = freedv_packet_tx_read(&tracker, 999);
    assert(!x.known); /* a mismatched or earlier clock cannot establish RX */
    x = freedv_packet_tx_read(&tracker, 8000);
    assert(x.known && x.tx && x.age_ms == 7000); /* age must remain visible to the caller */
    freedv_packet_tx_note(&tracker, false, 8100);
    x = freedv_packet_tx_read(&tracker, 8101);
    assert(x.known && !x.tx && x.age_ms == 1);
    assert(!pthread_mutex_destroy(&tracker.mutex));
    puts("PASS: packet TX is distinct, timestamped and unknown before a packet");
}
