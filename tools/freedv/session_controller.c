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
static bool stop_locked(fd_controller *c, fd_stop_reason reason) {
    if (!fd_stop(&c->state)) return false;
    if (c->state.stop_reason == FD_STOP_NONE) c->state.stop_reason = reason;
    return true;
}
static void timeout_callback(void *context, freedv_watchdog_reason reason) {
    fd_controller *c = context;
    pthread_mutex_lock(&c->mutex);
    if (stop_locked(c, reason == FREEDV_WATCHDOG_TIMEOUT ? FD_STOP_TIMEOUT : FD_STOP_TIMER_ERROR)) (void)clear_locked(c);
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
    uint64_t retry_at_ms = 0;
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
            fd_stop_reason reason = !valid ? FD_STOP_OBSERVATION : FD_STOP_UNEXPECTED_TX;
            if (!valid && c->backend.abort_reason) {
                fd_stop_reason cached = c->backend.abort_reason(c->context);
                if (cached != FD_STOP_NONE) reason = cached;
            }
            if ((!valid || (!c->state.tx_case && o.tx)) && stop_locked(c, reason)) {
                c->probe_unkeyed = false;
                (void)clear_locked(c);
                c->cancelling = cancel_timer = true;
            }
        }
        /* A failed bus clear must keep retrying even if the coordinator is
         * blocked joining an audio/file worker. Do not restore or release here. */
        struct timespec now;
        clock_gettime(CLOCK_MONOTONIC, &now);
        uint64_t now_ms = (uint64_t)now.tv_sec * 1000u + now.tv_nsec / 1000000u;
        if (c->state.state == FD_WAIT_RX && !c->state.software_cleared && now_ms >= retry_at_ms) {
            (void)clear_locked(c);
            retry_at_ms = now_ms + 100;
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
        if (acquired && !ok && !c->backend.release(c->context)) {
            /* A physical key can arrive during acquisition rollback. Keep the
             * owner reachable until commands clear and a later RX restores it. */
            c->state.state = FD_WAIT_RX;
            c->state.software_cleared = false;
            (void)clear_locked(c);
        }
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
        if (!ok && stop_locked(c, FD_STOP_START_FAILED)) {
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
bool fd_controller_stop_with_reason(fd_controller *c, fd_stop_reason reason) {
    if (reason != FD_STOP_OPERATOR && reason != FD_STOP_CLOSE) return false;
    if (!c || !c->initialized) return false;
    pthread_mutex_lock(&c->mutex);
    if (c->cancelling || (c->state.state != FD_RUNNING && c->state.state != FD_ARMED && c->state.state != FD_PREPARING && c->state.state != FD_WAIT_RX && !(c->state.state == FD_READY && c->state.resources_prepared))) {
        pthread_mutex_unlock(&c->mutex);
        return false;
    }
    if (c->state.state != FD_WAIT_RX) (void)stop_locked(c, reason);
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
    bool ok = !c->cancelling && c->state.state == FD_READY && !c->state.resources_prepared;
    if (ok) {
        ok = c->backend.release(c->context);
        if (!ok) {
            c->state.state = FD_WAIT_RX;
            c->state.software_cleared = false;
            (void)clear_locked(c);
        }
    }
    if (ok) ok = fd_release(&c->state);
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

bool fd_controller_begin_prepare(fd_controller *c) {
    if (!c || !c->initialized) return false;
    pthread_mutex_lock(&c->mutex);
    fd_observation o = {0};
    bool ok = !c->cancelling && c->backend.observe(c->context, &o) && o.known && o.fresh && !o.tx && fd_begin_prepare(&c->state);
    pthread_mutex_unlock(&c->mutex);
    return ok;
}
bool fd_controller_finish_prepare(fd_controller *c) {
    if (!c || !c->initialized) return false;
    pthread_mutex_lock(&c->mutex);
    fd_observation o = {0};
    bool ok = c->backend.observe(c->context, &o) && fd_finish_prepare(&c->state, o);
    if (!ok && stop_locked(c, FD_STOP_PREPARATION_FAILED)) (void)clear_locked(c);
    pthread_mutex_unlock(&c->mutex);
    return ok;
}

bool fd_controller_stop(fd_controller *c) { return fd_controller_stop_with_reason(c, FD_STOP_OPERATOR); }
