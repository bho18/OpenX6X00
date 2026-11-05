// SPDX-License-Identifier: LGPL-2.1-or-later
#pragma once
#include "voice_session.h"
#include "mode.h"
/* radio.c owns all baseband writes and protections. Settings remain ordinary
 * preferences; profile overrides are applied to hardware only while owned. */
fd_voice_driver radio_voice_driver(void);
/* GUI entry requires an applied native QUIET/lease acknowledgement before
 * asynchronous codec preparation; neither cache echoes nor gain 0 establish it. */
bool radio_voice_claim(const fd_qualified_profile *);
bool radio_voice_supported(const fd_qualified_profile *);
bool radio_voice_control_recovery_required(void);
const fd_qualified_profile *radio_voice_validation_profile(void);
bool radio_voice_validation_status(const fd_qualified_profile *, fd_validation_admission *, const char **reason);
bool radio_voice_claim_validation(const fd_qualified_profile *, const fd_validation_admission *);
bool radio_voice_tuning_request(fd_local_operation, int32_t, fd_voice_tuning *);
bool radio_voice_volume(int32_t delta);
bool radio_voice_local_ptt(bool pressed, uint64_t *sequence);
void radio_voice_observation(fd_voice_observation *);
bool radio_voice_audio_stats(fd_voice_audio_snapshot *);
