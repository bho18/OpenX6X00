// SPDX-License-Identifier: LGPL-2.1-or-later
#include "session_controller.h"
#include <string.h>

/* Called with the controller lock held. A failed clear keeps the session in
 * WAIT_RX with ownership held; a later poll retries instead of restoring. */
static bool clear_locked(fd_controller *c) {
    uint64_t sequence = 0;
    if (!c->backend.quiesce(c->context, &sequence)) return false;
    return fd_commands_cleared(&c->state, sequence);
}
static void timeout_callback(void *context, freedv_watchdog_reason reason) {
    (void)reason; /* both timeout and timer failure request the same safe stop */
    fd_controller *c = context;
    pthread_mutex_lock(&c->mutex);
    if (fd_stop(&c->state)) (void)clear_locked(c);
    pthread_mutex_unlock(&c->mutex);
}
static bool backend_valid(const fd_controller_backend *b) {
    return b && b->acquire_snapshot && b->release && b->observe && b->tx_permitted &&
           b->start && b->quiesce && b->restore_if_safe;
}
bool fd_controller_init(fd_controller *c, const fd_controller_backend *backend, void *context) {
    if (!c || !backend_valid(backend)) return false;
    memset(c, 0, sizeof(*c));
    if (pthread_mutex_init(&c->mutex, NULL)) return false;
    c->backend = *backend;
    c->context = context;
    if (!freedv_watchdog_open(&c->watchdog, timeout_callback, c)) {
        pthread_mutex_destroy(&c->mutex);
        return false;
    }
    c->initialized = true;
    return true;
}
bool fd_controller_acquire(fd_controller *c) {
    if (!c || !c->initialized) return false;
    pthread_mutex_lock(&c->mutex);
    bool acquired = false, quiescent = false, ok = false;
    fd_observation observed = {0};
    if (c->state.state == FD_IDLE) {
        acquired = c->backend.acquire_snapshot(c->context, &quiescent, &observed);
        if (acquired) ok = fd_acquire(&c->state, false, quiescent, observed);
        if (acquired && !ok) c->backend.release(c->context);
    }
    pthread_mutex_unlock(&c->mutex);
    return ok;
}
bool fd_controller_select_matrix(fd_controller *c, fd_matrix_case selected) {
    if (!c || !c->initialized) return false;
    pthread_mutex_lock(&c->mutex);
    bool ok = fd_select_matrix(&c->state, selected);
    pthread_mutex_unlock(&c->mutex);
    return ok;
}
bool fd_controller_select_passive_rx(fd_controller *c) {
    if (!c || !c->initialized) return false;
    pthread_mutex_lock(&c->mutex);
    bool ok = fd_select_passive_rx(&c->state);
    pthread_mutex_unlock(&c->mutex);
    return ok;
}
bool fd_controller_arm(fd_controller *c, bool local) {
    if (!c || !c->initialized) return false;
    pthread_mutex_lock(&c->mutex);
    bool ok = fd_arm(&c->state, local);
    pthread_mutex_unlock(&c->mutex);
    return ok;
}
bool fd_controller_run(fd_controller *c, uint64_t now_ms, unsigned timeout_ms) {
    if (!c || !c->initialized || !timeout_ms || timeout_ms > 10000) return false;
    pthread_mutex_lock(&c->mutex);
    if (c->cancelling) { pthread_mutex_unlock(&c->mutex); return false; }
    fd_observation observed = {0};
    bool valid = c->backend.observe(c->context, &observed);
    bool permitted = valid && (!c->state.tx_case || c->backend.tx_permitted(c->context));
    bool begun = valid && fd_run(&c->state, now_ms, permitted, observed);
    bool timer = false, ok = false, cancel_timer = false;
    if (begun) {
        /* Passive receive has no automatic TX timeout. */
        timer = !c->state.tx_case || freedv_watchdog_arm(&c->watchdog, timeout_ms);
        if (timer) ok = c->backend.start(c->context, c->state.matrix_case);
        if (!ok && fd_stop(&c->state)) {
            (void)clear_locked(c);
            cancel_timer = c->state.tx_case && timer;
            if (cancel_timer) c->cancelling = true;
        }
    }
    pthread_mutex_unlock(&c->mutex);
    /* Never cancel while holding the controller or radio lock: a running
     * timeout callback may need those locks to clear software TX. */
    if (cancel_timer) {
        freedv_watchdog_cancel(&c->watchdog);
        pthread_mutex_lock(&c->mutex);
        c->cancelling = false;
        pthread_mutex_unlock(&c->mutex);
    }
    return ok;
}
bool fd_controller_stop(fd_controller *c) {
    if (!c || !c->initialized) return false;
    pthread_mutex_lock(&c->mutex);
    if (c->cancelling || (c->state.state != FD_RUNNING && c->state.state != FD_ARMED && c->state.state != FD_WAIT_RX)) {
        pthread_mutex_unlock(&c->mutex);
        return false;
    }
    if (c->state.state != FD_WAIT_RX) (void)fd_stop(&c->state);
    c->cancelling = true;
    pthread_mutex_unlock(&c->mutex);
    freedv_watchdog_cancel(&c->watchdog);
    pthread_mutex_lock(&c->mutex);
    bool ok = c->state.software_cleared || clear_locked(c);
    c->cancelling = false;
    pthread_mutex_unlock(&c->mutex);
    return ok;
}
bool fd_controller_poll_restore(fd_controller *c) {
    if (!c || !c->initialized) return false;
    pthread_mutex_lock(&c->mutex);
    bool ok = false;
    if (!c->cancelling && c->state.state == FD_WAIT_RX && (c->state.software_cleared || clear_locked(c))) {
        fd_observation observed = {0};
        if (c->backend.observe(c->context, &observed) && fd_rx_observed(&c->state, observed)) {
            if (c->backend.restore_if_safe(c->context, c->state.clear_sequence)) {
                ok = fd_restored(&c->state);
            } else {
                /* Restoration may have failed partway. Clear again and require
                 * another packet; never release ownership on this path. */
                c->state.state = FD_WAIT_RX;
                c->state.software_cleared = false;
                (void)clear_locked(c);
            }
        }
    }
    pthread_mutex_unlock(&c->mutex);
    return ok;
}
bool fd_controller_release(fd_controller *c) {
    if (!c || !c->initialized) return false;
    pthread_mutex_lock(&c->mutex);
    bool ok = !c->cancelling && fd_release(&c->state);
    if (ok) c->backend.release(c->context);
    pthread_mutex_unlock(&c->mutex);
    return ok;
}
bool fd_controller_close(fd_controller *c) {
    if (!c || !c->initialized) return false;
    pthread_mutex_lock(&c->mutex);
    bool idle = c->state.state == FD_IDLE;
    pthread_mutex_unlock(&c->mutex);
    if (!idle) return false;
    freedv_watchdog_close(&c->watchdog);
    pthread_mutex_destroy(&c->mutex);
    c->initialized = false;
    return true;
}
