/*
 *  SPDX-License-Identifier: LGPL-2.1-or-later
 *
 *  Xiegu X6100 LVGL GUI
 *
 *  Copyright (c) 2022-2023 Belousov Oleg aka R1CBU
 */

#pragma once

#include <stdbool.h>
#include <stddef.h>

#include "radio_types.h"

#define AUDIO_PLAY_RATE     (44100)
#define AUDIO_CAPTURE_RATE  (44100)
// #define AUDIO_PLAY_RATE     (22050)
// #define AUDIO_CAPTURE_RATE  (22050)

typedef enum {
    AUDIO_PLAY_OFF, // No play, BASE will not stream audio from mic or this app
    AUDIO_PLAY_ON, // Play mode, BASE will stream audio from this app. Both mic gain set to minimal
    AUDIO_PLAY_VOICE_REC  // Rec mode, BASE will stream audio from hmic and this app
} audio_play_mode_t;

/* The GUI's exit code when PulseAudio cannot be reached or goes away: x6100_daemon starts it
 * again and does not count the start against a pending update */
#define AUDIO_EXIT_NO_SERVER 3

void audio_init();
void audio_mixer_setup(radio_base_ver_t base_ver);
int audio_play(int16_t *buf, size_t samples);
void audio_play_wait();
void audio_play_flush();

/* Local playback on the Bluetooth headphones, from the playing thread (see audio.c) */
bool audio_play_bt_begin(void);
void audio_play_bt_end(void);

/* Short-delay playback into the codec for the phone's voice, from the playing thread (see audio.c) */
bool audio_play_lowlat_begin(void);
void audio_play_lowlat_end(void);
bool audio_play_if_room(int16_t *buf, size_t samples);

void audio_gain_db(int16_t *buf, size_t samples, float gain, int16_t *out);
void audio_gain_db_transition(int16_t *buf, size_t samples, float gain1, float gain2, int16_t *out);

float audio_set_play_vol(float db);
float audio_set_rec_vol(float db);

float audio_get_peak_db();

#ifdef ENABLE_FREEDV_DIAGNOSTICS
#include <sys/types.h>
#include <stdint.h>
/* The diagnostic session must own routing and competing audio writers before
 * opening this stream. Samples
 * not accepted remain the caller's responsibility; -1 is a stream/write error.
 * This API never selects a radio route or keys RF. */
#define AUDIO_FREEDV_UNDERFLOW_EVENTS 16
typedef struct {
    uint64_t monotonic_ns;
    uint64_t accepted_samples;
    bool cancellation_requested;
} audio_freedv_underflow_event_t;
typedef struct {
    uint64_t accepted_samples;
    uint64_t write_errors;
    uint64_t underflows; /* includes startup; compare snapshots after warm-up */
    uint64_t first_write_monotonic_ns;
    uint64_t underflows_before_first_write, underflows_after_first_write;
    unsigned underflow_events_stored, underflow_events_discarded;
    audio_freedv_underflow_event_t underflow_events[AUDIO_FREEDV_UNDERFLOW_EVENTS];
    uint64_t flushes;
    bool cancelled;
    bool flush_acknowledged;
    uint32_t maxlength_bytes, target_bytes, prebuffer_bytes, min_request_bytes;
    bool latency_known;
    uint64_t queued_latency_us; /* PulseAudio observation, not acoustic latency */
} audio_freedv_playback_stats_t;
typedef struct audio_freedv_playback audio_freedv_playback_t;
/* The session owns the handle. Any thread may cancel/write/read statistics;
 * close requires all callers joined and is never part of the RF stop path. */
audio_freedv_playback_t *audio_freedv_playback_open(void);
ssize_t audio_freedv_playback_submit(audio_freedv_playback_t *, const int16_t *, size_t);
/* Lock-free write fence for the RF watchdog: request, clear RF commands, then
 * let the audio worker issue the server flush. Never wait for PulseAudio before
 * clearing software PTT/MODEM. cancel() itself acquires the PA mainloop lock. */
void audio_freedv_playback_request_cancel(audio_freedv_playback_t *);
bool audio_freedv_playback_cancel(audio_freedv_playback_t *);
/* Lock-free observation for restoration guards; no PA/mainloop lock. */
bool audio_freedv_playback_quiescent(audio_freedv_playback_t *);
audio_freedv_playback_stats_t audio_freedv_playback_observe(audio_freedv_playback_t *);
void audio_freedv_playback_close(audio_freedv_playback_t *);
/* Compatibility wrappers for the previous single-worker tests. New diagnostic
 * sessions use the explicit handle so their watchdog can cancel playback. */
bool audio_freedv_playback_begin(void);
ssize_t audio_freedv_playback_write(const int16_t *samples, size_t count);
audio_freedv_playback_stats_t audio_freedv_playback_stats(void);
void audio_freedv_playback_end(void); /* flush and disconnect, no drain wait */

/* An exclusive diagnostic session owns capture. Stop and join all consumers
 * before starting another capture. Default PCM comes from the normal mono PA
 * source before application decimation; configure() can select an independent
 * stereo channel instead. Start/stop run outside PA callbacks. queued_samples
 * is UINT_MAX if a concurrent update made the observation inconsistent. */
typedef struct {
    unsigned queued_samples;
    unsigned dropped_samples;
    unsigned hole_samples;
    unsigned capture_errors;
} audio_freedv_capture_stats_t;
bool audio_freedv_capture_begin(void);
void audio_freedv_capture_end(void);
size_t audio_freedv_capture_read(int16_t *out, size_t capacity);
audio_freedv_capture_stats_t audio_freedv_capture_stats(void);
/* Configure only while capture is stopped and its consumers have joined.
 * 0 retains the normal mono source; 1/2 select physical left/right from a
 * separate stereo source; 3 takes their explicit arithmetic mean. A nonempty
 * mixer item must exactly match one enumerated hardware choice. No RF control. */
bool audio_freedv_capture_configure(unsigned channel, const char *mixer_item);
/* Exactly one evidence reader. Returns frames, writing two samples per frame.
 * A configured stereo source must be drained while active, or its overflow
 * invalidates capture. The codec still receives only the selected mono path. */
size_t audio_freedv_capture_read_stereo(int16_t *out, size_t frame_capacity);
typedef struct audio_freedv_mixer_snapshot audio_freedv_mixer_snapshot_t;
audio_freedv_mixer_snapshot_t *audio_freedv_mixer_snapshot(void);
bool audio_freedv_mixer_restore(audio_freedv_mixer_snapshot_t *);
void audio_freedv_mixer_snapshot_free(audio_freedv_mixer_snapshot_t *);
/* Selected diagnostic state only, suitable for result metadata (JSON). */
bool audio_freedv_mixer_snapshot_describe(const audio_freedv_mixer_snapshot_t *, char *buffer, size_t capacity);
/* Newline-separated exact enumeration names; false on unsupported hardware or
 * insufficient buffer, never a truncated choice list. */
bool audio_freedv_mixer_items(char *buffer, size_t capacity);
/* Existing non-diagnostic PA handles or recently queued ordinary audio. */
bool audio_freedv_other_audio_busy(void);
#endif
