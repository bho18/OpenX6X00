// SPDX-License-Identifier: LGPL-2.1-or-later
#pragma once
#include <stdbool.h>
#include <stdint.h>
#include <stdatomic.h>
#include "voice_engine.h"
#include "qualified_profile.h"
typedef enum { FD_USB_PREPARE,FD_USB_RX,FD_USB_START,FD_USB_TX,FD_USB_DRAIN,FD_USB_FAULT,FD_USB_DONE } fd_usb_state;
typedef struct { bool valid,pressed,rf_valid,rf_tx,protection;uint64_t release,sequence; } fd_usb_inputs;
typedef struct {
    void *context;
    bool (*observe)(void *,fd_usb_inputs *);
    bool (*key)(void *,uint64_t expected_release);
    void (*clear)(void *,bool halt);
} fd_usb_control;
typedef struct {
    const char *source,*sink,*radio_source,*radio_sink,*trace_prefix;
    fd_usb_control control;
} fd_usb_config;
typedef struct fd_usb_audio fd_usb_audio;
typedef struct {
    fd_usb_state state;unsigned fault;uint64_t generation,turns,heartbeat_ms;
    unsigned input_queue,output_queue,holes,underflows,gaps;
    bool armed,pressed,rf_valid,rf_tx,stopped;
    fd_voice_stats engine;
} fd_usb_snapshot;
fd_usb_audio *fd_usb_audio_open(const fd_usb_config *);
void fd_usb_audio_arm(fd_usb_audio *,bool);
void fd_usb_audio_stop(fd_usb_audio *); /* clears software RF before audio waits */
fd_usb_snapshot fd_usb_audio_snapshot(fd_usb_audio *);
void fd_usb_audio_close(fd_usb_audio *); /* only after snapshot.stopped */
const char *fd_usb_fault_text(unsigned);

#ifdef ENABLE_FREEDV_USB_TEST
bool freedv_usb_available(void);
bool freedv_usb_active(void);
void freedv_usb_open(void);
bool freedv_usb_ptt(bool);
void freedv_usb_abort(void);
bool freedv_usb_leave(void);
const fd_qualified_profile *freedv_usb_profile_view(void);
#else
static inline bool freedv_usb_available(void){return false;}
static inline bool freedv_usb_active(void){return false;}
static inline void freedv_usb_open(void){}
static inline bool freedv_usb_ptt(bool p){(void)p;return false;}
static inline void freedv_usb_abort(void){}
static inline bool freedv_usb_leave(void){return false;}
static inline const fd_qualified_profile *freedv_usb_profile_view(void){return 0;}
#endif
