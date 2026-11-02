# Updated image: short TX delivery check

Use this procedure only on the new delivery-fix diagnostic image, identified by
its source/build manifest. The original `2083e3e7` image does not contain the new
case or fixes. Preserve the original working card, settings and baseband; use the
spare card. This is a short delivery validation, not another codec benchmark or
ten-turn batch. Native receive independence remains unresolved.

The update adds a one-frame TX startup lead using the existing bounded queue,
cancellable output backpressure, timed underflow records, full short WAV excerpts,
capture activity levels and a fixed-waveform TX case. It changes neither the codec
models nor microphone/modem gain. Software-controlled TX still cannot exceed ten
seconds. Physical PTT is released throughout; the dummy load stays connected.

## Prepare once

1. Verify ordinary receive, controls and waterfall after boot. Record application
   and baseband versions and the image checksum. Use USB, split off, TX shift zero.
2. Set up the two SDR++ recorders using `SDRPP-RECORDING.md`. Audio is mono Int16
   WAV from the USB receiver stream; Baseband WAV is IQ. Disable Ignore silence,
   squelch and audio noise reduction. Tune the SDR VFO to the exact radio frequency,
   include the full approximately 3 kHz USB passband and avoid input overload.
3. Keep large IQ on the PC. Record its path/hash, sample rate and center frequency.
   Start each recording about one second before Run and stop about one second
   after RF ends. Associate filenames with the displayed radio case numbers.
4. Open Settings -> Info -> Open FreeDV Diagnostics and Acquire. Stay in this
   acquired session until finished. Select **R1 M1 P0 / Hand mic / Existing mono /
   Keep mixer**. The top dropdown is the case; the smaller dropdown is the route.

## Qualify the exact profile

1. Top **Route R1 M1 P0**, Arm -> Run. Speak immediately into the hand mic.
   Expect the generated beep on RF without ordinary analog speech. Watch the
   displayed **AC** capture level: it should respond when speaking, rather than
   remain around the nearly constant background value. The number is PCM activity
   after DC removal, not proof of microphone identity or intelligibility.
2. After Ready/restoration, immediately select **Confirm independent mic/TX route**
   and press **Run without Arm**. Selecting it alone does not save confirmation.
   Require "Observation confirmed locally". Do not confirm usable microphone
   capture if the activity never responds. Record that unresolved input and skip
   the dependent mic trials; the fixed-waveform delivery test remains useful.
3. Same small route and settings: **Manual Stop test**, Arm -> Run -> Stop after
   approximately two seconds; **Watchdog timeout test**, Arm -> Run and let its
   three seconds expire; **Audio failure test**, Arm -> Run and let its intentional
   failure stop it after approximately 1.5 seconds. Wait for restoration each time.
4. Select **Confirm Stop/timeout/failure observations**, Run without Arm; require
   the confirmation message. This confirms functional recovery; external 500 ms
   RF timing remains a separate result. Do not Close or change the profile.

Stop the sequence for stuck RF, stale status, failed restoration, unexpected
capture/codec/write faults or protection errors. Preserve the evidence. Software
cannot override physical held PTT; release it if the panel requests that.

## Four short comparisons

| Case | Top dropdown / action | What to do and observe |
|---|---|---|
| F1 | **Known RADE waveform TX 8s**, Arm -> Run | Stay quiet. This streams the pinned known modem recording to RF without microphone encoding. Record the full SDR WAV and IQ. The known waveform is deliberately cut at the bounded deadline; this is not an EOO test. |
| M1 | **Hand/built-in mic RADE TX 8s**, Arm -> Run | Say "First microphone test, one two three" immediately after Run. Watch capture AC activity. Let the transmission finish normally. |
| M2 | Same case, new Arm -> Run | Say "Second microphone test, four five six" immediately. Let it finish normally. |
| S1 | Mic TX if qualified, otherwise **Known RADE waveform TX 8s**; new Arm -> Run | Press Stop after approximately three seconds. RF should cease and restoration should complete without waiting for EOO. |

Keep **R1 M1 P0 / Hand mic / Existing mono / Keep mixer** throughout. Do not
press physical microphone PTT. The SDR should hear digital sound, not analog
speech. Write F1/M1/M2/S1, the actual radio case number and filenames in notes.
Normal mic TX may finish before eight seconds when EOO and its bounded drain
complete. Do not keep speaking through its final second. The new image saves
full short excerpts rather than only the first four seconds.

F1 requires the same-profile functional Stop confirmation but does not require
an attestation of usable microphone capture. M1/M2 require both confirmations.
If capture remains quiet, run F1/S1 and export; do not falsely confirm mic capture.

The fixed-waveform file and live microphone cases serve different purposes:

- Clear F1 decode establishes the tested Linux playback/RF path for a known input.
- Clear local mic decode but bad SDR mic decode points to delivery/RF impairment.
- Quiet or bad microphone capture is its own input/source/level issue.
- Balanced accepted-sample counts do not excuse underflows or timing discontinuities.

## Export once

After Ready/restoration, press Export. Use the existing test kit's
`Collect-NativeSession.ps1` to copy all `/tmp/freedv-native-*` folders plus your
notes and short SDR audio WAVs. Keep large IQ on the PC with file references and
hashes. Export before rebooting. No helper arms RF and no automatic upload occurs.

Return the consolidated ZIP. Reference-decode F1 and the complete normal mic
recordings, then compare intelligibility, timing, capture levels, underflow event
times, sample accounting, prefill delay, EOO, abort and exact restoration.
Do not start ten mixed turns until this short voice-quality check passes.
