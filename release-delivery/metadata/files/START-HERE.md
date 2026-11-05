# X6200 report-informed development build

Release: `freedv-native-development-20261105.2`. Native half-duplex operation remains **BLOCKED** on stock baseband. This revision strengthens software contract and stereo-routing checks and withdraws legacy route/gain experiments. It does not update the MCU/baseband or FPGA and does not establish working native TX.

The Windows ZIP contains exactly one complete `x6200-native-development.img`. Verify `SHA256SUMS` before writing that image to a separate spare card; retain your existing card. The image is already assembled—do not combine its contents with an earlier image.

Read the [candidate procedure](guide/docs/freedv/candidate-test.md) and [integrated qualification protocol](guide/docs/freedv/native-half-duplex-qualification.md) before device work. The immediate next test is a read-only ALSA/PulseAudio/input inventory under that protocol. Endpoint/PTT mapping remains conditional on available read-only evidence; no mapping UI is claimed. `native-voice-test-kit.zip` contains the matching collection and offline evidence helpers.

Extract `native-voice-test-kit.zip` into its own folder. Copy this release's outer `candidate-manifest.json` into that folder beside `Invoke-FreeDVAgent.ps1` before running the documented `Preflight` command. Keep the outer release directory intact; the helper kit has its own guide and checksum file.

[Build results](BUILD-RESULTS.md) record what actually passed. [Build details](guide/docs/freedv/native-development-build.md) describe reproduction and limitations. `software-verification.json` is the exact original software record; `PUBLICATION-MANIFEST.json` records the documentation-only source exclusion and public Git snapshot. The separate corresponding-source archive contains the retained application/build/test files byte-for-byte. No private history bundle or stock firmware research payload is included.
