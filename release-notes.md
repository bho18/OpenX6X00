X6200 FreeDV operational Beta: audio hardening

Headphone adjustments retain fast dial input across rounded readback and transient errors, use native 0–100 display units, and save confirmed preferences in the background. The optional preview is a quiet half-second tone with independent bounded recovery. The microphone meter distinguishes near-maximum peaks from overload during RX and TX.

Mic gain uses the existing click/dial editor without extra buttons. Audio selection applies on confirmation, dropdowns follow native styling, and recovery actions appear only when needed. Brief RX radio-status interruptions can recover while the codec remains warm; TX status loss, protection, unexpected TX and unsuccessful clearing remain stop conditions.

The image uses unchanged stock baseband, a paired USB capture/playback device and on-radio PTT, with normal tuning/waterfall/power and both knob workflows preserved. Accepted recording/codec/playback/RF-transmit milestones need no repeat. Actual headset behavior and live peer reception remain field validation.

Validation: all five build variants, 71 host tests, 208 controller executions (104 scenarios repeated twice), Windows/Linux helper CI, production collector/decoder checks, ARM emulation and actual-image ABI/filesystem/key checks passed on source 20cf0e65fecb6459fc8c7d6d40d65b514b82a11c. The repeated local campaign also passed an independent host timing probe. Earlier failed attempts remain recorded; the cause of the earlier host interruption is unproven.

Download x6200-usb-rf-windows.zip for the image and usb-rf-test-kit.zip for its matching optional read-only evidence collector. START-HERE.md contains installation and focused field checks. Keep the prior card as a fallback.
