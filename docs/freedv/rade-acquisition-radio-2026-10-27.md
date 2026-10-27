# X6200 RADE acquisition experiment: radio smoke comparison

Evidence date: 2026-10-27. This report compares the original benchmark package with the opt-in ARM NEON acquisition experiment on the X6200. Both result bundles were read as data; no uploaded executable was run during this review.

The experiment reduces measured processing time for the no-sync acquisition workload, but **RADE receive still fails the proposed performance gate**. Synchronized receive is essentially unchanged. All seven cases complete successfully and retain the original functional counters. Completion of these short cases is not hardware qualification.

## Timing results

Processing real-time factor (RTF) is processing time divided by represented audio duration; lower is better. The proposed p99 limit is **0.70**, applied without rounding. Deadline counts below are the reported misses divided by measured blocks.

| Workload | Original mean RTF | Candidate mean RTF | Original p99 ratio | Candidate p99 ratio | Original misses | Candidate misses | Candidate short timing check |
|---|---:|---:|---:|---:|---:|---:|---|
| 700E TX | 0.215806 | 0.216533 | 0.238157 | 0.247651 | 0/38 | 0/38 | Within limits |
| 700E synchronized RX | 0.184580 | 0.192848 | 0.208744 | 0.224597 | 0/38 | 0/38 | Within limits |
| 700E no-sync RX | 0.281384 | 0.284552 | 0.292924 | 0.311735 | 0/38 | 0/38 | Within limits |
| RADE V1 TX | 0.644494 | 0.590081 | 0.700613 | 0.627817 | 0/25 | 0/25 | Within limits |
| RADE V1 synchronized RX | 1.252197 | 1.260680 | 1.311725 | 1.314540 | 25/25 | 25/25 | **FAIL** |
| RADE V1 no-sync RX | 1.505031 | 1.199747 | 1.540519 | 1.254256 | 25/25 | 25/25 | **FAIL** |
| RADE V1 interrupted RX | 1.292792 | 1.231441 | 1.541795 | 1.293856 | 117/117 | 117/117 | **FAIL** |

No-sync mean processing time decreases **20.284%** and its p99 decreases **18.582%**. Interrupted RX mean processing time decreases **4.746%** and its p99 decreases **16.081%**. Synchronized RX mean processing time increases 0.677%, a small difference between these individual runs. All three RADE receive workloads still exceed real time on average and miss every measured deadline.

For 120 ms RADE blocks, candidate p99 processing times are 75.338 ms for TX, 157.745 ms for synchronized RX, 150.511 ms for no-sync RX and 155.263 ms for interrupted RX. Interrupted RX has a maximum of 182.467 ms, above its p99. The proposed 70% limit corresponds to 84 ms per 120 ms block.

The original TX p99 of 0.700613 strictly exceeds 0.70; the candidate's 0.627817 is within the limit. The acquisition change is on the receive path, so this TX difference does **not** establish a causal TX improvement. The unchanged 700E workloads also vary between runs. Repeated alternating baseline/candidate measurements are needed to quantify variability.

## Duration and comparison controls

The original and candidate suites use identical durations, pacing and workloads:

| Cases | Measured audio | Measured blocks | Warm-up audio |
|---|---:|---:|---:|
| 700E TX and no-sync RX | 3.04 s | 38 | 1.04 s |
| 700E synchronized RX | 3.04 s | 38 | 1.00625 s |
| RADE continuous TX, synchronized RX and no-sync RX | 3.00 s | 25 | 1.08 s |
| RADE interrupted RX | 14.04 s | 117 | 1.08 s |

All cases are paced. Initialization is reported separately from measured processing; candidate RADE initialization ranges from 11.290 to 11.723 ms. In each 25-block continuous RADE run, the reported p99 equals the maximum. These samples are too short to estimate a stable tail distribution or meet the required 30-minute qualification duration.

Each of the seven case manifests is identical between bundles except for its creation timestamp. This includes reported benchmark-executable SHA256, fixture SHA256, source-manifest SHA256, warm-up and measurement settings, workload, pacing and model metadata. The benchmark executables are unchanged; the intended candidate change is in the RADE shared library.

