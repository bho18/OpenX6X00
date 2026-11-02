/* SPDX-License-Identifier: BSD-2-Clause
 * Shared RADE speech kernels. Sequencing follows rade_c's rade_tx_wav.c and
 * rade_rx_wav.c, Copyright (C) 2024 David Rowe. See THIRD-PARTY-NOTICES.md.
 * No devices, radio control, filesystem, allocation or process exit.
 */
#pragma once
#include <stdbool.h>
#include <stddef.h>
#include <string.h>
#include "fargan.h"
#include "rade_dsp.h"
#include "lpcnet.h"

typedef struct {
    FARGANState fargan;
    float continuation[5 * NB_TOTAL_FEATURES];
    unsigned continuation_frames;
    bool ready;
} fd_rade_speech_decoder;
static inline void fd_rade_speech_init(fd_rade_speech_decoder *s) {
    memset(s, 0, sizeof(*s));
    fargan_init(&s->fargan);
}
/* One consumer owns this state. Keep it across ordinary loss/reacquisition.
 * Capacity is checked before any synthesis or continuation-state mutation. */
static inline int fd_rade_speech_render(fd_rade_speech_decoder *s, const float *features,
                                        int count, float *output, size_t capacity) {
    if (!s || count < 0 || count % NB_TOTAL_FEATURES || count > RADE_NZMF * RADE_FRAMES_PER_STEP * NB_TOTAL_FEATURES || (count && !features)) return -1;
    size_t frames = (size_t)count / NB_TOTAL_FEATURES;
    size_t skip = s->ready ? 0 : 5 - s->continuation_frames;
    size_t needed = frames > skip ? (frames - skip) * LPCNET_FRAME_SIZE : 0;
    if (needed > capacity || (needed && !output)) return -1;
    int produced = 0;
    for (size_t j = 0; j < frames; ++j) {
        const float *feat = features + j * NB_TOTAL_FEATURES;
        if (!s->ready) {
            memcpy(s->continuation + s->continuation_frames * NB_TOTAL_FEATURES,
                   feat, NB_TOTAL_FEATURES * sizeof(float));
            if (++s->continuation_frames == 5) {
                float packed[5 * NB_FEATURES], zero[FARGAN_CONT_SAMPLES] = {0};
                for (int k = 0; k < 5; ++k)
                    memcpy(packed + k * NB_FEATURES, s->continuation + k * NB_TOTAL_FEATURES,
                           NB_FEATURES * sizeof(float));
                fargan_cont(&s->fargan, zero, packed);
                s->ready = true;
            }
            continue;
        }
        fargan_synthesize(&s->fargan, output + produced, feat);
        produced += LPCNET_FRAME_SIZE;
    }
    return produced;
}
static inline bool fd_rade_speech_encode(LPCNetEncState *encoder, const short *pcm,
                                          float *features, int count, int arch) {
    if (!encoder || !pcm || !features || count <= 0 || count % NB_TOTAL_FEATURES || count > RADE_NZMF * RADE_FRAMES_PER_STEP * NB_TOTAL_FEATURES) return false;
    for (int j = 0; j < count / NB_TOTAL_FEATURES; ++j)
        lpcnet_compute_single_frame_features(encoder, pcm + j * LPCNET_FRAME_SIZE,
                                           features + j * NB_TOTAL_FEATURES, arch);
    return true;
}
