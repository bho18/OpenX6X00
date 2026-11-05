// SPDX-License-Identifier: LGPL-2.1-or-later
// 700E in-memory timing prototype. No radio or physical audio I/O.
#define _POSIX_C_SOURCE 200809L
#include <freedv_api.h>
#include <samplerate.h>
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
/* Adapt a repeated 44.1 kHz mono fixture to one codec frame. Both cursors and
 * filter state survive frame and fixture boundaries. */
static void read_resampled(SRC_STATE *state, const float *fixture, size_t count,
                           size_t *cursor, float *output, size_t wanted) {
    float chunk[441];
    size_t produced = 0;
    while (produced < wanted) {
        for (size_t i = 0; i < 441; ++i) chunk[i] = fixture[(*cursor + i) % count];
        SRC_DATA d = {.data_in = chunk, .input_frames = 441, .data_out = output + produced,
                      .output_frames = (long)(wanted - produced), .src_ratio = 8000.0 / 44100.0};
        int error = src_process(state, &d);
        if (error || (!d.input_frames_used && !d.output_frames_gen)) {
            fprintf(stderr, "input resampling failed: %s\n", src_strerror(error)); exit(1);
        }
        *cursor = (*cursor + (size_t)d.input_frames_used) % count;
        produced += (size_t)d.output_frames_gen;
    }
}
static size_t write_resampled(SRC_STATE *state, const float *input, size_t count,
                              float *output, short *pcm, size_t output_capacity) {
    size_t used = 0, generated = 0;
    while (used < count) {
        SRC_DATA d = {.data_in = input + used, .input_frames = (long)(count - used),
                      .data_out = output, .output_frames = (long)output_capacity,
                      .src_ratio = 44100.0 / 8000.0};
        int error = src_process(state, &d);
        if (error || (!d.input_frames_used && !d.output_frames_gen)) {
            fprintf(stderr, "output resampling failed: %s\n", src_strerror(error)); exit(1);
        }
        used += (size_t)d.input_frames_used;
        src_float_to_short_array(output, pcm, (int)d.output_frames_gen);
        generated += (size_t)d.output_frames_gen;
    }
    return generated;
}
int main(int argc, char **argv) {
    if ((argc != 5 && argc != 7) || (strcmp(argv[1], "tx") && strcmp(argv[1], "rx")) ||
        (strcmp(argv[4], "paced") && strcmp(argv[4], "unpaced")) || (argc == 7 && strcmp(argv[5], "resample"))) {
        fprintf(stderr, "usage: %s tx|rx input-8khz-s16.raw seconds paced|unpaced [resample warmup_seconds]\n", argv[0]);
        return 2;
    }
    char *end;
    long seconds = strtol(argv[3], &end, 10);
    if (*end || seconds < 1 || seconds > 1800) { fprintf(stderr, "seconds must be 1..1800\n"); return 2; }
    long warmup_seconds = 0;
    if (argc == 7) {
        warmup_seconds = strtol(argv[6], &end, 10);
        if (*end || warmup_seconds < 0 || warmup_seconds > 60) { fprintf(stderr, "warmup must be 0..60 seconds\n"); return 2; }
    }
    FILE *input = fopen(argv[2], "rb");
    if (!input) { perror("input"); return 1; }
    if (fseek(input, 0, SEEK_END)) { perror("seek"); return 1; }
    long bytes = ftell(input);
    if (bytes < 2 || bytes > 1024 * 1024 || bytes % 2 || fseek(input, 0, SEEK_SET)) {
        fprintf(stderr, "fixture must be nonempty 16-bit PCM, at most 1 MiB\n"); return 1;
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
    bool resample = argc == 7;
    SRC_STATE *input_src = NULL, *output_src = NULL;
    float *fixture_44100 = NULL, *frame_float = NULL, *output_44100 = NULL;
    short *output_pcm = NULL;
    size_t count_44100 = 0, output_capacity = frame_capacity * 7 + 512;
    if (resample) {
        float *original = malloc(count * sizeof(*original));
        size_t converted_capacity = count * 6 + 512;
        fixture_44100 = malloc(converted_capacity * sizeof(*fixture_44100));
        frame_float = malloc(frame_capacity * sizeof(*frame_float));
        output_44100 = malloc(output_capacity * sizeof(*output_44100));
        output_pcm = malloc(output_capacity * sizeof(*output_pcm));
        if (!original || !fixture_44100 || !frame_float || !output_44100 || !output_pcm) { fprintf(stderr, "allocation failed\n"); return 1; }
        src_short_to_float_array(fixture, original, (int)count);
        SRC_DATA d = {.data_in = original, .input_frames = (long)count, .data_out = fixture_44100,
                      .output_frames = (long)converted_capacity, .src_ratio = 44100.0 / 8000.0, .end_of_input = 1};
        int error = src_simple(&d, SRC_SINC_FASTEST, 1);
        free(original);
        if (error || d.input_frames_used != (long)count || d.output_frames_gen <= 0) {
            fprintf(stderr, "fixture conversion failed: %s\n", src_strerror(error)); return 1;
        }
        count_44100 = (size_t)d.output_frames_gen;
        input_src = src_new(SRC_SINC_FASTEST, 1, &error);
        if (!input_src) { fprintf(stderr, "input resampler: %s\n", src_strerror(error)); return 1; }
        output_src = src_new(SRC_SINC_FASTEST, 1, &error);
        if (!output_src) { fprintf(stderr, "output resampler: %s\n", src_strerror(error)); return 1; }
    }
    size_t capacity = 1024, frames = 0, cursor = 0;
    double *ratios = malloc(capacity * sizeof(*ratios));
    if (!in || !out || !ratios) { fprintf(stderr, "allocation failed\n"); return 1; }
    bool tx = !strcmp(argv[1], "tx"), paced = !strcmp(argv[4], "paced");
    uint64_t deadline = now_ns(), total_audio_ns = 0, total_compute_ns = 0, deadline_misses = 0, warmup_ns = 0;
    int64_t output_samples = 0;
    uint64_t resampled_output_samples = 0;
    size_t sync_frames = 0;
    while (total_audio_ns < (uint64_t)seconds * 1000000000ULL) {
        int n = tx ? speech_n : freedv_nin(f);
        if (n <= 0 || (size_t)n > frame_capacity) { fprintf(stderr, "invalid codec input size\n"); return 1; }
        uint64_t interval = (uint64_t)n * 1000000000ULL / (uint64_t)(tx ? speech_rate : modem_rate);
        bool warming = warmup_ns < (uint64_t)warmup_seconds * 1000000000ULL;
        if (!warming && frames == 0) deadline = now_ns();
        uint64_t start = now_ns();
        if (resample) {
            read_resampled(input_src, fixture_44100, count_44100, &cursor, frame_float, (size_t)n);
            src_float_to_short_array(frame_float, in, n);
        } else {
            for (int i = 0; i < n; ++i) { in[i] = fixture[cursor++]; if (cursor == count) cursor = 0; }
        }
        int produced;
        if (tx) { freedv_tx(f, out, in); produced = modem_tx; }
        else { produced = freedv_rx(f, out, in); if (produced < 0 || (size_t)produced > frame_capacity) { fprintf(stderr, "decode failed\n"); return 1; } }
        size_t output_44100_count = 0;
        if (resample && produced) {
            src_short_to_float_array(out, frame_float, produced);
            output_44100_count = write_resampled(output_src, frame_float, (size_t)produced,
                                                 output_44100, output_pcm, output_capacity);
        }
        if (!tx) {
            int sync = 0;
            float snr = 0;
            freedv_get_modem_stats(f, &sync, &snr);
            if (sync && !warming) ++sync_frames;
        }
        uint64_t elapsed = now_ns() - start;
        if (!warming && frames == capacity) {
            capacity *= 2;
            double *grown = realloc(ratios, capacity * sizeof(*ratios));
            if (!grown) { fprintf(stderr, "allocation failed\n"); return 1; }
            ratios = grown;
        }
        if (warming) warmup_ns += interval;
        else {
            ratios[frames++] = (double)elapsed / (double)interval;
            total_compute_ns += elapsed;
            total_audio_ns += interval;
            output_samples += produced;
            resampled_output_samples += output_44100_count;
        }
        deadline += interval;
        if (paced) {
            if (now_ns() > deadline) { if (!warming) ++deadline_misses; }
            else sleep_until(deadline);
        }
    }
    qsort(ratios, frames, sizeof(*ratios), compare_double);
    size_t p99_index = (frames * 99 + 99) / 100 - 1;
    struct rusage usage;
    if (getrusage(RUSAGE_SELF, &usage)) { perror("getrusage"); return 1; }
    char misses[32];
    if (paced) snprintf(misses, sizeof(misses), "%" PRIu64, deadline_misses);
    else snprintf(misses, sizeof(misses), "null");
    printf("{\"schema_version\":1,\"mode\":\"700E\",\"direction\":\"%s\","
           "\"pacing\":\"%s\",\"frames\":%zu,\"audio_seconds\":%.6f,"
           "\"processing_realtime_factor\":%.6f,\"p99_frame_ratio\":%.6f,\"max_frame_ratio\":%.6f,"
           "\"deadline_misses\":%s,\"output_samples\":%" PRId64 ",\"peak_rss_kib_linux\":%ld,"
           "\"resampled_output_samples\":%" PRIu64 ","
           "\"sync_frames\":%zu,\"warmup_audio_seconds\":%.6f,"
           "\"resampling_included\":%s,\"radio_io\":false,\"full_pipeline_qualified\":false}\n",
           argv[1], argv[4], frames, (double)total_audio_ns / 1e9,
           (double)total_compute_ns / (double)total_audio_ns, ratios[p99_index], ratios[frames-1],
           misses, output_samples, usage.ru_maxrss, resampled_output_samples, sync_frames, (double)warmup_ns / 1e9,
           resample ? "true" : "false");
    freedv_close(f);
    if (input_src) src_delete(input_src);
    if (output_src) src_delete(output_src);
    free(fixture_44100); free(frame_float); free(output_44100); free(output_pcm);
    free(ratios); free(in); free(out); free(fixture);
    return 0;
}
