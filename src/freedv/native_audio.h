// SPDX-License-Identifier: LGPL-2.1-or-later
#pragma once
#include "../audio.h"
#include "../../tools/freedv/rade_stream.h"
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#ifdef __cplusplus
extern "C" {
#endif
/* Resource preparation is explicit and must run while unkeyed and exclusively
 * owned. None of these functions selects a radio route or keys RF. */
typedef enum {
    FD_NATIVE_CAPTURE, FD_NATIVE_TONE, FD_NATIVE_RADE_FIXTURE,
    FD_NATIVE_RADE_MIC_TX, FD_NATIVE_RADE_LIVE_RX
} fd_native_kind;
typedef struct {
    fd_native_kind kind;
    const char *fixture_path; /* signed16LE mono at8kHz, RX fixture only */
    const char *output_directory; /* existing private per-case directory */
    uint64_t generation;
    unsigned inject_failure_ms; /*0 disabled; controlled worker-failure case */
    unsigned duration_ms; /* 1..120000; coordinator imposes RF<=10000 */
    unsigned capture_channel; /*0 existingmono,1L,2R,3explicit mean */
    const char *mixer_item; /*NULL keep exact saved setting */
    bool interrupt_fixture; /*2s zero input every12s, preserving decoder state */
    bool capture_input; /* concurrent physical capture during fixture playback */
} fd_native_config;
typedef struct fd_native_audio fd_native_audio;
typedef struct {
    fd_native_kind kind;
    bool prepared, started, stop_requested, finished, quiescent, mixer_restored;
    bool native_codec_available;
    unsigned failures;
    uint64_t generation, elapsed_ms, input_samples, output_samples, interrupted_input_samples;
    uint64_t recorder_samples, recorder_discarded_samples;
    uint64_t aborted_codec_output_samples, aborted_playback_samples;
    audio_freedv_capture_stats_t capture;
    audio_freedv_playback_stats_t playback;
    fd_stream_stats codec;
} fd_native_stats;
fd_native_audio *fd_native_audio_prepare(const fd_native_config *, char *error, size_t error_size);
bool fd_native_audio_start(fd_native_audio *);
/* Atomic fencing only; safe before software RF command clearing. Worker requests
 * PA flush asynchronously. Does not acquire PA/controller/radio/file locks. */
void fd_native_audio_request_stop(fd_native_audio *);
bool fd_native_audio_stop_requested(fd_native_audio *);
bool fd_native_audio_failed(fd_native_audio *);
bool fd_native_audio_quiescent(fd_native_audio *); /* cached, nonblocking */
fd_native_stats fd_native_audio_observe(fd_native_audio *);
/* Joins workers, closes files and captures final stats; outside all RF locks.
 * Does not restore mixer or ordinary radio routing. */
bool fd_native_audio_finish(fd_native_audio *);
/* Only after a fresh post-clear RX observation; outside radio/controller locks.
 * Checks exact saved ALSA readback. */
bool fd_native_audio_restore(fd_native_audio *);
/* Cached selected-state JSON; no ALSA or file operations. */
bool fd_native_audio_mixer_describe(fd_native_audio *, bool after_restore, char *, size_t);
/* Caller has joined and restored, or preparation failed before hardware change. */
void fd_native_audio_destroy(fd_native_audio *);
bool fd_native_audio_codec_available(void);
#ifdef __cplusplus
}
#endif
