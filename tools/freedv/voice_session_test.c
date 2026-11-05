// SPDX-License-Identifier: LGPL-2.1-or-later
#define _POSIX_C_SOURCE 200809L
#include "../../src/freedv/voice_session.h"
#include <assert.h>
#include <pthread.h>
#include <stdio.h>
#include <string.h>
#include <time.h>
static uint64_t now(void){struct timespec t;clock_gettime(CLOCK_MONOTONIC,&t);return(uint64_t)t.tv_sec*1000+t.tv_nsec/1000000;}
static void pause_ms(void){struct timespec t={0,1000000};nanosleep(&t,NULL);}
static struct {
    atomic_bool held[2],stale,frozen,tx,drain,error,stall,preparing,stall_finish,maintenance_failure;
    atomic_uint clears,restores,closes,prepares,tunes,transmits,receives,maintenance_calls;
    uint64_t sequence;
} fake;
static bool prepare(void *p,const fd_qualified_profile *profile,const atomic_bool *cancel){
    (void)p;assert(profile);atomic_fetch_add(&fake.prepares,1);atomic_store(&fake.preparing,true);
    while(atomic_load(&fake.stall) && !atomic_load(cancel))pause_ms();
    atomic_store(&fake.preparing,false);return !atomic_load(cancel);
}
static bool fence(void *p){(void)p;return true;}
static bool rx(void *p,uint64_t generation){(void)p;assert(generation);atomic_fetch_add(&fake.receives,1);return true;}
static bool tx(void *p,uint64_t generation,const atomic_bool *cancel){(void)p;assert(generation);
    if(atomic_load(cancel))return false;atomic_store(&fake.tx,true);atomic_fetch_add(&fake.transmits,1);return true;}
static bool finish(void *p,uint64_t generation){(void)p;assert(generation);if(atomic_load(&fake.stall_finish)){while(atomic_load(&fake.tx))pause_ms();return false;}return true;}
static int drained(void *p){(void)p;return atomic_load(&fake.error)?-1:atomic_load(&fake.drain);}
static void clear(void *p){(void)p;atomic_store(&fake.tx,false);atomic_fetch_add(&fake.clears,1);}
static bool retune(void *p,fd_voice_tuning *tuning,const fd_qualified_profile *profile){(void)p;assert(profile && tuning->frequency);atomic_fetch_add(&fake.tunes,1);return true;}
static bool restore(void *p){(void)p;assert(!atomic_load(&fake.tx) && !atomic_load(&fake.held[0]) && !atomic_load(&fake.held[1]));atomic_fetch_add(&fake.restores,1);return true;}
static bool close_driver(void *p){(void)p;atomic_fetch_add(&fake.closes,1);return true;}
static bool maintain(void *p){(void)p;atomic_fetch_add(&fake.maintenance_calls,1);return !atomic_load(&fake.maintenance_failure);}
static bool observe(void *p,fd_voice_observation *o,bool *sync,bool *mic){
    (void)p;uint64_t time=now();if(atomic_load(&fake.stale))time-=1000;
    if(!atomic_load(&fake.frozen))++fake.sequence;
    *o=(fd_voice_observation){.valid=true,.tx=atomic_load(&fake.tx),.sequence=fake.sequence,.monotonic_ms=time};
    for(unsigned i=0;i<2;++i)o->ptt[i]=(fd_ptt_observation){true,atomic_load(&fake.held[i]),fake.sequence,time};
    *sync=true;*mic=true;return true;
}
static void wait_state(fd_voice_session *s,fd_voice_state state){
    uint64_t deadline=now()+2500;
    while(fd_session_observe(s).state!=state){if(now()>=deadline){fprintf(stderr,"expected %d, got %d (%s)\n",state,fd_session_observe(s).state,fd_voice_state_name(fd_session_observe(s)));assert(0);}pause_ms();}
}
static fd_qualified_profile profile(fd_profile_mode mode){return(fd_qualified_profile){.native_contract_id="host-native-v2",.rx_audio={1,1},.tx_audio={1,1},
    .id="test-only",.hardware="fake",.build_id="development",.baseband="fake",.control_version="fake",
    .evidence_sha256="0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
    .local_mode=mode,.upper_sideband=true,.capture_device="fake",.playback_device="fake",.mixer="fake",
    .sample_rate=44100,.rx_low_hz=100,.rx_high_hz=3100,.tx_low_hz=100,.tx_high_hz=3100,.rx2_low_hz=0,.rx2_high_hz=3300,.power_tenths_watt=10,
    .capabilities=FD_CAP_ALL,.complete_pass=true};}
