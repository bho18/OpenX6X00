// SPDX-License-Identifier: LGPL-2.1-or-later
#pragma once
#include "diagnostic_state.h"
#include "src/freedv/watchdog.h"
#include <stdbool.h>
#include <stdint.h>
#include <pthread.h>

/* Mock-backed contract for the future in-process radio implementation. None of
 * these operations is implemented against real hardware yet. Implementations
 * must be bounded, must serialize radio changes with the radio control lock,
 * and must never call back into the controller synchronously. In particular,
 * quiesce requests worker shutdown, flushes generated audio and clears software
 * MODEM/PTT without waiting for file-worker joins. */
typedef struct {
    /* Atomically acquire the exclusive session and a complete snapshot, while
     * rejecting recording, competing digital/audio sessions and other busy
     * states. An incomplete snapshot or stale packet must fail acquisition.
     * A failed acquire must leave no ownership or route changes behind. */
    bool (*acquire_snapshot)(void *ctx, bool *commands_quiescent, fd_observation *observation);
    void (*release)(void *ctx);
    bool (*observe)(void *ctx, fd_observation *observation);
    bool (*tx_permitted)(void *ctx);
    /* -1 is passive receive only, without routing changes or playback. All
     * numbered cases are potentially keyed and require local arming. Recheck
     * frequency, protection guards and packet TX state under the radio lock
     * immediately before applying any change; refuse when uncertain. */
    bool (*start)(void *ctx, int matrix_case);
    /* On success, sequence is sampled after audio flush and command clearing. */
    bool (*quiesce)(void *ctx, uint64_t *sequence);
    /* Recheck a fresh post-clear RX packet while holding the radio lock before
     * applying transient restoration. Never restore while hardware TX is held. */
    bool (*restore_if_safe)(void *ctx, uint64_t clear_sequence);
} fd_controller_backend;

typedef struct {
    pthread_mutex_t mutex;
    freedv_watchdog watchdog;
    fd_session state;
    fd_controller_backend backend;
    void *context;
    bool initialized;
    bool cancelling; /* blocks restoration/new runs while a watchdog is joined */
} fd_controller;

bool fd_controller_init(fd_controller *controller, const fd_controller_backend *backend, void *context);
bool fd_controller_acquire(fd_controller *controller);
bool fd_controller_select_matrix(fd_controller *controller, fd_matrix_case selected);
bool fd_controller_select_passive_rx(fd_controller *controller);
bool fd_controller_arm(fd_controller *controller, bool local);
/* timeout_ms must be 1..10000. Backend start must return promptly; the timer
 * cannot interrupt a backend operation that blocks while holding the lock. */
bool fd_controller_run(fd_controller *controller, uint64_t now_ms, unsigned timeout_ms);
/* Stops and clears, or reports failure while retaining ownership for retry. */
bool fd_controller_stop(fd_controller *controller);
/* Retry clearing if needed; restore only after a later fresh RX packet and a
 * successful backend recheck. False includes TX held, stale status or failure. */
bool fd_controller_poll_restore(fd_controller *controller);
bool fd_controller_release(fd_controller *controller);
/* Only succeeds after release. Caller must serialize destruction with other APIs. */
bool fd_controller_close(fd_controller *controller);
