/* Experimental differential test, no radio or audio-device access.
 * Compile the pinned reference rade_acq.c with renamed public symbols. */
#include "rade_acq.h"
#include "rade_acq_parallel.h"
#include <float.h>
#include <fenv.h>
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

extern int reference_rade_acq_detect_pilots(rade_acq *, const RADE_COMP *, int *, float *);
static rade_acq *seed_acq, *reference, *candidate;
static RADE_COMP *input;
static size_t input_count;
#ifndef RADE_ACQ_EXPECT_NEON
#define RADE_ACQ_EXPECT_NEON 0
#endif
extern int rade_acq_test_neon_used(void);
static unsigned cases, strict_failures, path_failures, neon_cases, scalar_cases, parallel_cases;
static unsigned workers;
static struct rade_acq_pool *pool;
static uint32_t rng;

static uint32_t bits(float x) { uint32_t r; memcpy(&r, &x, 4); return r; }
static float random_unit(void) {
    rng ^= rng << 13; rng ^= rng >> 17; rng ^= rng << 5;
    return ((rng >> 8) + 0.5f) / 16777216.0f;
}
static void noise(float scale) {
    rng = 0x57d314a9u;
    for (size_t n = 0; n < input_count; n++) {
        float radius = sqrtf(-2.0f * logf(random_unit())) * scale;
        float phase = 2.0f * (float)M_PI * random_unit();
        input[n].real = radius * cosf(phase);
        input[n].imag = radius * sinf(phase);
    }
}
static void pilots(int nmf, int t, float hz, float amplitude) {
    for (int p = 0; p < 2; p++) {
        for (int n = 0; n < RADE_M; n++) {
            int pos = t + p * nmf + n;
            if (pos < 0 || (size_t)pos >= input_count) abort();
            RADE_COMP shifted = rade_cmul(seed_acq->p[n],
                rade_cexp(2.0f * (float)M_PI * hz * pos / RADE_FS));
            input[pos] = rade_cadd(input[pos], rade_cscale(shifted, amplitude));
        }
    }
}
static const char *json_float(float value, char buffer[40]) {
    if (isfinite(value)) snprintf(buffer, 40, "%.9g", value);
    else strcpy(buffer, "null");
    return buffer;
}
static int compare(const char *name, int nmf, int nfreq, int expect_vector) {
    memcpy(reference, seed_acq, sizeof(*reference));
    memcpy(candidate, seed_acq, sizeof(*candidate));
    reference->nmf = candidate->nmf = nmf;
    reference->n_fcoarse = candidate->n_fcoarse = nfreq;
    int rt = -1, ct = -1;
    float rf = NAN, cf = NAN;
    int rd = reference_rade_acq_detect_pilots(reference, input, &rt, &rf);
    struct rade_acquisition_stats before, after;
    rade_acq_pool_stats(pool,&before);
    int cd = rade_acq_pool_detect(pool,candidate,input,&ct,&cf);
    rade_acq_pool_stats(pool,&after);
    int expected_parallel=RADE_ACQ_EXPECT_NEON && workers>1 && expect_vector && nmf%4==0 && nmf/4>=(int)workers;
    int parallel_used=after.parallel_calls>before.parallel_calls;
    parallel_cases += parallel_used;
    if(parallel_used!=expected_parallel)path_failures++;
    if(after.calls!=before.calls+1 || after.parallel_calls+after.serial_calls!=after.calls)path_failures++;
    for(unsigned i=0;i<3;i++) {
        uint64_t delta=after.helper_jobs[i]-before.helper_jobs[i];
        if(delta!=(uint64_t)(expected_parallel && i+1<workers))path_failures++;
    }
    int used_neon = parallel_used || rade_acq_test_neon_used();
    int expected_neon = RADE_ACQ_EXPECT_NEON && expect_vector;
    int expected_path = used_neon == expected_neon && parallel_used==expected_parallel;
    if (used_neon) neon_cases++; else scalar_cases++;
    if (!expected_path) path_failures++;
    unsigned bitdiff = 0, valuediff = 0, nonfinite = 0;
    double max_abs = 0, sum_squared = 0;
    for (int g = 0; g < 2; g++) {
        for (int t = 0; t < RADE_NMF; t++) {
          for (int f = 0; f < RADE_ACQ_NFREQ; f++) {
            RADE_COMP rr = g ? reference->Dt2[t][f] : reference->Dt1[t][f];
            RADE_COMP cc = g ? candidate->Dt2[t][f] : candidate->Dt1[t][f];
            float rv[2] = { rr.real, rr.imag };
            float cv[2] = { cc.real, cc.imag };
            for (int k = 0; k < 2; k++) {
                int different_bits = bits(rv[k]) != bits(cv[k]);
                bitdiff += different_bits;
                if (!isfinite(rv[k]) || !isfinite(cv[k])) {
                    nonfinite++;
                    valuediff += different_bits;
                } else {
                    valuediff += rv[k] != cv[k];
                    double d = fabs((double)rv[k] - cv[k]);
                    if (d > max_abs) max_abs = d;
                    sum_squared += d * d;
                }
            }
          }
        }
    }
    int exact = bitdiff == 0 && rd == cd && rt == ct && bits(rf) == bits(cf)
        && bits(reference->Dthresh) == bits(candidate->Dthresh)
        && bits(reference->Dtmax12) == bits(candidate->Dtmax12)
        && reference->f_ind_max == candidate->f_ind_max;
    int equal_decision = rd == cd && rt == ct && bits(rf) == bits(cf);
    cases++;
    if (!exact) strict_failures++;
    char floats[6][40];
    printf("{\"case\":\"%s\",\"nmf\":%d,\"nfreq\":%d,"
           "\"workers\":%u,\"parallel_used\":%s,\"expected_parallel\":%s,\"neon_used\":%s,\"expected_neon\":%s,\"path_expected\":%s,"
           "\"bit_exact\":%s,\"decision_equal\":%s,\"grid_bit_differences\":%u,"
           "\"grid_value_differences\":%u,\"nonfinite_values\":%u,"
           "\"max_absolute_grid_difference\":%.17g,\"rms_grid_difference\":%.17g,"
           "\"reference_detection\":%d,\"candidate_detection\":%d,"
           "\"reference_t\":%d,\"candidate_t\":%d,"
           "\"reference_f\":%s,\"candidate_f\":%s,"
           "\"reference_threshold\":%s,\"candidate_threshold\":%s,"
           "\"reference_max\":%s,\"candidate_max\":%s}\n",
           name, nmf, nfreq, workers, parallel_used?"true":"false",expected_parallel?"true":"false",used_neon ? "true" : "false",
           expected_neon ? "true" : "false", expected_path ? "true" : "false",
           exact ? "true" : "false", equal_decision ? "true" : "false", bitdiff, valuediff,
           nonfinite, max_abs, sqrt(sum_squared / (4 * RADE_NMF * RADE_ACQ_NFREQ)),
           rd, cd, rt, ct, json_float(rf, floats[0]), json_float(cf, floats[1]),
           json_float(reference->Dthresh, floats[2]), json_float(candidate->Dthresh, floats[3]),
           json_float(reference->Dtmax12, floats[4]), json_float(candidate->Dtmax12, floats[5]));
    fflush(stdout);
    return rd;
}
int main(int argc, char **argv) {
    char *end = NULL;
    long requested = argc == 2 ? strtol(argv[1], &end, 10) : 0;
    if (argc != 2 || !end || *end || requested < 1 || requested > 4) return 2;
    workers = (unsigned)requested;
    if (rade_acq_pool_create(workers,&pool)) { fprintf(stderr, "pool creation failed\n"); return 2; }
    if(rade_acq_pool_workers(pool)!=workers)return 2;
    seed_acq = calloc(1, sizeof(*seed_acq));
    reference = malloc(sizeof(*reference)); candidate = malloc(sizeof(*candidate));
    input_count = 2 * RADE_NMF + RADE_M + RADE_NCP;
    input = calloc(input_count, sizeof(*input));
    rade_ofdm *ofdm = malloc(sizeof(*ofdm));
    if (!seed_acq || !reference || !candidate || !input || !ofdm) return 2;
    rade_ofdm_init(ofdm, 3);
    rade_acq_init(seed_acq, ofdm, RADE_ACQ_FRANGE, RADE_ACQ_FSTEP);
    compare("zero-full", RADE_NMF, RADE_ACQ_NFREQ, 1);
    noise(0.1f); compare("noise-full", RADE_NMF, RADE_ACQ_NFREQ, 1);
    noise(1000.0f); compare("high-level-noise", RADE_NMF, RADE_ACQ_NFREQ, 1);
    noise(1.0e-12f); compare("low-normal-noise", RADE_NMF, RADE_ACQ_NFREQ, 0);
    const float cfos[] = {-50.0f, -48.75f, -2.5f, 0.0f, 1.25f, 47.5f, 49.9f};
    for (size_t i = 0; i < sizeof(cfos)/sizeof(cfos[0]); i++) {
        char name[64]; snprintf(name, sizeof(name), "pilot-cfo-%g", cfos[i]);
        noise(0.03f); pilots(RADE_NMF, 173, cfos[i], 1.0f);
        compare(name, RADE_NMF, RADE_ACQ_NFREQ, 1);
    }
    for (int t = 0; t <= RADE_NMF - 1; t += RADE_NMF - 1) {
        char name[64]; snprintf(name, sizeof(name), "pilot-timing-boundary-%d", t);
        noise(0.01f); pilots(RADE_NMF, t, 0, 1);
        compare(name, RADE_NMF, RADE_ACQ_NFREQ, 1);
    }
    for (int rem = 1; rem <= 3; rem++) {
        char name[64]; snprintf(name, sizeof(name), "timing-remainder-%d", rem);
        noise(0.03f); pilots(RADE_NMF - 4 + rem, 173, 0, 1);
        compare(name, RADE_NMF - 4 + rem, RADE_ACQ_NFREQ - rem, 1);
    }
    noise(0.03f); compare("one-timing-position", 1, 1, 0);
    noise(0.03f); compare("three-timing-positions", 3, 3, 0);
    noise(0.03f); compare("four-timing-positions", 4, 3, 1);
    /* Identical pilots at every frequency force ties across f; periodic input
       forces ties across t. The original strict > scan must pick the first. */
    rade_acq *saved = malloc(sizeof(*saved));
    if (!saved) return 2;
    memcpy(saved, seed_acq, sizeof(*saved));
    for (int f = 0; f < RADE_ACQ_NFREQ; f++)
        for (int n = 0; n < RADE_M; n++)
            seed_acq->p_w[f][n] = (RADE_COMP){0.125f, 0.25f};
    for (size_t n = 0; n < input_count; n++) input[n] = (RADE_COMP){0.5f, -0.25f};
    compare("equal-time-frequency-ties", RADE_NMF, RADE_ACQ_NFREQ, 1);
    memcpy(seed_acq, saved, sizeof(*saved)); free(saved);
    /* Bracket and bisect the acquisition transition against a fixed noise
       sequence. Every point compares the complete grids and selected result. */
    float lo = 0.0f, hi = 4.0f;
    noise(0.1f); pilots(RADE_NMF, 173, 1.25f, lo);
    int low_detection = compare("threshold-bracket-low", RADE_NMF, RADE_ACQ_NFREQ, 1);
    noise(0.1f); pilots(RADE_NMF, 173, 1.25f, hi);
    int high_detection = compare("threshold-bracket-high", RADE_NMF, RADE_ACQ_NFREQ, 1);
    if (low_detection || !high_detection) { fprintf(stderr, "threshold not bracketed\n"); return 2; }
    for (int i = 0; i < 12; i++) {
        char name[64]; snprintf(name, sizeof(name), "near-threshold-%02d", i);
        float middle = (lo + hi) * 0.5f;
        noise(0.1f); pilots(RADE_NMF, 173, 1.25f, middle);
        int detection = compare(name, RADE_NMF, RADE_ACQ_NFREQ, 1);
        if (detection) hi = middle; else lo = middle;
    }
    /* Conservative scalar fallback must reproduce even exceptional grids. */
    noise(FLT_MIN); compare("subnormal-noise", RADE_NMF, RADE_ACQ_NFREQ, 0);
    noise(1.0e-20f); compare("subnormal-products", RADE_NMF, RADE_ACQ_NFREQ, 0);
    noise(0.1f); input[0].real = NAN; compare("nan-input", 8, 3, 0);
    noise(0.1f); input[0].imag = INFINITY; compare("inf-input", 8, 3, 0);
    noise(0.1f); input[0].real = nextafterf(0x1p-40f, 0.0f);
    compare("below-input-domain", 8, 3, 0);
    noise(0.1f); input[0].real = nextafterf(0x1p16f, INFINITY);
    compare("above-input-domain", 8, 3, 0);
    noise(0.1f); input[0].real = 0x1p-40f; input[1].imag = 0x1p16f;
    compare("input-domain-boundaries", 8, 3, 1);
    for (size_t n = 0; n < input_count; n++) {
        input[n].real = n & 1 ? -0.0f : 0.0f;
        input[n].imag = n & 1 ? 0.0f : -0.0f;
    }
    compare("signed-zero-input", 8, 3, 1);
    RADE_COMP saved_pilot = seed_acq->p_w[0][0];
    noise(0.1f); seed_acq->p_w[0][0].real = nextafterf(0x1p-32f, 0.0f);
    compare("below-pilot-domain", 8, 3, 0);
    seed_acq->p_w[0][0] = saved_pilot;
    seed_acq->p_w[0][0].imag = nextafterf(0x1p16f, INFINITY);
    compare("above-pilot-domain", 8, 3, 0);
    seed_acq->p_w[0][0] = saved_pilot;
    seed_acq->p_w[0][0].real = NAN;
    compare("nan-pilot", 8, 3, 0);
    seed_acq->p_w[0][0] = saved_pilot;
    seed_acq->p_w[0][0].real = 0x1p-32f; seed_acq->p_w[0][0].imag = 0x1p16f;
    compare("pilot-domain-boundaries", 8, 3, 1);
    seed_acq->p_w[0][0] = saved_pilot;
    noise(0.1f);
    int original_rounding = fegetround();
    if (original_rounding != FE_TONEAREST || fesetround(FE_UPWARD)) return 2;
    compare("upward-rounding", 8, 3, 0);
    if (fesetround(original_rounding)) return 2;
    printf("{\"summary\":true,\"workers\":%u,\"parallel_cases\":%u,\"cases\":%u,\"strict_failures\":%u,"
           "\"path_failures\":%u,\"neon_cases\":%u,\"scalar_cases\":%u,"
           "\"threshold_amplitude_low\":%.9g,"
           "\"threshold_amplitude_high\":%.9g,\"radio_qualified\":false}\n",
           workers, parallel_cases, cases, strict_failures, path_failures, neon_cases, scalar_cases, lo, hi);
    rade_acq_pool_destroy(pool);
    free(input); free(ofdm); free(candidate); free(reference); free(seed_acq);
    return strict_failures || path_failures ? 1 : 0;
}
