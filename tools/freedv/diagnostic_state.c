// SPDX-License-Identifier: LGPL-2.1-or-later
#include "diagnostic_state.h"
#include <limits.h>

bool fd_acquire(fd_session *s, bool busy, bool observed_tx) {
    if (!s || s->state != FD_IDLE || busy || observed_tx) return false;
    s->state = FD_READY;
    s->tx_case = false;
    s->deadline_ms = 0;
    return true;
}
bool fd_select(fd_session *s, bool tx_case) {
    if (!s || s->state != FD_READY) return false;
    s->tx_case = tx_case;
    return true;
}
bool fd_arm(fd_session *s) {
    if (!s || s->state != FD_READY || !s->tx_case) return false;
    s->state = FD_ARMED;
    return true;
}
bool fd_run(fd_session *s, uint64_t now_ms) {
    if (!s || (s->tx_case ? s->state != FD_ARMED : s->state != FD_READY)) return false;
    if (s->tx_case && now_ms > UINT64_MAX - 10000) return false;
    s->deadline_ms = s->tx_case ? now_ms + 10000 : 0;
    s->state = FD_RUNNING;
    return true;
}
bool fd_stop(fd_session *s) {
    if (!s || (s->state != FD_RUNNING && s->state != FD_ARMED)) return false;
    s->state = FD_WAIT_RX;
    s->deadline_ms = 0;
    return true;
}
bool fd_tick(fd_session *s, uint64_t now_ms) {
    if (!s || s->state != FD_RUNNING || !s->tx_case || now_ms < s->deadline_ms) return false;
    return fd_stop(s);
}
bool fd_rx_observed(fd_session *s, bool observed_tx) {
    if (!s || s->state != FD_WAIT_RX || observed_tx) return false;
    s->state = FD_RESTORE;
    return true;
}
bool fd_restored(fd_session *s) {
    if (!s || s->state != FD_RESTORE) return false;
    s->state = FD_READY;
    s->tx_case = false;
    return true;
}
bool fd_release(fd_session *s) {
    if (!s || s->state != FD_READY) return false;
    s->state = FD_IDLE;
    return true;
}
