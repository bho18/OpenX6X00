# Investigating RADE receive cost

The first on-radio SDK package executes correctly but misses RX deadlines. Its
ARM Opus build reports NEON and NE10 enabled with CPU features presumed at build
time. `opus_arch=0` is consistent with direct NEON calls in this configuration;
it is not proof that the binary is scalar. Profile the modem and speech stages
before deciding that the radio cannot support RADE or changing product scope.

## On-radio sampling of the existing binary

Keep the GUI/waterfall running in receive. Use the existing package under `/tmp`;
no flashing, route changes, audio-device access or RF transmission is involved.
The pinned X6200 kernel configuration enables `CONFIG_PERF_EVENTS`. The operator
has now successfully captured software-clock samples on the working radio;
see the [first target profile](rade-radio-profile-2026-10-27.md). Its report
displayed library-relative addresses rather than function names. The returned
capture and matching binaries resolve 93.53% of samples to coarse pilot
acquisition; the library retains its symbols and debug information.

`/tmp/freedv-benchmark` disappears after reboot. If it is absent, restore the
existing runtime package using the [RAM smoke-test copy procedure](https://github.com/bho18/OpenX6X00/blob/topic/baseline-inventory-package/docs/freedv/ram-smoke-test.md),
then restore executable permissions. There is no need to rerun the seven-case
smoke suite just to restore these files. The operator's existing Windows package
directory is `Downloads\x6200-freedv-benchmark-package\freedv-package`.

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
PERF_PAGER=cat perf report --stdio --no-children --sort dso,symbol --percent-limit 1 -i /tmp/rade-rx-profile.data
```

Return the printed symbol report or the exact error if perf is missing, sampling
is unsupported or symbols cannot be resolved. Do not install arbitrary tools or
change kernel security settings to bypass an error. No build-ID cache is written.
`cpu-clock:u` limits samples to this workload's user-space instructions; there
is no call-chain collection. The profile includes fixture setup and warm-up as
well as measured processing; a first diagnostic, not isolated steady-state cost.
All output in `/tmp` is temporary. Use a different output filename for repeats.

If recording succeeds but reporting fails with `/bin/less: No such file or
directory`, rerun only the report command above. `PERF_PAGER=cat` avoids the
missing pager. A missing `tips.txt` message is unrelated to the captured samples.

If the report shows addresses instead of function names, preserve the capture
and exact runtime library before reboot. `perf version --build-options` can
help establish whether this perf build supports libelf. Missing names do not by
themselves establish that the benchmark library is stripped. Do not use a library
from a different compiler/build to assign functions to these addresses.

At the radio shell, run `exit`. In Windows PowerShell, copy this small evidence
set (including the library, which may be tens of MiB):

```powershell
$RadioIp = "192.168.100.64"
$ProfileDir = "$env:USERPROFILE\Downloads\rade-rx-profile-$(Get-Date -Format 'yyyyMMdd-HHmmss')"
New-Item -ItemType Directory -Path $ProfileDir -ErrorAction Stop | Out-Null
$RadioFiles = @(
    "/tmp/rade-rx-profile.data",
    "/tmp/freedv-benchmark/lib/librade.so.0.1",
    "/tmp/freedv-benchmark/bin/rade-frame-bench",
    "/tmp/freedv-benchmark/package-manifest.json",
    "/tmp/freedv-benchmark/SHA256SUMS"
)
foreach ($RadioFile in $RadioFiles) {
    scp "root@${RadioIp}:$RadioFile" "$ProfileDir/"
    if ($LASTEXITCODE -ne 0) { throw "Copy failed: $RadioFile" }
}
Compress-Archive -Path "$ProfileDir\*" -DestinationPath "$ProfileDir.zip" -ErrorAction Stop
Write-Output "$ProfileDir.zip"
```

Return that ZIP explicitly; nothing uploads automatically. Check returned binary
hashes against the package hash list and the captured build IDs before mapping
addresses. Preserve ELF load-segment offsets when translating perf addresses;
inspect the exact binary's symbol table/disassembly rather than assuming a
source function from a similar build. The requested set contains benchmark
metadata and executables, not microphone recordings or radio configuration.

For the documented capture format, the cloud/desktop helper can reproduce the
function sample counts without executing target code or installing perf:

```sh
python3 tools/freedv/decode_rade_profile.py EXTRACTED_PROFILE_DIRECTORY --output NEW_SUMMARY.json
```

It checks the returned manifest/library/worker against `SHA256SUMS`, validates
the supported little-endian ARM ELF32 and single software-clock event layout,
and maps samples only for the exact recorded runtime paths. It rejects unknown
layouts and never overwrites an existing summary. This is a limited independent
decoder, not a general perf replacement. The package checksum list is identity
evidence, not an authenticated signature; absent build IDs are not invented.
System-library functions remain unresolved unless their files are separately
provided to another suitable analysis tool. Use `--capture-name NAME.data` for
a differently named capture using the same documented recording command.

Mappings are resolved by their recorded timestamps and process exec epoch,
because perf's per-CPU buffers can put earlier mapping events later in the file.
Future mappings and ambiguous timestamp ties cause failure rather than guessed
symbol assignments. This ordering was verified against both returned radio
captures and synthetic remapping/exec cases.

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
