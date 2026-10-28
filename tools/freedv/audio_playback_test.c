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
size_t pa_stream_writable_size(const pa_stream *s) { assert(s == (pa_stream *)&token_stream && locks == 1); return writable; }
int pa_stream_write(pa_stream *s, const void *data, size_t bytes, pa_free_cb_t cb, int64_t offset, pa_seek_mode_t seek) {
    assert(s == (pa_stream *)&token_stream && locks == 1 && data && !cb && !offset && seek == PA_SEEK_RELATIVE);
    ++writes; if (write_error) return -1; written += bytes; return 0;
}
int pa_stream_get_latency(pa_stream *s, pa_usec_t *latency, int *negative) {
    assert(s == (pa_stream *)&token_stream && locks == 1); *latency = 42000; *negative = latency_negative; return latency_error;
}
void pa_stream_set_underflow_callback(pa_stream *s, pa_stream_notify_cb_t cb, void *userdata) {
    assert(s == (pa_stream *)&token_stream && locks == 1);
    assert((cb == diagnostic_playback_underflow && userdata) || (!cb && !userdata));
}
static pa_stream_success_cb_t flush_callback;
static void *flush_userdata;
static pa_buffer_attr actual_attr = {.maxlength=17640, .tlength=8820, .prebuf=5292, .minreq=882};
const pa_buffer_attr *pa_stream_get_buffer_attr(pa_stream *s) { (void)s; assert(locks == 1); return &actual_attr; }
void pa_operation_cancel(pa_operation *op) { assert(op == (pa_operation *)&token_operation && locks == 1); flush_callback = NULL; flush_userdata = NULL; }
pa_operation *pa_stream_flush(pa_stream *s, pa_stream_success_cb_t cb, void *userdata) {
    assert(s == (pa_stream *)&token_stream && locks == 1 && cb && userdata);
    flush_callback = cb; flush_userdata = userdata;
    return flush_ok ? (pa_operation *)&token_operation : NULL;
}
void pa_operation_unref(pa_operation *op) { assert(op == (pa_operation *)&token_operation); ++flush_unrefs; }
int pa_stream_disconnect(pa_stream *s) { assert(s == (pa_stream *)&token_stream && locks == 1); ++disconnects; return 0; }
void pa_stream_unref(pa_stream *s) { assert(s == (pa_stream *)&token_stream && locks == 1); }


static void *cancel_worker(void *arg) {
    assert(audio_freedv_playback_cancel(arg));
    return NULL;
}
int main(void) {
    int16_t pcm[] = {1,2,3,4,5};
    assert(!audio_freedv_playback_open());
    mloop = (pa_threaded_mainloop *)&token_loop;
    audio_freedv_playback_t *p = audio_freedv_playback_open();
    assert(fd_writer_reserve());
    assert(audio_play(pcm, 5) == -1);
    assert(!audio_play_if_room(pcm, 5));
    assert(!audio_play_lowlat_begin());
    fd_writer_release();
    assert(p && !bt_stm); /* independent of the worker's network/Bluetooth TLS */
    assert(audio_freedv_playback_observe(p).maxlength_bytes == 17640);
    writable = 5;
    assert(audio_freedv_playback_submit(p, pcm, 5) == 2);
    writable = 0;
    assert(audio_freedv_playback_submit(p, pcm + 2, 3) == 0);
    writable = 6;
    assert(audio_freedv_playback_submit(p, pcm + 2, 3) == 3);
    assert(written == sizeof(pcm) && writes == 2);
    assert(audio_freedv_playback_observe(p).accepted_samples == 5);
    assert(audio_freedv_playback_submit(p, NULL, 0) == 0);
    assert(audio_freedv_playback_submit(p, NULL, 1) == -1);
    assert(audio_freedv_playback_submit(p, pcm, SIZE_MAX) == -1);
    writable = (size_t)-1;
    assert(audio_freedv_playback_submit(p, pcm, 1) == -1);
    writable = 2; write_error = 1;
    assert(audio_freedv_playback_submit(p, pcm, 1) == -1);
    test_state = PA_STREAM_FAILED;
    assert(audio_freedv_playback_submit(p, pcm, 1) == -1);
    assert(audio_freedv_playback_observe(p).write_errors == 5);
    assert(audio_freedv_playback_observe(p).queued_latency_us == 42000);
    latency_negative = 1;
    assert(!audio_freedv_playback_observe(p).latency_known);
    latency_negative = 0; latency_error = -1;
    assert(!audio_freedv_playback_observe(p).latency_known);
    diagnostic_playback_underflow(p->stream, p);
    assert(audio_freedv_playback_observe(p).underflows == 1);
    pa_threaded_mainloop_lock(mloop);
    audio_freedv_playback_request_cancel(p); /* must work even while PA lock is held */
    pa_threaded_mainloop_unlock(mloop);
    assert(audio_freedv_playback_observe(p).cancelled);
    assert(!audio_freedv_playback_observe(p).flush_acknowledged);
    assert(!audio_freedv_playback_cancel(p)); /* failed request is not acknowledgement */
    assert(audio_freedv_playback_observe(p).cancelled);
    assert(!audio_freedv_playback_observe(p).flush_acknowledged);
    flush_ok = 1;
    pthread_t worker;
    assert(!pthread_create(&worker, NULL, cancel_worker, p));
    assert(!pthread_join(worker, NULL)); /* watchdog can reach the owner's stream */
    assert(!audio_freedv_playback_observe(p).flush_acknowledged);
    assert(audio_freedv_playback_submit(p, pcm, 1) == -1); /* late worker output fenced */
    pa_threaded_mainloop_lock(mloop);
    flush_callback(p->stream, 1, flush_userdata);
    pa_threaded_mainloop_unlock(mloop);
    assert(audio_freedv_playback_observe(p).flush_acknowledged);
    assert(audio_freedv_playback_observe(p).flushes == 1);
    audio_freedv_playback_close(p);
    assert(disconnects == 1 && flush_unrefs == 1 && !locks && !flush_callback);
    test_state = PA_STREAM_READY;
    assert(audio_freedv_playback_begin());
    assert(!audio_freedv_playback_begin());
    assert(!audio_freedv_playback_stats().accepted_samples);
    audio_freedv_playback_end();
    audio_freedv_playback_end();
    assert(disconnects == 2);
    puts("PASS: cross-thread cancellation, acknowledged flush, partial writes and finite buffer attributes");
    return 0;
}
