# X6200 RADE stage timing: independent radio-results audit

Evidence date: 2026-10-27. The uploaded stage-result ZIP was read as data; no uploaded code or binary was executed. This report compares it with the preceding NEON acquisition smoke bundle.

**The current serial RADE receive worker is actually slower than real time, not merely short of the proposed 70% margin.** Synchronized receive needs 154.921 ms of processing on average for each 120 ms of represented audio. Its p99 is 167.331 ms, and it reports 25 missed deadlines out of 25 measured blocks. The 70% gate remains unchanged; even a 100% threshold would not make this result adequate.

## Whole-block results

RTF is measured processing time divided by represented audio duration. These timers measure monotonic elapsed time, including interruptions and scheduling delays, rather than isolated thread CPU time. The benchmark resets its deadline after warm-up and includes resampling in whole-block processing.

| Workload | Previous NEON mean RTF | Stage mean RTF | Previous p99 ratio | Stage p99 ratio | Stage missed deadlines |
|---|---:|---:|---:|---:|---:|
| 700E TX | 0.216533 | 0.219557 | 0.247651 | 0.251165 | 0/38 |
| 700E synchronized RX | 0.192848 | 0.185097 | 0.224597 | 0.195358 | 0/38 |
| 700E no-sync RX | 0.284552 | 0.280506 | 0.311735 | 0.300369 | 0/38 |
| RADE V1 TX | 0.590081 | 0.615339 | 0.627817 | 0.652627 | 0/25 |
| RADE V1 synchronized RX | 1.260680 | 1.291008 | 1.314540 | 1.394427 | 25/25 |
| RADE V1 no-sync RX | 1.199747 | 1.220993 | 1.254256 | 1.284215 | 25/25 |
| RADE V1 interrupted RX | 1.231441 | 1.276090 | 1.293856 | 1.539965 | 117/117 |

| RADE workload | Mean processing per 120 ms block | p99 processing | Maximum processing | Measured processing / represented audio |
|---|---:|---:|---:|---:|
| TX | 73.841 ms | 78.315 ms | 78.315 ms | 1.846018120 s / 3.00 s |
| Synchronized RX | 154.921 ms | 167.331 ms | 167.331 ms | 3.873025062 s / 3.00 s |
| No-sync RX | 146.519 ms | 154.106 ms | 154.106 ms | 3.662979796 s / 3.00 s |
| Interrupted RX | 153.131 ms | 184.796 ms | 185.015 ms | 17.916302768 s / 14.04 s |

For synchronized RX, processing a 3-second audio segment takes 3.873 seconds, a deficit of 0.873 seconds. If this average persisted in live streaming, the worker would process only about 77.46% of arriving audio per unit wall time; a finite queue could delay overflow but could not remove that sustained deficit. This is a throughput inference from measured processing, not a measured production queue or audible dropout count. Deadline misses are based on cumulative scheduled completion times and do not alone prove every individual block exceeded 120 ms.

The ordinary cases still use about 3 seconds measured after about 1 second warm-up. RADE has 25 measured blocks and 1.08 seconds of warm-up; the interruption case has 117 measured blocks over 14.04 seconds. With 25 blocks, the reported p99 is the maximum. These remain smoke measurements rather than sustained qualification.

## Stage costs

Each cell below gives mean milliseconds per measured block, followed by its share of that workload's total processing time. Means for interrupted RX include inactive zero-duration speech/output stages; means conditional on those stages actually running are stated below.

