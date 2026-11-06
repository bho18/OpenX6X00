/* SPDX-License-Identifier: BSD-2-Clause
 * Host integration tests only. Keep real Pulse connections and data flowing,
 * but vary one operation's completion or inject one capture hole. This file is
 * never included in a radio image or downloadable operator kit. */
#define _GNU_SOURCE
#include <pulse/pulseaudio.h>
#include <dlfcn.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>
#include <stdatomic.h>
#include <stdbool.h>

typedef struct {
    _Atomic(pa_operation *) operation;
    pa_stream *stream;
    pa_stream_success_cb_t callback;
    void *userdata;
    unsigned long long issued;
    atomic_int real_done, success, delivered;
    bool is_trigger;
} delayed_operation;
static delayed_operation pending;
static unsigned matches;
static atomic_int current_phase;
static int phase_code(const char *name){return !strcmp(name,"tx")?1:!strcmp(name,"rx-return")?2:0;}
void usb_test_operation_phase(const char *name){atomic_store(&current_phase,phase_code(name));}
static const char *phase_name(void){int phase=atomic_load(&current_phase);return phase==1?"tx":phase==2?"rx-return":"initial-rx";}
static int hole_delivered;
static atomic_ullong io_payload_started,io_pause_started,io_pause_duration,trigger_started,trigger_acked;
unsigned long long usb_test_trigger_started_ms(void){return atomic_load(&trigger_started);}
unsigned long long usb_test_trigger_ack_ms(void){return atomic_load(&trigger_acked);}
static atomic_uint io_pause_count;
static atomic_int buffer_reject_reported;
static atomic_ullong render_hold_started,render_hold_ended;
static atomic_uint render_hold_hits;
unsigned long long usb_test_render_hold_started_ms(void){return atomic_load(&render_hold_started);}
unsigned long long usb_test_render_hold_ended_ms(void){return atomic_load(&render_hold_ended);}
unsigned usb_test_render_hold_hits(void){return atomic_load(&render_hold_hits);}
unsigned usb_test_io_pause_count(void){return atomic_load(&io_pause_count);}
unsigned long long usb_test_io_pause_duration_ms(void){return atomic_load(&io_pause_duration);}
unsigned long long usb_test_io_pause_started_ms(void){return atomic_load(&io_pause_started);}
static atomic_int cold_started,cold_recovered;
static atomic_ullong cold_ack,cold_resume;
/* Test harness coordination only; these symbols are absent from radio builds. */
unsigned long long usb_test_cold_ack_ms(void){return atomic_load(&cold_ack);}
unsigned usb_test_cold_recovered(void){return (unsigned)atomic_load(&cold_recovered);}
static unsigned long long now_ms(void) {
    struct timespec t;clock_gettime(CLOCK_MONOTONIC,&t);
    return (unsigned long long)t.tv_sec*1000+t.tv_nsec/1000000;
}
static const char *setting(const char *key,const char *fallback) {
    const char *value=getenv(key);return value?value:fallback;
}
static int selected(pa_stream *stream,const char *action) {
    const char *mode=getenv("USB_TEST_PA_MODE");
    const char *device=pa_stream_get_device_name(stream);
    if(!mode || !device || strcmp(device,setting("USB_TEST_PA_DEVICE","radio_sink_fixture")) ||
       strcmp(action,setting("USB_TEST_PA_ACTION","flush")))return 0;
    const char *phase=getenv("USB_TEST_PA_PHASE");
    if(phase&&strcmp(phase,phase_name()))return 0;
    ++matches;
    return matches==(unsigned)strtoul(setting("USB_TEST_PA_MATCH","1"),NULL,10);
}
static void completed(pa_stream *stream,int success,void *unused) {
    (void)unused;pending.real_done=1;pending.success=success;
    if(!strcmp(setting("USB_TEST_PA_MODE",""),"reject")) {
        pending.delivered=1;
        if(pending.callback)pending.callback(stream,0,pending.userdata);
        pending.operation=NULL;
    }
}
static void begin(pa_stream *stream,pa_stream_success_cb_t callback,void *userdata,const char *action) {
    if(getenv("USB_TEST_COLD_CAPTURE"))atomic_store(&cold_started,1);
    pending=(delayed_operation){.stream=stream,.callback=callback,.userdata=userdata,.issued=now_ms(),.is_trigger=!strcmp(action,"trigger")};
    if(pending.is_trigger)atomic_store(&trigger_started,pending.issued);
    fprintf(stderr,"INJECT PA %s mode=%s device=%s delay=%sms phase=%s\n",action,
            setting("USB_TEST_PA_MODE",""),pa_stream_get_device_name(stream),setting("USB_TEST_PA_DELAY_MS","650"),phase_name());
}
pa_operation *pa_stream_flush(pa_stream *stream,pa_stream_success_cb_t callback,void *userdata) {
    pa_operation *(*real)(pa_stream*,pa_stream_success_cb_t,void*)=dlsym(RTLD_NEXT,"pa_stream_flush");
    if(!selected(stream,"flush"))return real(stream,callback,userdata);
    begin(stream,callback,userdata,"flush");
    pending.operation=real(stream,completed,NULL);return pending.operation;
}
pa_operation *pa_stream_cork(pa_stream *stream,int cork,pa_stream_success_cb_t callback,void *userdata) {
    pa_operation *(*real)(pa_stream*,int,pa_stream_success_cb_t,void*)=dlsym(RTLD_NEXT,"pa_stream_cork");
    const char *action=cork?"cork":"uncork";
    if(!selected(stream,action))return real(stream,cork,callback,userdata);
    begin(stream,callback,userdata,action);
    pending.operation=real(stream,cork,completed,NULL);return pending.operation;
}
pa_operation *pa_stream_trigger(pa_stream *stream,pa_stream_success_cb_t callback,void *userdata){
    pa_operation *(*real)(pa_stream*,pa_stream_success_cb_t,void*)=dlsym(RTLD_NEXT,"pa_stream_trigger");
    if(!selected(stream,"trigger"))return real(stream,callback,userdata);
    begin(stream,callback,userdata,"trigger");pending.operation=real(stream,completed,NULL);return pending.operation;
}
pa_operation_state_t pa_operation_get_state(const pa_operation *operation) {
    pa_operation_state_t (*real)(const pa_operation*)=dlsym(RTLD_NEXT,"pa_operation_get_state");
    pa_operation_state_t state=real(operation);
    if(operation!=pending.operation || state==PA_OPERATION_CANCELLED)return state;
    const char *mode=setting("USB_TEST_PA_MODE","");
    if(!strcmp(mode,"missing"))return PA_OPERATION_RUNNING;
    if(!strcmp(mode,"reject"))return state;
    if(now_ms()-pending.issued<strtoul(setting("USB_TEST_PA_DELAY_MS","650"),NULL,10))return PA_OPERATION_RUNNING;
    if(pending.real_done && !pending.delivered) {
        pending.delivered=1;
        if(pending.is_trigger)atomic_store(&trigger_acked,now_ms());
        if(atomic_load(&cold_started)){
            unsigned long long now=now_ms();atomic_store(&cold_ack,now);
            atomic_store(&cold_resume,now+strtoul(setting("USB_TEST_COLD_RESUME_MS","30"),NULL,10));
            fprintf(stderr,"INJECT cold ACK success=%d after=%llums resume=%s\n",pending.success,now-pending.issued,
                getenv("USB_TEST_COLD_NEVER")?"never":setting("USB_TEST_COLD_RESUME_MS","30"));
        }
        if(pending.callback)pending.callback(pending.stream,pending.success,pending.userdata);
        pending.operation=NULL;
    }
    return state;
}
void pa_operation_cancel(pa_operation *operation) {
    void (*real)(pa_operation*)=dlsym(RTLD_NEXT,"pa_operation_cancel");
    if(operation==pending.operation)pending.operation=NULL;
    real(operation);
}
int pa_stream_peek(pa_stream *stream,const void **data,size_t *bytes) {
    int (*real)(pa_stream*,const void**,size_t*)=dlsym(RTLD_NEXT,"pa_stream_peek");
    int result=real(stream,data,bytes);
    const char *device=pa_stream_get_device_name(stream),*target=getenv("USB_TEST_HOLE_DEVICE"),*trigger=getenv("USB_TEST_HOLE_TRIGGER");
    if(!hole_delivered && result>=0 && *data && *bytes && device && target && !strcmp(device,target) &&
       trigger && access(trigger,F_OK)==0) {
        hole_delivered=1;*data=NULL;
        fprintf(stderr,"INJECT capture hole device=%s bytes=%zu\n",device,*bytes);
    }
    return result;
}

