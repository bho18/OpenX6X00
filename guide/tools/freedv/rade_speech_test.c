/* SPDX-License-Identifier: BSD-2-Clause
 * Independent encoder comparison and decoder chunk-boundary/rejection checks.
 */
#include "rade_speech.h"
#include "cpu_support.h"
#include <assert.h>
#include <stdlib.h>
#include <math.h>
#include <stdio.h>
int main(void) {
    LPCNetEncState *a = lpcnet_encoder_create(), *b = lpcnet_encoder_create();
    fd_rade_speech_decoder *whole = calloc(1, sizeof(*whole)), *split = calloc(1, sizeof(*split));
    assert(a && b && whole && split);
    fd_rade_speech_init(whole); fd_rade_speech_init(split);
    enum { N = RADE_NZMF * RADE_FRAMES_PER_STEP, SAMPLES = N * LPCNET_FRAME_SIZE };
    short pcm[SAMPLES]; float fa[N * NB_TOTAL_FEATURES], fb[N * NB_TOTAL_FEATURES];
    float x[SAMPLES], y[SAMPLES];
    unsigned outputs = 0;
    int arch = opus_select_arch();
    for (int block = 0; block < 20; ++block) {
        for (int i = 0; i < SAMPLES; ++i) pcm[i] = (short)(12000 * sin((block * SAMPLES + i) * 0.173));
        for (int j = 0; j < N; ++j)
            lpcnet_compute_single_frame_features(a, pcm + j * LPCNET_FRAME_SIZE,
                                               fa + j * NB_TOTAL_FEATURES, arch);
        assert(fd_rade_speech_encode(b, pcm, fb, N * NB_TOTAL_FEATURES, arch));
        assert(!memcmp(fa, fb, sizeof(fa)));
        int count = fd_rade_speech_render(whole, fa, N * NB_TOTAL_FEATURES, x, SAMPLES);
        assert(count > 0);
        /* Rejected capacity must not consume continuation or mutate FARGAN. */
        assert(fd_rade_speech_render(split, fa, N * NB_TOTAL_FEATURES, y, 0) == -1);
        size_t out = 0;
        for (int j = 0; j < N; ++j) {
            int n = fd_rade_speech_render(split, fa + j * NB_TOTAL_FEATURES,
                                          NB_TOTAL_FEATURES, y + out, SAMPLES - out);
            assert(n >= 0); out += (unsigned)n;
            assert(fd_rade_speech_render(split, NULL, 0, NULL, 0) == 0); /* sync gap does not reset state */
        }
        assert(out == (size_t)count && !memcmp(x, y, out * sizeof(float)));
        outputs += (unsigned)out;
    }
    assert(!fd_rade_speech_encode(a, pcm, fa, 1, arch));
    assert(fd_rade_speech_render(whole, fa, 1, x, SAMPLES) == -1);
    lpcnet_encoder_destroy(a); lpcnet_encoder_destroy(b); free(whole); free(split);
    printf("PASS: independent TX feature equality, RX chunk equality and rejected-buffer state; %u output samples\n", outputs);
}
