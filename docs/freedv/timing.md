# Independent event-to-RF-off timing

The older `native_session.py` timing helper records supplied intervals and
uncertainty; it does not validate either. Software command timestamps alone
cannot prove RF cessation. Physical held PTT is a separate measurement from
software-owned transmission.

First validate the actual setup on the equipment and retain the setup report.
Define the initiating event precisely (physical Stop activation, timeout trigger,
or injected protection/failure) and the RF-off threshold. Record continuous IQ
and the event with correlated video only if a common observable reference
establishes a conservative bound across the recording paths. Record frame and
sample resolutions, start offsets, capture/buffering delays, alignment method,
threshold/noise-floor choice, all delay bounds and uncertainty. Do not treat
unsynchronized file timestamps as alignment.

If video/IQ cannot bound the entire event-to-RF interval, use synchronized trigger
and RF-envelope instrumentation. Verify trigger delay, envelope detector
response, sample clock/resolution and threshold on that hardware.

The accepted result is `interval_ms + uncertainty_ms <= 500`. Include all
uncertainty contributions conservatively. The report identifies the event/RF
artifacts and setup-validation hashes, method, reviewer and physically-held-PTT
classification. Missing correlation, setup validation, recordings or uncertainty
basis is INCONCLUSIVE. Missing equipment is BLOCKED. A bound above 500 ms is
FAIL. Repeat software-owned Stop, timeout and protection scenarios independently
from held-PTT observations. Never defeat held physical PTT merely to obtain a
software timing result.

The machine assessment also requires a verified setup report bound by SHA256,
the event and RF artifact hashes from this case, and nonnegative
`uncertainty_components_ms`. Their sum cannot exceed the declared uncertainty.