int main(int argc,char **argv){
    fd_qualified_profile usb=profile(FD_PROFILE_USB),dig=profile(FD_PROFILE_USB_DIG);
    assert(!fd_profile_production());assert(fd_profile_select(&usb,&dig,"fake","development")==&dig);
    dig.capabilities&=~FD_CAP_NO_ANALOG_LEAK;assert(fd_profile_select(&usb,&dig,"fake","development")==&usb);
    usb.complete_pass=false;assert(!fd_profile_select(&usb,&dig,"fake","development"));usb.complete_pass=true;
    assert(!fd_profile_select(&usb,NULL,"fake","other-build"));
    fd_voice_driver driver={.prepare=prepare,.fence=fence,.receive=rx,.transmit=tx,.finish=finish,
        .drained=drained,.emergency_clear=clear,.retune=retune,.restore=restore,.close=close_driver,.observe=observe,.maintain=maintain};
    fd_voice_session *s=fd_session_create(&driver);assert(s);
    fd_voice_tuning tuning={14100000,1};
    assert(!fd_session_enter(s,&usb,"fake","development",tuning,29));
    if(argc>1 && !strcmp(argv[1],"--timeout")) {
        assert(fd_session_enter(s,&usb,"fake","development",tuning,30));wait_state(s,FD_VS_RX);
        atomic_store(&fake.held[0],true);atomic_store(&fake.held[1],true);wait_state(s,FD_VS_TX);
        uint64_t deadline=now()+32000;
        while(fd_session_observe(s).state!=FD_VS_FAULT){assert(now()<deadline);pause_ms();}
        assert(fd_session_observe(s).fault==FD_ABORT_TIMEOUT && !atomic_load(&fake.tx));
        assert(fd_session_observe(s).ptt_held[0] && fd_session_observe(s).ptt_held[1]);
        atomic_store(&fake.held[0],false);for(unsigned i=0;i<30;++i)pause_ms();
        assert(fd_session_observe(s).state==FD_VS_FAULT);
        atomic_store(&fake.held[1],false);wait_state(s,FD_VS_RX);
        fd_session_leave(s);wait_state(s,FD_VS_OFF);assert(fd_session_destroy(s));
        puts("PASS: actual 30-second timeout clears TX and requires both sources to release");return 0;
    }
    for(unsigned i=0;i<100;++i){assert(fd_session_enter(s,&usb,"fake","development",tuning,180));wait_state(s,FD_VS_RX);
        assert(fd_session_observe(s).tuning.frequency==14100000 && fd_session_observe(s).tuning.vfo==1);
        assert(!fd_session_destroy(s));fd_session_leave(s);wait_state(s,FD_VS_OFF);}
    assert(atomic_load(&fake.prepares)==100 && atomic_load(&fake.closes)==100);
    assert(fd_session_enter(s,&usb,"fake","development",tuning,30));wait_state(s,FD_VS_RX);
    assert(!fd_session_timeout(s,29) && !fd_session_timeout(s,601));
    assert(fd_session_timeout(s,600) && fd_session_observe(s).timeout_seconds==600);
    assert(fd_session_timeout(s,30));
    tuning.frequency=7177000;tuning.vfo=0;assert(fd_session_retune(s,tuning));
    uint64_t deadline=now()+1000;while(!atomic_load(&fake.tunes)){assert(now()<deadline);pause_ms();}wait_state(s,FD_VS_RX);
    atomic_store(&fake.held[0],true);wait_state(s,FD_VS_TX);assert(!fd_session_retune(s,tuning));
    atomic_store(&fake.held[1],true);atomic_store(&fake.held[0],false);pause_ms();assert(fd_session_observe(s).state==FD_VS_TX);
    atomic_store(&fake.held[1],false);wait_state(s,FD_VS_TX_DRAIN);
    atomic_store(&fake.held[1],true);atomic_store(&fake.drain,true);wait_state(s,FD_VS_TX);
    unsigned clears=atomic_load(&fake.clears);fd_session_abort(s,FD_ABORT_STOP);
    assert(atomic_load(&fake.clears)>clears);wait_state(s,FD_VS_FAULT);
    assert(fd_session_observe(s).fault==FD_ABORT_STOP);assert(!fd_session_retune(s,tuning));
    assert(!fd_session_ptt(s,FD_PTT_HAND,true,1));
    atomic_store(&fake.held[1],false);wait_state(s,FD_VS_RX);
    atomic_store(&fake.frozen,true);wait_state(s,FD_VS_FAULT);
    assert(fd_session_observe(s).ptt_unknown[0]); /* new wall time cannot refresh an old sequence */
    atomic_store(&fake.frozen,false);wait_state(s,FD_VS_RX);
    atomic_store(&fake.stale,true);wait_state(s,FD_VS_FAULT);assert(fd_session_observe(s).ptt_unknown[0]);
    fd_session_leave(s);wait_state(s,FD_VS_EXITING);assert(!fd_session_destroy(s));
    atomic_store(&fake.stale,false);wait_state(s,FD_VS_OFF);
    /* The retained watchdog clears TX even while the coordinator is inside
     * a stalled finish hook; its cause must survive that hook's failure. */
    assert(fd_session_enter(s,&usb,"fake","development",tuning,180));wait_state(s,FD_VS_RX);
    atomic_store(&fake.stall_finish,true);atomic_store(&fake.held[0],true);wait_state(s,FD_VS_TX);
    atomic_store(&fake.held[0],false);wait_state(s,FD_VS_TX_DRAIN);
    deadline=now()+2200;while(atomic_load(&fake.tx)){assert(now()<deadline);pause_ms();}
    atomic_store(&fake.stall_finish,false);wait_state(s,FD_VS_RX);
    fd_session_leave(s);wait_state(s,FD_VS_OFF);
    /* A held hand PTT keeps exit owned until independently released. */
    atomic_store(&fake.held[1],true);assert(fd_session_enter(s,&usb,"fake","development",tuning,180));
    wait_state(s,FD_VS_WAIT_PTT_RELEASE);fd_session_leave(s);wait_state(s,FD_VS_EXITING);assert(!fd_session_destroy(s));
    atomic_store(&fake.held[1],false);wait_state(s,FD_VS_OFF);
    /* Cancellation while prepare is still running. */
    atomic_store(&fake.stall,true);assert(fd_session_enter(s,&usb,"fake","development",tuning,180));
    deadline=now()+1000;while(!atomic_load(&fake.preparing)){assert(now()<deadline);pause_ms();}
    fd_session_leave(s);wait_state(s,FD_VS_OFF);assert(fd_session_destroy(s));
    atomic_store(&fake.stall,false);s=fd_session_create(&driver);assert(s);
    assert(fd_session_enter(s,&usb,"fake","development",tuning,180));wait_state(s,FD_VS_RX);
    atomic_store(&fake.maintenance_failure,true);wait_state(s,FD_VS_FAULT);
    unsigned maintained=atomic_load(&fake.maintenance_calls),cleared=atomic_load(&fake.clears);
    for(unsigned i=0;i<50;++i)pause_ms();
    assert(atomic_load(&fake.maintenance_calls)==maintained && atomic_load(&fake.clears)==cleared);
    assert(fd_session_observe(s).fault==FD_ABORT_DEVICE);
    fd_session_leave(s);wait_state(s,FD_VS_OFF);assert(fd_session_destroy(s));
    puts("PASS: coordinator latches failed lease maintenance once without resetting release/exit fences");
    puts("PASS: profile selection, 100 lifecycle cycles, RX tuning, overlapping PTT, drain repress, Stop, stale health, held-PTT exit, prepare cancellation");
}
