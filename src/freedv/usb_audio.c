// SPDX-License-Identifier: LGPL-2.1-or-later
#define _POSIX_C_SOURCE 200809L
#include "usb_voice.h"
#include "usb_evidence_store.h"
#include "usb_render_budget.h"
#include "usb_audio_levels.h"
#include "usb_headsets_internal.h"
#include <pulse/pulseaudio.h>
#include <pulse/timeval.h>
#include <pulse/rtclock.h>
#include <pthread.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>
#include <samplerate.h>
#include <errno.h>
#ifndef FREEDV_BUILD_ID
#define FREEDV_BUILD_ID "unqualified-development"
#endif
#ifndef FREEDV_SOURCE_COMMIT
#define FREEDV_SOURCE_COMMIT "unqualified-development"
#endif
#if defined(HOST_BUILD)
extern void usb_test_operation_phase(const char *) __attribute__((weak));
extern void usb_test_mic_cutoff(uint64_t) __attribute__((weak));
extern void usb_test_mic_release_observed(void) __attribute__((weak));
extern void usb_test_mic_close_requested(void) __attribute__((weak));
extern void usb_test_backend_timing_requested(void) __attribute__((weak));
extern void usb_test_backend_tail_wait_started(uint64_t) __attribute__((weak));
#endif

