// SPDX-License-Identifier: LGPL-2.1-or-later
#define _POSIX_C_SOURCE 200809L
#include "usb_voice.h"
#include <pulse/pulseaudio.h>
#include <pulse/timeval.h>
#include <pthread.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>
#include <samplerate.h>

#define RATE 48000u
#define RADIO_RATE 44100u
#define QMAX 24000u
#define EVENT_MAX 256u
#define OP_TIMEOUT_MS 1000u
#define CAPTURE_TIMEOUT_MS 750u
#define PREPARATION_CAPTURE_TIMEOUT_MS 1500u

typedef struct { int16_t p[QMAX]; unsigned head,n; } pcm_queue;
enum {
    U_OK,U_STOP,U_DEVICE,U_PTT,U_STATUS,U_PROTECTION,U_QUEUE,U_ENGINE,
    U_KEY,U_DRAIN,U_TIMEOUT,U_STALL,U_IDENTITY,U_PREPARE,U_PA_TIMEOUT,
    U_PA_REJECT,U_CAPTURE_HOLE,U_CAPTURE_STALE,U_IO,U_MOVED,U_CONTROL,
    U_TRANSITION,U_QUALIFICATION,U_CAPTURE_RATE
};
enum { MIC,RADIO_RX,HEADSET,MODEM,STREAM_COUNT };
static const char *const stream_names[]={"mic","radio-rx","headset","modem"};
typedef struct {
    atomic_uint_fast64_t last,captured,discarded,max_gap,first,first_count;
    atomic_uint holes,underflows,payload_underflows;
    atomic_int state,suspended;
} stream_metrics;
typedef struct {
    uint64_t ms,duration; unsigned state; int error,status;
    char event[32],operation[40],stream[16];
} trace_event;
struct fd_usb_audio {
    fd_usb_config c;
    pthread_t worker,watch,control_worker;
    pthread_mutex_t mutex,events_mutex,control_mutex;
    pthread_cond_t control_changed;
    atomic_bool stop,stopped,armed,rf_disabled,qualification_requested,qualification_running,qualification_complete;
    atomic_bool qualification_keyed,qualification_loopback,reference_input_active,fault_record_ready;
    atomic_int context_state_id;
    atomic_uint fault,state,qualification_done;
    atomic_uint_fast64_t heartbeat,io_heartbeat,tx_at,epoch;
    fd_usb_snapshot snapshot;
    unsigned first_fault_code,first_fault_pa_error,first_fault_state;
    uint64_t first_fault_operation_ms,first_fault_mic_age,first_fault_rx_age,first_fault_io_age;
    char first_fault_origin[64],first_fault_stream[16];
    trace_event events[EVENT_MAX];unsigned event_head,event_n,event_dropped;
    bool control_exit,key_pending,clear_pending,key_running,control_halt,key_result;
    uint64_t key_epoch,key_release,key_request,key_completed,clear_request,clear_completed;
    pa_threaded_mainloop *loop;pa_context *context;pa_stream *streams[STREAM_COUNT];pa_time_event *io_timer;
    pa_operation *drain;int drain_ack;uint64_t drain_started;
    struct { int ack; char name[40];uint64_t start; } operation;
    fd_voice_engine *engine;SRC_STATE *radio_input,*radio_output;
    uint64_t generation,turns,release,phase_at,rx_sequence,pending_key,pending_clear,qualification_release;
    bool modem_started,headset_started,permit_press,finishing,producer_finished,radio_output_ended,rf_clear_confirmed;
    pcm_queue capture,radio_capture,play;
    stream_metrics metrics[STREAM_COUNT];
    unsigned gaps;uint64_t submitted_rx,submitted_mic,rendered_rx,rendered_modem,converted_radio,reference_submitted;
    int16_t qualification_pcm[RATE*4];unsigned qualification_n,reference_cursor;
    bool qualification_waiting_loopback;
    uint64_t reference_next,reference_started,reference_synthesis_base,reference_underflow_base,reference_headset_base;
    bool qualification_rate_started;
    uint64_t rate_start_ms[2],rate_start_count[2],qualified_rate_window[2];
    uint64_t preparation_excluded_ms[2],preparation_excluded_frames[2];
    unsigned qualified_rate_hz[2];
    char source[512],sink[512],source_card[32],sink_card[32];unsigned sources,sinks;
    FILE *trace,*taps[4];unsigned trace_rows;uint64_t tap_samples[4],last_trace;
};
static uint64_t now_ms(void){struct timespec t;clock_gettime(CLOCK_MONOTONIC,&t);return(uint64_t)t.tv_sec*1000+t.tv_nsec/1000000;}
static void pause_ms(unsigned n){struct timespec t={n/1000,(long)(n%1000)*1000000};nanosleep(&t,NULL);}
static unsigned stream_index(fd_usb_audio *a,pa_stream *s){for(unsigned i=0;i<STREAM_COUNT;++i)if(a->streams[i]==s)return i;return STREAM_COUNT;}
static unsigned age(uint64_t now,uint64_t last){return now>=last?(unsigned)(now-last):0;}
static void io_lock(fd_usb_audio *a){pa_threaded_mainloop_lock(a->loop);}
static void io_unlock(fd_usb_audio *a){pa_threaded_mainloop_unlock(a->loop);}
static void event(fd_usb_audio *a,const char *name,const char *operation,unsigned stream,int status,int error,uint64_t duration){
    trace_event e={.ms=now_ms(),.duration=duration,.state=atomic_load(&a->state),.status=status,.error=error};
    snprintf(e.event,sizeof(e.event),"%s",name);snprintf(e.operation,sizeof(e.operation),"%s",operation?operation:"");
    snprintf(e.stream,sizeof(e.stream),"%s",stream<STREAM_COUNT?stream_names[stream]:"");
    pthread_mutex_lock(&a->events_mutex);
    if(a->event_n<EVENT_MAX){a->events[(a->event_head+a->event_n)%EVENT_MAX]=e;++a->event_n;}else ++a->event_dropped;
    pthread_mutex_unlock(&a->events_mutex);
}
static uint64_t request_clear(fd_usb_audio *a,bool halt){
    if(halt && a->c.control.inhibit)a->c.control.inhibit(a->c.control.context);
    atomic_fetch_add(&a->epoch,1);
    pthread_mutex_lock(&a->control_mutex);
    a->key_pending=false;a->clear_pending=true;a->control_halt|=halt;
    uint64_t request=++a->clear_request;pthread_cond_signal(&a->control_changed);
    pthread_mutex_unlock(&a->control_mutex);return request;
}
static void fail_at(fd_usb_audio *a,unsigned fault,const char *origin,unsigned stream,int error,uint64_t duration){
    unsigned expected=0;
    bool first=atomic_compare_exchange_strong(&a->fault,&expected,fault);
    if(first){
        uint64_t now=now_ms();pthread_mutex_lock(&a->mutex);
        a->first_fault_code=fault;a->first_fault_pa_error=error>0?(unsigned)error:0;a->first_fault_operation_ms=duration;a->first_fault_state=atomic_load(&a->state);
        a->first_fault_mic_age=age(now,atomic_load(&a->metrics[MIC].last));a->first_fault_rx_age=age(now,atomic_load(&a->metrics[RADIO_RX].last));
        a->first_fault_io_age=age(now,atomic_load(&a->io_heartbeat));
        snprintf(a->first_fault_origin,sizeof(a->first_fault_origin),"%s",origin);
        snprintf(a->first_fault_stream,sizeof(a->first_fault_stream),"%s",stream<STREAM_COUNT?stream_names[stream]:"");
        pthread_mutex_unlock(&a->mutex);atomic_store(&a->fault_record_ready,true);event(a,"first-fault",origin,stream,(int)fault,error,duration);
    }
    atomic_store(&a->armed,false);atomic_store(&a->stop,true);
    if(first)request_clear(a,true);
}
#define FAIL(a,code,origin) fail_at(a,code,origin,STREAM_COUNT,0,0)
const char *fd_usb_fault_text(unsigned n){
    static const char *const names[]={"","Stopped","Audio connection failed","Panel PTT unavailable","Radio status lost or unexpected TX","Radio protection","Audio backlog","Codec failure","TX not confirmed","Modem playback underrun/drain failed","TX time limit","Audio coordinator stalled","Connect one Shokz Loop110","Audio preparation failed","Audio operation timed out","Audio operation rejected","Active capture contains a hole","Required capture stopped","Audio read/write failed","Audio device moved","Radio control failed","Codec transition failed","RF appeared during audio evaluation","Capture sample delivery rate is not ready"};
    return n<sizeof(names)/sizeof(*names)?names[n]:"USB mode fault";
}
static void state(fd_usb_audio *a,fd_usb_state next){unsigned prior=atomic_exchange(&a->state,next);a->phase_at=now_ms();if(prior!=(unsigned)next)event(a,"state",NULL,STREAM_COUNT,(int)next,0,0);}
static void push(fd_usb_audio *a,pcm_queue *q,const int16_t *p,unsigned n){
    if(n>QMAX-q->n){FAIL(a,U_QUEUE,"pcm-queue-full");return;}
    for(unsigned i=0;i<n;++i)q->p[(q->head+q->n+i)%QMAX]=p[i];
    q->n+=n;
}
static unsigned pop(pcm_queue *q,int16_t *p,unsigned n){if(n>q->n)n=q->n;for(unsigned i=0;i<n;++i)p[i]=q->p[(q->head+i)%QMAX];q->head=(q->head+n)%QMAX;q->n-=n;return n;}
static unsigned convert(fd_usb_audio *a,SRC_STATE *src,const int16_t *input,unsigned count,int16_t *output,double ratio,bool final){
    float in[4096],out[4608];if(count>4096){FAIL(a,U_ENGINE,"resampler-input-size");return 0;}
    for(unsigned i=0;i<count;++i)in[i]=input[i]/32768.f;
    SRC_DATA data={.data_in=in,.data_out=out,.input_frames=count,.output_frames=4608,.src_ratio=ratio,.end_of_input=final};
    if(src_process(src,&data)||data.input_frames_used!=(long)count){FAIL(a,U_ENGINE,"resampler-processing");return 0;}
    src_float_to_short_array(out,output,(int)data.output_frames_gen);return(unsigned)data.output_frames_gen;
}
static void tap(fd_usb_audio *a,unsigned slot,const int16_t *p,unsigned n){
    if(!a->taps[slot]||a->tap_samples[slot]>=RATE*30)return;
    if(n>RATE*30-a->tap_samples[slot])n=RATE*30-a->tap_samples[slot];
    if(fwrite(p,2,n,a->taps[slot])!=n)FAIL(a,U_IO,"trace-pcm-write");
    a->tap_samples[slot]+=n;
}
static const char *device(fd_usb_audio *a,unsigned i){return i==MIC?a->source:i==HEADSET?a->sink:i==RADIO_RX?a->c.radio_source:a->c.radio_sink;}
/* These callbacks execute only on Pulse's dedicated I/O thread. They never
 * call a radio API, wait for a codec transition, or write evidence files. */
