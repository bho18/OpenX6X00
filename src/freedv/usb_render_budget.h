// SPDX-License-Identifier: LGPL-2.1-or-later
#pragma once
#include <stdbool.h>
/* Frame domains are explicit: engine input is 48 kHz; transport output and
 * its bounded queue are 44.1 kHz. A converter may emit buffered history, so
 * reserve its complete declared output capacity rather than an input ratio. */
#define FD_USB_RENDER_INPUT_FRAMES 4096u
#define FD_USB_RENDER_OUTPUT_FRAMES 4608u
#define FD_USB_TX_QUEUE_FRAMES 22050u
static inline bool fd_usb_tx_render_room(unsigned queued_frames){
    return queued_frames<=FD_USB_TX_QUEUE_FRAMES&&
        FD_USB_TX_QUEUE_FRAMES-queued_frames>=FD_USB_RENDER_OUTPUT_FRAMES;
}
