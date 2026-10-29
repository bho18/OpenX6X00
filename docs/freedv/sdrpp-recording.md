# SDR++ recording setup for the X6200 tests

Supplement to [Session 1](native-session-1.md). Use the diagnostic image already
on the radio. The receiver model is not yet specified, so RF gain, input attenuation
and hardware sample rate remain device-specific. This setup requires no virtual
audio cable or additional decoder on the PC.

## Set up the USB receiver

1. Connect the X6200 to its dummy load and keep the hand-mic PTT released. Keep
   the SDR receiving the radio through the existing test arrangement. Do not
   connect a transmitter output directly to an SDR input.
2. In SDR++, start the source, select the receiver/VFO you will record and choose
   **USB**. Tune that VFO to exactly the X6200's displayed USB frequency.
3. Use approximately a **3 kHz USB passband**, containing the whole modem signal.
   Do not narrow it to the 735 Hz routing-test beep. Keep the USB signal inside
   the source's sampled bandwidth and leave tuning/filter settings unchanged
   during each recording.
4. Turn off **squelch**, audio noise reduction and notch processing wherever the
   installed build exposes them. Do not adjust levels during a test. Use a short
   existing tone-routing case to check reception and avoid SDR overload/clipping;
   note source gain/attenuation and any AGC settings rather than guessing a value.

## Create two recorder instances

Each Recorder instance selects either Audio or Baseband. To obtain both at once:

1. Open **Module Manager** in SDR++'s left sidebar.
2. In its add-module row, enter a unique name such as **FreeDV Audio**, choose
   module type **recorder**, and press **+**.
3. Repeat with **FreeDV IQ**, also type **recorder**.
4. Open the two new recorder panels in the sidebar. An existing recorder may be
   used for one of these instead. Do not delete an existing configured instance.

Current upstream SDR++ permits multiple recorder instances. Labels and available
format options may differ in older builds; use the matching Audio/Baseband
choices. If recorder is absent from Module Manager, record the SDR++ version and
that limitation before changing software.

## Configure FreeDV Audio

- Select **Audio**.
- Set its recording folder to the test kit's prepared `evidence` folder, or copy
  the completed files there afterward.
- Where available, choose **Container: WAV** and **Sample type: Int16**.
- Set **Stream** to the USB receiver/VFO selected above, often named Radio.
- Turn **Stereo OFF** so the demodulated file is mono.
- Turn **Ignore silence OFF**. This option discards quiet sample blocks and would
  shorten recorded gaps and alter the audio timeline.
- Set recorder volume to a clear, non-clipping level and leave it fixed. Check
  its meter while receiving the tone. SDR RF overload must be corrected at the
  source rather than hidden by reducing recorder volume.

The audio WAV uses the selected stream's sample rate, commonly 48 kHz. There is
no need to force 8 kHz in SDR++; the pinned reference workflow converts it later.

## Configure FreeDV IQ

- Select **Baseband**.
- Choose a separate folder on the PC for the large IQ files, outside `evidence`.
- Where available, choose **Container: WAV**, **Sample type: Int16**.
- Keep the hardware sample rate at a supported setting with the full signal and
  adjacent noise visible. Record the actual sample rate and center frequency.

Baseband WAV stores **two channels representing I and Q**. It is not demodulated
stereo speech: do not downmix it or supply it as the USB audio WAV for decoding.
Baseband uses the IQ-front-end rate, independently of the audio recorder rate.

At a recorded rate of 1 MS/s, Int16 IQ uses approximately 4 MB/s (40 MB for ten
seconds). Prefer short per-case IQ recordings or stopped segments within the
session; keep large recordings on the PC. Ordinary WAV also has a roughly 4 GiB
size limit, so do not run a high-rate recorder unattended for the whole session.

## Record each routing/recovery or RADE TX case

1. With the radio still unkeyed, press **Record** in both SDR++ recorder panels.
2. Confirm both display Recording and their duration counters advance.
3. Wait approximately one second, then use the radio's local **Arm -> Run**.
4. Follow the case instructions. For live RADE TX, speak the numbered phrase
   immediately. Digital modem sound in SDR++ is expected; it is not ordinary
   analog speech.
5. After RF ends, leave about one second of trailing recording, then press
   **Stop** in both SDR++ panels. These are recorder controls, not radio PTT.
6. Note the radio case number, turn/test label and the two generated filenames.
   If useful, rename stopped recordings to `turn-01-audio.wav` and
   `turn-01-iq.wav`; never rename an actively written file.
7. Keep the mono audio WAV in `evidence`. Keep IQ on the PC and record its path,
   sample rate, format, center frequency and SHA256 in notes or recordings.json.

Listen to the short tone test's audio file before committing to the ten turns.
It should contain the received tone and the pre/post quiet interval, not the PC
microphone or another VFO. Confirm the baseband file has data as well. Neither
recording should show a Paused indication or unexplained sample gaps.

## RF stopping evidence

Audio alone cannot prove RF-off: USB AGC, filters, buffering and audio tails can
change what is heard after RF stops. Retain IQ for RF-envelope analysis. If also
filming the radio Stop action and SDR++ spectrum together, use a single video;
SDR++ display latency and frame uncertainty must remain part of the timing bound.
The two recorders do not establish a common timebase with the radio's exported
monotonic clock. Leave the exact 500 ms gate INCONCLUSIVE unless correlation and
the conservative bound support it.

## Reference and version limits

Recorder controls, multiple instances, source sample rates, channels and silence
handling were checked against upstream SDR++ commit
`f10fb98af8b80de01de94e60c17608e4281e2515`:
[recorder implementation](https://github.com/AlexandreRouma/SDRPlusPlus/blob/f10fb98af8b80de01de94e60c17608e4281e2515/misc_modules/recorder/src/main.cpp)
and [Module Manager](https://github.com/AlexandreRouma/SDRPlusPlus/blob/f10fb98af8b80de01de94e60c17608e4281e2515/core/src/gui/menus/module_manager.cpp).
This is documentation/source inspection, not a test of the operator's installed
SDR++ version or receiver hardware.
