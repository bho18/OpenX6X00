# Draft: on-radio FreeDV voice for X6200

This draft is for review; it has not been submitted upstream.

OpenX6X00 already supports digital data modes. The proposed feature adds a
native FreeDV voice session that takes speech from a radio microphone, encodes
and transmits a FreeDV waveform, and decodes received speech to the radio speaker.
It would reuse the existing app framework, radio control and transmit protections.

The first milestone is to test the X6200's simultaneous audio routes and measure
RADE V1 and 700E processing on the device. Hand-mic PTT can be handled by the
baseband directly, so PTT and analog-speech leakage require hardware evidence.
The UI, activity-frequency markers, dependencies and support for other radios
should be discussed after those findings are available.

Questions for maintainers: preferred audio-session abstraction and codec
packaging; desired waveform and X6100 scope; placement in the existing UI; and
how best to contribute public tests and hardware results. No target-radio test
results are claimed by this draft.
