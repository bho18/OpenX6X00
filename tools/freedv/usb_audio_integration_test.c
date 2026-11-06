// SPDX-License-Identifier: BSD-2-Clause
#define _POSIX_C_SOURCE 200809L
#undef NDEBUG
#include "../../src/freedv/usb_voice.h"
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

typedef struct {
    atomic_bool pressed,physical_tx,packet_tx,halted,stall,freeze,protection,inflight;
    atomic_uint keys,clears,delay,block_key,block_clear;
    atomic_uint_fast64_t release,sequence,packet_at,ack_at;
} fake;
static uint64_t ms(void) {
    struct timespec t;clock_gettime(CLOCK_MONOTONIC,&t);
    return (uint64_t)t.tv_sec*1000+t.tv_nsec/1000000;
}
static void pause_ms(unsigned n) {
    struct timespec t={n/1000,(n%1000)*1000000};nanosleep(&t,NULL);
}
/* Packet cadence is independent of polling. The 650 ms acknowledgement case
 * intentionally differs from a blocking 900 ms hardware write. */
static bool observe(void *context,fd_usb_inputs *out) {
    fake *f=context;
    if(atomic_load(&f->stall))pause_ms(1000);
    uint64_t now=ms(),at=atomic_load(&f->packet_at);
    if(!atomic_load(&f->freeze) && now-at>=20) {
        uint64_t ack=atomic_load(&f->ack_at);
        bool tx=atomic_load(&f->physical_tx) && (!ack || now>=ack);
        atomic_store(&f->packet_tx,tx);atomic_store(&f->packet_at,now);
        atomic_fetch_add(&f->sequence,1);
    }
    *out=(fd_usb_inputs){.valid=true,.pressed=atomic_load(&f->pressed),
        .rf_valid=now-atomic_load(&f->packet_at)<=250,.rf_tx=atomic_load(&f->packet_tx),
        .protection=atomic_load(&f->protection),.release=atomic_load(&f->release),
        .sequence=atomic_load(&f->sequence)};
    return true;
}
static bool key(void *context,uint64_t release) {
    fake *f=context;
    if(atomic_load(&f->halted) || !atomic_load(&f->pressed) || release!=atomic_load(&f->release))return false;
    atomic_fetch_add(&f->keys,1);atomic_store(&f->inflight,true);
    unsigned block=atomic_load(&f->block_key);
    if(block){fprintf(stderr,"BLOCKING_KEY %u ms\n",block);fflush(stderr);pause_ms(block);}
    /* Model an I2C ON already in flight: Stop may fence future writes, but
     * cannot retract this write. The controller must issue compensating OFF. */
    atomic_store(&f->physical_tx,true);
    atomic_store(&f->ack_at,ms()+atomic_load(&f->delay));
    atomic_store(&f->inflight,false);return true;
}
static void clear(void *context,bool halt) {
    fake *f=context;
    if(halt)atomic_store(&f->halted,true);
    unsigned block=atomic_load(&f->block_clear);if(block)pause_ms(block);
    atomic_store(&f->physical_tx,false);atomic_store(&f->ack_at,0);
    atomic_fetch_add(&f->clears,1);
}
static void inhibit(void *context) { atomic_store(&((fake*)context)->halted,true); }
static void release_ptt(fake *f) {
    atomic_store(&f->pressed,false);atomic_fetch_add(&f->release,1);
}
static void marker(const char *name) {
    const char *path=getenv(name);assert(path);
    FILE *file=fopen(path,"w");assert(file);fputs("trigger\n",file);fclose(file);
}
static fd_usb_snapshot wait_state(fd_usb_audio *audio,fd_usb_state wanted,unsigned timeout) {
    uint64_t until=ms()+timeout;fd_usb_snapshot s={0};
    do {
        s=fd_usb_audio_snapshot(audio);
        if(s.state==wanted)return s;
        if(s.fault){fprintf(stderr,"Unexpected fault %u: %s at %s/%s\n",s.fault,
            fd_usb_fault_text(s.fault),s.first_fault_origin,s.first_fault_stream);assert(!s.fault);}
        pause_ms(5);
    }while(ms()<until);
    fprintf(stderr,"Wanted state %u, got %u\n",wanted,s.state);assert(0);return s;
}
static fd_usb_snapshot wait_stopped(fd_usb_audio *audio,unsigned timeout) {
    uint64_t until=ms()+timeout;fd_usb_snapshot s;
    do{s=fd_usb_audio_snapshot(audio);if(s.stopped)return s;pause_ms(5);}while(ms()<until);
    fprintf(stderr,"Stop did not complete: state=%u fault=%u\n",s.state,s.fault);assert(0);return s;
}
static void wait_key_inflight(fake *f) {
    uint64_t until=ms()+5000;
    while(!atomic_load(&f->inflight) && ms()<until)pause_ms(2);
    assert(atomic_load(&f->inflight));
}
static void result(const char *directory,const char *scenario,fake *f,fd_usb_snapshot s) {
    char path[768];snprintf(path,sizeof(path),"%s/harness-result.json",directory);
    FILE *out=fopen(path,"w");assert(out);
    fprintf(out,"{\"status\":\"PASS\",\"scenario\":\"%s\",\"state\":%u,\"fault\":%u,"
        "\"keys\":%u,\"clears\":%u,\"physical_tx\":%s,\"rf_valid\":%s,\"rf_tx\":%s,"
        "\"rf_clear_confirmed\":%s,\"turns\":%llu,\"eoo\":%llu,\"mic_captured\":%llu,"
        "\"mic_max_gap_ms\":%llu,\"rx_max_gap_ms\":%llu,\"mic_holes\":%u,\"rx_holes\":%u,"
        "\"qualification_complete\":%s,\"qualification_turns\":%u,\"first_fault_origin\":\"%s\","
        "\"first_fault_stream\":\"%s\",\"first_fault_pa_error\":%u,\"first_fault_operation_ms\":%llu}\n",
        scenario,s.state,s.fault,atomic_load(&f->keys),atomic_load(&f->clears),atomic_load(&f->physical_tx)?"true":"false",
        s.rf_valid?"true":"false",s.rf_tx?"true":"false",s.rf_clear_confirmed?"true":"false",
        (unsigned long long)s.turns,(unsigned long long)s.engine.eoo_count,(unsigned long long)s.mic_captured,
        (unsigned long long)s.mic_max_gap_ms,(unsigned long long)s.rx_max_gap_ms,s.mic_holes,s.rx_holes,
        s.qualification_complete?"true":"false",s.qualification_turns,s.first_fault_origin,s.first_fault_stream,
        s.first_fault_pa_error,(unsigned long long)s.first_fault_operation_ms);
    fclose(out);
}
int main(int argc,char **argv) {
    assert(argc==2 || argc==3);const char *scenario=argc==3?argv[2]:"--normal";
    fake f={0};atomic_store(&f.packet_at,ms());char prefix[512];
    snprintf(prefix,sizeof(prefix),"%s/session",argv[1]);
    bool qualification=!strcmp(scenario,"--rf-disabled") || !strcmp(scenario,"--qualification-physical-tx") ||
                       !strcmp(scenario,"--qualification-held-ptt");
    fd_usb_config config={.source="alsa_input.usb-fixture",.sink="alsa_output.usb-fixture",
        .radio_source="radio_source_fixture",.radio_sink="radio_sink_fixture",.trace_prefix=prefix,
        .control={.context=&f,.observe=observe,.key=key,.clear=clear,.inhibit=inhibit},
        .rf_disabled=qualification,.qualification_turns=3,.qualification_control_delay_ms=450};
    fd_usb_audio *audio=fd_usb_audio_open(&config);assert(audio);
    if(!strcmp(scenario,"--pa-reject") || !strcmp(scenario,"--pa-missing")) {
        fd_usb_snapshot s=wait_stopped(audio,10000);
        assert(s.fault==(!strcmp(scenario,"--pa-reject")?15u:14u));
        assert(!atomic_load(&f.keys) && !atomic_load(&f.physical_tx));
        assert(s.first_fault_origin[0] && s.first_fault_stream[0]);
        result(argv[1],scenario,&f,s);fd_usb_audio_close(audio);return 0;
    }
    wait_state(audio,FD_USB_RX,20000);pause_ms(350);
    if(!strcmp(scenario,"--rx-signal")) {
        uint64_t until=ms()+12000;
        while(fd_usb_audio_snapshot(audio).engine.synthesis_blocks<4 && ms()<until)pause_ms(10);
        assert(fd_usb_audio_snapshot(audio).engine.synthesis_blocks>=4);
    } else assert(!fd_usb_audio_snapshot(audio).engine.synchronized);
    if(qualification) {
        assert(!fd_usb_audio_enable_rf(audio));
        assert(fd_usb_audio_start_qualification(audio));
        if(strcmp(scenario,"--rf-disabled")) {
            pause_ms(300);
            if(!strcmp(scenario,"--qualification-physical-tx"))atomic_store(&f.physical_tx,true);
            else atomic_store(&f.pressed,true);
            fd_usb_snapshot s=wait_stopped(audio,10000);
            assert(s.fault && !s.qualification_complete && !atomic_load(&f.keys));
            assert(!atomic_load(&f.physical_tx));
            result(argv[1],scenario,&f,s);fd_usb_audio_close(audio);return 0;
        }
        uint64_t until=ms()+60000;fd_usb_snapshot s;
        do{s=fd_usb_audio_snapshot(audio);assert(!s.fault && !atomic_load(&f.keys));if(s.qualification_complete)break;pause_ms(10);}while(ms()<until);
        assert(s.qualification_complete && s.qualification_turns==3 && s.turns==3 && s.engine.eoo_count==3);
        assert(s.rf_disabled && !s.rf_tx && !atomic_load(&f.physical_tx));
        atomic_store(&f.pressed,true);pause_ms(50);assert(!fd_usb_audio_enable_rf(audio));release_ptt(&f);pause_ms(100);
        assert(fd_usb_audio_enable_rf(audio));
        fd_usb_audio_stop(audio);s=wait_stopped(audio,10000);
        assert(!atomic_load(&f.keys));result(argv[1],scenario,&f,s);fd_usb_audio_close(audio);return 0;
    }
    if(!strcmp(scenario,"--inactive-hole")) {
        marker("USB_TEST_HOLE_TRIGGER");pause_ms(250);assert(!fd_usb_audio_snapshot(audio).fault);
    }
    if(!strcmp(scenario,"--inactive-stale")) {
        marker("USB_TEST_STALE_TRIGGER");pause_ms(800);
        assert(!fd_usb_audio_snapshot(audio).fault);
        assert(remove(getenv("USB_TEST_STALE_TRIGGER"))==0);pause_ms(600);
    }
    fd_usb_audio_arm(audio,true);
    if(!strcmp(scenario,"--prekey-stale")) {
        marker("USB_TEST_STALE_TRIGGER");pause_ms(900);assert(!fd_usb_audio_snapshot(audio).fault);
        atomic_store(&f.pressed,true);fd_usb_snapshot s=wait_stopped(audio,10000);
        assert(s.fault==17 && !atomic_load(&f.keys) && !atomic_load(&f.physical_tx));
        result(argv[1],scenario,&f,s);fd_usb_audio_close(audio);return 0;
    }
    if(!strcmp(scenario,"--stop-during-key") || !strcmp(scenario,"--release-during-key")) {
        atomic_store(&f.block_key,900);atomic_store(&f.pressed,true);wait_key_inflight(&f);pause_ms(100);
        if(!strcmp(scenario,"--stop-during-key")) {
            uint64_t before=ms();fd_usb_audio_stop(audio);assert(ms()-before<100);assert(atomic_load(&f.halted));
            fd_usb_snapshot s=wait_stopped(audio,10000);assert(s.fault==1);
            pause_ms(300);assert(!atomic_load(&f.physical_tx) && atomic_load(&f.keys)==1);
            result(argv[1],scenario,&f,s);fd_usb_audio_close(audio);return 0;
        }
        release_ptt(&f);wait_state(audio,FD_USB_RX,10000);pause_ms(400);
        assert(!atomic_load(&f.physical_tx) && atomic_load(&f.keys)==1);
        assert(fd_usb_audio_snapshot(audio).engine.eoo_count==0);
        fd_usb_audio_stop(audio);fd_usb_snapshot s=wait_stopped(audio,10000);
        result(argv[1],scenario,&f,s);fd_usb_audio_close(audio);return 0;
    }
    /* Release before delayed TX acknowledgement: no accepted speech or EOO. */
    atomic_store(&f.delay,300);atomic_store(&f.pressed,true);
    uint64_t until=ms()+5000;while(!atomic_load(&f.keys) && ms()<until)pause_ms(5);assert(atomic_load(&f.keys)==1);
    pause_ms(50);release_ptt(&f);wait_state(audio,FD_USB_RX,6000);assert(!atomic_load(&f.physical_tx));pause_ms(100);
    if(!strcmp(scenario,"--stall")) {
        /* Stall the coordinator while waiting for an acknowledgement, when
         * microphone capture is intentionally discarded. This isolates the
         * watchdog from the separate bounded-queue overflow protection. */
        atomic_store(&f.delay,900);atomic_store(&f.pressed,true);
        wait_state(audio,FD_USB_KEY_PENDING,6000);atomic_store(&f.stall,true);
        pause_ms(750);assert(!atomic_load(&f.physical_tx));atomic_store(&f.stall,false);
        fd_usb_snapshot s=wait_stopped(audio,12000);assert(s.fault==11);
        assert(atomic_load(&f.halted));result(argv[1],scenario,&f,s);fd_usb_audio_close(audio);return 0;
    }
    atomic_store(&f.delay,!strcmp(scenario,"--block-key")?20:650);
    atomic_store(&f.block_key,!strcmp(scenario,"--block-key")?900:0);
    atomic_store(&f.pressed,true);wait_state(audio,FD_USB_TX,6000);
    atomic_store(&f.delay,0);atomic_store(&f.block_key,0);puts("TX_ACTIVE");fflush(stdout);
    unsigned expected=0;
    if(!strcmp(scenario,"--tx-stall")) {
        atomic_store(&f.stall,true);pause_ms(750);assert(!atomic_load(&f.physical_tx));atomic_store(&f.stall,false);expected=11;
    }else if(!strcmp(scenario,"--disconnect"))expected=2;
    else if(!strcmp(scenario,"--capture-stale")){marker("USB_TEST_STALE_TRIGGER");expected=17;}
    else if(!strcmp(scenario,"--active-hole")){marker("USB_TEST_HOLE_TRIGGER");expected=16;}
    else if(!strcmp(scenario,"--stale-status")){atomic_store(&f.freeze,true);expected=4;}
    if(expected) {
        fd_usb_snapshot s=wait_stopped(audio,12000);
        if(!strcmp(scenario,"--tx-stall")) {
            assert(s.fault==6 || s.fault==11);
            assert(!strcmp(s.first_fault_origin,s.fault==6?"pcm-queue-full":"coordinator-heartbeat"));
        }else assert(s.fault==expected);
        assert(!atomic_load(&f.physical_tx) && atomic_load(&f.halted));
        assert(s.first_fault_origin[0]);result(argv[1],scenario,&f,s);fd_usb_audio_close(audio);return 0;
    }
    /* Let a complete modem payload reach the real PA sink, then emulate a
     * release/repress between polls while the present level is still held. */
    if(!strcmp(scenario,"--inactive-radio-hole")){marker("USB_TEST_HOLE_TRIGGER");pause_ms(1700);}
    else if(!strcmp(scenario,"--inactive-radio-stale")) {
        marker("USB_TEST_STALE_TRIGGER");pause_ms(800);assert(!fd_usb_audio_snapshot(audio).fault);
        assert(remove(getenv("USB_TEST_STALE_TRIGGER"))==0);pause_ms(900);
    }else pause_ms(1700);
    atomic_fetch_add(&f.release,1);wait_state(audio,FD_USB_RX,6000);
    pause_ms(300);assert(atomic_load(&f.keys)==2 && !atomic_load(&f.physical_tx));
    release_ptt(&f);pause_ms(100);atomic_store(&f.pressed,true);wait_state(audio,FD_USB_TX,6000);pause_ms(1200);
    release_ptt(&f);wait_state(audio,FD_USB_RX,6000);
    pause_ms(50);fd_usb_snapshot s=fd_usb_audio_snapshot(audio);
    fprintf(stderr,"COMPLETED turns=%llu eoo=%llu\n",(unsigned long long)s.turns,(unsigned long long)s.engine.eoo_count);
    assert(s.turns==2 && s.engine.eoo_count==2);
    assert(s.engine.rade_creations==1 && s.engine.encoder_creations==1 && s.engine.decoder_creations==1);
    if(strcmp(scenario,"--inactive-stale"))assert(s.mic_max_gap_ms<500);
    if(strcmp(scenario,"--inactive-radio-stale"))assert(s.rx_max_gap_ms<500);
    fd_usb_audio_stop(audio);s=wait_stopped(audio,10000);
    assert(!atomic_load(&f.physical_tx) && atomic_load(&f.keys)==3);
    result(argv[1],scenario,&f,s);fd_usb_audio_close(audio);
    puts("PASS complete four-stream turns, retained release, fresh RX, no rekey and unchanged codec lifetime; fake RF only");
    return 0;
}
