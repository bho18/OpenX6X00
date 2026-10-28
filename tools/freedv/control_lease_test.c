// SPDX-License-Identifier: LGPL-2.1-or-later
#include "src/control_owner.h"
#include "src/radio.h"
#include "src/freedv/writer_gate.h"
#include <assert.h>
#include <pthread.h>
#include <stdatomic.h>
#include <stdio.h>

static radio_state_t test_radio_state = RADIO_RX;
static atomic_uint incorrect_claims;
radio_state_t radio_get_state(void) { return test_radio_state; }

static void *remote_claims(void *arg) {
    ctl_kind_t kind = *(ctl_kind_t *)arg;
    for (int i = 0; i < 10000; ++i) {
        if (ctl_claim(kind, 7, "remote", true)) atomic_fetch_add(&incorrect_claims, 1);
        ctl_release(kind, 7);
    }
    return NULL;
}
int main(void) {
    ctl_init();
    ctl_info_t info;
    assert(ctl_claim(CTL_BROWSER, 2, "remote", false));
    assert(!ctl_diagnostic_acquire()); /* refuse a competing remote owner */
    ctl_release(CTL_BROWSER, 2);
    test_radio_state = RADIO_TX;
    assert(!ctl_diagnostic_acquire());
    test_radio_state = RADIO_ATU_WAIT;
    assert(!ctl_diagnostic_acquire());
    test_radio_state = RADIO_RX;
    assert(ctl_claim(CTL_LOCAL, 0, "", false));
    assert(fd_writer_enter());
    assert(fd_writer_enter()); /* nested setters must retain the outer admission */
    assert(!ctl_diagnostic_acquire());
    fd_writer_exit();
    assert(!ctl_diagnostic_acquire());
    fd_writer_exit();
    assert(ctl_diagnostic_acquire());
    assert(!fd_writer_enter());
    assert(!ctl_diagnostic_acquire());
    assert(!ctl_claim(CTL_DIAGNOSTIC, 0, "bypass", true));
    assert(!ctl_claim(CTL_LOCAL, 0, "local", true));
    ctl_local_input();
    ctl_release(CTL_DIAGNOSTIC, 0);
    ctl_get(&info);
    assert(info.kind == CTL_DIAGNOSTIC);
    ctl_kind_t kinds[] = {CTL_TCI, CTL_BROWSER};
    pthread_t threads[2];
    assert(!pthread_create(&threads[0], NULL, remote_claims, &kinds[0]));
    assert(!pthread_create(&threads[1], NULL, remote_claims, &kinds[1]));
    assert(!pthread_join(threads[0], NULL));
    assert(!pthread_join(threads[1], NULL));
    assert(atomic_load(&incorrect_claims) == 0);
    ctl_get(&info);
    assert(info.kind == CTL_DIAGNOSTIC);
    assert(!ctl_diagnostic_release(false));
    test_radio_state = RADIO_TX;
    assert(!ctl_diagnostic_release(true));
    test_radio_state = RADIO_RX;
    assert(ctl_diagnostic_release(true));
    ctl_get(&info);
    assert(info.kind == CTL_LOCAL);
    assert(ctl_claim(CTL_BROWSER, 2, "remote", true));
    ctl_get(&info);
    assert(info.kind == CTL_BROWSER && info.id == 2);
    ctl_release(CTL_BROWSER, 2);
    assert(ctl_diagnostic_acquire());
    assert(ctl_diagnostic_release(true));
    ctl_get(&info);
    assert(info.kind == CTL_NONE);
    puts("PASS: diagnostic lease rejects competing owners and remote takeovers");
}
