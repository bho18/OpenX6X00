# Official FreeDV GUI reference procedure

Keep the frozen codec fixtures and arithmetic checks unchanged. The older
backend runner remains additional evidence, not a complete GUI application.
The new `qualification/reference.py` verifies pinned GUI, backend, RADE, upstream
Opus archives and model hashes automatically before extracting disposable trees.
It replaces the two floating backend/RADE references only in disposable copies
and records that adapter. It does not apply our acquisition patches to the
reference. Two recorded GCC 14 compatibility edits explicitly qualify
`std::isnan`/`std::isinf` in the GUI SNR checks; codec arithmetic is unchanged. Full GUI builds and source-only verification have separate statuses.

Pins and archive checksums are in `qualification/reference-sources.json` and
`tools/freedv/sources.json`. Host package locks, build configuration, compiler,
platform, audio library/device/settings and generated binary hash accompany a
complete build. Require system dependencies at the recorded versions to avoid
floating dependency fallbacks. The build manifest must name the complete
`freedv` executable; a backend executable or configure success does not qualify.

Separately install an official GUI release on the reference computer. Record
its version, original installer/package hash, installed executable hash, platform
and audio settings. Do not substitute the source-built development GUI's identity
for the installed-release track.

Prepare a manifest of at least 100 numbered original speech recordings, containing
turn identity, phrase transcript, first and last words, rate/channels and SHA256.
Use RADE V1 explicitly, upper-sideband orientation and half duplex. Test both
native TX → GUI RX and GUI TX → native RX at native 8 kHz and 44.1/48 kHz audio
boundaries. For every direction/rate record cold entry and retained-context warm
turns. Keep original speech, modem output and decoded speech; record sample
counts and any rate conversion. Warm codec output need not match a fresh context
bit for bit.

A listening reviewer identifies intelligible phrases and first/last words against
the numbered originals, confirms turn identity/no previous-turn speech, and
records name/date and artifact hashes in the assessment. Synchronization, RMS,
nonzero output or synthetic tones do not establish intelligibility. Until the
review is recorded, speech acceptance remains INCONCLUSIVE. Final RF RX with
speaker on additionally needs a transmitting peer or controlled RF source.
