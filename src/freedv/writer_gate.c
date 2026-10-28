// SPDX-License-Identifier: LGPL-2.1-or-later
#include "writer_gate.h"
#include <pthread.h>
#include <limits.h>
#include <assert.h>
static pthread_mutex_t mutex = PTHREAD_MUTEX_INITIALIZER;
static unsigned writers;
static bool reserved;
static _Thread_local unsigned depth;
bool fd_writer_enter(void) {
    if (depth) { if (depth == UINT_MAX) return false; ++depth; return true; }
    pthread_mutex_lock(&mutex);
    bool ok = !reserved && writers != UINT_MAX;
    if (ok) { ++writers; depth = 1; }
    pthread_mutex_unlock(&mutex);
    return ok;
}
void fd_writer_exit(void) {
    assert(depth);
    if (--depth) return;
    pthread_mutex_lock(&mutex);
    assert(writers); --writers;
    pthread_mutex_unlock(&mutex);
}
bool fd_writer_reserve(void) {
    pthread_mutex_lock(&mutex);
    bool ok = !reserved && !writers;
    if (ok) reserved = true;
    pthread_mutex_unlock(&mutex);
    return ok;
}
void fd_writer_release(void) {
    pthread_mutex_lock(&mutex);
    assert(reserved && !writers);
    reserved = false;
    pthread_mutex_unlock(&mutex);
}
