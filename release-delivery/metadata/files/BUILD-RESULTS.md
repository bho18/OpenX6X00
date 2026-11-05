# Build results

- Release: `freedv-native-development-20261105.2`.
- Built source: `bbcd265e3f644f508067eafb157e4f31978adb11`.
- Published corresponding-source snapshot: `e2d5cdc7c85f8bcfd74d1b2e2d20ce2d55757ca1`; only incidental research documents excluded.
- Host regression checks: 42 passed.
- X6200 diagnostic/normal and X6100 full application builds: passed.
- Full host native validation GUI compilation, four targeted codec checks and isolated GUI transport smoke: passed. The validation entry was not driven.
- ARM/QEMU: 33 modem blocks, 30 synchronized blocks, 29 synthesis blocks, 0 failures.
- Image ABI/dependency closure, filesystem, signing-key and root-partition preservation checks: passed.

Hardware qualification remains **BLOCKED**. Physical audio routes, independent PTT, RF-off timing, speech intelligibility and processing margin are unqualified. Reused codec provenance is not a new on-radio timing result. No radio was accessed or flashed during this build.
