// SPDX-License-Identifier: LGPL-2.1-or-later
#pragma once
#include <linux/input.h>
#include <sys/ioctl.h>
#include <unistd.h>
#include <errno.h>
#include <string.h>
#include <stdatomic.h>
#include <stdbool.h>
#include <stdint.h>
/* Independent evdev client queue retains releases even if the GUI stalls.
 * Caller serializes this fd and counter. SYN_DROPPED invalidates admission. */
static inline bool fd_usb_panel_poll(int fd,atomic_uint_fast64_t *releases,bool *pressed){
    bool valid=true;struct input_event events[16];
    for(unsigned batch=0;batch<64;++batch){
        ssize_t n=read(fd,events,sizeof(events));
        if(n<0){if(errno!=EAGAIN && errno!=EWOULDBLOCK)valid=false;break;}
        if(!n || n%(ssize_t)sizeof(events[0])){valid=false;break;}
        for(unsigned j=0;j<(unsigned)n/sizeof(events[0]);++j){
            if(events[j].type==EV_SYN && events[j].code==SYN_DROPPED)valid=false;
            if(events[j].type==EV_KEY && events[j].code==BTN_TRIGGER_HAPPY4 && !events[j].value)atomic_fetch_add(releases,1);
        }
        if(batch==63)valid=false;
    }
    unsigned long keys[(KEY_MAX+8*sizeof(long))/(8*sizeof(long))];memset(keys,0,sizeof(keys));
    if(ioctl(fd,EVIOCGKEY(sizeof(keys)),keys)<0)valid=false;
    *pressed=(keys[BTN_TRIGGER_HAPPY4/(8*sizeof(long))] & (1UL<<(BTN_TRIGGER_HAPPY4%(8*sizeof(long)))))!=0;
    return valid;
}
