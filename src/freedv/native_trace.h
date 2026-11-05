// SPDX-License-Identifier: LGPL-2.1-or-later
#pragma once
#include "voice_types.h"
#include "qualified_profile.h"
#include <stdio.h>

/* Bounded in-memory evidence. Export is an explicit operator action; normal
 * audio processing never opens a recording or writes to the filesystem. */
typedef struct {
    uint64_t adapter_instance, engine_instance, capture_instance, playback_instance;
    uint64_t generation, input_samples, output_samples, delivered_samples;
    uint64_t processing_max_ns, synthesis_max_ns;
    unsigned input_high_water, output_high_water, capture_queued;
    unsigned capture_dropped, capture_holes, capture_errors, playback_underflows;
    unsigned rade_creations, encoder_creations, decoder_creations;
    unsigned resampler_creations, worker_creations, capture_stream_creations;
    bool failed, cancelled, finished;
} fd_native_audio_trace;
void fd_native_trace_begin(const fd_qualified_profile *, bool simulation);
void fd_native_trace_note(const fd_voice_status *, const fd_voice_observation *);
void fd_native_trace_command(uint64_t generation, bool modem, bool ptt);
void fd_native_trace_audio(const fd_native_audio_trace *);
bool fd_native_trace_export(FILE *);
