# RF delivery follow-up, 2026-10-29

## Outcome

The two operator-identified known-waveform recordings reproduce the pinned RADE
fixture over RF. The two live-microphone recordings also acquire and decode with
the pinned reference, but their output is quiet noise, not intelligible speech.
The operator confirms no recognizable words even in listening copies amplified
by about 28 dB. **Live transmitted intelligible speech remains FAIL.**

The present evidence puts microphone content/source/level ahead of an assumption
that the radio cannot encode fast enough. It does not isolate the fault to a
specific microphone, register, gain or software stage. There are no new radio
capture WAVs, local pre-playback modem WAVs or queue/timing counters in this ZIP.

## Evidence and provenance

Input: `freedv-complete-20261029-105610.zip`, SHA256
`b33be403e39e545a9d955109a3af8d837308f83ab67d03683aa1270389fd7af1`.
It has 16 entries, 24,804,555 expanded bytes: 12 mono 48 kHz PCM16 SDR audio WAVs
and four operator metadata files. Seven WAVs are byte-identical to those already
reviewed from October 28; they are historical evidence, not seven new trials.
There are five new recordings. No IQ recording is included.

The operator identifies 10:36:33 and 10:40:10 as known audio, and 10:45:32 and
10:45:55 as live microphone. The 10:42:14 case is unclassified. The hand-mic PTT
was released throughout today's microphone trials. Its state during yesterday's
intelligible local TX14 is not established.

No radio case directories, coordinator exports or build manifests are included.
The reused operator `session.json` still names the original `.1` release; its
empty observations and old creation time identify it as a reused template, not
proof of today's installed build. The intended `.2` candidate is source `21df019`;
this archive does not independently verify that installation or the applied
route/microphone/gain settings. Known-waveform content matches the expected
fixture. Do not silently relabel old metadata as a current build attestation.

The operator reports that copying radio results failed and that the radio was
subsequently rebooted. The recorded error is an SSH connection timeout before
SCP transfer, followed by an invalid local evidence path. Entering the script
body interactively allowed later commands to continue after errors. Today's
temporary radio data cannot be reconstructed from the supplied SDR audio; the
radio uses tmpfs for `/tmp`. A separate previously saved copy, if one exists,
would be needed to recover those counters.

## Reference decode

Unmodified RADE `c8a3dc156045cae2cd251e1a4be0c304c9ddf2f9`, model SHA256
`4ed9445b96698bad25d852e912b41495ddfa30c8dbc8a55f9cde5826ed793453`,
was used with the previously validated fixture check. Decoder SHA256 is
`3b34f0e5082ecca328326088055f51239574b59d2477fd134c22a4491baf780c`;
runtime-library SHA256 is
`ecc1afb3fc038369de7a291faed5317a6874f05ada4f4f5dc5f374bcc1c255c4`.
Original WAVs remain unchanged. Analysis resamples them to mono 8 kHz PCM16;
the reference performs V1 demodulation and FARGAN speech synthesis at 16 kHz.

| SDR filename time | Operator identity | Reference output | Valid tracked sync blocks | Maximum UW errors in those blocks | Decoded AC RMS, after first 0.5 s |
|---|---|---:|---:|---:|---:|
| 10:36:33 | Known waveform | 8.59 s | 62 | 0 | 3,229 |
| 10:40:10 | Known waveform | 8.47 s | 63 | 2 | 3,411 |
| 10:42:14 | Unconfirmed | None | 0 | N/A | N/A |
| 10:45:32 | Live microphone | 6.67 s | 57 | 0 | 88 |
| 10:45:55 | Live microphone | 6.67 s | 57 | 0 | 99 |

RMS is in signed-16-bit PCM counts, not an RF power measurement. The live output
is about 30-32 dB below the known speech, and the operator hears only noise after
amplification. Nonzero samples and clean sync are not an intelligible-speech PASS.
The decoded duration can include synthesis during post-carrier sync holdover;
in particular it is not a measurement of RF keying duration. The known-file case
deliberately cuts at its bounded deadline without a terminal EOO, so its tail is
not an EOO interoperability test. The unclassified file is not counted as a
failed microphone encoder merely because no RADE was acquired.

