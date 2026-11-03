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
static int operation_token, flush_unref;
static pa_stream_success_cb_t capture_flush_callback;
pa_operation *pa_stream_flush(pa_stream *s, pa_stream_success_cb_t cb, void *data) {
    assert(s == capture_stm && lock_depth == 1 && !data);
    capture_flush_callback = cb; return (pa_operation *)&operation_token;
}
void pa_operation_cancel(pa_operation *p) { assert(p == (pa_operation *)&operation_token && lock_depth == 1); capture_flush_callback = NULL; }
void pa_operation_unref(pa_operation *p) { assert(p == (pa_operation *)&operation_token); ++flush_unref; }

void pa_threaded_mainloop_lock(pa_threaded_mainloop *m) { assert(m == mloop); assert(!lock_depth); ++lock_depth; }
void pa_threaded_mainloop_unlock(pa_threaded_mainloop *m) { assert(m == mloop); assert(lock_depth == 1); --lock_depth; }
void pa_threaded_mainloop_signal(pa_threaded_mainloop *m,int wait) { assert(m == mloop && lock_depth == 1 && !wait); }
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

    /* Server capture flush must acknowledge before the next boundary starts;
     * this is separate from resetting the application's PCM ring. */
    assert(audio_freedv_capture_flush_begin());
    assert(audio_freedv_capture_flushed() == 0);
    capture_flush_callback(capture_stm, 0, NULL);
    assert(audio_freedv_capture_flushed() == -1);
    assert(audio_freedv_capture_flush_begin());
    capture_flush_callback(capture_stm, 1, NULL);
    assert(audio_freedv_capture_flushed() == 1);
    audio_freedv_capture_flush_close();
    assert(flush_unref == 2 && !capture_flush_callback);
    mock_bytes = sizeof(input); drop_error = 0;
    read_callback(capture_stm, 0, NULL);
    assert(audio_freedv_capture_read(output, 8) == 0 && dsp_seen == 15 && drops == 5);
    assert(audio_freedv_capture_begin());
    audio_freedv_capture_stats_t s = audio_freedv_capture_stats();
    assert(!s.dropped_samples && !s.hole_samples && !s.capture_errors && !s.queued_samples);
    pa_threaded_mainloop_lock(mloop);
    mock_state = PA_STREAM_FAILED;
    diagnostic_stereo_state(capture_stm,NULL);
    pa_threaded_mainloop_unlock(mloop);
    assert(audio_freedv_capture_stats().capture_errors == 1);
    mock_state = PA_STREAM_READY;
    audio_freedv_capture_end();

    /* Exercise the actual callback, bounded queue and background WAV writer
     * together; PulseAudio and DSP remain mocked and no radio is accessed. */
    /* The legacy callback must neither duplicate voice input nor run a
     * competing consumer. Only the retained voice callback admits PCM. */
    voice_capture = capture_stm; voice_capture_channels = 1;
    unsigned before = dsp_seen;
    assert(audio_freedv_capture_begin());
    read_callback(capture_stm, 0, NULL);
    assert(dsp_seen == before && audio_freedv_capture_read(output, 8) == 0);
    voice_capture_read(capture_stm, 0, NULL);
    assert(audio_freedv_capture_read(output, 8) == 4 && dsp_seen == before);
    s = audio_freedv_capture_stats();
    assert(s.callbacks == 1 && s.largest_callback_samples == 4);
    /* Model a resumed callback after the observed server pause. Its elapsed
     * gap remains visible even when the worker next sees a fresh timestamp. */
    atomic_store(&capture_last_ms,capture_clock_ms()-773);
    voice_capture_read(capture_stm, 0, NULL);
    s=audio_freedv_capture_stats();
    assert(s.callbacks==2 && s.longest_inter_callback_gap_ms>=773 && s.longest_callback_gap_ms>=773);
    assert(audio_freedv_capture_read(output,8)==4);
    audio_freedv_capture_end();
    voice_capture_read(capture_stm, 0, NULL);
    assert(audio_freedv_capture_read(output, 8) == 0);
    assert(audio_freedv_capture_begin());
    assert(audio_freedv_capture_stats().callbacks == 0);
    assert(!audio_freedv_capture_stats().longest_inter_callback_gap_ms);
    atomic_store(&capture_started_ms,capture_clock_ms()-1001);
    voice_capture_read(capture_stm,0,NULL);
    s=audio_freedv_capture_stats();
    assert(s.first_callback_delay_ms>=1001 && !s.longest_inter_callback_gap_ms);
    audio_freedv_capture_end(); voice_capture = NULL;
    read_callback(capture_stm, 0, NULL);
    assert(dsp_seen == before + 4);

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
