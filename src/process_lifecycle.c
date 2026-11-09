// SPDX-License-Identifier: LGPL-2.1-or-later
#define _POSIX_C_SOURCE 200809L
#include "process_lifecycle.h"
#include <errno.h>
#include <fcntl.h>
#include <poll.h>
#include <pthread.h>
#include <signal.h>
#include <stdatomic.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <sys/eventfd.h>
#include <sys/file.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>

#ifndef PROCESS_LIFECYCLE_GRACE_MS
#define PROCESS_LIFECYCLE_GRACE_MS 5000u
#endif

/* The handler must never fall through to libatomic's locking implementation. */
_Static_assert(ATOMIC_INT_LOCK_FREE == 2, "lifecycle signal flags must be lock-free");
static process_lifecycle_ops callbacks;
static sigset_t coordinator_mask;
static volatile sig_atomic_t wake_fd = -1;
static atomic_int initialized, stopping, aborting, complete, incomplete;
static atomic_int audio_restart, requested_signal;

/* Fixed little-endian v1 diagnostic format, decoded by usb_rf_evidence.py.
 * Only lock-free 32-bit stores touch this pre-faulted mapping during shutdown.
 * /tmp preserves it across process death/restart, not loss of radio power. */
enum { JOURNAL_RUNNING=1,JOURNAL_STOPPING=2,JOURNAL_RESTORED=3,JOURNAL_INCOMPLETE=4 };
typedef struct {
    uint32_t pid,start_low,start_high;
    _Atomic uint32_t state,signal,reason,identity_sequence;
    uint32_t reserved;
    char process_id[64],session_id[256];
    uint32_t padding[8];
} lifecycle_record;
typedef struct {
    char magic[8];
    uint32_t version,header_size,record_size,byte_order,reserved[2];
    lifecycle_record current,previous;
} lifecycle_journal;
_Static_assert(sizeof(lifecycle_record)==384 && sizeof(lifecycle_journal)==800,"lifecycle journal layout");
_Static_assert(ATOMIC_INT_LOCK_FREE==2 && sizeof(uint32_t)==sizeof(unsigned),"journal stores must be lock-free");
static lifecycle_journal *journal;
static int journal_fd=-1;

static void journal_close(void) {
    if(journal){munmap(journal,sizeof(*journal));journal=NULL;}
    if(journal_fd>=0){close(journal_fd);journal_fd=-1;}
}
static bool journal_open(const char *path) {
    if(!path)return true;
    int fd=open(path,O_RDWR|O_CREAT|O_CLOEXEC|O_NOFOLLOW,0600);
    if(fd<0)return false;
    struct stat st;
    if(fstat(fd,&st)<0){int error=errno;close(fd);errno=error;return false;}
    if(!S_ISREG(st.st_mode) || st.st_uid!=geteuid()){close(fd);errno=EPERM;return false;}
    if(flock(fd,LOCK_EX|LOCK_NB)<0){int error=errno;close(fd);errno=error;return false;}
    lifecycle_journal old;
    bool valid=st.st_size==(off_t)sizeof(old) && pread(fd,&old,sizeof(old),0)==(ssize_t)sizeof(old) &&
        !memcmp(old.magic,"X6LIFEV1",8) && old.version==1 && old.header_size==32 && old.record_size==384 && old.byte_order==0x01020304;
    if(ftruncate(fd,sizeof(old))<0){int error=errno;close(fd);errno=error;return false;}
    void *mapped=mmap(NULL,sizeof(old),PROT_READ|PROT_WRITE,MAP_SHARED,fd,0);
    if(mapped==MAP_FAILED){int error=errno;close(fd);errno=error;return false;}
    journal=mapped;journal_fd=fd;
    memset(journal,0,sizeof(*journal));memcpy(journal->magic,"X6LIFEV1",8);
    journal->version=1;journal->header_size=32;journal->record_size=384;journal->byte_order=0x01020304;
    if(valid)memcpy(&journal->previous,&old.current,sizeof(old.current));
    struct timespec now;clock_gettime(CLOCK_MONOTONIC,&now);
    uint64_t start=(uint64_t)now.tv_sec*1000000000u+(unsigned long)now.tv_nsec;
    journal->current.pid=(uint32_t)getpid();journal->current.start_low=(uint32_t)start;journal->current.start_high=(uint32_t)(start>>32);
    snprintf(journal->current.process_id,sizeof(journal->current.process_id),"%u-%llx",journal->current.pid,(unsigned long long)start);
    atomic_store_explicit(&journal->current.state,JOURNAL_RUNNING,memory_order_release);
    return true;
}
static void journal_state(unsigned state,unsigned signal,unsigned reason) {
    if(!journal)return;
    if(signal)atomic_store_explicit(&journal->current.signal,signal,memory_order_relaxed);
    if(reason)atomic_store_explicit(&journal->current.reason,reason,memory_order_relaxed);
    unsigned prior=atomic_load_explicit(&journal->current.state,memory_order_acquire);
    /* A late GUI ACK cannot overwrite the deadline's truthful outcome. */
    while(prior!=JOURNAL_INCOMPLETE && !atomic_compare_exchange_weak_explicit(
        &journal->current.state,&prior,state,memory_order_release,memory_order_acquire)){}
}
void process_lifecycle_set_session(const char *session_id) {
    if(!journal || !session_id || process_lifecycle_stopping())return;
    size_t length=strnlen(session_id,sizeof(journal->current.session_id));
    if(!length || length>=sizeof(journal->current.session_id))return;
    atomic_fetch_add_explicit(&journal->current.identity_sequence,1,memory_order_acq_rel);
    memcpy(journal->current.session_id,session_id,length+1);
    atomic_fetch_add_explicit(&journal->current.identity_sequence,1,memory_order_release);
}

