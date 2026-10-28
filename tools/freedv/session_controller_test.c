// SPDX-License-Identifier: LGPL-2.1-or-later
#define _POSIX_C_SOURCE 200809L
#include "session_controller.h"
#include <assert.h>
#include <pthread.h>
#include <stdio.h>
#include <time.h>

typedef struct {
    pthread_mutex_t mutex;
    pthread_cond_t changed;
    bool owned, busy, quiescent, allowed, start_ok, clear_ok, restore_ok;
    bool software_tx, route_changed, audio_pending;
    fd_observation observed;
    unsigned starts, clears, restores, releases, observations;
    int last_case;
} fake;
static bool acquire(void *arg, bool *quiet, fd_observation *observed) {
    fake *f = arg; pthread_mutex_lock(&f->mutex);
    bool ok = !f->busy && !f->owned;
    if (ok) { f->owned = true; *quiet = f->quiescent; *observed = f->observed; }
    pthread_mutex_unlock(&f->mutex); return ok;
}
static void release(void *arg) {
    fake *f = arg; pthread_mutex_lock(&f->mutex);
    assert(f->owned && !f->software_tx && !f->route_changed);
    f->owned = false; ++f->releases;
    pthread_mutex_unlock(&f->mutex);
}
static bool observe(void *arg, fd_observation *observed) {
    fake *f = arg; pthread_mutex_lock(&f->mutex); *observed = f->observed;
    ++f->observations; pthread_cond_broadcast(&f->changed);
    pthread_mutex_unlock(&f->mutex); return true;
}
static bool permitted(void *arg) {
    fake *f = arg; pthread_mutex_lock(&f->mutex); bool ok = f->allowed;
    pthread_mutex_unlock(&f->mutex); return ok;
}
static bool start(void *arg, const fd_case *selected) {
    int which = selected->matrix_case;
    fake *f = arg; pthread_mutex_lock(&f->mutex);
    assert(f->owned); ++f->starts; f->last_case = which;
    if (which >= 0) { f->software_tx = selected->kind != FD_CASE_RX_PLAYBACK; f->route_changed = true; }
    bool ok = f->start_ok;
    pthread_mutex_unlock(&f->mutex); return ok;
}
static bool quiesce(void *arg, uint64_t *sequence) {
    fake *f = arg; pthread_mutex_lock(&f->mutex);
    assert(f->owned); ++f->clears;
    bool ok = f->clear_ok;
    if (ok) { f->software_tx = false; *sequence = f->observed.sequence; }
    pthread_cond_broadcast(&f->changed);
    pthread_mutex_unlock(&f->mutex); return ok;
}
static bool restore(void *arg, uint64_t clear_sequence) {
    fake *f = arg; pthread_mutex_lock(&f->mutex);
    bool ok = f->owned && !f->software_tx && f->restore_ok && f->observed.known && f->observed.fresh &&
              !f->observed.tx && f->observed.sequence > clear_sequence;
    if (ok) { f->route_changed = false; ++f->restores; }
    pthread_mutex_unlock(&f->mutex); return ok;
}
static void set_observed(fake *f, bool known, bool fresh, bool tx, uint64_t seq) {
    pthread_mutex_lock(&f->mutex); f->observed = (fd_observation){known, fresh, tx, seq}; pthread_mutex_unlock(&f->mutex);
}
static void wait_for_clear(fake *f, unsigned count) {
    struct timespec limit; assert(!clock_gettime(CLOCK_REALTIME, &limit)); limit.tv_sec += 5;
    pthread_mutex_lock(&f->mutex);
    while (f->clears < count) assert(!pthread_cond_timedwait(&f->changed, &f->mutex, &limit));
    pthread_mutex_unlock(&f->mutex);
}
static bool audio_quiet(void *arg) {
    fake *f = arg; pthread_mutex_lock(&f->mutex); bool ok = !f->audio_pending;
    pthread_mutex_unlock(&f->mutex); return ok;
}
static void wait_for_observation(fake *f, unsigned count) {
    struct timespec limit; assert(!clock_gettime(CLOCK_REALTIME, &limit)); limit.tv_sec += 5;
    pthread_mutex_lock(&f->mutex);
    while (f->observations < count) assert(!pthread_cond_timedwait(&f->changed, &f->mutex, &limit));
    pthread_mutex_unlock(&f->mutex);
}
static bool wait_restore(fd_controller *c) {
    for (unsigned i = 0; i < 2500; ++i) {
        if (fd_controller_poll_restore(c)) return true;
        struct timespec delay = {0, 2000000}; nanosleep(&delay, NULL);
    }
    return false;
}
int main(void) {
    fake f = {.mutex=PTHREAD_MUTEX_INITIALIZER, .changed=PTHREAD_COND_INITIALIZER,
              .quiescent=true, .allowed=true, .start_ok=true, .clear_ok=true, .restore_ok=true,
              .observed={true, true, false, 1}};
    fd_controller_backend backend = {acquire, release, observe, permitted, start, quiesce, audio_quiet, restore};
    fd_controller c;
    assert(!fd_controller_init(&c, NULL, &f));
    assert(fd_controller_init(&c, &backend, &f));
    f.busy = true; assert(!fd_controller_acquire(&c)); f.busy = false;
    f.quiescent = false; assert(!fd_controller_acquire(&c)); f.quiescent = true;
    set_observed(&f, false, false, false, 0); assert(!fd_controller_acquire(&c));
    set_observed(&f, true, false, false, 1); assert(!fd_controller_acquire(&c));
    set_observed(&f, true, true, true, 1); assert(!fd_controller_acquire(&c));
    set_observed(&f, true, true, false, 2); assert(fd_controller_acquire(&c));
    assert(!fd_controller_close(&c));
    assert(!fd_controller_run(&c, 10, 10000)); /* no selection */
    assert(fd_controller_select_matrix(&c, FD_R1_M0_P1));
    assert(!f.starts && !f.software_tx); /* selection cannot key */
    assert(!fd_controller_run(&c, 10, 10000)); /* no arm */
    assert(!fd_controller_arm(&c, false));
    assert(fd_controller_arm(&c, true));
    f.allowed = false; assert(!fd_controller_run(&c, 10, 10000)); f.allowed = true;
    set_observed(&f, true, true, true, 3); assert(!fd_controller_run(&c, 10, 10000));
    set_observed(&f, true, true, false, 4);
    assert(!fd_controller_run(&c, 10, 10001));
    assert(fd_controller_run(&c, 10, 10000));
    assert(f.starts == 1 && f.software_tx && f.last_case == FD_R1_M0_P1);
    set_observed(&f, true, true, true, 5); /* hardware PTT remains held */
    f.clear_ok = false;
    assert(!fd_controller_stop(&c));
    assert(!fd_controller_release(&c));
    f.clear_ok = true;
    assert(!fd_controller_poll_restore(&c));
    assert(!f.software_tx && f.route_changed && f.restores == 0);
    set_observed(&f, true, false, false, 6); assert(!fd_controller_poll_restore(&c));
    set_observed(&f, true, true, false, 5); assert(!fd_controller_poll_restore(&c)); /* not post-clear */
    set_observed(&f, true, true, false, 6);
    f.restore_ok = false;
    assert(!fd_controller_poll_restore(&c)); /* failed restore requires another clear and newer packet */
    f.restore_ok = true;
    assert(!fd_controller_poll_restore(&c));
    set_observed(&f, true, true, false, 7);
    assert(fd_controller_poll_restore(&c));
    assert(!f.route_changed && f.restores == 1);
    assert(fd_controller_release(&c));

    /* A failed start may have changed the route: it must clear before restore. */
    assert(fd_controller_acquire(&c));
    assert(fd_controller_select_matrix(&c, FD_R0_M1_P0) && fd_controller_arm(&c, true));
    f.start_ok = false;
    assert(!fd_controller_run(&c, 20, 10000));
    assert(!f.software_tx && f.route_changed && !fd_controller_release(&c));
    set_observed(&f, true, true, false, 8);
    assert(fd_controller_poll_restore(&c) && fd_controller_release(&c));
    f.start_ok = true;

    /* Timer expiry independently calls quiesce; it never assumes TX is RX. */
    assert(fd_controller_acquire(&c));
    assert(fd_controller_select_matrix(&c, FD_R0_M0_P0) && fd_controller_arm(&c, true));
    unsigned clear_before = f.clears;
    assert(fd_controller_run(&c, 30, 25));
    wait_for_clear(&f, clear_before + 1);
    assert(!fd_controller_poll_restore(&c));
    set_observed(&f, true, true, false, 9);
    assert(fd_controller_poll_restore(&c) && fd_controller_release(&c));
    /* A receive route needs a completed monitored probe and local confirmation. */
    assert(fd_controller_acquire(&c));
    assert(!fd_controller_select_rx_playback(&c, FD_R1_M0_P0));
    assert(!fd_controller_confirm_rx_route(&c, FD_R1_M0_P0, true));
    assert(fd_controller_select_matrix(&c, FD_R1_M0_P0));
    assert(fd_controller_arm(&c, true));
    assert(fd_controller_run(&c, 40, 10000));
    set_observed(&f, true, true, false, 10);
    pthread_mutex_lock(&f.mutex); unsigned observations = f.observations; pthread_mutex_unlock(&f.mutex);
    wait_for_observation(&f, observations + 1);
    assert(fd_controller_stop(&c));
    set_observed(&f, true, true, false, 11);
    f.audio_pending = true;
    assert(!fd_controller_poll_restore(&c)); /* command clear and RX do not imply a flushed sink */
    f.audio_pending = false;
    assert(fd_controller_poll_restore(&c));
    assert(!fd_controller_confirm_rx_route(&c, FD_R1_M0_P0, false));
    assert(!fd_controller_confirm_rx_route(&c, FD_R0_M0_P0, true));
    assert(fd_controller_confirm_rx_route(&c, FD_R1_M0_P0, true));
    assert(fd_controller_select_rx_playback(&c, FD_R1_M0_P0));
    assert(!fd_controller_arm(&c, true));
    assert(fd_controller_run(&c, 50, 120000)); /* no relaxation of RF case limit */
    pthread_mutex_lock(&f.mutex); clear_before = f.clears; pthread_mutex_unlock(&f.mutex);
    set_observed(&f, true, true, true, 12);
    wait_for_clear(&f, clear_before + 1); /* unexpected TX aborts without GUI polling */
    assert(!fd_controller_release(&c));
    assert(!fd_controller_poll_restore(&c));
    set_observed(&f, true, true, false, 13);
    assert(wait_restore(&c) && fd_controller_release(&c));
    assert(fd_controller_acquire(&c));
    assert(!fd_controller_select_rx_playback(&c, FD_R1_M0_P0)); /* qualification never persists */
    assert(fd_controller_select_passive_rx(&c));
    assert(fd_controller_run(&c, 55, 120000)); /* monitor abort cancelled the prior timer */
    assert(fd_controller_stop(&c));
    set_observed(&f, true, true, false, 14);
    assert(fd_controller_poll_restore(&c));
    assert(fd_controller_select_mic_tx(&c, FD_R1_M1_P1));
    assert(fd_controller_arm(&c, true));
    assert(!fd_controller_run(&c, 60, 10001));
    assert(fd_controller_stop(&c));
    set_observed(&f, true, true, false, 15);
    assert(fd_controller_poll_restore(&c) && fd_controller_release(&c));
    assert(fd_controller_close(&c));
    pthread_cond_destroy(&f.changed); pthread_mutex_destroy(&f.mutex);
    puts("PASS: mocked session acquisition, arm, stop, timeout and post-clear restore");
}
