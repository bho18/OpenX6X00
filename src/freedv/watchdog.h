// SPDX-License-Identifier: LGPL-2.1-or-later
#pragma once
#include <stdbool.h>
#include <stdint.h>
#include <pthread.h>
#include <time.h>

typedef enum { FREEDV_WATCHDOG_TIMEOUT, FREEDV_WATCHDOG_ERROR } freedv_watchdog_reason;
typedef void (*freedv_watchdog_expired_fn)(void *context, freedv_watchdog_reason reason);
typedef struct {
    pthread_mutex_t mutex;
    pthread_cond_t changed;
    pthread_t worker;
    struct timespec deadline;
    freedv_watchdog_expired_fn expired;
    void *context;
    uint64_t generation;
    bool shutdown, armed, executing;
} freedv_watchdog;

/* Each session creates a new, initially disarmed watchdog. The callback must
 * request audio flush and software TX clearing and must not call cancel/close. */
bool freedv_watchdog_open(freedv_watchdog *watchdog, freedv_watchdog_expired_fn expired, void *context);
/* 1..10000 ms, no implicit restart or second arm while active. */
bool freedv_watchdog_arm(freedv_watchdog *watchdog, unsigned duration_ms);
/* Waits for an already-started callback. Do not hold the radio lock. */
void freedv_watchdog_cancel(freedv_watchdog *watchdog);
/* Cancels, waits for the worker and destroys synchronization. Do not hold the
 * radio lock. No other thread may use the watchdog during or after close. */
void freedv_watchdog_close(freedv_watchdog *watchdog);
