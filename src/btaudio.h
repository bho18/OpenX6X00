/*
 *  SPDX-License-Identifier: LGPL-2.1-or-later
 *
 *  Bluetooth headphones: btaudio-ctl runner and "Mute speaker".
 */

#pragma once

#include <stdbool.h>
#include <stddef.h>

#ifdef __cplusplus
extern "C" {
#endif

/* Called in the LVGL thread with the output of the command. */
typedef void (*btaudio_done_cb_t)(const char *out, int status, void *user);

/* Mute the speaker (AF volume 0) while headphones are connected. */
void btaudio_init(void);

/* Run "btaudio-ctl <args>" in a worker thread; cb (may be NULL) gets the result. */
void btaudio_run(const char *args, btaudio_done_cb_t cb, void *user);
void sh_run(const char *command, btaudio_done_cb_t cb, void *user);

bool btaudio_mute_get(void);
void btaudio_mute_set(bool on);

/* True while headphones are connected and the speaker is to stay silent. */
bool btaudio_speaker_muted(void);

/* Headphone sink name for local playback while the speaker is silenced. */
bool btaudio_hp_sink(char *buf, size_t size);

/* Temporarily isolate only the managed raw radio -> A2DP RX loopbacks.
 * These asynchronous calls never change Bluetooth preferences or profiles.
 * HELD includes a matching monitor and acknowledged absence of raw routes.
 * A failed release is terminal: the kernel lease is already closed and the
 * monitor will reconcile independently. Close only after normal writers have
 * been released; native volume handover stays deferred until then. */
typedef struct btaudio_rx_hold btaudio_rx_hold_t;
typedef enum {
    BTAUDIO_RX_PENDING, BTAUDIO_RX_HELD, BTAUDIO_RX_FAILED,
    BTAUDIO_RX_REBOOT_REQUIRED, BTAUDIO_RX_RELEASED
} btaudio_rx_hold_status;
btaudio_rx_hold_t *btaudio_rx_hold_begin(void);
btaudio_rx_hold_status btaudio_rx_hold_poll(btaudio_rx_hold_t *, char *reason, size_t);
void btaudio_rx_hold_release(btaudio_rx_hold_t *);
bool btaudio_rx_hold_close(btaudio_rx_hold_t *);

#ifdef __cplusplus
}
#endif