bool process_lifecycle_stopping(void) { return atomic_load_explicit(&stopping, memory_order_acquire) != 0; }
bool process_lifecycle_aborting(void) { return atomic_load_explicit(&aborting, memory_order_acquire) != 0; }
bool process_lifecycle_incomplete(void) { return atomic_load_explicit(&incomplete, memory_order_acquire) != 0; }

static bool wake(void) {
    uint64_t one = 1;
    ssize_t n;
    do { n = write(wake_fd, &one, sizeof(one)); } while (n < 0 && errno == EINTR);
    /* A full counter is already readable. Atomic flags remain authoritative. */
    return n == (ssize_t)sizeof(one) || (n < 0 && errno == EAGAIN);
}

static void signal_requested(int sig) {
    int saved_errno = errno, expected = 0;
    atomic_store_explicit(&stopping, 1, memory_order_release);
    (void)atomic_compare_exchange_strong_explicit(&requested_signal, &expected, sig,
                                                memory_order_release, memory_order_relaxed);
    (void)wake();
    errno = saved_errno;
}

void process_lifecycle_cleanup_complete(void) {
    if (process_lifecycle_stopping()) {
        journal_state(JOURNAL_RESTORED,0,0);
        atomic_store_explicit(&complete, 1, memory_order_release);
        (void)wake();
    }
}

static uint64_t now_ms(void) {
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return (uint64_t)t.tv_sec * 1000u + (unsigned long)t.tv_nsec / 1000000u;
}

static void wait_wake(int milliseconds) {
    struct pollfd p = {.fd = wake_fd, .events = POLLIN};
    if (poll(&p, 1, milliseconds) > 0) {
        uint64_t count;
        /* Exactly one read: a signal storm cannot trap a drain-to-empty loop. */
        (void)read(wake_fd, &count, sizeof(count));
    }
}

static void bypass(void) { if (callbacks.bypass) callbacks.bypass(callbacks.context); }

static void end_with_signal(int sig) {
    struct sigaction action;
    memset(&action, 0, sizeof(action));
    action.sa_handler = SIG_DFL;
    sigemptyset(&action.sa_mask);
    (void)sigaction(sig, &action, NULL);
    sigset_t only;
    sigemptyset(&only); sigaddset(&only, sig);
    (void)pthread_sigmask(SIG_UNBLOCK, &only, NULL);
    (void)raise(sig);
    _exit(128 + sig);
}