typedef struct {pa_stream *stream;pa_stream_request_cb_t callback;void *userdata;int reported;unsigned long long stale_started;} read_registration;
static read_registration readers[8];
static void deliver_read(pa_stream *stream,size_t bytes,void *userdata) {
    read_registration *registration=userdata;
    const char *target=getenv("USB_TEST_STALE_DEVICE"),*trigger=getenv("USB_TEST_STALE_TRIGGER");
    const char *device=pa_stream_get_device_name(stream);
    bool cold=atomic_load(&cold_started)&&(getenv("USB_TEST_COLD_NEVER")||
        !atomic_load(&cold_resume)||now_ms()<atomic_load(&cold_resume));
    bool target_stale=target&&trigger&&device&&!strcmp(target,device)&&access(trigger,F_OK)==0;
    unsigned long long now=now_ms(),duration=strtoull(setting("USB_TEST_STALE_DURATION_MS","0"),NULL,10);
    if(target_stale&&!registration->stale_started)registration->stale_started=now;
    bool dropping=target_stale&&(!duration||now-registration->stale_started<duration);
    if(cold||dropping) {
        if(!registration->reported){registration->reported=1;fprintf(stderr,"INJECT callback starvation device=%s\n",device);}
        int (*peek)(pa_stream*,const void**,size_t*)=dlsym(RTLD_NEXT,"pa_stream_peek");
        int (*drop)(pa_stream*)=dlsym(RTLD_NEXT,"pa_stream_drop");
        for(unsigned i=0;i<64;++i){const void *data;size_t count;if(peek(stream,&data,&count)<0 || !count)break;if(drop(stream)<0)break;}
        return;
    }
    if(duration&&target_stale&&!dropping&&registration->reported==1){registration->reported=2;fprintf(stderr,"INJECT valid capture resumed device=%s omitted_ms=%llu no-hole=1\n",device,now-registration->stale_started);}
    if(registration->callback)registration->callback(stream,bytes,registration->userdata);
    /* Publish recovery after the controller has consumed the real callback. */
    if(atomic_load(&cold_started)&&device){
        if(!strcmp(device,"alsa_input.usb-fixture"))atomic_fetch_or(&cold_recovered,1);
        else if(!strcmp(device,"radio_source_fixture"))atomic_fetch_or(&cold_recovered,2);
    }
}
void pa_stream_set_read_callback(pa_stream *stream,pa_stream_request_cb_t callback,void *userdata) {
    void (*real)(pa_stream*,pa_stream_request_cb_t,void*)=dlsym(RTLD_NEXT,"pa_stream_set_read_callback");
    if(!callback){for(unsigned i=0;i<8;++i)if(readers[i].stream==stream)memset(&readers[i],0,sizeof(readers[i]));real(stream,NULL,userdata);return;}
    read_registration *registration=NULL;
    for(unsigned i=0;i<8;++i)if(readers[i].stream==stream){registration=&readers[i];break;}
    if(!registration)for(unsigned i=0;i<8;++i)if(!readers[i].stream){registration=&readers[i];break;}
    if(!registration){real(stream,callback,userdata);return;}
    *registration=(read_registration){.stream=stream,.callback=callback,.userdata=userdata};
    real(stream,deliver_read,registration);
}