#define RATE 48000u
#define RADIO_RATE 44100u
#define QMAX 24000u
#define EVENT_MAX 256u
#define OP_TIMEOUT_MS 1000u
#define CAPTURE_TIMEOUT_MS 750u
#define PREPARATION_CAPTURE_TIMEOUT_MS 1500u
/* Declared service budget, not a measured hardware timing guarantee. */
#define IO_SERVICE_BUDGET_MS 150u
#define CODEC_QUANTUM_MS 120u
#define BUFFER_MARGIN_MS 30u
#define OUTPUT_RESERVE_MS (IO_SERVICE_BUDGET_MS+CODEC_QUANTUM_MS+BUFFER_MARGIN_MS)
#define OUTPUT_TARGET_MS 450u
#define STREAM_CAPACITY_MS 1000u
_Static_assert(FD_USB_TX_QUEUE_FRAMES==RADIO_RATE/2,"Transport queue uses 44.1 kHz frames");
_Static_assert(FD_USB_RENDER_OUTPUT_FRAMES<=QMAX,"A complete conversion fits the bounded queue");

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
    atomic_bool stop,stopped,watch_exit,armed,rf_disabled,qualification_requested,qualification_running,qualification_complete;
    atomic_bool qualification_keyed,qualification_loopback,reference_input_active,fault_record_ready;
    atomic_int context_state_id;
    atomic_int mic_gain_db,meter_gain_db,mic_input_rms_dbfs10,mic_input_peak_dbfs10,mic_rms_dbfs10,mic_peak_dbfs10;
    atomic_bool mic_meter_ready,headset_volume_ready,headset_muted;
    atomic_bool leaving,startup_ready;
    atomic_uint tx_timeout_seconds,turn_timeout_ms;
    atomic_uint headset_volume_percent;
    atomic_uint_fast64_t mic_clipped_samples,headset_volume_request;
    fd_usb_level_window mic_meter;int mic_meter_gain;
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
    /* Sink master changes are serialized on Pulse's I/O thread. The atomic
     * request carries a revision and desired volume/mute, so bursts of knob
     * detents accumulate while a previous operation is in flight. */
    pa_operation *volume_operation;
    pa_cvolume headset_channels;
    uint64_t volume_started,volume_next_poll,volume_target,volume_applied,volume_query_request;
    unsigned volume_stage;int volume_ack;
    bool volume_seen,volume_query_sync,volume_transaction,volume_mute_pending,volume_force_query;
    pa_operation *backend_operation;uint64_t backend_generation,backend_protocol_ack,backend_requested,backend_requested_us,backend_drain_started,backend_ack_us,backend_until_us,backend_completed_us;
    uint64_t backend_sink_usec,backend_transport_usec,backend_configured_usec,backend_pending_usec,backend_wait_usec;int64_t backend_read_index,backend_write_index;
    int backend_ack;bool backend_complete,backend_reported;pa_stream_flags_t requested_flags[STREAM_COUNT];
    unsigned playback_tail_completed_intervals,playback_tail_interrupted_intervals;
    struct { int ack; char name[40];uint64_t start; } operation;
    fd_voice_engine *engine;SRC_STATE *radio_input,*radio_output;
    uint64_t generation,turns,release,phase_at,rx_sequence,pending_key,pending_clear,qualification_release,qualification_idle_until;
    bool modem_started,headset_started,permit_press,finishing,producer_finished,radio_output_ended,rf_clear_confirmed;
    bool admission_permitted,configuration_seen,mic_navigation_close;
    uint64_t configuration_sequence,applied_tuning_revision,readiness_ms;
    pcm_queue capture,radio_capture,play;
    stream_metrics metrics[STREAM_COUNT];
    unsigned gaps,mic_fragment_frames,render_queue_high_water,render_deferred_count,render_final_deferred_count,render_final_flush_calls,render_audit_rows;
    bool render_backpressured,render_final_backpressured;uint64_t transport_tail_frames;uint64_t submitted_rx,submitted_mic,rendered_rx,rendered_modem,converted_radio,reference_submitted;
    uint64_t tx_capture_started,tx_capture_ended,tx_submitted_base;
    /* Pulse sample indices, in 48 kHz mono frames. The existing capture
     * queue also holds uncommitted closing frames; it is never enlarged. */
    uint64_t mic_start_index,mic_next_index,mic_queue_index,mic_cutoff_index,mic_submitted_end,mic_tap_start;
    uint64_t mic_close_started,mic_timing_issued,mic_frozen,mic_reached,mic_closed,mic_release_index;
    uint64_t mic_source_usec,mic_transport_usec,mic_sink_usec,mic_frontier_extra;
    int64_t mic_write_index;unsigned mic_committed,mic_cutoff_split;
    bool mic_live,mic_closing,mic_cutoff_frozen,mic_cutoff_reached,mic_close_reported,mic_index_gap;
    pa_operation *mic_close_operation;int mic_close_ack;uint64_t mic_close_generation;
    int16_t qualification_pcm[RATE*4];unsigned qualification_n,reference_cursor;
    bool qualification_waiting_loopback;
    uint64_t reference_next,reference_started,reference_synthesis_base,reference_underflow_base,reference_headset_base;
    bool qualification_rate_started;
    uint64_t rate_start_ms[2],rate_start_count[2],qualified_rate_window[2];
    uint64_t preparation_excluded_ms[2],preparation_excluded_frames[2];
    unsigned qualified_rate_hz[2];
    char source[512],sink[512],selected_headset_id[256],selected_headset_name[160],requested_headset_id[256];
    fd_usb_headset_collection headsets;
    FILE *trace,*taps[4];unsigned trace_rows;uint64_t tap_samples[4],last_trace,last_sample_ms;
    char session_id[256]; bool finalizing,trace_complete,trace_history_truncated;
    fd_voice_stats final_engine; atomic_int storage_error;
};
static uint64_t now_ms(void){struct timespec t;clock_gettime(CLOCK_MONOTONIC,&t);return(uint64_t)t.tv_sec*1000+t.tv_nsec/1000000;}
static uint64_t now_us(void){struct timespec t;clock_gettime(CLOCK_MONOTONIC,&t);return(uint64_t)t.tv_sec*1000000+t.tv_nsec/1000;}
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
    static const char *const names[]={"","Stopped","Audio connection failed","Panel PTT unavailable","Radio status lost or unexpected TX","Radio protection","Audio backlog","Codec failure","TX not confirmed","Modem playback underrun/drain failed","TX time limit","Audio coordinator stalled","Select an available paired USB microphone and speaker","Audio preparation failed","Audio operation timed out","Audio operation rejected","Active capture contains a hole","Required capture stopped","Audio read/write failed","Audio device moved","Radio control failed","Codec transition failed","RF appeared during audio evaluation","Capture sample delivery rate is not ready"};
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
    float in[FD_USB_RENDER_INPUT_FRAMES],out[FD_USB_RENDER_OUTPUT_FRAMES];if(count>FD_USB_RENDER_INPUT_FRAMES){FAIL(a,U_ENGINE,"resampler-input-size");return 0;}
    for(unsigned i=0;i<count;++i)in[i]=input[i]/32768.f;
    SRC_DATA data={.data_in=in,.data_out=out,.input_frames=count,.output_frames=FD_USB_RENDER_OUTPUT_FRAMES,.src_ratio=ratio,.end_of_input=final};
    if(src_process(src,&data)||data.input_frames_used!=(long)count){FAIL(a,U_ENGINE,"resampler-processing");return 0;}
    src_float_to_short_array(out,output,(int)data.output_frames_gen);return(unsigned)data.output_frames_gen;
}
static unsigned trace_seconds(fd_usb_audio *a){unsigned seconds=a->c.trace_pcm_seconds?a->c.trace_pcm_seconds:30;return seconds>120?120:seconds;}
static void tap(fd_usb_audio *a,unsigned slot,const int16_t *p,unsigned n){
    uint64_t limit=(uint64_t)RATE*trace_seconds(a);
    if(!a->taps[slot]||a->tap_samples[slot]>=limit)return;
    if(n>limit-a->tap_samples[slot])n=(unsigned)(limit-a->tap_samples[slot]);
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
static bool mic_index(fd_usb_audio *a,uint64_t *index){
    const pa_timing_info *t=pa_stream_get_timing_info(a->streams[MIC]);
    if(!t||t->read_index_corrupt||t->write_index_corrupt||t->read_index<0||t->read_index%2){
        fail_at(a,U_CAPTURE_RATE,"microphone-invalid-sample-index",MIC,0,0);return false;
    }
    *index=(uint64_t)t->read_index/2;return true;
}
static void meter_mic(fd_usb_audio *a,const int16_t *pcm,unsigned n){
    int gain=atomic_load(&a->mic_gain_db);
    if(gain!=a->mic_meter_gain){a->mic_meter=(fd_usb_level_window){0};a->mic_meter_gain=gain;}
    fd_usb_level_add(&a->mic_meter,pcm,n,gain);
    if(a->mic_meter.samples<RATE/10)return;
    atomic_store(&a->mic_input_rms_dbfs10,fd_usb_level_rms_dbfs10(a->mic_meter.input_squares,a->mic_meter.samples));
    atomic_store(&a->mic_input_peak_dbfs10,fd_usb_level_dbfs10(a->mic_meter.input_peak));
    atomic_store(&a->mic_rms_dbfs10,fd_usb_level_rms_dbfs10(a->mic_meter.output_squares,a->mic_meter.samples));
    atomic_store(&a->mic_peak_dbfs10,fd_usb_level_dbfs10(a->mic_meter.output_peak));
    atomic_store(&a->meter_gain_db,gain);atomic_store(&a->mic_meter_ready,true);
    a->mic_meter=(fd_usb_level_window){0};
}
static void read_pcm(pa_stream *s,size_t requested,void *p){
    (void)requested;fd_usb_audio *a=p;unsigned i=stream_index(a,s);if(i>RADIO_RX)return;
    for(unsigned loops=0;loops<64;++loops){
        const void *data;size_t bytes;
        if(pa_stream_peek(s,&data,&bytes)<0){fail_at(a,U_IO,"capture-peek",i,pa_context_errno(a->context),0);return;}
        if(!bytes)return;
        unsigned st=atomic_load(&a->state);bool active=(i==MIC&&a->mic_live)||(i==RADIO_RX&&st==FD_USB_RX);
        uint64_t now=now_ms();
        if(data&&bytes%2==0){
            if(i==MIC)meter_mic(a,data,(unsigned)(bytes/2));
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
        }else if(active&&!atomic_load(&a->stop)){
            unsigned frames=(unsigned)bytes/2,keep=frames;
            if(i==MIC){
                uint64_t index;if(!mic_index(a,&index))return;
                if(index!=a->mic_next_index){a->mic_index_gap=true;fail_at(a,U_CAPTURE_RATE,"tx-microphone-index-gap",MIC,0,0);return;}
                if(a->mic_cutoff_frozen&&index+keep>a->mic_cutoff_index){
                    keep=index<a->mic_cutoff_index?(unsigned)(a->mic_cutoff_index-index):0;
                    if(keep&&keep<frames)a->mic_cutoff_split=frames-keep;
                }
                push(a,&a->capture,data,keep);a->mic_next_index=index+frames;
                if(!a->mic_closing||a->mic_cutoff_frozen)a->mic_committed+=keep;
                if(a->mic_cutoff_frozen&&index+frames>=a->mic_cutoff_index){a->mic_cutoff_reached=true;a->mic_live=false;a->mic_reached=now;}
                atomic_fetch_add(&a->metrics[i].discarded,frames-keep);
            }else push(a,&a->radio_capture,data,frames);
        }
        else atomic_fetch_add(&a->metrics[i].discarded,bytes/2);
        if(pa_stream_drop(s)<0){fail_at(a,U_IO,"capture-drop",i,pa_context_errno(a->context),0);return;}
        if(atomic_load(&a->stop))return;
    }
    FAIL(a,U_QUEUE,"capture-callback-budget");
}
/* A fresh raw record frontier closes the Pulse capture pipeline, rather than
 * claiming a sample-perfect physical PTT edge. source_usec includes source
 * and in-flight frames; transport adds its reported uncertainty. Monitor sink
 * latency is deliberately not subtracted from this production frontier. */
static void mic_timing_done(pa_stream *s,int success,void *p){
    fd_usb_audio *a=p;uint64_t now=now_ms();const pa_timing_info *t=pa_stream_get_timing_info(s);
    if(atomic_load(&a->stop)||atomic_load(&a->fault)||atomic_load(&a->state)!=FD_USB_DRAIN||!a->mic_closing||a->mic_close_generation!=a->generation)return;
    a->mic_close_ack=success?1:-1;
    if(!success||!t||t->read_index_corrupt||t->write_index_corrupt||t->read_index<0||t->read_index%2||t->write_index<0||t->write_index%2||
       now-a->mic_timing_issued>=CAPTURE_TIMEOUT_MS||t->source_usec>CAPTURE_TIMEOUT_MS*1000u||
       t->transport_usec>CAPTURE_TIMEOUT_MS*1000u-t->source_usec){
        fail_at(a,U_CAPTURE_RATE,"microphone-close-timing-invalid",MIC,0,now-a->mic_timing_issued);return;
    }
    if((uint64_t)t->read_index/2!=a->mic_next_index){a->mic_index_gap=true;fail_at(a,U_CAPTURE_RATE,"tx-microphone-index-gap",MIC,0,now-a->mic_timing_issued);return;}
    a->mic_write_index=t->write_index;a->mic_source_usec=t->source_usec;a->mic_transport_usec=t->transport_usec;a->mic_sink_usec=t->sink_usec;
    a->mic_frontier_extra=((t->source_usec+t->transport_usec)*RATE+999999)/1000000;
    a->mic_cutoff_index=(uint64_t)t->write_index/2+a->mic_frontier_extra;
    uint64_t committed_end=a->mic_queue_index+a->mic_committed;
    if(a->mic_cutoff_index<committed_end||a->mic_cutoff_index<a->mic_start_index||
       a->mic_cutoff_index>a->mic_next_index+QMAX){
        fail_at(a,U_CAPTURE_RATE,"microphone-close-frontier-invalid",MIC,0,now-a->mic_timing_issued);return;
    }
    unsigned keep=a->capture.n;
    if(a->mic_queue_index+keep>a->mic_cutoff_index){
        keep=(unsigned)(a->mic_cutoff_index-a->mic_queue_index);
        a->mic_cutoff_split=a->capture.n-keep;atomic_fetch_add(&a->metrics[MIC].discarded,a->capture.n-keep);a->capture.n=keep;
    }
    a->mic_committed=keep;a->mic_cutoff_frozen=true;a->mic_frozen=now;
    if(a->mic_next_index>=a->mic_cutoff_index){a->mic_cutoff_reached=true;a->mic_live=false;a->mic_reached=now;}
#if defined(HOST_BUILD)
    if(usb_test_mic_cutoff)usb_test_mic_cutoff(a->mic_cutoff_index);
#endif
    event(a,"capture-cutoff-frozen","mic-timing",MIC,1,0,now-a->mic_timing_issued);
}
static bool mic_begin(fd_usb_audio *a){
    io_lock(a);read_pcm(a->streams[MIC],0,a);uint64_t index=0;bool ok=!atomic_load(&a->stop)&&mic_index(a,&index);
    if(ok){
        a->mic_start_index=a->mic_next_index=a->mic_queue_index=a->mic_submitted_end=index;
        a->mic_cutoff_index=0;a->mic_committed=0;a->mic_cutoff_split=0;a->mic_live=true;a->mic_closing=false;
        a->mic_cutoff_frozen=a->mic_cutoff_reached=a->mic_close_reported=a->mic_index_gap=false;
        a->mic_close_started=a->mic_timing_issued=a->mic_frozen=a->mic_reached=a->mic_closed=0;
        a->mic_release_index=a->mic_source_usec=a->mic_transport_usec=a->mic_sink_usec=a->mic_frontier_extra=0;a->mic_write_index=0;
        a->mic_close_ack=0;a->mic_tap_start=a->tap_samples[0];
        a->mic_navigation_close=false;a->tx_capture_started=now_ms();a->tx_capture_ended=0;a->tx_submitted_base=a->submitted_mic;state(a,FD_USB_TX);
    }
    io_unlock(a);return ok;
}
static bool mic_close_begin(fd_usb_audio *a,uint64_t released){
    event(a,a->mic_navigation_close?"capture-navigation-observed":"capture-release-observed","mic-timing",MIC,1,0,0);
    io_lock(a);
    /* Serialize the release with the whole read callback, then drain what is
     * already readable before holding later indexed frames uncommitted. */
    read_pcm(a->streams[MIC],0,a);
    if(atomic_load(&a->stop)){io_unlock(a);return false;}
    a->tx_capture_ended=released;a->mic_close_started=released;a->mic_release_index=a->mic_next_index;
    a->mic_closing=true;a->finishing=false;state(a,FD_USB_DRAIN);
    a->mic_timing_issued=now_ms();a->mic_close_generation=a->generation;event(a,"capture-close-start","mic-timing",MIC,0,0,0);
#if defined(HOST_BUILD)
    if(usb_test_mic_close_requested)usb_test_mic_close_requested();
#endif
    a->mic_close_operation=pa_stream_update_timing_info(a->streams[MIC],mic_timing_done,a);
    if(!a->mic_close_operation)fail_at(a,U_CAPTURE_RATE,"microphone-close-request-failed",MIC,pa_context_errno(a->context),0);
    io_unlock(a);return !atomic_load(&a->stop);
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
    bool payload=i==MODEM?a->modem_started:a->headset_started;unsigned st=atomic_load(&a->state);
    bool required=i==MODEM?(st==FD_USB_TX||st==FD_USB_DRAIN):st==FD_USB_RX;
    if(!payload||!required||atomic_load(&a->stop)||pa_stream_get_underflow_index(s)<=0||(i==MODEM&&a->drain))return;
    atomic_fetch_add(&a->metrics[i].payload_underflows,1);
    if(i==MODEM)fail_at(a,U_DRAIN,"modem-payload-underflow",MODEM,0,0);
    if(i==HEADSET)fail_at(a,U_DRAIN,"headset-payload-underflow",HEADSET,0,0);
}
static void context_state(pa_context *c,void *p){fd_usb_audio *a=p;atomic_store(&a->context_state_id,pa_context_get_state(c));if(!PA_CONTEXT_IS_GOOD(pa_context_get_state(c))&&!atomic_load(&a->stop))fail_at(a,U_DEVICE,"context-state",STREAM_COUNT,pa_context_errno(c),0);}
enum { VOLUME_QUERY=1,VOLUME_SET,VOLUME_MUTE };
#define VOLUME_MUTE_BIT UINT64_C(128)
static unsigned volume_percent(const pa_cvolume *volume){
    uint64_t percent=((uint64_t)pa_cvolume_max(volume)*100+PA_VOLUME_NORM/2)/PA_VOLUME_NORM;
    return percent>100?100:(unsigned)percent;
}
static const char *volume_operation_name(unsigned stage){return stage==VOLUME_QUERY?"headset-volume-read":stage==VOLUME_SET?"headset-volume-set":"headset-mute-set";}
static void volume_success(pa_context *c,int success,void *p){(void)c;fd_usb_audio *a=p;a->volume_ack=success?1:-1;}
static void volume_info(pa_context *c,const pa_sink_info *i,int end,void *p){
    (void)c;fd_usb_audio *a=p;
    if(end){a->volume_ack=end>0&&a->volume_seen?1:-1;return;}
    if(!i||strcmp(i->name,a->sink)||!pa_cvolume_valid(&i->volume))return;
    a->headset_channels=i->volume;a->volume_seen=true;
    atomic_store(&a->headset_volume_percent,volume_percent(&i->volume));atomic_store(&a->headset_muted,i->mute!=0);
}
static void volume_problem(fd_usb_audio *a,uint64_t now){
    event(a,"headset-volume-error",volume_operation_name(a->volume_stage),HEADSET,-1,pa_context_errno(a->context),now-a->volume_started);
    atomic_store(&a->headset_volume_ready,false);
    /* Failed UI controls do not revoke RF or interrupt a healthy audio path.
     * Consume this transaction, then reconcile with the device before any
     * newer request. A bounded retry avoids flooding a failing server. */
    if(a->volume_transaction)a->volume_applied=a->volume_target;
    a->volume_transaction=false;a->volume_mute_pending=false;a->volume_force_query=true;a->volume_next_poll=now+500;
}
static void volume_pump(fd_usb_audio *a,uint64_t now){
    if(atomic_load(&a->stop)||pa_context_get_state(a->context)!=PA_CONTEXT_READY||!a->sink[0])return;
    if(a->volume_operation){
        bool timed_out=now-a->volume_started>=OP_TIMEOUT_MS;
        if(pa_operation_get_state(a->volume_operation)==PA_OPERATION_RUNNING&&!timed_out)return;
        if(timed_out){pa_operation_cancel(a->volume_operation);a->volume_ack=-1;}
        pa_operation_unref(a->volume_operation);a->volume_operation=NULL;
        if(a->volume_ack!=1){volume_problem(a,now);return;}
        if(a->volume_stage==VOLUME_QUERY){
            if(a->volume_query_sync){
                uint_fast64_t expected=a->volume_query_request;
                uint_fast64_t actual=(expected&~UINT64_C(255))|atomic_load(&a->headset_volume_percent)|(atomic_load(&a->headset_muted)?VOLUME_MUTE_BIT:0);
                /* A query may update the idle desired value, but never erase
                 * detents accepted since that query started. */
                if(atomic_compare_exchange_strong(&a->headset_volume_request,&expected,actual))a->volume_applied=actual;
            }
            atomic_store(&a->headset_volume_ready,true);a->volume_next_poll=now+500;a->volume_force_query=false;
        }
    }
    if(a->volume_transaction){
        if(a->volume_mute_pending){
            a->volume_mute_pending=false;a->volume_stage=VOLUME_MUTE;a->volume_ack=0;a->volume_started=now;
            a->volume_operation=pa_context_set_sink_mute_by_name(a->context,a->sink,(a->volume_target&VOLUME_MUTE_BIT)!=0,volume_success,a);
            if(!a->volume_operation)volume_problem(a,now);
            return;
        }
        a->volume_applied=a->volume_target;a->volume_transaction=false;a->volume_force_query=true;a->volume_next_poll=now;
    }
    if((a->volume_force_query||!atomic_load(&a->headset_volume_ready)||now>=a->volume_next_poll)&&now>=a->volume_next_poll){
        a->volume_stage=VOLUME_QUERY;a->volume_ack=0;a->volume_seen=false;a->volume_started=now;
        a->volume_query_request=atomic_load(&a->headset_volume_request);a->volume_query_sync=a->volume_query_request==a->volume_applied;
        a->volume_operation=pa_context_get_sink_info_by_name(a->context,a->sink,volume_info,a);
        if(!a->volume_operation)volume_problem(a,now);
        return;
    }
    if(!atomic_load(&a->headset_volume_ready)||a->volume_force_query)return;
    uint64_t requested=atomic_load(&a->headset_volume_request);
    if(requested==a->volume_applied)return;
    pa_cvolume volume=a->headset_channels;
    /* Pulse units are perceptual. Scale all channels together to preserve
     * their existing balance; cap the largest channel at unity. */
    pa_cvolume_scale(&volume,(pa_volume_t)((requested&127)*PA_VOLUME_NORM/100));
    a->volume_target=requested;a->volume_transaction=true;
    a->volume_mute_pending=((requested&VOLUME_MUTE_BIT)!=0)!=atomic_load(&a->headset_muted);
    a->volume_stage=VOLUME_SET;a->volume_ack=0;a->volume_started=now;
    a->volume_operation=pa_context_set_sink_volume_by_name(a->context,a->sink,&volume,volume_success,a);
    if(!a->volume_operation)volume_problem(a,now);
}
static void io_tick(pa_mainloop_api *api,pa_time_event *e,const struct timeval *tv,void *p){
    (void)api;(void)tv;fd_usb_audio *a=p;uint64_t now=now_ms();atomic_store(&a->io_heartbeat,now);volume_pump(a,now);
    pa_context_rttime_restart(a->context,e,pa_rtclock_now()+10000);
}
static void source_info(pa_context *c,const pa_source_info *i,int end,void *p){
    (void)c;fd_usb_headset_collection *h=&((fd_usb_audio *)p)->headsets;if(end<0)h->failed=true;else if(!end)fd_usb_headsets_source(h,i);
}
static void sink_info(pa_context *c,const pa_sink_info *i,int end,void *p){
    (void)c;fd_usb_headset_collection *h=&((fd_usb_audio *)p)->headsets;if(end<0)h->failed=true;else if(!end)fd_usb_headsets_sink(h,i);
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
    a->mic_live=false;a->mic_committed=0;
    a->headset_started=a->modem_started=a->producer_finished=a->radio_output_ended=false;
    a->render_backpressured=a->render_final_backpressured=false;a->drain_ack=0;a->drain_started=0;
    if(a->drain){pa_operation_cancel(a->drain);pa_operation_unref(a->drain);a->drain=NULL;}
    if(a->backend_operation){pa_operation_cancel(a->backend_operation);pa_operation_unref(a->backend_operation);a->backend_operation=NULL;}
    a->backend_protocol_ack=a->backend_requested=a->backend_requested_us=a->backend_drain_started=a->backend_ack_us=a->backend_until_us=a->backend_completed_us=0;
    a->backend_sink_usec=a->backend_transport_usec=a->backend_configured_usec=a->backend_pending_usec=a->backend_wait_usec=0;a->backend_read_index=a->backend_write_index=0;
    a->backend_ack=0;a->backend_complete=a->backend_reported=false;
    pa_stream_set_write_callback(a->streams[MODEM],write_pcm,a);io_unlock(a);
}
static bool actual_inputs(fd_usb_audio *a,fd_usb_inputs *in);
static bool buffer_contract(fd_usb_audio *a,const char *phase);
static bool output_timing_ready(fd_usb_audio *a){
    for(unsigned i=HEADSET;i<=MODEM;++i){
        char name[40];snprintf(name,sizeof(name),"%s-timing-ready",stream_names[i]);io_lock(a);a->operation.ack=0;
        bool ok=wait_operation_locked(a,pa_stream_update_timing_info(a->streams[i],op_done,a),name,i,true);io_unlock(a);
        if(!ok)return false;
    }return true;
}
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
    #if defined(HOST_BUILD)
    if(usb_test_operation_phase)usb_test_operation_phase(atomic_load(&a->state)==FD_USB_PREPARE?"initial-rx":d==FD_VOICE_TX?"tx":"rx-return");
#endif
    event(a,"preparation-start","direction",STREAM_COUNT,d,0,0);
    reset_queues(a);
    if(!stream_op(a,MODEM,false,1)||!stream_op(a,MODEM,true,0)||!stream_op(a,HEADSET,true,0))return false;
    if(src_reset(a->radio_input)||src_reset(a->radio_output)){FAIL(a,U_TRANSITION,"transport-resampler-reset");return false;}
    uint64_t begin=now_ms();event(a,"codec-start","direction",STREAM_COUNT,d,0,0);
    bool ok=fd_voice_transition(a->engine,d,++a->generation);
    event(a,"codec-end","direction",STREAM_COUNT,ok,0,now_ms()-begin);
    if(!ok){FAIL(a,U_TRANSITION,"codec-direction");return false;}
    io_lock(a);a->operation.ack=0;
    bool timed=wait_operation_locked(a,pa_stream_update_timing_info(a->streams[MIC],op_done,a),"mic-timing-ready",MIC,true);io_unlock(a);
    if(!timed||!output_timing_ready(a))return false;
    /* Uncork before RF admission. Automatic PA prebuffering holds an empty
     * modem stream until the accepted speech/modem prefill becomes available. */
    if(d==FD_VOICE_TX){
        io_lock(a);a->operation.ack=0;
        bool prebuffered=wait_operation_locked(a,pa_stream_prebuf(a->streams[MODEM],op_done,a),"modem-prebuf",MODEM,true);io_unlock(a);
        if(!prebuffered||!stream_op(a,MODEM,false,0))return false;
    }
    if(!preparation_capture_ready(a,d)||!buffer_contract(a,atomic_load(&a->state)==FD_USB_PREPARE?"initial-rx":d==FD_VOICE_TX?"tx":"rx-return"))return false;
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
static bool buffer_contract(fd_usb_audio *a,const char *phase){
    pa_buffer_attr attrs[STREAM_COUNT];pa_sample_spec specs[STREAM_COUNT];pa_timing_info timing[STREAM_COUNT];bool ready[STREAM_COUNT],timed[STREAM_COUNT];
    io_lock(a);
    for(unsigned i=0;i<STREAM_COUNT;++i){
        const pa_buffer_attr *p=pa_stream_get_buffer_attr(a->streams[i]);const pa_sample_spec *s=pa_stream_get_sample_spec(a->streams[i]);
        ready[i]=p&&s&&pa_stream_get_state(a->streams[i])==PA_STREAM_READY;
        attrs[i]=p?*p:(pa_buffer_attr){0};specs[i]=s?*s:(pa_sample_spec){0};
        const pa_timing_info *t=pa_stream_get_timing_info(a->streams[i]);timed[i]=t&&!t->read_index_corrupt&&!t->write_index_corrupt;timing[i]=t?*t:(pa_timing_info){0};
    }
    io_unlock(a);unsigned failed=STREAM_COUNT;
    for(unsigned i=0;i<STREAM_COUNT;++i){
        pa_buffer_attr p=attrs[i];pa_sample_spec s=specs[i];bool capture=i<=RADIO_RX;
        uint64_t bytes_per_second=(uint64_t)s.rate*s.channels*2;
        uint64_t required_reserve=(bytes_per_second*OUTPUT_RESERVE_MS+999)/1000;
        uint64_t required_capture=(bytes_per_second*IO_SERVICE_BUDGET_MS+999)/1000+(uint64_t)p.fragsize*2;
        uint64_t usable=p.tlength>p.minreq?(uint64_t)p.tlength-p.minreq:0;
        bool good=ready[i]&&s.format==PA_SAMPLE_S16LE&&s.channels==1&&s.rate==(i==RADIO_RX||i==MODEM?RADIO_RATE:RATE)&&p.maxlength&&p.maxlength!=UINT32_MAX;
        if(capture)good=good&&p.fragsize&&p.fragsize!=UINT32_MAX&&!(p.fragsize%2)&&p.maxlength>=required_capture;
        else good=good&&timed[i]&&p.tlength&&p.tlength!=UINT32_MAX&&p.minreq&&p.minreq!=UINT32_MAX&&p.prebuf!=UINT32_MAX&&p.maxlength>=p.tlength&&p.tlength>=p.prebuf&&usable>=required_reserve&&p.prebuf>=required_reserve;
        if(!good&&failed==STREAM_COUNT)failed=i;
        if(a->trace)fprintf(a->trace,"{\"kind\":\"audio-budget\",\"ms\":%llu,\"phase\":\"%s\",\"stream\":\"%s\",\"rate\":%u,\"channels\":%u,\"maxlength\":%u,\"tlength\":%u,\"prebuf\":%u,\"minreq\":%u,\"fragsize\":%u,\"capture\":%s,\"io_service_budget_ms\":%u,\"codec_quantum_ms\":%u,\"margin_ms\":%u,\"requested_target_ms\":%u,\"requested_capacity_ms\":%u,\"required_reserve_bytes\":%llu,\"usable_reserve_bytes\":%llu,\"required_capture_bytes\":%llu,\"passed\":%s,\"request_domain\":\"%s\",\"requested_flags\":%u,\"adjust_latency\":%s,\"early_requests\":%s,\"timing_valid\":%s,\"configured_sink_usec\":%llu,\"reported_sink_usec\":%llu}\n",
            (unsigned long long)now_ms(),phase,stream_names[i],s.rate,s.channels,p.maxlength,p.tlength,p.prebuf,p.minreq,p.fragsize,capture?"true":"false",IO_SERVICE_BUDGET_MS,CODEC_QUANTUM_MS,BUFFER_MARGIN_MS,OUTPUT_TARGET_MS,STREAM_CAPACITY_MS,(unsigned long long)required_reserve,(unsigned long long)usable,(unsigned long long)required_capture,good?"true":"false",capture?"capture-fragments":"per-stream-playback",(unsigned)a->requested_flags[i],a->requested_flags[i]&PA_STREAM_ADJUST_LATENCY?"true":"false",a->requested_flags[i]&PA_STREAM_EARLY_REQUESTS?"true":"false",timed[i]?"true":"false",(unsigned long long)timing[i].configured_sink_usec,(unsigned long long)timing[i].sink_usec);
    }
    if(failed<STREAM_COUNT){char origin[40];snprintf(origin,sizeof(origin),"%s-buffer-budget",stream_names[failed]);fail_at(a,U_PREPARE,origin,failed,0,0);return false;}
    return true;
}
static void identity(FILE *f,fd_usb_audio *a){
    fputs("\"session_id\":",f);json_string(f,a->session_id);
    fputs(",\"build_id\":",f);json_string(f,FREEDV_BUILD_ID);
    fputs(",\"source_commit\":",f);json_string(f,FREEDV_SOURCE_COMMIT);
}
static bool finish_json(FILE *f,const char *temporary,const char *path,bool replace){
    bool ok=!ferror(f);if(fflush(f)||fsync(fileno(f)))ok=false;if(fclose(f))ok=false;
    if(ok)ok=replace?rename(temporary,path)==0:link(temporary,path)==0;
    unlink(temporary);return ok;
}
static void persist_record(fd_usb_audio *a,const char *kind,const char *path){
    if(!a->c.incident_dir)return;
    char json[16385];FILE *f=fopen(path,"rb");int error=0;
    if(!f)error=errno;else{
        size_t n=fread(json,1,sizeof(json)-1,f);json[n]=0;
        if(ferror(f)||!feof(f))error=EIO;
        fclose(f);
        if(!error){int result=fd_usb_evidence_store(a->c.incident_dir,a->session_id,kind,json);if(result<0)error=-result;}
    }
    if(error){atomic_store(&a->storage_error,error);fail_at(a,U_IO,"incident-store-write",STREAM_COUNT,error,0);}
}
static void current_session(fd_usb_audio *a){
    char path[768],temporary[800];const char *slash=strrchr(a->c.trace_prefix,'/');
    size_t n=slash?(size_t)(slash-a->c.trace_prefix+1):0;
    if(n+sizeof("freedv-usb-current.json")>sizeof(path)){atomic_store(&a->storage_error,ENAMETOOLONG);return;}
    memcpy(path,a->c.trace_prefix,n);strcpy(path+n,"freedv-usb-current.json");snprintf(temporary,sizeof(temporary),"%s.%ld.tmp",path,(long)getpid());
    FILE *f=fopen(temporary,"wx");if(!f){atomic_store(&a->storage_error,errno);return;}
    fputs("{\"kind\":\"session-pointer\",\"schema\":\"x6200.usb-current.v1\",\"terminal\":false,",f);identity(f,a);
    fputs(",\"trace_basename\":",f);char base[300];snprintf(base,sizeof(base),"%s.jsonl",a->session_id);json_string(f,base);
    fputs(",\"completion_basename\":",f);snprintf(base,sizeof(base),"%s-completion.json",a->session_id);json_string(f,base);
    if(a->c.operational){fputs(",\"readiness_basename\":",f);snprintf(base,sizeof(base),"%s-readiness.json",a->session_id);json_string(f,base);}fputs("}\n",f);
    if(!finish_json(f,temporary,path,true))atomic_store(&a->storage_error,EIO);
}
static void operational_fields(FILE *f,fd_usb_audio *a,const fd_usb_snapshot *s){
    if(!a->c.operational)return;
    fprintf(f,",\"usb_startup_contract_version\":4,\"operational\":true,\"startup_ready\":%s,\"leaving\":%s,\"configuration_ready\":%s,\"tx_eligible\":%s,\"tuning_revision\":%llu,\"tx_timeout_seconds\":%u",atomic_load(&a->startup_ready)?"true":"false",atomic_load(&a->leaving)?"true":"false",s&&s->configuration_ready?"true":"false",s&&s->tx_eligible?"true":"false",(unsigned long long)(s?s->tuning_revision:0),atomic_load(&a->tx_timeout_seconds));
}
static void readiness_payload(FILE *f,fd_usb_audio *a,fd_usb_inputs in){
    fputs("{\"kind\":\"startup-readiness\",\"schema\":\"x6200.usb.operational-readiness.v1\",",f);identity(f,a);
    fprintf(f,",\"usb_startup_contract_version\":4,\"operational\":true,\"status\":\"PASS\",\"ready_ms\":%llu,\"generation\":%llu,\"tuning_revision\":%llu,\"radio_sequence\":%llu,\"ptt_release_sequence\":%llu,\"ptt\":false,\"rf_valid\":true,\"rf_tx\":false,\"configuration_ready\":true,\"tx_eligible\":%s,\"captures_fresh\":true,\"buffers_ready\":true,\"headset_id\":",(unsigned long long)a->readiness_ms,(unsigned long long)a->generation,(unsigned long long)in.tuning_revision,(unsigned long long)in.sequence,(unsigned long long)in.release,in.tx_eligible?"true":"false");
    json_string(f,a->selected_headset_id);fputs(",\"headset_name\":",f);json_string(f,a->selected_headset_name);fputs(",\"source\":",f);json_string(f,a->source);fputs(",\"sink\":",f);json_string(f,a->sink);fputs("}\n",f);
}
static void readiness(fd_usb_audio *a,fd_usb_inputs in){
    a->readiness_ms=now_ms();
    if(a->trace){readiness_payload(a->trace,a,in);fflush(a->trace);}
    if(!a->c.trace_prefix)return;
    char path[768],temporary[800];snprintf(path,sizeof(path),"%s-readiness.json",a->c.trace_prefix);snprintf(temporary,sizeof(temporary),"%s.%ld.tmp",path,(long)getpid());
    FILE *f=fopen(temporary,"wx");if(!f){fail_at(a,U_IO,"readiness-receipt-open",STREAM_COUNT,errno,0);return;}
    readiness_payload(f,a,in);if(!finish_json(f,temporary,path,false)){fail_at(a,U_IO,"readiness-receipt-write",STREAM_COUNT,EIO,0);return;}persist_record(a,"readiness",path);
}
static void completion_payload(FILE *f,fd_usb_audio *a){
    fd_usb_snapshot s=a->snapshot;
    fputs("{\"kind\":\"completion\",\"schema\":\"x6200.usb-completion.v1\",",f);identity(f,a);operational_fields(f,a,&s);
    if(a->c.operational)fprintf(f,",\"completion_reason\":\"%s\"",atomic_load(&a->fault)>U_STOP?"fault":atomic_load(&a->fault)==U_STOP?"stop":atomic_load(&a->leaving)?"navigation":"stop");
    fprintf(f,",\"usb_output_contract_version\":1,\"turns\":%llu,\"playback_tail_completed_intervals\":%u,\"playback_tail_interrupted_intervals\":%u,\"terminal\":true,\"state\":%u,\"fault\":%u,\"rf_disabled\":%s,\"rf_valid\":%s,\"rf_tx\":%s,\"rf_clear_confirmed\":%s,\"qualification_complete\":%s,\"qualification_turns\":%u,\"qualification_target\":%u,\"last_sample_ms\":%llu,\"finalized_ms\":%llu,\"trace_complete\":%s,\"trace_history_truncated\":%s,\"trace_rows\":%u,\"events_dropped\":%u,\"storage_errno\":%d}\n",
        (unsigned long long)a->turns,a->playback_tail_completed_intervals,a->playback_tail_interrupted_intervals,s.state,s.fault,s.rf_disabled?"true":"false",s.rf_valid?"true":"false",s.rf_tx?"true":"false",s.rf_clear_confirmed?"true":"false",s.qualification_complete?"true":"false",s.qualification_turns,s.qualification_target,
        (unsigned long long)a->last_sample_ms,(unsigned long long)now_ms(),a->trace_complete?"true":"false",a->trace_history_truncated?"true":"false",a->trace_rows,a->event_dropped,atomic_load(&a->storage_error));
}
static void completion(fd_usb_audio *a){
    if(!a->c.trace_prefix)return;
    char path[768],temporary[800];snprintf(path,sizeof(path),"%s-completion.json",a->c.trace_prefix);snprintf(temporary,sizeof(temporary),"%s.%ld.tmp",path,(long)getpid());
    FILE *f=fopen(temporary,"wx");if(!f){atomic_store(&a->storage_error,errno);return;}
    completion_payload(f,a);bool ok=!ferror(f);if(fflush(f)||fsync(fileno(f)))ok=false;if(fclose(f))ok=false;
    if(!ok){atomic_store(&a->storage_error,EIO);unlink(temporary);return;}
    /* The authoritative local receipt includes the outcome of persistent
     * completion storage. An earlier intentional Stop cannot hide its error. */
    int before=atomic_load(&a->storage_error);persist_record(a,"completion",temporary);
    if(atomic_load(&a->storage_error)!=before){
        f=fopen(temporary,"w");if(!f){unlink(temporary);return;}
        completion_payload(f,a);ok=!ferror(f);if(fflush(f)||fsync(fileno(f)))ok=false;if(fclose(f))ok=false;
    }
    if(!ok||link(temporary,path)){atomic_store(&a->storage_error,EIO);}
    unlink(temporary);
}
static void describe_streams(fd_usb_audio *a){
    if(!a->trace)return;
    for(unsigned i=0;i<STREAM_COUNT;++i){
        io_lock(a);const pa_buffer_attr *p=pa_stream_get_buffer_attr(a->streams[i]);pa_buffer_attr attr=p?*p:(pa_buffer_attr){0};
        const pa_sample_spec *s=pa_stream_get_sample_spec(a->streams[i]);pa_sample_spec spec=s?*s:(pa_sample_spec){0};
        const pa_timing_info *t=pa_stream_get_timing_info(a->streams[i]);bool timed=t&&!t->read_index_corrupt&&!t->write_index_corrupt;uint64_t configured=t?t->configured_sink_usec:0,sink=t?t->sink_usec:0;
        const char *d=pa_stream_get_device_name(a->streams[i]);char name[512];snprintf(name,sizeof(name),"%s",d?d:"");io_unlock(a);
        fprintf(a->trace,"{\"kind\":\"stream\",\"ms\":%llu,\"stream\":\"%s\",\"device\":",(unsigned long long)now_ms(),stream_names[i]);json_string(a->trace,name);
        fprintf(a->trace,",\"rate\":%u,\"channels\":%u,\"maxlength\":%u,\"tlength\":%u,\"prebuf\":%u,\"minreq\":%u,\"fragsize\":%u,\"requested_flags\":%u,\"adjust_latency\":%s,\"early_requests\":%s,\"request_domain\":\"%s\",\"timing_valid\":%s,\"configured_sink_usec\":%llu,\"reported_sink_usec\":%llu}\n",spec.rate,spec.channels,attr.maxlength,attr.tlength,attr.prebuf,attr.minreq,attr.fragsize,(unsigned)a->requested_flags[i],a->requested_flags[i]&PA_STREAM_ADJUST_LATENCY?"true":"false",a->requested_flags[i]&PA_STREAM_EARLY_REQUESTS?"true":"false",i<=RADIO_RX?"capture-fragments":"per-stream-playback",timed?"true":"false",(unsigned long long)configured,(unsigned long long)sink);
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
    a->io_timer=pa_context_rttime_new(a->context,pa_rtclock_now()+10000,io_tick,a);
    if(!a->io_timer){io_unlock(a);return false;}
    if(a->c.source&&a->c.sink){snprintf(a->source,sizeof(a->source),"%s",a->c.source);snprintf(a->sink,sizeof(a->sink),"%s",a->c.sink);
        snprintf(a->selected_headset_id,sizeof(a->selected_headset_id),"%s",a->requested_headset_id[0]?a->requested_headset_id:"host-fixture");snprintf(a->selected_headset_name,sizeof(a->selected_headset_name),"Host test USB headset");}else{
        bool ok=wait_operation_locked(a,pa_context_get_source_info_list(a->context,source_info,a),"discover-sources",MIC,false)&&wait_operation_locked(a,pa_context_get_sink_info_list(a->context,sink_info,a),"discover-sinks",HEADSET,false);
        fd_usb_headset catalog[FD_USB_HEADSET_MAX];unsigned count=fd_usb_headsets_catalog(&a->headsets,catalog,FD_USB_HEADSET_MAX),matches=0;fd_usb_headset *selected=NULL;
        for(unsigned i=0;i<count;++i)if(!a->requested_headset_id[0]||!strcmp(catalog[i].id,a->requested_headset_id)){selected=&catalog[i];++matches;}
        if(!ok||matches!=1){io_unlock(a);FAIL(a,U_IDENTITY,"endpoint-identity");return false;}
        snprintf(a->source,sizeof(a->source),"%s",selected->source);snprintf(a->sink,sizeof(a->sink),"%s",selected->sink);
        snprintf(a->selected_headset_id,sizeof(a->selected_headset_id),"%s",selected->id);snprintf(a->selected_headset_name,sizeof(a->selected_headset_name),"%s",selected->name);
    }
    pa_sample_spec spec={PA_SAMPLE_S16LE,RATE,1};
    a->streams[MIC]=pa_stream_new(a->context,"USB microphone",&spec,NULL);a->streams[HEADSET]=pa_stream_new(a->context,"USB decoded speech",&spec,NULL);
    spec.rate=RADIO_RATE;a->streams[RADIO_RX]=pa_stream_new(a->context,"Radio demodulated audio",&spec,NULL);a->streams[MODEM]=pa_stream_new(a->context,"Radio modem audio",&spec,NULL);
    for(unsigned i=0;i<STREAM_COUNT;++i)if(!a->streams[i]){io_unlock(a);return false;}
    for(unsigned i=0;i<STREAM_COUNT;++i){pa_stream_set_state_callback(a->streams[i],stream_state,a);pa_stream_set_moved_callback(a->streams[i],stream_state,a);pa_stream_set_suspended_callback(a->streams[i],stream_suspended,a);}
    pa_stream_set_read_callback(a->streams[MIC],read_pcm,a);pa_stream_set_read_callback(a->streams[RADIO_RX],read_pcm,a);
    pa_stream_set_write_callback(a->streams[HEADSET],write_pcm,a);pa_stream_set_write_callback(a->streams[MODEM],write_pcm,a);
    pa_stream_set_underflow_callback(a->streams[HEADSET],underflow,a);pa_stream_set_underflow_callback(a->streams[MODEM],underflow,a);
    /* Capture fragments control source latency. Playback tlength is instead
     * an explicit per-stream reserve; ADJUST would split it with the device.
     * EARLY requests bound the requested device cadence to minreq, rather
     * than consuming nearly the whole client reserve in one backend burst. */
    pa_stream_flags_t base_flags=PA_STREAM_DONT_MOVE|PA_STREAM_AUTO_TIMING_UPDATE;
    pa_stream_flags_t flags=base_flags|PA_STREAM_EARLY_REQUESTS;
    /* Unity is relative to the operator's current USB master even on flat-
     * volume servers; opening this stream must not raise device loudness. */
    pa_stream_flags_t headset_flags=flags|PA_STREAM_RELATIVE_VOLUME;
    pa_stream_flags_t capture_flags=base_flags|PA_STREAM_ADJUST_LATENCY;
    a->requested_flags[MIC]=a->requested_flags[RADIO_RX]=capture_flags;a->requested_flags[HEADSET]=headset_flags;a->requested_flags[MODEM]=flags|PA_STREAM_START_CORKED;
    pa_buffer_attr attr={.maxlength=RATE*2*STREAM_CAPACITY_MS/1000,.tlength=RATE*2*OUTPUT_TARGET_MS/1000,.prebuf=(uint32_t)-1,.minreq=RATE*2/50,.fragsize=RATE*2/50};
    pa_cvolume volume;pa_cvolume_set(&volume,1,PA_VOLUME_NORM);
    bool ok=pa_stream_connect_record(a->streams[MIC],a->source,&attr,capture_flags)>=0&&pa_stream_connect_playback(a->streams[HEADSET],a->sink,&attr,headset_flags,&volume,NULL)>=0;
    attr=(pa_buffer_attr){.maxlength=RADIO_RATE*2*STREAM_CAPACITY_MS/1000,.tlength=RADIO_RATE*2*OUTPUT_TARGET_MS/1000,.prebuf=(uint32_t)-1,.minreq=RADIO_RATE*2/50,.fragsize=RADIO_RATE*2/50};pa_cvolume_set(&volume,1,PA_VOLUME_NORM);
    ok=ok&&pa_stream_connect_record(a->streams[RADIO_RX],a->c.radio_source,&attr,capture_flags)>=0&&pa_stream_connect_playback(a->streams[MODEM],a->c.radio_sink,&attr,flags|PA_STREAM_START_CORKED,&volume,NULL)>=0;
    until=now_ms()+5000;bool ready=false;
    while(ok&&!ready&&!atomic_load(&a->stop)&&now_ms()<until){ready=true;for(unsigned i=0;i<STREAM_COUNT;++i)if(pa_stream_get_state(a->streams[i])!=PA_STREAM_READY)ready=false;io_unlock(a);atomic_store(&a->heartbeat,now_ms());pause_ms(1);io_lock(a);}
    io_unlock(a);if(!ready||!output_timing_ready(a))return false;
    io_lock(a);const pa_buffer_attr *mic_attr=pa_stream_get_buffer_attr(a->streams[MIC]);
    a->mic_fragment_frames=mic_attr&&mic_attr->fragsize!=UINT32_MAX?mic_attr->fragsize/2:0;io_unlock(a);
    if(!a->mic_fragment_frames){FAIL(a,U_PREPARE,"microphone-fragment-size");return false;}
    describe_streams(a);if(!buffer_contract(a,"initial-rx")||!direction(a,FD_VOICE_RX))return false;
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
    if(st==FD_USB_RX)result.pressed=!atomic_load(&a->qualification_loopback)&&now_ms()>=a->qualification_idle_until&&now_ms()-a->phase_at>=250;
    if(st==FD_USB_TX&&!result.pressed)result.release=++a->qualification_release;
    else result.release=a->qualification_release;
    result.rf_tx=atomic_load(&a->qualification_keyed);return result;
}
static void drain_done(pa_stream *s,int success,void *p){(void)s;fd_usb_audio *a=p;a->drain_ack=success?1:-1;if(success)a->backend_protocol_ack=now_ms();}
static void backend_timing_done(pa_stream *s,int success,void *p){
    fd_usb_audio *a=p;uint64_t now=now_us();
    if(atomic_load(&a->stop)||atomic_load(&a->fault)||atomic_load(&a->state)!=FD_USB_DRAIN||a->backend_generation!=a->generation||!a->backend_requested||a->drain_ack!=1)return;
    const pa_timing_info *t=pa_stream_get_timing_info(s);a->backend_ack=success?1:-1;a->backend_ack_us=now;
    if(t){a->backend_read_index=t->read_index;a->backend_write_index=t->write_index;a->backend_sink_usec=t->sink_usec;a->backend_transport_usec=t->transport_usec;a->backend_configured_usec=t->configured_sink_usec;}
    if(!success||!t||t->read_index_corrupt||t->write_index_corrupt||t->read_index<0||t->write_index<0||t->read_index%2||t->write_index%2||
       now/1000-a->backend_requested>=OP_TIMEOUT_MS||pa_stream_get_state(s)!=PA_STREAM_READY||pa_stream_is_suspended(s)!=0||pa_stream_is_corked(s)!=0){
        fail_at(a,U_DRAIN,"modem-backend-timing-invalid",MODEM,0,now/1000-a->backend_requested);return;
    }
    uint64_t deadline=(a->phase_at+3000)*1000,remaining=deadline>now?deadline-now:0;
    uint64_t pending=t->write_index>t->read_index?(uint64_t)(t->write_index-t->read_index)/2:0;
    if(pending>(uint64_t)RADIO_RATE*3){fail_at(a,U_DRAIN,"modem-backend-tail-budget",MODEM,0,now/1000-a->backend_requested);return;}
    a->backend_pending_usec=(pending*1000000+RADIO_RATE-1)/RADIO_RATE;
    if(t->sink_usec==UINT64_MAX||t->transport_usec==UINT64_MAX||t->sink_usec>=remaining||t->transport_usec>=remaining-t->sink_usec||
       a->backend_pending_usec>=remaining-t->sink_usec-t->transport_usec){
        fail_at(a,U_DRAIN,"modem-backend-tail-budget",MODEM,0,now/1000-a->backend_requested);return;
    }
    a->backend_wait_usec=t->sink_usec+t->transport_usec+a->backend_pending_usec;
    /* Microsecond ceiling only; this is not an arbitrary silence extension.
     * sink_usec includes the render/resampler/device queue after protocol EOF. */
    a->backend_until_us=now+a->backend_wait_usec+1;
    event(a,"operation-end","modem-backend-timing",MODEM,1,0,now/1000-a->backend_requested);
    event(a,"backend-tail-start","modem-backend-timing",MODEM,1,0,a->backend_wait_usec/1000);
#if defined(HOST_BUILD)
    if(usb_test_backend_tail_wait_started)usb_test_backend_tail_wait_started(a->backend_until_us);
#endif
}
static void backend_tail_report(fd_usb_audio *a,bool complete){
    if(!a->trace||a->backend_reported||!a->backend_requested)return;
    char line[2000];io_lock(a);a->backend_reported=true;if(complete)++a->playback_tail_completed_intervals;else ++a->playback_tail_interrupted_intervals;
    snprintf(line,sizeof(line),"{\"kind\":\"backend-tail\",\"schema\":\"x6200.usb.playback-tail.v1\",\"turn\":%llu,\"drain_started_ms\":%llu,\"protocol_ack_ms\":%llu,\"timing_issued_ms\":%llu,\"timing_issued_us\":%llu,\"timing_ack_ms\":%llu,\"timing_ack_us\":%llu,\"write_index_bytes\":%lld,\"read_index_bytes\":%lld,\"sink_usec\":%llu,\"transport_usec\":%llu,\"configured_sink_usec\":%llu,\"protocol_pending_usec\":%llu,\"wait_usec\":%llu,\"wait_until_us\":%llu,\"completed_us\":%llu,\"complete\":%s,\"rf_tx_confirmed\":%s,\"rf_disabled\":%s,\"scope\":\"pulse-reported-backend-tail\"}\n",
        (unsigned long long)(a->turns+1),(unsigned long long)a->backend_drain_started,(unsigned long long)a->backend_protocol_ack,(unsigned long long)a->backend_requested,(unsigned long long)a->backend_requested_us,(unsigned long long)(a->backend_ack_us/1000),(unsigned long long)a->backend_ack_us,(long long)a->backend_write_index,(long long)a->backend_read_index,(unsigned long long)a->backend_sink_usec,(unsigned long long)a->backend_transport_usec,(unsigned long long)a->backend_configured_usec,(unsigned long long)a->backend_pending_usec,(unsigned long long)a->backend_wait_usec,(unsigned long long)a->backend_until_us,(unsigned long long)a->backend_completed_us,complete?"true":"false",complete?"true":"false",atomic_load(&a->rf_disabled)?"true":"false");
    io_unlock(a);fputs("{",a->trace);identity(a->trace,a);fputc(',',a->trace);fputs(line+1,a->trace);
}
static bool backend_tail_step(fd_usb_audio *a){
    io_lock(a);
    if(atomic_load(&a->stop)||atomic_load(&a->fault)){io_unlock(a);return false;}
    if(!a->backend_requested){
        a->backend_requested_us=now_us();a->backend_requested=a->backend_requested_us/1000;a->backend_drain_started=a->phase_at;a->backend_generation=a->generation;
        event(a,"operation-end","modem-drain",MODEM,1,0,a->backend_protocol_ack-a->drain_started);
        event(a,"operation-start","modem-backend-timing",MODEM,0,0,0);
#if defined(HOST_BUILD)
        if(usb_test_backend_timing_requested)usb_test_backend_timing_requested();
#endif
        a->backend_operation=pa_stream_update_timing_info(a->streams[MODEM],backend_timing_done,a);
        if(!a->backend_operation)fail_at(a,U_PA_REJECT,"modem-backend-timing",MODEM,pa_context_errno(a->context),0);
    }
    if(a->backend_operation&&pa_operation_get_state(a->backend_operation)!=PA_OPERATION_RUNNING){pa_operation_unref(a->backend_operation);a->backend_operation=NULL;}
    if(!a->backend_ack&&now_ms()-a->backend_requested>=OP_TIMEOUT_MS)fail_at(a,U_PA_TIMEOUT,"modem-backend-timing",MODEM,0,now_ms()-a->backend_requested);
    if(a->backend_ack==1&&a->backend_until_us&&!atomic_load(&a->stop)&&!atomic_load(&a->fault)){
        if(pa_stream_get_state(a->streams[MODEM])!=PA_STREAM_READY||pa_stream_is_suspended(a->streams[MODEM])!=0||pa_stream_is_corked(a->streams[MODEM])!=0)fail_at(a,U_DRAIN,"modem-backend-state",MODEM,0,0);
        else if(now_us()>=a->backend_until_us){a->backend_completed_us=now_us();a->backend_complete=true;}
    }
    bool complete=a->backend_complete&&!atomic_load(&a->stop)&&!atomic_load(&a->fault);io_unlock(a);return complete;
}
/* A healthy modem/sink trace cannot reveal speech that disappeared before
 * encoding. Screen accepted microphone frames against the active wall-time
 * window before emitting EOO or counting a completed turn. One percent is a
 * delivery tolerance, not a measured hardware oscillator specification; two
 * negotiated capture fragments cover the two callback boundary blocks. */
static bool capture_accounting(fd_usb_audio *a){
    uint64_t elapsed=a->tx_capture_ended>=a->tx_capture_started?a->tx_capture_ended-a->tx_capture_started:0;
    uint64_t accepted=a->submitted_mic-a->tx_submitted_base,expected=elapsed*RATE/1000;
    uint64_t boundary=(uint64_t)a->mic_fragment_frames*2,required=expected*99/100;
    required=required>boundary?required-boundary:0;bool evaluated=elapsed>=750,passed=(!evaluated||accepted>=required)&&a->mic_cutoff_reached&&a->mic_submitted_end==a->mic_cutoff_index&&accepted==a->mic_cutoff_index-a->mic_start_index&&!a->mic_index_gap;
    if(a->trace)fprintf(a->trace,"{\"kind\":\"source-accounting\",\"ms\":%llu,\"turn\":%llu,\"active_ms\":%llu,\"accepted_mic_frames\":%llu,\"expected_mic_frames\":%llu,\"required_mic_frames\":%llu,\"boundary_frames\":%llu,\"delivery_tolerance_percent\":1,\"evaluated\":%s,\"passed\":%s}\n",
        (unsigned long long)now_ms(),(unsigned long long)(a->turns+1),(unsigned long long)elapsed,(unsigned long long)accepted,(unsigned long long)expected,(unsigned long long)required,(unsigned long long)boundary,evaluated?"true":"false",passed?"true":"false");
    if(!passed){FAIL(a,U_CAPTURE_RATE,"tx-microphone-frame-deficit");return false;}return true;
}
static void capture_close_report(fd_usb_audio *a,bool complete){
    if(!a->trace||a->mic_close_reported||!a->tx_capture_started)return;
    char line[2048];io_lock(a);a->mic_close_reported=true;
    snprintf(line,sizeof(line),"{\"kind\":\"capture-close\",\"turn\":%llu,\"start_index\":%llu,\"cutoff_index\":%llu,\"consumed_index\":%llu,\"submitted_first_index\":%llu,\"submitted_end_index\":%llu,\"mic_tap_start\":%llu,\"mic_tap_end\":%llu,\"capture_started_ms\":%llu,\"release_fence_index\":%llu,\"close_started_ms\":%llu,\"timing_issued_ms\":%llu,\"frozen_ms\":%llu,\"reached_ms\":%llu,\"closed_ms\":%llu,\"complete\":%s,\"index_gap\":%s,\"tail_frames\":%llu,\"write_index_bytes\":%lld,\"source_usec\":%llu,\"transport_usec\":%llu,\"sink_usec\":%llu,\"frontier_extra_frames\":%llu,\"cutoff_split_frames\":%u,\"close_timeout_ms\":%u,\"scope\":\"pulse-capture-pipeline-frontier\"}\n",
        (unsigned long long)(a->turns+1),(unsigned long long)a->mic_start_index,(unsigned long long)a->mic_cutoff_index,(unsigned long long)a->mic_submitted_end,(unsigned long long)a->mic_start_index,(unsigned long long)a->mic_submitted_end,(unsigned long long)a->mic_tap_start,(unsigned long long)a->tap_samples[0],(unsigned long long)a->tx_capture_started,(unsigned long long)a->mic_release_index,(unsigned long long)a->mic_close_started,(unsigned long long)a->mic_timing_issued,(unsigned long long)a->mic_frozen,(unsigned long long)a->mic_reached,(unsigned long long)a->mic_closed,complete?"true":"false",a->mic_index_gap?"true":"false",(unsigned long long)(a->mic_cutoff_frozen&&a->mic_cutoff_index>a->mic_release_index?a->mic_cutoff_index-a->mic_release_index:0),(long long)a->mic_write_index,(unsigned long long)a->mic_source_usec,(unsigned long long)a->mic_transport_usec,(unsigned long long)a->mic_sink_usec,(unsigned long long)a->mic_frontier_extra,a->mic_cutoff_split,CAPTURE_TIMEOUT_MS);
    io_unlock(a);
    /* A navigation boundary closes accepted speech without inventing a PTT
     * release event. The retained physical counter remains the admission one. */
    if(a->c.operational){size_t n=strlen(line);if(n>=2)line[n-2]=0;fputs(line,a->trace);fprintf(a->trace,",\"close_trigger\":\"%s\",\"physical_release_sequence\":%llu}\n",a->mic_navigation_close?"navigation":"ptt-release",(unsigned long long)a->release);}else fputs(line,a->trace);
}
static void render_audit(fd_usb_audio *a,const char *stage,unsigned reserved_from,unsigned before,unsigned produced,unsigned after,unsigned input_frames){
    if(after>a->render_queue_high_water)a->render_queue_high_water=after;
    if(!a->trace)return;
    if(a->render_audit_rows>=1024){a->trace_history_truncated=true;return;}++a->render_audit_rows;
    fprintf(a->trace,"{\"kind\":\"render-queue\",\"ms\":%llu,\"stage\":\"%s\",\"input_rate\":48000,\"output_rate\":44100,\"reserved_from_frames\":%u,\"queue_before_frames\":%u,\"input_frames\":%u,\"generated_frames\":%u,\"queue_after_frames\":%u,\"output_capacity_frames\":%u,\"queue_limit_frames\":%u,\"deferred_calls\":%u,\"final_deferred_calls\":%u}\n",
        (unsigned long long)now_ms(),stage,reserved_from,before,input_frames,produced,after,FD_USB_RENDER_OUTPUT_FRAMES,FD_USB_TX_QUEUE_FRAMES,a->render_deferred_count,a->render_final_deferred_count);
}
static void process_audio(fd_usb_audio *a,unsigned st){
    int16_t pcm[FD_USB_RENDER_OUTPUT_FRAMES],converted[FD_USB_RENDER_OUTPUT_FRAMES];unsigned n=0;
    io_lock(a);unsigned take=st==FD_USB_RX?441:a->mic_committed<480?a->mic_committed:480;
    n=pop(st==FD_USB_RX?&a->radio_capture:&a->capture,pcm,take);
    if(st!=FD_USB_RX){a->mic_committed-=n;a->mic_queue_index+=n;a->mic_submitted_end=a->mic_queue_index;}
    io_unlock(a);
    bool reference=st==FD_USB_RX&&atomic_load(&a->qualification_loopback);
    if(n){
        if(st==FD_USB_RX){n=convert(a,a->radio_input,pcm,n,converted,(double)RATE/RADIO_RATE,false);memcpy(pcm,converted,n*2);a->converted_radio+=n;}
        else a->submitted_mic+=n;
        tap(a,st==FD_USB_RX?1:0,pcm,n);
        if(!reference){
            if(st==FD_USB_RX)a->submitted_rx+=n;
            /* Keep the source-accounting tap raw. Only the speech submitted
             * to the retained TX codec receives the operator's digital gain. */
            int gain=atomic_load(&a->mic_gain_db);uint64_t clipped=0;
            const int16_t *input=pcm;
            if(st!=FD_USB_RX){clipped=fd_usb_pcm_gain(converted,pcm,n,gain);input=converted;}
            size_t accepted=fd_voice_submit(a->engine,a->generation,input,n);
            if(st!=FD_USB_RX)atomic_fetch_add(&a->mic_clipped_samples,accepted==n?clipped:fd_usb_pcm_gain(NULL,pcm,accepted,gain));
            if(accepted!=n){FAIL(a,U_QUEUE,"codec-input-queue");return;}
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
    io_lock(a);bool input_empty=!a->capture.n,closed=a->mic_cutoff_reached;unsigned play_n=a->play.n;
    if(st==FD_USB_DRAIN&&a->mic_close_operation&&pa_operation_get_state(a->mic_close_operation)!=PA_OPERATION_RUNNING){pa_operation_unref(a->mic_close_operation);a->mic_close_operation=NULL;}
    io_unlock(a);
    if(st==FD_USB_DRAIN&&!a->finishing){
        if(now_ms()-a->mic_close_started>=CAPTURE_TIMEOUT_MS){fail_at(a,U_CAPTURE_STALE,"microphone-close-timeout",MIC,0,now_ms()-a->mic_close_started);return;}
        if(closed&&input_empty){
            a->mic_closed=now_ms();capture_close_report(a,true);if(!capture_accounting(a)||atomic_load(&a->stop)||atomic_load(&a->fault))return;
            if(!fd_voice_finish(a->engine,a->generation)){FAIL(a,U_ENGINE,"codec-finish");return;}a->finishing=true;
        }
    }
    unsigned limit=st==FD_USB_RX?QMAX:FD_USB_TX_QUEUE_FRAMES;
    if(play_n>limit){FAIL(a,U_QUEUE,"render-queue-limit");return;}
    unsigned room=st==FD_USB_RX?limit-play_n:fd_usb_tx_render_room(play_n)?FD_USB_RENDER_INPUT_FRAMES:0;
    if(room>FD_USB_RENDER_INPUT_FRAMES)room=FD_USB_RENDER_INPUT_FRAMES;
    if(st!=FD_USB_RX&&!room){
        ++a->render_deferred_count;
        if(!a->render_backpressured)render_audit(a,"regular-wait",play_n,play_n,0,play_n,0);
        a->render_backpressured=true;
    }else a->render_backpressured=false;
    if(room){
        n=fd_voice_receive(a->engine,a->generation,pcm,room);
        if(n){tap(a,st==FD_USB_RX?3:2,pcm,n);
            if(st!=FD_USB_RX&&atomic_load(&a->qualification_running)){
                if(n>RATE*4-a->qualification_n){FAIL(a,U_QUEUE,"qualification-recording-bound");return;}
                memcpy(a->qualification_pcm+a->qualification_n,pcm,n*2);a->qualification_n+=n;
            }
            unsigned engine_frames=n;const int16_t *rendered=pcm;
            if(st!=FD_USB_RX){n=convert(a,a->radio_output,pcm,n,converted,(double)RADIO_RATE/RATE,false);rendered=converted;a->rendered_modem+=n;}else a->rendered_rx+=n;
            io_lock(a);unsigned before=a->play.n;push(a,&a->play,rendered,n);unsigned after=a->play.n;io_unlock(a);
            if(st!=FD_USB_RX)render_audit(a,"regular",play_n,before,n,after,engine_frames);
        }
    }
    fd_voice_stats stats=fd_voice_observe(a->engine);
    if(stats.failures||stats.cancelled){FAIL(a,U_ENGINE,"codec-worker");return;}
    if(st!=FD_USB_RX&&stats.finished&&!stats.output_depth&&!a->radio_output_ended){
        /* The regular conversion above may have consumed the former headroom.
         * Reserve a complete output buffer again using current queue frames.
         * Zero-input SRC is complete only when it actually emits zero frames. */
        io_lock(a);unsigned reserved=a->play.n;io_unlock(a);
        if(fd_usb_tx_render_room(reserved)){
            a->render_final_backpressured=false;
            n=convert(a,a->radio_output,NULL,0,converted,(double)RADIO_RATE/RATE,true);++a->render_final_flush_calls;
            io_lock(a);unsigned before=a->play.n;push(a,&a->play,converted,n);unsigned after=a->play.n;io_unlock(a);
            a->transport_tail_frames+=n;a->radio_output_ended=n==0;
            render_audit(a,"src-final",reserved,before,n,after,0);
        }else{++a->render_final_deferred_count;
            if(!a->render_final_backpressured)render_audit(a,"src-final-wait",reserved,reserved,0,reserved,0);
            a->render_final_backpressured=true;
        }
    }
    io_lock(a);a->producer_finished=stats.finished&&!stats.output_depth&&a->radio_output_ended;
    if(st!=FD_USB_RX&&!a->drain){size_t writable=pa_stream_writable_size(a->streams[MODEM]);if(writable==(size_t)-1)fail_at(a,U_IO,"modem-writable-size",MODEM,pa_context_errno(a->context),0);else if(writable)write_pcm(a->streams[MODEM],writable,a);}
    if(stats.input_depth+a->capture.n+(a->radio_capture.n*RATE+RADIO_RATE-1)/RADIO_RATE>=QMAX||stats.output_depth+a->play.n>QMAX*2)FAIL(a,U_QUEUE,"aggregate-queue-limit");
    if(st==FD_USB_DRAIN&&a->producer_finished&&!a->play.n&&!a->drain){
        pa_stream_set_write_callback(a->streams[MODEM],NULL,NULL);a->drain_ack=0;
        a->drain_started=now_ms();event(a,"operation-start","modem-drain",MODEM,0,0,0);a->drain=pa_stream_drain(a->streams[MODEM],drain_done,a);
        if(!a->drain)fail_at(a,U_PA_REJECT,"modem-drain",MODEM,pa_context_errno(a->context),0);
        else{
            /* A complete short payload can be smaller than the prebuffer.
             * Register drain first, then release that final finite payload;
             * no further live samples are required to finish it. */
            a->operation.ack=0;
            (void)wait_operation_locked(a,pa_stream_trigger(a->streams[MODEM],op_done,a),"modem-trigger",MODEM,true);
        }
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
    if(atomic_load(&a->storage_error)){FAIL(a,U_IO,"evidence-storage-not-ready");return false;}
    uint64_t mw=0,rw=0;unsigned mr=measured_rate(a,MIC,&mw),rr=measured_rate(a,RADIO_RX,&rw);
    if(mw<3000||rw<3000||mr<RATE*95/100||mr>RATE*105/100){FAIL(a,U_CAPTURE_RATE,"microphone-measured-rate");return false;}
    if(rr<RADIO_RATE*95/100||rr>RADIO_RATE*105/100){FAIL(a,U_CAPTURE_RATE,"radio-rx-measured-rate");return false;}
    a->qualified_rate_hz[MIC]=mr;a->qualified_rate_hz[RADIO_RX]=rr;a->qualified_rate_window[MIC]=mw;a->qualified_rate_window[RADIO_RX]=rw;
    atomic_store(&a->qualification_running,false);atomic_store(&a->qualification_complete,true);atomic_store(&a->armed,false);
    event(a,"qualification-complete",NULL,STREAM_COUNT,1,0,0);return true;
}
static void controls_snapshot(fd_usb_audio *a,fd_usb_snapshot *s){
    s->mic_gain_db=atomic_load(&a->mic_gain_db);
    s->mic_input_rms_dbfs10=atomic_load(&a->mic_input_rms_dbfs10);s->mic_input_peak_dbfs10=atomic_load(&a->mic_input_peak_dbfs10);
    s->mic_rms_dbfs10=atomic_load(&a->mic_rms_dbfs10);s->mic_peak_dbfs10=atomic_load(&a->mic_peak_dbfs10);
    s->mic_meter_ready=atomic_load(&a->mic_meter_ready)&&atomic_load(&a->meter_gain_db)==s->mic_gain_db;
    s->mic_clipped_samples=atomic_load(&a->mic_clipped_samples);
    s->headset_volume_percent=atomic_load(&a->headset_volume_percent);s->headset_volume_ready=atomic_load(&a->headset_volume_ready);s->headset_muted=atomic_load(&a->headset_muted);
}
static void publish(fd_usb_audio *a,fd_usb_inputs in){
    uint64_t now=now_ms();fd_usb_snapshot s={.state=atomic_load(&a->state),.fault=atomic_load(&a->fault),.generation=a->generation,.turns=a->turns,.heartbeat_ms=atomic_load(&a->heartbeat),
        .operational=a->c.operational,.startup_ready=atomic_load(&a->startup_ready),.leaving=atomic_load(&a->leaving),.configuration_ready=in.configuration_ready,.tx_eligible=in.tx_eligible,.tuning_revision=in.tuning_revision,.tx_timeout_seconds=atomic_load(&a->tx_timeout_seconds),
        .armed=atomic_load(&a->armed),.pressed=in.pressed,.rf_valid=in.rf_valid,.rf_tx=in.rf_tx,.stopped=atomic_load(&a->stopped)||a->finalizing,
        .rf_disabled=atomic_load(&a->rf_disabled),.qualification_complete=atomic_load(&a->qualification_complete),.qualification_running=atomic_load(&a->qualification_running),
        .qualification_target=a->c.qualification_turns,.qualification_turns=atomic_load(&a->qualification_done),.qualification_loopback=atomic_load(&a->qualification_loopback),.rf_clear_confirmed=a->rf_clear_confirmed,
        .mic_age_ms=age(now,atomic_load(&a->metrics[MIC].last)),.rx_age_ms=age(now,atomic_load(&a->metrics[RADIO_RX].last)),
        .mic_captured=atomic_load(&a->metrics[MIC].captured),.rx_captured=atomic_load(&a->metrics[RADIO_RX].captured),
        .mic_discarded=atomic_load(&a->metrics[MIC].discarded),.rx_discarded=atomic_load(&a->metrics[RADIO_RX].discarded),
        .mic_max_gap_ms=atomic_load(&a->metrics[MIC].max_gap),.rx_max_gap_ms=atomic_load(&a->metrics[RADIO_RX].max_gap),
        .mic_holes=atomic_load(&a->metrics[MIC].holes),.rx_holes=atomic_load(&a->metrics[RADIO_RX].holes),
        .headset_underflows=atomic_load(&a->metrics[HEADSET].underflows),.modem_underflows=atomic_load(&a->metrics[MODEM].payload_underflows),.engine=a->engine?fd_voice_observe(a->engine):a->final_engine};
    controls_snapshot(a,&s);snprintf(s.headset_id,sizeof(s.headset_id),"%s",a->selected_headset_id);snprintf(s.headset_name,sizeof(s.headset_name),"%s",a->selected_headset_name);
    s.submitted_mic=a->submitted_mic;s.rendered_modem=a->rendered_modem;s.modem_tap_samples=a->tap_samples[2];
    s.render_queue_high_water=a->render_queue_high_water;s.render_deferred_count=a->render_deferred_count;s.render_final_deferred_count=a->render_final_deferred_count;s.render_final_flush_calls=a->render_final_flush_calls;s.transport_tail_frames=a->transport_tail_frames;s.modem_written_frames=atomic_load(&a->metrics[MODEM].captured);
    s.qualification_idle_remaining_ms=atomic_load(&a->qualification_running)&&now<a->qualification_idle_until?(unsigned)(a->qualification_idle_until-now):0;
    s.mic_rate_hz=measured_rate(a,MIC,&s.mic_rate_window_ms);s.rx_rate_hz=measured_rate(a,RADIO_RX,&s.rx_rate_window_ms);
    if(a->loop){io_lock(a);s.input_queue=a->capture.n+(a->radio_capture.n*RATE+RADIO_RATE-1)/RADIO_RATE;s.output_queue=a->play.n;s.gaps=a->gaps;io_unlock(a);}
    s.holes=s.mic_holes+s.rx_holes;s.underflows=s.headset_underflows+s.modem_underflows;
    pthread_mutex_lock(&a->mutex);uint64_t first_fault_io_age=a->first_fault_io_age;s.first_fault_code=a->first_fault_code;s.first_fault_pa_error=a->first_fault_pa_error;s.first_fault_operation_ms=a->first_fault_operation_ms;s.first_fault_mic_age_ms=a->first_fault_mic_age;s.first_fault_rx_age_ms=a->first_fault_rx_age;
    snprintf(s.first_fault_origin,sizeof(s.first_fault_origin),"%s",a->first_fault_origin);snprintf(s.first_fault_stream,sizeof(s.first_fault_stream),"%s",a->first_fault_stream);a->snapshot=s;pthread_mutex_unlock(&a->mutex);
    trace_event events[EVENT_MAX];unsigned count;
    pthread_mutex_lock(&a->events_mutex);count=a->event_n;for(unsigned i=0;i<count;++i)events[i]=a->events[(a->event_head+i)%EVENT_MAX];a->event_head=(a->event_head+count)%EVENT_MAX;a->event_n=0;pthread_mutex_unlock(&a->events_mutex);
    if(a->trace){
        unsigned emitted=0;for(;emitted<count&&a->trace_rows<6999;++emitted){trace_event *e=&events[emitted];++a->trace_rows;
            fprintf(a->trace,"{\"kind\":\"event\",\"ms\":%llu,\"state\":%u,\"event\":\"%s\",\"operation\":\"%s\",\"stream\":\"%s\",\"status\":%d,\"pa_errno\":%d,\"duration_ms\":%llu}\n",(unsigned long long)e->ms,e->state,e->event,e->operation,e->stream,e->status,e->error,(unsigned long long)e->duration);
        }
        if(emitted<count||(!a->finalizing&&now-a->last_trace>=100&&a->trace_rows>=6999))a->trace_history_truncated=true;
        if(a->finalizing||((s.stopped||now-a->last_trace>=100)&&a->trace_rows<6999)){a->last_trace=now;a->last_sample_ms=now;++a->trace_rows;
            fputs("{\"kind\":\"sample\"",a->trace);operational_fields(a->trace,a,&s);
            fprintf(a->trace,",\"mic_gain_db\":%d,\"mic_meter_ready\":%s,\"mic_input_rms_dbfs10\":%d,\"mic_input_peak_dbfs10\":%d,\"mic_rms_dbfs10\":%d,\"mic_peak_dbfs10\":%d,\"mic_clipped_samples\":%llu,\"headset_volume_percent\":%u,\"headset_volume_ready\":%s,\"headset_muted\":%s,",
                s.mic_gain_db,s.mic_meter_ready?"true":"false",s.mic_input_rms_dbfs10,s.mic_input_peak_dbfs10,s.mic_rms_dbfs10,s.mic_peak_dbfs10,(unsigned long long)s.mic_clipped_samples,s.headset_volume_percent,s.headset_volume_ready?"true":"false",s.headset_muted?"true":"false");
            fprintf(a->trace,"\"ms\":%llu,\"state\":%u,\"fault\":%u,\"generation\":%llu,\"turns\":%llu,\"rf_valid\":%s,\"rf_tx\":%s,\"ptt\":%s,\"capture\":%u,\"playback\":%u,\"engine_in\":%u,\"engine_out\":%u,\"eoo\":%llu,\"sync\":%s,\"gaps\":%u,\"underflows\":%u,\"rf_disabled\":%s,\"qualification_running\":%s,\"qualification_complete\":%s,\"qualification_loopback\":%s,\"qualification_target\":%u,\"qualification_turns\":%u,\"rf_clear_confirmed\":%s,\"mic_age_ms\":%llu,\"rx_age_ms\":%llu,\"mic_rate_hz\":%u,\"rx_rate_hz\":%u,\"mic_rate_window_ms\":%llu,\"rx_rate_window_ms\":%llu,\"mic_captured\":%llu,\"rx_captured\":%llu,\"mic_discarded\":%llu,\"rx_discarded\":%llu,\"mic_holes\":%u,\"rx_holes\":%u,\"headset_underflows\":%u,\"modem_underflows\":%u,\"submitted_rx\":%llu,\"submitted_mic\":%llu,\"rendered_rx\":%llu,\"rendered_modem\":%llu,\"io_heartbeat_age_ms\":%u,\"mic_suspended\":%d,\"rx_suspended\":%d,\"headset_suspended\":%d,\"modem_suspended\":%d,\"preparation_excluded_ms\":[%llu,%llu],\"preparation_excluded_frames\":[%llu,%llu],\"first_fault\":{\"code\":%u,\"origin\":\"%s\",\"stream\":\"%s\",\"pa_errno\":%u,\"operation_ms\":%llu,\"mic_age_ms\":%llu,\"rx_age_ms\":%llu,\"io_heartbeat_age_ms\":%llu}}\n",
                (unsigned long long)now,s.state,s.fault,(unsigned long long)s.generation,(unsigned long long)s.turns,in.rf_valid?"true":"false",in.rf_tx?"true":"false",in.pressed?"true":"false",s.input_queue,s.output_queue,s.engine.input_depth,s.engine.output_depth,(unsigned long long)s.engine.eoo_count,s.engine.synchronized?"true":"false",s.gaps,s.underflows,s.rf_disabled?"true":"false",s.qualification_running?"true":"false",s.qualification_complete?"true":"false",s.qualification_loopback?"true":"false",s.qualification_target,s.qualification_turns,s.rf_clear_confirmed?"true":"false",(unsigned long long)s.mic_age_ms,(unsigned long long)s.rx_age_ms,s.mic_rate_hz,s.rx_rate_hz,(unsigned long long)s.mic_rate_window_ms,(unsigned long long)s.rx_rate_window_ms,(unsigned long long)s.mic_captured,(unsigned long long)s.rx_captured,(unsigned long long)s.mic_discarded,(unsigned long long)s.rx_discarded,s.mic_holes,s.rx_holes,s.headset_underflows,s.modem_underflows,(unsigned long long)a->submitted_rx,(unsigned long long)a->submitted_mic,(unsigned long long)a->rendered_rx,(unsigned long long)a->rendered_modem,age(now,atomic_load(&a->io_heartbeat)),atomic_load(&a->metrics[MIC].suspended),atomic_load(&a->metrics[RADIO_RX].suspended),atomic_load(&a->metrics[HEADSET].suspended),atomic_load(&a->metrics[MODEM].suspended),(unsigned long long)a->preparation_excluded_ms[MIC],(unsigned long long)a->preparation_excluded_ms[RADIO_RX],(unsigned long long)a->preparation_excluded_frames[MIC],(unsigned long long)a->preparation_excluded_frames[RADIO_RX],s.first_fault_code,s.first_fault_origin,s.first_fault_stream,s.first_fault_pa_error,(unsigned long long)s.first_fault_operation_ms,(unsigned long long)s.first_fault_mic_age_ms,(unsigned long long)s.first_fault_rx_age_ms,(unsigned long long)first_fault_io_age);
        }
        fflush(a->trace);
    }
}
/* Radio configuration runs elsewhere. This gate never writes radio state or
 * recreates a codec: it drops old queues, resets acquisition after the applied
 * tuning revision, then requires a newer known-RX packet with PTT released. */
static bool operational_tick(fd_usb_audio *a,fd_usb_inputs *in){
    if(!a->c.operational||atomic_load(&a->leaving))return true;
    unsigned st=atomic_load(&a->state);
    if(st!=FD_USB_RX&&st!=FD_USB_TUNING)return true;
    if(st==FD_USB_RX&&(!atomic_load(&a->startup_ready)||!in->configuration_ready||in->tuning_revision!=a->applied_tuning_revision)){
        atomic_store(&a->armed,false);a->c.control.permit(a->c.control.context,false);a->admission_permitted=false;
        a->configuration_seen=false;a->configuration_sequence=in->sequence;state(a,FD_USB_TUNING);reset_queues(a);
        if(!stream_op(a,HEADSET,true,0))return false;
        event(a,"configuration-wait","rx-tuning",STREAM_COUNT,0,0,0);
    }
    if(atomic_load(&a->state)!=FD_USB_TUNING)return true;
    if(!in->configuration_ready){a->configuration_seen=false;return true;}
    if(!a->configuration_seen||in->tuning_revision!=a->applied_tuning_revision){
        uint64_t revision=in->tuning_revision;
        if(!direction(a,FD_VOICE_RX)||!actual_inputs(a,in))return false;
        a->applied_tuning_revision=revision;a->configuration_sequence=in->sequence;
        a->configuration_seen=in->configuration_ready&&in->tuning_revision==revision;
        return true;
    }
    if(in->rf_tx){FAIL(a,U_STATUS,"unexpected-tx-during-configuration");return false;}
    if(in->pressed||in->sequence<=a->configuration_sequence)return true;
    uint64_t now=now_ms();
    for(unsigned i=0;i<2;++i)if(atomic_load(&a->metrics[i].state)!=PA_STREAM_READY||age(now,atomic_load(&a->metrics[i].last))>CAPTURE_TIMEOUT_MS)return true;
    pthread_mutex_lock(&a->control_mutex);
    if(atomic_load(&a->leaving)||atomic_load(&a->stop)){pthread_mutex_unlock(&a->control_mutex);return true;}
    bool permitted=!in->tx_eligible||a->c.control.permit(a->c.control.context,true);
    if(permitted){atomic_store(&a->rf_disabled,!in->tx_eligible);atomic_store(&a->armed,in->tx_eligible);}
    pthread_mutex_unlock(&a->control_mutex);
    if(!permitted){
        /* A real press or a newer tuning request can win between our sample
         * and the adapter gate. Wait for a released, fresh RX observation. */
        a->configuration_seen=false;event(a,"configuration-admission-retry","operational",STREAM_COUNT,0,0,0);return true;
    }
    bool first=!atomic_exchange(&a->startup_ready,true);
    if(first)readiness(a,*in);
    if(atomic_load(&a->fault)||atomic_load(&a->stop))return false;
    a->admission_permitted=in->tx_eligible;a->rf_clear_confirmed=true;a->permit_press=true;state(a,FD_USB_RX);
    event(a,first?"startup-ready":"configuration-ready","operational",STREAM_COUNT,1,0,0);return true;
}
static void *run(void *p){
    fd_usb_audio *a=p;fd_usb_inputs actual={0};
    if(a->c.retain_runtime_traces){
        int retained=fd_usb_runtime_trace_prune(a->c.trace_prefix,0);
        if(retained<0){
            /* No new runtime files may escape a failed retention bound. Set
             * this before publishing first-fault readiness to the watchdog;
             * cleanup therefore cannot create sidecars in the failed tree. */
            a->c.trace_prefix=NULL;atomic_store(&a->storage_error,-retained);
            fail_at(a,U_IO,"runtime-retention",STREAM_COUNT,-retained,0);goto done;
        }
    }
    if(a->c.trace_prefix){
        char path[1024];snprintf(path,sizeof(path),"%s.jsonl",a->c.trace_prefix);a->trace=fopen(path,"wx");
        static const char *const names[]={"mic","radio-rx","modem","decoded"};for(unsigned i=0;i<4;++i){snprintf(path,sizeof(path),"%s-%s.raw",a->c.trace_prefix,names[i]);a->taps[i]=fopen(path,"wbx");}
        if(a->trace){fprintf(a->trace,"{\"kind\":\"session\",\"schema\":\"x6200.usb-audio.v%u\",",a->c.operational?4:3);identity(a->trace,a);operational_fields(a->trace,a,NULL);
            fprintf(a->trace,",\"usb_output_contract_version\":1,\"rf_disabled\":%s,\"qualification_target\":%u,\"qualification_idle_ms\":%u,\"trace_pcm_seconds\":%u,\"operation_timeout_ms\":%u,\"capture_timeout_ms\":%u,\"preparation_capture_timeout_ms\":%u}\n",atomic_load(&a->rf_disabled)?"true":"false",a->c.qualification_turns,a->c.qualification_idle_ms,trace_seconds(a),OP_TIMEOUT_MS,CAPTURE_TIMEOUT_MS,PREPARATION_CAPTURE_TIMEOUT_MS);
            fflush(a->trace);current_session(a);char metadata[768];snprintf(metadata,sizeof(metadata),"%s.jsonl",a->c.trace_prefix);persist_record(a,"metadata",metadata);}

    }
    if(!prepare(a)){if(!atomic_load(&a->fault)&&!atomic_load(&a->leaving))FAIL(a,U_PREPARE,"initial-audio-preparation");goto done;}
    a->permit_press=false;a->rf_clear_confirmed=true;state(a,FD_USB_RX);
    while(!atomic_load(&a->stop)){
        atomic_store(&a->heartbeat,now_ms());if(!actual_inputs(a,&actual)||!operational_tick(a,&actual))break;
        if(atomic_load(&a->qualification_running)&&actual.pressed){FAIL(a,U_PTT,"panel-held-during-evaluation");break;}
        if(atomic_exchange(&a->qualification_requested,false)){
            if(atomic_load(&a->state)!=FD_USB_RX||!atomic_load(&a->rf_disabled)||actual.pressed||actual.rf_tx){FAIL(a,U_PREPARE,"evaluation-start-state");break;}
            atomic_store(&a->qualification_done,0);atomic_store(&a->qualification_complete,false);
            io_lock(a);for(unsigned i=0;i<2;++i){a->rate_start_ms[i]=atomic_load(&a->metrics[i].last);a->rate_start_count[i]=atomic_load(&a->metrics[i].captured);a->preparation_excluded_ms[i]=a->preparation_excluded_frames[i]=0;}io_unlock(a);
            a->qualification_rate_started=true;a->qualification_idle_until=now_ms()+a->c.qualification_idle_ms;atomic_store(&a->qualification_running,true);a->permit_press=true;state(a,FD_USB_RX);event(a,"qualification-start",NULL,STREAM_COUNT,0,0,0);
        }
        fd_usb_inputs in=effective_inputs(a,actual);unsigned st=atomic_load(&a->state);uint64_t now=now_ms();
        unsigned required=st==FD_USB_RX||st==FD_USB_TUNING?RADIO_RX:MIC;
        if((st==FD_USB_RX||st==FD_USB_TUNING||st==FD_USB_TX||st==FD_USB_KEY_PENDING)&&age(now,atomic_load(&a->metrics[required].last))>CAPTURE_TIMEOUT_MS){fail_at(a,U_CAPTURE_STALE,"required-capture-age",required,0,0);break;}
        if(st==FD_USB_RX){
            if(in.rf_tx){FAIL(a,U_STATUS,"unexpected-tx-in-rx");break;}
            if(!in.pressed&&!atomic_load(&a->leaving))a->permit_press=true;
            bool admitted=!atomic_load(&a->leaving)&&(atomic_load(&a->qualification_running)||atomic_load(&a->armed));
            if(in.pressed&&a->permit_press&&admitted){
                a->permit_press=false;a->release=in.release;atomic_store(&a->turn_timeout_ms,a->c.operational?atomic_load(&a->tx_timeout_seconds)*1000:20000);state(a,FD_USB_START);a->rf_clear_confirmed=false;
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
            else if(done_key&&!ok){if(atomic_load(&a->leaving)){atomic_store(&a->stop,true);break;}FAIL(a,U_CONTROL,"key-request-failed");break;}
            else if(done_key&&in.rf_tx){
                if(!mic_begin(a)||!actual_inputs(a,&actual))break;
                in=effective_inputs(a,actual);
                if(!in.pressed||in.release!=a->release){io_lock(a);a->mic_live=false;io_unlock(a);a->pending_clear=request_clear(a,false);a->rx_sequence=actual.sequence;state(a,FD_USB_UNKEY);}
                else if(!in.rf_tx){FAIL(a,U_STATUS,"tx-status-disappeared-at-capture-start");break;}
            }
            else if(now-a->phase_at>2000){FAIL(a,U_KEY,"tx-acknowledgement-timeout");break;}
        }else if(st==FD_USB_TX){
            if(!in.rf_tx){FAIL(a,U_STATUS,"tx-status-disappeared");break;}
            if(!in.pressed||in.release!=a->release||atomic_load(&a->leaving)){
                a->mic_navigation_close=atomic_load(&a->leaving);
#if defined(HOST_BUILD)
                if(!a->mic_navigation_close&&usb_test_mic_release_observed)usb_test_mic_release_observed();
#endif
                if(!mic_close_begin(a,now_ms()))break;
            }
        }
        st=atomic_load(&a->state);
        if(st==FD_USB_RX||st==FD_USB_TX||st==FD_USB_DRAIN)process_audio(a,st);
        /* Trigger/prebuffer acknowledgements can yield while RF is active.
         * Never count a drain using the observation from before that wait,
         * or advance a turn after an operation failed or Stop was requested. */
        if(atomic_load(&a->stop)||atomic_load(&a->fault))break;
        if(st==FD_USB_DRAIN){
            if(!actual_inputs(a,&actual))break;
            in=effective_inputs(a,actual);
            if(!in.rf_tx){FAIL(a,U_STATUS,a->backend_requested?"tx-status-disappeared-during-backend-tail":"tx-status-disappeared-during-drain");break;}
            io_lock(a);int ack=a->drain_ack;io_unlock(a);
            if(now_ms()-a->phase_at>3000){FAIL(a,U_DRAIN,"modem-drain-timeout");break;}
            if(ack<0){FAIL(a,U_DRAIN,"modem-drain-rejected");break;}
            if(ack>0&&backend_tail_step(a)){
                if(atomic_load(&a->stop)||atomic_load(&a->fault)||!actual_inputs(a,&actual))break;
                in=effective_inputs(a,actual);
                if(!in.rf_tx){FAIL(a,U_STATUS,"tx-status-disappeared-during-backend-tail");break;}
                if(atomic_load(&a->stop)||atomic_load(&a->fault))break;
                event(a,"backend-tail-complete","modem-backend-timing",MODEM,1,0,(a->backend_completed_us-a->backend_ack_us)/1000);backend_tail_report(a,true);
                if(atomic_load(&a->stop)||atomic_load(&a->fault))break;
                a->pending_clear=request_clear(a,false);a->rx_sequence=actual.sequence;state(a,FD_USB_UNKEY);
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
                if(atomic_load(&a->leaving)){atomic_store(&a->stop,true);break;}
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
        capture_close_report(a,false);backend_tail_report(a,false);
        io_lock(a);if(a->drain){pa_operation_cancel(a->drain);pa_operation_unref(a->drain);a->drain=NULL;}
        if(a->mic_close_operation){pa_operation_cancel(a->mic_close_operation);pa_operation_unref(a->mic_close_operation);a->mic_close_operation=NULL;}
        if(a->backend_operation){pa_operation_cancel(a->backend_operation);pa_operation_unref(a->backend_operation);a->backend_operation=NULL;}
        if(a->volume_operation){pa_operation_cancel(a->volume_operation);pa_operation_unref(a->volume_operation);a->volume_operation=NULL;}
        if(a->io_timer){pa_threaded_mainloop_get_api(a->loop)->time_free(a->io_timer);a->io_timer=NULL;}
        for(unsigned i=0;i<STREAM_COUNT;++i)if(a->streams[i]){pa_stream_set_state_callback(a->streams[i],NULL,NULL);pa_stream_set_moved_callback(a->streams[i],NULL,NULL);pa_stream_set_suspended_callback(a->streams[i],NULL,NULL);pa_stream_set_read_callback(a->streams[i],NULL,NULL);pa_stream_set_write_callback(a->streams[i],NULL,NULL);pa_stream_set_underflow_callback(a->streams[i],NULL,NULL);pa_stream_disconnect(a->streams[i]);pa_stream_unref(a->streams[i]);a->streams[i]=NULL;}
        if(a->context){pa_context_set_state_callback(a->context,NULL,NULL);pa_context_disconnect(a->context);pa_context_unref(a->context);a->context=NULL;}
        io_unlock(a);pa_threaded_mainloop_stop(a->loop);
    }
    a->final_engine=fd_voice_observe(a->engine);fd_voice_destroy(a->engine);a->engine=NULL;
    if(a->radio_input)src_delete(a->radio_input);
    if(a->radio_output)src_delete(a->radio_output);
    if(a->loop){pa_threaded_mainloop_free(a->loop);a->loop=NULL;}
    /* All metadata writers finish before the terminal receipt is formed.
     * In particular, a late persistent first-fault error cannot appear after
     * a completion that claims storage succeeded. */
    atomic_store(&a->watch_exit,true);pthread_join(a->watch,NULL);
    for(unsigned i=0;i<4;++i)if(a->taps[i]){if(fclose(a->taps[i]))atomic_store(&a->storage_error,EIO);a->taps[i]=NULL;}
    a->finalizing=true;state(a,atomic_load(&a->fault)>U_STOP?FD_USB_FAULT:FD_USB_DONE);publish(a,actual);
    a->trace_complete=a->trace&&!ferror(a->trace);
    if(a->trace){if(fclose(a->trace))a->trace_complete=false;a->trace=NULL;}
    completion(a);atomic_store(&a->stopped,true);
    return NULL;
}
static void fault_sidecar(fd_usb_audio *a){
    if(!a->c.trace_prefix)return;
    char path[1024],temporary[1080];snprintf(path,sizeof(path),"%s-fault.json",a->c.trace_prefix);snprintf(temporary,sizeof(temporary),"%s.tmp",path);
    FILE *f=fopen(temporary,"wx");if(!f)return;
    /* The release/acquire fault_record_ready fence makes these first-fault
     * fields immutable here. Storage I/O holds no shared runtime mutex. */
    fputs("{",f);identity(f,a);
    fprintf(f,",\"kind\":\"first-fault\",\"ms\":%llu,\"state\":%u,\"rf_disabled\":%s,\"first_fault\":{\"code\":%u,\"origin\":\"%s\",\"stream\":\"%s\",\"pa_errno\":%u,\"operation_ms\":%llu,\"mic_age_ms\":%llu,\"rx_age_ms\":%llu,\"io_heartbeat_age_ms\":%llu},\"context_state\":%d,\"mic_state\":%d,\"rx_state\":%d,\"headset_state\":%d,\"modem_state\":%d,\"mic_suspended\":%d,\"rx_suspended\":%d,\"headset_suspended\":%d,\"modem_suspended\":%d,\"mic_holes\":%u,\"rx_holes\":%u,\"mic_captured\":%llu,\"rx_captured\":%llu}\n",
        (unsigned long long)now_ms(),a->first_fault_state,atomic_load(&a->rf_disabled)?"true":"false",a->first_fault_code,a->first_fault_origin,a->first_fault_stream,a->first_fault_pa_error,(unsigned long long)a->first_fault_operation_ms,(unsigned long long)a->first_fault_mic_age,(unsigned long long)a->first_fault_rx_age,(unsigned long long)a->first_fault_io_age,atomic_load(&a->context_state_id),atomic_load(&a->metrics[MIC].state),atomic_load(&a->metrics[RADIO_RX].state),atomic_load(&a->metrics[HEADSET].state),atomic_load(&a->metrics[MODEM].state),atomic_load(&a->metrics[MIC].suspended),atomic_load(&a->metrics[RADIO_RX].suspended),atomic_load(&a->metrics[HEADSET].suspended),atomic_load(&a->metrics[MODEM].suspended),atomic_load(&a->metrics[MIC].holes),atomic_load(&a->metrics[RADIO_RX].holes),(unsigned long long)atomic_load(&a->metrics[MIC].captured),(unsigned long long)atomic_load(&a->metrics[RADIO_RX].captured));
    bool written=!ferror(f);if(fclose(f)!=0)written=false;
    if(written&&link(temporary,path)==0)persist_record(a,"fault",path); /* Immutable first fault. */
    (void)unlink(temporary);
}
static void *watch(void *p){
    fd_usb_audio *a=p;bool recorded=false;
    while(!atomic_load(&a->watch_exit)){
        uint64_t now=now_ms(),tx=atomic_load(&a->tx_at),beat=atomic_load(&a->heartbeat),io=atomic_load(&a->io_heartbeat);
        if(!atomic_load(&a->stop)){
            if(tx&&now>=tx&&now-tx>atomic_load(&a->turn_timeout_ms))FAIL(a,U_TIMEOUT,"turn-deadline");
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
    if(!c||!c->control.observe||!c->control.key||!c->control.clear||
       (c->operational&&(!c->control.permit||(c->tx_timeout_seconds&&(c->tx_timeout_seconds<30||c->tx_timeout_seconds>600)))))return NULL;
#if !defined(HOST_BUILD)
    if(c->source||c->sink)return NULL; /* Production selection is paired physical USB only. */
#endif
    if(c->headset_id&&strlen(c->headset_id)>=256)return NULL;
    if(c->retain_runtime_traces&&(!c->operational||!c->trace_prefix||strncmp(c->trace_prefix,"/tmp/freedv-usb-",16)||strchr(c->trace_prefix+5,'/')))return NULL;
    fd_usb_audio *a=calloc(1,sizeof(*a));if(!a)return NULL;a->c=*c;const char *base=c->trace_prefix?strrchr(c->trace_prefix,'/'):NULL;snprintf(a->session_id,sizeof(a->session_id),"%s",c->trace_prefix?(base?base+1:c->trace_prefix):"untraced");
    snprintf(a->requested_headset_id,sizeof(a->requested_headset_id),"%s",c->headset_id?c->headset_id:"");a->c.headset_id=a->requested_headset_id;
    if(c->operational){a->c.qualification_turns=0;a->c.qualification_idle_ms=0;a->c.qualification_control_delay_ms=0;}
    atomic_store(&a->tx_timeout_seconds,c->tx_timeout_seconds?c->tx_timeout_seconds:180);atomic_store(&a->turn_timeout_ms,c->operational?atomic_load(&a->tx_timeout_seconds)*1000:20000);
    pthread_mutex_init(&a->mutex,NULL);pthread_mutex_init(&a->events_mutex,NULL);pthread_mutex_init(&a->control_mutex,NULL);pthread_cond_init(&a->control_changed,NULL);
    atomic_store(&a->heartbeat,now_ms());atomic_store(&a->io_heartbeat,now_ms());atomic_store(&a->rf_disabled,c->operational?true:c->rf_disabled);
    if(c->operational)c->control.permit(c->control.context,false);
    atomic_store(&a->mic_gain_db,fd_usb_mic_gain_clamp(c->mic_gain_db));
    atomic_store(&a->mic_input_rms_dbfs10,FD_USB_LEVEL_FLOOR_DBFS10);atomic_store(&a->mic_input_peak_dbfs10,FD_USB_LEVEL_FLOOR_DBFS10);
    atomic_store(&a->mic_rms_dbfs10,FD_USB_LEVEL_FLOOR_DBFS10);atomic_store(&a->mic_peak_dbfs10,FD_USB_LEVEL_FLOOR_DBFS10);
    if(pthread_create(&a->control_worker,NULL,control_run,a))goto sync_failure;
    /* Create the monitor first so even an immediate preparation failure has
     * a valid monitor handle and a complete evidence-writer join boundary. */
    if(pthread_create(&a->watch,NULL,watch,a))goto control_failure;
    if(pthread_create(&a->worker,NULL,run,a)){
        atomic_store(&a->watch_exit,true);pthread_join(a->watch,NULL);goto control_failure;
    }
    return a;
control_failure:
    pthread_mutex_lock(&a->control_mutex);a->control_exit=true;pthread_cond_signal(&a->control_changed);pthread_mutex_unlock(&a->control_mutex);pthread_join(a->control_worker,NULL);
sync_failure:
    pthread_cond_destroy(&a->control_changed);pthread_mutex_destroy(&a->control_mutex);pthread_mutex_destroy(&a->events_mutex);pthread_mutex_destroy(&a->mutex);free(a);return NULL;
}

void fd_usb_audio_arm(fd_usb_audio *a,bool armed){if(a&&!a->c.operational&&!atomic_load(&a->stop)&&!atomic_load(&a->rf_disabled))atomic_store(&a->armed,armed);}
bool fd_usb_audio_set_tx_timeout(fd_usb_audio *a,unsigned seconds){
    if(!a||!a->c.operational||seconds<30||seconds>600||atomic_load(&a->stop)||atomic_load(&a->leaving))return false;
    unsigned st=atomic_load(&a->state);if(st!=FD_USB_RX&&st!=FD_USB_TUNING)return false;
    atomic_store(&a->tx_timeout_seconds,seconds);return true;
}
bool fd_usb_audio_set_mic_gain(fd_usb_audio *a,int gain_db){
    if(!a||atomic_load(&a->stop))return false;
    gain_db=fd_usb_mic_gain_clamp(gain_db);
    if(atomic_exchange(&a->mic_gain_db,gain_db)!=gain_db)atomic_store(&a->mic_meter_ready,false);
    return true;
}
bool fd_usb_audio_adjust_headset_volume(fd_usb_audio *a,int delta_percent){
    if(!a||atomic_load(&a->stop)||!atomic_load(&a->headset_volume_ready))return false;
    if(!delta_percent)return true;
    uint_fast64_t prior=atomic_load(&a->headset_volume_request),next;
    do{
        int64_t percent=(int64_t)(prior&127)+delta_percent;if(percent<0)percent=0;if(percent>100)percent=100;
        next=((prior&~UINT64_C(255))+256)|(uint64_t)percent|(delta_percent>0?0:prior&VOLUME_MUTE_BIT);
    }while(!atomic_compare_exchange_weak(&a->headset_volume_request,&prior,next));
    return true;
}
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
void fd_usb_audio_leave(fd_usb_audio *a){
    if(!a||atomic_load(&a->stopped)||atomic_exchange(&a->leaving,true))return;
    pthread_mutex_lock(&a->control_mutex);atomic_store(&a->armed,false);
    if(a->c.control.permit)a->c.control.permit(a->c.control.context,false);
    pthread_mutex_unlock(&a->control_mutex);
    /* PREP/START/KEY_PENDING may be blocked in a bounded operation; the
     * epoch cancels any late key and queues OFF without waiting for audio. */
    unsigned st=atomic_load(&a->state);
    if(st!=FD_USB_TX&&st!=FD_USB_DRAIN){request_clear(a,false);atomic_store(&a->stop,true);}
}
fd_usb_snapshot fd_usb_audio_snapshot(fd_usb_audio *a){
    if(!a)return(fd_usb_snapshot){.state=FD_USB_DONE,.stopped=true};
    pthread_mutex_lock(&a->mutex);fd_usb_snapshot s=a->snapshot;
    if(atomic_load(&a->fault_record_ready)){
        s.first_fault_code=a->first_fault_code;s.first_fault_pa_error=a->first_fault_pa_error;s.first_fault_operation_ms=a->first_fault_operation_ms;s.first_fault_mic_age_ms=a->first_fault_mic_age;s.first_fault_rx_age_ms=a->first_fault_rx_age;
        snprintf(s.first_fault_origin,sizeof(s.first_fault_origin),"%s",a->first_fault_origin);snprintf(s.first_fault_stream,sizeof(s.first_fault_stream),"%s",a->first_fault_stream);
    }
    pthread_mutex_unlock(&a->mutex);
    controls_snapshot(a,&s);
    s.leaving=atomic_load(&a->leaving);s.operational=a->c.operational;s.tx_timeout_seconds=atomic_load(&a->tx_timeout_seconds);
    s.stopped=atomic_load(&a->stopped);s.state=atomic_load(&a->state);s.fault=atomic_load(&a->fault);return s;
}
void fd_usb_audio_close(fd_usb_audio *a){
    if(!a||!atomic_load(&a->stopped))return;
    pthread_join(a->worker,NULL);
    pthread_cond_destroy(&a->control_changed);pthread_mutex_destroy(&a->control_mutex);pthread_mutex_destroy(&a->events_mutex);pthread_mutex_destroy(&a->mutex);free(a);
}