static void *coordinate(void *unused) {
    (void)unused;
    /* This thread remains able to receive the minimal handler even when the
     * GUI and all radio/audio workers are blocked. It never waits for them to
     * deliver the eventfd notification that starts its independent deadline. */
    (void)pthread_sigmask(SIG_SETMASK, &coordinator_mask, NULL);
    for (;;) {
        int sig = atomic_exchange_explicit(&requested_signal, 0, memory_order_acquire);
        if (!sig) {
            if (atomic_load_explicit(&audio_restart, memory_order_acquire)) {
                journal_state(JOURNAL_RESTORED,0,2);
                bypass();
                _exit(3);
            }
            wait_wake(-1);
            continue;
        }
        journal_state(JOURNAL_STOPPING,(unsigned)sig,1);
        /* The handler closes entry before this observation; there is no gap
         * in which another GUI session can begin after ordinary shutdown. */
        bool active = callbacks.active && callbacks.active(callbacks.context);
        if (active) {
            uint64_t deadline = now_ms() + PROCESS_LIFECYCLE_GRACE_MS;
            if (callbacks.request_cleanup) callbacks.request_cleanup(callbacks.context);
            while (!atomic_load_explicit(&complete, memory_order_acquire)) {
                uint64_t now = now_ms();
                if (now >= deadline) break;
                wait_wake((int)(deadline - now));
                /* Further TERM/INT requests never move the first deadline or
                 * change the original signal's exit status. */
                (void)atomic_exchange_explicit(&requested_signal, 0, memory_order_acquire);
            }
            if (!atomic_load_explicit(&complete, memory_order_acquire)) {
                atomic_store_explicit(&incomplete, 1, memory_order_release);
                atomic_store_explicit(&aborting, 1, memory_order_release);
                journal_state(JOURNAL_INCOMPLETE,0,0);
                if (callbacks.abort_requested) callbacks.abort_requested(callbacks.context);
                /* Do not wait for filesystem/GUI locks. These flags request
                 * best-effort abort; they are not an RF-clear or successful
                 * finalization acknowledgement. */
            }
        } else journal_state(JOURNAL_RESTORED,0,0);
        bypass();
        end_with_signal(sig);
    }
}

bool process_lifecycle_init(const process_lifecycle_ops *ops) {
    if (!ops) { errno = EINVAL; return false; }
    int expected = 0;
    if (!atomic_compare_exchange_strong(&initialized, &expected, 1)) { errno = EALREADY; return false; }
    callbacks = *ops;
    if(!journal_open(ops->journal_path)){atomic_store(&initialized,0);return false;}
    int fd = eventfd(0, EFD_CLOEXEC | EFD_NONBLOCK);
    if (fd < 0) { journal_close();atomic_store(&initialized, 0); return false; }
    wake_fd = fd;
    sigset_t set, old_mask;
    sigemptyset(&set); sigaddset(&set, SIGTERM); sigaddset(&set, SIGINT);
    int error = pthread_sigmask(SIG_BLOCK, &set, &old_mask);
    if (error) { close(fd);journal_close(); wake_fd = -1; atomic_store(&initialized, 0); errno = error; return false; }
    coordinator_mask = old_mask;
    struct sigaction action = {.sa_handler = signal_requested, .sa_flags = SA_RESTART}, old_term, old_int;
    action.sa_mask = set;
    bool term_installed = sigaction(SIGTERM, &action, &old_term) == 0;
    bool int_installed = term_installed && sigaction(SIGINT, &action, &old_int) == 0;
    pthread_t coordinator;
    error = int_installed ? pthread_create(&coordinator, NULL, coordinate, NULL) : errno;
    if (error) {
        if (int_installed) (void)sigaction(SIGINT, &old_int, NULL);
        if (term_installed) (void)sigaction(SIGTERM, &old_term, NULL);
        close(fd);journal_close(); wake_fd = -1; atomic_store(&initialized, 0);
    } else {
        (void)pthread_detach(coordinator);
    }
    /* Restore before any normal worker or subprocess is created. The
     * coordinator restores this same mask itself; ordinary exec children
     * retain their original TERM/INT behavior. */
    (void)pthread_sigmask(SIG_SETMASK, &old_mask, NULL);
    if (error) { errno = error; return false; }
    return true;
}

bool process_lifecycle_request_audio_restart(void) {
    if (!atomic_load_explicit(&initialized, memory_order_acquire)) return false;
    int expected = 0;
    if (!atomic_compare_exchange_strong(&stopping, &expected, 1))
        return atomic_load_explicit(&audio_restart, memory_order_acquire) != 0;
    atomic_store_explicit(&audio_restart, 1, memory_order_release);
    return wake();
}