| Shared recorded input or executable | SHA256 |
|---|---|
| 700E benchmark executable | `6962137d441b7a56fe6908e285a66c96190c21508f59896e1802b162c6cd2d9f` |
| RADE benchmark executable | `fe6b48a5bd8892925e2bbea2ab1c2ac492bad8dbd497d49994e69c39b5cc6cb9` |
| 700E speech fixture | `b5554e78d9a5d34e886df2142d3e9327c11688b9aacb6404dea2de67d20df13b` |
| 700E modem fixture | `87e541f9549aaeafe9ed00923cc0fe16e38bfc077d0e484b5b5e673a4e88dea6` |
| RADE speech fixture | `138e5f19c4a4c4ee4484fc8fa8b583aafbd474f5e5880edd02e6bfc71170acaa` |
| RADE modem fixture, continuous and interrupted RX | `c10576000bfac17d0fd9a0df448e1781ff1e75b0be80f6a63b8e7c61044f569b` |
| Silence fixture | `f85f2c34eb2843d2aa5951ee6e8e76985655b2e3ae2cbdd76bdfd654ecf19997` |
| Source manifest | `6e169fa00bb668d6501603ac9178b33b7544f9601725fffd9e0c74a23eddfee4` |

Package manifests retain the same dependency source pins, Buildroot SDK-manifest hash, GCC 12.3.0 compiler, Opus archive/config/model hashes and SDK NE10 hash. The static libsamplerate archive hash differs between builds, while the linked benchmark-executable hashes remain identical; an archive-hash difference alone does not establish a processing change. Shared-library dependency names also match.

The result bundles do not contain the actual benchmark executables, fixtures or runtime-loaded shared libraries. Their hashes above are recorded manifest evidence, not hashes recomputed from those payloads during this audit. In particular, case manifests do not hash the runtime-loaded RADE library. Exact deployed-library identity therefore depends on package provenance and installation; a future collection should record loaded-library hashes.

## Functional observations

Every case returns zero and is marked validated. Every reported functional counter matches the original case, including measured frames, output counts, resampled output counts and RADE nonzero-output counts.

| RADE workload | Sync blocks | Feature blocks | Speech/modem output samples | Resampled output samples | Nonzero output samples |
|---|---:|---:|---:|---:|---:|
| TX | 0 | 0 | 24,000 | 132,300 | 132,299 |
| Synchronized RX | 25 | 25 | 48,000 | 132,300 | 132,145 |
| No-sync RX | 0 | 0 | 0 | 0 | 0 |
| Interrupted RX | 97 | 96 | 184,320 | 508,032 | 507,565 |

Both interrupted runs mute 16,000 input samples and report 23 synchronized blocks after the first interruption, supporting recovery for this deterministic fixture. The 700E synchronized fixture reports 38 synchronized frames in each run; the silence fixture reports zero.

The no-sync fixture is literal zero PCM, not receiver hiss. It exercises repeated acquisition but cannot validate weak-signal sensitivity or a proposed RF noise gate. See [acquisition and gating follow-ups](rade-acquisition-followups.md).

Actual output PCM and feature arrays are absent from these radio result bundles. Matching counters do not establish sample-by-sample equality, perceptual quality or interoperability. The benchmarks have no radio-control or physical-audio access. This is software fixture processing, not controlled RF receive interoperability.

## Resource observations and limits

The inventories identify the same ARMv7 system, Linux 6.1.82 and application 0.3.6. The operator previously reported baseband 1.0.7; the inventory helper does not query it. Application and baseband versions are distinct from package source commits.

There are 54 original and 51 candidate resource samples. All report 1,008,000 kHz, the performance governor and cooling-device state zero. Both bundles expose no temperature-zone readings. This supports no observed clock reduction during these brief runs; it cannot establish temperature, thermal headroom or sustained throttling behavior.

Candidate sampled RADE resident memory reaches 8,116 KiB for TX, 8,680 KiB for synchronized RX, 5,148 KiB for no-sync RX and 8,620 KiB for interrupted RX. The interrupted run shows no progressive growth in its short sampled interval. Separately, the worker's Linux peak-RSS statistic reports 13,048–13,052 KiB. These are different observations and should not be substituted for one another.

Available memory in the one-time inventories changes from 858,596 to 791,548 KiB, alongside higher shared-memory use. This is not sufficient evidence of a codec leak. No long-duration memory-stability conclusion is justified.

