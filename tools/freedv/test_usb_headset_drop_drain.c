/* Test-only interposer: real Pulse playback, but suppress the drain callback.
 * It reproduces the client's missing-completion observation without hardware.
 * Never included in the downloadable device helper kit. */
#define _GNU_SOURCE
#include <dlfcn.h>
#include <pulse/pulseaudio.h>
pa_operation *pa_stream_drain(pa_stream *stream,pa_stream_success_cb_t callback,void *userdata) {
    (void)callback;(void)userdata;
    pa_operation *(*real_drain)(pa_stream *,pa_stream_success_cb_t,void *) =
        (pa_operation *(*)(pa_stream *,pa_stream_success_cb_t,void *))dlsym(RTLD_NEXT,"pa_stream_drain");
    return real_drain?real_drain(stream,NULL,NULL):NULL;
}
