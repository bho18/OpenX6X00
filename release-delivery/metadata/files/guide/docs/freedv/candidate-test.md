# X6200 native digital development — current operator guide

The next hardware round is **receive-only endpoint and input-availability
mapping**. Native RADE TX and the earlier recorder/gain/MODEM trial sequence are
blocked. This image adds Linux integration and evidence support; it does not
implement or qualify the required X6200 MCU audio/PTT interface.

Use [the integrated qualification protocol](native-half-duplex-qualification.md)
for the evidence contract and later conversation tests. Installation, exact image
identity and release checks are in the development build guide supplied with the
matching image release and its candidate manifest. This helper-only kit does not
contain an image or the private research package. Keep the current working card and do
not change baseband firmware as part of this round.

## Why the old round was withdrawn

The reviewed stock TX DSP sums both ADC channels independently of RECORD; RX
RECORD replaces both ordinary demodulator DAC slots with the ADC sum. MODEM TX
entry also overwrites ADC routing. A zero PGA gain is 0 dB, not mute. These
findings supersede further gain/order/RECORD sweeps as a path to native digital
voice. They do not identify every physical codec net or prove that a Linux-only
change can provide the required simultaneous microphone/RF and receive/speaker
paths.

The physical connector behind the identified MCU input still needs mapping.
Aggregate TX and the admitted/shared PTT shadow do not retain raw physical
release while MODEM remains active. An ordinary I2C STOP refreshes stock host
presence; that mechanism is not an independent native lease or a measured RF
shutdown deadline. Cached command reads and successful transport are not
applied-state acknowledgements.

The Python and Windows helpers therefore reject `Stage` before issuing a GUI
request. `Preflight` inventories available endpoints; it never reports ready to
transmit. Existing evidence may still be collected and assessed.

## Prepare and collect one receive-only inventory

1. Verify this release's image and helper hashes against its manifests, using
   the candidate card and the published reconstruction/installation guide.
   Record the installed GUI, Linux source and baseband identities. An image
   checksum proves file identity, not hardware qualification.
2. Start with both physical PTT controls released. Leave the ordinary radio in
   receive. Do not assert MODEM/software PTT, stage an RF case, alter raw codec
   registers, change gain/order combinations or use a zero gain/line-out/monitor
   setting as a transmit-inhibit substitute. Do not assume the cached VOX
   commands establish an applied inhibit.
3. From the matching Windows kit, run the read-only inventory and retain its
   output and hash:

   ```powershell
   .\Invoke-FreeDVAgent.ps1 -Action Preflight | Tee-Object -FilePath .\receive-inventory.json
   Get-FileHash -Algorithm SHA256 .\receive-inventory.json
   ```

   `INVENTORIED` means the listed commands returned data. It grants no route or
   RF capability. `INVENTORY_INCOMPLETE` names missing observations; preserve
   them as unknown. The inventory includes ALSA/PulseAudio capture and playback
   endpoints, channels, existing mixer values and Linux input devices.
4. Associate the named Linux endpoints and individual channels with measured
   board/codec nets and physical destinations. Use the documented hardware and
   passive receive observations first. Distinguish RF-demodulated input from
   microphone input, host playback destinations from the speaker/headphone/line
   outputs, and actual board nets from Linux channel indices. Do not average
   stereo channels while trying to identify their physical sources.
5. Inventory evdev availability and passively observe the released inputs.
   Record which physical controls can be independently observed, their source
   IDs, freshness and missing release-history semantics. Do not press a stock
   physical PTT solely to create an event trace: a powered edge experiment
   requires independently verified TX inhibition. If that is unavailable,
   leave the connector/edge mapping BLOCKED. Injected Linux input events are
   host tests and cannot identify the physical connector.

Any powered stimulus or control exercise that could key RF waits for an
independently established inhibit and measured RF shutdown. The present
unsupported backend provides neither. Receive mapping must not silently become
an isolated TX experiment.

Return one evidence bundle with identities, the unedited inventory, endpoint
and board-net observations, separately named requested/applied/observed states,
PTT availability, hashes and explicit unknowns. There is no automatic progression
from this inventory to an RF turn.

## Later candidate and end-to-end test

A supported candidate needs a measured endpoint mapping, explicit native RX/TX
routes, real analog-bypass isolation, raw physical PTT with retained release
history, effective applied-state acknowledgements and a native lease that
expires independently of ordinary Linux/I2C activity. Its inhibit must cover
physical, stale software and autonomous TX sources, with independently measured
RF shutdown. The Linux APIs are contracts for that future binding; they are not
new stock wire commands.

Only then use the real retained coordinator with a distinct local Arm → Run
grant for each bounded instrumented TX turn. Qualify live microphone encoding,
RF decoding, analog isolation and automatic turnaround before the ten-turn
conversation described in the integrated protocol. Host simulation, an applied
configuration acknowledgement, decoder sync and an activity meter cannot replace
physical observations or listening evidence.

For existing historical captures, `Collect`, `Assess`, `Status`, `Events` and
`Abort` remain available where the corresponding diagnostic interface exists.
`CaptureWatch` only observes the current source; it does not select a new route.
The virtual-audio `Probe` is host/adapter evidence only. Do not run the withdrawn
recorder, gain, stored-waveform or pre-key trial sequence from older guides.
