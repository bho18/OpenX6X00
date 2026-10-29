# First on-radio RADE no-sync sampling profile

The working X6200 captured 3448 `cpu-clock:u` samples at 99 Hz from the unchanged
RADE benchmark, with **zero lost samples**. The returned capture and matching
ELF symbols now identify **3225 of 3448 samples (93.5325%)** inside
`rade_acq_detect_pilots`. Its inner correlation loop uses scalar floating-point
instructions. This is a measured optimization target; RADE feasibility remains
**INCONCLUSIVE** until a validated implementation passes target measurements.

## Evidence and workload

Evidence consists of the operator's terminal transcript and the returned
`rade-rx-profile-20261027-090350.zip` (27534062 bytes). The archive contains the
capture, runtime library, benchmark, package manifest and checksum list. These
were read as data; neither returned executable was run during profile analysis.
The library, worker and manifest match the returned package checksum list. The
worker hash also matches the previously returned smoke-test manifests. Sources,
model and compiler records remain those of package commit
`7564fc10e33bd0f7219ca9e3ebd6b4595e1ebe53`.

| Evidence | SHA256 |
| --- | --- |
| Returned ZIP | `78506291847739bb71f2077cd7d5d6d896320e0e4a5695986696c9952ac7194d` |
| perf capture | `e1a1a351295ef94cb2acebbc17dc0a8793dabb85b213c8937e6a5dfa14b9a79b` |
| `librade.so.0.1` | `9d411e29b2aa69d79009817a23f5cc45bbf93d91da152cae1e7f3a3625bfe00c` |
| `rade-frame-bench` | `fe6b48a5bd8892925e2bbea2ab1c2ac492bad8dbd497d49994e69c39b5cc6cb9` |
| Package manifest | `5a92a19deac1c2cc5ca25742dc25bd15806bff796daeeeb96d8c366e0caaff73` |
| Package checksum list | `c76ba958c6684fe17324082cdeea4661dbbbdde5f473c4230b95448549a95500` |

The capture records `/tmp/freedv-benchmark/lib/librade.so.0.1` and the expected
worker path. Neither returned binary has a GNU build-ID, and the capture has no
build-ID feature. The identity evidence is the recorded path plus matching
returned package hashes; a recorded build-ID match cannot be claimed.
The runtime package was recopied into `/tmp/freedv-benchmark` after that temporary
directory was found absent. The known working setup is application 0.3.6 and
reported baseband 1.0.7. No new full inventory was returned with this profile.

The operator ran:

```sh
perf record --no-buildid-cache -e cpu-clock:u -F 99 -o /tmp/rade-rx-profile.data -- ./bin/rade-frame-bench rx fixtures/silence.raw 20 unpaced 3 continuous
```

The command produced 167 measured blocks representing 20.04 seconds of audio,
after 3 seconds of represented warm-up. It completed with zero synchronized
blocks, features and output samples, as expected for the no-signal fixture.

| Worker metric | Returned value |
| --- | --- |
| Mean processing time / represented audio | 1.509861 |
| p99 processing time / block interval | 1.555462 |
| Maximum processing time / block interval | 1.557180 |
| p99 / maximum block time | 186.655 / 186.862 ms |
| Initialization time, reported separately | 12.285 ms |
| Peak RSS | 5116 KiB |
| Deadline misses | null (unpaced run) |
| `opus_arch` | 0 |

The nominal block interval is 120 ms. These values resemble the previous
no-sync smoke result, but sampling adds overhead and this unpaced run is not a
paced deadline, sustained performance or live audio qualification. Zero output
on this input means the measured blocks did not exercise speech synthesis.

Perf reported 0.134 MB written, 3448 samples, and an approximate event count of
34828282480. Its initial report failed because `/bin/less` was absent. Repeating
only the report with `PERF_PAGER=cat` succeeded. The subsequent missing `tips.txt`
message does not invalidate the saved capture.

## Address concentration

