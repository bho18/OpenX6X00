# Investigating RADE receive cost

The first on-radio SDK package executes correctly but misses RX deadlines. Its
ARM Opus build reports NEON and NE10 enabled with CPU features presumed at build
time. `opus_arch=0` is consistent with direct NEON calls in this configuration;
it is not proof that the binary is scalar. Profile the modem and speech stages
before deciding that the radio cannot support RADE or changing product scope.

## On-radio sampling of the existing binary

Keep the GUI/waterfall running in receive. Use the existing package under `/tmp`;
no flashing, route changes, audio-device access or RF transmission is involved.
The pinned X6200 kernel configuration enables `CONFIG_PERF_EVENTS`; whether
the installed `perf` command and userspace sampling work still needs checking.

The following runs 20 seconds of represented no-sync input plus 3 seconds of
warm-up, unpaced, under software-clock sampling at 99 Hz. It may take around
35 seconds or longer. Sampling overhead makes the resulting timings diagnostic,
not qualification measurements. The program prints its ordinary benchmark JSON,
then perf prints a symbol-cost report. The `&&` chain stops if a command fails:

```sh
cd /tmp/freedv-benchmark &&
perf --version &&
test ! -e /tmp/rade-rx-profile.data &&
perf record --no-buildid-cache -e cpu-clock:u -F 99 -o /tmp/rade-rx-profile.data -- ./bin/rade-frame-bench rx fixtures/silence.raw 20 unpaced 3 continuous &&
perf report --stdio --no-children --sort dso,symbol --percent-limit 1 -i /tmp/rade-rx-profile.data
```

Return the printed symbol report or the exact error if perf is missing, sampling
is unsupported or symbols cannot be resolved. Do not install arbitrary tools or
change kernel security settings to bypass an error. No build-ID cache is written.
`cpu-clock:u` limits samples to this workload's user-space instructions; there
is no call-chain collection. The profile includes fixture setup and warm-up as
well as measured processing; a first diagnostic, not isolated steady-state cost.
All output in `/tmp` is temporary. Use a different output filename for repeats.

After reviewing that report, repeat with `fixtures/rade-modem.raw` and a new
profile filename to examine synchronization, decoding and FARGAN. Do not assume
the no-sync bottleneck also dominates synchronized reception.

## Optional host-only stage interposition

`tools/freedv/rade_stage_profile.c` measures inclusive elapsed time around six
exported RADE functions without altering codec/model bytes. It uses Linux
`LD_PRELOAD` and headers from the exact pinned RADE/Opus source. It is intended
only for the existing single-threaded host-validation benchmark, not the radio
GUI, production streaming or multi-threaded callers. Do not apply LD_PRELOAD
globally. No radio deployment of this helper is qualified.

With a fresh external work directory and variables pointing to the matching
host validation package, RADE source and generated Opus source/headers:

```sh
cc -O2 -g -Wall -Wextra -Werror -Wno-error=cpp -shared -fPIC \
  -I"$RADE_SRC/src" -I"$OPUS_SRC/dnn" -I"$OPUS_SRC/celt" -I"$OPUS_SRC/include" \
  tools/freedv/rade_stage_profile.c -ldl -o "$PROFILE_WORK/interpose.so"
FREEDV_PROFILE_OUTPUT="$PROFILE_WORK/silence-stages.json" \
LD_PRELOAD="$PROFILE_WORK/interpose.so" \
  "$PACKAGE/bin/rade-frame-bench" rx "$PACKAGE/fixtures/silence.raw" 20 unpaced 3 continuous \
  > "$PROFILE_WORK/silence-result.json"
```

Repeat signal/no-signal cases without the interposer and compare functional
counters (sync, features, frames, output/nonzero sample counts and represented
duration). Host stages must all forward to the existing library; absent symbols
abort. The profile file uses exclusive creation and reports an error instead of
overwriting an existing file. Check that it exists after a run.

`rade_rx` includes the other timed RADE functions, so do not add all rows into a
total. Counts/times include warm-up and fixture loop transitions, not just the
interval in the worker's steady-state JSON. Resampling and FARGAN calls outside
`rade_rx` are not timed by this interposer. Host results locate candidates for
on-radio profiling; differences in CPU, dispatch and scheduling prevent using
their timing or stage proportions as radio performance evidence.
