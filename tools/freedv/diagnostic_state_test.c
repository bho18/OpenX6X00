// SPDX-License-Identifier: LGPL-2.1-or-later
#include "diagnostic_state.h"
#include <assert.h>
#include <stdio.h>

int main(void) {
    fd_session s = {0};
    fd_observation rx = {true, true, false, 10};
    fd_observation tx = {true, true, true, 11};
    fd_observation unknown = {false, false, false, 0};
    fd_observation stale = {true, false, false, 10};
    assert(!fd_acquire(&s, true, true, rx));
    assert(!fd_acquire(&s, false, false, rx));
    assert(!fd_acquire(&s, false, true, tx));
    assert(!fd_acquire(&s, false, true, unknown));
    assert(!fd_acquire(&s, false, true, stale));
    assert(fd_acquire(&s, false, true, rx));
    assert(fd_select(&s, true));
    assert(!fd_run(&s, 1, true, rx)); /* case selection cannot key */
    assert(!fd_arm(&s, false)); /* remote arming refused */
    assert(fd_arm(&s, true));
    assert(!fd_select(&s, false));
    assert(!fd_run(&s, 1, false, rx)); /* frequency/guard refused */
    assert(!fd_run(&s, 1, true, tx)); /* hardware PTT became active */
    assert(!fd_run(&s, 1, true, unknown));
    assert(!fd_run(&s, 1, true, stale));
    assert(!fd_run(&s, UINT64_MAX - 9999, true, rx));
    assert(s.state == FD_ARMED);
    assert(fd_run(&s, 100, true, rx));
    assert(!fd_acquire(&s, false, true, rx));
    assert(!fd_arm(&s, true));
    assert(!fd_tick(&s, 10099));
    assert(fd_tick(&s, 10100)); /* caller must flush and clear commands now */
    assert(!fd_rx_observed(&s, rx)); /* no successful clear yet */
    assert(!fd_release(&s));
    assert(fd_commands_cleared(&s, 11));
    assert(!fd_rx_observed(&s, (fd_observation){true, true, true, 12})); /* held hardware PTT */
    assert(!fd_rx_observed(&s, (fd_observation){false, false, false, 12}));
    assert(!fd_rx_observed(&s, (fd_observation){true, false, false, 12}));
    assert(!fd_rx_observed(&s, (fd_observation){true, true, false, 11})); /* not after clear */
    assert(fd_rx_observed(&s, (fd_observation){true, true, false, 12}));
    assert(!fd_release(&s));
    assert(!fd_run(&s, 10101, true, rx));
    assert(fd_restored(&s));
    assert(fd_release(&s));
    assert(!fd_restored(&s));
    assert(!fd_stop(&s));
    assert(fd_acquire(&s, false, true, rx));
    assert(fd_select(&s, false));
    assert(fd_run(&s, 0, false, rx));
    assert(fd_stop(&s));
    assert(fd_commands_cleared(&s, 10));
    assert(fd_rx_observed(&s, (fd_observation){true, true, false, 11}));
    assert(fd_restored(&s));
    assert(fd_release(&s));
    puts("PASS: session arming, guards, timeout, fresh post-clear RX and held PTT");
}
