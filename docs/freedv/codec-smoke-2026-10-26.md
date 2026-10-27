# First on-radio codec smoke measurements

**Current-build timing result: 700E within short-run limits; RADE V1 FAIL.**
All seven workloads executed, synchronized when expected, and produced valid
result records. Successful execution is separate from meeting real-time timing
or the proposed 70% processing-time target. Overall hardware feasibility and
30-minute qualification remain **INCONCLUSIVE**.

## Setup and evidence

- Operator: X6200 running application 0.3.6; image stamp 0.3.4, level 2;
  previously reported Xiegu baseband 1.0.7. Kernel 6.1.82, ARMv7.
- Normal GUI/waterfall was to remain active; operator reported no noticeable
  slowdown and noted that the tests ran quickly. This is a qualitative report,
  not a measured display frame-rate result. Exact waterfall settings were not
  returned.
- Package and results ran from RAM-backed `/tmp` because the large third card
  partition is unmounted and has no filesystem TYPE in the returned `blkid`.
  No filesystem repair/format, firmware installation, audio-device access or
  radio-control/RF test was performed.
- Returned archive: `freedv-smoke-01.zip`, 33409 bytes,
  SHA256 `f2ac419f27718f27440e059107a02047d53453f915dbbab10be64b05f17101fb`.
  All 45 entries were inspected; the archive contains 79161 uncompressed bytes.
  Windows backslash-separated archive paths were read as data without executing
  or extracting uploaded files. Raw uploaded results remain outside Git.
- Collection started 2026-10-26 21:59:43 America/Chicago
  (`2026-10-27T02:59:43.831353+00:00`); the last worker exited at
  `2026-10-27T03:00:41.311192+00:00`.
- Source/model identities in the case manifests agree with the package manifest
  and the pinned source manifest at its recorded commit. Executable hashes are
  consistent across cases using the same codec. Fixture hashes are recorded;
  fixture/executable bytes were not returned for independent rehashing.
  `summary.json` reports all seven cases validated with return code zero and
  `complete=true`.

## Processing measurements

Ratios below express codec-plus-resampling wall time divided by represented
audio time; they are not total-radio CPU utilization. A value above 100% cannot
keep pace in this measured serial implementation. The proposed p99 target is
**at most 70%**, evaluated without rounding. The short cases contain only 25 or
38 measured blocks, so their nearest-rank p99 is the maximum observed block;
these are not reliable estimates of a long-run tail distribution.

| Workload | Measured blocks / audio | Mean time / audio | p99 time / audio | Late blocks | Short timing check |
| --- | --- | --- | --- | --- | --- |
| 700E TX | 38 / 3.04 s | 21.5806% | 23.8157% | 0 | Within limits |
| 700E synchronized RX | 38 / 3.04 s | 18.4580% | 20.8744% | 0 | Within limits |
| 700E no-sync RX | 38 / 3.04 s | 28.1384% | 29.2924% | 0 | Within limits |
| RADE V1 TX | 25 / 3.00 s | 64.4494% | 70.0613% | 0 | FAIL: p99 exceeds 70% |
| RADE V1 synchronized RX | 25 / 3.00 s | 125.2197% | 131.1725% | 25 | FAIL |
| RADE V1 no-sync RX | 25 / 3.00 s | 150.5031% | 154.0519% | 25 | FAIL |
| RADE V1 interrupted RX | 117 / 14.04 s | 129.2792% | 154.1795% | 117 | FAIL |

RADE's represented input interval was 120 ms per measured block. Synchronized
RX averaged approximately 150.3 ms, with a 157.407 ms maximum. No-sync RX averaged
180.6 ms, with a 184.862 ms maximum. Interrupted RX reached 185.152 ms. Every
measured RADE RX block missed its paced deadline. The harness retains all input
and finishes late; it does not simulate or count dropped live audio blocks.

