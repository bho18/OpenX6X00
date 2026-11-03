# X6200 FreeDV continuity-fix candidate — current test instructions

This is the single current workflow for this iteration. Flash the **new continuity-fix image** to the candidate card. The working .2 card is recovery only. The downloaded ZIP contains one complete `.img`; extract that file and write it with verification. There are no parts to assemble.

## PTT instructions for every turn

**Do not keep the hand-mic PTT released for the whole turn.** This test deliberately compares microphone input with that button released, held, then released again. Apply the same sequence to all four turns (USB one/two and Digital one/two).

* **Radio-side PTT:** leave released throughout the entire test.
* **Hand-mic PTT:** leave released during boot, setup, Acquire and Arm. Start timing when you press **Run**. Leave it released for seconds **0–2**, press and hold it for seconds **2–5**, then release it at **5 seconds** and keep it released through the remaining speech, automatic finish, restoration and the wait between turns.
* If the case faults, **release hand-mic PTT immediately**, then use Stop if needed and follow the failure-collection step.

Speak during all three phases. The earlier instruction to keep both physical PTT controls released for an entire turn belongs to the previous test; it does not apply to this comparison. This clarification changes no test timing or firmware and requires no new image download.

## What this iteration fixes and tests

The independent recording in the November 3 08:39 collection proves a defect in the previous candidate: turn two silently lost **25,520 samples (578.685 ms)** during a 773 ms capture-delivery pause. The original 200 ms PulseAudio record queue could overflow without reporting holes or drops. The new candidate requests one second of finite transport capacity, verifies the actual grant, retains the **500 ms processing-backlog limit**, and faults explicitly when capture delivery stalls more than 500 ms after the first callback. Initial capture has a separate one-second deadline. Late callbacks cannot erase a recorded stall.

The adapter now keeps its empty playback stream uncorked while acquired, with cancellation plus acknowledged flush fencing each turn. This avoids permitting idle suspension between ordinary turns. No silent PCM is written to keep it alive. Device suspension is a plausible contributor to the physical pause, not a proven hardware cause.

The AF-volume-4 experiment did **not** restore microphone audio: both the GUI and independent source recording stayed nearly constant during TX. We still need to distinguish mode-dependent input routing from physical hand-microphone PTT gating. This workflow combines that comparison with the retained-turn check: **two eight-second turns in USB, then two in USB-DIG**. No tone, speaker, old A/B/P matrix, virtual probe, RX test or official-GUI listening repetition is requested.

## Install and prepare once

1. Preserve the working .2 card. Extract the release ZIP, verify its download/image hashes against `DOWNLOAD-SHA256SUMS.txt` and `candidate-manifest.json`, flash its single `.img` to the candidate card and boot with both physical PTT controls released. During each test turn, use the hand-mic PTT sequence above.
2. Use the same dummy load, attenuated SDR connection and 14.100 MHz setup. The diagnostic requests **1 W**. Keep the SDR demodulator in upper sideband, with the same frequency, approximately 3 kHz filter and gain for both profiles. Avoid clipping. Create a `recordings` folder in the extracted release directory.
3. On the ordinary radio receive screen select **USB**, speaker/AF **4** and hand microphone. Keep microphone/monitor gains and processing settings consistent for the two modes. The collector preserves effective settings; record any intentional differences rather than assuming the SSB and DIGI saved groups match. Do not retune to compensate for the previously observed SDR recording offset.
4. Open **APP → Modes → FreeDV test**. Select **Repeat hand-mic TX 8s + finish / R1 M1 P0 / Hand mic / Existing mono / Keep mixer**. Select **Acquire once** and wait for ready. The mono choice uses the retained production capture stream.

## USB: first turn and retained turn

5. Start one continuous SDR WAV recording as `recordings\USB.wav`. In PowerShell, from the extracted release folder, run:

   ```powershell
   powershell -ExecutionPolicy Bypass -File .\tools\Start-FreeDVInputCheck.ps1 -RadioIp 192.168.100.62 -CandidateManifest .\candidate-manifest.json
   ```

   The helper verifies the installed GUI hash, physical capture source, recorder and storage, then starts a **60-second independent input recording**. It uses the existing SSH key and strict host checking. It does not key RF, change routing/mixers, restart services or alter preferences. If preflight says BLOCKED, stop and preserve the output. The execution-policy flag applies to this process only.
