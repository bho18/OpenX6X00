/* SPDX-License-Identifier: BSD-2-Clause */
#pragma once
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#ifdef __cplusplus
extern "C" {
#endif
/* Device/control/filesystem-free bounded RADE V1 engine. Prepare while unkeyed.
 * A fresh object owns each RX/TX turn; generation rejects previous-turn PCM.
 * Submit has one producer and receive one consumer. Both return immediately. */
typedef struct fd_rade_stream fd_rade_stream;
typedef enum { FD_STREAM_RX, FD_STREAM_TX } fd_stream_direction;
typedef struct {
    uint64_t generation, submitted_samples, consumed_samples, output_samples, delivered_samples;
    uint64_t blocks, rendered_blocks, synchronized_blocks, feature_blocks;
    uint64_t initialization_ns, represented_audio_ns;
    uint64_t receiver_service_ns, speech_service_ns;
    uint64_t receiver_deadline_misses, speech_deadline_misses, padded_native_samples;
    uint64_t receiver_p99_ns, receiver_max_ns, speech_p99_ns, speech_max_ns;
    double receiver_p99_frame_ratio, speech_p99_frame_ratio;
    unsigned input_depth, output_depth, feature_depth, feature_max_depth, input_max_depth, output_max_depth;
    unsigned failures;
    int opus_arch;
    bool stopped, synchronized, tx_finished;
} fd_stream_stats;
fd_rade_stream *fd_stream_prepare(fd_stream_direction, uint64_t generation);
bool fd_stream_start(fd_rade_stream *);
size_t fd_stream_submit(fd_rade_stream *, uint64_t generation, const int16_t *, size_t);
size_t fd_stream_receive(fd_rade_stream *, uint64_t generation, int16_t *, size_t);
/* Atomic request only: no device calls, allocation, joins or codec finalization. */
void fd_stream_abort(fd_rade_stream *);
/* Graceful TX only: fence new input, pad the final partial speech frame and
 * append EOO. Caller keeps draining output and still enforces its RF deadline. */
bool fd_stream_finish_tx(fd_rade_stream *);
bool fd_stream_tx_finished(fd_rade_stream *);
unsigned fd_stream_output_pending(fd_rade_stream *);
fd_stream_stats fd_stream_observe(fd_rade_stream *);
bool fd_stream_join(fd_rade_stream *); /* abort/join, retain final statistics */
void fd_stream_destroy(fd_rade_stream *); /* joins helpers outside RF stop */
#ifdef __cplusplus
}
#endif
