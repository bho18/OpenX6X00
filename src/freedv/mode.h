// SPDX-License-Identifier: LGPL-2.1-or-later
#pragma once
#include <stdbool.h>
#include <stdint.h>
#ifdef __cplusplus
extern "C" {
#endif
/* The UI submits local receive transactions; remote callers are never granted
 * an owned write scope. No frequency or VFO changes happen before acceptance. */
#include "voice_types.h"
#include "qualified_profile.h"
typedef struct fd_voice_audio_snapshot fd_voice_audio_snapshot;
#ifdef ENABLE_FREEDV_VOICE
bool freedv_mode_available(void);
bool freedv_mode_active(void);
bool freedv_mode_status(fd_voice_status *);
const fd_qualified_profile *freedv_mode_profile(void); /* immutable selected UI view */
bool freedv_mode_audio_stats(fd_voice_audio_snapshot *);
void freedv_mode_open(void);
bool freedv_mode_defer_dialog(void *dialog, void *parent);
bool freedv_mode_defer_app(int action); /* 0 closes; otherwise opens destination after OFF */
bool freedv_mode_request(fd_local_operation, int32_t value);
bool freedv_mode_radio_ptt(bool pressed);
void freedv_mode_protection(void);
void freedv_mode_device_fault(void);
#else
static inline bool freedv_mode_available(void) { return false; }
static inline bool freedv_mode_active(void) { return false; }
static inline const fd_qualified_profile *freedv_mode_profile(void) { return 0; }
static inline bool freedv_mode_audio_stats(fd_voice_audio_snapshot *out) { (void)out;return false; }
static inline bool freedv_mode_status(fd_voice_status *out) { fd_voice_status empty={.state=FD_VS_OFF};if(out)*out=empty;return false; }
static inline void freedv_mode_open(void) {}
static inline bool freedv_mode_defer_dialog(void *d,void *p) { (void)d;(void)p;return false; }
static inline bool freedv_mode_defer_app(int action) { (void)action; return false; }
static inline bool freedv_mode_request(fd_local_operation op,int32_t v) { (void)op;(void)v;return false; }
static inline bool freedv_mode_radio_ptt(bool p) { (void)p;return false; }
static inline void freedv_mode_protection(void) {}
static inline void freedv_mode_device_fault(void) {}
#endif
#ifdef __cplusplus
}
#endif
