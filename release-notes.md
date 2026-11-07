X6200 FreeDV operational Beta: live microphone gain and simpler exit

Microphone gain now changes immediately as you turn either editing dial, so the speech meter shows the current gain. Center-click saves it. Cancel, closing the popup or leaving without saving restores the previous value. Timeout editing retains its existing apply/cancel behavior.

Exit, GEN, Apps and other navigation keep the normal coordinated cleanup. The transient Stop now action is removed; repeated Exit presses are consumed while cleanup finishes. Internal protection and terminal shutdown handling remain bounded.

USB audio and the on-radio PTT remain required. Stock baseband is unchanged. Previously accepted recording, codec, playback and RF-transmit milestones need no repeat. Actual headset idle behavior and live peer reception remain field validation.

Validation passed on source 9688d21cbf360ad14acf059e7df304b5dba9a1ec: all five build variants, 71 host tests, 208 controller executions (104 scenarios repeated twice), all three Windows/Linux/controller CI jobs, production collector/decoder checks, GUI smoke, ARM emulation and actual-image ABI/filesystem/signing-key/boot-partition checks. The repeated campaign also passed its independent host timing probe. Preview validation checks separate first-use/reused-stream windows and complete leading/trailing ramps. Earlier failed runs are retained as historical evidence; no timing or waveform limits were relaxed.

Download x6200-usb-rf-windows.zip for the image, and usb-rf-test-kit.zip for its matching optional read-only evidence collector. START-HERE.md contains installation and focused checks. Keep the previous card as a fallback.
