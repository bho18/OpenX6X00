// SPDX-License-Identifier: LGPL-2.1-or-later
#define _POSIX_C_SOURCE 200809L
#include "../../src/freedv/usb_headsets_internal.h"
#include <assert.h>
#include <string.h>
#include <stdio.h>
#include <time.h>

static pa_proplist *properties(const char *card,const char *serial){
    pa_proplist *p=pa_proplist_new();assert(p);
    pa_proplist_sets(p,"device.bus","usb");pa_proplist_sets(p,"alsa.card",card);
    pa_proplist_sets(p,"device.vendor.id","1234");pa_proplist_sets(p,"device.product.id","5678");
    pa_proplist_sets(p,"alsa.card_name","Generic USB headset");
    if(serial)pa_proplist_sets(p,"device.serial",serial);
    else pa_proplist_sets(p,"device.bus_path","pci-0000:00:14.0-usb-0:2:1.0");
    return p;
}
static void pair(fd_usb_headset_collection *c,pa_proplist *p){
    pa_source_info source={.name="alsa_input.usb-test.mono",.description="USB microphone",.driver="module-alsa-card.c",.monitor_of_sink=PA_INVALID_INDEX,.proplist=p};
    pa_sink_info sink={.name="alsa_output.usb-test.stereo",.description="USB speaker",.driver="module-alsa-card.c",.proplist=p};
    fd_usb_headsets_source(c,&source);fd_usb_headsets_sink(c,&sink);
}
int main(void){
    fd_usb_headset_collection c={0};fd_usb_headset out[FD_USB_HEADSET_MAX];pa_proplist *p=properties("2","serial-one");
    pair(&c,p);assert(fd_usb_headsets_catalog(&c,out,FD_USB_HEADSET_MAX)==1);
    assert(!strcmp(out[0].id,"usb:1234:5678:serial:serial-one"));assert(!strcmp(out[0].name,"Generic USB headset"));
    pa_source_info monitor={.name="monitor",.driver="module-alsa-card.c",.monitor_of_sink=0,.proplist=p};
    fd_usb_headsets_source(&c,&monitor);assert(fd_usb_headsets_catalog(&c,out,FD_USB_HEADSET_MAX)==1);
    /* ALSA renumbering preserves a serial identity. */
    fd_usb_headset_collection moved={0};pa_proplist_sets(p,"alsa.card","7");pair(&moved,p);
    assert(fd_usb_headsets_catalog(&moved,out,FD_USB_HEADSET_MAX)==1);assert(!strcmp(out[0].id,"usb:1234:5678:serial:serial-one"));
    /* Duplicate physical identity and ambiguous endpoint profiles are denied. */
    pair(&c,p);assert(fd_usb_headsets_catalog(&c,out,FD_USB_HEADSET_MAX)==0);
    pair(&moved,p);assert(fd_usb_headsets_catalog(&moved,out,FD_USB_HEADSET_MAX)==0);
    fd_usb_headset_collection path={0};pa_proplist *q=properties("4",NULL);pair(&path,q);assert(fd_usb_headsets_catalog(&path,out,FD_USB_HEADSET_MAX)==1);assert(strstr(out[0].id,":path:pci-"));
    pa_proplist_sets(q,"device.bus","platform");fd_usb_headset_collection internal={0};pair(&internal,q);assert(!fd_usb_headsets_catalog(&internal,out,FD_USB_HEADSET_MAX));
    pa_proplist_sets(q,"device.bus","usb");pa_proplist_unset(q,"device.bus_path");fd_usb_headset_collection anonymous={0};pair(&anonymous,q);assert(!fd_usb_headsets_catalog(&anonymous,out,FD_USB_HEADSET_MAX));
    /* Half devices on different physical cards cannot be combined. */
    fd_usb_headset_collection halves={0};pa_source_info source={.name="mic",.driver="module-alsa-card.c",.monitor_of_sink=PA_INVALID_INDEX,.proplist=p};fd_usb_headsets_source(&halves,&source);
    pa_proplist_sets(q,"device.serial","serial-two");pa_sink_info sink={.name="speaker",.driver="module-alsa-card.c",.proplist=q};fd_usb_headsets_sink(&halves,&sink);assert(!fd_usb_headsets_catalog(&halves,out,FD_USB_HEADSET_MAX));
    pa_proplist_free(p);pa_proplist_free(q);
    /* Cancellation owns no GUI/Pulse callbacks after finished close. This
     * also works when no Pulse server is present; no daemon is auto-spawned. */
    fd_usb_headset_scan *scan=fd_usb_headsets_scan_open();assert(scan);fd_usb_headsets_scan_cancel(scan);
    bool closed=false;for(unsigned n=0;n<6000&&!closed;++n){closed=fd_usb_headsets_scan_close(scan);if(!closed){struct timespec delay={0,1000000};nanosleep(&delay,NULL);}}assert(closed);
    puts("USB paired physical headset selection passed");return 0;
}
