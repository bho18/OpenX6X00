/* Host-test observer only. Fine capture fragments do not request a new sink
 * latency: deliberately omit PA_STREAM_ADJUST_LATENCY and EARLY_REQUESTS. */
#define _POSIX_C_SOURCE 200809L
#include <pulse/pulseaudio.h>
#include <signal.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

typedef struct {
    pa_mainloop *loop;pa_context *context;pa_stream *stream;FILE *pcm,*timing;
    const char *device;unsigned rate;uint64_t frames;bool ready;int error;
} observer;
static volatile sig_atomic_t stopping;
static uint64_t now_ns(void){struct timespec t;clock_gettime(CLOCK_MONOTONIC,&t);return(uint64_t)t.tv_sec*1000000000+t.tv_nsec;}
static void stop_signal(int signum){(void)signum;stopping=1;}
static void receive(pa_stream *stream,size_t wanted,void *data){
    (void)wanted;observer *o=data;
    for(unsigned count=0;count<128;++count){
        const void *pcm;size_t bytes;
        if(pa_stream_peek(stream,&pcm,&bytes)<0){o->error=3;return;}if(!bytes)return;
        if(!pcm||bytes%2){o->error=4;return;}
        uint64_t at=now_ns();const pa_timing_info *t=pa_stream_get_timing_info(stream);
        if(fwrite(pcm,1,bytes,o->pcm)!=bytes){o->error=5;return;}
        fprintf(o->timing,"{\"kind\":\"block\",\"frame_start\":%llu,\"frames\":%zu,\"received_ns\":%llu,\"timing_valid\":%s",
            (unsigned long long)o->frames,bytes/2,(unsigned long long)at,t&&!t->read_index_corrupt&&!t->write_index_corrupt?"true":"false");
        if(t)fprintf(o->timing,",\"read_index_bytes\":%lld,\"write_index_bytes\":%lld,\"sink_usec\":%llu,\"source_usec\":%llu,\"transport_usec\":%llu,\"configured_source_usec\":%llu,\"timestamp_sec\":%lld,\"timestamp_usec\":%lld",
            (long long)t->read_index,(long long)t->write_index,(unsigned long long)t->sink_usec,
            (unsigned long long)t->source_usec,(unsigned long long)t->transport_usec,
            (unsigned long long)t->configured_source_usec,(long long)t->timestamp.tv_sec,(long long)t->timestamp.tv_usec);
        fputs("}\n",o->timing);fflush(o->timing);o->frames+=bytes/2;
        if(pa_stream_drop(stream)<0){o->error=6;return;}
    }o->error=7;
}
static void stream_state(pa_stream *stream,void *data){
    observer *o=data;pa_stream_state_t state=pa_stream_get_state(stream);
    if(state==PA_STREAM_READY&&!o->ready){
        const pa_buffer_attr *a=pa_stream_get_buffer_attr(stream);o->ready=true;
        fprintf(o->timing,"{\"kind\":\"ready\",\"monotonic_ns\":%llu,\"rate\":%u,\"channels\":1,\"flags\":%u,\"adjust_latency\":false,\"early_requests\":false,\"fragsize\":%u,\"maxlength\":%u,\"scope\":\"passive rendered sink monitor; not physical DAC time\"}\n",
            (unsigned long long)now_ns(),o->rate,(unsigned)(PA_STREAM_DONT_MOVE|PA_STREAM_AUTO_TIMING_UPDATE),a?a->fragsize:0,a?a->maxlength:0);fflush(o->timing);
    }else if(!PA_STREAM_IS_GOOD(state)&&!stopping)o->error=8;
}
static void context_state(pa_context *context,void *data){
    observer *o=data;pa_context_state_t state=pa_context_get_state(context);
    if(state==PA_CONTEXT_READY&&!o->stream){
        pa_sample_spec spec={.format=PA_SAMPLE_S16LE,.rate=o->rate,.channels=1};
        o->stream=pa_stream_new(context,"Passive test monitor",&spec,NULL);
        if(!o->stream){o->error=9;return;}
        pa_stream_set_state_callback(o->stream,stream_state,o);pa_stream_set_read_callback(o->stream,receive,o);
        pa_buffer_attr attr={.maxlength=o->rate*2*2,.tlength=UINT32_MAX,.prebuf=UINT32_MAX,.minreq=UINT32_MAX,.fragsize=o->rate*2/50};
        if(pa_stream_connect_record(o->stream,o->device,&attr,PA_STREAM_DONT_MOVE|PA_STREAM_AUTO_TIMING_UPDATE)<0)o->error=10;
    }else if(!PA_CONTEXT_IS_GOOD(state)&&!stopping)o->error=11;
}
int main(int argc,char **argv){
    observer o={0};const char *output=NULL,*timing=NULL;
    for(int index=1;index<argc;++index){
        if(!strcmp(argv[index],"--device")&&index+1<argc)o.device=argv[++index];
        else if(!strcmp(argv[index],"--rate")&&index+1<argc)o.rate=(unsigned)strtoul(argv[++index],NULL,10);
        else if(!strcmp(argv[index],"--output")&&index+1<argc)output=argv[++index];
        else if(!strcmp(argv[index],"--timing")&&index+1<argc)timing=argv[++index];
        else return 2;
    }
    if(!o.device||!output||!timing||(o.rate!=44100&&o.rate!=48000))return 2;
    o.pcm=fopen(output,"wb");o.timing=fopen(timing,"w");if(!o.pcm||!o.timing)return 2;
    signal(SIGTERM,stop_signal);signal(SIGINT,stop_signal);
    o.loop=pa_mainloop_new();if(!o.loop)return 2;
    o.context=pa_context_new(pa_mainloop_get_api(o.loop),"Passive host-test observer");if(!o.context)return 2;
    pa_context_set_state_callback(o.context,context_state,&o);
    if(pa_context_connect(o.context,NULL,PA_CONTEXT_NOFLAGS,NULL)<0)o.error=12;
    uint64_t started=now_ns();
    while(!stopping&&!o.error){
        int result=0;if(pa_mainloop_iterate(o.loop,0,&result)<0){o.error=13;break;}
        if(!o.ready&&now_ns()-started>5000000000ull){o.error=14;break;}
        struct timespec tick={0,1000000};nanosleep(&tick,NULL);
    }
    if(o.ready&&!o.error)receive(o.stream,0,&o);
    fprintf(o.timing,"{\"kind\":\"completion\",\"frames\":%llu,\"error\":%d,\"stopped_ns\":%llu,\"adjust_latency\":false}\n",
        (unsigned long long)o.frames,o.error,(unsigned long long)now_ns());
    if(o.stream){pa_stream_disconnect(o.stream);pa_stream_unref(o.stream);}pa_context_disconnect(o.context);
    pa_context_unref(o.context);pa_mainloop_free(o.loop);fclose(o.pcm);fclose(o.timing);return o.error?1:0;
}
