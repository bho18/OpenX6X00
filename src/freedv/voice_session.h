// SPDX-License-Identifier: LGPL-2.1-or-later
#pragma once
#include "qualified_profile.h"
#include <stdatomic.h>
#include <stddef.h>
#include <stdint.h>
typedef struct fd_voice_session fd_voice_session;
#include "voice_types.h"
/* Driver calls run on the retained coordinator thread except emergency_clear.
 * emergency_clear is an independent, bounded software-TX fence. It must be safe
 * concurrently with every other call. The driver serializes all potential key
 * writes against cancel and never keys after the fence. No GUI/filesystem joins
 * belong in that path. fence waits for callbacks before resetting generations.
 * restore may touch only owned overrides, never saved frequency/active VFO. */
typedef struct {
    void *context;
    bool (*prepare)(void *, const fd_qualified_profile *, const atomic_bool *cancel);
    bool (*fence)(void *);
    bool (*receive)(void *, uint64_t generation);
    bool (*transmit)(void *, uint64_t generation, const atomic_bool *cancel);
    bool (*finish)(void *, uint64_t generation);
    int (*drained)(void *); /* 1 actual engine+device completion, 0 pending, -1 error */
    void (*emergency_clear)(void *);
    bool (*retune)(void *, fd_voice_tuning *, const fd_qualified_profile *);
    bool (*restore)(void *);
    bool (*close)(void *);
    bool (*observe)(void *, fd_voice_observation *, bool *sync, bool *mic);
} fd_voice_driver;
fd_voice_session *fd_session_create(const fd_voice_driver *);
bool fd_session_enter(fd_voice_session *, const fd_qualified_profile *, const char *hardware,
        const char *build_id, fd_voice_tuning, unsigned timeout_seconds);
bool fd_session_ptt(fd_voice_session *, fd_ptt_source, bool pressed, uint64_t sequence);
bool fd_session_retune(fd_voice_session *, fd_voice_tuning);
/* Preference changes are accepted only in stable receive. */
bool fd_session_timeout(fd_voice_session *, unsigned timeout_seconds);
void fd_session_abort(fd_voice_session *, fd_abort_reason);
void fd_session_leave(fd_voice_session *);
fd_voice_status fd_session_observe(fd_voice_session *);
/* Destroy only after observe returns OFF. Never hides held/unknown PTT. */
bool fd_session_destroy(fd_voice_session *);
const char *fd_voice_state_name(fd_voice_status);
