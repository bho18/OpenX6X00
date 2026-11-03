// SPDX-License-Identifier: LGPL-2.1-or-later
#pragma once
#include "voice_engine.h"
#include "qualified_profile.h"
#include "../audio.h"
typedef struct fd_voice_audio fd_voice_audio;
/* Explicit diagnostic recording only. Callbacks copy to bounded queues; they
 * must never block or call files/devices. Change only while fenced. Normal
 * voice operation leaves all callbacks NULL. */
typedef void (*fd_voice_audio_samples_fn)(void *,const int16_t *,size_t);
typedef struct {fd_voice_audio_samples_fn input,output,stereo;void *context;} fd_voice_audio_taps;
bool fd_voice_audio_set_taps(fd_voice_audio *,const fd_voice_audio_taps *);
/* Stable export codes; never infer the first fault from a stale stream count. */
typedef enum {
    FD_AUDIO_OK, FD_AUDIO_CAPTURE, FD_AUDIO_BACKLOG, FD_AUDIO_INPUT_REJECTED,
    FD_AUDIO_FINISH, FD_AUDIO_WRITE, FD_AUDIO_ENGINE, FD_AUDIO_DRAIN,
    FD_AUDIO_STREAM, FD_AUDIO_CAPTURE_FLUSH, FD_AUDIO_CAPTURE_FLUSH_TIMEOUT,
    FD_AUDIO_REARM, FD_AUDIO_TRANSITION, FD_AUDIO_PLAYBACK_REARM,
    FD_AUDIO_UNCORK, FD_AUDIO_CAPTURE_BEGIN
} fd_voice_audio_fault;
typedef struct fd_voice_audio_snapshot {
    uint64_t sampled_ms, generation;
    bool cancelled, failed, finishing;
    unsigned first_fault;
    uint64_t fault_generation, fault_ms;
    unsigned fault_capture_queued, fault_engine_input_depth;
    fd_voice_stats engine;
    audio_freedv_capture_stats_t capture;
    audio_freedv_playback_stats_t playback;
    uint64_t previous_tx_generation, previous_tx_input_samples, previous_tx_first_write_ns, previous_tx_underflows;
    bool previous_tx_finished;
} fd_voice_audio_snapshot;
/* Nonblocking cached telemetry. Does not acquire the PA lock or touch devices. */
bool fd_voice_audio_snapshot_get(fd_voice_audio *,fd_voice_audio_snapshot *);
/* Register before begin. Called once on first failure from the pump; handler
 * may cancel/unkey independently, but must not close/fence/join this adapter. */
void fd_voice_audio_on_error(fd_voice_audio *,void (*handler)(void *),void *context);
/* Caller owns radio/mixer routing. Devices must match the qualified profile.
 * One retained capture source, playback stream, engine and pump per entry. */
fd_voice_audio *fd_voice_audio_open(const fd_qualified_profile *);
/* Synchronous callback/pump fence, used outside the RF-clear path. */
bool fd_voice_audio_fence(fd_voice_audio *);
bool fd_voice_audio_begin(fd_voice_audio *, fd_voice_direction, uint64_t generation);
bool fd_voice_audio_finish(fd_voice_audio *, uint64_t generation);
int fd_voice_audio_drained(fd_voice_audio *);
void fd_voice_audio_cancel(fd_voice_audio *);
bool fd_voice_audio_failed(fd_voice_audio *);
bool fd_voice_audio_microphone_active(fd_voice_audio *);
fd_voice_stats fd_voice_audio_observe(fd_voice_audio *);
void fd_voice_audio_close(fd_voice_audio *);