static void stream_state(pa_stream *s,void *p){
    fd_usb_audio *a=p;unsigned i=stream_index(a,s);if(i==STREAM_COUNT)return;
    pa_stream_state_t st=pa_stream_get_state(s);atomic_store(&a->metrics[i].state,st);
    if(st==PA_STREAM_READY){atomic_store(&a->metrics[i].suspended,pa_stream_is_suspended(s));const char *d=pa_stream_get_device_name(s);if(!d||strcmp(d,device(a,i)))fail_at(a,U_MOVED,"stream-device-changed",i,pa_context_errno(a->context),0);}
    else if(!PA_STREAM_IS_GOOD(st)&&!atomic_load(&a->stop))fail_at(a,U_DEVICE,"stream-state",i,pa_context_errno(a->context),0);
}
static void stream_suspended(pa_stream *s,void *p){
    fd_usb_audio *a=p;unsigned i=stream_index(a,s);if(i==STREAM_COUNT)return;
    int suspended=pa_stream_is_suspended(s);atomic_store(&a->metrics[i].suspended,suspended);
    event(a,"stream-suspended",NULL,i,suspended,pa_context_errno(a->context),0);
}
static void read_pcm(pa_stream *s,size_t requested,void *p){
    (void)requested;fd_usb_audio *a=p;unsigned i=stream_index(a,s);if(i>RADIO_RX)return;
    for(unsigned loops=0;loops<64;++loops){
        const void *data;size_t bytes;
        if(pa_stream_peek(s,&data,&bytes)<0){fail_at(a,U_IO,"capture-peek",i,pa_context_errno(a->context),0);return;}
        if(!bytes)return;
        unsigned st=atomic_load(&a->state);bool active=(i==MIC&&st==FD_USB_TX)||(i==RADIO_RX&&st==FD_USB_RX);
        uint64_t now=now_ms();
        if(data&&bytes%2==0){
            uint64_t old=atomic_exchange(&a->metrics[i].last,now),gap=age(now,old);
            if(old&&gap>atomic_load(&a->metrics[i].max_gap))atomic_store(&a->metrics[i].max_gap,gap);
            uint64_t total=atomic_fetch_add(&a->metrics[i].captured,bytes/2)+bytes/2;
            uint64_t empty=0;
            if(atomic_compare_exchange_strong(&a->metrics[i].first,&empty,now))atomic_store(&a->metrics[i].first_count,total);
        }
        if(bytes%2){fail_at(a,U_IO,"capture-frame-size",i,0,0);}
        else if(!data){
            atomic_fetch_add(&a->metrics[i].holes,1);
            if(active)fail_at(a,U_CAPTURE_HOLE,"active-capture-hole",i,0,0);
            else atomic_fetch_add(&a->metrics[i].discarded,bytes/2);
        }else if(active&&!atomic_load(&a->stop))push(a,i==MIC?&a->capture:&a->radio_capture,data,(unsigned)bytes/2);
        else atomic_fetch_add(&a->metrics[i].discarded,bytes/2);
        if(pa_stream_drop(s)<0){fail_at(a,U_IO,"capture-drop",i,pa_context_errno(a->context),0);return;}
        if(atomic_load(&a->stop))return;
    }
    FAIL(a,U_QUEUE,"capture-callback-budget");
}
static void write_pcm(pa_stream *s,size_t bytes,void *p){
    fd_usb_audio *a=p;unsigned i=stream_index(a,s);int16_t pcm[2048];
    if(i>=STREAM_COUNT)return;
    if(bytes>RATE*2){fail_at(a,U_QUEUE,"playback-request-size",i,0,0);return;}
    while(bytes&&!atomic_load(&a->stop)){
        unsigned n=(unsigned)bytes/2;if(n>2048)n=2048;if(!n){fail_at(a,U_IO,"playback-frame-size",i,0,0);return;}
        unsigned st=atomic_load(&a->state);
        if(i==MODEM){
            if(st!=FD_USB_TX&&st!=FD_USB_DRAIN)return;
            if(!a->modem_started&&a->play.n<RADIO_RATE/4&&!a->producer_finished)return;
            if(n>a->play.n)n=a->play.n;
            if(!n)return;
            pop(&a->play,pcm,n);a->modem_started=true;
            for(unsigned j=0;j<n;++j)pcm[j]/=4;
        }else{
            memset(pcm,0,n*2);
            if(st==FD_USB_RX){
                if(!a->headset_started&&a->play.n>=RATE/4)a->headset_started=true;
                if(a->headset_started&&pop(&a->play,pcm,n)<n&&(!atomic_load(&a->qualification_loopback)||atomic_load(&a->reference_input_active)))++a->gaps;
            }
        }
        if(pa_stream_write(s,pcm,n*2,NULL,0,PA_SEEK_RELATIVE)<0){fail_at(a,U_IO,"playback-write",i,pa_context_errno(a->context),0);return;}
        atomic_fetch_add(&a->metrics[i].captured,n);bytes-=n*2;
    }
}
static void underflow(pa_stream *s,void *p){
    fd_usb_audio *a=p;unsigned i=stream_index(a,s);if(i>=STREAM_COUNT)return;
    atomic_fetch_add(&a->metrics[i].underflows,1);
    bool payload=i==MODEM?a->modem_started:a->headset_started;
    if(!payload||atomic_load(&a->stop)||pa_stream_get_underflow_index(s)<=0||(i==MODEM&&a->drain))return;
    atomic_fetch_add(&a->metrics[i].payload_underflows,1);
    if(i==MODEM)fail_at(a,U_DRAIN,"modem-payload-underflow",MODEM,0,0);
}
static void context_state(pa_context *c,void *p){fd_usb_audio *a=p;atomic_store(&a->context_state_id,pa_context_get_state(c));if(!PA_CONTEXT_IS_GOOD(pa_context_get_state(c))&&!atomic_load(&a->stop))fail_at(a,U_DEVICE,"context-state",STREAM_COUNT,pa_context_errno(c),0);}
static void io_tick(pa_mainloop_api *api,pa_time_event *e,const struct timeval *tv,void *p){
    (void)tv;fd_usb_audio *a=p;atomic_store(&a->io_heartbeat,now_ms());
    struct timeval next;pa_gettimeofday(&next);pa_timeval_add(&next,10000);api->time_restart(e,&next);
}
static bool physical(pa_proplist *p,const char *driver){const char *bus=pa_proplist_gets(p,"device.bus"),*vendor=pa_proplist_gets(p,"device.vendor.id"),*product=pa_proplist_gets(p,"device.product.id");return driver&&!strncmp(driver,"module-alsa-",12)&&bus&&!strcmp(bus,"usb")&&vendor&&!strcmp(vendor,"3511")&&product&&!strcmp(product,"2b1e");}
static void source_info(pa_context *c,const pa_source_info *i,int end,void *p){
    (void)c;fd_usb_audio *a=p;if(end||!i||i->monitor_of_sink!=PA_INVALID_INDEX||!physical(i->proplist,i->driver))return;
    const char *card=pa_proplist_gets(i->proplist,"alsa.card");if(!card||strlen(i->name)>=sizeof(a->source)||strlen(card)>=sizeof(a->source_card))return;
    ++a->sources;strcpy(a->source,i->name);strcpy(a->source_card,card);
}
static void sink_info(pa_context *c,const pa_sink_info *i,int end,void *p){
    (void)c;fd_usb_audio *a=p;if(end||!i||!physical(i->proplist,i->driver))return;
    const char *card=pa_proplist_gets(i->proplist,"alsa.card");if(!card||strlen(i->name)>=sizeof(a->sink)||strlen(card)>=sizeof(a->sink_card))return;
    ++a->sinks;strcpy(a->sink,i->name);strcpy(a->sink_card,card);
}
static bool wait_operation_locked(fd_usb_audio *a,pa_operation *o,const char *name,unsigned stream,bool acknowledged){
    uint64_t start=now_ms();event(a,"operation-start",name,stream,0,0,0);
    if(!o){fail_at(a,U_PA_REJECT,name,stream,pa_context_errno(a->context),0);return false;}
    while(pa_operation_get_state(o)==PA_OPERATION_RUNNING&&!atomic_load(&a->stop)&&now_ms()-start<OP_TIMEOUT_MS){
        io_unlock(a);atomic_store(&a->heartbeat,now_ms());pause_ms(1);io_lock(a);
    }
    pa_operation_state_t result=pa_operation_get_state(o);bool complete=result==PA_OPERATION_DONE;
    int ack=a->operation.ack;uint64_t duration=now_ms()-start;
    if(!complete)pa_operation_cancel(o);
    pa_operation_unref(o);
    event(a,"operation-end",name,stream,complete?ack:-2,pa_context_errno(a->context),duration);
    if(atomic_load(&a->stop))return false;
    if(!complete){fail_at(a,U_PA_TIMEOUT,name,stream,pa_context_errno(a->context),duration);return false;}
    if(acknowledged&&ack!=1){fail_at(a,U_PA_REJECT,name,stream,pa_context_errno(a->context),duration);return false;}
    return true;
}
static void op_done(pa_stream *s,int success,void *p){(void)s;fd_usb_audio *a=p;a->operation.ack=success?1:-1;}
static bool stream_op(fd_usb_audio *a,unsigned i,bool flush,int cork){
    char name[40];snprintf(name,sizeof(name),"%s-%s",stream_names[i],flush?"flush":cork?"cork":"uncork");
    io_lock(a);a->operation.ack=0;
    if(!flush&&!cork)event(a,"uncork-suspended-before",name,i,pa_stream_is_suspended(a->streams[i]),0,0);
    pa_operation *o=flush?pa_stream_flush(a->streams[i],op_done,a):pa_stream_cork(a->streams[i],cork,op_done,a);
    bool ok=wait_operation_locked(a,o,name,i,true);
    if(!flush&&!cork)event(a,"uncork-suspended-after",name,i,pa_stream_is_suspended(a->streams[i]),0,0);
    io_unlock(a);return ok;
}
static void reset_queues(fd_usb_audio *a){
    io_lock(a);a->capture.n=a->capture.head=a->play.n=a->play.head=a->radio_capture.n=a->radio_capture.head=0;
    a->headset_started=a->modem_started=a->producer_finished=a->radio_output_ended=false;a->drain_ack=0;a->drain_started=0;
    if(a->drain){pa_operation_cancel(a->drain);pa_operation_unref(a->drain);a->drain=NULL;}
    pa_stream_set_write_callback(a->streams[MODEM],write_pcm,a);io_unlock(a);
}
static bool actual_inputs(fd_usb_audio *a,fd_usb_inputs *in);
/* An operation ACK and READY stream states do not establish that the server
 * has resumed sample delivery. A cold sink can pause both captures while it
 * wakes. Reacquire new valid frames after preparation, with RF still denied;
 * the unchanged 750 ms health guard applies after admission. */
