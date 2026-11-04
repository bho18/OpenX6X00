# Local Codex assignment: current X6200 FreeDV route investigation

Execute this task on the user's Windows computer. Take responsibility for all computer operations; do not hand the user another list of shell commands. The user should only position the acoustic speaker, use the required physical radio controls, and assess listening. Start useful work immediately and continue through the applicable result branch. Request physical actions only when their prerequisites are ready.

## Location and exact identities

Work in `C:\Users\kd7cr\Downloads\x6200-freedv-route-review`.

- Radio: `root@192.168.100.62`; web UI: `http://192.168.100.62/`.
- Use Windows OpenSSH with the existing Windows SSH key and verified host record. Keep strict host checking. Do not copy private keys, change authentication or edit known-host records. If access is genuinely unavailable, report the exact failure.
- Installed candidate: `freedv-route-review-20261104`, source `725c9d294958def3633738d160873338c99917bb`, GUI SHA256 `aac58c9a441da877ea1b59671f5ab497687ba8654716b60683ca4b8ca5fd6396`.
- Corrected `Invoke-FreeDVAgent.ps1` SHA256: `99a281e9de3349049714f67a92a1cc0d80c162966a24f18a6221959ddc1ddb87`.
- Corrected helper download: https://github.com/bho18/OpenX6X00/releases/download/freedv-route-review-20261104/Invoke-FreeDVAgent.ps1
- Operating guide: https://github.com/bho18/OpenX6X00/blob/5e9ef0296ca1163499784d15cbb70d98d67f5a35/docs/freedv/candidate-test.md

This assignment supersedes the obsolete short LOCAL-CODEX-TASK note in earlier source archives. In particular, there are no held-PTT phases in this experiment. The installed image and Python helpers stay in place. Do not reflash or reinstall the image.

## Operating boundaries

Both physical PTT buttons remain RELEASED throughout every case in this assignment, including RF. The diagnostic screen's Arm and Run controls are separate from PTT.

Every RF transmission requires the user's genuine local Arm then Run, for that individual transmission. Do not generate these through the browser, remote input, a provenance workaround or direct control writes. Staging alone cannot transmit. Local Acquire and the initial unkeyed Run also use the existing interface's physical controls.

Use USB initially, the existing suitable test frequency, existing dummy load and attenuated SDR arrangement. VOX must be off before Acquire. Freeze microphone preferences, gains, monitor level, filtering and mixer settings across comparisons. Do not experiment with raw I2C, register bits or ALSA settings. Do not restart the GUI/system PulseAudio, reset preferences or change baseband firmware.

Do not repeat old tone/speaker demonstrations, the A/B/P matrix, USB-DIG sweeps or 100 virtual lifecycles. The optional virtual probe is not a prerequisite for this route test. Stop unchanged repetitions at the first unresolved failure; collect and diagnose the evidence already obtained.

## 1. Bootstrap and prepare without user commands

Check the local helper hash. If missing or wrong, preserve the existing copy and download the corrected helper above, then verify its hash. Do not replace the other candidate files. Verify `candidate-manifest.json` and let preflight compare the installed radio identity.

Use this launch form for every action, with the working directory above:

    powershell.exe -NoProfile -ExecutionPolicy Bypass -File ".\Invoke-FreeDVAgent.ps1" -Action ACTION [arguments]

The override applies only to the child process. Do not change saved/global execution policy. If enforced Group Policy blocks it, report that dependency instead of changing the policy.

Create a timestamped private evidence directory on the PC and log commands, stdout, stderr, exit codes and local helper hashes. Avoid logging secrets. Run `-Action Preflight`, then `-Action SpeechFixture`. If a helper/path/launch problem occurs, investigate and repair routine local scripting problems yourself, preserving the original and recording the diff and hashes. Do not ask the user to execute troubleshooting commands. Do not weaken build checks, RF gates, evidence checks or firmware protection to get a pass; stop the dependent experiment if such a change would be necessary.

Verify `numbered-speech.wav` is mono PCM16 at 44.1 kHz and retain its hash. Start its repeating playback on the Windows speaker in a persistent process you own; System.Media.SoundPlayer with PlayLooping is suitable when the process stays alive. Manage playback yourself rather than asking the user to repeatedly speak or operate a media player. Retain the fixture unchanged.

Ask the user once to position that speaker near the hand microphone at ordinary speech level, keep both PTT controls released, confirm USB/VOX-off and the existing load arrangement, open APP > Modes > FreeDV test, and press Acquire locally. Use existing status/inventory and the browser to verify what can be verified remotely. Do not ask the user to transcribe information you can read. Wait for physical readiness before starting a time-limited source watcher.

## 2. Run the ordinary-recording control

Stage `-Action Stage -Case record-ordinary-zero -RepeatFive`. Require fresh `STAGED` acknowledgement and the exact selected case 25, route R1 M0 P0, mono capture, Keep mixer. The UI label is "Ordinary recorder sequence / internal zero". Record its stage_request_id. This case preserves ordinary microphone selection and hand gain.

Start `-Action CaptureWatch -DurationSeconds 150 -OutputDir <this batch folder>` in a separate persistent process, logging its output. Wait for its RECORDING message, which confirms source samples are arriving. With the fixture playing and the user already ready, tell the user only: "Press Run once on the radio now. Leave both PTT buttons released."

