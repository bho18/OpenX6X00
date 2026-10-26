// SPDX-License-Identifier: LGPL-2.1-or-later
// 700E codec-only timing prototype. No radio or physical audio I/O.
#define _POSIX_C_SOURCE 200809L
#include <freedv_api.h>
#include <errno.h>
#include <inttypes.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/resource.h>
#include <time.h>

static uint64_t now_ns(void) {
    struct timespec t;
    if (clock_gettime(CLOCK_MONOTONIC, &t)) { perror("clock_gettime"); exit(1); }
    return (uint64_t)t.tv_sec * 1000000000ULL + (uint64_t)t.tv_nsec;
}
static int compare_double(const void *a, const void *b) {
    double x = *(const double *)a, y = *(const double *)b;
    return (x > y) - (x < y);
}
static void sleep_until(uint64_t ns) {
    struct timespec t = {(time_t)(ns / 1000000000ULL), (long)(ns % 1000000000ULL)};
    int result;
    do { result = clock_nanosleep(CLOCK_MONOTONIC, TIMER_ABSTIME, &t, NULL); } while (result == EINTR);
    if (result) { errno = result; perror("clock_nanosleep"); exit(1); }
}
int main(int argc, char **argv) {
    if (argc != 5 || (strcmp(argv[1], "tx") && strcmp(argv[1], "rx")) ||
        (strcmp(argv[4], "paced") && strcmp(argv[4], "unpaced"))) {
        fprintf(stderr, "usage: %s tx|rx input-8khz-s16.raw seconds paced|unpaced\n", argv[0]);
        return 2;
    }
    char *end;
    long seconds = strtol(argv[3], &end, 10);
    if (*end || seconds < 1 || seconds > 1800) { fprintf(stderr, "seconds must be 1..1800\n"); return 2; }
    FILE *input = fopen(argv[2], "rb");
    if (!input) { perror("input"); return 1; }
    if (fseek(input, 0, SEEK_END)) { perror("seek"); return 1; }
    long bytes = ftell(input);
    if (bytes < 2 || bytes > 128 * 1024 * 1024 || bytes % 2 || fseek(input, 0, SEEK_SET)) {
        fprintf(stderr, "fixture must be nonempty 16-bit PCM, at most 128 MiB\n"); return 1;
    }
    size_t count = (size_t)bytes / sizeof(short);
    short *fixture = malloc((size_t)bytes);
    if (!fixture || fread(fixture, sizeof(short), count, input) != count) { fprintf(stderr, "fixture read failed\n"); return 1; }
    fclose(input);
    struct freedv *f = freedv_open(FREEDV_MODE_700E);
    if (!f) { fprintf(stderr, "700E is unavailable\n"); return 1; }
    int speech_n = freedv_get_n_speech_samples(f), speech_max = freedv_get_n_max_speech_samples(f);
    int modem_max = freedv_get_n_max_modem_samples(f), modem_tx = freedv_get_n_nom_modem_samples(f);
    int speech_rate = freedv_get_speech_sample_rate(f), modem_rate = freedv_get_modem_sample_rate(f);
    if (speech_rate != 8000 || modem_rate != 8000 || speech_n <= 0 || speech_max < speech_n || modem_max <= 0 || modem_tx <= 0) {
        fprintf(stderr, "unsupported codec rates or frame sizes\n"); return 1;
    }
    size_t frame_capacity = (size_t)(speech_max > modem_max ? speech_max : modem_max);
    if ((size_t)modem_tx > frame_capacity) frame_capacity = (size_t)modem_tx;
    short *in = calloc(frame_capacity, sizeof(short)), *out = calloc(frame_capacity, sizeof(short));
    size_t capacity = 1024, frames = 0, cursor = 0;
    double *ratios = malloc(capacity * sizeof(*ratios));
    if (!in || !out || !ratios) { fprintf(stderr, "allocation failed\n"); return 1; }
    bool tx = !strcmp(argv[1], "tx"), paced = !strcmp(argv[4], "paced");
    uint64_t deadline = now_ns(), total_audio_ns = 0, total_compute_ns = 0, deadline_misses = 0;
    int64_t output_samples = 0;
    size_t sync_frames = 0;
    while (total_audio_ns < (uint64_t)seconds * 1000000000ULL) {
        int n = tx ? speech_n : freedv_nin(f);
        if (n <= 0 || (size_t)n > frame_capacity) { fprintf(stderr, "invalid codec input size\n"); return 1; }
        for (int i = 0; i < n; ++i) { in[i] = fixture[cursor++]; if (cursor == count) cursor = 0; }
        uint64_t interval = (uint64_t)n * 1000000000ULL / (uint64_t)(tx ? speech_rate : modem_rate);
        uint64_t start = now_ns();
        if (tx) { freedv_tx(f, out, in); output_samples += modem_tx; }
        else { int produced = freedv_rx(f, out, in); if (produced < 0) { fprintf(stderr, "decode failed\n"); return 1; } output_samples += produced; }
        uint64_t elapsed = now_ns() - start;
        if (!tx) {
            int sync = 0;
            float snr = 0;
            freedv_get_modem_stats(f, &sync, &snr);
            if (sync) ++sync_frames;
        }
        if (frames == capacity) {
            capacity *= 2;
            double *grown = realloc(ratios, capacity * sizeof(*ratios));
            if (!grown) { fprintf(stderr, "allocation failed\n"); return 1; }
            ratios = grown;
        }
        ratios[frames++] = (double)elapsed / (double)interval;
        total_compute_ns += elapsed;
        total_audio_ns += interval;
        deadline += interval;
        if (paced) {
            if (now_ns() > deadline) ++deadline_misses;
            else sleep_until(deadline);
        }
    }
    qsort(ratios, frames, sizeof(*ratios), compare_double);
    size_t p99_index = (frames * 99 + 99) / 100 - 1;
    struct rusage usage;
    if (getrusage(RUSAGE_SELF, &usage)) { perror("getrusage"); return 1; }
    printf("{\"schema_version\":1,\"mode\":\"700E\",\"direction\":\"%s\","
           "\"pacing\":\"%s\",\"frames\":%zu,\"audio_seconds\":%.6f,"
           "\"codec_realtime_factor\":%.6f,\"p99_frame_ratio\":%.6f,\"max_frame_ratio\":%.6f,"
           "\"deadline_misses\":%" PRIu64 ",\"output_samples\":%" PRId64 ",\"peak_rss_kib_linux\":%ld,"
           "\"sync_frames\":%zu,"
           "\"resampling_included\":false,\"radio_io\":false,\"full_pipeline_qualified\":false}\n",
           argv[1], argv[4], frames, (double)total_audio_ns / 1e9,
           (double)total_compute_ns / (double)total_audio_ns, ratios[p99_index], ratios[frames-1],
           deadline_misses, output_samples, usage.ru_maxrss, sync_frames);
    freedv_close(f);
    free(ratios); free(in); free(out); free(fixture);
    return 0;
}
