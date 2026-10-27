# First on-radio RADE no-sync sampling profile

The working X6200 captured 3448 `cpu-clock:u` samples at 99 Hz from the unchanged
RADE benchmark. The operator's pasted report states **zero lost samples**.
The 22 displayed library addresses account for **89.20% of reported sample
overhead** (sum of percentages rounded by perf). This identifies a concentrated
region for investigation; exact symbol mapping is still **INCONCLUSIVE**.

## Evidence and workload

Evidence is the operator's terminal transcript supplied on 2026-10-27. The
binary perf capture and matching SDK library have not yet been returned, so no
independent capture hash, build-ID match or function assignment is claimed.
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
No IPC value was collected by this software-clock profile.

## Required follow-up

Preserve the perf capture, exact loaded library and executable, package manifest
and checksum list using the [profiling procedure](rade-profiling.md). Verify the
identities and resolve this region with the matching ELF symbols/disassembly.
The package build requests debug information and copies its libraries directly;
unresolved names alone do not prove that debug information was stripped. A perf
build without libelf is one possible explanation, not yet confirmed on this
working rootfs.

The previous host profile makes acquisition correlation a candidate, but it
cannot identify these ARM addresses. Profile synchronized RX separately after
establishing symbol resolution. Any optimization must retain the complete
acquisition search, thresholds and model and pass correctness tests before
target timing is compared. Native audio routing, PTT behavior and overall
hardware feasibility remain unqualified.
