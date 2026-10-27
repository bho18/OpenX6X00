# Codec smoke test using RAM on the working X6200

Use this procedure for the operator's working application 0.3.6, original image
stamp 0.3.4/level 2 and reported baseband 1.0.7. It runs the existing SDK-built
benchmark package without flashing, installing system libraries, touching the
unrecognized third partition or changing radio settings. The executables use
memory fixtures; they have no audio-device or radio-control access. TX and RX
in benchmark case names mean codec processing directions, not RF transmission.

This is a preliminary compatibility/execution test on a different runtime from
the package's original pinned firmware image. The observed loader is glibc 2.36
and NE10 is present; dependency resolution and actual execution must still be
checked. The SDK/model/source provenance remains the package manifest's, while
the collected inventory describes the running firmware. It is not a replacement
for paired baseline/diagnostic-image qualification or a 30-minute workload.

## Check space on the radio

Leave the normal GUI and waterfall active in receive, with no unrelated digital
application running. At the radio shell:

```sh
df -h /tmp
```

Require at least 150 MiB available before copying the runtime package; otherwise
return that output and do not proceed. `/tmp` is a RAM-backed filesystem in the
returned mount listing. Both files and results disappear after shutdown/reboot.
RAM used for package storage is part of this preliminary test's memory setup.
Run `exit` to return to Windows PowerShell.

## Download and copy from Windows PowerShell

Download [x6200-freedv-benchmark-package from run 40432198181](https://github.com/bho18/OpenX6X00/actions/runs/40432198181/artifacts/12702668426).
Extract the artifact ZIP into
`Downloads\x6200-freedv-benchmark-package`. The folder must directly contain
`freedv-benchmark-package.tar.gz` and `ARCHIVE-SHA256SUMS`.

Compare the package archive's `Get-FileHash -Algorithm SHA256` result with its
line in `ARCHIVE-SHA256SUMS`. Then unpack the runtime archive on the PC (Python
gzip support on the radio is not needed):

```powershell
$RadioIp = Read-Host "Radio IP address"
$BenchDir = "$env:USERPROFILE\Downloads\x6200-freedv-benchmark-package"
Get-FileHash "$BenchDir\freedv-benchmark-package.tar.gz" -Algorithm SHA256
Get-Content "$BenchDir\ARCHIVE-SHA256SUMS"
```

After confirming the package archive hash matches:

```powershell
tar -xzf "$BenchDir\freedv-benchmark-package.tar.gz" -C "$BenchDir"
scp -r "$BenchDir\freedv-benchmark" "root@${RadioIp}:/tmp/"
ssh "root@$RadioIp"
```

Copy only `freedv-benchmark`, not the corresponding-source archive, build logs
or firmware images. Preserve the downloaded corresponding sources on the PC.

## Run the short suite at the radio shell

Windows file transfers may not preserve executable permissions. Restore them
only for these two package executables; this does not change their byte hashes.
Use the dynamic loader's listing mode to check resolution before the suite:

```sh
cd /tmp/freedv-benchmark
chmod +x bin/codec2-frame-bench bin/rade-frame-bench
/lib/ld-linux-armhf.so.3 --list bin/codec2-frame-bench &&
/lib/ld-linux-armhf.so.3 --list bin/rade-frame-bench &&
python3 tools/freedv/run_benchmarks.py --radio --output /tmp/freedv-smoke-01
```

The `&&` chain stops on a dependency error. The runner verifies package file
hashes before launching codec workloads and collects a fresh radio inventory.
It runs seven short workloads including synchronized/no-sync RX and interrupted
RADE RX. Allow several minutes, depending on initialization and actual codec
speed. Ctrl-C stops it. If a step fails, return its exact output and any partial
result directory; do not install or replace libraries to make it pass. Use a
new result name if repeating, rather than overwriting the previous evidence.

## Retrieve before shutdown

At the radio shell, run `exit`. In the same Windows PowerShell session:

```powershell
scp -r "root@${RadioIp}:/tmp/freedv-smoke-01" "$env:USERPROFILE\Downloads\"
Compress-Archive -Path "$env:USERPROFILE\Downloads\freedv-smoke-01" -DestinationPath "$env:USERPROFILE\Downloads\freedv-smoke-01.zip"
```

Return the ZIP and report whether the GUI/waterfall remained active, any observed
slowdown and the displayed waterfall settings. Keep recordings/private settings
out of the return; the suite does not record audio or upload anything. It does
not measure routing, RF interoperability, PTT recovery or live queue/underrun
gates; overall hardware qualification remains INCONCLUSIVE even if all short
cases execute. Do not start the long qualification suite from this procedure.
