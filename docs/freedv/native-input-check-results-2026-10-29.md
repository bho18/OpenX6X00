# Microphone capture checkpoint, 2026-10-29

## Result

The operator reports clear ordinary USB hand-mic speech at the SDR in A0, but no
voice in the new diagnostic captures. The files agree: **R1 M1 P0 has not provided
usable mic capture in this session, with either released or held hand-mic PTT.**
These are tone-routing probes, not RADE encoding runs. The microphone signal is
already missing before any codec is involved. Do not confirm the mic route or
repeat live RADE voice trials on the strength of a large peak number.

Evidence ZIP SHA256:
`1efe62eff2c854791aa633589fc4400c0cb93f2af59a9b500bc99c4f44844c87`.
There are 28 entries and 5,555,220 expanded bytes, with six actual radio cases,
raw capture/output WAVs, build manifests, events and resource observations.
The manifests attest source `21df019884da3e8cdc1b4521a4b4f8a51ca210e1` (.2).
Collection is now working. Operator files contain the copied manifest and an
unfilled notes header; there are no SDR A0/probe WAVs in this checkpoint. A0's
intelligibility and physical button use are operator observations.

## What the high reading means

| Route probe | Recorded AC RMS after initial settling | Recorded peak | Sustained input |
|---|---:|---:|---|
| Case 4 | 0.78 | 283 | Nearly constant; no voice |
| Case 5 | 0.71 | 151 | Nearly constant; no voice |
| Case 6 | 103.05 including a short transient | 7,460 | No voice; approximately 0.71 RMS outside the transient |

All figures are PCM16 sample counts. The displayed fields are **AC RMS** and
**peak**, not interchangeable speech measurements. In case 6 the large event
crosses 20 counts above the DC baseline from about 4.601 to 4.614 seconds: roughly
13 ms. Outside a surrounding exclusion window the capture ranges from about
145 to 152 counts, with DC near 148.5 and AC RMS about 0.71. That large peak does
not demonstrate speech or a microphone becoming usable. Its physical cause is
not established by the recordings alone; no raw button-edge events were logged.

Cases 1-5 also contain brief initial switching activity; their settled samples
are effectively flat. Exported input-level statistics and independently measured
WAV statistics agree. The case-6 transient is present in the WAV, so the peak is
not merely a display artifact. There are no reported capture holes, drops or
capture/write errors. The operator identifies case 6 as held PTT and says an
earlier probe alternated button state; the exact earlier case is unconfirmed.

## Route and restart interpretation

R/M/P are **command flags**, not microphone/speaker names:

| Flag | Meaning |
|---|---|
| R | Baseband RECORD/audio-routing bit. Requests the alternate record/playback path; it is not the WAV recorder's on/off switch |
| M | Baseband MODEM transmit bit. It can key RF even when P=0 |
| P | Application/software PTT bit. It is separate from the physical hand-mic button |

During the active parts of cases 4-6, resource logs contain the cached commanded
tuple R=true/M=true/P=false and fresh observed packet TX. The brief M=false/TX=true
records near case endings belong to clearing/waiting for RX. These logs distinguish
commands and packet TX; they do not prove the baseband's internal microphone mux.
The application requested the selected route rather than silently using the
dropdown's initial R0 M0 P0 setting after reboot.

Reboot resets transient commands, diagnostic selections/confirmations and audio
streams, and restores normal saved configuration. It can change the starting
route or mixer configuration. It does not change the meanings of R/M/P under an
unchanged baseband. Each diagnostic Run explicitly applies its selected route.
Thus a wrong/incomplete route remains a hypothesis, but a reboot-induced wrong
dropdown is not supported by this session's command observations.

Normal saved microphone selection at Acquire is **Auto (2)**; the cases select
**Hand (1)**. Saved hand/internal gains are 20/30, line input/output 10/10 and
monitor 0. ALSA ADC and playback values are 160/160 and AD0 Stereo enum 3. These
saved gains/mixer fields match the earlier silent TX13 and intelligible local
TX14 evidence; they do not identify a gain change after reboot. Normal Auto
versus diagnostic Hand is an actual configuration difference to investigate,
not proof that microphone selection caused the failure.

## Recovery and recording integrity

The manual Stop, timeout and injected-failure cases have their expected first
causes. All six cases finish with matching selected radio/ALSA snapshots, zero
transport/restoration failures and fresh post-clear RX. Command-clear intervals
are 78/44/46/43/45/44 ms. These are software measurements, not external RF timing.
Each playback stream reports one underflow with cancellation already requested;
the evidence does not show recurring steady-state tone starvation. No codec runs
in these cases. Exact external 500 ms, live voice intelligibility and other
physical gates remain separate.

## Software UI correction

The large ordinary TX panel covers the diagnostic AC readout. Diagnostics were
missing from the existing digital-app check that chooses a compact TX meter
strip. [PR36](https://github.com/bho18/OpenX6X00/pull/36) adds that GUI-only check.
Host build/20 tests and both matching-SDK X6200 applications pass. A host-only
local-FIFO simulated TX screenshot verifies Capture AC, observed TX and Stop
remain visible, followed by restoration and Close. The initial automation lost
rapid navigation detents; paced input completed the check. No physical radio
was controlled. The .2 image is unchanged; the fix is for a subsequent verified
build and does not repair microphone routing by itself.

## Next routing investigation

Stop repeating R1 M1 P0 speech trials for now. First compare the documented
ordinary voice-recording path, then isolate MODEM versus software PTT with
R1 M0 P1. The standard voice recorder requests RECORD and suppresses internal-mic
gain; the diagnostic probe retains both saved gains and forces Hand selection.
Those differences must be compared deliberately, one at a time, rather than
inferred from the three route bits alone.

If needed, preserve separate left/right capture under supported stereo/mixer
options before ruling out native capture or mono cancellation. No codec core
changes, automatic gain boost, raw undocumented register writes or external audio
fallback are justified by this checkpoint. Preserve the working card, baseband
and signing policy. Existing local arming, frequency/SWR guards and safe recovery
requirements apply to any new keyed route experiment. Raw recordings remain
private; no upstream submission.
