// SPDX-License-Identifier: LGPL-2.1-or-later
#include "diagnostic_state.h"
#include <assert.h>
#include <stdio.h>

int main(void) {
    fd_session s = {0};
    assert(!fd_acquire(&s, true, false));
    assert(!fd_acquire(&s, false, true));
    assert(fd_acquire(&s, false, false));
    assert(fd_select(&s, true));
    assert(!fd_run(&s, 1)); /* selection cannot key */
    assert(fd_arm(&s));
    assert(fd_run(&s, 100));
    assert(!fd_tick(&s, 10099));
    assert(fd_tick(&s, 10100));
    assert(!fd_rx_observed(&s, true)); /* held hardware PTT */
    assert(!fd_release(&s));
    assert(fd_rx_observed(&s, false));
    assert(!fd_release(&s));
    assert(!fd_run(&s, 10101));
    assert(fd_restored(&s));
    assert(fd_release(&s));
    assert(fd_acquire(&s, false, false));
    assert(fd_select(&s, false));
    assert(fd_run(&s, 0));
    assert(fd_stop(&s));
    assert(fd_rx_observed(&s, false));
    assert(fd_restored(&s));
    assert(fd_release(&s));
    puts("PASS: diagnostic session arming, timeout, held PTT and restoration gate");
}
