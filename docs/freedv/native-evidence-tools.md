# Native audio evidence tools

Start with the [current receive-only operator guide](candidate-test.md) and
[integrated qualification protocol](native-half-duplex-qualification.md).
The old Session 1/2 route procedures are historical references. Legacy Stage is
withdrawn in the Python/Windows helper before any GUI request; inventory success
grants no TX. CaptureWatch can open the existing capture source, but it does not
change the route. Virtual probes and file decoding remain simulation/offline
checks, not physical qualification.

## Portable package and assessment

Build the Windows-friendly ZIP from the repository:

```sh
python3 tools/freedv/build_native_test_package.py --output /path/to/new-test-package.zip
```

The package includes the current guides, read-only inventory/historical
collection helpers, the standalone v2 conversation assessor and per-file
checksums; no radio binary or MCU payload. Current inventory and assessment use
the Python standard library. Optional historical SDR offset analysis needs
NumPy/SciPy, listed in DEPENDENCIES.txt. The full-checkout native_session.py
commands below are historical evidence tools, not the next hardware procedure.

Assess a returned directory or ZIP (Windows path separators are supported):

```sh
python3 tools/freedv/native_session.py analyze --input returned-results.zip --output new-assessment.json
```

The assessment verifies selected-state comparisons, fresh post-clear packet RX,
flush acknowledgement, stop causes and audio errors. It does not infer external
RF cutoff, intelligibility or native receive independence from those checks.
Input archives are limited to 256 MiB; leave large IQ on the PC and supply hashes.

## Engineer-operated pinned reference decoding

Use the pinned RADE checkout, matching already configured host build, model
archive and frozen modem fixture. This command rebuilds only the host decoder and
executes the known-fixture check, preserving executable/library/fixture identities:

```sh
python3 tools/freedv/native_session.py validate-reference \
  --source /path/to/rade_c --build /path/to/rade-host-build --output /path/to/new-reference-check
```

Copy the returned SDR WAVs beside the operator's recordings.json, with relative
WAV associations. Run:

```sh
python3 tools/freedv/native_session.py decode --recordings /path/to/operator/recordings.json \
  --decoder /path/to/rade-host-build/src/rade_rx_wav --ffmpeg /path/to/ffmpeg \
  --reference /path/to/new-reference-check/reference.json --output /path/to/new-decodes
```

Original files remain unchanged. Conversion produces mono PCM16 at 8 kHz;
RADE V1 plus FARGAN produces speech at 16 kHz. Commands, logs and hashes are saved.
Identify the spoken turn phrase by listening before assigning the transmitted
speech gate PASS. Non-silent decoder output alone is not a pass. Build provenance
is a local trusted engineer record, not a cryptographic attestation.

## External RF timing

The `timing` subcommand records an operator-measured interval from correlated
video and a conservative uncertainty against the real evidence file:

```sh
python3 tools/freedv/native_session.py timing --interval-ms ACTUAL_MEASUREMENT \
  --uncertainty-ms ACTUAL_UNCERTAINTY --evidence /path/to/video --output /path/to/new-rf-timing.json
```

The numeric placeholders must be replaced by measurements, not guessed values.
PASS requires the conservative sum <=500 ms and the stated correlation/continuity
conditions. A longer display interval with unknown positive latency is INCONCLUSIVE.
No video timing is extracted automatically; unsynchronized timestamps are not a
valid measurement. Physical held PTT remains a separate result.