The report used `--stdio --no-children --sort dso,symbol --percent-limit 1`.
Every displayed row belongs to `librade.so.0.1`. The rows cover 22 four-byte
address slots from `0x0000d22c` through `0x0000d280`, an 88-byte span including
the final slot. The largest individual row is `0x0000d274`, at 13.60%.

| Address | Reported overhead | Address | Reported overhead |
| --- | --- | --- | --- |
| 0xd22c | 3.45% | 0xd258 | 3.71% |
| 0xd230 | 3.42% | 0xd25c | 2.93% |
| 0xd234 | 3.42% | 0xd260 | 3.54% |
| 0xd238 | 7.45% | 0xd264 | 3.22% |
| 0xd23c | 3.05% | 0xd268 | 2.87% |
| 0xd240 | 3.65% | 0xd26c | 3.80% |
| 0xd244 | 3.57% | 0xd270 | 3.19% |
| 0xd248 | 4.35% | 0xd274 | 13.60% |
| 0xd24c | 3.05% | 0xd278 | 2.90% |
| 0xd250 | 3.68% | 0xd27c | 3.10% |
| 0xd254 | 3.57% | 0xd280 | 3.68% |

The 1% display threshold hides other rows. The 89.20% sum is therefore neither
the complete library share nor a measured function percentage. Sampling skid
also prevents treating an individual address percentage as instruction latency.
No IPC value was collected by this software-clock profile. Decoding all samples
in the returned capture gives 3076 samples (89.2111%) within that span, consistent
with the summed rounded percentages above.

## Resolved functions and instructions

The capture's library mapping starts at `0xb54e0000`, with file offset zero.
The returned ELF's executable `PT_LOAD` segment starts at file offset and virtual
address zero. Thus these library-relative sampled addresses map directly to its
ELF virtual addresses. The function symbol starts at `0xd138`, size 1016 bytes.
`addr2line` and disassembly resolve the hot span to the correlation loop at
`rade_acq.c:112-115` and inlined complex addition in `rade_dsp.h:123-124`.

| Resolved function or library | Self samples | Share of all samples |
| --- | --- | --- |
| `rade_acq_detect_pilots` | 3225 | 93.5325% |
| `sinc_mono_vari_process` in benchmark | 125 | 3.6253% |
| `rade_cdot_float` | 63 | 1.8271% |
| `rade_bpf_process` | 23 | 0.6671% |
| System libm (function bytes not returned) | 7 | 0.2030% |
| System libc (function bytes not returned) | 4 | 0.1160% |
| Benchmark `main` | 1 | 0.0290% |

The independently decoded event-period total is 34828282480, exactly matching
perf's printed count; all 3448 samples have period 10101010. First-to-last sample
span is 34.9932 seconds. These are self samples across the whole command, including
startup, fixture preparation, warm-up and processing; they are not inclusive
per-stage wall times or a steady-state-only profile. In particular, resampling
samples can include fixture preparation even though this no-sync case produces
no speech output. No call chain was collected.

The hot instructions use scalar `s` registers for `vldr`, `vmul.f32`,
`vnmul.f32`, `vfma.f32` and `vadd.f32`. They do not process four floating-point
lanes together. The compiler already fuses the complex products, then adds each
result to an ordered accumulator. A SIMD candidate should preserve this product
and accumulation structure while batching independent timing positions.
This evidence concerns the modem's acquisition code; Opus's separately enabled
NEON/NE10 paths remain a different part of the pipeline.

## Required follow-up

The returned library contains both `.symtab` and DWARF debugging sections.
Unresolved on-radio names were not evidence of stripped library symbols. A perf
build without libelf remains a possible explanation, not confirmed by an
installed-feature report. Offline symbol resolution has removed that blocker.

Target acquisition cost is now established independently of the earlier host
profile. Profile synchronized RX separately: it need not share this no-sync
bottleneck. Any optimization must retain the complete
acquisition search, thresholds and model and pass correctness tests before
target timing is compared. Native audio routing, PTT behavior and overall
hardware feasibility remain unqualified.
