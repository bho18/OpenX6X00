# FreeDV PTT evaluation

Reviewed 2026-10-29 against diagnostic source
`21df019884da3e8cdc1b4521a4b4f8a51ca210e1`, pinned X6200Control
`0ee1ae3d4d9d74cbac7e349147ebc440eed8fad8`, Buildroot `8b7ca9d6`,
and the returned baseband 1.0.7 evidence. This is a design review, not a new
hardware result or a change to the published diagnostic image.

## Finding

PTT must request the complete FreeDV transmit session. It must not key ordinary
analog microphone audio and then start the encoder afterward. Successful
microphone capture, software-owned RF and Stop tests do not establish physical
PTT operation. **Ordinary hand-mic PTT driving FreeDV remains INCONCLUSIVE.**

USB/USB-DIG can remain the underlying RF modulation for RADE. The requirement is
that only the generated modem waveform reaches that modulator, with the analog
microphone bypass excluded. An AM selection must not turn a FreeDV PTT request
into an ordinary AM transmission.

| Input | Inspected behavior | Evaluation |
|---|---|---|
| Radio side PTT | Matrix key `BTN_TRIGGER_HAPPY4` becomes `KEYPAD_PTT`; the main screen calls `radio_set_ptt()` directly on press/release | A concrete software interception point exists. Replace direct keying with session intents when FreeDV owns the mode; verify no parallel hardware bypass |
| Hand-mic PTT | Upstream explicitly describes direct baseband keying. The inspected microphone key table has no PTT mapping | Cannot assume Linux can intercept it or that clearing software PTT overrides it |
| Packet TX flag | Backend copies baseband `flag.tx`; the radio changes state and notifies the GUI after observing it | A result to monitor, not an independent physical button request or advance warning |
| Diagnostic Arm/Run | Locally starts a bounded software-owned case; route application precedes audio start | Useful diagnostic control, not a production hand-mic PTT binding |
| CAT/TCI/browser keying | Separate callers can request ordinary PTT | While FreeDV owns the mode, reject unsupported callers or route explicitly supported requests through the same session controller; never allow an analog bypass |

The pinned device tree confirms the side key's Linux matrix mapping, a 25 ms
debounce and no autorepeat. It does not establish a separate hand-mic PTT input.
No independent raw hand-mic press/release API or documented selective PTT-inhibit
API was found in the inspected control library. This is a limit of the inspected
interface, not proof that the hardware lacks either capability.

Two consequences are important:

- Starting an encoder on a packet TX rising edge is too late to guarantee no
  initial analog speech. Packets usually arrive around every 35 ms, and encoder
  buffering adds further delay. No fixed latency bound follows from that average.
- MODEM itself keys RF in the measured routes. Once software holds TX for the
  digital waveform, a physical release need not produce a TX falling edge.
  Inferring hand-mic release from aggregate TX status cannot control a reliable
  end-of-over tail or distinguish an unexpected transmitter from a button press.

The register name `micsel_pttmode_chge_spmode_auxiqgen_sqlthr` contains `pttmode`,
but supplies no documented PTT-inhibit semantics or setter. Do not guess at its
unmapped bits. Minimum mic gain, zero requested power and software PTT=false are
not established substitutes for independently proven analog isolation/RF inhibit.
Mic gain may also affect the samples needed by the encoder.

## Required production behavior

1. Enter FreeDV only with fresh RX and all supported PTT inputs released. Reserve
   audio/radio ownership for the mode's lifetime, select its qualified USB route
   and filters, disable autonomous VOX and prepare codec contexts while unkeyed.
   Refuse incompatible mode changes and competing keyers. The independent live
   receive/speaker route remains a separate unresolved prerequisite.
2. Establish a measured no-analog-bypass condition **before a physical press can
   key RF**. Prefer a documented means to make the button a request without
   autonomous baseband keying. Otherwise prove a preconfigured unkeyed digital
   route that remains analog-free when physical PTT keys. `R1 M0 P0` is a candidate
   to investigate, not a qualified physical-PTT contract. Never pre-enable MODEM
   as an idle precaution: it already keys this radio.
3. Deliver independent, timestamped PTT edges to a session controller outside
   page/focus and GUI scheduling dependencies. Maintain input identity, current
   level, sequence and generation. A button can work with the hand mic selected;
   do not silently switch microphones because the side key historically serves
   the built-in mic in Walkie mode. Handle wake, repeat, debounce and lost input.
4. On press, start microphone intake/encoding, accumulate bounded output and key
   the qualified generated-audio route when ready. If capture is only possible
   after keying, that alternative needs an already proven analog-free startup
   interval, preserved mic capture and a bounded readiness timeout. Silence or a
   codec-supported preamble can bridge startup only on that proven route.
5. On release, stop speech intake, finish the intended bounded EOO/tail and drain
   only accounted audio before unkeying. Independent release sensing must still
   work while software holds MODEM/PTT for that tail. A release before readiness
   cancels the pending transmission; it must not cause a delayed key-up.
6. On Stop, audio failure, stale status or protection trip, fence output, flush
   and clear software keys immediately; do not wait for an EOO. Preserve an
   analog-free fault state while physical PTT is held. Where autonomous physical
   keying cannot be inhibited, software cannot promise RF cessation: require
   release and report that limitation. Require release and a new press after a
   fault rather than automatically rekeying.
7. Return to the qualified FreeDV receive state between turns. Restore ordinary
   analog microphone routing only when leaving FreeDV, after safe release and
   fresh RX. Restoring analog after each digital turn would expose the next
   physical press to the same bypass problem.

