// SPDX-License-Identifier: LGPL-2.1-or-later
#pragma once
#include "usb_voice.h"
#include "voice_types.h"
/* Stock-baseband USB topology; never an MCU lease or applied-route ACK. */
bool radio_usb_claim(void);
bool radio_usb_owned(void);
bool radio_usb_quiet(void);
bool radio_usb_restore(void); /* GUI poll; queues hardware work and never waits for I2C */
bool radio_usb_terminal_restore(void); /* terminal exit also accepts held panel PTT */
bool radio_usb_release(void); /* after audio restoration, fresh RX and panel release */
bool radio_usb_observe(void *,fd_usb_inputs *);
/* Claim starts with new TX inhibited. The operational coordinator opens
 * admission after current configuration, fresh RX/audio and PTT release. */
bool radio_usb_allow_tx(bool);
bool radio_usb_permit(void *,bool); /* automatic operational readiness gate */
bool radio_usb_request(fd_local_operation,int32_t value); /* owned local GUI operation */
int radio_usb_power_tenths(void); /* thread-safe selected normal radio power */
/* Only terminal navigation replay can latch this authority. Poweroff caller
 * holds the radio lock; reset uses the independent hardware reset GPIO. */
bool radio_usb_terminal_authorize(void);
bool radio_usb_terminal_hardware(bool reset);
void radio_usb_inhibit(void *); /* atomic cancellation only; never waits for I2C */
/* Retain an external protection event even if its live level clears before the
 * coordinator polls. Distinct from ordinary Exit's cancellation. */
void radio_usb_policy_inhibit(void);
bool radio_usb_key(void *,uint64_t expected_release);
void radio_usb_clear(void *,bool halt);
void radio_usb_edge(bool pressed);
