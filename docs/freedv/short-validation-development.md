# Short validation implementation status

The remaining five codec cases now have a standalone runner,
`tools/freedv/run_short_validation.py`. It uses the unchanged tested `b9fcea82`
X6200 SDK package and runs RADE TX, 700E TX, 700E synchronized RX, 700E silence RX
and RADE synthetic-noise RX sequentially. Each measures 120 seconds after five
seconds of warm-up. No new radio binary or firmware flash is needed for this
file-only batch. Native speaker/microphone and turnaround tests still require
the in-process diagnostic firmware integration described in the combined plan.

The runner checks the exact package checksum-list identity before importing
its verified helpers, then verifies all package files. It checks GUI presence,
available memory and storage between cases. It preserves independent failures,
updates a summary after each case and retains a partial summary on interruption.
Timing-margin failures are separate from execution completion. It neither
opens audio devices nor controls the radio, and never uploads results.

## Development checks completed

- Ten runner tests passed, covering package rejection before imports, fixed
  durations, deterministic input, malformed/incomplete evidence, synchronization,
  continued collection after failure, interruption and timing-margin failures.
- Four existing timing-assessment tests passed.
- All eleven host diagnostic/session tests passed, including actual capture
  and playback adapters against mocked PulseAudio, watchdog, held TX,
  restoration sequencing and ownership primitives. These are host observations.
- The full host GUI built with diagnostics ON and OFF.
- Actual host collector/executable flows ran for all five cases using two-second
  integration inputs. The four frame-benchmark cases validated. On x86 the noise
  decoder uses the scalar fallback; the radio validator correctly rejects it as
  evidence of acquisition-helper execution. That rejection was retained rather
  than weakened or reported as a complete radio suite.
- Native reference decoding of the noise input ran for 120 measured seconds
  after five warm-up seconds, in serial and overlapping-stage configurations.
  Both produced zero synchronization, speech features and output samples.
- An ARM development executable under Cortex-A7 QEMU processed three measured
  seconds of that noise after one warm-up second. All 34 acquisition calls used
  the parallel path, each helper completed 34 jobs, and the strict acquisition
  validator passed. This is development correctness, not radio performance or
  a matching-SDK deployment check.

The input is two seconds of deterministic 8-kHz mono PCM16, repeated by the
benchmark. An integer LCG followed by two low-pass stages and DC removal defines
the bytes. This is not calibrated RF noise or an off-air sensitivity test.
Noise SHA256:
`2218159f25b72c1b6986b3fe56c7806c5437e1884b1971728a274678168a4af9`.
Source/model pins remain those of the tested SDK package. Native serial result
SHA256:
`73151917b20462946adf363e343c9c631493ae3b9ec31f9b43d464b2650bf9f1`.
ARM development result SHA256:
`98055e82af6cf60812ee81b18ae54a893a8d7d31ab7241829954f43668128d85`.

## Diagnostic playback adapter

An opt-in worker-owned API now opens the existing low-latency codec stream and
reports the exact number of accepted samples. The caller retains unwritten
samples on a partial write or a full buffer. Errors return -1. Accepted samples,
write errors, underflows and flushes have separate counters. PulseAudio queued
latency is labelled separately from acoustic latency. Stop requests a flush and
disconnects without a blocking drain. Startup underflows are counted; compare
snapshots after warm-up when measuring steady speech.

Tests cover stream acquisition/re-entry, odd byte counts, partial writes and
retry, no room, invalid input, writable-size/write/stream errors, latency,
underflows, flush success/failure, repeated Stop and counter reset on reopen.

The adapter does not acquire the radio session, select a route or key RF. The
caller must first exclude competing writers and own restoration. It is not
wired to a hardware test button yet; the Info page remains read-only. Complete
snapshot/restoration, the real session backend, codec/audio integration, guarded
controls and a diagnostic image remain outstanding. Host checks do not qualify
those missing paths.

The environment still receives proxy CONNECT 403 for `api.github.com`; Git
pushes work. Automatic Actions dispatch, artifact/SDK retrieval and PR creation
are unavailable through that API here. No radio image or hardware-ready label
is claimed by this change. Work remains in the fork.
