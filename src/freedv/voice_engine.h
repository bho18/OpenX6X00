// SPDX-License-Identifier: BSD-2-Clause
#pragma once
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
typedef struct fd_voice_engine fd_voice_engine;
typedef enum { FD_VOICE_IDLE, FD_VOICE_RX, FD_VOICE_TX } fd_voice_direction;
typedef struct {
    uint64_t generation, turns, input_samples, output_samples, delivered_samples;
    uint64_t modem_blocks, speech_frames, eoo_count, padded_samples;
    uint64_t synchronized_blocks, synthesis_blocks, acquisitions;
    uint64_t processing_ns, synthesis_ns, queue_wait_ns, telemetry_overwrites;
    /* Notification-to-resume latency for an idle worker. This is distinct
     * from measured processing spans and output-queue backpressure waits. */
    uint64_t scheduling_delay_ns, scheduling_max_ns, scheduling_events;
    uint64_t processing_max_ns, synthesis_max_ns;
    uint64_t duration_histogram[32]; /* log2 microseconds, bounded */
    unsigned input_depth, output_depth, input_high_water, output_high_water;
    unsigned failures, rade_creations, encoder_creations, decoder_creations;
    unsigned resampler_creations, worker_creations;
    unsigned warmup_runs; /* Successful disposable process-wide warm-ups. */
    bool warmup_reused;
    bool synchronized, finished, cancelled;
} fd_voice_stats;
/* Warm immutable processing paths once successfully per process, then allocate one live context and retained
 * workers. No radio, device, filesystem, or per-turn recording. */
fd_voice_engine *fd_voice_create(unsigned audio_rate);
/* Coordinator calls transition only after fencing external audio callbacks.
 * Waits for affected engine workers, clears PCM/features/SRC and per-turn state,
 * retains neural histories. RX reset uses the pinned reset-to-search API. */
bool fd_voice_transition(fd_voice_engine *, fd_voice_direction, uint64_t generation);
size_t fd_voice_submit(fd_voice_engine *, uint64_t generation, const int16_t *, size_t);
size_t fd_voice_receive(fd_voice_engine *, uint64_t generation, int16_t *, size_t);
bool fd_voice_finish(fd_voice_engine *, uint64_t generation);
/* Lock-free fence. Software RF clearing must not wait for a codec or device. */
void fd_voice_cancel(fd_voice_engine *);
fd_voice_stats fd_voice_observe(fd_voice_engine *);
void fd_voice_destroy(fd_voice_engine *);
#ifdef FREEDV_VOICE_TEST_HOOKS
enum { FD_VOICE_TEST_ENCODE=1, FD_VOICE_TEST_DECODE, FD_VOICE_TEST_SYNTHESIS };
bool fd_voice_test_fail(unsigned stage); /* only linked into the fault runner */
#endif