6. Locally **Arm → Run**. During the eight-second case, use the following approximate timing, speaking continuously and announcing the physical button changes:

   | Case time | Hand-mic PTT | Say |
   | --- | --- | --- |
   | 0–2 seconds | Released | “USB one, released, alpha start.” |
   | 2–5 seconds | Press and hold | “USB one, held, one two three four.” |
   | 5–8 seconds | Release | “USB one, released again, alpha finish.” |

   Keep the radio-side PTT released throughout. Release hand-mic PTT **before** the automatic case finish. Listen on the SDR for any ordinary analog voice alongside the modem; note whether it appears only while held. The software MODEM command does not guarantee control over physically held PTT.
7. Wait for automatic unkey and **completed restoration**, then wait **20 seconds**. Leave the session acquired. **Arm → Run** once more with the same released → held → released timing, saying “USB two … bravo start … five six seven eight … bravo finish.” Do not Close/Acquire between the two turns.
8. Export after the second turn, then Close FreeDV and stop the SDR recording. If any case faults or fails to restore, release hand PTT, use Stop if needed, export and **end this batch**. Do not retry a failed session or continue to USB-DIG. Collect the failure evidence once the recorder finishes. Restoration must wait for observed RX; use the established recovery path if it stays in TX.

## USB-DIG: same comparison

9. After closing FreeDV and after the first 60-second watcher has finished, select **USB-DIG** on the ordinary receive screen. Retain 14.100 MHz, AF 4, microphone/monitor gains and the same SDR settings. Reopen FreeDV test and select/acquire the same case and route.
10. Start `recordings\USB-DIG.wav` and run the **same PowerShell helper again**. It creates a separate timestamped independent recording.
11. Run two eight-second turns using the same physical PTT timing and the same 20-second interval while retained. Say “Digital one … charlie start … one two three four … charlie finish,” then “Digital two … delta start … five six seven eight … delta finish.” Release PTT before each case ends. Export, Close and stop the SDR recording. Stop after any fault; no retries.

## Collect once

12. After the second watcher's 60 seconds have elapsed (or the first if the batch stopped early), run:

   ```powershell
   powershell -ExecutionPolicy Bypass -File .\tools\Invoke-FreeDVRetest.ps1 -Action Collect -RadioIp 192.168.100.62 -RecordingDir .\recordings
   ```

   Upload the resulting `freedv-collect-*.zip`. Keep the original files unchanged and do not reboot before collection. Include whether each turn completed, whether input or analog speech appeared only while hand PTT was held, and any difference in button behavior. The collector includes case WAVs/JSON, independent raw/WAV capture and delivery timestamps, installed identity, logs and mixer/device inventory. COLLECTED means retrieval, not a voice-test PASS.

## How these results will be assessed

* Compare every GUI capture sample against the concurrent reference using `tools/check_capture_continuity.py`. A unique full match establishes PCM continuity only. A proved deletion fails; ambiguous alignment stays INCONCLUSIVE. Neither outcome establishes intelligibility.
* Check actual granted buffers, first-callback delay, subsequent gaps, first-fault reason and frozen queue depths. Fault **16 / FD_AUDIO_CAPTURE_STALL** is an explicit stalled-input failure, not a successful turn. A continuing server stall may now stop promptly instead of producing an apparently completed short recording.
* Check one EOO per successful turn, retained resources, fresh RX restoration and underflows before drain. Keep drain-tail underflows distinct.
* Compare microphone activity in released/held/released phases within each mode, then compare modes with effective settings recorded. Mic recovery only while held would support PTT-dependent gating. Recovery in only one profile would support a profile-dependent route. Analog speech mixed into RF still fails isolation even if mic capture improves.
* Match saved modem output to SDR and assess derived frequency-corrected copies when useful. No additional listening task is needed until saved microphone input contains identifiable speech. USB and USB-DIG remain unqualified; independent hand-PTT release, bidirectional RF and prior processing-margin failures are not cleared by this test.

## For local Codex

Use this guide only. Automate hash verification, the two read-only watcher launches, completion checks and the final collection. The user operates Arm/Run and physical PTT locally. Use the current manifest; do not change SSH keys, host records, global execution policy, services, routing or RF remotely. Preserve the working card and all original evidence. Verify helper SHA256SUMS and ZIP CRC/member hashes. Do not publish private evidence or repeat the previous probes/basic tests. Report actual faults, missing files and unassessed listening separately from collection success.