| Measured region | TX | Synchronized RX | No-sync RX | Interrupted RX |
|---|---:|---:|---:|---:|
| Input resampling | 7.611 ms (10.31%) | 6.982 ms (4.51%) | 7.147 ms (4.88%) | 7.042 ms (4.60%) |
| TX speech feature processing | 7.174 ms (9.72%) | 0 | 0 | 0 |
| RADE TX | 49.877 ms (67.55%) | 0 | 0 | 0 |
| RADE RX | 0 | 64.273 ms (41.49%) | 139.337 ms (95.10%) | 77.120 ms (50.36%) |
| RX speech/FARGAN processing | 0 | 74.489 ms (48.08%) | 0 | 61.326 ms (40.05%) |
| Output resampling | 9.127 ms (12.36%) | 9.128 ms (5.89%) | 0 | 7.487 ms (4.89%) |
| Other/residual | 0.051 ms (0.07%) | 0.049 ms (0.03%) | 0.035 ms (0.02%) | 0.155 ms (0.10%) |
| Total | 73.841 ms | 154.921 ms | 146.519 ms | 153.131 ms |

Synchronized speech reconstruction is the largest measured region, at 74.489 ms, followed by the RADE receiver at 64.273 ms. Combined they cost 138.762 ms before resampling, already above the 120 ms interval. Removing both resamplers entirely would therefore not make the current serial synchronized workload real time. Input and output resampling together cost 16.110 ms, or 10.40% of synchronized total processing.

The speech region wraps FARGAN continuation/synthesis and its local preparation loop; it is not a timer around neural matrix kernels alone. The RADE RX region wraps `rade_rx`, including its applicable acquisition, tracking, filtering, demodulation and neural feature-decoding work. The no-sync run produces no speech and spends 95.10% of measured time inside RADE RX, consistent with acquisition being its separate bottleneck. These aggregate stage timers do not identify individual hot functions within a stage.

Interrupted RX runs speech and output processing on 96 of 117 measured blocks. Their means per active block are 74.741 ms and 9.125 ms respectively, similar to synchronized RX. Its RADE RX p99 is 177.117 ms, showing the mixed acquisition/tracking workload's expensive tail. Stage p99 values are computed separately and must not be added to infer a whole-block p99; their slowest samples may occur on different blocks.

The stage source SHA256 matches the packaged record: `552ed488c9c60daa09a541bfc74a3fdb89943c07424ccb49ee0bd124e4e388b2`. Source review confirms warm-up exclusion and additive stage timing inside the whole-block timer. Clock calls and ordinary timing bookkeeping are not subtracted. The residual is not a standalone measurement of all instrumentation overhead: some timing cost lies inside individual stage regions. Final reporting/statistics after the closing timer remain outside it.

## Why a responsive GUI does not establish codec feasibility

The inventory exposes four CPU affinities and every sampled benchmark process reports one thread. A serial codec worker can fall behind while other cores keep the GUI and waterfall responsive. Low average utilization over the whole chip, if observed, would not prove that this worker can complete each dependent processing sequence on time. These bundles do not measure total CPU utilization or the GUI's responsiveness.

The 70% target is a proposed engineering margin: 84 ms for a 120 ms block. It reserves room for variability and the future live audio/radio path. Changing that margin is a product and engineering decision supported by sustained latency, queue and underrun measurements. It cannot fix the present mean processing demand above 120 ms. Queueing helps absorb bounded timing variation when average throughput is sufficient; it cannot correct a sustained processing deficit.

There may be an opportunity to pipeline RADE feature decoding and speech reconstruction on separate cores, because their individual mean regions are smaller than the audio interval. This is a design hypothesis, not a benchmark result. It would require verifying state ownership, FARGAN continuity, thread safety, bounded queueing, extra buffering/latency and contention under the ordinary GUI/waterfall load. It also would not automatically solve the no-sync acquisition workload. Do not divide the serial timing by four or claim that four cores already provide this speedup.

## Comparison controls and functional evidence

Both packages retain the same source dependency pins, acquisition patch, compiler/SDK identity, Opus model/config and requested case settings. The stage package changes the RADE benchmark executable as expected. All 700E case manifests differ only by timestamp. RADE TX and silence inputs match their preceding fixture hashes.

**The RADE modem fixture changed between packages.** The preceding hash is `c10576000bfac17d0fd9a0df448e1781ff1e75b0be80f6a63b8e7c61044f569b`; the stage hash is `749c636231c2fd2ce6e8a78588c91d09d9235f3ecebad37f2f6646ac0b84493d`. Consequently, synchronized and interrupted cases are not identical-input comparisons. Their timing differences cannot be attributed solely to instrumentation. Even for identical input cases, single-run variation and different binaries limit causal claims.

