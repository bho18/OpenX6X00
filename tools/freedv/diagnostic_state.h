// SPDX-License-Identifier: LGPL-2.1-or-later
#pragma once
#include <stdbool.h>
#include <stdint.h>

typedef enum { FD_IDLE, FD_READY, FD_ARMED, FD_RUNNING, FD_WAIT_RX, FD_RESTORE } fd_state;
typedef struct {
    fd_state state;
    bool tx_case;
    uint64_t deadline_ms;
} fd_session;

/* Return false without changing state when preconditions are not satisfied. */
bool fd_acquire(fd_session *s, bool busy, bool observed_tx);
bool fd_select(fd_session *s, bool tx_case);
bool fd_arm(fd_session *s);
bool fd_run(fd_session *s, uint64_t now_ms);
/* True requests that the caller stop audio and clear software TX immediately. */
bool fd_stop(fd_session *s);
bool fd_tick(fd_session *s, uint64_t now_ms);
/* Restore snapshots only after this returns true. */
bool fd_rx_observed(fd_session *s, bool observed_tx);
/* Call only after successful restoration; otherwise the session stays blocked. */
bool fd_restored(fd_session *s);
bool fd_release(fd_session *s);