Manage the rest. Five eight-second recordings should run with twenty seconds after restoration between them. Poll Status and Events at reasonable intervals. After the helpers have been copied to the radio, the same read-only route_agent.py status/events commands over SSH avoid recopies on every poll. Retain status/events output with the batch.

On a fault, request `-Action Abort`, verify fresh RX/recovery and stop further cases. Keep collecting the bounded independent source recording; let it finish normally if the environment remains stable. If the watcher expires before Run, do not start a case without coverage. If coverage is lost during a case, mark it incomplete; do not automatically repeat RF to repair evidence.

After all cases and the watcher finish, run `-Action Collect -OutputDir <this batch folder>`, then `-Action Assess`. Capture Assess output locally: it assesses the radio's `/tmp/freedv-agent-results`; OutputDir is not a selector for offline assessment. Resume interrupted collection to the same local folder. Verify transfer hashes and preserve originals.

Explicitly count FIVE distinct completed case directories with the SAME stage_request_id. The helper's per-case assessment alone does not enforce the requested repetition count. Verify no missing/truncated recordings, exact recorder stop, complete control traces, no failed transport and successful restoration. Keep cached state, successful transport and observed RX distinct. Preserve any missing/failed evidence as INCONCLUSIVE/FAIL.

Analyze continuous source and adapter input for fixture presence, flat intervals, clipping and timing around start/stop and keepalive commands. Stereo source channels may already be mixed; do not assume independence. Stop fixture playback temporarily and play a labelled sequence of the five captured inputs for the user. Ask for one grouped listening assessment: recognisable phrase numbers, first/last words and any missing intervals. Record reviewer, files, hashes and observations. Activity or ASR alone cannot pass intelligibility.

If ordinary capture fails in source AND adapter, stop RF progression and investigate the common source/routing boundary from these recordings. If source contains speech but adapter input does not, investigate delivery/channel handling using saved PCM. If only later cycles fail, examine the first failing stop/start/idle sequence. Do not run all gain/selector combinations by default. A one-variable comparison requires a specific finding that makes it useful.

## 3. Only after ordinary capture succeeds: isolate MODEM

Require all five baseline captures to be complete and intelligible before this step. Successful explicit-hand-only capture is not a substitute for the ordinary-selector baseline required here.

Stage `-Action Stage -Case record-then-modem` and require case 28, R1 M1 P0, mono, Keep mixer. UI label: "Ordinary recording then MODEM only 8s". Keep the same USB frequency and acoustic setup. Restart the same fixture playback.

Prepare SDR recording using available documented local application controls if possible. If you cannot automate the SDR, ask the user only to start its recording, then identify the resulting file yourself. Do not change RF drive or attenuation. Confirm the recording is active before arming.

When the user is ready, start a fresh `CaptureWatch`; use 60 seconds if storage permits to allow operator margin (the operator guide's 30 seconds is a shorter alternative). Wait for RECORDING, then ask: "Press Arm, then Run locally for this case. Leave both PTT buttons released."

The case establishes ordinary recording for at least two seconds before adding only MODEM for eight seconds of stored waveform. Monitor it, let source capture finish, collect and assess. Verify matching continuous source coverage from record_baseline_started_monotonic_ms through restoration_completed_monotonic_ms, including the MODEM timestamp. Native capture.wav alone excludes preparation and cannot establish the pre-key baseline. Associate the original SDR recording with this exact case and hash it.

If speech disappears at MODEM, stop and return the isolated transition evidence. Do not launch another unchanged RF attempt or a USB-DIG sweep.

If speech survives, prepare one `live-speech` case with a fresh continuous source watch and SDR recording. Require a separate local Arm then Run. Both physical PTT controls remain released. Collect immediately after completion/restoration. This live case uses a different shared activation sequence: if it loses capture, compare command traces rather than assuming encoder load.

If raw captured speech is intelligible but local decode is poor, replay that exact capture through available pinned fresh/retained/reference processing. If local decode is intelligible but SDR decode is poor, analyze frequency offset, continuity, clipping and filtering using the existing recordings. Work on derived copies; preserve originals. Record the installed FreeDV release and RADE V1/audio settings when using it. Report missing decode tooling as BLOCKED for that analysis, without repeating an RF case. Schedule a concise listening assessment rather than asking the user to locate and convert files.

## 4. Deliver one result

Monitor storage before each batch. Keep recordings bounded and use incremental collection; do not delete original evidence or unrelated radio logs. If storage is insufficient, stop the next case and report the exact dependency. Stop only playback/recording processes you created. Keep keys and private evidence off public services.

Produce one private evidence folder, one ZIP and SHA256 manifest, and one RESULT.md recording: exact installed/helper/fixture identities; acknowledged stages and actual case counts; first failure; source/adapter comparison; control trace and restoration; SDR associations; listening assessments; completed, failed, blocked and inconclusive items; and the next evidence-supported action. COLLECTED means retrieved, not passed. Keep the historical processing-margin failure, independent PTT-release limitation and unqualified RF timing separate.

If a physical action is needed, ask for that action only and wait for it. Otherwise keep doing the authorized computer work yourself. Do not finish by giving the user shell commands to run. Start now with the local helper verification and preflight.
