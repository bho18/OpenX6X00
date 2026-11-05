// SPDX-License-Identifier: LGPL-2.1-or-later
#pragma once
#include "capture_ring.h"
#include <pthread.h>
#include <stdbool.h>

typedef enum { FD_CAPTURE_CODEC, FD_CAPTURE_RECORDING, FD_CAPTURE_READERS } fd_capture_reader;
typedef enum { FD_CAPTURE_OK, FD_CAPTURE_SOURCE, FD_CAPTURE_BACKLOG } fd_capture_failure;
typedef void (*fd_capture_failure_fn)(void *, fd_capture_failure);
typedef struct {
    freedv_capture_ring queues[FD_CAPTURE_READERS];
    pthread_t thread;
    atomic_bool stop;
    atomic_uint failure;
    bool started;
    bool enabled[FD_CAPTURE_READERS];
    fd_capture_failure_fn on_failure;
    void *context;
} fd_capture_dispatch;
/* Session owns start/stop; initialize only while all readers are stopped.
 * Exactly one dispatcher consumes physical capture. Each destination has one
 * consumer; slow disk cannot consume or steal codec samples. */
void fd_capture_dispatch_init(fd_capture_dispatch *d);
bool fd_capture_dispatch_start(fd_capture_dispatch *d, bool codec, bool recording,
                               fd_capture_failure_fn on_failure, void *context);
/* Request does not join; safe from the failure callback. */
void fd_capture_dispatch_cancel(fd_capture_dispatch *d);
bool fd_capture_dispatch_join(fd_capture_dispatch *d);
size_t fd_capture_dispatch_read(fd_capture_dispatch *d, fd_capture_reader reader,
                                int16_t *samples, size_t capacity);
