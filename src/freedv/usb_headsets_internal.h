// SPDX-License-Identifier: LGPL-2.1-or-later
#pragma once
#include "usb_headsets.h"
#include <pulse/pulseaudio.h>
typedef struct {
    fd_usb_headset device;
    char card[32];
    unsigned sources,sinks;
} fd_usb_headset_pair;
typedef struct { unsigned count;bool failed;fd_usb_headset_pair pairs[FD_USB_HEADSET_MAX]; } fd_usb_headset_collection;
void fd_usb_headsets_source(fd_usb_headset_collection *,const pa_source_info *);
void fd_usb_headsets_sink(fd_usb_headset_collection *,const pa_sink_info *);
unsigned fd_usb_headsets_catalog(const fd_usb_headset_collection *,fd_usb_headset *,unsigned);
