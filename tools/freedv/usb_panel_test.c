// SPDX-License-Identifier: BSD-2-Clause
#define _GNU_SOURCE
#undef NDEBUG
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdarg.h>
#include <fcntl.h>
#include <sys/ioctl.h>
#include <string.h>
static bool held,io_error;
static int panel_ioctl(int fd,unsigned long request,...){(void)fd;va_list args;va_start(args,request);unsigned long *bits=va_arg(args,unsigned long *);va_end(args);memset(bits,0,_IOC_SIZE(request));if(io_error)return -1;if(held)bits[707/(8*sizeof(long))]|=1UL<<(707%(8*sizeof(long)));return 0;}
#define ioctl panel_ioctl
#include "../../src/freedv/usb_ptt.h"
#undef ioctl
int main(void){int fds[2];assert(!pipe2(fds,O_NONBLOCK));atomic_uint_fast64_t release=0;bool pressed;
    assert(fd_usb_panel_poll(fds[0],&release,&pressed) && !pressed && !atomic_load(&release));
    struct input_event events[]={{.type=EV_KEY,.code=BTN_TRIGGER_HAPPY4,.value=1},{.type=EV_KEY,.code=BTN_TRIGGER_HAPPY4,.value=0},{.type=EV_KEY,.code=BTN_TRIGGER_HAPPY4,.value=1}};
    held=true;assert(write(fds[1],events,sizeof(events))==sizeof(events));assert(fd_usb_panel_poll(fds[0],&release,&pressed) && pressed && atomic_load(&release)==1);
    assert(fd_usb_panel_poll(fds[0],&release,&pressed) && pressed && atomic_load(&release)==1);
    struct input_event dropped={.type=EV_SYN,.code=SYN_DROPPED};assert(write(fds[1],&dropped,sizeof(dropped))==sizeof(dropped));assert(!fd_usb_panel_poll(fds[0],&release,&pressed));
    io_error=true;assert(!fd_usb_panel_poll(fds[0],&release,&pressed));io_error=false;close(fds[1]);assert(!fd_usb_panel_poll(fds[0],&release,&pressed));close(fds[0]);return 0;}