After returning this bundle, the operator confirmed that the normal radio GUI and waterfall stayed active and responsive, with no other digital-mode application running. This is operator evidence for the required test conditions. The bundles do not record GUI process load or frame rate, and the observation does not establish long-duration stability. The benchmark also lacks production queue-depth and audio-underrun instrumentation.

## Package provenance and included SDK validation

| Evidence | Original | Candidate |
|---|---|---|
| Uploaded bundle | `freedv-smoke-01.zip` | `freedv-neon-smoke-20261027-095857.zip` |
| Bundle SHA256, independently computed | `f2ac419f27718f27440e059107a02047d53453f915dbbab10be64b05f17101fb` | `bc519dfae6608d803c71c657e424485f8f15612fffdbd3df086e38bebe41097a` |
| Package source commit | `7564fc10e33bd0f7219ca9e3ebd6b4595e1ebe53` | `1df458d0280d0c78cc8fd117d28cd01532a495fd` |
| Corresponding-source archive SHA256 | `94d7705b26d1b6bda921a9b6c15375dd6ccf9aa93c608f3ef95264782f8c94be` | `21855209909dd617dd60746c6564c3174cdd539f63750306076320ae3da1f8c3` |

Both ZIPs have 45 entries and no duplicate normalized filenames. All entries were read successfully. The package manifests report clean source worktrees. The common SDK-manifest SHA256 is `26ca3b08069bf554529cde06a4229ba20c40278297ecb159f73fe96b566c1977`; the common Opus model SHA256 is `4ed9445b96698bad25d852e912b41495ddfa30c8dbc8a55f9cde5826ed793453`.

The candidate manifest records acquisition patch SHA256 `767652147fdb8c59e8ca124075ec6e4f05ed5a5cd7e456d925eae2d0379c5c6b` and patched-source SHA256 `28f72e33aa6ad70cdad2ad0e311651e9298b03abbff93c8de50083097e0f6090`. It enables `RADE_ACQ_NEON=1` with source-local `-marm -mfpu=neon-vfpv4 -ffp-contract=fast` options. The experiment remains marked not production-qualified.

The embedded SDK validation summary was inspected during this audit. It records:

- Matching Buildroot SDK execution under QEMU user mode configured for Cortex-A7.
- 47 enabled differential cases: 34 vector cases and 13 scalar fallback cases, with zero strict or path failures.
- 47 default-off scalar cases, also with zero strict or path failures.
- Bit-exact results for the tested differential cases; this is finite test coverage, not proof for every floating-point input.
- Six passing V1 text cases, finite TX/RX features and upstream V1 loss reported as 0.113 against a limit of 0.119.
- 790,240 decoded speech samples from 49.7641875 seconds of source speech, with the required minimum retention fraction of 0.95.
- Seven passing functional benchmark cases, including interrupted reception, and identification of the `rade_acq_grid_neon` vector function.

These are inspected build-validation records, not correctness tests rerun as part of this radio-results audit. QEMU timing is not radio timing. The recorded validation-summary SHA256 is `30326cc649651be7e7e1e6b7ab2418797376f3a97ce5a991eb0565a13b918a40`.

## Decision and next measurements

**Candidate codec timing gate: FAIL.** All RADE receive workloads still exceed the p99 target and miss every measured deadline. The short 700E and RADE TX results are within limits but do not meet the full qualification requirements.

**Hardware feasibility: INCONCLUSIVE.** Native microphone/speaker routing, PTT behavior, restoration, real audio queues and underruns, controlled RF interoperability and sustained thermal/memory behavior remain unqualified by this evidence. Nothing here justifies a legacy-only product decision or external-audio fallback.

Retain the acquisition experiment as a measured partial improvement. Next, separate RADE decoder, FARGAN and resampling processing time with additive stage instrumentation, and profile the acquisition candidate to locate its remaining cost. Repeat short baseline/candidate cases in alternating order while documenting GUI/waterfall operation and runtime-library identity. Preserve full-block timing as the acceptance metric because stage instrumentation adds overhead. Begin 30-minute qualification runs when an implementation plausibly meets the complete timing target; the current receive failures already identify the immediate optimization work.
