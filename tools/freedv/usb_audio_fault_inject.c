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
} delayed_operation;
static delayed_operation pending;
static unsigned matches;
static int hole_delivered;
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
    pending=(delayed_operation){.stream=stream,.callback=callback,.userdata=userdata,.issued=now_ms()};
    fprintf(stderr,"INJECT PA %s mode=%s device=%s delay=%sms\n",action,
            setting("USB_TEST_PA_MODE",""),pa_stream_get_device_name(stream),setting("USB_TEST_PA_DELAY_MS","650"));
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

typedef struct {pa_stream *stream;pa_stream_request_cb_t callback;void *userdata;int reported;} read_registration;
static read_registration readers[8];
static void deliver_read(pa_stream *stream,size_t bytes,void *userdata) {
    read_registration *registration=userdata;
    const char *target=getenv("USB_TEST_STALE_DEVICE"),*trigger=getenv("USB_TEST_STALE_TRIGGER");
    const char *device=pa_stream_get_device_name(stream);
    bool cold=atomic_load(&cold_started)&&(getenv("USB_TEST_COLD_NEVER")||
        !atomic_load(&cold_resume)||now_ms()<atomic_load(&cold_resume));
    if(cold||(target && trigger && device && !strcmp(target,device) && access(trigger,F_OK)==0)) {
        if(!registration->reported){registration->reported=1;fprintf(stderr,"INJECT callback starvation device=%s\n",device);}
        int (*peek)(pa_stream*,const void**,size_t*)=dlsym(RTLD_NEXT,"pa_stream_peek");
        int (*drop)(pa_stream*)=dlsym(RTLD_NEXT,"pa_stream_drop");
        for(unsigned i=0;i<64;++i){const void *data;size_t count;if(peek(stream,&data,&count)<0 || !count)break;if(drop(stream)<0)break;}
        return;
    }
    if(registration->callback)registration->callback(stream,bytes,registration->userdata);
    /* Publish recovery after the controller has consumed the real callback. */
    if(atomic_load(&cold_started)&&device){
        if(!strcmp(device,"alsa_input.usb-fixture"))atomic_fetch_or(&cold_recovered,1);
        else if(!strcmp(device,"radio_source_fixture"))atomic_fetch_or(&cold_recovered,2);
    }
}
void pa_stream_set_read_callback(pa_stream *stream,pa_stream_request_cb_t callback,void *userdata) {
    void (*real)(pa_stream*,pa_stream_request_cb_t,void*)=dlsym(RTLD_NEXT,"pa_stream_set_read_callback");
    if(!callback){real(stream,NULL,userdata);return;}
    read_registration *registration=NULL;
    for(unsigned i=0;i<8;++i)if(readers[i].stream==stream){registration=&readers[i];break;}
    if(!registration)for(unsigned i=0;i<8;++i)if(!readers[i].stream){registration=&readers[i];break;}
    if(!registration){real(stream,callback,userdata);return;}
    *registration=(read_registration){.stream=stream,.callback=callback,.userdata=userdata};
    real(stream,deliver_read,registration);
}
