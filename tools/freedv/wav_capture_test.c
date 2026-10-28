// SPDX-License-Identifier: LGPL-2.1-or-later
#define _POSIX_C_SOURCE 200809L
#define ENABLE_FREEDV_DIAGNOSTICS 1
#include "src/freedv/wav_capture.h"
#include <assert.h>
#include <fcntl.h>
#include <stdatomic.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

static pthread_mutex_t mock_mutex = PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t mock_condition = PTHREAD_COND_INITIALIZER;
static const int16_t samples[] = {0, 1, -1, 32767, -32768};
static size_t cursor;
static bool active, report_hole;
static int failure_count;
static freedv_wav_error failure_reason;
static atomic_int write_count, fail_after;

size_t __real_fwrite(const void *p, size_t size, size_t count, FILE *file);
size_t __wrap_fwrite(const void *p, size_t size, size_t count, FILE *file) {
    int current = atomic_fetch_add(&write_count, 1) + 1;
    if (atomic_load(&fail_after) && current >= atomic_load(&fail_after)) return 0;
    return __real_fwrite(p, size, count, file);
}
bool audio_freedv_capture_begin(void) {
    pthread_mutex_lock(&mock_mutex);
    bool ok = !active;
    if (ok) { active = true; cursor = 0; }
    pthread_mutex_unlock(&mock_mutex);
    return ok;
}
void audio_freedv_capture_end(void) {
    pthread_mutex_lock(&mock_mutex); active = false; pthread_mutex_unlock(&mock_mutex);
}
size_t audio_freedv_capture_read(int16_t *out, size_t capacity) {
    pthread_mutex_lock(&mock_mutex);
    size_t n = sizeof(samples) / sizeof(samples[0]) - cursor;
    if (n > capacity) n = capacity;
    memcpy(out, samples + cursor, n * sizeof(*out));
    cursor += n;
    pthread_mutex_unlock(&mock_mutex);
    return n;
}
audio_freedv_capture_stats_t audio_freedv_capture_stats(void) {
    pthread_mutex_lock(&mock_mutex);
    audio_freedv_capture_stats_t stats = {.hole_samples = report_hole ? 1u : 0u};
    pthread_mutex_unlock(&mock_mutex);
    return stats;
}
static void failed(void *context, freedv_wav_error error) {
    (void)context;
    pthread_mutex_lock(&mock_mutex);
    ++failure_count; failure_reason = error;
    pthread_cond_broadcast(&mock_condition);
    pthread_mutex_unlock(&mock_mutex);
}
static void wait_for_failure(void) {
    struct timespec deadline;
    assert(!clock_gettime(CLOCK_REALTIME, &deadline));
    deadline.tv_sec += 5;
    pthread_mutex_lock(&mock_mutex);
    while (!failure_count) assert(!pthread_cond_timedwait(&mock_condition, &mock_mutex, &deadline));
    pthread_mutex_unlock(&mock_mutex);
}
static void read_exact(const char *path, unsigned char *data, size_t bytes) {
    FILE *file = fopen(path, "rb"); assert(file);
    assert(fread(data, 1, bytes, file) == bytes && fgetc(file) == EOF);
    fclose(file);
}
static size_t queue_read(void *arg, int16_t *out, size_t capacity) {
    size_t *offset = arg;
    size_t n = 5 - *offset;
    if (n > capacity) n = capacity;
    memcpy(out, samples + *offset, n * sizeof(*out));
    *offset += n;
    return n;
}
static audio_freedv_capture_stats_t queue_stats(void *arg) {
    (void)arg; return (audio_freedv_capture_stats_t){0};
}
int main(void) {
    char directory[] = "/tmp/freedv-wav-test-XXXXXX";
    assert(mkdtemp(directory));
    char path[160], hole_path[160], fail_path[160];
    snprintf(path, sizeof(path), "%s/ok.wav", directory);
    snprintf(hole_path, sizeof(hole_path), "%s/hole.wav", directory);
    snprintf(fail_path, sizeof(fail_path), "%s/io.wav", directory);
    freedv_wav_capture capture;
    freedv_wav_capture_init(&capture);
    assert(freedv_wav_capture_stop(&capture) == FREEDV_WAV_NOT_RUNNING);
    assert(freedv_wav_capture_start(&capture, path, failed, NULL));
    assert(!freedv_wav_capture_start(&capture, hole_path, failed, NULL));
    assert(freedv_wav_capture_stop(&capture) == FREEDV_WAV_OK);
    assert(capture.samples == 5 && !failure_count);
    unsigned char file[54];
    read_exact(path, file, sizeof(file));
    assert(!memcmp(file, "RIFF", 4) && file[4] == 46 && !memcmp(file + 8, "WAVEfmt ", 8));
    assert(file[20] == 1 && file[22] == 1 && file[24] == 0x44 && file[25] == 0xac);
    assert(!memcmp(file + 36, "data", 4) && file[40] == 10);
    const unsigned char expected[] = {0,0, 1,0, 255,255, 255,127, 0,128};
    assert(!memcmp(file + 44, expected, sizeof(expected)));
    struct stat st; assert(!stat(path, &st) && (st.st_mode & 0777) == 0600);
    assert(!freedv_wav_capture_start(&capture, path, failed, NULL)); /* cannot overwrite */
    read_exact(path, file, sizeof(file));
    assert(!memcmp(file + 44, expected, sizeof(expected)));

    pthread_mutex_lock(&mock_mutex); report_hole = true; pthread_mutex_unlock(&mock_mutex);
    assert(freedv_wav_capture_start(&capture, hole_path, failed, NULL));
    wait_for_failure();
    assert(freedv_wav_capture_stop(&capture) == FREEDV_WAV_CAPTURE_ERROR);
    assert(capture.capture_stats.hole_samples == 1 && failure_count == 1 && failure_reason == FREEDV_WAV_CAPTURE_ERROR);

    pthread_mutex_lock(&mock_mutex); report_hole = false; failure_count = 0; pthread_mutex_unlock(&mock_mutex);
    atomic_store(&write_count, 0); atomic_store(&fail_after, 2); /* header succeeds, PCM write fails */
    assert(freedv_wav_capture_start(&capture, fail_path, failed, NULL));
    wait_for_failure();
    assert(freedv_wav_capture_stop(&capture) == FREEDV_WAV_IO_ERROR);
    assert(failure_count == 1 && failure_reason == FREEDV_WAV_IO_ERROR);
    atomic_store(&fail_after, 0);
    char queue_path[160]; snprintf(queue_path, sizeof(queue_path), "%s/queue.wav", directory);
    size_t queue_cursor = 0;
    freedv_wav_source queued = {queue_read, queue_stats, &queue_cursor};
    assert(!active);
    assert(freedv_wav_capture_start_source(&capture, queue_path, failed, NULL, &queued));
    assert(!active); /* external source never starts physical capture */
    assert(freedv_wav_capture_stop(&capture) == FREEDV_WAV_OK && capture.samples == 5);
    assert(!active && queue_cursor == 5);
    read_exact(queue_path, file, sizeof(file));
    assert(!memcmp(file + 44, expected, sizeof(expected)));
    unlink(queue_path);
    unlink(path); unlink(hole_path); unlink(fail_path); rmdir(directory);
    pthread_cond_destroy(&mock_condition); pthread_mutex_destroy(&mock_mutex);
    puts("PASS: WAV header, PCM, exclusive creation, capture failure and write failure");
}
