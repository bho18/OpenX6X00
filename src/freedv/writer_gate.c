// SPDX-License-Identifier: LGPL-2.1-or-later
#include "writer_gate.h"
#include <pthread.h>
#include <limits.h>
#include <assert.h>
#include <stdatomic.h>
#include <fcntl.h>
#include <sys/file.h>
#include <unistd.h>
static pthread_mutex_t mutex = PTHREAD_MUTEX_INITIALIZER;
static unsigned writers;
static bool reserved;
static atomic_bool voice_reserved;
/* Web requests may edit the shared SQLite file in another process. A lock
 * spans each mutation/commit there and the complete owned session here. The
 * check and mutation therefore cannot race mode entry. Kernel release on
 * process exit also prevents a stale on-disk "TX active" preference. */
static int external_lock=-1;
static bool reserve_external(void) {
    int fd=open("/tmp/openx6x00-settings.lock",O_RDONLY|O_CREAT|O_CLOEXEC|O_NOFOLLOW,0666);
    if(fd<0)return false;
    if(flock(fd,LOCK_EX|LOCK_NB)){close(fd);return false;}
    external_lock=fd;return true;
}
static _Thread_local unsigned depth;
static _Thread_local unsigned voice_depth;
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
    bool ok = !reserved && !writers && reserve_external();
    if (ok) reserved = true;
    pthread_mutex_unlock(&mutex);
    return ok;
}
void fd_writer_release(void) {
    pthread_mutex_lock(&mutex);
    assert(reserved && !writers);
    if(external_lock>=0){close(external_lock);external_lock=-1;}
    reserved = false;
    atomic_store_explicit(&voice_reserved, false, memory_order_release);
    pthread_mutex_unlock(&mutex);
}
bool fd_writer_voice_reserve(void) {
    pthread_mutex_lock(&mutex);
    bool ok = !reserved && !writers && reserve_external();
    if (ok) {
        reserved = true;
        atomic_store_explicit(&voice_reserved, true, memory_order_release);
    }
    pthread_mutex_unlock(&mutex);
    return ok;
}
bool fd_writer_voice_begin(void) {
    if (voice_depth) {
        if (voice_depth == UINT_MAX || depth == UINT_MAX) return false;
        ++voice_depth; ++depth; return true;
    }
    pthread_mutex_lock(&mutex);
    bool ok = reserved && atomic_load_explicit(&voice_reserved, memory_order_relaxed) && !depth && writers != UINT_MAX;
    if (ok) { ++writers; depth = voice_depth = 1; }
    pthread_mutex_unlock(&mutex);
    return ok;
}
void fd_writer_voice_end(void) {
    assert(voice_depth);
    --voice_depth;
    fd_writer_exit();
}
bool fd_writer_voice_owned(void) { return atomic_load_explicit(&voice_reserved, memory_order_acquire); }
bool fd_writer_voice_authorized(void) { return voice_depth != 0; }
int fd_writer_hardware_begin(bool safety_only) {
    pthread_mutex_lock(&mutex);
    /* The diagnostic owner already uses the old low-level backend contract.
     * A production reservation requires an owned scope, except safe unkeying. */
    if (reserved && !atomic_load_explicit(&voice_reserved, memory_order_relaxed)) {
        pthread_mutex_unlock(&mutex); return 2;
    }
    if (reserved && !voice_depth && !safety_only) { pthread_mutex_unlock(&mutex); return 0; }
    if (depth == UINT_MAX || (!depth && writers == UINT_MAX)) { pthread_mutex_unlock(&mutex); return 0; }
    if (!depth) ++writers;
    ++depth;
    pthread_mutex_unlock(&mutex); return 1;
}
void fd_writer_hardware_end(int admission) { if (admission == 1) fd_writer_exit(); }
