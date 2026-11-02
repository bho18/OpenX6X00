# Frozen RADE V1 receive fixture

`rade-v1-reference-s16le.raw` is a generated modem waveform from the pinned
RADE C project's `input_sample.wav`, not a radio recording. Its authoritative
identity, format and SHA256 are in `tools/freedv/sources.json`, under
`rade_c.modem_fixture`. Revision `rade-v1-host-reference-1` contains 79,872
mono signed 16-bit little-endian samples at 8 kHz (9.984 seconds).

The package builder copies these exact bytes to `fixtures/rade-modem.raw` and
fails if their size or SHA256 differs. The companion provenance metadata is
`rade-v1-reference.json` here and `fixtures/rade-modem.json` in a package.
The package manifest records that metadata's hash and the authoritative pin.
No host transmitter regenerates this RADE receive input during package builds.

This establishes a **new fixed waveform baseline**. Earlier packages generated
RADE modem input on their build host, producing different hashes across builds.
Compare the case manifests' fixture hashes before attributing timing changes
to a codec modification. Package payloads and receiver binaries remain separate:
deployable binaries still require the matching X6200 Buildroot SDK and existing
correctness gates. Host checks do not qualify radio performance.

The original host-validation package used firmware source `d25c7d4d1f7b15932d387456023edbfc4e191524`,
GCC 14.2.0, pinned RADE C/Opus sources and verified model assets. Its transmitter
was run as follows, then the WAV's PCM payload was extracted without resampling:

```sh
rade_c/src/rade_tx_wav sources/rade_c/input_sample.wav rade-modem.wav
```

The recorded generator, library, input, model, configuration and output-WAV
identities are in the provenance JSON. They document the selected reference;
**regeneration on another host is not claimed to be bit reproducible**. Host
Opus CPU dispatch and build configuration can change the generated samples.
The committed bytes and authoritative SHA256 define this fixture. A future
fixture update requires an explicit revision/hash change and renewed functional
RX checks; never derive a new expected hash silently during a build.

The speech input and generator come from the pinned [FreeDV RADE C project](https://github.com/freedv/rade_c/tree/c8a3dc156045cae2cd251e1a4be0c304c9ddf2f9).
Its BSD 2-Clause repository license is reproduced in `rade_c-LICENSE` here.
The transmitter carries Copyright (C) 2024 David Rowe; its complete notice is
retained in `tools/freedv/THIRD-PARTY-NOTICES.md`. Benchmark packages also retain
Opus and model notices under `licenses/`. This PCM fixture contains no model
weights or executable code.