static bool render_hold(pa_stream *stream){
    const char *trigger=getenv("USB_TEST_RENDER_HOLD_TRIGGER"),*device=pa_stream_get_device_name(stream);
    if(!trigger||!device||strcmp(device,"radio_sink_fixture")||access(trigger,F_OK))return false;
    unsigned long long now=now_ms(),start=atomic_load(&render_hold_started);
    if(!start){atomic_store(&render_hold_started,now);start=now;fprintf(stderr,"INJECT render hold started_ms=%llu device=%s no-discard=1\n",start,device);}
    unsigned delay=(unsigned)strtoul(setting("USB_TEST_RENDER_HOLD_MS","150"),NULL,10);
    if(now-start<delay){atomic_fetch_add(&render_hold_hits,1);return true;}
    if(!atomic_load(&render_hold_ended)){atomic_store(&render_hold_ended,now);fprintf(stderr,"INJECT render hold ended_ms=%llu elapsed_ms=%llu hits=%u no-discard=1\n",now,now-start,atomic_load(&render_hold_hits));}
    return false;
}
size_t pa_stream_writable_size(const pa_stream *stream){
    size_t (*real)(const pa_stream*)=dlsym(RTLD_NEXT,"pa_stream_writable_size");
    if(render_hold((pa_stream *)stream))return 0;
    return real(stream);
}
typedef struct {pa_stream *stream;pa_stream_request_cb_t callback;void *userdata;} write_registration;
static write_registration writers[8];
static void deliver_write(pa_stream *stream,size_t bytes,void *userdata){
    write_registration *r=userdata;if(!render_hold(stream)&&r->callback)r->callback(stream,bytes,r->userdata);
}
void pa_stream_set_write_callback(pa_stream *stream,pa_stream_request_cb_t callback,void *userdata){
    void (*real)(pa_stream*,pa_stream_request_cb_t,void*)=dlsym(RTLD_NEXT,"pa_stream_set_write_callback");
    if(!callback){for(unsigned i=0;i<8;++i)if(writers[i].stream==stream)memset(&writers[i],0,sizeof(writers[i]));real(stream,NULL,userdata);return;}
    write_registration *r=NULL;for(unsigned i=0;i<8;++i)if(writers[i].stream==stream){r=&writers[i];break;}
    if(!r)for(unsigned i=0;i<8;++i)if(!writers[i].stream){r=&writers[i];break;}
    if(!r){real(stream,callback,userdata);return;}*r=(write_registration){stream,callback,userdata};real(stream,deliver_write,r);
}
/* A service interruption holds the real client PA lock; the server continues
 * consuming its actual negotiated reserve and retaining incoming capture. */
