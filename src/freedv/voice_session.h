// SPDX-License-Identifier: LGPL-2.1-or-later
#pragma once
#include "qualified_profile.h"
#include <stdatomic.h>
#include <stddef.h>
#include <stdint.h>
typedef struct fd_voice_session fd_voice_session;
#include "voice_types.h"
enum { FD_VALIDATION_ABI = 1, FD_VALIDATION_MAX_TURN_SECONDS = 60,
       FD_VALIDATION_GRANT_MS = 15000 };
/* Produced by the native hardware binding, never by remote/UI profile input.
 * Capability observation and candidate identity are independent of production
 * qualification. The driver independently rechecks this admission on claim. */
typedef struct {
    unsigned abi_version;
    const char *hardware, *build_id, *baseband, *control_version;
    unsigned independent_ptt_sources;
    bool keying_owned;
    unsigned max_turn_seconds;
} fd_validation_admission;
bool fd_validation_admission_valid(const fd_qualified_profile *, const fd_validation_admission *);
/* Driver calls run on the retained coordinator thread except emergency_clear
 * and observe. Observe may also run at the local Arm/Run boundary; calls to
 * observe are serialized by the session, but must tolerate other driver calls.
 * Arm/Run require each physical source's sample sequence to advance, with its
 * timestamp at or after the request; cached released state cannot grant TX.
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
    /* Optional typed startup rejection. A released PTT is recoverable and
     * must not permanently mark otherwise healthy audio streams unusable. */
    fd_abort_reason (*startup_abort_reason)(void *);
    /* Capture the admitted physical edge/release counters before a possibly
     * blocking fence. Transmit must retain this generation's baseline. */
    bool (*begin_transmit)(void *, uint64_t generation, const fd_voice_observation *admission);
} fd_voice_driver;
fd_voice_session *fd_session_create(const fd_voice_driver *);
bool fd_session_enter(fd_voice_session *, const fd_qualified_profile *, const char *hardware,
        const char *build_id, fd_voice_tuning, unsigned timeout_seconds);
bool fd_session_enter_validation(fd_voice_session *, const fd_qualified_profile *,
        const fd_validation_admission *, fd_voice_tuning, unsigned timeout_seconds);
/* Caller must be the local GUI/input boundary. Each Arm -> Run grant admits
 * exactly one subsequent fresh physical press, and expires after 15 seconds. */
bool fd_session_validation_arm(fd_voice_session *);
bool fd_session_validation_run(fd_voice_session *);
void fd_session_validation_disarm(fd_voice_session *);
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
