Linux development firmware for the X6200 native half-duplex FreeDV/RADE work. **Native RX/TX remains BLOCKED on the stock baseband** until the required audio-routing, independent PTT and keying-control contract is established. This image does not update the baseband.

Download `x6200-native-development-windows.zip` for a complete `.img` and the Linux documentation, or `x6200-native-development.img.xz` for the compressed image alone. Check `DOWNLOAD-SHA256SUMS.txt` and read `START-HERE.md`.

The build adds explicit capability checks, guarded PTT/startup handling and persistent-session traces. Validation passed: all 40 host tests, X6200 development/normal and X6100 compatibility builds, real-codec checks, ARM/QEMU processing with zero failures, and image filesystem/runtime/signing-key/partition-preservation checks. No physical conversation or RF qualification is claimed.

Original build source: `dd42af45012b2b31da4c0fc2d876581052bb817b`. The release source snapshot contains the matching application/build/test files; separate baseband research documentation and local history are excluded. `PUBLICATION-MANIFEST.json` records those documentation-only exclusions and the published asset identities. No application code or image bytes were changed for publication.

Subsequent MCU analysis reinforces the blocked status: MODEM selects line input at ordinary TX entry, and RECORD changes codec routing and receive DSP. This release is a software development checkpoint, not the next hardware audio/RF qualification candidate. The next candidate needs reviewed explicit route transitions and independent physical PTT/keying support; legacy route/gain trials are not the recommended next campaign.
