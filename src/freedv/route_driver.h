// SPDX-License-Identifier: LGPL-2.1-or-later
#pragma once
#include "../hw/hw.h"
#include <stddef.h>
/* The caller owns the radio lock and the audio lease. These operations never
 * persist preferences. A transport success is not physical route readback. */
typedef struct {
    int microphone;
    unsigned hand_gain, internal_gain;
    bool record, modem, ptt, gain_before_key;
} fd_route_settings;
/* These are the firmware's ordinary recorder operations. Keep their order and
 * their deliberately small write set: recording does not mute/re-enable HMIC,
 * choose a microphone, key MODEM/PTT or force an extra full-register resend.
 * The periodic radio-thread keepalive remains independent. */
static inline void fd_route_record_start(unsigned internal_gain) {
    hw_set_i(HW_IMIC,internal_gain);
    hw_set_i(HW_RECORD,true);
}
static inline void fd_route_record_stop(unsigned hand_gain,unsigned internal_gain) {
    hw_set_i(HW_RECORD,false);
    hw_set_i(HW_HMIC,hand_gain);
    hw_set_i(HW_IMIC,internal_gain);
}
static inline void fd_route_stop(void) {
    hw_set_i(HW_HMIC,0); hw_set_i(HW_IMIC,0); hw_set_i(HW_VOX,0);
    hw_set_i(HW_MODEM,0); hw_set_i(HW_PTT,0);
}
typedef bool (*fd_route_admit_fn)(void *);
static inline bool fd_route_admitted(fd_route_admit_fn admit,void *context) {
    if(!admit || admit(context))return true;
    /* A release can arrive during a preceding transport write. Suppress all
     * following key/gain writes and clear any already-commanded software TX. */
    fd_route_stop();return false;
}
static inline bool fd_route_activate_guarded(const fd_route_settings *r,fd_route_admit_fn admit,void *context) {
    /* This is the guarded modem transition, not the ordinary recorder
     * sequence. Its before/after-key gain ordering remains under test. */
    hw_set_i(HW_HMIC,0); hw_set_i(HW_IMIC,0); hw_set_i(HW_VOX,0);
    hw_set_i(HW_MIC,r->microphone);
    hw_set_i(HW_RECORD,r->record);
    if(r->gain_before_key){hw_set_i(HW_HMIC,r->hand_gain);hw_set_i(HW_IMIC,r->internal_gain);}
    if(!fd_route_admitted(admit,context))return false;
    hw_set_i(HW_MODEM,r->modem);
    if(!fd_route_admitted(admit,context))return false;
    hw_set_i(HW_PTT,r->ptt);
    if(!fd_route_admitted(admit,context))return false;
    if(!hw_resend_all())return false;
    if(!fd_route_admitted(admit,context))return false;
    hw_set_i(HW_HMIC,r->hand_gain); hw_set_i(HW_IMIC,r->internal_gain);
    if(!fd_route_admitted(admit,context))return false;
    if(!hw_resend_all())return false;
    return fd_route_admitted(admit,context);
}
static inline bool fd_route_activate(const fd_route_settings *r) {
    return fd_route_activate_guarded(r,NULL,NULL);
}