700E synchronized on all 38 measured RX frames and reported zero synchronization
on silence. RADE synchronized on all 25 continuous signal blocks, zero silence
blocks, and 97 of 117 interrupted-input blocks. It reported 23 synchronized blocks
after the first interruption, establishing reacquisition in this software fixture
test. None of these establish RF interoperability, live audio quality or routing.

RADE initialization reported 11.278–11.899 ms, separate from measured block time;
this is not total command startup/fixture preparation time. Required resampling
is included in every result, and synchronized RADE RX includes FARGAN synthesis.

## Resources and provenance limits

All 54 resource samples report CPU policy frequency 1008000 kHz, governor
`performance`, and cooling-device state 0. No temperature zones were available.
There is no observed frequency reduction in this short test; sustained thermal
behavior remains unmeasured. The fresh inventory reports 858596 KiB (about
838 MiB) available RAM after placing the package in tmpfs.

Sampled worker RSS settled at approximately 2.2–3.8 MiB for 700E and 5.0–8.6 MiB
for RADE. The longer interrupted case remained at 8740 KiB after initialization.
Workers also reported `getrusage` peaks of 12660–12908 KiB; keep these distinct
from periodically sampled RSS. These few seconds cannot establish leak-free or
thermally stable 30-minute operation.

| Package identity | Value |
| --- | --- |
| SDK package source | `7564fc10e33bd0f7219ca9e3ebd6b4595e1ebe53` |
| Compiler | ARM Buildroot GCC 12.3.0; Buildroot `cc34b5b2` |
| 700E executable SHA256 | `6962137d441b7a56fe6908e285a66c96190c21508f59896e1802b162c6cd2d9f` |
| RADE executable SHA256 | `fe6b48a5bd8892925e2bbea2ab1c2ac492bad8dbd497d49994e69c39b5cc6cb9` |
| Opus model SHA256 | `4ed9445b96698bad25d852e912b41495ddfa30c8dbc8a55f9cde5826ed793453` |
| Opus generated config SHA256 | `251ca684246f27e378dab22a56611495687308f0ffde3090683d4f697ee305ff` |
| SDK NE10 SHA256 | `b82c9a9fa7540c199ca3781abf40f3ebce599d61493a5a8df8b1046b16626808` |
| Installed NE10 SHA256, reported separately | `38ddbda7364ae2ea70fd79ebee5a6b10170bcf607ed6d1cf81df20e734b21d12` |

The installed NE10 hash differs from the SDK library hash. Stripping or other
build differences have not been established; do not claim identical runtime
libraries. Loader resolution and actual execution succeeded. These measurements
apply to the operator's current working runtime, not a booted pinned test image.

All RADE results report `opus_arch=0`. This alone does not prove scalar execution:
the pinned Opus can compile direct NEON calls when NEON is presumed, and its
dispatch index can be zero in that configuration. Inspect the actual generated
configuration, compiler flags and library-side dispatch before attributing the
failure to missing SIMD. The pinned RADE V1 TX/RX source also supplies a literal
architecture index 0 to its encoder/decoder; whether that bypasses optimized
dispatch depends on that same Opus configuration. No CPU optimization claim or
speedup is established by the returned bundle.

## Next action

Do not spend several hours on the unchanged full RADE qualification suite: it
already misses every RX deadline in the short test. Preserve these results as
the first measured baseline. Audit the generated SDK/Opus configuration and
profile resampling, modem/acquisition, RADE inference and FARGAN separately.
Any corrected or optimized build needs its own source/config/model identity,
correctness checks and repeat smoke measurements before sustained testing.

Keep 700E as the reference, not an automatic substitute for RADE in the product.
Native microphone/speaker routing, PTT recovery, live queue/underrun behavior,
intrinsic latency, full hardware feasibility and the DATA filesystem condition
remain open. If a correctly configured RADE implementation still misses the
agreed gate, present the measurements for the separate product-scope decision.