The microphone files track for 57 consecutive blocks with zero reported UW
errors. This is materially different from never acquiring a modem. UW checks
are sync evidence, not a checksum of every recovered speech parameter.

## Timing and recovered content

After compensating frequency offset analytically for comparison, overlapping
0.8 s windows of the known waveform correlate approximately 0.83-0.87 with the
pinned fixture. Across reference positions 0.6-7.4 s, alignment drifts by about
0.25 ms in each recording; the approximately 4.75 ms discontinuities seen in
yesterday's encoded TX14 are not seen in these checked intervals.

This establishes a working path for the known waveform in the tested captures.
The fixed-file case bypasses the live encoder and its output queue, so it does
**not** by itself qualify the new encoder-fed prefill/backpressure behavior.
Without radio counters, actual underflow counts, accepted-sample accounting and
restoration for today's cases remain INCONCLUSIVE.

Recovered speech parameters in both microphone files are nearly steady and low
energy. As an analysis comparison, the first cepstral feature's middle-frame
mean/standard deviation is about -9.47/0.17 and -9.22/0.16. Yesterday's silent
local TX13 gives about -9.24/0.13; yesterday's intelligible local TX14 gives
about +2.56/1.66. The first feature is a codec-domain value, not calibrated dB.
The first 20 mean recovered features are also much closer to silent TX13 than
to the known speech. A synthetic zero-input reference was encoded and decoded
offline as an additional silence comparison; no radio or physical audio device
was accessed.

The decoder estimates around +50.1/+49.4 Hz offset in the mic captures. Separate
analysis copies shifted those offsets to approximately zero and re-decoded.
They still produce 6.67 s of low-energy output and nearly unchanged feature
statistics. Frequency correction does not recover missing words here. The known
files' offsets are about +47.6/+56.7 Hz; receiver settings and differences between
the reference and the operator's FreeDV version remain possible contributors to
GUI acquisition variability, not an established explanation.

Taken together, the evidence favors absent/incorrect/very low microphone content
at or before encoding over a general failure to transport or acquire RADE. It
does not prove where that content was lost. PTT-dependent microphone enabling is
one hypothesis because physical PTT was released, not a confirmed hardware fact.
Source selection, gains and capture dispatch also need the missing local data.
Do not add TX workers, apply large gain changes or declare the playback issue
fully solved on this evidence.

## Next focused evaluation

1. Establish SSH access and run collection as a script before another RF session;
   see [Windows collection recovery](native-collection-recovery.md). Check the
   radio's current address rather than reusing an earlier card/network address.
2. In the next locally controlled session, keep the tested microphone/route
   explicit and collect one matched set of raw capture, local pre-playback modem
   output, SDR audio and counters. Confirm capture AC responds to speaking.
   Capture-only absence during receive is not definitive for a mic that may
   require keyed routing.
3. Include a controlled released-versus-held physical-PTT input comparison only
   after software recovery and generated-RF routing are established. Log button
   state explicitly: software command bits and aggregate packet TX cannot reveal
   it while MODEM holds TX. This is a separate qualification step, not permission
   to press physical PTT blindly during the existing TX-delivery checklist. Use
   the [PTT review](ptt-evaluation.md), dummy load, local controls and explicit
   release/recovery procedure; software cannot override an unqualified held PTT.
4. Listen to raw speech and reference-decode the local modem output before
   attributing another RF failure to timing. Then compare the same run's RF decode
   and timed underflow events. Export and verify PC copies before any reboot.

No repeated standalone performance batch or routine 30-minute test is needed.
No new firmware change or image is justified yet. Hand mic/radio speaker remain
primary; native receive independence, built-in mic, physical PTT/restart and exact
external 500 ms RF timing retain their separate unresolved gates. The original
70% margin finding is unchanged. Raw audio stays private; no upstream submission.
