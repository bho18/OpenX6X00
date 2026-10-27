# First X6200 test card: baseline, read-only diagnostics and codec benchmark

These images permit ordinary firmware checks, read-only cached route observations
and in-memory codec tests. The diagnostic image does not expose routing experiments,
Arm or RF test controls. Audio routing and PTT recovery qualification remain blocked.

Both application variants are compiled from the same source commit with the matching
baseline Buildroot SDK. The base image's bootloader, boot partition and Xiegu
baseband are preserved. The offline image tool verifies the ext4 filesystem and
copies only the GUI, benchmark package and an explicit build record. Source,
compiler/model identities and checksums accompany the package. Host emulation and
successful compilation do not establish that the card will boot or audio will work.

Use the spare microSD card. Preserve the working card and back up settings using
the firmware's supported backup procedure. Do not apply a baseband update for these
tests. Verify downloaded image hashes against SHA256SUMS before writing the spare
card with your usual SD imaging tool. Confirm the selected device is the spare card.

1. Boot `x6200-baseline.img`. Record application/baseband versions and verify normal
   receive, controls, waterfall, speaker and normal microphone behavior using the
   ordinary firmware procedure and dummy load. If behavior differs from the working
   card, return to that card and record the failure before further tests.
2. Once baseline behavior is established, write `x6200-diagnostic-readonly.img` to
   the spare card. Repeat the ordinary checks. Settings → Info shows the diagnostic
   snapshot: cached commands are separate from the packet TX flag, age and GUI state.
   Record what the display shows in receive. Missing or stale packet data is unknown.
3. With the GUI/waterfall running, stay in receive and close other digital apps.
   Open a shell on the radio by your existing supported access method. Run:

   ```sh
   cd /opt/freedv-benchmark
   python3 tools/freedv/run_benchmarks.py --radio --output /mnt/freedv-smoke-01
   ```

   The benchmark never accesses the microphone, speaker, RF controls or physical
   audio devices. If any workload fails, retain the entire new result directory.
4. If all seven workloads execute and validate synchronization/reacquisition, run:

   ```sh
   python3 tools/freedv/run_benchmarks.py --radio --suite qualification --output /mnt/freedv-long-01
   ```

   This takes about 3.5 hours plus warm-up/initialization. Keep the GUI and waterfall
   active throughout. Record settings, observed GUI responsiveness and any interruptions.
   Ctrl-C stops the benchmark; it does not change radio state.
5. Return the result directory, `/etc/freedv-test-build.json`, versions and operator
   observations. Nothing uploads automatically. Captures/configuration databases are
   not collected. These results qualify only measured codec properties; the overall
   milestone remains INCONCLUSIVE until the native routing and recovery tests exist.

Do not run RECORD/MODEM/PTT register experiments from a shell. The later guarded
firmware session must own those changes and wait for fresh observed RX before
restoration. No RF routing experiment is authorized by the read-only page.
