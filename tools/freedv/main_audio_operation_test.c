// SPDX-License-Identifier: LGPL-2.1-or-later
#define _POSIX_C_SOURCE 200809L
#define ENABLE_FREEDV_DIAGNOSTICS 1
#include <pulse/pulseaudio.h>
#include <assert.h>
#include <math.h>
#include <signal.h>
#include <pthread.h>
#include <stdatomic.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>
/* Faults still execute the actual server operation. Only its acknowledgement
 * is lost/rejected, exactly the contract handled by the production helper. */
static atomic_int flush_fault;
static void rejected(pa_stream *s,int success,void *context){
    (void)success;atomic_store_explicit((atomic_int *)context,-1,memory_order_release);(void)s;
}
static pa_operation *fixture_flush(pa_stream *s,pa_stream_success_cb_t callback,void *context){
    int fault=atomic_exchange(&flush_fault,0);
    return pa_stream_flush(s,fault==1?NULL:fault==2?rejected:callback,fault==1?NULL:context);
}
#define pa_stream_flush fixture_flush
#include "src/freedv/main_audio_operation.h"
#undef pa_stream_flush
#include "src/freedv/main_audio_recovery.h"

static pa_threaded_mainloop *loop;
static pa_context *context;
static pa_stream *play,*monitor;
static uint64_t measured_samples,measured_energy;
static atomic_bool blocked_done;
static audio_main_quiet_result_t blocked_result;
static void *blocked_operation(void *unused){
    (void)unused;blocked_result=fd_main_audio_operation(loop,play,true);
    atomic_store_explicit(&blocked_done,true,memory_order_release);return NULL;
}
static void pause_ms(unsigned ms){struct timespec t={ms/1000,(long)(ms%1000)*1000000};nanosleep(&t,NULL);}
static void state_context(pa_context *c,void *unused){(void)c;(void)unused;pa_threaded_mainloop_signal(loop,0);}
static void state_stream(pa_stream *s,void *unused){(void)s;(void)unused;pa_threaded_mainloop_signal(loop,0);}
static void write_audio(pa_stream *s,size_t bytes,void *unused){
    (void)unused;int16_t pcm[1024];for(unsigned i=0;i<1024;++i)pcm[i]=i&1?4096:-4096;
    while(bytes){size_t n=bytes>sizeof(pcm)?sizeof(pcm):bytes;assert(!pa_stream_write(s,pcm,n,NULL,0,PA_SEEK_RELATIVE));bytes-=n;}
}
static void read_audio(pa_stream *s,size_t bytes,void *unused){
    (void)bytes;(void)unused;const void *data;size_t n;
    while(pa_stream_readable_size(s)>0){assert(!pa_stream_peek(s,&data,&n));if(!n)break;
        measured_samples+=n/2;
        if(data){const int16_t *pcm=data;for(size_t i=0;i<n/2;++i){int64_t value=pcm[i];measured_energy+=(uint64_t)(value*value);}}
        assert(!pa_stream_drop(s));
    }
}
static void ready(void){
    for(unsigned i=0;i<400;++i){pa_threaded_mainloop_lock(loop);bool c=pa_context_get_state(context)==PA_CONTEXT_READY;
        bool p=play&&pa_stream_get_state(play)==PA_STREAM_READY,m=monitor&&pa_stream_get_state(monitor)==PA_STREAM_READY;
        pa_threaded_mainloop_unlock(loop);if(c&&p&&m)return;pause_ms(5);}
    assert(!"Private PulseAudio fixture did not become ready");
}
static void connect_fixture(void){
    loop=pa_threaded_mainloop_new();assert(loop);assert(!pa_threaded_mainloop_start(loop));pa_threaded_mainloop_lock(loop);
    context=pa_context_new(pa_threaded_mainloop_get_api(loop),"main-audio-recovery-fixture");assert(context);
    pa_context_set_state_callback(context,state_context,NULL);assert(!pa_context_connect(context,NULL,PA_CONTEXT_NOAUTOSPAWN,NULL));pa_threaded_mainloop_unlock(loop);
    for(unsigned i=0;i<400;++i){pa_threaded_mainloop_lock(loop);bool c=pa_context_get_state(context)==PA_CONTEXT_READY;pa_threaded_mainloop_unlock(loop);if(c)break;pause_ms(5);}
    pa_threaded_mainloop_lock(loop);assert(pa_context_get_state(context)==PA_CONTEXT_READY);
    pa_sample_spec spec={PA_SAMPLE_S16LE,44100,1};pa_buffer_attr attr={.maxlength=35280,.tlength=4410,.prebuf=0,.minreq=882,.fragsize=882};
    play=pa_stream_new(context,"ordinary-main-audio",&spec,NULL);assert(play);pa_stream_set_state_callback(play,state_stream,NULL);pa_stream_set_write_callback(play,write_audio,NULL);
    assert(!pa_stream_connect_playback(play,"radio_fixture",&attr,PA_STREAM_DONT_MOVE|PA_STREAM_ADJUST_LATENCY,NULL,NULL));
    monitor=pa_stream_new(context,"ordinary-output-monitor",&spec,NULL);assert(monitor);pa_stream_set_state_callback(monitor,state_stream,NULL);pa_stream_set_read_callback(monitor,read_audio,NULL);
    assert(!pa_stream_connect_record(monitor,"radio_fixture.monitor",&attr,PA_STREAM_DONT_MOVE|PA_STREAM_ADJUST_LATENCY));pa_threaded_mainloop_unlock(loop);ready();
}
static double power_window(void){
    pause_ms(100);pa_threaded_mainloop_lock(loop);measured_samples=measured_energy=0;pa_threaded_mainloop_unlock(loop);pause_ms(500);
    pa_threaded_mainloop_lock(loop);uint64_t samples=measured_samples,energy=measured_energy;pa_threaded_mainloop_unlock(loop);assert(samples>1000);return sqrt((double)energy/samples);
}
static void identity(uint32_t index){
    pa_threaded_mainloop_lock(loop);assert(pa_stream_get_device_index(play)==index);assert(!strcmp(pa_stream_get_device_name(play),"radio_fixture"));
    const pa_sample_spec *s=pa_stream_get_sample_spec(play);assert(s->rate==44100&&s->channels==1&&s->format==PA_SAMPLE_S16LE);pa_threaded_mainloop_unlock(loop);
}
static void disconnect_fixture(void){
    pa_threaded_mainloop_lock(loop);pa_stream_set_read_callback(monitor,NULL,NULL);pa_stream_set_write_callback(play,NULL,NULL);
    pa_stream_disconnect(monitor);pa_stream_unref(monitor);pa_stream_disconnect(play);pa_stream_unref(play);pa_context_disconnect(context);pa_context_unref(context);pa_threaded_mainloop_unlock(loop);
    pa_threaded_mainloop_stop(loop);pa_threaded_mainloop_free(loop);
}
int main(int argc,char **argv){
    assert(argc>=2);connect_fixture();pa_threaded_mainloop_lock(loop);uint32_t index=pa_stream_get_device_index(play);pa_threaded_mainloop_unlock(loop);
    if(!strcmp(argv[1],"cycles")){
        assert(power_window()>1000);for(unsigned i=0;i<3;++i){assert(fd_main_audio_operation(loop,play,true)==AUDIO_MAIN_ACKNOWLEDGED);assert(power_window()<1);
            assert(fd_main_audio_operation(loop,play,false)==AUDIO_MAIN_ACKNOWLEDGED);assert(power_window()>1000);identity(index);}
    }else if(!strcmp(argv[1],"partial-timeout")||!strcmp(argv[1],"partial-reject")){
        assert(power_window()>1000);atomic_store(&flush_fault,!strcmp(argv[1],"partial-timeout")?1:2);uint64_t start=fd_main_audio_ns();
        audio_main_quiet_result_t result=fd_main_audio_operation(loop,play,true);uint64_t elapsed=(fd_main_audio_ns()-start)/1000000;
        assert(result==(!strcmp(argv[1],"partial-timeout")?AUDIO_MAIN_TIMED_OUT:AUDIO_MAIN_FAILED));
        if(result==AUDIO_MAIN_TIMED_OUT)assert(elapsed>=490&&elapsed<800);
        assert(power_window()<1);
        assert(fd_main_audio_operation(loop,play,false)==AUDIO_MAIN_ACKNOWLEDGED);assert(power_window()>1000);identity(index);
    }else if(!strcmp(argv[1],"stalled-lock")){
        /* A PA lock itself is not cancellable. The GUI policy must expire
         * without joining/freeing this real blocked operation or starting a
         * second one. Once the fixture releases PA, its worker can be joined. */
        assert(power_window()>1000);pa_threaded_mainloop_lock(loop);pthread_t worker;
        fd_main_recovery recovery;uint64_t start=fd_main_audio_ns()/1000000;
        fd_main_recovery_begin(&recovery,true,start);
        assert(fd_main_recovery_step(&recovery,start,false,false,AUDIO_MAIN_FAILED)==FD_MAIN_RECOVERY_START);
        assert(!pthread_create(&worker,NULL,blocked_operation,NULL));pause_ms(2550);
        assert(!atomic_load_explicit(&blocked_done,memory_order_acquire));
        assert(fd_main_recovery_step(&recovery,fd_main_audio_ns()/1000000,true,false,AUDIO_MAIN_FAILED)==FD_MAIN_RECOVERY_STALLED);
        pa_threaded_mainloop_unlock(loop);
        for(unsigned i=0;i<200&&!atomic_load_explicit(&blocked_done,memory_order_acquire);++i)pause_ms(5);
        assert(atomic_load_explicit(&blocked_done,memory_order_acquire));assert(!pthread_join(worker,NULL));
        assert(blocked_result==AUDIO_MAIN_TIMED_OUT);
        assert(fd_main_audio_operation(loop,play,false)==AUDIO_MAIN_ACKNOWLEDGED);assert(power_window()>1000);identity(index);
    }else if(!strcmp(argv[1],"sink-loss")){
        assert(argc==3);char command[128];snprintf(command,sizeof(command),"pactl unload-module %u",(unsigned)strtoul(argv[2],NULL,10));assert(!system(command));
        for(unsigned i=0;i<200;++i){pa_threaded_mainloop_lock(loop);bool lost=pa_stream_get_state(play)!=PA_STREAM_READY;pa_threaded_mainloop_unlock(loop);if(lost)break;pause_ms(5);}
        assert(fd_main_audio_operation(loop,play,false)==AUDIO_MAIN_LOST);
    }else if(!strcmp(argv[1],"server-loss")){
        assert(argc==3);assert(!kill((pid_t)strtol(argv[2],NULL,10),SIGTERM));
        for(unsigned i=0;i<200;++i){pa_threaded_mainloop_lock(loop);bool lost=pa_stream_get_state(play)!=PA_STREAM_READY;pa_threaded_mainloop_unlock(loop);if(lost)break;pause_ms(5);}
        assert(fd_main_audio_operation(loop,play,false)==AUDIO_MAIN_LOST);
    }else assert(!"unknown scenario");
    disconnect_fixture();printf("PASS: real PulseAudio %s\n",argv[1]);return 0;
}