int pa_stream_write(pa_stream *stream,const void *data,size_t bytes,pa_free_cb_t free_cb,int64_t offset,pa_seek_mode_t seek){
    int (*real)(pa_stream*,const void*,size_t,pa_free_cb_t,int64_t,pa_seek_mode_t)=dlsym(RTLD_NEXT,"pa_stream_write");
    const char *device=pa_stream_get_device_name(stream),*target=getenv("USB_TEST_IO_PAUSE_DEVICE");
    if(device&&target&&!strcmp(device,target)&&bytes){
        const unsigned char *pcm=data;bool nonzero=false;for(size_t i=0;i<bytes;++i)if(pcm[i]){nonzero=true;break;}
        unsigned long long now=now_ms(),first=atomic_load(&io_payload_started);
        if(nonzero&&!first){atomic_store(&io_payload_started,now);first=now;}
        unsigned expected=0;
        if(first&&now-first>=strtoull(setting("USB_TEST_IO_PAUSE_AFTER_MS","600"),NULL,10)&&atomic_compare_exchange_strong(&io_pause_count,&expected,1)){
            atomic_store(&io_pause_started,now);unsigned delay=(unsigned)strtoul(setting("USB_TEST_IO_PAUSE_MS","150"),NULL,10);
            fprintf(stderr,"INJECT IO service pause device=%s requested_ms=%u after_payload_ms=%llu\n",device,delay,now-first);
            struct timespec span={delay/1000,(long)(delay%1000)*1000000};nanosleep(&span,NULL);
            unsigned long long elapsed=now_ms()-now;atomic_store(&io_pause_duration,elapsed);
            fprintf(stderr,"INJECT IO service resumed device=%s elapsed_ms=%llu no-discard=1\n",device,elapsed);
        }
    }
    return real(stream,data,bytes,free_cb,offset,seek);
}
const pa_buffer_attr *pa_stream_get_buffer_attr(pa_stream *stream){
    const pa_buffer_attr *(*real)(pa_stream*)=dlsym(RTLD_NEXT,"pa_stream_get_buffer_attr");
    const pa_buffer_attr *value=real(stream);const char *device=pa_stream_get_device_name(stream);
    if(value&&getenv("USB_TEST_BUFFER_REJECT")&&device&&!strcmp(device,"radio_sink_fixture")){
        if(!atomic_exchange(&buffer_reject_reported,1))fprintf(stderr,"INJECT negotiated buffer rejection device=%s tlength=6174 minreq=1764 prebuf=4412\n",device);
        static _Thread_local pa_buffer_attr insufficient;
        insufficient=*value;insufficient.maxlength=17640;insufficient.tlength=6174;insufficient.prebuf=4412;insufficient.minreq=1764;
        return &insufficient;
    }
    return value;
}
