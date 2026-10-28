// SPDX-License-Identifier: LGPL-2.1-or-later
/* Exercise the production adapter with short writes and PulseAudio failures. */
#define ENABLE_FREEDV_DIAGNOSTICS 1
#include "src/audio.c"
#include <assert.h>

static int token_loop, token_stream, locks, writes, disconnects;
static size_t writable, written;
static int write_error, latency_error, latency_negative;
static int token_operation, flush_ok, flush_unrefs, connect_error;
static pa_stream_state_t test_state = PA_STREAM_READY;
void pa_threaded_mainloop_lock(pa_threaded_mainloop *m) { assert(m == mloop && !locks); ++locks; }
void pa_threaded_mainloop_unlock(pa_threaded_mainloop *m) { assert(m == mloop && locks == 1); --locks; }
pa_stream_state_t pa_stream_get_state(const pa_stream *s) { assert(s == (pa_stream *)&token_stream && locks == 1); return test_state; }
pa_stream *pa_stream_new(pa_context *c, const char *name, const pa_sample_spec *spec, const pa_channel_map *map) {
    (void)c; (void)name; assert(spec->channels == 1 && !map && locks == 1); return (pa_stream *)&token_stream;
}
void pa_stream_set_state_callback(pa_stream *s, pa_stream_notify_cb_t cb, void *userdata) {
    (void)s; (void)cb; assert(!userdata && locks == 1);
}
int pa_stream_connect_playback(pa_stream *s, const char *device, const pa_buffer_attr *attr,
                               pa_stream_flags_t flags, const pa_cvolume *volume, pa_stream *sync) {
    (void)s; (void)device; (void)flags; assert(attr && !volume && !sync && locks == 1); return connect_error;
}
void pa_threaded_mainloop_wait(pa_threaded_mainloop *m) { (void)m; assert(0); }
size_t pa_usec_to_bytes(pa_usec_t usec, const pa_sample_spec *spec) { return usec * spec->rate * spec->channels * 2 / 1000000; }
int pa_context_errno(const pa_context *c) { (void)c; return 0; }
const char *pa_strerror(int err) { (void)err; return "test failure"; }
void _lv_log_add(lv_log_level_t level, const char *file, int line, const char *func, const char *format, ...) {
    (void)level; (void)file; (void)line; (void)func; (void)format;
}
size_t pa_stream_writable_size(const pa_stream *s) { assert(s == bt_stm && locks == 1); return writable; }
int pa_stream_write(pa_stream *s, const void *data, size_t bytes, pa_free_cb_t cb, int64_t offset, pa_seek_mode_t seek) {
    assert(s == bt_stm && locks == 1 && data && !cb && !offset && seek == PA_SEEK_RELATIVE);
    ++writes; if (write_error) return -1; written += bytes; return 0;
}
int pa_stream_get_latency(pa_stream *s, pa_usec_t *latency, int *negative) {
    assert(s == bt_stm && locks == 1); *latency = 42000; *negative = latency_negative; return latency_error;
}
void pa_stream_set_underflow_callback(pa_stream *s, pa_stream_notify_cb_t cb, void *userdata) {
    assert(s == bt_stm && locks == 1);
    assert((cb == diagnostic_playback_underflow && userdata == &diagnostic_playback_stats) || (!cb && !userdata));
}
pa_operation *pa_stream_flush(pa_stream *s, pa_stream_success_cb_t cb, void *userdata) {
    assert(s == bt_stm && locks == 1 && !cb && !userdata);
    return flush_ok ? (pa_operation *)&token_operation : NULL;
}
void pa_operation_unref(pa_operation *op) { assert(op == (pa_operation *)&token_operation); ++flush_unrefs; }
int pa_stream_disconnect(pa_stream *s) { assert(s == bt_stm && locks == 1); ++disconnects; return 0; }
void pa_stream_unref(pa_stream *s) { assert(s == bt_stm && locks == 1); }

int main(void) {
    int16_t pcm[] = {1,2,3,4,5};
    assert(audio_freedv_playback_write(pcm, 5) == -1);
    assert(!audio_freedv_playback_begin()); /* audio subsystem unavailable */
    mloop = (pa_threaded_mainloop *)&token_loop;
    assert(audio_freedv_playback_begin());
    assert(!audio_freedv_playback_begin()); /* cannot replace active stream */
    writable = 5; /* odd byte room: only two complete samples fit */
    assert(audio_freedv_playback_write(pcm, 5) == 2);
    writable = 0;
    assert(audio_freedv_playback_write(pcm + 2, 3) == 0);
    writable = 6;
    assert(audio_freedv_playback_write(pcm + 2, 3) == 3);
    assert(written == sizeof(pcm) && writes == 2);
    assert(audio_freedv_playback_stats().accepted_samples == 5);
    assert(audio_freedv_playback_write(NULL, 0) == 0);
    assert(audio_freedv_playback_write(NULL, 1) == -1);
    assert(audio_freedv_playback_write(pcm, SIZE_MAX) == -1);
    writable = (size_t)-1;
    assert(audio_freedv_playback_write(pcm, 1) == -1);
    writable = 2; write_error = 1;
    assert(audio_freedv_playback_write(pcm, 1) == -1);
    test_state = PA_STREAM_FAILED;
    assert(audio_freedv_playback_write(pcm, 1) == -1);
    assert(audio_freedv_playback_stats().write_errors == 5);
    assert(audio_freedv_playback_stats().accepted_samples == 5);
    assert(audio_freedv_playback_stats().queued_latency_us == 42000);
    latency_negative = 1;
    assert(!audio_freedv_playback_stats().latency_known);
    latency_negative = 0; latency_error = -1;
    assert(!audio_freedv_playback_stats().latency_known);
    diagnostic_playback_underflow(bt_stm, &diagnostic_playback_stats);
    assert(audio_freedv_playback_stats().underflows == 1);
    audio_freedv_playback_end();
    assert(disconnects == 1 && !bt_stm && !diagnostic_playback_active);
    assert(audio_freedv_playback_stats().write_errors == 6); /* flush failure */
    assert(!audio_freedv_playback_stats().latency_known);
    audio_freedv_playback_end();
    assert(disconnects == 1 && !locks);
    test_state = PA_STREAM_READY;
    assert(audio_freedv_playback_begin());
    assert(!audio_freedv_playback_stats().write_errors && !audio_freedv_playback_stats().accepted_samples);
    flush_ok = 1;
    audio_freedv_playback_end();
    assert(disconnects == 2 && flush_unrefs == 1 && audio_freedv_playback_stats().flushes == 1);
    puts("PASS: partial playback, no room, error accounting, latency, underflow and stop");
    return 0;
}
