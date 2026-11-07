FreeDV operational Beta: interface and idle recovery refinement

FreeDV keeps the normal waterfall, tuning and radio power controls. This revision adds:

- Theme-aware notices and settings, including custom fonts/colors and monochrome styles.
- Whole-value Mic Gain and TX timeout editing: MFK click to edit/apply, either ring to adjust, F5 to cancel an edit.
- Native knob selection and bindings preserved. Volume becomes USB Headphones only while in FreeDV; both knob displays and the status row show confirmed readback.
- Separate saved headphone volume/mute for each USB device. New devices start at 30%; the previously selected device imports its existing level once. Optional quiet volume previews go only to the USB device during idle RX.
- A dedicated microphone/status row that leaves the frequency scale visible.
- Bounded microphone capture recovery that preserves the warm codec and healthy RX. Failed recovery exposes Retry mic; hold it for Audio. Held PTT cannot resume TX after recovery without release and a new press.
- Balanced display blank/wake drawing state and a bounded recent-event incident record.

Download `x6200-usb-rf-windows.zip` for the SD-card image and `usb-rf-test-kit.zip` for its matching optional evidence helper. Follow `START-HERE.md`. Keep the previous `freedv-operational-beta-20261106.1` card as a fallback. The image and kit use startup contract 4 and output/capture-opening/refinement contracts 1.

Stock baseband is unchanged. This Beta uses half-duplex RADE V1, a USB microphone/headphone device in HOST, and the radio's panel PTT. Native mic/speaker and hand-mic PTT remain unsupported.

Software validation passed: 71 host test suites, all five application build variants, 174 controller executions (87 cases repeated twice), and all three required GitHub CI jobs on source de6c186232b40172421f77e3883e7cb8ae917003. Real-codec collector/decoder smoke, ARM/QEMU processing, image ABI/loader/filesystem and signing-key preservation checks passed. All prior failed attempts remain retained; no physical radio was accessed for this validation.

Physical idle/wake recovery and live RF receive remain field checks. Earlier operator recording, codec, headset playback and RF transmit milestones remain accepted; this release does not ask you to repeat those component tests. Use the guide's combined UI/knob/idle check alongside a known working RADE V1 peer and alternating turns.