These changes need a mode-level session controller rather than remapping the
button to the diagnostic Run control. Diagnostic local Arm/Run requirements and
ten-second software limits remain in force for diagnostic cases.

## Remaining evaluation and compact validation

First inspect input devices, documented baseband controls and raw packet fields
read-only for an independent hand-mic button level. The defined protocol exposes
TX and other mic keys, but no named PTT level. Unknown packet bits are hypotheses,
not a validated control interface. Verify any supported inhibit preserves both
button observations and the microphone capture needed for FreeDV.

Then use one focused, instrumented session alongside the next native/PTT work,
without repeating the standalone codec batch. This is a coverage specification,
not instructions to use physical PTT with the current TX-delivery checklist:

| Short case | Evidence required |
|---|---|
| Side and hand PTT press/hold/release separately | Independent button edges, commanded route, transport outcome, fresh packet TX, audio generation and SDR WAV/IQ; ability to sense release while MODEM remains held |
| Rapid press/release during preparation; consecutive turns | No late key-up, duplicate start, stale output or incorrect microphone selection; intelligible first/last words and bounded EOO |
| Encoder not ready or failed; unexpected external keying | No ordinary mic audio at RF; honest distinction between software unkeying and hardware-held TX |
| Stop while each physical button is held | Generated audio cancelled promptly, no analog restoration while held, clear release prompt and exact restoration after fresh RX |
| Mode exit/change and controlled service restart, released then held | No automatic resumed TX or analog fallback during restart; account for the interval before the startup guard runs |

Use the dummy load, requested 1 W and short locally armed cases. Keep held-PTT
tests separate from the ten-second software-owned limit: software cannot enforce
that limit against an unqualified autonomous hardware key. Operator release and
the existing service-based recovery procedure remain necessary. Keep SDR IQ on
the PC, associate case IDs/hashes and export once. Correlate external RF timing
with stated uncertainty; unsynchronized timestamps do not prove the 500 ms gate.

Process-loss behavior needs its own evidence. The diagnostic startup guard waits
for fresh RX before restoring analog routing, but cannot guarantee what the
baseband emits while the application is absent or before that guard executes.
Any stronger fail-closed claim requires a proven hardware/baseband mechanism.

## Decision

The side PTT has a clear implementation path, pending integration and hardware
qualification. Normal hand-mic PTT support is conditional on independent button
observation and a proven no-analog-bypass contract. These are explicit product
acceptance gates, not assumptions justified by working microphone encoding.

If supported native controls cannot meet them, document the tested alternatives
and vendor uncertainties before proposing a changed PTT experience or any
hardware/baseband modification. Do not silently substitute a screen button or
declare the hand-mic PTT supported. This review leaves the `.2` diagnostic image
and its focused TX-delivery procedure unchanged; no redeployment is required for
the review itself. No upstream submission or baseband modification is made.

## Inspected sources

- [Side-key mapping](https://github.com/bho18/OpenX6X00/blob/21df019884da3e8cdc1b4521a4b4f8a51ca210e1/src/front_keys.c#L44), [direct PTT handler](https://github.com/bho18/OpenX6X00/blob/21df019884da3e8cdc1b4521a4b4f8a51ca210e1/src/screen_main.c#L681) and [mic key table](https://github.com/bho18/OpenX6X00/blob/21df019884da3e8cdc1b4521a4b4f8a51ca210e1/src/mic_keys.c#L32).
- [Packet TX mapping](https://github.com/bho18/OpenX6X00/blob/21df019884da3e8cdc1b4521a4b4f8a51ca210e1/src/hw/x6200/hw_x6200.c#L454) and [RX/TX notification](https://github.com/bho18/OpenX6X00/blob/21df019884da3e8cdc1b4521a4b4f8a51ca210e1/src/radio.c#L1500).
- [Upstream manual on autonomous PTT](https://github.com/bho18/OpenX6X00/blob/21df019884da3e8cdc1b4521a4b4f8a51ca210e1/docs/MANUAL.md#L818) and [direct baseband connection](https://github.com/bho18/OpenX6X00/blob/21df019884da3e8cdc1b4521a4b4f8a51ca210e1/docs/MANUAL.md#L957).
- [Pinned matrix device tree](https://github.com/gdyuldin/AetherX6200Buildroot/blob/8b7ca9d6d4e82ade0fc7654c46b9f8688689a4a3/br2_external/board/X6200/dts/sun8i-r16-x6200.dts#L122), [packet definition](https://github.com/gdyuldin/X6200Control/blob/0ee1ae3d4d9d74cbac7e349147ebc440eed8fad8/include/aether_radio/x6200_control/low/flow.h#L43), [software PTT setter](https://github.com/gdyuldin/X6200Control/blob/0ee1ae3d4d9d74cbac7e349147ebc440eed8fad8/src/control.c#L156) and [register definitions](https://github.com/gdyuldin/X6200Control/blob/0ee1ae3d4d9d74cbac7e349147ebc440eed8fad8/include/aether_radio/x6200_control/low/control.h#L40).
- [Current diagnostic start ordering](https://github.com/bho18/OpenX6X00/blob/21df019884da3e8cdc1b4521a4b4f8a51ca210e1/src/freedv/diagnostics.c#L215), [clear/restore guard](https://github.com/bho18/OpenX6X00/blob/21df019884da3e8cdc1b4521a4b4f8a51ca210e1/src/radio.c#L1867), [startup guard](https://github.com/bho18/OpenX6X00/blob/21df019884da3e8cdc1b4521a4b4f8a51ca210e1/src/radio.c#L1960) and [remaining physical-PTT procedure](native-session-2.md#physical-ptt-and-controlled-restart).
