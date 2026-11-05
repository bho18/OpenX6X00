// SPDX-License-Identifier: LGPL-2.1-or-later
#define _POSIX_C_SOURCE 200809L
#include "usb_voice.h"
#include <pulse/pulseaudio.h>
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
typedef struct {int16_t p[QMAX];unsigned head,n;} pcm_queue;
enum { U_OK,U_STOP,U_DEVICE,U_PTT,U_STATUS,U_PROTECTION,U_QUEUE,U_ENGINE,U_KEY,U_DRAIN,U_TIMEOUT,U_STALL,U_IDENTITY,U_PREPARE };
struct fd_usb_audio {
    fd_usb_config c;pthread_t worker,watch;pthread_mutex_t mutex;
    atomic_bool stop,stopped,armed;atomic_uint fault,state;
    atomic_uint_fast64_t heartbeat,tx_at;
    fd_usb_snapshot snapshot;
    pa_mainloop *loop;pa_context *context;pa_stream *mic,*rx,*headset,*modem;
    pa_operation *drain;int drain_ack;
    fd_voice_engine *engine;uint64_t generation,turns,release,phase_at,rx_sequence;
    SRC_STATE *radio_input,*radio_output;
    pcm_queue capture,radio_capture,play;bool modem_started,headset_started,permit_press,finishing,producer_finished,radio_output_ended;
    unsigned holes,underflows,gaps;uint64_t mic_last,rx_last;
    char source[512],sink[512],source_card[32],sink_card[32];unsigned sources,sinks;
    FILE *trace,*taps[4];unsigned trace_rows;uint64_t tap_samples[4],last_trace;
};
static uint64_t now_ms(void){struct timespec t;clock_gettime(CLOCK_MONOTONIC,&t);return(uint64_t)t.tv_sec*1000+t.tv_nsec/1000000;}
const char *fd_usb_fault_text(unsigned n){static const char *s[]={"","Stopped","Audio device lost","Panel PTT unavailable","Radio status lost or unexpected TX","Radio protection","Audio backlog","Codec failure","TX not confirmed","Modem drain failed","TX time limit","Audio worker stalled","Connect one Shokz Loop110","Audio preparation failed"};return n<sizeof(s)/sizeof(*s)?s[n]:"USB mode fault";}
static void failed(fd_usb_audio *a,unsigned fault){unsigned expected=0;atomic_compare_exchange_strong(&a->fault,&expected,fault);atomic_store(&a->armed,false);atomic_store(&a->stop,true);a->c.control.clear(a->c.control.context,true);}
static void state(fd_usb_audio *a,fd_usb_state s){atomic_store(&a->state,s);a->phase_at=now_ms();}
static void push(fd_usb_audio *a,pcm_queue *q,const int16_t *p,unsigned n){if(n>QMAX-q->n){failed(a,U_QUEUE);return;}for(unsigned i=0;i<n;++i)q->p[(q->head+q->n+i)%QMAX]=p[i];q->n+=n;}
static unsigned pop(pcm_queue *q,int16_t *p,unsigned n){if(n>q->n)n=q->n;for(unsigned i=0;i<n;++i)p[i]=q->p[(q->head+i)%QMAX];q->head=(q->head+n)%QMAX;q->n-=n;return n;}
static unsigned convert(fd_usb_audio *a,SRC_STATE *src,const int16_t *input,unsigned count,int16_t *output,double ratio,bool final){
    float in[4096],out[4608];if(count>4096){failed(a,U_ENGINE);return 0;}
    for(unsigned i=0;i<count;++i)in[i]=input[i]/32768.f;
    SRC_DATA data={.data_in=in,.data_out=out,.input_frames=count,.output_frames=4608,.src_ratio=ratio,.end_of_input=final};
    if(src_process(src,&data) || data.input_frames_used!=(long)count){failed(a,U_ENGINE);return 0;}
    src_float_to_short_array(out,output,(int)data.output_frames_gen);return(unsigned)data.output_frames_gen;
}
static void tap(fd_usb_audio *a,unsigned slot,const int16_t *p,unsigned n){if(!a->taps[slot] || a->tap_samples[slot]>=RATE*30)return;if(n>RATE*30-a->tap_samples[slot])n=RATE*30-a->tap_samples[slot];if(fwrite(p,2,n,a->taps[slot])!=n)failed(a,U_DEVICE);a->tap_samples[slot]+=n;}
static const char *device(fd_usb_audio *a,pa_stream *s){return s==a->mic?a->source:s==a->headset?a->sink:s==a->rx?a->c.radio_source:a->c.radio_sink;}
static void stream_state(pa_stream *s,void *p){fd_usb_audio *a=p;pa_stream_state_t st=pa_stream_get_state(s);if(st==PA_STREAM_READY){const char *d=pa_stream_get_device_name(s);if(!d || strcmp(d,device(a,s)))failed(a,U_DEVICE);}else if(!PA_STREAM_IS_GOOD(st))failed(a,U_DEVICE);}
static void read_pcm(pa_stream *s,size_t requested,void *p){
    (void)requested;fd_usb_audio *a=p;
    for(unsigned i=0;i<64;++i){const void *data;size_t bytes;if(pa_stream_peek(s,&data,&bytes)<0){failed(a,U_DEVICE);return;}if(!bytes)return;
        if(!data || bytes%2){++a->holes;failed(a,U_DEVICE);}else{
            if(s==a->mic)a->mic_last=now_ms();else a->rx_last=now_ms();
            unsigned st=atomic_load(&a->state);
            if(s==a->mic && (st==FD_USB_TX || (st==FD_USB_START && atomic_load(&a->tx_at))))push(a,&a->capture,data,(unsigned)bytes/2);
            if(s==a->rx && st==FD_USB_RX)push(a,&a->radio_capture,data,(unsigned)bytes/2);
        }
        if(pa_stream_drop(s)<0){failed(a,U_DEVICE);return;}
    }failed(a,U_QUEUE);
}
static void write_pcm(pa_stream *s,size_t bytes,void *p){
    fd_usb_audio *a=p;int16_t pcm[2048];if(bytes>RATE*2){failed(a,U_QUEUE);return;}
    while(bytes && !atomic_load(&a->stop)){
        unsigned n=(unsigned)bytes/2;if(n>2048)n=2048;if(!n){failed(a,U_DEVICE);return;}
        unsigned st=atomic_load(&a->state);bool tx=s==a->modem;
        if(tx){if(st!=FD_USB_TX && st!=FD_USB_DRAIN)return;if(!a->modem_started && a->play.n<RADIO_RATE/4 && !a->producer_finished)return;if(n>a->play.n)n=a->play.n;if(!n)return;pop(&a->play,pcm,n);a->modem_started=true;
            /* Mono is duplicated by the radio sink. Conservative -12 dB
             * amplitude limits drive until RF gain/ALC has been measured. */
            for(unsigned i=0;i<n;++i)pcm[i]/=4;
        }else{
            memset(pcm,0,n*2);
            if(st==FD_USB_RX){if(!a->headset_started && a->play.n>=RATE/4)a->headset_started=true;
                if(a->headset_started && pop(&a->play,pcm,n)<n)++a->gaps;}
        }
        if(pa_stream_write(s,pcm,n*2,NULL,0,PA_SEEK_RELATIVE)<0){failed(a,U_DEVICE);return;}bytes-=n*2;
    }
}
static void underflow(pa_stream *s,void *p){fd_usb_audio *a=p;if(s==a->modem && (!a->modem_started || pa_stream_get_underflow_index(s)<=0 || a->drain))return;++a->underflows;if(s==a->modem)failed(a,U_DRAIN);}
static void context_state(pa_context *c,void *p){if(!PA_CONTEXT_IS_GOOD(pa_context_get_state(c)))failed(p,U_DEVICE);}
static bool physical(pa_proplist *p,const char *driver){const char *bus=pa_proplist_gets(p,"device.bus"),*vendor=pa_proplist_gets(p,"device.vendor.id"),*product=pa_proplist_gets(p,"device.product.id");return driver && !strncmp(driver,"module-alsa-",12) && bus && !strcmp(bus,"usb") && vendor && !strcmp(vendor,"3511") && product && !strcmp(product,"2b1e");}
static void source_info(pa_context *c,const pa_source_info *i,int end,void *p){(void)c;fd_usb_audio *a=p;if(end || !i || i->monitor_of_sink!=PA_INVALID_INDEX || !physical(i->proplist,i->driver))return;const char *card=pa_proplist_gets(i->proplist,"alsa.card");if(!card || strlen(i->name)>=sizeof(a->source) || strlen(card)>=sizeof(a->source_card))return;++a->sources;strcpy(a->source,i->name);strcpy(a->source_card,card);}
static void sink_info(pa_context *c,const pa_sink_info *i,int end,void *p){(void)c;fd_usb_audio *a=p;if(end || !i || !physical(i->proplist,i->driver))return;const char *card=pa_proplist_gets(i->proplist,"alsa.card");if(!card || strlen(i->name)>=sizeof(a->sink) || strlen(card)>=sizeof(a->sink_card))return;++a->sinks;strcpy(a->sink,i->name);strcpy(a->sink_card,card);}
static void pump(fd_usb_audio *a){int result=0;atomic_store(&a->heartbeat,now_ms());if(pa_mainloop_iterate(a->loop,0,&result)<0)failed(a,U_DEVICE);struct timespec t={0,1000000};nanosleep(&t,NULL);}
static bool operation(fd_usb_audio *a,pa_operation *o){if(!o)return false;uint64_t until=now_ms()+500;while(pa_operation_get_state(o)==PA_OPERATION_RUNNING && !atomic_load(&a->stop) && now_ms()<until)pump(a);bool ok=pa_operation_get_state(o)==PA_OPERATION_DONE;if(!ok)pa_operation_cancel(o);pa_operation_unref(o);return ok;}
static void op_done(pa_stream *s,int success,void *p){(void)s;*(int *)p=success?1:-1;}
static bool stream_op(fd_usb_audio *a,pa_stream *s,bool flush,int cork){int status=0;pa_operation *o=flush?pa_stream_flush(s,op_done,&status):pa_stream_cork(s,cork,op_done,&status);return operation(a,o) && status==1;}
static bool fence(fd_usb_audio *a,fd_voice_direction direction){
    a->capture.n=a->capture.head=a->play.n=a->play.head=a->radio_capture.n=a->radio_capture.head=0;a->headset_started=a->modem_started=a->producer_finished=a->radio_output_ended=false;
    if(src_reset(a->radio_input) || src_reset(a->radio_output))return false;
    if(a->drain){pa_operation_cancel(a->drain);pa_operation_unref(a->drain);a->drain=NULL;}
    if(!stream_op(a,a->modem,false,1) || !stream_op(a,a->modem,true,0) || !stream_op(a,a->headset,true,0))return false;
    pa_stream_set_write_callback(a->modem,write_pcm,a);
    return fd_voice_transition(a->engine,direction,++a->generation);
}
static void drain_done(pa_stream *s,int success,void *p){(void)s;((fd_usb_audio *)p)->drain_ack=success?1:-1;}
static bool prepare(fd_usb_audio *a){
    a->engine=fd_voice_create(RATE);if(!a->engine || atomic_load(&a->stop))return false;
    int error=0;a->radio_input=src_new(SRC_SINC_FASTEST,1,&error);a->radio_output=src_new(SRC_SINC_FASTEST,1,&error);if(!a->radio_input || !a->radio_output)return false;
    a->loop=pa_mainloop_new();a->context=a->loop?pa_context_new(pa_mainloop_get_api(a->loop),"X6200 USB FreeDV"):NULL;if(!a->context)return false;
    pa_context_set_state_callback(a->context,context_state,a);if(pa_context_connect(a->context,NULL,PA_CONTEXT_NOAUTOSPAWN,NULL)<0)return false;
    uint64_t until=now_ms()+5000;while(pa_context_get_state(a->context)!=PA_CONTEXT_READY && !atomic_load(&a->stop) && now_ms()<until)pump(a);if(pa_context_get_state(a->context)!=PA_CONTEXT_READY)return false;
    if(a->c.source && a->c.sink){snprintf(a->source,sizeof(a->source),"%s",a->c.source);snprintf(a->sink,sizeof(a->sink),"%s",a->c.sink);}else{
        if(!operation(a,pa_context_get_source_info_list(a->context,source_info,a)) || !operation(a,pa_context_get_sink_info_list(a->context,sink_info,a)) || a->sources!=1 || a->sinks!=1 || strcmp(a->source_card,a->sink_card)){failed(a,U_IDENTITY);return false;}
    }
    pa_sample_spec spec={PA_SAMPLE_S16LE,RATE,1};pa_buffer_attr attr={.maxlength=19200,.tlength=9600,.prebuf=0,.minreq=1920,.fragsize=1920};
    a->mic=pa_stream_new(a->context,"USB microphone",&spec,NULL);a->headset=pa_stream_new(a->context,"USB decoded speech",&spec,NULL);
    spec.rate=RADIO_RATE;a->rx=pa_stream_new(a->context,"Radio demodulated audio",&spec,NULL);a->modem=pa_stream_new(a->context,"Radio modem audio",&spec,NULL);
    if(!a->mic || !a->rx || !a->headset || !a->modem)return false;
    pa_stream *streams[]={a->mic,a->rx,a->headset,a->modem};
    for(unsigned i=0;i<4;++i){pa_stream_set_state_callback(streams[i],stream_state,a);pa_stream_set_moved_callback(streams[i],stream_state,a);}
    pa_stream_set_read_callback(a->mic,read_pcm,a);pa_stream_set_read_callback(a->rx,read_pcm,a);pa_stream_set_write_callback(a->headset,write_pcm,a);pa_stream_set_write_callback(a->modem,write_pcm,a);pa_stream_set_underflow_callback(a->modem,underflow,a);pa_stream_set_underflow_callback(a->headset,underflow,a);
    pa_stream_flags_t flags=PA_STREAM_DONT_MOVE|PA_STREAM_ADJUST_LATENCY|PA_STREAM_AUTO_TIMING_UPDATE;pa_cvolume volume;pa_cvolume_set(&volume,1,PA_VOLUME_NORM/2);
    if(pa_stream_connect_record(a->mic,a->source,&attr,flags)<0 || pa_stream_connect_playback(a->headset,a->sink,&attr,flags,&volume,NULL)<0)return false;
    attr=(pa_buffer_attr){.maxlength=17640,.tlength=8820,.prebuf=(uint32_t)-1,.minreq=1764,.fragsize=1764};
    if(pa_stream_connect_record(a->rx,a->c.radio_source,&attr,flags)<0)return false;
    pa_cvolume_set(&volume,1,PA_VOLUME_NORM);
    if(pa_stream_connect_playback(a->modem,a->c.radio_sink,&attr,flags|PA_STREAM_START_CORKED,&volume,NULL)<0)return false;
    until=now_ms()+5000;bool ready=false;while(!ready && !atomic_load(&a->stop) && now_ms()<until){ready=true;for(unsigned i=0;i<4;++i)if(pa_stream_get_state(streams[i])!=PA_STREAM_READY)ready=false;pump(a);}if(!ready)return false;
    if(!fence(a,FD_VOICE_RX))return false;
    a->mic_last=a->rx_last=now_ms();return true;
}
static void publish(fd_usb_audio *a,fd_usb_inputs in){
    fd_usb_snapshot s={.state=atomic_load(&a->state),.fault=atomic_load(&a->fault),.generation=a->generation,.turns=a->turns,.heartbeat_ms=atomic_load(&a->heartbeat),.input_queue=a->capture.n+(a->radio_capture.n*RATE+RADIO_RATE-1)/RADIO_RATE,.output_queue=a->play.n,.holes=a->holes,.underflows=a->underflows,.gaps=a->gaps,.armed=atomic_load(&a->armed),.pressed=in.pressed,.rf_valid=in.rf_valid,.rf_tx=in.rf_tx,.stopped=atomic_load(&a->stopped),.engine=fd_voice_observe(a->engine)};
    pthread_mutex_lock(&a->mutex);a->snapshot=s;pthread_mutex_unlock(&a->mutex);
    uint64_t now=now_ms();if(a->trace && now-a->last_trace>=100 && a->trace_rows<6000){a->last_trace=now;++a->trace_rows;fprintf(a->trace,"{\"ms\":%llu,\"state\":%u,\"fault\":%u,\"generation\":%llu,\"turns\":%llu,\"rf_valid\":%s,\"rf_tx\":%s,\"ptt\":%s,\"capture\":%u,\"playback\":%u,\"engine_in\":%u,\"engine_out\":%u,\"eoo\":%llu,\"sync\":%s,\"gaps\":%u}\n",(unsigned long long)now,s.state,s.fault,(unsigned long long)s.generation,(unsigned long long)s.turns,in.rf_valid?"true":"false",in.rf_tx?"true":"false",in.pressed?"true":"false",s.input_queue,s.output_queue,s.engine.input_depth,s.engine.output_depth,(unsigned long long)s.engine.eoo_count,s.engine.synchronized?"true":"false",s.gaps);fflush(a->trace);}
}
static void *run(void *p){
    fd_usb_audio *a=p;fd_usb_inputs in={0};
    if(a->c.trace_prefix){char path[256];snprintf(path,sizeof(path),"%s.jsonl",a->c.trace_prefix);a->trace=fopen(path,"wx");static const char *names[]={"mic","radio-rx","modem","decoded"};for(unsigned i=0;i<4;++i){snprintf(path,sizeof(path),"%s-%s.raw",a->c.trace_prefix,names[i]);a->taps[i]=fopen(path,"wbx");}}
    if(!prepare(a)){if(!atomic_load(&a->fault))failed(a,U_PREPARE);goto done;}
    a->permit_press=false;state(a,FD_USB_RX);
    while(!atomic_load(&a->stop)){
        pump(a);if(!a->c.control.observe(a->c.control.context,&in) || !in.valid){failed(a,U_PTT);break;}if(!in.rf_valid){failed(a,U_STATUS);break;}if(in.protection){failed(a,U_PROTECTION);break;}
        unsigned st=atomic_load(&a->state);uint64_t now=now_ms();
        if((now-a->mic_last>500) || (now-a->rx_last>500)){failed(a,U_DEVICE);break;}
        if(st==FD_USB_RX){
            if(in.rf_tx){failed(a,U_STATUS);break;}if(!in.pressed)a->permit_press=true;
            if(in.pressed && a->permit_press && atomic_load(&a->armed)){
                a->permit_press=false;a->release=in.release;state(a,FD_USB_START);atomic_store(&a->tx_at,now);
                if(!fence(a,FD_VOICE_TX) || atomic_load(&a->stop) || !a->c.control.observe(a->c.control.context,&in) || !in.pressed || in.release!=a->release){a->c.control.clear(a->c.control.context,false);if(!atomic_load(&a->stop)){if(!fence(a,FD_VOICE_RX))failed(a,U_ENGINE);state(a,FD_USB_RX);}continue;}
                if(!a->c.control.key(a->c.control.context,a->release)){failed(a,U_KEY);break;}
            }
        }else if(st==FD_USB_START && atomic_load(&a->tx_at)){
            if(!in.pressed || in.release!=a->release){a->c.control.clear(a->c.control.context,false);a->rx_sequence=in.sequence;if(!fence(a,FD_VOICE_RX))failed(a,U_ENGINE);state(a,FD_USB_START);atomic_store(&a->tx_at,0);/* await RX below */}
            else if(in.rf_tx){if(!stream_op(a,a->modem,false,0)){failed(a,U_DEVICE);break;}state(a,FD_USB_TX);}
            else if(now_ms()-a->phase_at>750){failed(a,U_KEY);break;}
            if(!atomic_load(&a->tx_at) && !in.rf_tx && in.sequence>a->rx_sequence){state(a,FD_USB_RX);a->permit_press=false;}
        }else if(st==FD_USB_TX){
            if(!in.rf_tx){failed(a,U_STATUS);break;}
            if(!in.pressed || in.release!=a->release){state(a,FD_USB_DRAIN);a->finishing=false;}
        }
        st=atomic_load(&a->state);
        if(st==FD_USB_RX || st==FD_USB_TX || st==FD_USB_DRAIN){
            int16_t pcm[4096],converted[4608];unsigned n=0;
            if(st==FD_USB_RX){n=pop(&a->radio_capture,pcm,441);if(n){unsigned count=convert(a,a->radio_input,pcm,n,converted,(double)RATE/RADIO_RATE,false);push(a,&a->capture,converted,count);}}
            n=pop(&a->capture,pcm,480);if(n){tap(a,st==FD_USB_RX?1:0,pcm,n);if(fd_voice_submit(a->engine,a->generation,pcm,n)!=n){failed(a,U_QUEUE);break;}}
            if(st==FD_USB_DRAIN && !a->capture.n && !a->finishing){if(!fd_voice_finish(a->engine,a->generation)){failed(a,U_ENGINE);break;}a->finishing=true;}
            unsigned limit=st==FD_USB_RX?QMAX:RADIO_RATE/2;if(a->play.n>limit){failed(a,U_QUEUE);break;}
            unsigned room=limit-a->play.n;if(room>4096)room=4096;if(room){n=fd_voice_receive(a->engine,a->generation,pcm,room);if(n){tap(a,st==FD_USB_RX?3:2,pcm,n);if(st==FD_USB_RX)push(a,&a->play,pcm,n);else{unsigned count=convert(a,a->radio_output,pcm,n,converted,(double)RADIO_RATE/RATE,false);push(a,&a->play,converted,count);}}}
            if(st!=FD_USB_RX && !a->drain && pa_stream_writable_size(a->modem)>0)write_pcm(a->modem,pa_stream_writable_size(a->modem),a);
        }
        fd_voice_stats stats=fd_voice_observe(a->engine);if(stats.failures || stats.cancelled){failed(a,U_ENGINE);break;}
        if(stats.finished && !stats.output_depth && !a->radio_output_ended && a->play.n<RADIO_RATE/2-128){int16_t tail[4608];unsigned n=convert(a,a->radio_output,NULL,0,tail,(double)RADIO_RATE/RATE,true);push(a,&a->play,tail,n);a->radio_output_ended=true;}
        a->producer_finished=stats.finished && !stats.output_depth && a->radio_output_ended;
        if(stats.input_depth+a->capture.n+(a->radio_capture.n*RATE+RADIO_RATE-1)/RADIO_RATE>=QMAX || stats.output_depth+a->play.n>QMAX*2){failed(a,U_QUEUE);break;}
        if(st==FD_USB_DRAIN){
            if(now_ms()-a->phase_at>3000){failed(a,U_DRAIN);break;}
            if(a->producer_finished && !a->play.n && !a->drain){pa_stream_set_write_callback(a->modem,NULL,NULL);a->drain_ack=0;a->drain=pa_stream_drain(a->modem,drain_done,a);if(!a->drain){failed(a,U_DRAIN);break;}}
            if(a->drain_ack<0){failed(a,U_DRAIN);break;}
            if(a->drain_ack>0){a->c.control.clear(a->c.control.context,false);a->rx_sequence=in.sequence;atomic_store(&a->tx_at,0);++a->turns;if(!fence(a,FD_VOICE_RX)){failed(a,U_ENGINE);break;}state(a,FD_USB_START);a->permit_press=false;}
        }
        /* START with tx_at==0 is the post-clear RX acknowledgement fence. */
        if(atomic_load(&a->state)==FD_USB_START && !atomic_load(&a->tx_at)){
            if(!in.rf_tx && in.sequence>a->rx_sequence){state(a,FD_USB_RX);a->permit_press=false;}
            else if(now_ms()-a->phase_at>1500){failed(a,U_STATUS);break;}
        }
        publish(a,in);
    }
done:
    a->c.control.clear(a->c.control.context,true);if(a->engine)fd_voice_cancel(a->engine);
    /* Capture a fresh post-clear RF observation for the trace. This is packet
     * telemetry, not a physical RF-off measurement or an MCU lease ACK. */
    uint64_t clear_until=now_ms()+1500;
    do{if(a->c.control.observe(a->c.control.context,&in) && in.rf_valid && !in.rf_tx)break;struct timespec t={0,5000000};nanosleep(&t,NULL);}while(now_ms()<clear_until);
    if(a->drain){pa_operation_cancel(a->drain);pa_operation_unref(a->drain);a->drain=NULL;}
    pa_stream *streams[]={a->mic,a->rx,a->headset,a->modem};for(unsigned i=0;i<4;++i)if(streams[i]){pa_stream_set_state_callback(streams[i],NULL,NULL);pa_stream_disconnect(streams[i]);pa_stream_unref(streams[i]);}
    if(a->context){pa_context_set_state_callback(a->context,NULL,NULL);pa_context_disconnect(a->context);pa_context_unref(a->context);}if(a->loop)pa_mainloop_free(a->loop);
    state(a,atomic_load(&a->fault)>U_STOP?FD_USB_FAULT:FD_USB_DONE);atomic_store(&a->stopped,true);publish(a,in);fd_voice_destroy(a->engine);a->engine=NULL;
    if(a->radio_input)src_delete(a->radio_input);
    if(a->radio_output)src_delete(a->radio_output);
    if(a->trace)fclose(a->trace);
    for(unsigned i=0;i<4;++i)if(a->taps[i])fclose(a->taps[i]);
    return NULL;
}
static void *watch(void *p){fd_usb_audio *a=p;while(!atomic_load(&a->stopped)){uint64_t tx=atomic_load(&a->tx_at),beat=atomic_load(&a->heartbeat),now=now_ms();if(!atomic_load(&a->stop)){if(tx && now>=tx && now-tx>20000)failed(a,U_TIMEOUT);if(tx && now>=beat && now-beat>500)failed(a,U_STALL);}struct timespec t={0,20000000};nanosleep(&t,NULL);}return NULL;}
fd_usb_audio *fd_usb_audio_open(const fd_usb_config *c){if(!c || !c->control.observe || !c->control.key || !c->control.clear)return NULL;fd_usb_audio *a=calloc(1,sizeof(*a));if(!a)return NULL;a->c=*c;pthread_mutex_init(&a->mutex,NULL);atomic_store(&a->heartbeat,now_ms());if(pthread_create(&a->worker,NULL,run,a)){pthread_mutex_destroy(&a->mutex);free(a);return NULL;}if(pthread_create(&a->watch,NULL,watch,a)){fd_usb_audio_stop(a);pthread_join(a->worker,NULL);pthread_mutex_destroy(&a->mutex);free(a);return NULL;}return a;}
void fd_usb_audio_arm(fd_usb_audio *a,bool armed){if(a && !atomic_load(&a->stop))atomic_store(&a->armed,armed);}
void fd_usb_audio_stop(fd_usb_audio *a){if(a)failed(a,U_STOP);}
fd_usb_snapshot fd_usb_audio_snapshot(fd_usb_audio *a){if(!a)return(fd_usb_snapshot){.state=FD_USB_DONE,.stopped=true};pthread_mutex_lock(&a->mutex);fd_usb_snapshot s=a->snapshot;pthread_mutex_unlock(&a->mutex);s.stopped=atomic_load(&a->stopped);s.state=atomic_load(&a->state);s.fault=atomic_load(&a->fault);return s;}
void fd_usb_audio_close(fd_usb_audio *a){if(!a || !atomic_load(&a->stopped))return;pthread_join(a->worker,NULL);pthread_join(a->watch,NULL);pthread_mutex_destroy(&a->mutex);free(a);}
