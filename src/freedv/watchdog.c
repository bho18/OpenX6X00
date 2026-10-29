// SPDX-License-Identifier: LGPL-2.1-or-later
#define _POSIX_C_SOURCE 200809L
#include "watchdog.h"
#include <errno.h>
#include <string.h>

static void *watchdog_thread(void *arg) {
    freedv_watchdog *w = arg;
    pthread_mutex_lock(&w->mutex);
    while (!w->shutdown) {
        while (!w->shutdown && !w->armed) pthread_cond_wait(&w->changed, &w->mutex);
        if (w->shutdown) break;
        uint64_t generation = w->generation;
        int result = pthread_cond_timedwait(&w->changed, &w->mutex, &w->deadline);
        /* An unexpected timer failure must also request a safe stop. */
        if (w->armed && w->generation == generation && result != 0) {
            w->armed = false;
            w->executing = true;
            pthread_mutex_unlock(&w->mutex);
            w->expired(w->context, result == ETIMEDOUT ? FREEDV_WATCHDOG_TIMEOUT : FREEDV_WATCHDOG_ERROR);
            pthread_mutex_lock(&w->mutex);
            w->executing = false;
            pthread_cond_broadcast(&w->changed);
        }
    }
    pthread_mutex_unlock(&w->mutex);
    return NULL;
}

bool freedv_watchdog_open(freedv_watchdog *w, freedv_watchdog_expired_fn expired, void *context) {
    if (!w || !expired) return false;
    memset(w, 0, sizeof(*w));
    if (pthread_mutex_init(&w->mutex, NULL)) return false;
    pthread_condattr_t attr;
    if (pthread_condattr_init(&attr)) { pthread_mutex_destroy(&w->mutex); return false; }
    int error = pthread_condattr_setclock(&attr, CLOCK_MONOTONIC);
    if (!error) error = pthread_cond_init(&w->changed, &attr);
    pthread_condattr_destroy(&attr);
    if (error) { pthread_mutex_destroy(&w->mutex); return false; }
    w->expired = expired;
    w->context = context;
    if (pthread_create(&w->worker, NULL, watchdog_thread, w)) {
        pthread_cond_destroy(&w->changed);
        pthread_mutex_destroy(&w->mutex);
        return false;
    }
    return true;
}

bool freedv_watchdog_arm(freedv_watchdog *w, unsigned duration_ms) {
    if (!w || !duration_ms || duration_ms > 120000) return false;
    struct timespec deadline;
    if (clock_gettime(CLOCK_MONOTONIC, &deadline)) return false;
    deadline.tv_sec += (time_t)(duration_ms / 1000);
    deadline.tv_nsec += (long)(duration_ms % 1000) * 1000000L;
    if (deadline.tv_nsec >= 1000000000L) { ++deadline.tv_sec; deadline.tv_nsec -= 1000000000L; }
    pthread_mutex_lock(&w->mutex);
    bool ok = !w->shutdown && !w->armed && !w->executing && w->generation != UINT64_MAX;
    if (ok) {
        w->deadline = deadline;
        ++w->generation;
        w->armed = true;
        pthread_cond_signal(&w->changed);
    }
    pthread_mutex_unlock(&w->mutex);
    return ok;
}

void freedv_watchdog_cancel(freedv_watchdog *w) {
    pthread_mutex_lock(&w->mutex);
    w->armed = false;
    if (w->generation != UINT64_MAX) ++w->generation;
    pthread_cond_signal(&w->changed);
    while (w->executing) pthread_cond_wait(&w->changed, &w->mutex);
    pthread_mutex_unlock(&w->mutex);
}

void freedv_watchdog_close(freedv_watchdog *w) {
    pthread_mutex_lock(&w->mutex);
    w->shutdown = true;
    w->armed = false;
    pthread_cond_signal(&w->changed);
    pthread_mutex_unlock(&w->mutex);
    pthread_join(w->worker, NULL);
    pthread_cond_destroy(&w->changed);
    pthread_mutex_destroy(&w->mutex);
}
