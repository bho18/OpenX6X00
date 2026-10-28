// SPDX-License-Identifier: LGPL-2.1-or-later
#define _POSIX_C_SOURCE 200809L
#include "wav_capture.h"
#include <fcntl.h>
#include <limits.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

static void put16(unsigned char *p, unsigned value) {
    p[0] = (unsigned char)value;
    p[1] = (unsigned char)(value >> 8);
}
static void put32(unsigned char *p, uint32_t value) {
    p[0] = (unsigned char)value;
    p[1] = (unsigned char)(value >> 8);
    p[2] = (unsigned char)(value >> 16);
    p[3] = (unsigned char)(value >> 24);
}
static bool wav_header(FILE *file, uint32_t data_bytes) {
    unsigned char header[44] = {0};
    memcpy(header, "RIFF", 4); put32(header + 4, 36u + data_bytes);
    memcpy(header + 8, "WAVEfmt ", 8); put32(header + 16, 16);
    put16(header + 20, 1); put16(header + 22, 1); /* signed PCM mono */
    put32(header + 24, AUDIO_CAPTURE_RATE); put32(header + 28, AUDIO_CAPTURE_RATE * 2);
    put16(header + 32, 2); put16(header + 34, 16);
    memcpy(header + 36, "data", 4); put32(header + 40, data_bytes);
    return fwrite(header, 1, sizeof(header), file) == sizeof(header);
}
static void fail(freedv_wav_capture *capture, freedv_wav_error error) {
    if (capture->error == FREEDV_WAV_OK) {
        capture->error = error;
        capture->failure(capture->context, error);
        /* The session receives the abort request first. Stop the capture
         * producer too, without relying on the caller to notice the failure. */
        if (capture->owns_capture) audio_freedv_capture_end();
    }
}
static bool capture_failed(freedv_wav_capture *capture) {
    capture->capture_stats = capture->source.stats(capture->source.context);
    return capture->capture_stats.dropped_samples || capture->capture_stats.hole_samples ||
           capture->capture_stats.capture_errors;
}
static bool write_samples(FILE *file, const int16_t *samples, size_t count) {
    unsigned char bytes[4096 * 2];
    if (count > 4096) return false;
    for (size_t i = 0; i < count; ++i) put16(bytes + i * 2, (uint16_t)samples[i]);
    return fwrite(bytes, 2, count, file) == count;
}
static void *wav_worker(void *arg) {
    freedv_wav_capture *capture = arg;
    int16_t samples[4096];
    for (;;) {
        if (capture_failed(capture)) {
            fail(capture, FREEDV_WAV_CAPTURE_ERROR);
            break;
        }
        size_t count = capture->source.read(capture->source.context, samples, 4096);
        if (count) {
            /* RIFF's 32-bit size includes 36 bytes beyond the sample payload. */
            if (capture->samples + count > (UINT32_MAX - 36u) / 2u ||
                !write_samples(capture->file, samples, count)) {
                fail(capture, FREEDV_WAV_IO_ERROR);
                break;
            }
            capture->samples += count;
        } else if (atomic_load_explicit(&capture->stop_requested, memory_order_acquire)) {
            /* The producer was stopped before stop_requested was published;
             * take a final snapshot so a last callback error is not missed. */
            if (capture_failed(capture)) fail(capture, FREEDV_WAV_CAPTURE_ERROR);
            break;
        } else {
            struct timespec delay = {0, 10000000};
            nanosleep(&delay, NULL);
        }
    }
    /* Capture failures retain the safely written prefix for inspection. */
    if (fseek(capture->file, 0, SEEK_SET) || !wav_header(capture->file, (uint32_t)(capture->samples * 2)) ||
        fflush(capture->file)) fail(capture, FREEDV_WAV_IO_ERROR);
    if (fclose(capture->file)) fail(capture, FREEDV_WAV_IO_ERROR);
    capture->file = NULL;
    return NULL;
}

void freedv_wav_capture_init(freedv_wav_capture *capture) {
    if (!capture) return;
    memset(capture, 0, sizeof(*capture));
    atomic_init(&capture->stop_requested, false);
}

static bool start(freedv_wav_capture *capture, const char *path,
                  freedv_wav_failure_fn failure, void *context, const freedv_wav_source *source) {
    if (!capture || !path || !failure || capture->started) return false;
    capture->owns_capture = source == NULL;
    if (source) capture->source = *source;
    capture->error = FREEDV_WAV_OK;
    capture->samples = 0;
    capture->capture_stats = (audio_freedv_capture_stats_t){0};
    atomic_store_explicit(&capture->stop_requested, false, memory_order_relaxed);
    capture->failure = failure;
    capture->context = context;
    int fd = open(path, O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC, 0600);
    if (fd < 0) return false;
    capture->file = fdopen(fd, "wb");
    if (!capture->file) { close(fd); return false; }
    if (!wav_header(capture->file, 0)) { fclose(capture->file); capture->file = NULL; return false; }
    if (capture->owns_capture && !audio_freedv_capture_begin()) { fclose(capture->file); capture->file = NULL; return false; }
    if (pthread_create(&capture->worker, NULL, wav_worker, capture)) {
        if (capture->owns_capture) audio_freedv_capture_end();
        fclose(capture->file);
        capture->file = NULL;
        return false;
    }
    capture->started = true;
    return true;
}

freedv_wav_error freedv_wav_capture_stop(freedv_wav_capture *capture) {
    if (!capture || !capture->started) return FREEDV_WAV_NOT_RUNNING;
    if (capture->owns_capture) audio_freedv_capture_end();
    atomic_store_explicit(&capture->stop_requested, true, memory_order_release);
    if (pthread_join(capture->worker, NULL)) return FREEDV_WAV_JOIN_ERROR;
    capture->started = false;
    return capture->error;
}

static size_t physical_read(void *ctx, int16_t *samples, size_t count) {
    (void)ctx; return audio_freedv_capture_read(samples, count);
}
static audio_freedv_capture_stats_t physical_stats(void *ctx) {
    (void)ctx; return audio_freedv_capture_stats();
}
bool freedv_wav_capture_start(freedv_wav_capture *capture, const char *path,
                              freedv_wav_failure_fn failure, void *context) {
    if (!capture || capture->started) return false;
    capture->source = (freedv_wav_source){physical_read, physical_stats, NULL};
    return start(capture, path, failure, context, NULL);
}
bool freedv_wav_capture_start_source(freedv_wav_capture *capture, const char *path,
                                     freedv_wav_failure_fn failure, void *context,
                                     const freedv_wav_source *source) {
    if (!source || !source->read || !source->stats) return false;
    return start(capture, path, failure, context, source);
}
