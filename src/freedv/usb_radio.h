// SPDX-License-Identifier: LGPL-2.1-or-later
#pragma once
#include "usb_voice.h"
/* Stock-baseband USB topology; never an MCU lease or applied-route ACK. */
bool radio_usb_claim(void);
bool radio_usb_owned(void);
bool radio_usb_quiet(void);
bool radio_usb_restore(void); /* GUI thread; requires fresh RX and released panel PTT */
bool radio_usb_release(void); /* after audio restoration, fresh RX and panel release */
bool radio_usb_observe(void *,fd_usb_inputs *);
bool radio_usb_key(void *,uint64_t expected_release);
void radio_usb_clear(void *,bool halt);
void radio_usb_edge(bool pressed);
