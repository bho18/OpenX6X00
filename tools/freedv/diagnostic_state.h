// SPDX-License-Identifier: LGPL-2.1-or-later
#pragma once
#include <stdbool.h>
#include <stdint.h>

typedef enum { FD_IDLE, FD_READY, FD_ARMED, FD_RUNNING, FD_WAIT_RX, FD_RESTORE } fd_state;
typedef struct {
    bool known;
    bool fresh;       /* caller's explicit freshness limit, not inferred from !tx */
    bool tx;          /* baseband packet observation, not the software command */
    uint64_t sequence; /* changes for every received packet */
} fd_observation;
typedef struct {
    fd_state state;
    bool tx_case;
    bool software_cleared;
    uint64_t deadline_ms;
    uint64_t clear_sequence;
} fd_session;

/* Caller serializes all operations, acquires exclusive radio/audio ownership and
 * snapshots state under the radio lock. Do not use this prototype as the owner. */
bool fd_acquire(fd_session *s, bool busy, bool commands_quiescent, fd_observation observed);
bool fd_select(fd_session *s, bool tx_case);
/* The caller must establish the request came from local controls. */
bool fd_arm(fd_session *s, bool local);
/* The caller must recheck the transmit frequency and guards immediately before
 * running. Every potentially keyed matrix combination is a TX case. */
bool fd_run(fd_session *s, uint64_t now_ms, bool tx_permitted, fd_observation observed);
/* True requests immediate audio flush and clearing software MODEM/PTT. */
bool fd_stop(fd_session *s);
bool fd_tick(fd_session *s, uint64_t now_ms);
/* After the flush and command clear actually succeed, record the latest packet
 * sequence. A later packet is required before ordinary routing can return. */
bool fd_commands_cleared(fd_session *s, uint64_t latest_sequence);
/* Restore only after a known, fresh, post-clear packet reports TX=false. */
bool fd_rx_observed(fd_session *s, fd_observation observed);
/* Call only after successful restoration; otherwise the session stays blocked. */
bool fd_restored(fd_session *s);
bool fd_release(fd_session *s);
