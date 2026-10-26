// SPDX-License-Identifier: LGPL-2.1-or-later
/* Exercise the real PulseAudio callback with mocked PulseAudio and DSP. */
#define ENABLE_FREEDV_DIAGNOSTICS 1
#include "src/audio.c"
#include "src/freedv/wav_capture.h"
#include <assert.h>

static int token_loop, token_stream, token_context;
static const void *mock_data;
static size_t mock_bytes, dsp_seen;
static int peek_error, drop_error, drops, lock_depth;
static pa_stream_state_t mock_state = PA_STREAM_READY;

void pa_threaded_mainloop_lock(pa_threaded_mainloop *m) { assert(m == mloop); assert(!lock_depth); ++lock_depth; }
void pa_threaded_mainloop_unlock(pa_threaded_mainloop *m) { assert(m == mloop); assert(lock_depth == 1); --lock_depth; }
pa_stream_state_t pa_stream_get_state(const pa_stream *s) { assert(s == capture_stm); assert(lock_depth == 1); return mock_state; }
int pa_stream_peek(pa_stream *s, const void **data, size_t *nbytes) {
    assert(s == capture_stm);
    if (peek_error) return -1;
    *data = mock_data; *nbytes = mock_bytes;
    return 0;
}
int pa_stream_drop(pa_stream *s) { assert(s == capture_stm); ++drops; return drop_error ? -1 : 0; }
int pa_context_errno(const pa_context *c) { assert(c == ctx); return 0; }
const char *pa_strerror(int error) { (void)error; return "test failure"; }
void dsp_put_audio_samples(size_t count, int16_t *samples) { assert(samples); dsp_seen += count; }
void _lv_log_add(lv_log_level_t level, const char *file, int line, const char *func, const char *format, ...) {
    (void)level; (void)file; (void)line; (void)func; (void)format;
}
static void unexpected_wav_failure(void *context, freedv_wav_error error) {
    (void)context; (void)error;
    assert(!"unexpected WAV capture failure");
}

int main(void) {
    mloop = (pa_threaded_mainloop *)&token_loop;
    capture_stm = (pa_stream *)&token_stream;
    ctx = (pa_context *)&token_context;
    int16_t input[] = {42, -10, 123, -321}, output[8] = {0};
    mock_data = input; mock_bytes = sizeof(input);
    read_callback(capture_stm, 0, NULL);
    assert(dsp_seen == 4 && audio_freedv_capture_read(output, 8) == 0);
    mock_state = PA_STREAM_CREATING;
    assert(!audio_freedv_capture_begin());
    mock_state = PA_STREAM_READY;
    assert(audio_freedv_capture_begin());
    assert(!audio_freedv_capture_begin());
    read_callback(capture_stm, 0, NULL);
    assert(dsp_seen == 8 && audio_freedv_capture_read(output, 8) == 4);
    for (int i = 0; i < 4; ++i) assert(output[i] == input[i]);
    mock_data = NULL; mock_bytes = 4;
    read_callback(capture_stm, 0, NULL);
    assert(audio_freedv_capture_read(output, 8) == 2 && output[0] == 0 && output[1] == 0);
    assert(audio_freedv_capture_stats().hole_samples == 2);
    peek_error = 1;
    read_callback(capture_stm, 0, NULL);
    peek_error = 0;
    mock_data = input; mock_bytes = 3; drop_error = 1;
    read_callback(capture_stm, 0, NULL);
    assert(audio_freedv_capture_stats().capture_errors == 3); /* peek, odd byte count, drop */
    assert(audio_freedv_capture_read(output, 8) == 1 && output[0] == input[0]);
    audio_freedv_capture_end();
    mock_bytes = sizeof(input); drop_error = 0;
    read_callback(capture_stm, 0, NULL);
    assert(audio_freedv_capture_read(output, 8) == 0 && dsp_seen == 15 && drops == 5);
    assert(audio_freedv_capture_begin());
    audio_freedv_capture_stats_t s = audio_freedv_capture_stats();
    assert(!s.dropped_samples && !s.hole_samples && !s.capture_errors && !s.queued_samples);
    audio_freedv_capture_end();

    /* Exercise the actual callback, bounded queue and background WAV writer
     * together; PulseAudio and DSP remain mocked and no radio is accessed. */
    char directory[] = "/tmp/freedv-pipeline-test-XXXXXX", path[160];
    assert(mkdtemp(directory));
    snprintf(path, sizeof(path), "%s/capture.wav", directory);
    freedv_wav_capture writer;
    freedv_wav_capture_init(&writer);
    assert(freedv_wav_capture_start(&writer, path, unexpected_wav_failure, NULL));
    mock_data = input; mock_bytes = sizeof(input);
    read_callback(capture_stm, 0, NULL);
    assert(freedv_wav_capture_stop(&writer) == FREEDV_WAV_OK && writer.samples == 4);
    unsigned char wave[52];
    FILE *file = fopen(path, "rb"); assert(file);
    assert(fread(wave, 1, sizeof(wave), file) == sizeof(wave) && fgetc(file) == EOF);
    fclose(file);
    const unsigned char expected[] = {42, 0, 246, 255, 123, 0, 191, 254};
    assert(!memcmp(wave, "RIFF", 4) && !memcmp(wave + 36, "data", 4));
    assert(!memcmp(wave + 44, expected, sizeof(expected)));
    unlink(path); rmdir(directory);
    puts("PASS: PulseAudio callback, holes, errors, stop, reset and WAV pipeline");
    return 0;
}
