// SPDX-License-Identifier: BSD-2-Clause
#define _POSIX_C_SOURCE 200809L
#undef NDEBUG
#include "../../src/freedv/usb_voice.h"
#include <assert.h>
#include <stdio.h>
#include <string.h>
#include <time.h>
typedef struct {atomic_bool pressed,keyed,halted,stall;atomic_uint keys,delay;atomic_uint_fast64_t release,sequence,pending;} fake;
static uint64_t ms(void){struct timespec t;clock_gettime(CLOCK_MONOTONIC,&t);return(uint64_t)t.tv_sec*1000+t.tv_nsec/1000000;}
static bool observe(void *p,fd_usb_inputs *o){fake *f=p;if(atomic_load(&f->stall)){struct timespec t={1,0};nanosleep(&t,NULL);}uint64_t pending=atomic_load(&f->pending);if(pending && ms()-pending>=atomic_load(&f->delay) && !atomic_load(&f->halted))atomic_store(&f->keyed,true);*o=(fd_usb_inputs){.valid=true,.pressed=atomic_load(&f->pressed),.rf_valid=true,.rf_tx=atomic_load(&f->keyed),.release=atomic_load(&f->release),.sequence=atomic_fetch_add(&f->sequence,1)+1};return true;}
static bool key(void *p,uint64_t release){fake *f=p;if(atomic_load(&f->halted) || !atomic_load(&f->pressed) || release!=atomic_load(&f->release))return false;atomic_fetch_add(&f->keys,1);if(atomic_load(&f->delay))atomic_store(&f->pending,ms());else atomic_store(&f->keyed,true);return true;}
static void clear(void *p,bool halt){fake *f=p;if(halt)atomic_store(&f->halted,true);atomic_store(&f->pending,0);atomic_store(&f->keyed,false);}
static void pause_ms(unsigned n){struct timespec t={n/1000,(n%1000)*1000000};nanosleep(&t,NULL);}
static fd_usb_snapshot wait_state(fd_usb_audio *a,fd_usb_state state,unsigned timeout){uint64_t until=ms()+timeout;fd_usb_snapshot s;do{s=fd_usb_audio_snapshot(a);if(s.state==state)return s;if(s.fault){fprintf(stderr,"fault %u: %s\n",s.fault,fd_usb_fault_text(s.fault));assert(!s.fault);}pause_ms(5);}while(ms()<until);fprintf(stderr,"wanted %u got %u\n",state,s.state);assert(0);return s;}
int main(int argc,char **argv){
    assert(argc==2 || argc==3);fake f={0};char prefix[512];snprintf(prefix,sizeof(prefix),"%s/session",argv[1]);
    fd_usb_config c={.source="alsa_input.usb-fixture",.sink="alsa_output.usb-fixture",.radio_source="radio_source_fixture",.radio_sink="radio_sink_fixture",.trace_prefix=prefix,.control={&f,observe,key,clear}};
    fd_usb_audio *a=fd_usb_audio_open(&c);assert(a);wait_state(a,FD_USB_RX,20000);
    uint64_t synced_until=ms()+12000;
    while(fd_usb_audio_snapshot(a).engine.synthesis_blocks<4 && ms()<synced_until){assert(!fd_usb_audio_snapshot(a).fault);pause_ms(10);}
    assert(fd_usb_audio_snapshot(a).engine.synthesis_blocks>=4);
    fd_usb_audio_arm(a,true);
    atomic_store(&f.delay,300);atomic_store(&f.pressed,true);wait_state(a,FD_USB_START,5000);pause_ms(50);
    atomic_store(&f.pressed,false);atomic_fetch_add(&f.release,1);wait_state(a,FD_USB_RX,5000);assert(!atomic_load(&f.keyed));assert(atomic_load(&f.keys)==1);atomic_store(&f.delay,0);pause_ms(100);
    /* The hardware trace spent ~400 ms in START. Keep the microphone live
     * through a longer TX acknowledgement: no pre-admission speech may queue. */
    atomic_store(&f.delay,650);atomic_store(&f.pressed,true);wait_state(a,FD_USB_START,5000);pause_ms(300);
    fd_usb_snapshot starting=fd_usb_audio_snapshot(a);
    assert(starting.state==FD_USB_START && !starting.fault);
    assert(starting.input_queue==0 && starting.engine.input_depth==0);
    wait_state(a,FD_USB_TX,5000);atomic_store(&f.delay,0);puts("TX_ACTIVE");fflush(stdout);
    if(argc==3){
        if(!strcmp(argv[2],"--stall")){atomic_store(&f.stall,true);pause_ms(700);assert(!atomic_load(&f.keyed));atomic_store(&f.stall,false);}
        uint64_t until=ms()+10000;while(!fd_usb_audio_snapshot(a).stopped && ms()<until)pause_ms(5);
        fd_usb_snapshot s=fd_usb_audio_snapshot(a);assert(s.stopped && s.fault && !atomic_load(&f.keyed) && atomic_load(&f.halted));fd_usb_audio_close(a);puts("PASS fault cancels RF independently of worker cleanup");return 0;
    }
    pause_ms(1700);
    /* A complete release/repress between polls must finish the old turn, even
     * though the current physical level is still pressed. */
    atomic_fetch_add(&f.release,1);wait_state(a,FD_USB_RX,6000);pause_ms(300);assert(atomic_load(&f.keys)==2);assert(!atomic_load(&f.keyed));
    atomic_store(&f.pressed,false);atomic_fetch_add(&f.release,1);pause_ms(100);atomic_store(&f.pressed,true);
    wait_state(a,FD_USB_TX,5000);pause_ms(1000);
    uint64_t before=ms();fd_usb_audio_stop(a);assert(!atomic_load(&f.keyed));assert(atomic_load(&f.halted));assert(ms()-before<100);
    uint64_t until=ms()+10000;while(!fd_usb_audio_snapshot(a).stopped && ms()<until)pause_ms(5);
    fd_usb_snapshot s=fd_usb_audio_snapshot(a);assert(s.stopped);assert(s.engine.rade_creations==1 && s.engine.encoder_creations==1 && s.engine.decoder_creations==1);assert(s.engine.eoo_count==1);assert(atomic_load(&f.keys)==3);fd_usb_audio_close(a);
    puts("PASS retained four-stream controller: fresh press, retained release, drain, no rekey while held, synchronous Stop clear; fake RF only");return 0;
}
