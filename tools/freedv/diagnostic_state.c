// SPDX-License-Identifier: LGPL-2.1-or-later
#include "diagnostic_state.h"
#include <limits.h>

static bool observed_rx(fd_observation observed) {
    return observed.known && observed.fresh && !observed.tx;
}
bool fd_acquire(fd_session *s, bool busy, bool commands_quiescent, fd_observation observed) {
    if (!s || s->state != FD_IDLE || busy || !commands_quiescent || !observed_rx(observed)) return false;
    s->state = FD_READY;
    s->tx_case = false;
    s->software_cleared = false;
    s->deadline_ms = 0;
    s->clear_sequence = 0;
    return true;
}
bool fd_select(fd_session *s, bool tx_case) {
    if (!s || s->state != FD_READY) return false;
    s->tx_case = tx_case;
    return true;
}
bool fd_arm(fd_session *s, bool local) {
    if (!s || s->state != FD_READY || !s->tx_case || !local) return false;
    s->state = FD_ARMED;
    return true;
}
bool fd_run(fd_session *s, uint64_t now_ms, bool tx_permitted, fd_observation observed) {
    if (!s || (s->tx_case ? s->state != FD_ARMED : s->state != FD_READY) ||
        !observed_rx(observed) || (s->tx_case && !tx_permitted)) return false;
    if (s->tx_case && now_ms > UINT64_MAX - 10000) return false;
    s->deadline_ms = s->tx_case ? now_ms + 10000 : 0;
    s->state = FD_RUNNING;
    return true;
}
bool fd_stop(fd_session *s) {
    if (!s || (s->state != FD_RUNNING && s->state != FD_ARMED)) return false;
    s->state = FD_WAIT_RX;
    s->deadline_ms = 0;
    s->software_cleared = false;
    return true;
}
bool fd_tick(fd_session *s, uint64_t now_ms) {
    if (!s || s->state != FD_RUNNING || !s->tx_case || now_ms < s->deadline_ms) return false;
    return fd_stop(s);
}
bool fd_commands_cleared(fd_session *s, uint64_t latest_sequence) {
    if (!s || s->state != FD_WAIT_RX) return false;
    s->clear_sequence = latest_sequence;
    s->software_cleared = true;
    return true;
}
bool fd_rx_observed(fd_session *s, fd_observation observed) {
    if (!s || s->state != FD_WAIT_RX || !s->software_cleared || !observed_rx(observed) ||
        observed.sequence <= s->clear_sequence) return false;
    s->state = FD_RESTORE;
    return true;
}
bool fd_restored(fd_session *s) {
    if (!s || s->state != FD_RESTORE) return false;
    s->state = FD_READY;
    s->tx_case = false;
    s->software_cleared = false;
    return true;
}
bool fd_release(fd_session *s) {
    if (!s || s->state != FD_READY) return false;
    s->state = FD_IDLE;
    return true;
}