All cases complete with return code zero. Frame counts, synchronization counts, feature-block counts, total output counts and interruption/recovery counters match the preceding NEON suite. Synchronized RADE retains 25 sync/25 feature blocks. Interrupted RADE retains 97 sync/96 feature blocks, 16,000 interrupted samples and 23 sync blocks after the first interruption.

Nonzero PCM counts differ: synchronized output changes from 132,145 to 132,120, and interrupted output from 507,565 to 507,578. The changed input fixture is a relevant comparison limitation; these bundles cannot establish why each sample changed. They include no output PCM, so they do not establish bitwise or perceptual speech equality. An exact A/B instrumentation comparison should reuse the same pinned modem fixture.

The no-sync fixture remains literal digital zero. Its measurements are useful for the current acquisition loop but do not qualify seeded receiver noise or weak-signal detection. Keep separate nonzero noise and weak-pilot cases in subsequent acquisition experiments.

## Accounting and resource checks

All four RADE stage reports pass these independent consistency checks:

- Integer, nonnegative counters; active/call counts match the measured direction and feature activation.
- Stage sums equal `total_processing_ns` exactly, including residual time.
- Total processing divided by audio duration matches the reported RTF within its six-decimal rounding.
- Each stage p99 is at most its maximum; maxima fit the rounded whole-block maximum; inactive stages contain zeros.
- Core/input calls equal measured blocks; RX speech calls equal feature blocks.

These validate the reports' accounting relationships. Without raw per-block arrays they do not independently reconstruct every percentile.

All 53 resource observations report 1,008,000 kHz, performance governor and cooling state zero. Temperature zones are absent, so thermal margin is unknown. Sampled peak RSS is 8,120 KiB for RADE TX, 8,684 KiB for synchronized RX, 5,144 KiB for no-sync RX and 8,564 KiB for interrupted RX; the separate worker peak-RSS statistic is 12,980–13,044 KiB. Available memory is 860,572 KiB at inventory time. These short runs do not establish long-term memory or thermal stability.

## Provenance and decision

| Evidence | Value |
|---|---|
| Stage ZIP | `freedv-stage-smoke-20261027-122045.zip` |
| Independently computed ZIP SHA256 | `57ec2dc0ef65c419ad2f309ce62d24da81aeda5b93e34187b5f50107657ff745` |
| Stage package source commit | `99047e747cf07f0f169970b3edeaba475542250d` |
| Recorded stage worker SHA256 | `5ba7fcaa2be98f811684ef5c843744a4c00adc6960fff9f98ada3c6539f13b95` |
| Recorded RADE shared-library SHA256 | `fe2fc6b4800654e97d7ce99a1a9669f74330440f673ebadc05a617c1a948fa75` |
| Independently computed copied package-manifest SHA256 | `4c4e45311654c175782afe355866878fb1b01bc5695c42e76e64463e4bfcf50d` |
| Prior NEON ZIP SHA256 | `bc519dfae6608d803c71c657e424485f8f15612fffdbd3df086e38bebe41097a` |

The stage bundle contains 46 entries, including a package checksum list. Its summary records that package checksums were verified before execution. The copied package manifest matches its entry in that list. The SDK validation summary records checks for all four stage workloads in addition to the preceding acquisition correctness gates. These records were inspected, not rerun in this audit; the ZIP does not contain the package payload binaries to rehash here.

**Codec timing: FAIL for RADE RX; short TX and 700E checks within limits. Hardware feasibility: INCONCLUSIVE.** The evidence supports investigation of both acquisition cost and synchronized speech/decoder processing. It does not support silently relaxing the 70% gate, declaring production audio stability from GUI responsiveness, or changing the agreed product scope. Native routing/PTT qualification, real audio queues/underruns, sustained performance and controlled RF interoperability remain separate requirements.
