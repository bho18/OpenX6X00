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
#include <stdint.h>
static unsigned long long now_ms(void);

typedef struct {
    _Atomic(pa_operation *) operation;
    pa_stream *stream;
    pa_stream_success_cb_t callback;
    void *userdata;
    unsigned long long issued,wait_started;
    const char *action,*phase;
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
static atomic_ullong mic_release_hold_started,mic_release_hold_ended,mic_callback_started,mic_callback_ended,mic_release_observed,mic_barrier_started,mic_test_cutoff;
static atomic_ullong mic_release_posted,mic_lock_attempted,mic_release_lock_attempt;
static atomic_bool mic_lock_waiting,mic_release_saw_waiter;
typedef void (*mic_release_action_fn)(void *);
static _Atomic(mic_release_action_fn) mic_release_action;
static _Atomic(void *) mic_release_userdata;
void usb_test_mic_release_action(mic_release_action_fn action,void *userdata){atomic_store(&mic_release_userdata,userdata);atomic_store(&mic_release_action,action);}
unsigned long long usb_test_mic_release_posted_ms(void){return atomic_load(&mic_release_posted);}
int usb_test_mic_release_now(void){
    mic_release_action_fn action=atomic_load(&mic_release_action);if(!action)return 0;
    atomic_store(&mic_release_saw_waiter,atomic_load(&mic_lock_waiting));atomic_store(&mic_release_lock_attempt,atomic_load(&mic_lock_attempted));
    action(atomic_load(&mic_release_userdata));atomic_store(&mic_release_posted,now_ms());return 1;
}
void pa_threaded_mainloop_lock(pa_threaded_mainloop *loop){
    void (*real)(pa_threaded_mainloop*)=dlsym(RTLD_NEXT,"pa_threaded_mainloop_lock");
    atomic_store(&mic_lock_attempted,now_ms());atomic_store(&mic_lock_waiting,true);
    real(loop);atomic_store(&mic_lock_waiting,false);
}
static atomic_uint mic_release_peeks,mic_blocked_peeks;
static unsigned long long mic_sentinel_frames;
static atomic_int mic_close_requested,mic_close_invalid;
static pa_stream_success_cb_t mic_close_callback;static void *mic_close_userdata;
static void (*mic_close_stop_callback)(void *);static void *mic_close_stop_userdata;
void usb_test_mic_stop_callback(void (*callback)(void *),void *userdata){mic_close_stop_callback=callback;mic_close_stop_userdata=userdata;}
void usb_test_mic_close_requested(void){atomic_store(&mic_close_requested,1);}
unsigned long long usb_test_mic_release_hold_started_ms(void){return atomic_load(&mic_release_hold_started);}
unsigned long long usb_test_mic_barrier_started_ms(void){return atomic_load(&mic_barrier_started);}
void usb_test_mic_release_observed(void){atomic_store(&mic_release_observed,now_ms());}
void usb_test_mic_cutoff(uint64_t cutoff){atomic_store(&mic_test_cutoff,cutoff);}
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
    pending=(delayed_operation){.stream=stream,.callback=callback,.userdata=userdata,.issued=now_ms(),.action=action,.phase=phase_name(),.is_trigger=!strcmp(action,"trigger")};
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
static void close_timing_done(pa_stream *stream,int success,void *unused){
    (void)unused;const char *mode=setting("USB_TEST_MIC_CLOSE_MODE","");
    fprintf(stderr,"INJECT MIC close-timing mode=%s real_ack_ms=%llu success=%d withheld=%u phase=drain\n",mode,now_ms(),success,!strcmp(mode,"missing"));
    if(!strcmp(mode,"missing"))return;
    if(!strcmp(mode,"stop")){
        if(mic_close_stop_callback)mic_close_stop_callback(mic_close_stop_userdata);
        fprintf(stderr,"INJECT MIC close-timing stop-before-forward stop_ms=%llu callback_invoked=%u phase=drain\n",now_ms(),mic_close_stop_callback!=NULL);
    }else atomic_store(&mic_close_invalid,1);
    if(mic_close_callback)mic_close_callback(stream,success,mic_close_userdata);
    atomic_store(&mic_close_invalid,0);
}
pa_operation *pa_stream_update_timing_info(pa_stream *stream,pa_stream_success_cb_t callback,void *userdata){
    pa_operation *(*real)(pa_stream*,pa_stream_success_cb_t,void*)=dlsym(RTLD_NEXT,"pa_stream_update_timing_info");
    const char *mode=getenv("USB_TEST_MIC_CLOSE_MODE");
    if(!mode||!atomic_exchange(&mic_close_requested,0))return real(stream,callback,userdata);
    mic_close_callback=callback;mic_close_userdata=userdata;
    fprintf(stderr,"INJECT MIC close-timing requested_ms=%llu mode=%s phase=drain\n",now_ms(),mode);
    return real(stream,close_timing_done,NULL);
}
const pa_timing_info *pa_stream_get_timing_info(pa_stream *stream){
    const pa_timing_info *(*real)(pa_stream*)=dlsym(RTLD_NEXT,"pa_stream_get_timing_info");
    const pa_timing_info *info=real(stream);
    if(info&&atomic_load(&mic_close_invalid)){
        static _Thread_local pa_timing_info invalid;invalid=*info;
        invalid.write_index=0;invalid.source_usec=invalid.transport_usec=0;
        fprintf(stderr,"INJECT MIC close-timing retrograde read_index_bytes=%lld write_index_bytes=0 source_usec=0 transport_usec=0 phase=drain\n",(long long)invalid.read_index);
        return &invalid;
    }
    return info;
}
pa_operation_state_t pa_operation_get_state(const pa_operation *operation) {
    pa_operation_state_t (*real)(const pa_operation*)=dlsym(RTLD_NEXT,"pa_operation_get_state");
    pa_operation_state_t state=real(operation);
    if(operation!=pending.operation || state==PA_OPERATION_CANCELLED)return state;
    const char *mode=setting("USB_TEST_PA_MODE","");
    if(!strcmp(mode,"missing"))return PA_OPERATION_RUNNING;
    if(!strcmp(mode,"reject"))return state;
    /* The controller starts its operation deadline after the real Pulse API
     * returns. Starting this hold at issue time could under-exercise that
     * deadline by the API/logging time (650 ms injected, 649 ms observed).
     * Its first state poll is inside the measured wait, so this origin proves
     * the full requested interval without changing the production deadline. */
    unsigned long long now=now_ms();
    if(!pending.wait_started){pending.wait_started=now;if(pending.is_trigger)atomic_store(&trigger_started,now);}
    unsigned long long requested=strtoull(setting("USB_TEST_PA_DELAY_MS","650"),NULL,10);
    if(now-pending.wait_started<requested)return PA_OPERATION_RUNNING;
    if(pending.real_done && !pending.delivered) {
        pending.delivered=1;
        if(pending.is_trigger)atomic_store(&trigger_acked,now);
        fprintf(stderr,"INJECT PA delayed-ack action=%s phase=%s issued_ms=%llu wait_started_ms=%llu ack_ms=%llu requested_ms=%llu success=%d\n",
            pending.action,pending.phase,pending.issued,pending.wait_started,now,requested,pending.success);
        if(atomic_load(&cold_started)){
            atomic_store(&cold_ack,now);
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
typedef struct {uint64_t start,end,tap;unsigned frames;unsigned long long ms;} mic_observation;
static mic_observation mic_observations[16384];static unsigned mic_observation_n;static uint64_t mic_observer_n;
static int16_t *mic_observer_pcm;static pa_stream *mic_observer_stream;static uint64_t mic_observer_last=UINT64_MAX;
static void mic_observer_finish(void){
    const char *prefix=getenv("USB_TEST_MIC_OBSERVER_PREFIX");if(!prefix)return;
    char path[2048];snprintf(path,sizeof(path),"%s.pcm",prefix);FILE *f=fopen(path,"wb");
    if(f){fwrite(mic_observer_pcm,2,mic_observer_n,f);fclose(f);}
    snprintf(path,sizeof(path),"%s.jsonl",prefix);f=fopen(path,"w");
    if(f){for(unsigned i=0;i<mic_observation_n;++i){mic_observation r=mic_observations[i];fprintf(f,"{\"index_start\":%llu,\"index_end\":%llu,\"tap_start\":%llu,\"tap_frames\":%u,\"valid\":true,\"observed_ms\":%llu}\n",(unsigned long long)r.start,(unsigned long long)r.end,(unsigned long long)r.tap,r.frames,r.ms);}fclose(f);}
    fprintf(stderr,"INJECT MIC release mode=%s hold_started_ms=%llu hold_ended_ms=%llu callback_started_ms=%llu barrier_started_ms=%llu release_observed_ms=%llu callback_ended_ms=%llu blocked_peeks=%u cutoff_index=%llu sentinel_frames=%llu no-discard=1 release_posted_ms=%llu pa_lock_attempt_ms=%llu pa_lock_waiting=%u coordinator_wait_required=0\n",setting("USB_TEST_MIC_RELEASE_MODE",""),atomic_load(&mic_release_hold_started),atomic_load(&mic_release_hold_ended),atomic_load(&mic_callback_started),atomic_load(&mic_barrier_started),atomic_load(&mic_release_observed),atomic_load(&mic_callback_ended),atomic_load(&mic_blocked_peeks),atomic_load(&mic_test_cutoff),mic_sentinel_frames,atomic_load(&mic_release_posted),atomic_load(&mic_release_lock_attempt),atomic_load(&mic_release_saw_waiter));
    free(mic_observer_pcm);
}
static bool mic_release_target(pa_stream *stream){
    const char *device=pa_stream_get_device_name(stream),*trigger=getenv("USB_TEST_MIC_RELEASE_TRIGGER");
    return device&&!strcmp(device,"alsa_input.usb-fixture")&&trigger&&!access(trigger,F_OK);
}
static void observe_mic(pa_stream *stream,const void **data,size_t bytes){
    const char *prefix=getenv("USB_TEST_MIC_OBSERVER_PREFIX"),*device=pa_stream_get_device_name(stream);
    if(!prefix||!device||strcmp(device,"alsa_input.usb-fixture")||!*data||!bytes||bytes%2)return;
    const pa_timing_info *timing=pa_stream_get_timing_info(stream);
    if(!timing||timing->read_index_corrupt||timing->read_index<0||timing->read_index%2)return;
    uint64_t index=(uint64_t)timing->read_index/2,cutoff=atomic_load(&mic_test_cutoff);unsigned frames=(unsigned)bytes/2;
    static _Thread_local int16_t replacement[48000];
    if(cutoff&&index+frames>cutoff&&frames<=48000){
        memcpy(replacement,*data,bytes);unsigned from=index<cutoff?(unsigned)(cutoff-index):0;
        for(unsigned i=from;i<frames;++i)replacement[i]=(int16_t)30001;
        *data=replacement;
    }
    if(stream==mic_observer_stream&&index==mic_observer_last)return;
    if(!mic_observer_pcm){mic_observer_pcm=calloc(48000u*120u,2);if(!mic_observer_pcm)return;atexit(mic_observer_finish);}
    if(mic_observation_n>=16384||mic_observer_n+frames>48000u*120u)return;
    mic_observer_stream=stream;mic_observer_last=index;
    mic_observations[mic_observation_n++]=(mic_observation){index,index+frames,mic_observer_n,frames,now_ms()};
    memcpy(mic_observer_pcm+mic_observer_n,*data,bytes);mic_observer_n+=frames;
    if(cutoff&&index+frames>cutoff)mic_sentinel_frames+=index<cutoff?index+frames-cutoff:frames;
}
int pa_stream_peek(pa_stream *stream,const void **data,size_t *bytes) {
    int (*real)(pa_stream*,const void**,size_t*)=dlsym(RTLD_NEXT,"pa_stream_peek");
    int result=real(stream,data,bytes);
    if(result>=0&&mic_release_target(stream)){
        unsigned long long started=atomic_load(&mic_release_hold_started);
        if(started&&now_ms()-started<150){atomic_fetch_add(&mic_blocked_peeks,1);*data=NULL;*bytes=0;return 0;}
        if(*data&&*bytes&&!strcmp(setting("USB_TEST_MIC_RELEASE_MODE",""),"split")&&atomic_load(&mic_callback_started)&&!atomic_load(&mic_callback_ended)&&atomic_fetch_add(&mic_release_peeks,1)==1){
            /* The coordinator may already be waiting for our PA lock. Only
             * post the physical/retained input here; never wait for its
             * observation while holding the lock it needs to make progress. */
            atomic_store(&mic_barrier_started,now_ms());int posted=usb_test_mic_release_now();
            fprintf(stderr,"INJECT MIC callback-release posted=%d posted_ms=%llu pa_lock_waiting=%u coordinator_wait_required=0\n",posted,atomic_load(&mic_release_posted),atomic_load(&mic_release_saw_waiter));
        }
    }
    if(result>=0)observe_mic(stream,data,*bytes);
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
    if(mic_release_target(stream)){
        unsigned long long now=now_ms(),started=atomic_load(&mic_release_hold_started);
        if(!started){started=now;atomic_store(&mic_release_hold_started,now);}
        if(now-started<150)return; /* Retain actual queued PCM; never drop. */
        bool first=!atomic_load(&mic_release_hold_ended);
        if(first){atomic_store(&mic_release_hold_ended,now);atomic_store(&mic_callback_started,now);}
        if(registration->callback)registration->callback(stream,bytes,registration->userdata);
        if(first){atomic_store(&mic_callback_ended,now_ms());fprintf(stderr,"INJECT MIC callback-finished started_ms=%llu ended_ms=%llu release_posted_ms=%llu coordinator_observed_ms=%llu\n",atomic_load(&mic_callback_started),atomic_load(&mic_callback_ended),atomic_load(&mic_release_posted),atomic_load(&mic_release_observed));}
        return;
    }
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
