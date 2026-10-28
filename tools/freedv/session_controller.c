// SPDX-License-Identifier: LGPL-2.1-or-later
#include "session_controller.h"
#include <string.h>
#include <time.h>

/* Called with the controller lock held. A failed clear keeps the session in
 * WAIT_RX with ownership held; a later poll retries instead of restoring. */
static bool clear_locked(fd_controller *c) {
    uint64_t sequence = 0;
    if (c->state.selected_case.kind == FD_CASE_ROUTE) {
        fd_observation o = {0};
        bool valid = c->backend.observe(c->context, &o) && o.known && o.fresh;
        if (!valid || o.tx) c->probe_unkeyed = false;
        if (valid && o.sequence > c->probe_start_sequence) c->probe_observed = true;
    }
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
           b->start && b->quiesce && b->audio_quiescent && b->restore_if_safe;
}
/* Independent of LVGL and disk workers. Backends must provide bounded observe
 * and quiesce operations; no joins or file I/O are permitted under this lock. */
static void *monitor_session(void *arg) {
    fd_controller *c = arg;
    for (;;) {
        struct timespec delay = {0, 20000000};
        nanosleep(&delay, NULL);
        pthread_mutex_lock(&c->mutex);
        if (c->monitor_shutdown) { pthread_mutex_unlock(&c->mutex); break; }
        bool cancel_timer = false;
        if (c->state.state == FD_RUNNING) {
            fd_observation o = {0};
            bool valid = c->backend.observe(c->context, &o) && o.known && o.fresh;
            if (c->state.selected_case.kind == FD_CASE_ROUTE) {
                if (!valid || o.tx) c->probe_unkeyed = false;
                if (valid && o.sequence > c->probe_start_sequence) c->probe_observed = true;
            }
            if ((!valid || (!c->state.tx_case && o.tx)) && fd_stop(&c->state)) {
                c->probe_unkeyed = false;
                (void)clear_locked(c);
                c->cancelling = cancel_timer = true;
            }
        }
        pthread_mutex_unlock(&c->mutex);
        if (cancel_timer) {
            freedv_watchdog_cancel(&c->watchdog);
            pthread_mutex_lock(&c->mutex);
            c->cancelling = false;
            pthread_mutex_unlock(&c->mutex);
        }
    }
    return NULL;
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
    c->completed_probe = -1;
    if (pthread_create(&c->monitor, NULL, monitor_session, c)) {
        freedv_watchdog_close(&c->watchdog);
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
        if (acquired) {
            ok = fd_acquire(&c->state, false, quiescent, observed);
            c->completed_probe = -1;
        }
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
    if (!c || !c->initialized || !timeout_ms || timeout_ms > 120000) return false;
    pthread_mutex_lock(&c->mutex);
    if (c->cancelling || (c->state.tx_case && timeout_ms > 10000)) { pthread_mutex_unlock(&c->mutex); return false; }
    fd_observation observed = {0};
    bool valid = c->backend.observe(c->context, &observed);
    bool permitted = valid && (!c->state.tx_case || c->backend.tx_permitted(c->context));
    bool begun = valid && fd_run(&c->state, now_ms, permitted, observed);
    bool timer = false, ok = false, cancel_timer = false;
    if (begun) {
        c->completed_probe = -1;
        c->probe_unkeyed = true;
        c->probe_observed = false;
        c->probe_start_sequence = observed.sequence;
        /* The same independent timer bounds RX playback too. RF cases still
         * have the unconditional ten-second upper bound above. */
        timer = freedv_watchdog_arm(&c->watchdog, timeout_ms);
        if (timer) ok = c->backend.start(c->context, &c->state.selected_case);
        if (!ok && fd_stop(&c->state)) {
            c->probe_unkeyed = false;
            (void)clear_locked(c);
            cancel_timer = timer;
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
        if (c->backend.audio_quiescent(c->context) && c->backend.observe(c->context, &observed) && fd_rx_observed(&c->state, observed)) {
            if (c->backend.restore_if_safe(c->context, c->state.clear_sequence)) {
                if (c->state.selected_case.kind == FD_CASE_ROUTE && c->probe_unkeyed && c->probe_observed)
                    c->completed_probe = c->state.matrix_case;
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
    pthread_mutex_lock(&c->mutex);
    c->monitor_shutdown = true;
    pthread_mutex_unlock(&c->mutex);
    pthread_join(c->monitor, NULL);
    freedv_watchdog_close(&c->watchdog);
    pthread_mutex_destroy(&c->mutex);
    c->initialized = false;
    return true;
}

bool fd_controller_confirm_rx_route(fd_controller *c, fd_matrix_case route, bool local) {
    if (!c || !c->initialized || !local || (unsigned)route > 7) return false;
    pthread_mutex_lock(&c->mutex);
    bool ok = c->state.state == FD_READY && c->completed_probe == (int)route;
    if (ok) c->state.qualified_rx_routes |= 1u << route;
    pthread_mutex_unlock(&c->mutex);
    return ok;
}
bool fd_controller_select_rx_playback(fd_controller *c, fd_matrix_case route) {
    if (!c || !c->initialized) return false;
    pthread_mutex_lock(&c->mutex);
    bool ok = fd_select_rx_playback(&c->state, route);
    pthread_mutex_unlock(&c->mutex);
    return ok;
}
bool fd_controller_select_mic_tx(fd_controller *c, fd_matrix_case route) {
    if (!c || !c->initialized) return false;
    pthread_mutex_lock(&c->mutex);
    bool ok = fd_select_mic_tx(&c->state, route);
    pthread_mutex_unlock(&c->mutex);
    return ok;
}
bool fd_controller_status(fd_controller *c, fd_session *status) {
    if (!c || !c->initialized || !status) return false;
    pthread_mutex_lock(&c->mutex);
    *status = c->state;
    pthread_mutex_unlock(&c->mutex);
    return true;
}
