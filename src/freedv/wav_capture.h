// SPDX-License-Identifier: LGPL-2.1-or-later
#pragma once
#include <stdbool.h>
#include <stdint.h>
#include <stdatomic.h>
#include <pthread.h>
#include <stdio.h>
#include "../audio.h"

typedef enum {
    FREEDV_WAV_OK = 0, FREEDV_WAV_CAPTURE_ERROR, FREEDV_WAV_IO_ERROR,
    FREEDV_WAV_NOT_RUNNING, FREEDV_WAV_JOIN_ERROR
} freedv_wav_error;
typedef void (*freedv_wav_failure_fn)(void *context, freedv_wav_error error);
typedef struct {
    size_t (*read)(void *context, int16_t *samples, size_t count);
    audio_freedv_capture_stats_t (*stats)(void *context);
    void *context;
    unsigned channels; /*0/1 mono,2 stereo; returned counts are samples */
} freedv_wav_source;
typedef struct {
    freedv_wav_source source;
    bool owns_capture;
    pthread_t worker;
    FILE *file;
    atomic_bool stop_requested;
    freedv_wav_failure_fn failure;
    void *context;
    bool started;          /* controlled by the session owner */
    freedv_wav_error error; /* read after join only */
    uint64_t max_samples; /*0 unlimited; keep draining after excerpt is full */
    uint64_t discarded_samples;
    uint64_t samples;      /* read after join only */
    audio_freedv_capture_stats_t capture_stats; /* read after join only */
} freedv_wav_capture;

/* Initialize once before use, and only while no worker uses this object. */
void freedv_wav_capture_init(freedv_wav_capture *capture);
/* Caller must already own an exclusive diagnostic session. Creates a new file
 * with mode 0600, starts capture and a file worker; never overwrites a file.
 * On failure, a newly created incomplete file may remain for inspection. The
 * callback runs on the worker thread and must request session abort; it must not
 * call stop/join itself. No file is written by the PulseAudio callback. */
bool freedv_wav_capture_start(freedv_wav_capture *capture, const char *path,
                              freedv_wav_failure_fn failure, void *context);
/* Stops the producer, drains the queue and joins the worker. Do not hold the
 * radio lock or call from the failure callback. Caller serializes start/stop.
 * Stop returns NOT_RUNNING if no start succeeded. A failed join leaves the
 * object started; the caller must not reuse it while the worker may run. */
freedv_wav_error freedv_wav_capture_stop(freedv_wav_capture *capture);

/* Use a dispatcher recording queue; caller owns producer start/cancellation and
 * stops that producer before calling capture_stop. Never reads physical audio. */
bool freedv_wav_capture_start_source(freedv_wav_capture *, const char *path,
                                     freedv_wav_failure_fn, void *context,
                                     const freedv_wav_source *);
