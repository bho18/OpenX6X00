# Follow-up audio evidence changes

This source update is for the later native-routing session. It does not replace
the currently installed `2083e3e7` image or require a reflash for Session 1.
Do not copy a development executable onto the radio. A deployment still requires
the paired-image workflow, source-matching checks and installed-image verification.

The previous radio export counted one underflow per case but did not timestamp
it. Add bounded underflow events carrying monotonic time, accepted samples and
cancellation status, plus first accepted-write timing and before/after-write
counts. Sixteen events are retained and overflow is counted. Recording does not
allocate or write files in the PulseAudio callback. An event after the first
write can still be during initial buffering; it is not automatically a steady-state
failure. Compare timing against case start, prebuffer and output continuity.

The current image's first-four-second WAVs can miss later RF program or phrase
changes. The follow-up records full short route/TX/capture cases and 20-second
tone/interruption cases, while 120-second cases retain bounded four-second
excerpts. The configured limit is exported. Existing callers with zero excerpt
configuration retain the old four-second default. Explicit requests above twenty
seconds or above case duration are rejected.

Before preparation, reserve projected WAV bytes plus metadata within the existing
64 MiB session cap and preserve the 64 MiB free-space reserve. Recording queues
remain bounded, writes remain outside callbacks, and no files are deleted for
space. Software RF limits, local Arm/Run, cancellation and fresh-RX restoration
are unchanged. Old exports remain readable by the new evidence tools.
