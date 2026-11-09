# FreeDV compatibility hardening — 2026-11-09

These application changes improve the USB-headset half-duplex Beta and its
return to ordinary radio operation. They do not change the Linux/SDK baseline,
stock baseband firmware or supported audio topology. Use the image and helper
kit from the same GitHub release, and retain the previous working SD card as
the fallback. Source changes alone do not identify a qualified image.

The operating path remains USB microphone/headphones, RADE V1 and the radio's
panel PTT. Built-in and hand microphones, hand-mic PTT and the radio speaker
remain outside the current FreeDV audio path. Native Bluetooth microphone plus
headphones is a separate capability investigation, not a disqualified option.
See the [operating guide](usb-rf-end-to-end.md) for normal use.

## Controls and input ownership

Gain and timeout use a native LVGL whole-value control rather than a private
digit editor. Navigate with MFK, click to edit, turn to change the complete
value, then click to confirm. Microphone gain previews immediately in the
speech meter; Cancel edit/F5 restores the previous gain. Timeout changes take
effect only after confirmation. A failed confirmation stays in edit mode with
an explanation. Closing or defocusing an unfinished edit cancels its draft.
Widgets follow the selected application theme.

The normal knob selection/edit model remains intact. With Volume selected,
FreeDV adjusts the selected USB headset; selecting Power or another control
continues to operate that control. Ordinary speaker volume remains separate.

Mode entry and exit reset buffered input through the input producer's own
acknowledgement. Old detents, half-presses and remote events cannot become new
local input after ownership changes. Entry requires released controls; a failed
entry does not grant FreeDV ownership. An unfinished exit reset retains the
fence rather than reopening ordinary writers. This reset is coordinated without
blocking the GUI on the main-knob reader.

## Remote control responses

Queries remain available while FreeDV owns the radio. Competing settings and
TX commands are refused; remote PTT does not acquire authority over a FreeDV
transmission.

| Interface | Result of a refused mutation |
| --- | --- |
| CAT | Existing CI-V NG code `0xFA`, including refused PTT ON and OFF. Existing successful replies remain unchanged. |
| Local configurator socket | `{"t":"err","err":"..."}` for ownership, stale queued requests, shutdown, queue refusal or immediate application failure. |
| Web configurator | Existing HTTP 409 policy while FreeDV owns settings. Read requests remain available; the policy continues to cover all mutating web requests. |

CAT and configurator writes check admission on the GUI thread when they run,
and retain the writer scope through the complete mutation. A queued request
whose input generation changed is rejected even if FreeDV has already exited.
The configurator returns `ok` after GUI execution, not merely after enqueueing.
For Ethernet, `ok` means NetworkManager accepted the asynchronous save/apply
request; it does not confirm activation. None of these replies is independent
MCU application acknowledgement or an RF measurement.

## Ordinary radio policy after FreeDV

Ordinary observers and USB restoration share the same effective AGC/time
policy. AUTO in CW/CW-R with the decoder enabled sends the upstream FAST
setting while retaining the user's saved AUTO preference. Both VFOs are
restored consistently. This fixes application policy duplication; the analyzed
X6200 baseband maps its FAST/AUTO requests to the same fast preset, so this is
not evidence of a newly demonstrated RF defect or improvement.

WR12 notifications retain their final state across exclusive ownership and
queue coalescing. Reconciliation reapplies the requested power through all
existing digital, tune, scan, transverter, training, band and global-inhibit
limits even when the cached band-hold Boolean did not change. It does not
rewrite the saved power setting. During USB ownership, an actual external
WR12 block is retained as a protection event even if the live block disappears
before the audio coordinator polls. It inhibits new keying and requests the
existing fault/clear path; ordinary settings are restored after the required
RX observation. These are software-policy changes, not measured RF guarantees.

## Audio handover and shutdown

Entry and exit require completion callbacks for the ordinary PulseAudio
playback stream's cork/flush operation. A queued operation is not treated as
completed. One worker handles these operations; bounded recovery retries do
not replace or free a worker that remains alive.

If ordinary audio is lost or cannot be restored, the mode may request one GUI
restart after USB streams have stopped, ordinary radio restoration has
completed and fresh RX is observed. The existing daemon restart path recreates
ordinary audio. This does not restart a transmission or silently re-enter
FreeDV. If RX cannot be established, the restart is not authorized by a timeout.

SIGTERM/SIGINT first close admission to new mode entries and transmissions,
then give an active FreeDV session up to five seconds to finish graceful
cleanup. Repeated signals do not extend that deadline. Expiry requests abort,
performs the bounded WR12 bypass attempt and terminates with the original
signal. The deadline flag also reaches the active USB control observer if the
GUI is stalled. It does not certify completed cleanup or RF cessation. The
signal handler only publishes lock-free state and wakes the coordinator; it
does not perform radio, audio or filesystem cleanup itself.

The native Bluetooth hold pauses only the managed raw-radio RX-to-A2DP
loopbacks during USB FreeDV. It verifies the monitor identity and absence of
those routes, then releases the lease and reconciles the previous routing on
exit. It does not change Bluetooth pairing, user preferences or headset
profiles. The existing A2DP/HFP policy is not expanded or used as proof that
X6200 native Bluetooth microphone operation is impossible.

## Preferences and diagnostic capture

Existing microphone gain, headset identity, per-device volume/mute,
acknowledgement and timeout preferences are preserved. This hardening adds no
new numeric migration or speculative action-ID remap. Completed named migration
markers remain authoritative: an old origin value does not repeat a completed
reset, bookmark seed or action bridge.

The earlier integration compatibility limits still apply. Action 126 belongs
to FreeDV in this fork and Propagation uses 128; ambiguous version-14 bindings
retain their value. The existing first-upgrade version-14 TX-policy bridge runs
once. Returning the same settings database to official upstream can reinterpret
bindings, and its older web editor does not recognize FreeDV bookmark type 6.
Retain the original settings/card backup and review bindings when switching
firmware lineages.

Detailed session capture defaults to off and is latched at mode entry.
Ordinary operation retains bounded summaries and fault information without
microphone PCM taps or verbose event histories. Detailed capture is an explicit
diagnostic opt-in under **Settings → Info → FREEDV DIAGNOSTICS → Detailed
session capture** in the USB voice build. The engineering radio-test panel
remains available only in diagnostic builds. Capture is not needed for startup
or TX readiness. Enable it before entering the mode when a particular incident
needs audio evidence, then review recordings before sharing them.

## Verification boundaries

Focused host fixtures exercise production handlers and policy with synthetic
endpoints: CAT/configurator replies and queue races, input fencing, whole-value
editing, both-VFO AGC restoration, coalesced WR12 protection pulses, completed
migration ledgers, PulseAudio handover and process lifecycle deadlines.

Image builds, the full codec/audio matrix and release verification must still
bind the final source and release manifest. These fixtures do not validate
physical knob wiring, headset idle behavior, native Bluetooth microphone
support, antenna-level stop timing or a live two-way QSO. Earlier recording,
codec, playback and RF-transmit observations retain their original scope.
Live receive with a known working RADE V1 peer and alternating replies remains
the outstanding field milestone; ordinary startup does not repeat component
tests or require manual TX enablement.
