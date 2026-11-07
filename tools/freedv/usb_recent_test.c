// SPDX-License-Identifier: LGPL-2.1-or-later
#include "../../src/freedv/usb_recent.h"
#include <assert.h>
#include <limits.h>
#include <stdlib.h>

static void rotation_and_freeze(void) {
    fd_usb_recent recent={0};char before[FD_USB_RECENT_MAX_JSON+1],after[FD_USB_RECENT_MAX_JSON+1];
    assert(fd_usb_recent_serialize(&recent,before,sizeof(before),true)==-ENOENT && !*before);
    for(unsigned i=0;i<100;++i){
        fd_usb_recent_event event={.ms=i,.state=1,.stream=4};strcpy(event.name,"capture-state");
        fd_usb_recent_snapshot snapshot={.ms=i,.generation=i,.volume_percent=40};
        assert(fd_usb_recent_push_event(&recent,&event));assert(fd_usb_recent_push_snapshot(&recent,&snapshot));
    }
    assert(recent.live.event_count==64 && recent.live.events_evicted==36);
    assert(recent.live.snapshot_count==16 && recent.live.snapshots_evicted==84);
    assert(recent.live.events[recent.live.event_head].ms==36);
    assert(recent.live.snapshots[recent.live.snapshot_head].ms==84);
    assert(fd_usb_recent_freeze(&recent));
    int size=fd_usb_recent_serialize(&recent,before,sizeof(before),true);assert(size>0);
    for(unsigned i=100;i<200;++i){fd_usb_recent_event event={.ms=i};assert(fd_usb_recent_push_event(&recent,&event));}
    assert(!fd_usb_recent_freeze(&recent));
    assert(fd_usb_recent_serialize(&recent,after,sizeof(after),true)==size && !strcmp(before,after));
    assert(fd_usb_recent_serialize(&recent,after,(size_t)size,true)==-ENOSPC && !*after);
    assert(fd_usb_recent_serialize(&recent,after,(size_t)size+1,true)==size);
}
static void rejected_inputs(void) {
    fd_usb_recent recent={0};fd_usb_recent_event event={0};fd_usb_recent_snapshot snapshot={0};
    memset(event.name,'a',sizeof(event.name));assert(!fd_usb_recent_push_event(&recent,&event));
    memset(event.name,0,sizeof(event.name));event.name[0]='\n';assert(!fd_usb_recent_push_event(&recent,&event));
    event.name[0]=(char)0xff;assert(!fd_usb_recent_push_event(&recent,&event));
    event.name[0]=0;event.stream=5;assert(!fd_usb_recent_push_event(&recent,&event));
    event.stream=0;event.state=256;assert(!fd_usb_recent_push_event(&recent,&event));
    snapshot.volume_percent=101;assert(!fd_usb_recent_push_snapshot(&recent,&snapshot));
    snapshot.volume_percent=0;snapshot.fault=65536;assert(!fd_usb_recent_push_snapshot(&recent,&snapshot));
    assert(recent.live.events_rejected==5 && recent.live.snapshots_rejected==2);
    assert(!recent.live.event_count && !recent.live.snapshot_count);
}
static void maximum_json_bound(void) {
    fd_usb_recent recent={0};char output[FD_USB_RECENT_MAX_JSON+1];
    fd_usb_recent_event event={.ms=UINT64_MAX,.duration_ms=UINT64_MAX,.state=255,
        .status=INT_MIN,.error=INT_MIN,.stream=4};
    memset(event.name,'"',sizeof(event.name)-1);memset(event.operation,'\\',sizeof(event.operation)-1);
    fd_usb_recent_snapshot snapshot={.ms=UINT64_MAX,.generation=UINT64_MAX,.turns=UINT64_MAX,
        .display_generation=UINT64_MAX,.state=255,.fault=65535,.mic_health=255,
        .mic_age_ms=UINT_MAX,.rx_age_ms=UINT_MAX,.io_age_ms=UINT_MAX,.volume_percent=100};
    for(unsigned i=0;i<64;++i)assert(fd_usb_recent_push_event(&recent,&event));
    for(unsigned i=0;i<16;++i)assert(fd_usb_recent_push_snapshot(&recent,&snapshot));
    recent.live.events_evicted=recent.live.snapshots_evicted=UINT64_MAX;
    recent.live.events_rejected=recent.live.snapshots_rejected=UINT64_MAX;
    assert(fd_usb_recent_push_event(&recent,&event) && fd_usb_recent_push_snapshot(&recent,&snapshot));
    assert(recent.live.events_evicted==UINT64_MAX && recent.live.snapshots_evicted==UINT64_MAX);
    assert(fd_usb_recent_freeze(&recent));
    int size=fd_usb_recent_serialize(&recent,output,sizeof(output),true);
    assert(size>0 && (unsigned)size<=FD_USB_RECENT_MAX_JSON && (size_t)size==strlen(output));
    assert(strstr(output,"\\\"") && strstr(output,"\\\\"));
    assert((unsigned)size+1024<16384); /* identity envelope reserve */
    printf("Recent recorder actual worst-case serialization: %d bytes; identity reserve >=1024 bytes\n",size);
}
int main(void){rotation_and_freeze();rejected_inputs();maximum_json_bound();
    puts("Recent window rotation, immutable first fault, validation and JSON bound: PASS");return 0;}