static bool preparation_capture_ready(fd_usb_audio *a,fd_voice_direction d){
    uint64_t initial[2],begin=now_ms();
    io_lock(a);for(unsigned i=0;i<2;++i)initial[i]=atomic_load(&a->metrics[i].captured);io_unlock(a);
    event(a,"capture-resume-start","preparation",STREAM_COUNT,d,0,0);
    while(!atomic_load(&a->stop)&&now_ms()-begin<PREPARATION_CAPTURE_TIMEOUT_MS){
        atomic_store(&a->heartbeat,now_ms());
        fd_usb_inputs in={0};if(!actual_inputs(a,&in))return false;
        if(atomic_load(&a->qualification_running)&&in.pressed){FAIL(a,U_PTT,"panel-held-during-evaluation");return false;}
        if(d==FD_VOICE_TX&&in.rf_tx){FAIL(a,U_STATUS,"unexpected-tx-during-preparation");return false;}
        if(d==FD_VOICE_TX&&!atomic_load(&a->qualification_running)&&
           (!in.pressed||in.release!=a->release)){
            event(a,"capture-resume-cancelled","ptt-release",STREAM_COUNT,0,0,now_ms()-begin);
            return true; /* The caller cancels admission and restores RX. */
        }
        bool ready=true;uint64_t now=now_ms();
        for(unsigned i=0;i<2;++i)if(atomic_load(&a->metrics[i].state)!=PA_STREAM_READY||
            atomic_load(&a->metrics[i].captured)<=initial[i]||
            age(now,atomic_load(&a->metrics[i].last))>CAPTURE_TIMEOUT_MS)ready=false;
        if(ready){event(a,"capture-resume-ready","preparation",STREAM_COUNT,1,0,now_ms()-begin);return true;}
        pause_ms(1);
    }
    if(!atomic_load(&a->stop)){
        unsigned missing=MIC;uint64_t now=now_ms();
        for(unsigned i=0;i<2;++i)if(atomic_load(&a->metrics[i].state)!=PA_STREAM_READY||
            atomic_load(&a->metrics[i].captured)<=initial[i]||age(now,atomic_load(&a->metrics[i].last))>CAPTURE_TIMEOUT_MS){missing=i;break;}
        fail_at(a,U_CAPTURE_STALE,"capture-not-resumed-after-preparation",missing,0,now_ms()-begin);
    }
    return false;
}
static bool direction(fd_usb_audio *a,fd_voice_direction d){
    /* Only explicit preparation epochs are excluded from the RF-disabled
     * qualification rate window. Keep all active delivery gaps in that gate.
     * Snapshot explicit preparation time and frame totals under the I/O lock.
     * A pre-existing capture gap must stay in the measured rate window. */
    uint64_t before_ms[2],before_frames[2];
    bool exclude=atomic_load(&a->qualification_running)&&atomic_load(&a->rf_disabled);
    io_lock(a);uint64_t preparation_at=now_ms();for(unsigned i=0;i<2;++i){before_ms[i]=preparation_at;before_frames[i]=atomic_load(&a->metrics[i].captured);}io_unlock(a);
    event(a,"preparation-start","direction",STREAM_COUNT,d,0,0);
    reset_queues(a);
    if(!stream_op(a,MODEM,false,1)||!stream_op(a,MODEM,true,0)||!stream_op(a,HEADSET,true,0))return false;
    if(src_reset(a->radio_input)||src_reset(a->radio_output)){FAIL(a,U_TRANSITION,"transport-resampler-reset");return false;}
    uint64_t begin=now_ms();event(a,"codec-start","direction",STREAM_COUNT,d,0,0);
    bool ok=fd_voice_transition(a->engine,d,++a->generation);
    event(a,"codec-end","direction",STREAM_COUNT,ok,0,now_ms()-begin);
    if(!ok){FAIL(a,U_TRANSITION,"codec-direction");return false;}
    /* Uncork before RF admission. Automatic PA prebuffering holds an empty
     * modem stream until the accepted speech/modem prefill becomes available. */
    if(d==FD_VOICE_TX&&!stream_op(a,MODEM,false,0))return false;
    if(!preparation_capture_ready(a,d))return false;
    if(exclude){
        io_lock(a);for(unsigned i=0;i<2;++i){
            uint64_t last=atomic_load(&a->metrics[i].last);
            a->preparation_excluded_ms[i]+=last>before_ms[i]?last-before_ms[i]:0;
            a->preparation_excluded_frames[i]+=atomic_load(&a->metrics[i].captured)-before_frames[i];
        }io_unlock(a);
    }
    event(a,"preparation-complete","direction",STREAM_COUNT,d,0,0);
    return !atomic_load(&a->stop);
}
static void json_string(FILE *f,const char *s){fputc('"',f);for(;s&&*s;++s){unsigned char c=(unsigned char)*s;if(c=='"'||c=='\\'){fputc('\\',f);fputc(c,f);}else if(c>=32)fputc(c,f);}fputc('"',f);}
static void describe_streams(fd_usb_audio *a){
    if(!a->trace)return;
    for(unsigned i=0;i<STREAM_COUNT;++i){
        io_lock(a);const pa_buffer_attr *p=pa_stream_get_buffer_attr(a->streams[i]);pa_buffer_attr attr=p?*p:(pa_buffer_attr){0};
        const pa_sample_spec *s=pa_stream_get_sample_spec(a->streams[i]);pa_sample_spec spec=s?*s:(pa_sample_spec){0};
        const char *d=pa_stream_get_device_name(a->streams[i]);char name[512];snprintf(name,sizeof(name),"%s",d?d:"");io_unlock(a);
        fprintf(a->trace,"{\"kind\":\"stream\",\"ms\":%llu,\"stream\":\"%s\",\"device\":",(unsigned long long)now_ms(),stream_names[i]);json_string(a->trace,name);
        fprintf(a->trace,",\"rate\":%u,\"channels\":%u,\"maxlength\":%u,\"tlength\":%u,\"prebuf\":%u,\"minreq\":%u,\"fragsize\":%u}\n",spec.rate,spec.channels,attr.maxlength,attr.tlength,attr.prebuf,attr.minreq,attr.fragsize);
    }
    fflush(a->trace);
}
static bool prepare(fd_usb_audio *a){
    a->engine=fd_voice_create(RATE);if(!a->engine||atomic_load(&a->stop))return false;
    int error=0;a->radio_input=src_new(SRC_SINC_FASTEST,1,&error);a->radio_output=src_new(SRC_SINC_FASTEST,1,&error);if(!a->radio_input||!a->radio_output)return false;
    a->loop=pa_threaded_mainloop_new();a->context=a->loop?pa_context_new(pa_threaded_mainloop_get_api(a->loop),"X6200 USB FreeDV"):NULL;if(!a->context)return false;
    pa_context_set_state_callback(a->context,context_state,a);
    if(pa_context_connect(a->context,NULL,PA_CONTEXT_NOAUTOSPAWN,NULL)<0||pa_threaded_mainloop_start(a->loop)<0)return false;
    io_lock(a);uint64_t until=now_ms()+5000;
    while(pa_context_get_state(a->context)!=PA_CONTEXT_READY&&!atomic_load(&a->stop)&&now_ms()<until){io_unlock(a);atomic_store(&a->heartbeat,now_ms());pause_ms(1);io_lock(a);}
    if(pa_context_get_state(a->context)!=PA_CONTEXT_READY){io_unlock(a);return false;}
    pa_mainloop_api *api=pa_threaded_mainloop_get_api(a->loop);struct timeval next;pa_gettimeofday(&next);pa_timeval_add(&next,10000);a->io_timer=api->time_new(api,&next,io_tick,a);
    if(a->c.source&&a->c.sink){snprintf(a->source,sizeof(a->source),"%s",a->c.source);snprintf(a->sink,sizeof(a->sink),"%s",a->c.sink);}else{
        bool ok=wait_operation_locked(a,pa_context_get_source_info_list(a->context,source_info,a),"discover-sources",MIC,false)&&wait_operation_locked(a,pa_context_get_sink_info_list(a->context,sink_info,a),"discover-sinks",HEADSET,false);
        if(!ok||a->sources!=1||a->sinks!=1||strcmp(a->source_card,a->sink_card)){io_unlock(a);FAIL(a,U_IDENTITY,"endpoint-identity");return false;}
    }
    pa_sample_spec spec={PA_SAMPLE_S16LE,RATE,1};
    a->streams[MIC]=pa_stream_new(a->context,"USB microphone",&spec,NULL);a->streams[HEADSET]=pa_stream_new(a->context,"USB decoded speech",&spec,NULL);
    spec.rate=RADIO_RATE;a->streams[RADIO_RX]=pa_stream_new(a->context,"Radio demodulated audio",&spec,NULL);a->streams[MODEM]=pa_stream_new(a->context,"Radio modem audio",&spec,NULL);
    for(unsigned i=0;i<STREAM_COUNT;++i)if(!a->streams[i]){io_unlock(a);return false;}
    for(unsigned i=0;i<STREAM_COUNT;++i){pa_stream_set_state_callback(a->streams[i],stream_state,a);pa_stream_set_moved_callback(a->streams[i],stream_state,a);pa_stream_set_suspended_callback(a->streams[i],stream_suspended,a);}
    pa_stream_set_read_callback(a->streams[MIC],read_pcm,a);pa_stream_set_read_callback(a->streams[RADIO_RX],read_pcm,a);
    pa_stream_set_write_callback(a->streams[HEADSET],write_pcm,a);pa_stream_set_write_callback(a->streams[MODEM],write_pcm,a);
    pa_stream_set_underflow_callback(a->streams[HEADSET],underflow,a);pa_stream_set_underflow_callback(a->streams[MODEM],underflow,a);
    pa_stream_flags_t flags=PA_STREAM_DONT_MOVE|PA_STREAM_ADJUST_LATENCY|PA_STREAM_AUTO_TIMING_UPDATE;
    pa_buffer_attr attr={.maxlength=19200,.tlength=9600,.prebuf=0,.minreq=1920,.fragsize=1920};pa_cvolume volume;pa_cvolume_set(&volume,1,PA_VOLUME_NORM/2);
    bool ok=pa_stream_connect_record(a->streams[MIC],a->source,&attr,flags)>=0&&pa_stream_connect_playback(a->streams[HEADSET],a->sink,&attr,flags,&volume,NULL)>=0;
    attr=(pa_buffer_attr){.maxlength=17640,.tlength=8820,.prebuf=(uint32_t)-1,.minreq=1764,.fragsize=1764};pa_cvolume_set(&volume,1,PA_VOLUME_NORM);
    ok=ok&&pa_stream_connect_record(a->streams[RADIO_RX],a->c.radio_source,&attr,flags)>=0&&pa_stream_connect_playback(a->streams[MODEM],a->c.radio_sink,&attr,flags|PA_STREAM_START_CORKED,&volume,NULL)>=0;
    until=now_ms()+5000;bool ready=false;
    while(ok&&!ready&&!atomic_load(&a->stop)&&now_ms()<until){ready=true;for(unsigned i=0;i<STREAM_COUNT;++i)if(pa_stream_get_state(a->streams[i])!=PA_STREAM_READY)ready=false;io_unlock(a);atomic_store(&a->heartbeat,now_ms());pause_ms(1);io_lock(a);}
    io_unlock(a);if(!ready)return false;
    describe_streams(a);if(!direction(a,FD_VOICE_RX))return false;
    until=now_ms()+1500;
    while((!atomic_load(&a->metrics[MIC].first)||!atomic_load(&a->metrics[RADIO_RX].first))&&!atomic_load(&a->stop)&&now_ms()<until){atomic_store(&a->heartbeat,now_ms());pause_ms(1);}
    if(!atomic_load(&a->metrics[MIC].first)||!atomic_load(&a->metrics[RADIO_RX].first)){
        fail_at(a,U_CAPTURE_STALE,"initial-capture-readiness",!atomic_load(&a->metrics[MIC].first)?MIC:RADIO_RX,0,0);return false;
    }
    return !atomic_load(&a->stop);
}
static uint64_t request_key(fd_usb_audio *a,uint64_t release){
    pthread_mutex_lock(&a->control_mutex);uint64_t request=++a->key_request;
    a->key_epoch=atomic_load(&a->epoch);a->key_release=release;a->key_pending=true;a->key_result=false;
    pthread_cond_signal(&a->control_changed);pthread_mutex_unlock(&a->control_mutex);return request;
}
static void *control_run(void *p){
    fd_usb_audio *a=p;
    for(;;){
        pthread_mutex_lock(&a->control_mutex);
        while(!a->control_exit&&!a->clear_pending&&!a->key_pending)pthread_cond_wait(&a->control_changed,&a->control_mutex);
        if(a->control_exit&&!a->clear_pending&&!a->key_pending){pthread_mutex_unlock(&a->control_mutex);break;}
        if(a->clear_pending){
            bool halt=a->control_halt;uint64_t id=a->clear_request;a->clear_pending=false;a->key_pending=false;
            pthread_mutex_unlock(&a->control_mutex);uint64_t begin=now_ms();event(a,"control-start","clear",STREAM_COUNT,halt,0,0);
            a->c.control.clear(a->c.control.context,halt);atomic_store(&a->qualification_keyed,false);
            event(a,"control-end","clear",STREAM_COUNT,1,0,now_ms()-begin);
            pthread_mutex_lock(&a->control_mutex);a->clear_completed=id;pthread_mutex_unlock(&a->control_mutex);continue;
        }
        uint64_t id=a->key_request,epoch=a->key_epoch,release=a->key_release;a->key_pending=false;a->key_running=true;
        pthread_mutex_unlock(&a->control_mutex);uint64_t begin=now_ms();event(a,"control-start","key",STREAM_COUNT,0,0,0);
        bool disabled=atomic_load(&a->rf_disabled),ok=false;
        if(disabled){
            uint64_t until=begin+a->c.qualification_control_delay_ms;
            while(now_ms()<until&&!atomic_load(&a->stop)&&epoch==atomic_load(&a->epoch))pause_ms(5);
            ok=!atomic_load(&a->stop)&&epoch==atomic_load(&a->epoch);
            if(ok)atomic_store(&a->qualification_keyed,true);
        }else if(!atomic_load(&a->stop)&&epoch==atomic_load(&a->epoch))ok=a->c.control.key(a->c.control.context,release);
        if(atomic_load(&a->stop)||epoch!=atomic_load(&a->epoch)){
            ok=false;request_clear(a,atomic_load(&a->stop));
        }
        event(a,"control-end","key",STREAM_COUNT,ok,0,now_ms()-begin);
        pthread_mutex_lock(&a->control_mutex);a->key_completed=id;a->key_result=ok;a->key_running=false;pthread_mutex_unlock(&a->control_mutex);
    }
    return NULL;
}
static bool key_complete(fd_usb_audio *a,bool *ok){pthread_mutex_lock(&a->control_mutex);bool done=a->key_completed>=a->pending_key;*ok=a->key_result;pthread_mutex_unlock(&a->control_mutex);return done;}
static bool clear_complete(fd_usb_audio *a,uint64_t id){pthread_mutex_lock(&a->control_mutex);bool done=a->clear_completed>=id;pthread_mutex_unlock(&a->control_mutex);return done;}
static bool actual_inputs(fd_usb_audio *a,fd_usb_inputs *in){
    if(!a->c.control.observe(a->c.control.context,in)||!in->valid){FAIL(a,U_PTT,"panel-observation");return false;}
    if(!in->rf_valid){FAIL(a,U_STATUS,"radio-packet-stale");return false;}
    if(in->protection){FAIL(a,U_PROTECTION,"radio-protection");return false;}
    if(atomic_load(&a->rf_disabled)&&in->rf_tx){FAIL(a,U_QUALIFICATION,"actual-rf-tx-during-evaluation");return false;}
    return true;
}
static fd_usb_inputs effective_inputs(fd_usb_audio *a,fd_usb_inputs actual){
    if(!atomic_load(&a->qualification_running))return actual;
    fd_usb_inputs result=actual;unsigned st=atomic_load(&a->state);
    result.pressed=st==FD_USB_START||st==FD_USB_KEY_PENDING||(st==FD_USB_TX&&now_ms()-a->phase_at<1200);
    if(st==FD_USB_RX)result.pressed=!atomic_load(&a->qualification_loopback)&&now_ms()-a->phase_at>=250;
    if(st==FD_USB_TX&&!result.pressed)result.release=++a->qualification_release;
    else result.release=a->qualification_release;
    result.rf_tx=atomic_load(&a->qualification_keyed);return result;
}
static void drain_done(pa_stream *s,int success,void *p){(void)s;((fd_usb_audio *)p)->drain_ack=success?1:-1;}
static void process_audio(fd_usb_audio *a,unsigned st){
    int16_t pcm[4096],converted[4608];unsigned n=0;
    io_lock(a);n=pop(st==FD_USB_RX?&a->radio_capture:&a->capture,pcm,st==FD_USB_RX?441:480);io_unlock(a);
    bool reference=st==FD_USB_RX&&atomic_load(&a->qualification_loopback);
    if(n){
        if(st==FD_USB_RX){n=convert(a,a->radio_input,pcm,n,converted,(double)RATE/RADIO_RATE,false);memcpy(pcm,converted,n*2);a->converted_radio+=n;}
        else a->submitted_mic+=n;
        tap(a,st==FD_USB_RX?1:0,pcm,n);
        if(!reference){
            if(st==FD_USB_RX)a->submitted_rx+=n;
            if(fd_voice_submit(a->engine,a->generation,pcm,n)!=n){FAIL(a,U_QUEUE,"codec-input-queue");return;}
        }
    }
    if(reference&&now_ms()>=a->reference_next){
        unsigned total=a->qualification_n+RATE/2;
        n=total-a->reference_cursor;if(n>480)n=480;
        if(n){
            for(unsigned i=0;i<n;++i){unsigned at=a->reference_cursor+i;pcm[i]=at<a->qualification_n?a->qualification_pcm[at]:0;}
            if(fd_voice_submit(a->engine,a->generation,pcm,n)!=n){FAIL(a,U_QUEUE,"reference-input-queue");return;}
            a->reference_cursor+=n;a->reference_submitted+=n;a->reference_next+=10;
            if(a->reference_cursor>=a->qualification_n)atomic_store(&a->reference_input_active,false);
        }
    }
    io_lock(a);bool input_empty=!a->capture.n;unsigned play_n=a->play.n;io_unlock(a);
    if(st==FD_USB_DRAIN&&input_empty&&!a->finishing){if(!fd_voice_finish(a->engine,a->generation)){FAIL(a,U_ENGINE,"codec-finish");return;}a->finishing=true;}
    unsigned limit=st==FD_USB_RX?QMAX:RADIO_RATE/2;
    if(play_n>limit){FAIL(a,U_QUEUE,"render-queue-limit");return;}
    unsigned room=limit-play_n;if(room>4096)room=4096;
    if(room){
        n=fd_voice_receive(a->engine,a->generation,pcm,room);
        if(n){tap(a,st==FD_USB_RX?3:2,pcm,n);
            if(st!=FD_USB_RX&&atomic_load(&a->qualification_running)){
                if(n>RATE*4-a->qualification_n){FAIL(a,U_QUEUE,"qualification-recording-bound");return;}
                memcpy(a->qualification_pcm+a->qualification_n,pcm,n*2);a->qualification_n+=n;
            }
            if(st!=FD_USB_RX){n=convert(a,a->radio_output,pcm,n,converted,(double)RADIO_RATE/RATE,false);memcpy(pcm,converted,n*2);a->rendered_modem+=n;}else a->rendered_rx+=n;
            io_lock(a);push(a,&a->play,pcm,n);io_unlock(a);
        }
    }
    fd_voice_stats stats=fd_voice_observe(a->engine);
    if(stats.failures||stats.cancelled){FAIL(a,U_ENGINE,"codec-worker");return;}
    if(stats.finished&&!stats.output_depth&&!a->radio_output_ended&&play_n<RADIO_RATE/2-128){
        n=convert(a,a->radio_output,NULL,0,converted,(double)RADIO_RATE/RATE,true);io_lock(a);push(a,&a->play,converted,n);io_unlock(a);a->radio_output_ended=true;
    }
    io_lock(a);a->producer_finished=stats.finished&&!stats.output_depth&&a->radio_output_ended;
    if(st!=FD_USB_RX&&!a->drain){size_t writable=pa_stream_writable_size(a->streams[MODEM]);if(writable==(size_t)-1)fail_at(a,U_IO,"modem-writable-size",MODEM,pa_context_errno(a->context),0);else if(writable)write_pcm(a->streams[MODEM],writable,a);}
    if(stats.input_depth+a->capture.n+(a->radio_capture.n*RATE+RADIO_RATE-1)/RADIO_RATE>=QMAX||stats.output_depth+a->play.n>QMAX*2)FAIL(a,U_QUEUE,"aggregate-queue-limit");
    if(st==FD_USB_DRAIN&&a->producer_finished&&!a->play.n&&!a->drain){
        pa_stream_set_write_callback(a->streams[MODEM],NULL,NULL);a->drain_ack=0;
        a->drain_started=now_ms();event(a,"operation-start","modem-drain",MODEM,0,0,0);a->drain=pa_stream_drain(a->streams[MODEM],drain_done,a);
        if(!a->drain)fail_at(a,U_PA_REJECT,"modem-drain",MODEM,pa_context_errno(a->context),0);
    }
    io_unlock(a);
}
static unsigned measured_rate(fd_usb_audio *a,unsigned stream,uint64_t *window){
    if(atomic_load(&a->qualification_complete)){*window=a->qualified_rate_window[stream];return a->qualified_rate_hz[stream];}
    uint64_t first=a->qualification_rate_started?a->rate_start_ms[stream]:atomic_load(&a->metrics[stream].first),last=atomic_load(&a->metrics[stream].last);
    uint64_t count=atomic_load(&a->metrics[stream].captured),initial=a->qualification_rate_started?a->rate_start_count[stream]:atomic_load(&a->metrics[stream].first_count);
    *window=first&&last>=first?last-first:0;
    uint64_t frames=count>=initial?count-initial:0;
    if(a->qualification_rate_started){
        *window=*window>=a->preparation_excluded_ms[stream]?*window-a->preparation_excluded_ms[stream]:0;
        frames=frames>=a->preparation_excluded_frames[stream]?frames-a->preparation_excluded_frames[stream]:0;
    }
    return *window?(unsigned)(frames*1000/(*window)):0;
}
static bool finish_qualification(fd_usb_audio *a){
    uint64_t mw=0,rw=0;unsigned mr=measured_rate(a,MIC,&mw),rr=measured_rate(a,RADIO_RX,&rw);
    if(mw<3000||rw<3000||mr<RATE*95/100||mr>RATE*105/100){FAIL(a,U_CAPTURE_RATE,"microphone-measured-rate");return false;}
    if(rr<RADIO_RATE*95/100||rr>RADIO_RATE*105/100){FAIL(a,U_CAPTURE_RATE,"radio-rx-measured-rate");return false;}
    a->qualified_rate_hz[MIC]=mr;a->qualified_rate_hz[RADIO_RX]=rr;a->qualified_rate_window[MIC]=mw;a->qualified_rate_window[RADIO_RX]=rw;
    atomic_store(&a->qualification_running,false);atomic_store(&a->qualification_complete,true);atomic_store(&a->armed,false);
    event(a,"qualification-complete",NULL,STREAM_COUNT,1,0,0);return true;
}
static void publish(fd_usb_audio *a,fd_usb_inputs in){
    uint64_t now=now_ms();fd_usb_snapshot s={.state=atomic_load(&a->state),.fault=atomic_load(&a->fault),.generation=a->generation,.turns=a->turns,.heartbeat_ms=atomic_load(&a->heartbeat),
        .armed=atomic_load(&a->armed),.pressed=in.pressed,.rf_valid=in.rf_valid,.rf_tx=in.rf_tx,.stopped=atomic_load(&a->stopped),
        .rf_disabled=atomic_load(&a->rf_disabled),.qualification_complete=atomic_load(&a->qualification_complete),.qualification_running=atomic_load(&a->qualification_running),
        .qualification_target=a->c.qualification_turns,.qualification_turns=atomic_load(&a->qualification_done),.qualification_loopback=atomic_load(&a->qualification_loopback),.rf_clear_confirmed=a->rf_clear_confirmed,
        .mic_age_ms=age(now,atomic_load(&a->metrics[MIC].last)),.rx_age_ms=age(now,atomic_load(&a->metrics[RADIO_RX].last)),
        .mic_captured=atomic_load(&a->metrics[MIC].captured),.rx_captured=atomic_load(&a->metrics[RADIO_RX].captured),
        .mic_discarded=atomic_load(&a->metrics[MIC].discarded),.rx_discarded=atomic_load(&a->metrics[RADIO_RX].discarded),
        .mic_max_gap_ms=atomic_load(&a->metrics[MIC].max_gap),.rx_max_gap_ms=atomic_load(&a->metrics[RADIO_RX].max_gap),
        .mic_holes=atomic_load(&a->metrics[MIC].holes),.rx_holes=atomic_load(&a->metrics[RADIO_RX].holes),
        .headset_underflows=atomic_load(&a->metrics[HEADSET].underflows),.modem_underflows=atomic_load(&a->metrics[MODEM].payload_underflows),.engine=fd_voice_observe(a->engine)};
    s.mic_rate_hz=measured_rate(a,MIC,&s.mic_rate_window_ms);s.rx_rate_hz=measured_rate(a,RADIO_RX,&s.rx_rate_window_ms);
    if(a->loop){io_lock(a);s.input_queue=a->capture.n+(a->radio_capture.n*RATE+RADIO_RATE-1)/RADIO_RATE;s.output_queue=a->play.n;s.gaps=a->gaps;io_unlock(a);}
    s.holes=s.mic_holes+s.rx_holes;s.underflows=s.headset_underflows+s.modem_underflows;
    pthread_mutex_lock(&a->mutex);uint64_t first_fault_io_age=a->first_fault_io_age;s.first_fault_code=a->first_fault_code;s.first_fault_pa_error=a->first_fault_pa_error;s.first_fault_operation_ms=a->first_fault_operation_ms;s.first_fault_mic_age_ms=a->first_fault_mic_age;s.first_fault_rx_age_ms=a->first_fault_rx_age;
    snprintf(s.first_fault_origin,sizeof(s.first_fault_origin),"%s",a->first_fault_origin);snprintf(s.first_fault_stream,sizeof(s.first_fault_stream),"%s",a->first_fault_stream);a->snapshot=s;pthread_mutex_unlock(&a->mutex);
    trace_event events[EVENT_MAX];unsigned count;
    pthread_mutex_lock(&a->events_mutex);count=a->event_n;for(unsigned i=0;i<count;++i)events[i]=a->events[(a->event_head+i)%EVENT_MAX];a->event_head=(a->event_head+count)%EVENT_MAX;a->event_n=0;pthread_mutex_unlock(&a->events_mutex);
    if(a->trace){
        for(unsigned i=0;i<count&&a->trace_rows<7000;++i){trace_event *e=&events[i];++a->trace_rows;
            fprintf(a->trace,"{\"kind\":\"event\",\"ms\":%llu,\"state\":%u,\"event\":\"%s\",\"operation\":\"%s\",\"stream\":\"%s\",\"status\":%d,\"pa_errno\":%d,\"duration_ms\":%llu}\n",(unsigned long long)e->ms,e->state,e->event,e->operation,e->stream,e->status,e->error,(unsigned long long)e->duration);
        }
        if((s.stopped||now-a->last_trace>=100)&&a->trace_rows<7000){a->last_trace=now;++a->trace_rows;
            fprintf(a->trace,"{\"kind\":\"sample\",\"ms\":%llu,\"state\":%u,\"fault\":%u,\"generation\":%llu,\"turns\":%llu,\"rf_valid\":%s,\"rf_tx\":%s,\"ptt\":%s,\"capture\":%u,\"playback\":%u,\"engine_in\":%u,\"engine_out\":%u,\"eoo\":%llu,\"sync\":%s,\"gaps\":%u,\"underflows\":%u,\"rf_disabled\":%s,\"qualification_running\":%s,\"qualification_complete\":%s,\"qualification_loopback\":%s,\"qualification_target\":%u,\"qualification_turns\":%u,\"rf_clear_confirmed\":%s,\"mic_age_ms\":%llu,\"rx_age_ms\":%llu,\"mic_rate_hz\":%u,\"rx_rate_hz\":%u,\"mic_rate_window_ms\":%llu,\"rx_rate_window_ms\":%llu,\"mic_captured\":%llu,\"rx_captured\":%llu,\"mic_discarded\":%llu,\"rx_discarded\":%llu,\"mic_holes\":%u,\"rx_holes\":%u,\"headset_underflows\":%u,\"modem_underflows\":%u,\"submitted_rx\":%llu,\"submitted_mic\":%llu,\"rendered_rx\":%llu,\"rendered_modem\":%llu,\"io_heartbeat_age_ms\":%u,\"mic_suspended\":%d,\"rx_suspended\":%d,\"headset_suspended\":%d,\"modem_suspended\":%d,\"preparation_excluded_ms\":[%llu,%llu],\"preparation_excluded_frames\":[%llu,%llu],\"first_fault\":{\"code\":%u,\"origin\":\"%s\",\"stream\":\"%s\",\"pa_errno\":%u,\"operation_ms\":%llu,\"mic_age_ms\":%llu,\"rx_age_ms\":%llu,\"io_heartbeat_age_ms\":%llu}}\n",
                (unsigned long long)now,s.state,s.fault,(unsigned long long)s.generation,(unsigned long long)s.turns,in.rf_valid?"true":"false",in.rf_tx?"true":"false",in.pressed?"true":"false",s.input_queue,s.output_queue,s.engine.input_depth,s.engine.output_depth,(unsigned long long)s.engine.eoo_count,s.engine.synchronized?"true":"false",s.gaps,s.underflows,s.rf_disabled?"true":"false",s.qualification_running?"true":"false",s.qualification_complete?"true":"false",s.qualification_loopback?"true":"false",s.qualification_target,s.qualification_turns,s.rf_clear_confirmed?"true":"false",(unsigned long long)s.mic_age_ms,(unsigned long long)s.rx_age_ms,s.mic_rate_hz,s.rx_rate_hz,(unsigned long long)s.mic_rate_window_ms,(unsigned long long)s.rx_rate_window_ms,(unsigned long long)s.mic_captured,(unsigned long long)s.rx_captured,(unsigned long long)s.mic_discarded,(unsigned long long)s.rx_discarded,s.mic_holes,s.rx_holes,s.headset_underflows,s.modem_underflows,(unsigned long long)a->submitted_rx,(unsigned long long)a->submitted_mic,(unsigned long long)a->rendered_rx,(unsigned long long)a->rendered_modem,age(now,atomic_load(&a->io_heartbeat)),atomic_load(&a->metrics[MIC].suspended),atomic_load(&a->metrics[RADIO_RX].suspended),atomic_load(&a->metrics[HEADSET].suspended),atomic_load(&a->metrics[MODEM].suspended),(unsigned long long)a->preparation_excluded_ms[MIC],(unsigned long long)a->preparation_excluded_ms[RADIO_RX],(unsigned long long)a->preparation_excluded_frames[MIC],(unsigned long long)a->preparation_excluded_frames[RADIO_RX],s.first_fault_code,s.first_fault_origin,s.first_fault_stream,s.first_fault_pa_error,(unsigned long long)s.first_fault_operation_ms,(unsigned long long)s.first_fault_mic_age_ms,(unsigned long long)s.first_fault_rx_age_ms,(unsigned long long)first_fault_io_age);
        }
        fflush(a->trace);
    }
}
static void *run(void *p){
    fd_usb_audio *a=p;fd_usb_inputs actual={0};
    if(a->c.trace_prefix){
        char path[256];snprintf(path,sizeof(path),"%s.jsonl",a->c.trace_prefix);a->trace=fopen(path,"wx");
        static const char *const names[]={"mic","radio-rx","modem","decoded"};for(unsigned i=0;i<4;++i){snprintf(path,sizeof(path),"%s-%s.raw",a->c.trace_prefix,names[i]);a->taps[i]=fopen(path,"wbx");}
        if(a->trace)fprintf(a->trace,"{\"kind\":\"session\",\"schema\":\"x6200.usb-audio.v2\",\"rf_disabled\":%s,\"qualification_target\":%u,\"operation_timeout_ms\":%u,\"capture_timeout_ms\":%u,\"preparation_capture_timeout_ms\":%u}\n",atomic_load(&a->rf_disabled)?"true":"false",a->c.qualification_turns,OP_TIMEOUT_MS,CAPTURE_TIMEOUT_MS,PREPARATION_CAPTURE_TIMEOUT_MS);
    }
    if(!prepare(a)){if(!atomic_load(&a->fault))FAIL(a,U_PREPARE,"initial-audio-preparation");goto done;}
    a->permit_press=false;a->rf_clear_confirmed=true;state(a,FD_USB_RX);
    while(!atomic_load(&a->stop)){
        atomic_store(&a->heartbeat,now_ms());if(!actual_inputs(a,&actual))break;
        if(atomic_load(&a->qualification_running)&&actual.pressed){FAIL(a,U_PTT,"panel-held-during-evaluation");break;}
        if(atomic_exchange(&a->qualification_requested,false)){
            if(atomic_load(&a->state)!=FD_USB_RX||!atomic_load(&a->rf_disabled)||actual.pressed||actual.rf_tx){FAIL(a,U_PREPARE,"evaluation-start-state");break;}
            atomic_store(&a->qualification_done,0);atomic_store(&a->qualification_complete,false);
            io_lock(a);for(unsigned i=0;i<2;++i){a->rate_start_ms[i]=atomic_load(&a->metrics[i].last);a->rate_start_count[i]=atomic_load(&a->metrics[i].captured);a->preparation_excluded_ms[i]=a->preparation_excluded_frames[i]=0;}io_unlock(a);
            a->qualification_rate_started=true;atomic_store(&a->qualification_running,true);a->permit_press=true;state(a,FD_USB_RX);event(a,"qualification-start",NULL,STREAM_COUNT,0,0,0);
        }
        fd_usb_inputs in=effective_inputs(a,actual);unsigned st=atomic_load(&a->state);uint64_t now=now_ms();
        unsigned required=st==FD_USB_RX?RADIO_RX:MIC;
        if((st==FD_USB_RX||st==FD_USB_TX||st==FD_USB_KEY_PENDING)&&age(now,atomic_load(&a->metrics[required].last))>CAPTURE_TIMEOUT_MS){fail_at(a,U_CAPTURE_STALE,"required-capture-age",required,0,0);break;}
        if(st==FD_USB_RX){
            if(in.rf_tx){FAIL(a,U_STATUS,"unexpected-tx-in-rx");break;}
            if(!in.pressed)a->permit_press=true;
            bool admitted=atomic_load(&a->qualification_running)||atomic_load(&a->armed);
            if(in.pressed&&a->permit_press&&admitted){
                a->permit_press=false;a->release=in.release;state(a,FD_USB_START);a->rf_clear_confirmed=false;
                if(atomic_load(&a->qualification_running))a->qualification_n=0;
                if(!direction(a,FD_VOICE_TX))break;
                if(!actual_inputs(a,&actual))break;
                if(actual.rf_tx){FAIL(a,U_STATUS,"unexpected-tx-during-preparation");break;}
                in=effective_inputs(a,actual);
                if(!in.pressed||in.release!=a->release){a->pending_clear=request_clear(a,false);a->rx_sequence=actual.sequence;state(a,FD_USB_UNKEY);}
                else{
                    if(atomic_load(&a->metrics[MIC].state)!=PA_STREAM_READY||age(now_ms(),atomic_load(&a->metrics[MIC].last))>CAPTURE_TIMEOUT_MS){fail_at(a,U_CAPTURE_STALE,"microphone-not-ready-before-key",MIC,0,0);break;}
                    atomic_store(&a->tx_at,now_ms());state(a,FD_USB_KEY_PENDING);a->pending_key=request_key(a,a->release);
                }
            }
        }else if(st==FD_USB_KEY_PENDING){
            bool ok=false,done_key=key_complete(a,&ok);
            if(!in.pressed||in.release!=a->release){a->pending_clear=request_clear(a,false);a->rx_sequence=actual.sequence;state(a,FD_USB_UNKEY);}
            else if(done_key&&!ok){FAIL(a,U_CONTROL,"key-request-failed");break;}
            else if(done_key&&in.rf_tx){state(a,FD_USB_TX);}
            else if(now-a->phase_at>2000){FAIL(a,U_KEY,"tx-acknowledgement-timeout");break;}
        }else if(st==FD_USB_TX){
            if(!in.rf_tx){FAIL(a,U_STATUS,"tx-status-disappeared");break;}
            if(!in.pressed||in.release!=a->release){state(a,FD_USB_DRAIN);a->finishing=false;}
        }
        st=atomic_load(&a->state);
        if(st==FD_USB_RX||st==FD_USB_TX||st==FD_USB_DRAIN)process_audio(a,st);
        if(st==FD_USB_DRAIN){
            io_lock(a);int ack=a->drain_ack;io_unlock(a);
            if(now_ms()-a->phase_at>3000){FAIL(a,U_DRAIN,"modem-drain-timeout");break;}
            if(ack<0){FAIL(a,U_DRAIN,"modem-drain-rejected");break;}
            if(ack>0){
                event(a,"operation-end","modem-drain",MODEM,1,0,now_ms()-a->drain_started);a->pending_clear=request_clear(a,false);a->rx_sequence=actual.sequence;state(a,FD_USB_UNKEY);
                ++a->turns;if(atomic_load(&a->qualification_running))a->qualification_waiting_loopback=true;
            }
        }
        st=atomic_load(&a->state);
        if(st==FD_USB_UNKEY){
            if(clear_complete(a,a->pending_clear)){
                if(!actual_inputs(a,&actual))break;
                a->rx_sequence=actual.sequence;state(a,FD_USB_RX_READY);
                if(!direction(a,FD_VOICE_RX))break;
                /* Keep the post-OFF sequence baseline, but allow the complete
                 * RX acknowledgement window after bounded audio recovery. */
                if(!actual_inputs(a,&actual))break;
                a->phase_at=now_ms();
            }else if(now_ms()-a->phase_at>2000){FAIL(a,U_CONTROL,"clear-request-timeout");break;}
        }
        if(atomic_load(&a->state)==FD_USB_RX_READY){
            if(!actual.rf_tx&&actual.sequence>a->rx_sequence){
                a->rf_clear_confirmed=true;atomic_store(&a->tx_at,0);state(a,FD_USB_RX);a->permit_press=false;
                if(a->qualification_waiting_loopback){
                    a->qualification_waiting_loopback=false;a->reference_cursor=0;a->reference_next=a->reference_started=now_ms();
                    fd_voice_stats base=fd_voice_observe(a->engine);a->reference_synthesis_base=base.synthesis_blocks;
                    a->reference_underflow_base=atomic_load(&a->metrics[HEADSET].payload_underflows);
                    a->reference_headset_base=atomic_load(&a->metrics[HEADSET].captured);
                    atomic_store(&a->reference_input_active,true);atomic_store(&a->qualification_loopback,true);
                    event(a,"reference-rx-start","recorded-modem",STREAM_COUNT,1,0,0);
                }
            }else if(now_ms()-a->phase_at>1500){FAIL(a,U_STATUS,"fresh-rx-acknowledgement-timeout");break;}
        }
        if(atomic_load(&a->qualification_loopback)){
            uint64_t elapsed=now_ms()-a->reference_started;
            unsigned total=a->qualification_n+RATE/2;
            if(a->reference_cursor>=total&&elapsed>(uint64_t)total*1000/RATE+750){
                fd_voice_stats stats=fd_voice_observe(a->engine);io_lock(a);unsigned queued=a->play.n;io_unlock(a);
                if(!stats.input_depth&&!stats.output_depth&&!queued){
                    if(stats.synthesis_blocks<=a->reference_synthesis_base||atomic_load(&a->metrics[HEADSET].captured)<=a->reference_headset_base){FAIL(a,U_ENGINE,"evaluation-no-decoded-speech");break;}
                    if(atomic_load(&a->metrics[HEADSET].payload_underflows)>a->reference_underflow_base){FAIL(a,U_DRAIN,"evaluation-headset-payload-underflow");break;}
                    atomic_store(&a->qualification_loopback,false);atomic_fetch_add(&a->qualification_done,1);state(a,FD_USB_RX);
                    event(a,"reference-rx-complete","recorded-modem",STREAM_COUNT,1,0,elapsed);
                    if(atomic_load(&a->qualification_done)>=a->c.qualification_turns&&!finish_qualification(a))break;
                }
            }
            if(elapsed>6000){FAIL(a,U_ENGINE,"evaluation-reference-timeout");break;}
        }
        publish(a,actual);pause_ms(1);
    }
done:
    atomic_store(&a->armed,false);a->pending_clear=request_clear(a,true);if(a->engine)fd_voice_cancel(a->engine);
    /* A transport callback may be blocked in the kernel. Keep the object and
     * its callbacks alive until OFF has completed; never label that RF-off. */
    while(!clear_complete(a,a->pending_clear)){atomic_store(&a->heartbeat,now_ms());publish(a,actual);pause_ms(5);}
    (void)a->c.control.observe(a->c.control.context,&actual);
    uint64_t until=now_ms()+1500,sequence=actual.sequence;a->rf_clear_confirmed=false;
    do{
        if(a->c.control.observe(a->c.control.context,&actual)&&actual.rf_valid&&!actual.rf_tx&&actual.sequence>sequence){a->rf_clear_confirmed=true;break;}
        atomic_store(&a->heartbeat,now_ms());pause_ms(5);
    }while(now_ms()<until);
    pthread_mutex_lock(&a->control_mutex);a->control_exit=true;pthread_cond_signal(&a->control_changed);pthread_mutex_unlock(&a->control_mutex);pthread_join(a->control_worker,NULL);
    if(a->loop){
        io_lock(a);if(a->drain){pa_operation_cancel(a->drain);pa_operation_unref(a->drain);a->drain=NULL;}
        if(a->io_timer){pa_threaded_mainloop_get_api(a->loop)->time_free(a->io_timer);a->io_timer=NULL;}
        for(unsigned i=0;i<STREAM_COUNT;++i)if(a->streams[i]){pa_stream_set_state_callback(a->streams[i],NULL,NULL);pa_stream_set_moved_callback(a->streams[i],NULL,NULL);pa_stream_set_suspended_callback(a->streams[i],NULL,NULL);pa_stream_set_read_callback(a->streams[i],NULL,NULL);pa_stream_set_write_callback(a->streams[i],NULL,NULL);pa_stream_set_underflow_callback(a->streams[i],NULL,NULL);pa_stream_disconnect(a->streams[i]);pa_stream_unref(a->streams[i]);a->streams[i]=NULL;}
        if(a->context){pa_context_set_state_callback(a->context,NULL,NULL);pa_context_disconnect(a->context);pa_context_unref(a->context);a->context=NULL;}
        io_unlock(a);pa_threaded_mainloop_stop(a->loop);
    }
    state(a,atomic_load(&a->fault)>U_STOP?FD_USB_FAULT:FD_USB_DONE);atomic_store(&a->stopped,true);publish(a,actual);
    fd_voice_destroy(a->engine);a->engine=NULL;if(a->radio_input)src_delete(a->radio_input);if(a->radio_output)src_delete(a->radio_output);
    if(a->loop){pa_threaded_mainloop_free(a->loop);a->loop=NULL;}
    if(a->trace)fclose(a->trace);
    for(unsigned i=0;i<4;++i)if(a->taps[i])fclose(a->taps[i]);
    return NULL;
}
static void fault_sidecar(fd_usb_audio *a){
    if(!a->c.trace_prefix)return;
    char path[256],temporary[272];snprintf(path,sizeof(path),"%s-fault.json",a->c.trace_prefix);snprintf(temporary,sizeof(temporary),"%s.tmp",path);
    FILE *f=fopen(temporary,"wx");if(!f)return;
    /* The release/acquire fault_record_ready fence makes these first-fault
     * fields immutable here. Storage I/O holds no shared runtime mutex. */
    fprintf(f,"{\"kind\":\"first-fault\",\"ms\":%llu,\"state\":%u,\"rf_disabled\":%s,\"first_fault\":{\"code\":%u,\"origin\":\"%s\",\"stream\":\"%s\",\"pa_errno\":%u,\"operation_ms\":%llu,\"mic_age_ms\":%llu,\"rx_age_ms\":%llu,\"io_heartbeat_age_ms\":%llu},\"context_state\":%d,\"mic_state\":%d,\"rx_state\":%d,\"headset_state\":%d,\"modem_state\":%d,\"mic_suspended\":%d,\"rx_suspended\":%d,\"headset_suspended\":%d,\"modem_suspended\":%d,\"mic_holes\":%u,\"rx_holes\":%u,\"mic_captured\":%llu,\"rx_captured\":%llu}\n",
        (unsigned long long)now_ms(),a->first_fault_state,atomic_load(&a->rf_disabled)?"true":"false",a->first_fault_code,a->first_fault_origin,a->first_fault_stream,a->first_fault_pa_error,(unsigned long long)a->first_fault_operation_ms,(unsigned long long)a->first_fault_mic_age,(unsigned long long)a->first_fault_rx_age,(unsigned long long)a->first_fault_io_age,atomic_load(&a->context_state_id),atomic_load(&a->metrics[MIC].state),atomic_load(&a->metrics[RADIO_RX].state),atomic_load(&a->metrics[HEADSET].state),atomic_load(&a->metrics[MODEM].state),atomic_load(&a->metrics[MIC].suspended),atomic_load(&a->metrics[RADIO_RX].suspended),atomic_load(&a->metrics[HEADSET].suspended),atomic_load(&a->metrics[MODEM].suspended),atomic_load(&a->metrics[MIC].holes),atomic_load(&a->metrics[RADIO_RX].holes),(unsigned long long)atomic_load(&a->metrics[MIC].captured),(unsigned long long)atomic_load(&a->metrics[RADIO_RX].captured));
    bool written=!ferror(f);if(fclose(f)!=0)written=false;
    if(written)(void)link(temporary,path); /* Atomic publication, never replace an older record. */
    (void)unlink(temporary);
}
static void *watch(void *p){
    fd_usb_audio *a=p;bool recorded=false;
    while(!atomic_load(&a->stopped)){
        uint64_t now=now_ms(),tx=atomic_load(&a->tx_at),beat=atomic_load(&a->heartbeat),io=atomic_load(&a->io_heartbeat);
        if(!atomic_load(&a->stop)){
            if(tx&&now>=tx&&now-tx>20000)FAIL(a,U_TIMEOUT,"turn-deadline");
            if(tx&&now>=beat&&now-beat>500)FAIL(a,U_STALL,"coordinator-heartbeat");
            if(tx&&now>=io&&now-io>500)FAIL(a,U_STALL,"pulse-io-heartbeat");
        }
        if(!recorded&&atomic_load(&a->fault_record_ready)){fault_sidecar(a);recorded=true;}
        pause_ms(20);
    }
    if(!recorded&&atomic_load(&a->fault_record_ready))fault_sidecar(a);
    return NULL;
}
fd_usb_audio *fd_usb_audio_open(const fd_usb_config *c){
    if(!c||!c->control.observe||!c->control.key||!c->control.clear)return NULL;
    fd_usb_audio *a=calloc(1,sizeof(*a));if(!a)return NULL;a->c=*c;
    pthread_mutex_init(&a->mutex,NULL);pthread_mutex_init(&a->events_mutex,NULL);pthread_mutex_init(&a->control_mutex,NULL);pthread_cond_init(&a->control_changed,NULL);
    atomic_store(&a->heartbeat,now_ms());atomic_store(&a->io_heartbeat,now_ms());atomic_store(&a->rf_disabled,c->rf_disabled);
    if(pthread_create(&a->control_worker,NULL,control_run,a)){free(a);return NULL;}
    if(pthread_create(&a->worker,NULL,run,a)){pthread_mutex_lock(&a->control_mutex);a->control_exit=true;pthread_cond_signal(&a->control_changed);pthread_mutex_unlock(&a->control_mutex);pthread_join(a->control_worker,NULL);free(a);return NULL;}
    if(pthread_create(&a->watch,NULL,watch,a)){fd_usb_audio_stop(a);pthread_join(a->worker,NULL);free(a);return NULL;}
    return a;
}
void fd_usb_audio_arm(fd_usb_audio *a,bool armed){if(a&&!atomic_load(&a->stop)&&!atomic_load(&a->rf_disabled))atomic_store(&a->armed,armed);}
bool fd_usb_audio_start_qualification(fd_usb_audio *a){
    if(!a||atomic_load(&a->stop)||!a->c.qualification_turns||!atomic_load(&a->rf_disabled)||atomic_load(&a->qualification_running))return false;
    fd_usb_snapshot s=fd_usb_audio_snapshot(a);if(s.state!=FD_USB_RX||s.pressed||!s.rf_valid||s.rf_tx)return false;
    atomic_store(&a->qualification_requested,true);return true;
}
bool fd_usb_audio_enable_rf(fd_usb_audio *a){
    if(!a||atomic_load(&a->stop))return false;
    fd_usb_snapshot s=fd_usb_audio_snapshot(a);
    if(s.state!=FD_USB_RX||s.pressed||!s.rf_valid||s.rf_tx||atomic_load(&a->qualification_running))return false;
    if(atomic_load(&a->rf_disabled)&&!atomic_load(&a->qualification_complete))return false;
    atomic_store(&a->rf_disabled,false);event(a,"rf-enabled",NULL,STREAM_COUNT,1,0,0);return true;
}
void fd_usb_audio_stop(fd_usb_audio *a){if(a)FAIL(a,U_STOP,"local-stop");}
fd_usb_snapshot fd_usb_audio_snapshot(fd_usb_audio *a){
    if(!a)return(fd_usb_snapshot){.state=FD_USB_DONE,.stopped=true};
    pthread_mutex_lock(&a->mutex);fd_usb_snapshot s=a->snapshot;
    if(atomic_load(&a->fault_record_ready)){
        s.first_fault_code=a->first_fault_code;s.first_fault_pa_error=a->first_fault_pa_error;s.first_fault_operation_ms=a->first_fault_operation_ms;s.first_fault_mic_age_ms=a->first_fault_mic_age;s.first_fault_rx_age_ms=a->first_fault_rx_age;
        snprintf(s.first_fault_origin,sizeof(s.first_fault_origin),"%s",a->first_fault_origin);snprintf(s.first_fault_stream,sizeof(s.first_fault_stream),"%s",a->first_fault_stream);
    }
    pthread_mutex_unlock(&a->mutex);
    s.stopped=atomic_load(&a->stopped);s.state=atomic_load(&a->state);s.fault=atomic_load(&a->fault);return s;
}
void fd_usb_audio_close(fd_usb_audio *a){
    if(!a||!atomic_load(&a->stopped))return;
    pthread_join(a->worker,NULL);pthread_join(a->watch,NULL);
    pthread_cond_destroy(&a->control_changed);pthread_mutex_destroy(&a->control_mutex);pthread_mutex_destroy(&a->events_mutex);pthread_mutex_destroy(&a->mutex);free(a);
}
