# X6200 native half-duplex Linux development release

This is a Linux application development image. **Stock native RX/TX remains BLOCKED** by the unresolved radio audio/PTT contract. The baseband and FPGA are unchanged; this is not a completed native FreeDV conversation release.

The Windows ZIP contains exactly one full SD-card image, `x6200-native-development.img`, plus these guides and verification records. Decompressing the separate `.img.xz` download produces the same image.

- Original built source: `dd42af45012b2b31da4c0fc2d876581052bb817b`.
- Published corresponding-source snapshot: `1a2aad375dadc8a14f2b12c6c939b369cc30004d` (research documents excluded; no compiled code changed).
- Passed: 40 host tests; X6200 development/normal and X6100 compatibility builds; full native-validation host simulation and targeted codec checks.
- ARM/QEMU processed 33 modem blocks with zero failures. This is emulated correctness, not radio timing or RF evidence.
- Image checks passed for ELF dependencies/loader, filesystem, written files, signing-key preservation, and unchanged bytes outside the root partition.
- Native validation controls compiled; simulator transport smoke passed. Native validation UI interaction and a physical conversation were not performed.
- No radio was accessed, flashed or keyed during this build.

Read [START-HERE](START-HERE.md) and the [integrated qualification procedure](guide/docs/freedv/native-half-duplex-qualification.md). The [publication manifest](PUBLICATION-MANIFEST.json) identifies the unchanged image, documentation-only source exclusions and rewritten delivery documents. [Software verification](software-verification.json) is retained byte-for-byte from the original build; its complete source manifest includes the research documents omitted from public corresponding source.

Download `x6200-native-development-public-source.tar.xz` from the same release for application source. Only `docs/freedv/baseband-research/` is excluded; every retained code/build/test file is byte-identical. No private Git history or stock-firmware research payloads are included.

Use `DOWNLOAD-SHA256SUMS.txt` for the release downloads and `SHA256SUMS` for the extracted Windows ZIP contents.
