// SPDX-License-Identifier: LGPL-2.1-or-later
#pragma once
#include <stdbool.h>
#define FD_USB_HEADSET_MAX 16
typedef struct {
    char id[256],name[160],source[512],sink[512];
} fd_usb_headset;
typedef struct {
    bool pending;
    unsigned count;
    int error; /* zero: success, nonzero: discovery failed/cancelled */
    fd_usb_headset devices[FD_USB_HEADSET_MAX];
} fd_usb_headset_catalog;
typedef struct fd_usb_headset_scan fd_usb_headset_scan;
/* Scans own a private, bounded Pulse worker; neither call blocks the GUI. */
fd_usb_headset_scan *fd_usb_headsets_scan_open(void);
fd_usb_headset_catalog fd_usb_headsets_scan_snapshot(fd_usb_headset_scan *);
void fd_usb_headsets_scan_cancel(fd_usb_headset_scan *);
/* Returns false while pending; call again after snapshot.pending is false. */
bool fd_usb_headsets_scan_close(fd_usb_headset_scan *);
