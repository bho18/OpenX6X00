# First X6200 hardware inventory review

The operator returned `inventory.json` and `manifest.json` after running the
read-only collector with the normal GUI active. Collection completed; the old
helper's subsequent gzip archive creation failed because the radio's Python
lacks gzip support. This packaging error does not invalidate the JSON files.
PR #23 adds a plain-tar fallback and tests both packaging paths. Do not repeat
collection using the existing output directory or change the radio's Python.

Collection timestamp: `2026-10-27T02:33:07.843270+00:00`
(2026-10-26 21:33:07 America/Chicago). This report commits selected observations
and hashes; the uploaded raw results stay outside Git.

## Observed baseline

| Item | Evidence | Interpretation |
| --- | --- | --- |
| Architecture and kernel | `armv7l`, Linux `6.1.82` | ARMv7 runtime confirmed |
| Application | `/usr/share/x6100/app-version`: `0.3.6` | Confirms the operator's application version; exact installed commit is unknown |
| Image stamp | `model=x6200`, `level=2`, `version=0.3.4` | Identifies the original card image separately from the updated application |
| Xiegu baseband | Operator previously reported `1.0.7` | Collector deliberately does not query baseband registers |
| Python | Operator shell reported `3.11.8` | Inventory executed; optional gzip support unavailable |
| GUI | `pidof x6100_gui` succeeded | GUI process present at collection time; display/waterfall activity not measured |
| CPUs | `cpu0` through `cpu3`; affinity `[0,1,2,3]` | Four enumerated CPUs available to the collector |
| Frequency/governor | All four samples `1008000` kHz; `performance` | Instantaneous readings, not sustained-load measurements |
| RAM | `MemTotal=1026516` KiB; `MemAvailable=921356` KiB | Approximately 1002 MiB total and 900 MiB available at collection |
| Swap | `SwapTotal=0` | No swap observed |
| Thermal sensors | No `/sys/class/thermal/thermal_zone*` entries returned | Temperature unknown; cannot infer absence of thermal throttling |

Upstream v0.3.6 keeps `/etc/openx6x00-image` unchanged during application updates;
its X6200 required image level is 2. The `0.3.4` image stamp alongside application
`0.3.6` is therefore not evidence that the application update failed and is not,
by itself, a reason to flash a card. Preserve the current working installation.
Neither version string identifies the exact installed libc/NE10 dependencies.

## Audio observations

All collected ALSA enumeration, mixer and PulseAudio commands exited with zero.

- ALSA card 0 is `sun8i-a33-audio`; device 0 exposes capture and playback.
  `Subdevices: 0/1` indicates no free hardware subdevice at collection, consistent
  with an active sound server; it does not mean the endpoint is missing.
- ALSA names include `mixcapture` and `mixplayback`.
- PulseAudio source `alsa_input.platform-sound.stereo-fallback` and sink
  `alsa_output.platform-sound.stereo-fallback` both report `RUNNING`, `s16le`,
  two channels, 44100 Hz. The sink monitor source is also listed, in `IDLE`.
  A monitor is a playback tap, not independent evidence of received RF audio.
- Mixer `Mic1` capture switches are on; `Mic2`, `Line In`, `Mixer` and
  `Mixer Reversed` capture switches are off. These are ALSA control labels;
  they do not establish hand-mic versus built-in-mic wiring at the baseband.
- `AIF1 AD0 Stereo` and `AIF1 DA0 Stereo` are both `Mix Mono` on both listed
  channels. Separate left/right routing cannot be inferred from these settings.
- ADC/AIF1 capture and AIF1 playback digital volumes are 160, reported as 0 dB;
  `ADC Gain` is 3, also reported as 0 dB. No gains or switches were changed.

These observations establish endpoint availability and selected current mixer
state. They do not establish which signal is captured, capture during RF TX,
speaker/RF separation, analog leakage, feedback, latency or sample continuity.
The full RECORD/MODEM/PTT matrix and supported channel alternatives still require
the guarded in-process diagnostic implementation and independent observations.

## Source and evidence identity

The returned package identifies collector source commit
`7564fc10e33bd0f7219ca9e3ebd6b4595e1ebe53`, from
[run 40432198181](https://github.com/bho18/OpenX6X00/actions/runs/40432198181).
The four file hashes recorded in its manifest match the tracked `README.md`,
`collect_inventory.py`, `inventory.py` and `sources.json` at that commit.
This identifies the collection tooling, not the installed GUI or base image.
The JSON files are operator-supplied observations, not a signed hardware
attestation. The original result-directory `SHA256SUMS` was not returned.

| Returned file | SHA256 of received bytes |
| --- | --- |
| `inventory.json` | `6bfe95ebd180fa12858fcf5c1844cfcf206531bf0c08bd10d081819ebad43948` |
| `manifest.json` | `029bda1b02871fb1e5d1d14875f40927a864b6f7351fc56d16a7ff1f697c4b45` |

## Next measurement and decision status

Read-only inventory collection is complete. Native audio routing, PTT/recovery
and RADE/700E performance remain **INCONCLUSIVE**; the inventory performed none
of those tests. No external-audio fallback is justified by this evidence.

Before asking the operator to run the existing SDK benchmark package on this
updated working card, check the dynamic-loader/libc version, installed NE10
library identity and free data-partition space. Current inventory does not
contain those observations. Any benchmark on this installation must record its
actual version/image stamp and remain preliminary until the full sustained-load
gates are measured. Do not replace system libraries or flash the older v0.3.5
test images to resolve a runtime mismatch.

The next executable test is a short, in-memory 700E/RADE benchmark with no audio
device or radio-control access, subject to that runtime compatibility check.
Keep the normal GUI/waterfall active in receive. The 30-minute workloads follow
only after successful short runs and review of timing/resource results.
