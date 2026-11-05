/* SPDX-License-Identifier: BSD-2-Clause
 * Bounded USB-only PCM probe. No radio/control dependencies or default devices.
 * argv: source|- sink capture.raw|- playback.raw report.json capture_seconds
 * File format: signed 16-bit little-endian, 44100 Hz, mono (server resamples).
 */
#define _POSIX_C_SOURCE 200809L
#include <pulse/pulseaudio.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

static pa_mainloop *loop;
static pa_context *context;
static pa_stream *input, *output;
static FILE *capture, *playback;
static const char *source, *sink, *fault="";
static size_t captured, written, target, play_bytes;
static unsigned holes, overflows, underflows, pending;
static int drained, active;
static uint64_t start, first_capture, last_capture, max_gap;
static uint32_t capture_fragment, playback_target;
static uint64_t ms(void) { struct timespec t; clock_gettime(CLOCK_MONOTONIC,&t); return (uint64_t)t.tv_sec*1000+t.tv_nsec/1000000; }
static void fail(const char *why) { if(!*fault)fault=why; }
static int name_ok(const char *name,const char *prefix) {
    return !strncmp(name,prefix,strlen(prefix)) && strlen(name)<512 &&
        strspn(name,"abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-")==strlen(name);
}
static void context_state(pa_context *c,void *unused) {
    (void)unused; if(!PA_CONTEXT_IS_GOOD(pa_context_get_state(c)))fail("Pulse context failed");
}
static void binding(pa_stream *s,void *unused) {
    (void)unused; const char *actual=pa_stream_get_device_name(s);
    if(!actual || strcmp(actual,s==input?source:sink))fail("Endpoint binding changed");
}
static void stream_state(pa_stream *s,void *unused) {
    (void)unused; pa_stream_state_t state=pa_stream_get_state(s);
    if(state==PA_STREAM_READY)binding(s,NULL);
    else if(!PA_STREAM_IS_GOOD(state))fail("USB stream failed or disappeared");
}
static void overflow(pa_stream *s,void *unused) { (void)s;(void)unused; ++overflows; fail("Capture overflow"); }
static void underflow(pa_stream *s,void *unused) { (void)s;(void)unused; if(active && written<play_bytes){++underflows;fail("Playback underrun before end of file");} }
static void read_pcm(pa_stream *s,size_t requested,void *unused) {
    (void)requested;(void)unused; const void *data; size_t bytes;
    if(pa_stream_peek(s,&data,&bytes)<0){fail("Capture peek failed");return;}
    if(!bytes)return;
    if(bytes%2)fail("Partial capture sample");
    if(!data){++holes;fail("Capture hole");}
    if(captured<target && data && !*fault){
        uint64_t now=ms(); if(!first_capture)first_capture=now;
        if(last_capture && now-last_capture>max_gap)max_gap=now-last_capture;
        last_capture=now;
        size_t n=bytes<target-captured?bytes:target-captured;
        if(fwrite(data,1,n,capture)!=n)fail("Capture file write failed"); else captured+=n;
    }
    if(pa_stream_drop(s)<0)fail("Capture drop failed");
}
static void write_pcm(pa_stream *s,size_t requested,void *unused) {
    (void)unused; uint8_t buffer[8192];
    while(requested && written<play_bytes && !*fault){
        size_t n=play_bytes-written; if(n>requested)n=requested; if(n>sizeof(buffer))n=sizeof(buffer);
        n&=~(size_t)1; if(!n)break;
        if(fread(buffer,1,n,playback)!=n){fail("Playback file read failed");break;}
        if(pa_stream_write(s,buffer,n,NULL,0,PA_SEEK_RELATIVE)<0){fail("Playback write failed");break;}
        written+=n; requested-=n;
    }
}
static void operation_done(pa_stream *s,int success,void *unused) { (void)s;(void)unused; if(!success)fail("Uncork rejected"); --pending; }
static void drain_done(pa_stream *s,int success,void *unused) { (void)s;(void)unused; if(!success)fail("Drain failed"); else drained=1; }
static int pump(uint64_t deadline) {
    int result=0; if(ms()>deadline){fail("Audio operation deadline exceeded");return 0;}
    if(pa_mainloop_iterate(loop,0,&result)<0)fail("Pulse mainloop failed");
    struct timespec pause={0,1000000}; nanosleep(&pause,NULL); return !*fault;
}
int main(int argc,char **argv) {
    if(argc!=7)return 2;
    source=argv[1];sink=argv[2]; char *end=NULL; unsigned long seconds=strtoul(argv[6],&end,10);
    if(!*argv[6] || *end || seconds>30 || !name_ok(sink,"alsa_output.usb-") ||
       (seconds?(!name_ok(source,"alsa_input.usb-") || !strcmp(argv[3],"-")):
                (strcmp(source,"-") || strcmp(argv[3],"-"))))return 2;
    playback=fopen(argv[4],"rb"); if(!playback)return 2;
    if(fseek(playback,0,SEEK_END) || ftell(playback)<=0 || ftell(playback)>44100*2*30 || ftell(playback)%2){fclose(playback);return 2;}
    play_bytes=(size_t)ftell(playback);rewind(playback);target=seconds*44100*2;
    if(seconds){capture=fopen(argv[3],"wbx");if(!capture){fclose(playback);return 2;}}
    FILE *report=fopen(argv[5],"wx");if(!report){if(capture)fclose(capture);fclose(playback);return 2;}
    loop=pa_mainloop_new();context=loop?pa_context_new(pa_mainloop_get_api(loop),"X6200 USB-only validation"):NULL;
    if(!context){fail("Pulse allocation failed");goto finish;}
    pa_context_set_state_callback(context,context_state,NULL);
    if(pa_context_connect(context,NULL,PA_CONTEXT_NOAUTOSPAWN,NULL)<0){fail("Pulse connect failed");goto finish;}
    uint64_t deadline=ms()+5000;
    while(pa_context_get_state(context)!=PA_CONTEXT_READY && pump(deadline)){}
    if(*fault)goto finish;
    pa_sample_spec spec={PA_SAMPLE_S16LE,44100,1};
    pa_buffer_attr attr={.maxlength=17640,.tlength=8820,.prebuf=0,.minreq=1764,.fragsize=1764};
    output=pa_stream_new(context,"USB validation playback",&spec,NULL);
    if(!output){fail("Playback allocation failed");goto finish;}
    pa_stream_set_state_callback(output,stream_state,NULL);pa_stream_set_moved_callback(output,binding,NULL);
    pa_stream_set_underflow_callback(output,underflow,NULL);pa_stream_set_write_callback(output,write_pcm,NULL);
    pa_cvolume volume;pa_cvolume_set(&volume,1,PA_VOLUME_NORM/2);
    pa_stream_flags_t flags=PA_STREAM_START_CORKED|PA_STREAM_ADJUST_LATENCY|PA_STREAM_DONT_MOVE;
    if(pa_stream_connect_playback(output,sink,&attr,flags,&volume,NULL)<0){fail("Named USB sink unavailable");goto finish;}
    if(seconds){
        input=pa_stream_new(context,"USB validation capture",&spec,NULL);
        if(!input){fail("Capture allocation failed");goto finish;}
        pa_stream_set_state_callback(input,stream_state,NULL);pa_stream_set_moved_callback(input,binding,NULL);
        pa_stream_set_read_callback(input,read_pcm,NULL);pa_stream_set_overflow_callback(input,overflow,NULL);
        if(pa_stream_connect_record(input,source,&attr,flags)<0){fail("Named USB source unavailable");goto finish;}
    }
    deadline=ms()+5000;
    while((pa_stream_get_state(output)!=PA_STREAM_READY || (input && pa_stream_get_state(input)!=PA_STREAM_READY)) && pump(deadline)){}
    if(*fault)goto finish;
    const pa_buffer_attr *actual=pa_stream_get_buffer_attr(output);if(actual)playback_target=actual->tlength;
    if(input){actual=pa_stream_get_buffer_attr(input);if(actual)capture_fragment=actual->fragsize;}
    write_pcm(output,pa_stream_writable_size(output),NULL);
    if(*fault)goto finish;
    start=ms();active=1;pending=input?2:1;
    pa_operation *op=pa_stream_cork(output,0,operation_done,NULL);if(op)pa_operation_unref(op);else fail("Playback uncork failed");
    if(input){op=pa_stream_cork(input,0,operation_done,NULL);if(op)pa_operation_unref(op);else fail("Capture uncork failed");}
    deadline=start+5000+1000*(seconds+(play_bytes+88199)/88200);
    while((pending || captured<target || written<play_bytes) && pump(deadline)){}
    if(!*fault){op=pa_stream_drain(output,drain_done,NULL);if(op)pa_operation_unref(op);else fail("Drain request failed");}
    while(!drained && pump(deadline)){}
finish:
    active=0;
    if(input){pa_stream_set_state_callback(input,NULL,NULL);pa_stream_disconnect(input);pa_stream_unref(input);}
    if(output){pa_stream_set_state_callback(output,NULL,NULL);pa_stream_disconnect(output);pa_stream_unref(output);}
    if(context){pa_context_set_state_callback(context,NULL,NULL);pa_context_disconnect(context);pa_context_unref(context);}
    if(loop)pa_mainloop_free(loop);
    if(capture && fclose(capture))fail("Capture file close failed");
    if(fclose(playback))fail("Playback file close failed");
    fprintf(report,"{\"schema\":\"x6200.usb-pcm.v1\",\"ok\":%s,\"fault\":\"%s\",\"source\":\"%s\",\"sink\":\"%s\","
        "\"client_rate\":44100,\"client_channels\":1,\"dont_move\":true,\"captured_bytes\":%zu,\"playback_bytes\":%zu,"
        "\"drained\":%s,\"holes\":%u,\"overflows\":%u,\"underflows\":%u,\"capture_fragment_bytes\":%u,\"playback_target_bytes\":%u,"
        "\"elapsed_ms\":%llu,\"first_capture_ms\":%llu,\"max_capture_gap_ms\":%llu,\"rf_tested\":false}\n",
        *fault?"false":"true",fault,source,sink,captured,written,drained?"true":"false",holes,overflows,underflows,
        capture_fragment,playback_target,(unsigned long long)(start?ms()-start:0),
        (unsigned long long)(first_capture?first_capture-start:0),(unsigned long long)max_gap);
    if(fclose(report))return 1;
    if(*fault){fprintf(stderr,"%s\n",fault);return 1;}
    return 0;
}
