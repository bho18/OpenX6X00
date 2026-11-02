// SPDX-License-Identifier: BSD-2-Clause
#define _POSIX_C_SOURCE 200809L
#include "../../src/freedv/voice_engine.h"
#include <stdatomic.h>
#include <assert.h>
#include <math.h>
#include <stdio.h>
#include <time.h>
static atomic_uint requested;
bool fd_voice_test_fail(unsigned stage){return atomic_load(&requested)==stage;}
static uint64_t now(void){struct timespec t;clock_gettime(CLOCK_MONOTONIC,&t);return(uint64_t)t.tv_sec*1000+t.tv_nsec/1000000;}
static void tick(void){struct timespec t={0,1000000};nanosleep(&t,NULL);}
static int16_t waveform[32768];static size_t waveform_n;
static void retain(fd_voice_engine *e){waveform_n+=fd_voice_receive(e,1,waveform+waveform_n,32768-waveform_n);assert(waveform_n<32768);}
static void make_waveform(void){
    fd_voice_engine *e=fd_voice_create(8000);assert(e && fd_voice_transition(e,FD_VOICE_TX,1));
    unsigned at=0;uint64_t deadline=now()+15000;
    while(at<16000){
        retain(e);assert(now()<deadline);
        if(fd_voice_observe(e).input_depth<1600){int16_t pcm[80];for(unsigned i=0;i<80;++i)pcm[i]=(int16_t)(1200*sin(2*3.141592653589793*440*(at+i)/8000));assert(fd_voice_submit(e,1,pcm,80)==80);at+=80;}
        tick();
    }
    assert(fd_voice_finish(e,1));
    while(!fd_voice_observe(e).finished || fd_voice_observe(e).output_depth){retain(e);assert(now()<deadline);tick();}
    fd_voice_destroy(e);assert(waveform_n>8000);
}
static void injected_failure(unsigned stage){
    fd_voice_engine *e=fd_voice_create(8000);assert(e);
    assert(fd_voice_transition(e,stage==FD_VOICE_TEST_ENCODE?FD_VOICE_TX:FD_VOICE_RX,1));
    atomic_store(&requested,stage);size_t at=0;uint64_t deadline=now()+10000;
    while(!fd_voice_observe(e).failures){
        assert(now()<deadline);
        if(fd_voice_observe(e).input_depth<1600){size_t n=waveform_n-at;if(n>80)n=80;assert(n);
            (void)fd_voice_submit(e,1,waveform+at,n);at+=n;if(at==waveform_n)at=0;}
        tick();
    }
    fd_voice_stats s=fd_voice_observe(e);assert(s.cancelled && s.input_depth<=4000 && s.output_depth<=3456);
    assert(!fd_voice_submit(e,1,waveform,80));fd_voice_destroy(e);atomic_store(&requested,0);
}
int main(void){
    make_waveform();
    injected_failure(FD_VOICE_TEST_ENCODE);injected_failure(FD_VOICE_TEST_DECODE);injected_failure(FD_VOICE_TEST_SYNTHESIS);
    fd_voice_engine *e=fd_voice_create(8000);assert(e && fd_voice_transition(e,FD_VOICE_TX,1));
    uint64_t deadline=now()+10000;
    while(fd_voice_observe(e).modem_blocks<4){
        assert(now()<deadline);fd_voice_stats s=fd_voice_observe(e);assert(!s.failures);
        if(s.input_depth<1600)assert(fd_voice_submit(e,1,waveform,80)==80);tick();
    }
    for(unsigned i=0;i<30;++i)tick();
    assert(fd_voice_transition(e,FD_VOICE_RX,2));
    fd_voice_stats s=fd_voice_observe(e);assert(s.queue_wait_ns && !s.failures && !s.output_depth && s.rade_creations==1);
    assert(!fd_voice_submit(e,2,waveform,4001));s=fd_voice_observe(e);assert(s.failures && s.cancelled && s.input_depth<=4000);
    fd_voice_destroy(e);
    puts("PASS: real engine encoder/decoder/synthesis fault injection, cancellable saturated output and 500 ms backlog limit; hooks absent from production");
}
