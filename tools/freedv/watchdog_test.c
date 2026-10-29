// SPDX-License-Identifier: LGPL-2.1-or-later
#define _POSIX_C_SOURCE 200809L
#include "src/freedv/watchdog.h"
#include <assert.h>
#include <pthread.h>
#include <stdio.h>
#include <time.h>

typedef struct {
    pthread_mutex_t mutex;
    pthread_cond_t condition;
    int calls;
    bool hold, release, cancel_started, cancel_done;
    freedv_watchdog_reason reason;
    freedv_watchdog *watchdog;
} fixture;

static void fired(void *ctx, freedv_watchdog_reason reason) {
    fixture *f = ctx;
    pthread_mutex_lock(&f->mutex);
    ++f->calls; f->reason = reason;
    pthread_cond_broadcast(&f->condition);
    while (f->hold && !f->release) pthread_cond_wait(&f->condition, &f->mutex);
    pthread_mutex_unlock(&f->mutex);
}
static void *cancel_thread(void *ctx) {
    fixture *f = ctx;
    pthread_mutex_lock(&f->mutex);
    f->cancel_started = true;
    pthread_cond_broadcast(&f->condition);
    pthread_mutex_unlock(&f->mutex);
    freedv_watchdog_cancel(f->watchdog);
    pthread_mutex_lock(&f->mutex);
    f->cancel_done = true;
    pthread_cond_broadcast(&f->condition);
    pthread_mutex_unlock(&f->mutex);
    return NULL;
}
static void await_calls(fixture *f, int expected) {
    struct timespec limit;
    assert(!clock_gettime(CLOCK_REALTIME, &limit));
    limit.tv_sec += 5;
    pthread_mutex_lock(&f->mutex);
    while (f->calls < expected) assert(!pthread_cond_timedwait(&f->condition, &f->mutex, &limit));
    pthread_mutex_unlock(&f->mutex);
}
static void pause_ms(long ms) {
    struct timespec duration = {ms / 1000, (ms % 1000) * 1000000};
    nanosleep(&duration, NULL);
}
int main(void) {
    freedv_watchdog w;
    fixture f = {.mutex=PTHREAD_MUTEX_INITIALIZER, .condition=PTHREAD_COND_INITIALIZER, .watchdog=&w};
    assert(!freedv_watchdog_open(NULL, fired, &f));
    assert(!freedv_watchdog_open(&w, NULL, &f));
    assert(freedv_watchdog_open(&w, fired, &f));
    assert(!freedv_watchdog_arm(&w, 0));
    assert(!freedv_watchdog_arm(&w, 120001));
    assert(freedv_watchdog_arm(&w, 50));
    assert(!freedv_watchdog_arm(&w, 50));
    freedv_watchdog_cancel(&w);
    pause_ms(100);
    pthread_mutex_lock(&f.mutex); assert(f.calls == 0); pthread_mutex_unlock(&f.mutex);
    assert(freedv_watchdog_arm(&w, 25));
    await_calls(&f, 1);
    freedv_watchdog_cancel(&w);
    pthread_mutex_lock(&f.mutex); assert(f.reason == FREEDV_WATCHDOG_TIMEOUT); f.hold = true; pthread_mutex_unlock(&f.mutex);
    assert(freedv_watchdog_arm(&w, 25));
    await_calls(&f, 2);
    pthread_t canceler;
    assert(!pthread_create(&canceler, NULL, cancel_thread, &f));
    pthread_mutex_lock(&f.mutex);
    while (!f.cancel_started) pthread_cond_wait(&f.condition, &f.mutex);
    assert(!f.cancel_done); /* cancel cannot return while the callback is held */
    f.release = true;
    pthread_cond_broadcast(&f.condition);
    pthread_mutex_unlock(&f.mutex);
    assert(!pthread_join(canceler, NULL));
    assert(f.cancel_done && f.calls == 2);
    assert(freedv_watchdog_arm(&w, 10000));
    freedv_watchdog_close(&w);
    pause_ms(30);
    assert(f.calls == 2);
    pthread_cond_destroy(&f.condition);
    pthread_mutex_destroy(&f.mutex);
    puts("PASS: watchdog arm, timeout, cancellation, callback synchronization and close");
}
