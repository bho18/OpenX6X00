# SDK benchmark dependency contract

Deployable binaries use the SDK produced by the pinned X6200 Buildroot image
build. Debian ARM/QEMU builds are diagnostic comparisons and are not deployed.
The benchmark package records its compiler, firmware source commit, Opus model
and configuration hashes, static resampler hash and ELF dependencies.

The SDK exposes compiler aliases for the same ARM Buildroot target. The builder
accepts aliases only when their reported target agrees. SDK relocation and the
baseline artifact checksum/commit checks run before compilation.

Opus can detect the SDK's NE10 1.2.1 library automatically. RADE's imported Opus
CMake target otherwise loses that transitive dependency, causing undefined
`ne10_fft_c2c_1d_float32_neon` references at executable link time. The disposable
source copy adds the NE10 link dependency to imported Opus and the standalone
benchmark. The package records the linked SDK library hash. NE10 remains a
baseline runtime dependency; ARM execution/synchronization is checked under
QEMU, and only actual radio measurements qualify timing.

Benchmark code includes the exact generated Opus config.h used by its library.
Without it, the speech feature extractor's caller can choose the generic dispatch
path despite an optimized library. Results report `opus_arch`; this is an
implementation-specific dispatch index, not a performance score. Compare it
within the pinned Opus revision.

The matching SDK does not contain libsamplerate development headers. The builder
downloads libsamplerate 0.2.2 with TLS verification, checks its pinned SHA256,
builds it with that SDK and links it statically into both benchmarks. Its source
and BSD license accompany the source archive/package. No system resampling
library is installed or replaced on the radio. Host validation builds use the
same pinned resampler source.

These corrections make the package build reproducibly; they do not establish
native audio routing, PTT recovery, codec quality on hardware or a real-time
performance PASS. The full qualification record remains the source of those gates.
