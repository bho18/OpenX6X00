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
/* Live TX is a separate adapter gate. Claim starts it disabled; only the
 * local GUI may enable it after the retained audio evaluation has passed. */
bool radio_usb_allow_tx(bool);
void radio_usb_inhibit(void *); /* atomic cancellation only; never waits for I2C */
bool radio_usb_key(void *,uint64_t expected_release);
void radio_usb_clear(void *,bool halt);
void radio_usb_edge(bool pressed);
