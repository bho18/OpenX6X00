// SPDX-License-Identifier: BSD-2-Clause
#define _POSIX_C_SOURCE 200809L
#undef NDEBUG
#include "../../src/freedv/usb_voice.h"
#include "../../src/freedv/usb_render_budget.h"
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <pthread.h>
#include <dirent.h>
#include <sys/stat.h>
#include <errno.h>
#include <unistd.h>
#include <sched.h>
#ifndef FREEDV_BUILD_ID
#define FREEDV_BUILD_ID "unqualified-development"
#endif
#ifndef FREEDV_SOURCE_COMMIT
#define FREEDV_SOURCE_COMMIT "unqualified-development"
#endif
#include <limits.h>
#include <unistd.h>

/* Weak test-only symbols are resolved by the selected LD_PRELOAD injector. */
extern unsigned long long usb_test_cold_ack_ms(void) __attribute__((weak));
extern unsigned usb_test_cold_recovered(void) __attribute__((weak));
extern unsigned usb_test_io_pause_count(void) __attribute__((weak));
extern unsigned long long usb_test_io_pause_duration_ms(void) __attribute__((weak));
extern unsigned long long usb_test_io_pause_started_ms(void) __attribute__((weak));
extern unsigned long long usb_test_trigger_started_ms(void) __attribute__((weak));
extern unsigned long long usb_test_trigger_ack_ms(void) __attribute__((weak));
extern unsigned long long usb_test_render_hold_started_ms(void) __attribute__((weak));
extern unsigned long long usb_test_render_hold_ended_ms(void) __attribute__((weak));
extern unsigned usb_test_render_hold_hits(void) __attribute__((weak));
extern unsigned long long usb_test_mic_release_hold_started_ms(void) __attribute__((weak));
extern unsigned long long usb_test_mic_barrier_started_ms(void) __attribute__((weak));
extern void usb_test_mic_stop_callback(void (*)(void *),void *) __attribute__((weak));
extern void usb_test_mic_release_action(void (*)(void *),void *) __attribute__((weak));
extern int usb_test_mic_release_now(void) __attribute__((weak));
extern unsigned long long usb_test_mic_release_posted_ms(void) __attribute__((weak));
extern unsigned long long usb_test_backend_wait_started_ms(void) __attribute__((weak));
extern unsigned long long usb_test_backend_wait_until_us(void) __attribute__((weak));
extern void usb_test_mic_open_prefix_start(void) __attribute__((weak));
extern unsigned long long usb_test_mic_open_prefix_started_ms(void) __attribute__((weak));
extern unsigned long long usb_test_mic_open_prefix_released_ms(void) __attribute__((weak));
extern unsigned long long usb_test_mic_open_prefix_begin_index(void) __attribute__((weak));
extern unsigned usb_test_mic_open_prefix_hidden_peeks(void) __attribute__((weak));
extern void usb_test_volume_mode(unsigned) __attribute__((weak));
extern unsigned usb_test_headset_uncorks(void) __attribute__((weak));
extern unsigned usb_test_headset_payload_writes(void) __attribute__((weak));
extern unsigned usb_test_cue_connect_failures(void) __attribute__((weak));
extern void usb_test_cue_mode(unsigned) __attribute__((weak));
extern unsigned usb_test_cue_failures(void) __attribute__((weak));
extern unsigned usb_test_cue_pauses(void) __attribute__((weak));
extern unsigned long long usb_test_cue_pause_duration_ms(void) __attribute__((weak));
extern unsigned usb_test_volume_calls(void) __attribute__((weak));
extern unsigned usb_test_volume_failures(void) __attribute__((weak));
static void stop_from_close(void *audio){fd_usb_audio_stop(audio);}
static bool cold_case,cold_rx_case,cold_late_tx_case,key_retune_case,open_prefix_case;
static atomic_uint recovered_observations;
typedef struct {
    atomic_bool pressed,physical_tx,packet_tx,halted,stall,freeze,protection,inflight,permitted,configuration_ready,tx_eligible;
    atomic_uint_fast64_t tuning_revision;
    atomic_uint keys,key_attempts,key_denials,clears,delay,block_key,block_clear;
    atomic_uint_fast64_t release,sequence,packet_at,ack_at,freeze_until,freeze_recovered;
    pthread_t ticker;pthread_mutex_t packet_mutex;atomic_bool ticker_stop;atomic_uint packets_emitted,observations;
} fake;
typedef struct {unsigned key_sequence;uint64_t on_before_ns,on_after_ns,off_before_ns,off_after_ns;} rf_interval;
static rf_interval rf_intervals[128];static unsigned rf_interval_n;static pthread_mutex_t rf_events_mutex=PTHREAD_MUTEX_INITIALIZER;
static const char *run_directory,*run_scenario;static fake *run_control;
static atomic_uint rx_ready_repress_hits;static atomic_bool rx_ready_repress_up;static atomic_uint_fast64_t rx_ready_repress_release,rx_ready_repress_sequence;
static atomic_bool cue_press_armed;static atomic_uint cue_press_hits;
void usb_test_cue_admitted(void){
    if(run_control&&atomic_exchange(&cue_press_armed,false)){atomic_store(&run_control->pressed,true);atomic_fetch_add(&cue_press_hits,1);}
}
static uint64_t ms(void) {
    struct timespec t;clock_gettime(CLOCK_MONOTONIC,&t);
    return (uint64_t)t.tv_sec*1000+t.tv_nsec/1000000;
}
static uint64_t monotonic_ns(void){struct timespec t;clock_gettime(CLOCK_MONOTONIC,&t);return(uint64_t)t.tv_sec*1000000000+t.tv_nsec;}
static void set_physical_tx(fake *f,bool enabled){
    pthread_mutex_lock(&rf_events_mutex);uint64_t before=monotonic_ns();bool previous=atomic_exchange(&f->physical_tx,enabled);uint64_t after=monotonic_ns();
    if(previous!=enabled){
        if(enabled){assert(rf_interval_n<128);rf_intervals[rf_interval_n++]=(rf_interval){.key_sequence=atomic_load(&f->keys),.on_before_ns=before,.on_after_ns=after};}
        else{for(unsigned i=rf_interval_n;i>0;--i)if(!rf_intervals[i-1].off_after_ns){rf_intervals[i-1].off_before_ns=before;rf_intervals[i-1].off_after_ns=after;break;}
            printf("RF_PHYSICAL_OFF before_ns=%llu after_ns=%llu keys=%u\n",(unsigned long long)before,(unsigned long long)after,atomic_load(&f->keys));fflush(stdout);}
    }
    pthread_mutex_unlock(&rf_events_mutex);
}
static void pause_ms(unsigned n) {
    struct timespec t={n/1000,(n%1000)*1000000};nanosleep(&t,NULL);
}
/* Core calls this after publishing RX and before another RX input poll.
 * A direct new DOWN here deterministically tests the advertised readiness
 * boundary; there is no sleep and no extra UP sample to conceal a gap. */
void usb_test_rx_ready_published(void){
    fake *f=run_control;
    if(!f||!run_scenario||strcmp(run_scenario,"--operational-open-release")||atomic_load(&f->keys)!=1||atomic_load(&f->release)!=1||atomic_load(&rx_ready_repress_hits))return;
    bool up=!atomic_load(&f->pressed);assert(up&&!atomic_load(&f->physical_tx));
    atomic_store(&rx_ready_repress_up,up);atomic_store(&rx_ready_repress_release,atomic_load(&f->release));atomic_store(&rx_ready_repress_sequence,atomic_load(&f->sequence));
    atomic_store(&f->pressed,true);atomic_fetch_add(&rx_ready_repress_hits,1);
    printf("RX_READY_REPRESS boundary_ms=%llu up=%u release=%llu sequence=%llu before_next_rx_poll=1\n",(unsigned long long)ms(),up,(unsigned long long)atomic_load(&rx_ready_repress_release),(unsigned long long)atomic_load(&rx_ready_repress_sequence));fflush(stdout);
}
/* Real packet arrival is a separate producer. Polling never refreshes age or
 * sequence, so a blocked observer cannot manufacture fresh telemetry. */
static void emit_packet(fake *f){
    pthread_mutex_lock(&f->packet_mutex);uint64_t now=ms(),ack=atomic_load(&f->ack_at);
    if(atomic_load(&f->freeze)){pthread_mutex_unlock(&f->packet_mutex);return;}
    atomic_store(&f->packet_tx,atomic_load(&f->physical_tx)&&(!ack||now>=ack));
    atomic_store(&f->packet_at,now);atomic_fetch_add(&f->sequence,1);atomic_fetch_add(&f->packets_emitted,1);
    pthread_mutex_unlock(&f->packet_mutex);
}
static void *telemetry(void *context){fake *f=context;
    while(!atomic_load(&f->ticker_stop)){
        uint64_t until=atomic_load(&f->freeze_until);bool recovered=until&&ms()>=until;
        if(recovered){pthread_mutex_lock(&f->packet_mutex);atomic_store(&f->freeze,false);atomic_store(&f->freeze_until,0);atomic_store(&f->freeze_recovered,ms());pthread_mutex_unlock(&f->packet_mutex);}
        if(!atomic_load(&f->freeze))emit_packet(f);
        if(recovered)fprintf(stderr,"INJECT qualification telemetry resumed resumed_ms=%llu sequence=%llu\n",(unsigned long long)atomic_load(&f->freeze_recovered),(unsigned long long)atomic_load(&f->sequence));
        pause_ms(20);
    }return NULL;
}
static void start_telemetry(fake *f){pthread_mutex_init(&f->packet_mutex,NULL);emit_packet(f);assert(!pthread_create(&f->ticker,NULL,telemetry,f));}
static void stop_telemetry(fake *f){atomic_store(&f->ticker_stop,true);pthread_join(f->ticker,NULL);pthread_mutex_destroy(&f->packet_mutex);}
static bool observe(void *context,fd_usb_inputs *out) {
    fake *f=context;
    if(atomic_load(&f->stall))pause_ms(1000);
    if(cold_late_tx_case&&usb_test_cold_ack_ms&&usb_test_cold_ack_ms()){
        /* Let the first post-ACK observation see both completed callbacks,
         * avoiding a scheduler race between observation and metric reads. */
        uint64_t until=ms()+200;
        while(usb_test_cold_recovered()!=3&&ms()<until)pause_ms(1);
    }
    if(cold_late_tx_case&&usb_test_cold_recovered&&usb_test_cold_recovered()==3&&
       atomic_fetch_add(&recovered_observations,1)==1){
        /* First recovered observation admits fresh frames; the next one is
         * the final pre-key observation and must still require actual RX. */
        set_physical_tx(f,true);emit_packet(f);
    }
    atomic_fetch_add(&f->observations,1);pthread_mutex_lock(&f->packet_mutex);uint64_t now=ms(),at=atomic_load(&f->packet_at);
    *out=(fd_usb_inputs){.valid=true,.pressed=atomic_load(&f->pressed),
        .rf_valid=now>=at&&now-at<=250,.rf_tx=atomic_load(&f->packet_tx),
        .protection=atomic_load(&f->protection),.release=atomic_load(&f->release),
        .sequence=atomic_load(&f->sequence),.rf_age_ms=now>=at?now-at:0,.configuration_ready=atomic_load(&f->configuration_ready),
        .tuning_revision=atomic_load(&f->tuning_revision),.tx_eligible=atomic_load(&f->tx_eligible)};
    pthread_mutex_unlock(&f->packet_mutex);
    return true;
}
static bool key(void *context,uint64_t release) {
    fake *f=context;
    unsigned attempt=atomic_fetch_add(&f->key_attempts,1)+1;
    if(key_retune_case&&attempt==1){
        atomic_store(&f->tuning_revision,2);atomic_store(&f->configuration_ready,false);atomic_store(&f->permitted,false);
        atomic_fetch_add(&f->key_denials,1);puts("KEY_RETUNE_DENIED revision=2 RF=off");fflush(stdout);return false;
    }
    if(open_prefix_case&&attempt==1){assert(usb_test_mic_open_prefix_start);usb_test_mic_open_prefix_start();atomic_store(&f->delay,80);}
    if(cold_case&&!cold_rx_case){assert(usb_test_cold_ack_ms&&usb_test_cold_recovered);
        assert(usb_test_cold_ack_ms()>0&&usb_test_cold_recovered()==3);}
    if(atomic_load(&f->halted) || !atomic_load(&f->permitted) || !atomic_load(&f->pressed) || release!=atomic_load(&f->release))return false;
    atomic_fetch_add(&f->keys,1);atomic_store(&f->inflight,true);
    unsigned block=atomic_load(&f->block_key);
    if(block){fprintf(stderr,"BLOCKING_KEY %u ms\n",block);fflush(stderr);pause_ms(block);}
    /* Model an I2C ON already in flight: Stop may fence future writes, but
     * cannot retract this write. The controller must issue compensating OFF. */
    set_physical_tx(f,true);
    atomic_store(&f->ack_at,ms()+atomic_load(&f->delay));
    atomic_store(&f->inflight,false);return true;
}
static void clear(void *context,bool halt) {
    fake *f=context;
    if(halt)atomic_store(&f->halted,true);
    unsigned block=atomic_load(&f->block_clear);if(block)pause_ms(block);
    set_physical_tx(f,false);atomic_store(&f->ack_at,0);
    atomic_fetch_add(&f->clears,1);
}
static void inhibit(void *context) { atomic_store(&((fake*)context)->halted,true); }
static bool permit(void *context,bool enabled){fake *f=context;if(enabled&&atomic_load(&f->halted))return false;atomic_store(&f->permitted,enabled);return true;}
static void release_ptt(fake *f) {
    atomic_store(&f->pressed,false);atomic_fetch_add(&f->release,1);
}
static void release_from_callback(void *context){release_ptt(context);}
typedef struct {pthread_mutex_t pa;fake input;atomic_bool attempted,observed;} release_lock_model;
static void *release_lock_worker(void *context){
    release_lock_model *model=context;
    /* Prove the old circular-wait interleaving: the callback owns PA and the
     * coordinator cannot observe the input until that callback returns. */
    assert(pthread_mutex_trylock(&model->pa)==EBUSY);atomic_store(&model->attempted,true);
    assert(!pthread_mutex_lock(&model->pa));assert(!atomic_load(&model->input.pressed)&&atomic_load(&model->input.release)==1);
    atomic_store(&model->observed,true);assert(!pthread_mutex_unlock(&model->pa));return NULL;
}
static int release_lock_protocol_selftest(void){
    assert(usb_test_mic_release_action&&usb_test_mic_release_now&&usb_test_mic_release_posted_ms);
    release_lock_model model={0};atomic_store(&model.input.pressed,true);assert(!pthread_mutex_init(&model.pa,NULL));assert(!pthread_mutex_lock(&model.pa));
    pthread_t worker;assert(!pthread_create(&worker,NULL,release_lock_worker,&model));uint64_t until=ms()+1000;
    while(!atomic_load(&model.attempted)&&ms()<until)sched_yield();
    assert(atomic_load(&model.attempted)&&!atomic_load(&model.observed));
    usb_test_mic_release_action(release_from_callback,&model.input);assert(usb_test_mic_release_now()==1);
    assert(usb_test_mic_release_posted_ms()&&!atomic_load(&model.observed)&&!atomic_load(&model.input.pressed)&&atomic_load(&model.input.release)==1);
    assert(!pthread_mutex_unlock(&model.pa));assert(!pthread_join(worker,NULL));assert(atomic_load(&model.observed));assert(!pthread_mutex_destroy(&model.pa));
    puts("PASS release-lock-protocol: coordinator blocked; retained release posted without waiting; coordinator observed after callback unlock; no Pulse/codec/RF");
    printf("RELEASE_LOCK_PROTOCOL {\"status\":\"PASS\",\"source_commit\":\"%s\",\"build_id\":\"%s\",\"coordinator_blocked\":true,\"release_posted_without_wait\":true,\"observed_after_unlock\":true,\"radio_accessed\":false}\n",FREEDV_SOURCE_COMMIT,FREEDV_BUILD_ID);return 0;
}
static void marker(const char *name) {
    const char *path=getenv(name);assert(path);
    FILE *file=fopen(path,"w");assert(file);fputs("trigger\n",file);fclose(file);
}
static _Noreturn void failed_wait(fd_usb_audio *audio,const char *reason,fd_usb_snapshot original){
    uint64_t begin=ms();fd_usb_audio_stop(audio);fd_usb_snapshot last=fd_usb_audio_snapshot(audio);
    while(!last.stopped&&ms()-begin<5000){pause_ms(5);last=fd_usb_audio_snapshot(audio);}
    bool closed=false;if(last.stopped){fd_usb_audio_close(audio);closed=true;}
    char path[4096];snprintf(path,sizeof(path),"%s/harness-failure.json",run_directory);
    FILE *out=fopen(path,"w");
    if(out){fprintf(out,"{\"status\":\"FAIL\",\"scenario\":\"%s\",\"reason\":\"%s\",\"primary_fault\":%u,\"primary_origin\":\"%s\",\"cleanup_requested\":true,\"cleanup_wait_ms\":%llu,\"stopped\":%s,\"rf_clear_confirmed\":%s,\"closed\":%s,\"keys\":%u,\"rf_disabled\":%s,\"telemetry_freeze_recovered_ms\":%llu,\"exit_code\":2}\n",
        run_scenario,reason,original.fault,original.first_fault_origin[0]?original.first_fault_origin:last.first_fault_origin,(unsigned long long)(ms()-begin),last.stopped?"true":"false",last.rf_clear_confirmed?"true":"false",closed?"true":"false",run_control?atomic_load(&run_control->keys):0,last.rf_disabled?"true":"false",(unsigned long long)(run_control?atomic_load(&run_control->freeze_recovered):0));fclose(out);}
    fprintf(stderr,"FAILURE_CLEANUP primary_fault=%u stopped=%u closed=%u elapsed_ms=%llu\n",original.fault,last.stopped,closed,(unsigned long long)(ms()-begin));
    if(closed&&run_control)stop_telemetry(run_control);
    fflush(stdout);fflush(stderr);_Exit(2);
}
static fd_usb_snapshot healthy_snapshot(fd_usb_audio *audio,const char *reason){
    fd_usb_snapshot s=fd_usb_audio_snapshot(audio);
    if(s.fault){fprintf(stderr,"Unexpected fault %u: %s at %s/%s\n",s.fault,fd_usb_fault_text(s.fault),s.first_fault_origin,s.first_fault_stream);failed_wait(audio,reason,s);}
    return s;
}
static fd_usb_snapshot wait_mic_health(fd_usb_audio *audio,fd_usb_mic_health wanted,unsigned timeout){
    uint64_t until=ms()+timeout;fd_usb_snapshot s={0};
    do{s=healthy_snapshot(audio,"refinement-microphone-health-fault");if(s.mic_health==wanted)return s;pause_ms(5);}while(ms()<until);
    failed_wait(audio,"refinement-microphone-health-timeout",s);
}
static void refinement_phase(fd_usb_audio *audio,const char *phase){
    fd_usb_snapshot s=healthy_snapshot(audio,"refinement-phase-fault");char path[PATH_MAX];
    int n=snprintf(path,sizeof(path),"%s/refinement-phases.jsonl",run_directory);assert(n>0&&(size_t)n<sizeof(path));
    FILE *out=fopen(path,"a");assert(out);
    fprintf(out,"{\"phase\":\"%s\",\"ms\":%llu,\"state\":%u,\"fault\":%u,\"mic_health\":%u,\"mic_health_changed_ms\":%llu,\"mic_recovery_epoch\":%llu,\"mic_recovery_started_ms\":%llu,\"mic_recovery_attempts\":%u,\"mic_retry_available\":%s,\"mic_age_ms\":%llu,\"armed\":%s,\"pressed\":%s,\"physical_release_sequence\":%llu,\"keys\":%u,\"submitted_mic\":%llu,\"headset_volume_percent\":%u,\"headset_muted\":%s,\"headset_volume_ready\":%s,\"headset_volume_pending\":%s,\"headset_volume_initialized\":%s,\"headset_volume_requested_percent\":%u,\"headset_volume_requested_muted\":%s,\"headset_volume_requested_revision\":%llu,\"headset_volume_confirmed_revision\":%llu,\"headset_volume_failed_revision\":%llu,\"cue_available\":%s,\"cue_active\":%s,\"cue_requests\":%llu,\"cue_completed\":%llu,\"cue_canceled\":%llu,\"cue_failures\":%llu,\"mic_meter_ready\":%s,\"mic_health_reason\":\"%s\"}\n",
        phase,(unsigned long long)ms(),s.state,s.fault,s.mic_health,(unsigned long long)s.mic_health_changed_ms,
        (unsigned long long)s.mic_recovery_epoch,(unsigned long long)s.mic_recovery_started_ms,s.mic_recovery_attempts,
        s.mic_retry_available?"true":"false",(unsigned long long)s.mic_age_ms,s.armed?"true":"false",s.pressed?"true":"false",
        (unsigned long long)atomic_load(&run_control->release),atomic_load(&run_control->keys),(unsigned long long)s.submitted_mic,
        s.headset_volume_percent,s.headset_muted?"true":"false",s.headset_volume_ready?"true":"false",s.headset_volume_pending?"true":"false",
        s.headset_volume_initialized?"true":"false",s.headset_volume_requested_percent,s.headset_volume_requested_muted?"true":"false",
        (unsigned long long)s.headset_volume_requested_revision,(unsigned long long)s.headset_volume_confirmed_revision,
        (unsigned long long)s.headset_volume_failed_revision,s.cue_available?"true":"false",s.cue_active?"true":"false",
        (unsigned long long)s.cue_requests,(unsigned long long)s.cue_completed,(unsigned long long)s.cue_canceled,(unsigned long long)s.cue_failures,s.mic_meter_ready?"true":"false",s.mic_health_reason);
    assert(!fclose(out));
}
/* This case exercises only the audio controls in RX. The Python driver
 * independently queries the real private-Pulse sink before acknowledging each
 * phase; no snapshot or successful API return substitutes for that readback. */
static fd_usb_snapshot controls_snapshot(fd_usb_audio *audio,int gain,unsigned volume,bool muted){
    uint64_t until=ms()+3000;fd_usb_snapshot s={0};
    do{
        s=healthy_snapshot(audio,"unexpected-audio-controls-fault");
        if(s.mic_meter_ready&&s.mic_gain_db==gain&&s.headset_volume_ready&&
           s.headset_volume_percent==volume&&s.headset_muted==muted)return s;
        pause_ms(5);
    }while(ms()<until);
    failed_wait(audio,"audio-controls-readback-timeout",s);
}
static fd_usb_snapshot confirmed_volume(fd_usb_audio *audio,unsigned volume,bool muted){
    uint64_t until=ms()+3500;fd_usb_snapshot s={0};
    do{s=healthy_snapshot(audio,"refinement-volume-fault");
        if(s.headset_volume_initialized&&s.headset_volume_ready&&!s.headset_volume_pending&&
           s.headset_volume_percent==volume&&s.headset_muted==muted&&
           s.headset_volume_confirmed_revision==s.headset_volume_requested_revision)return s;
        pause_ms(5);
    }while(ms()<until);failed_wait(audio,"refinement-volume-readback-timeout",s);
}
static void controls_phase(fd_usb_audio *audio,fake *f,const char *directory,const char *name,fd_usb_snapshot s){
    if(!strcmp(run_scenario,"--audio-controls")){
        assert(s.state==FD_USB_RX&&!s.armed&&!s.rf_tx&&!s.turns&&!s.submitted_mic&&!s.mic_clipped_samples);
        assert(!atomic_load(&f->keys)&&!atomic_load(&f->pressed)&&!atomic_load(&f->physical_tx));
    }
    assert(s.engine.rade_creations==1&&s.engine.encoder_creations==1&&s.engine.decoder_creations==1&&
           s.engine.resampler_creations==4&&s.engine.worker_creations==2);
    char path[PATH_MAX];int n=snprintf(path,sizeof(path),"%s/audio-controls-%s.json",directory,name);
    assert(n>0&&(size_t)n<sizeof(path));FILE *out=fopen(path,"w");assert(out);
    fprintf(out,"{\"phase\":\"%s\",\"monotonic_ms\":%llu,\"state\":%u,\"generation\":%llu,\"armed\":%s,\"keys\":%u,\"rf_tx\":%s,\"turns\":%llu,\"submitted_mic\":%llu,\"mic_clipped_samples\":%llu,\"mic_gain_db\":%d,\"mic_meter_ready\":%s,\"mic_input_rms_dbfs10\":%d,\"mic_input_peak_dbfs10\":%d,\"mic_rms_dbfs10\":%d,\"mic_peak_dbfs10\":%d,\"headset_volume_ready\":%s,\"headset_volume_percent\":%u,\"headset_muted\":%s,\"headset_volume_requested_percent\":%u,\"headset_volume_requested_revision\":%llu,\"headset_volume_confirmed_revision\":%llu,\"rade_creations\":%u,\"encoder_creations\":%u,\"decoder_creations\":%u,\"resampler_creations\":%u,\"worker_creations\":%u}\n",
        name,(unsigned long long)ms(),s.state,(unsigned long long)s.generation,s.armed?"true":"false",atomic_load(&f->keys),s.rf_tx?"true":"false",
        (unsigned long long)s.turns,(unsigned long long)s.submitted_mic,(unsigned long long)s.mic_clipped_samples,s.mic_gain_db,
        s.mic_meter_ready?"true":"false",s.mic_input_rms_dbfs10,s.mic_input_peak_dbfs10,s.mic_rms_dbfs10,s.mic_peak_dbfs10,
        s.headset_volume_ready?"true":"false",s.headset_volume_percent,s.headset_muted?"true":"false",s.headset_volume_requested_percent,
        (unsigned long long)s.headset_volume_requested_revision,(unsigned long long)s.headset_volume_confirmed_revision,
        s.engine.rade_creations,s.engine.encoder_creations,s.engine.decoder_creations,s.engine.resampler_creations,s.engine.worker_creations);
    assert(!fclose(out));
    n=snprintf(path,sizeof(path),"%s/audio-controls-%s-ack",directory,name);assert(n>0&&(size_t)n<sizeof(path));
    uint64_t until=ms()+5000;
    while(access(path,F_OK)&&ms()<until){(void)healthy_snapshot(audio,"unexpected-audio-controls-observer-fault");pause_ms(5);}
    if(access(path,F_OK))failed_wait(audio,"audio-controls-observer-timeout",s);
}
static void audio_controls(fd_usb_audio *audio,fake *f,const char *directory){
    fd_usb_snapshot s=controls_snapshot(audio,0,40,true);uint64_t generation=s.generation;
    controls_phase(audio,f,directory,"baseline",s);
    for(unsigned i=0;i<3;++i)assert(fd_usb_audio_adjust_headset_volume(audio,1));
    controls_phase(audio,f,directory,"up",controls_snapshot(audio,0,43,false));
    for(unsigned i=0;i<3;++i)assert(fd_usb_audio_adjust_headset_volume(audio,-1));
    controls_phase(audio,f,directory,"down",controls_snapshot(audio,0,40,false));
    const int requests[]={6,INT_MAX,INT_MIN,0};const int applied[]={6,24,-12,0};
    const char *names[]={"gain6","gain24","gainminus12","gain0"};
    for(unsigned i=0;i<4;++i){
        assert(fd_usb_audio_set_mic_gain(audio,requests[i]));s=controls_snapshot(audio,applied[i],40,false);
        assert(s.generation==generation);controls_phase(audio,f,directory,names[i],s);
    }
}
static void qualification_status_freeze(fd_usb_audio *audio,fake *f){
    if(!getenv("USB_TEST_QUALIFICATION_STATUS_FREEZE"))return;
    uint64_t until=ms()+2000;fd_usb_snapshot s;
    do{s=healthy_snapshot(audio,"unexpected-qualification-start-fault");if(s.qualification_running)break;pause_ms(1);}while(ms()<until);
    if(!s.qualification_running)failed_wait(audio,"qualification-start-timeout",s);
    pthread_mutex_lock(&f->packet_mutex);uint64_t now=ms(),packet=atomic_load(&f->packet_at),sequence=atomic_load(&f->sequence);
    atomic_store(&f->freeze,true);atomic_store(&f->freeze_until,now+350);pthread_mutex_unlock(&f->packet_mutex);
    char path[4096];snprintf(path,sizeof(path),"%s/qualification-status-freeze.json",run_directory);FILE *out=fopen(path,"w");
    if(out){fprintf(out,"{\"kind\":\"test-telemetry-freeze\",\"ms\":%llu,\"duration_ms\":350,\"packet_ms\":%llu,\"sequence\":%llu,\"qualification_running\":true,\"keys\":%u}\n",(unsigned long long)now,(unsigned long long)packet,(unsigned long long)sequence,atomic_load(&f->keys));fclose(out);}
    fprintf(stderr,"INJECT qualification telemetry freeze started_ms=%llu duration_ms=350 packet_ms=%llu sequence=%llu\n",(unsigned long long)now,(unsigned long long)packet,(unsigned long long)sequence);
}
static fd_usb_snapshot wait_state(fd_usb_audio *audio,fd_usb_state wanted,unsigned timeout) {
    uint64_t until=ms()+timeout;fd_usb_snapshot s={0};
    do {
        s=fd_usb_audio_snapshot(audio);
        if(s.fault){fprintf(stderr,"Unexpected fault %u: %s at %s/%s\n",s.fault,
            fd_usb_fault_text(s.fault),s.first_fault_origin,s.first_fault_stream);failed_wait(audio,"unexpected-state-fault",s);}
        if(s.state==wanted)return s;
        pause_ms(5);
    }while(ms()<until);
    fprintf(stderr,"Wanted state %u, got %u\n",wanted,s.state);failed_wait(audio,"state-timeout",s);
}
static fd_usb_snapshot wait_completed_turn(fd_usb_audio *audio,uint64_t turns,uint64_t eoo,unsigned timeout){
    /* The public state is an immediate atomic observation; the counters and
     * RF-clear flag arrive in the next copied snapshot. RX alone therefore
     * cannot establish that this particular turn has completed. */
    uint64_t until=ms()+timeout;fd_usb_snapshot s={0};bool reported=false;
    do{
        s=healthy_snapshot(audio,"unexpected-turn-completion-fault");
        if(s.state==FD_USB_RX&&s.turns==turns&&s.engine.eoo_count==eoo&&
           s.rf_valid&&!s.rf_tx&&s.rf_clear_confirmed)return s;
        if(!reported&&s.state==FD_USB_RX){
            fprintf(stderr,"TURN_RETURN_PENDING turns=%llu expected_turns=%llu eoo=%llu expected_eoo=%llu rf_clear=%u\n",
                (unsigned long long)s.turns,(unsigned long long)turns,(unsigned long long)s.engine.eoo_count,
                (unsigned long long)eoo,s.rf_clear_confirmed?1u:0u);reported=true;
        }
        pause_ms(5);
    }while(ms()<until);
    failed_wait(audio,"turn-completion-timeout",s);
}
static fd_usb_snapshot wait_stopped(fd_usb_audio *audio,unsigned timeout) {
    uint64_t until=ms()+timeout;fd_usb_snapshot s;
    do{s=fd_usb_audio_snapshot(audio);if(s.stopped)return s;pause_ms(5);}while(ms()<until);
    fprintf(stderr,"Stop did not complete: state=%u fault=%u\n",s.state,s.fault);failed_wait(audio,"stop-timeout",s);
}
static void wait_key_inflight(fd_usb_audio *audio,fake *f) {
    uint64_t until=ms()+5000;
    while(!atomic_load(&f->inflight) && ms()<until){(void)healthy_snapshot(audio,"unexpected-key-wait-fault");pause_ms(2);}
    assert(atomic_load(&f->inflight));
}
typedef struct {char session[32];unsigned turn;uint64_t accepted_mic,rendered_modem,modem_tap_start,modem_tap_end,started_ms,ended_ms;} turn_record;
static turn_record turn_records[32];static unsigned turn_record_n,durability_sessions,total_turns,total_eoo;
static unsigned closed_fds[10],closed_tasks[10];static char session_prefixes[10][32];
static unsigned resources(const char *name){DIR *d=opendir(name);assert(d);unsigned n=0;struct dirent *e;
    while((e=readdir(d)))if(strcmp(e->d_name,".")&&strcmp(e->d_name,".."))++n;
    closedir(d);return n;
}
static void durable_turn(fd_usb_audio *audio,fake *f,const char *session,unsigned turn,unsigned hold_ms){
    fd_usb_snapshot before=healthy_snapshot(audio,"unexpected-durability-start-fault");assert(before.state==FD_USB_RX&&!before.rf_tx);
    atomic_store(&f->pressed,true);wait_state(audio,FD_USB_TX,10000);uint64_t started=ms();puts("TX_ACTIVE");fflush(stdout);
    pause_ms(hold_ms);release_ptt(f);fd_usb_snapshot after=wait_completed_turn(audio,before.turns+1,before.engine.eoo_count+1,10000);pause_ms(100);
    assert(after.turns==before.turns+1&&after.engine.eoo_count==before.engine.eoo_count+1&&after.rf_clear_confirmed);
    assert(after.engine.rade_creations==1&&after.engine.encoder_creations==1&&after.engine.decoder_creations==1);
    assert(turn_record_n<32);turn_record *r=&turn_records[turn_record_n++];snprintf(r->session,sizeof(r->session),"%s",session);r->turn=turn;
    r->modem_tap_start=before.modem_tap_samples;r->modem_tap_end=after.modem_tap_samples;
    r->accepted_mic=after.submitted_mic-before.submitted_mic;r->rendered_modem=after.rendered_modem-before.rendered_modem;r->started_ms=started;r->ended_ms=ms();
    assert(r->accepted_mic>=48000&&r->rendered_modem>0);++total_turns;++total_eoo;
}
static void result(const char *directory,const char *scenario,fake *f,fd_usb_snapshot s) {
    char path[768];snprintf(path,sizeof(path),"%s/harness-result.json",directory);
    FILE *out=fopen(path,"w");assert(out);
    fprintf(out,"{\"status\":\"PASS\",\"scenario\":\"%s\",\"state\":%u,\"fault\":%u,"
        "\"keys\":%u,\"clears\":%u,\"physical_tx\":%s,\"rf_valid\":%s,\"rf_tx\":%s,"
        "\"rf_clear_confirmed\":%s,\"turns\":%llu,\"eoo\":%llu,\"mic_captured\":%llu,\"submitted_mic\":%llu,"
        "\"mic_max_gap_ms\":%llu,\"rx_max_gap_ms\":%llu,\"mic_holes\":%u,\"rx_holes\":%u,"
        "\"qualification_complete\":%s,\"qualification_turns\":%u,\"first_fault_origin\":\"%s\","
        "\"first_fault_stream\":\"%s\",\"first_fault_pa_error\":%u,\"first_fault_operation_ms\":%llu}\n",
        scenario,s.state,s.fault,atomic_load(&f->keys),atomic_load(&f->clears),atomic_load(&f->physical_tx)?"true":"false",
        s.rf_valid?"true":"false",s.rf_tx?"true":"false",s.rf_clear_confirmed?"true":"false",
        (unsigned long long)s.turns,(unsigned long long)s.engine.eoo_count,(unsigned long long)s.mic_captured,(unsigned long long)s.submitted_mic,
        (unsigned long long)s.mic_max_gap_ms,(unsigned long long)s.rx_max_gap_ms,s.mic_holes,s.rx_holes,
        s.qualification_complete?"true":"false",s.qualification_turns,s.first_fault_origin,s.first_fault_stream,
        s.first_fault_pa_error,(unsigned long long)s.first_fault_operation_ms);
    assert(!fseek(out,-2,SEEK_CUR));
    fprintf(out,",\"session_count\":%u,\"total_turns\":%u,\"total_eoo\":%u,\"telemetry_oracle\":{\"period_ms\":20,\"emitted\":%u,\"observations\":%u,\"poll_refresh_probe_pass\":true},\"session_prefixes\":[",
        durability_sessions?durability_sessions:1,durability_sessions?total_turns:(unsigned)s.turns,durability_sessions?total_eoo:(unsigned)s.engine.eoo_count,atomic_load(&f->packets_emitted),atomic_load(&f->observations));
    for(unsigned i=0;i<durability_sessions;++i)fprintf(out,"%s\"%s\"",i?",":"",session_prefixes[i]);
    fputs("],\"resource_samples\":[",out);
    for(unsigned i=0;i<durability_sessions;++i)fprintf(out,"%s{\"fds\":%u,\"threads\":%u}",i?",":"",closed_fds[i],closed_tasks[i]);
    fputs("],\"turn_records\":[",out);
    for(unsigned i=0;i<turn_record_n;++i){turn_record *r=&turn_records[i];fprintf(out,"%s{\"session\":\"%s\",\"turn\":%u,\"accepted_mic_samples\":%llu,\"rendered_modem_samples\":%llu,\"modem_tap_start\":%llu,\"modem_tap_end\":%llu,\"started_ms\":%llu,\"ended_ms\":%llu}",i?",":"",r->session,r->turn,(unsigned long long)r->accepted_mic,(unsigned long long)r->rendered_modem,(unsigned long long)r->modem_tap_start,(unsigned long long)r->modem_tap_end,(unsigned long long)r->started_ms,(unsigned long long)r->ended_ms);}
    fprintf(out,"],\"operational\":%s,\"startup_ready\":%s,\"leaving\":%s,\"configuration_ready\":%s,\"tuning_revision\":%llu,\"tx_timeout_seconds\":%u,\"physical_ptt_pressed\":%s,\"physical_release_sequence\":%llu,\"rade_creations\":%llu,\"encoder_creations\":%llu,\"decoder_creations\":%llu,\"rx_synthesis_blocks\":%llu,\"key_attempts\":%u,\"key_denials\":%u,\"rx_ready_repress_hits\":%u,\"rx_ready_repress_up\":%s,\"rx_ready_repress_release\":%llu,\"rx_ready_repress_sequence\":%llu,\"rf_intervals\":[",s.operational?"true":"false",s.startup_ready?"true":"false",s.leaving?"true":"false",s.configuration_ready?"true":"false",(unsigned long long)s.tuning_revision,s.tx_timeout_seconds,atomic_load(&f->pressed)?"true":"false",(unsigned long long)atomic_load(&f->release),(unsigned long long)s.engine.rade_creations,(unsigned long long)s.engine.encoder_creations,(unsigned long long)s.engine.decoder_creations,(unsigned long long)s.engine.synthesis_blocks,atomic_load(&f->key_attempts),atomic_load(&f->key_denials),atomic_load(&rx_ready_repress_hits),atomic_load(&rx_ready_repress_up)?"true":"false",(unsigned long long)atomic_load(&rx_ready_repress_release),(unsigned long long)atomic_load(&rx_ready_repress_sequence));pthread_mutex_lock(&rf_events_mutex);
    for(unsigned i=0;i<rf_interval_n;++i){rf_interval r=rf_intervals[i];fprintf(out,"%s{\"key_sequence\":%u,\"on_before_ns\":%llu,\"on_after_ns\":%llu,\"off_before_ns\":%llu,\"off_after_ns\":%llu}",i?",":"",r.key_sequence,(unsigned long long)r.on_before_ns,(unsigned long long)r.on_after_ns,(unsigned long long)r.off_before_ns,(unsigned long long)r.off_after_ns);}
    pthread_mutex_unlock(&rf_events_mutex);
    fprintf(out,"],\"mic_health\":%u,\"mic_health_changed_ms\":%llu,\"mic_recovery_epoch\":%llu,\"mic_recovery_started_ms\":%llu,\"mic_recovery_attempts\":%u,\"mic_retry_available\":%s,\"headset_volume_percent\":%u,\"headset_muted\":%s,\"headset_volume_ready\":%s,\"headset_volume_pending\":%s,\"headset_volume_initialized\":%s,\"headset_volume_requested_percent\":%u,\"headset_volume_requested_revision\":%llu,\"headset_volume_confirmed_revision\":%llu,\"headset_volume_failed_revision\":%llu,\"cue_available\":%s,\"cue_active\":%s,\"cue_requests\":%llu,\"cue_completed\":%llu,\"cue_canceled\":%llu,\"cue_failures\":%llu,",
        s.mic_health,(unsigned long long)s.mic_health_changed_ms,(unsigned long long)s.mic_recovery_epoch,(unsigned long long)s.mic_recovery_started_ms,s.mic_recovery_attempts,s.mic_retry_available?"true":"false",
        s.headset_volume_percent,s.headset_muted?"true":"false",s.headset_volume_ready?"true":"false",s.headset_volume_pending?"true":"false",s.headset_volume_initialized?"true":"false",
        s.headset_volume_requested_percent,(unsigned long long)s.headset_volume_requested_revision,(unsigned long long)s.headset_volume_confirmed_revision,(unsigned long long)s.headset_volume_failed_revision,
        s.cue_available?"true":"false",s.cue_active?"true":"false",(unsigned long long)s.cue_requests,(unsigned long long)s.cue_completed,(unsigned long long)s.cue_canceled,(unsigned long long)s.cue_failures);
    fprintf(out,"\"headset_uncorks\":%u,\"headset_payload_writes\":%u,\"cue_connect_failures\":%u,\"cue_press_hits\":%u,",
        usb_test_headset_uncorks?usb_test_headset_uncorks():0,usb_test_headset_payload_writes?usb_test_headset_payload_writes():0,usb_test_cue_connect_failures?usb_test_cue_connect_failures():0,atomic_load(&cue_press_hits));
    fprintf(out,"\"mic_near_maximum\":%s,\"mic_overload\":%s,\"radio_recovering\":%s,\"radio_recovery_started_ms\":%llu,\"radio_recovery_attempts\":%u,\"headset_volume_recovering\":%s,\"headset_volume_unavailable\":%s,\"headset_volume_retries\":%u,\"cue_recovering\":%s,\"cue_unavailable\":%s,\"cue_retries\":%u,\"volume_calls\":%u,\"volume_failures\":%u,\"cue_injected_failures\":%u,\"cue_io_pauses\":%u,\"cue_io_pause_ms\":%llu,",
        s.mic_near_maximum?"true":"false",s.mic_overload?"true":"false",s.radio_recovering?"true":"false",(unsigned long long)s.radio_recovery_started_ms,s.radio_recovery_attempts,
        s.headset_volume_recovering?"true":"false",s.headset_volume_unavailable?"true":"false",s.headset_volume_retries,s.cue_recovering?"true":"false",s.cue_unavailable?"true":"false",s.cue_retries,
        usb_test_volume_calls?usb_test_volume_calls():0,usb_test_volume_failures?usb_test_volume_failures():0,usb_test_cue_failures?usb_test_cue_failures():0,usb_test_cue_pauses?usb_test_cue_pauses():0,usb_test_cue_pause_duration_ms?usb_test_cue_pause_duration_ms():0);
    fprintf(out,"\"io_service_pause\":{\"count\":%u,\"started_ms\":%llu,\"duration_ms\":%llu},\"render_queue\":{\"limit_frames\":%u,\"output_bound_frames\":%u,\"high_water_frames\":%u,\"deferred_calls\":%u,\"final_deferred_calls\":%u,\"final_flush_calls\":%u,\"final_flush_frames\":%llu,\"generated_frames\":%llu,\"written_frames\":%llu},\"render_hold\":{\"started_ms\":%llu,\"ended_ms\":%llu,\"hits\":%u}}\n",
        usb_test_io_pause_count?usb_test_io_pause_count():0,usb_test_io_pause_started_ms?usb_test_io_pause_started_ms():0,usb_test_io_pause_duration_ms?usb_test_io_pause_duration_ms():0,
        FD_USB_TX_QUEUE_FRAMES,FD_USB_RENDER_OUTPUT_FRAMES,s.render_queue_high_water,s.render_deferred_count,s.render_final_deferred_count,s.render_final_flush_calls,(unsigned long long)s.transport_tail_frames,(unsigned long long)(s.rendered_modem+s.transport_tail_frames),(unsigned long long)s.modem_written_frames,
        usb_test_render_hold_started_ms?usb_test_render_hold_started_ms():0,usb_test_render_hold_ended_ms?usb_test_render_hold_ended_ms():0,usb_test_render_hold_hits?usb_test_render_hold_hits():0);fclose(out);stop_telemetry(f);
}
/* Hardening observations are distinct from verdicts: independent Pulse and
 * monitor readers bind these phase boundaries to the actual device output. */
static fd_usb_snapshot hardening_phase(fd_usb_audio *audio,const char *name){
    fd_usb_snapshot s=fd_usb_audio_snapshot(audio);char path[PATH_MAX];
    int n=snprintf(path,sizeof(path),"%s/hardening-phases.jsonl",run_directory);assert(n>0&&(size_t)n<sizeof(path));
    FILE *out=fopen(path,"a");assert(out);
    fprintf(out,"{\"phase\":\"%s\",\"ms\":%llu,\"state\":%u,\"fault\":%u,\"armed\":%s,\"pressed\":%s,\"rf_valid\":%s,\"rf_tx\":%s,\"physical_release_sequence\":%llu,\"keys\":%u,\"clears\":%u,\"submitted_mic\":%llu,\"turns\":%llu,\"headset_volume_percent\":%u,\"headset_volume_requested_percent\":%u,\"headset_muted\":%s,\"headset_volume_ready\":%s,\"headset_volume_pending\":%s,\"headset_volume_initialized\":%s,\"headset_volume_requested_revision\":%llu,\"headset_volume_confirmed_revision\":%llu,\"headset_volume_failed_revision\":%llu,\"headset_volume_recovering\":%s,\"headset_volume_unavailable\":%s,\"headset_volume_retries\":%u,\"cue_available\":%s,\"cue_active\":%s,\"cue_recovering\":%s,\"cue_unavailable\":%s,\"cue_retries\":%u,\"cue_requests\":%llu,\"cue_completed\":%llu,\"cue_canceled\":%llu,\"cue_failures\":%llu,\"radio_recovering\":%s,\"radio_recovery_started_ms\":%llu,\"radio_recovery_attempts\":%u,\"mic_meter_ready\":%s,\"mic_near_maximum\":%s,\"mic_overload\":%s,\"mic_peak_dbfs10\":%d,\"mic_clipped_samples\":%llu,\"rade_creations\":%u,\"encoder_creations\":%u,\"decoder_creations\":%u,\"volume_calls\":%u,\"volume_failures\":%u,\"cue_injected_failures\":%u,\"cue_io_pauses\":%u,\"cue_io_pause_ms\":%llu}\n",
        name,(unsigned long long)ms(),s.state,s.fault,s.armed?"true":"false",s.pressed?"true":"false",s.rf_valid?"true":"false",s.rf_tx?"true":"false",
        (unsigned long long)atomic_load(&run_control->release),atomic_load(&run_control->keys),atomic_load(&run_control->clears),(unsigned long long)s.submitted_mic,(unsigned long long)s.turns,
        s.headset_volume_percent,s.headset_volume_requested_percent,s.headset_muted?"true":"false",s.headset_volume_ready?"true":"false",s.headset_volume_pending?"true":"false",s.headset_volume_initialized?"true":"false",
        (unsigned long long)s.headset_volume_requested_revision,(unsigned long long)s.headset_volume_confirmed_revision,(unsigned long long)s.headset_volume_failed_revision,
        s.headset_volume_recovering?"true":"false",s.headset_volume_unavailable?"true":"false",s.headset_volume_retries,
        s.cue_available?"true":"false",s.cue_active?"true":"false",s.cue_recovering?"true":"false",s.cue_unavailable?"true":"false",s.cue_retries,
        (unsigned long long)s.cue_requests,(unsigned long long)s.cue_completed,(unsigned long long)s.cue_canceled,(unsigned long long)s.cue_failures,
        s.radio_recovering?"true":"false",(unsigned long long)s.radio_recovery_started_ms,s.radio_recovery_attempts,
        s.mic_meter_ready?"true":"false",s.mic_near_maximum?"true":"false",s.mic_overload?"true":"false",s.mic_peak_dbfs10,(unsigned long long)s.mic_clipped_samples,
        s.engine.rade_creations,s.engine.encoder_creations,s.engine.decoder_creations,
        usb_test_volume_calls?usb_test_volume_calls():0,usb_test_volume_failures?usb_test_volume_failures():0,
        usb_test_cue_failures?usb_test_cue_failures():0,usb_test_cue_pauses?usb_test_cue_pauses():0,usb_test_cue_pause_duration_ms?usb_test_cue_pause_duration_ms():0);
    assert(!fclose(out));return s;
}
static fd_usb_snapshot hardening_volume(fd_usb_audio *audio,unsigned target,unsigned actual,unsigned timeout){
    uint64_t until=ms()+timeout;fd_usb_snapshot s={0};
    do{s=healthy_snapshot(audio,"hardening-volume-fault");
        if(s.headset_volume_ready&&!s.headset_volume_pending&&!s.headset_volume_unavailable&&
           s.headset_volume_requested_percent==target&&s.headset_volume_percent==actual&&
           s.headset_volume_requested_revision==s.headset_volume_confirmed_revision)return s;
        pause_ms(5);
    }while(ms()<until);failed_wait(audio,"hardening-volume-timeout",s);
}
static fd_usb_snapshot hardening_flag(fd_usb_audio *audio,unsigned wanted,unsigned timeout){
    uint64_t until=ms()+timeout;fd_usb_snapshot s={0};
    do{s=healthy_snapshot(audio,"hardening-health-fault");bool found=
        wanted==1?s.headset_volume_recovering:wanted==2?s.headset_volume_unavailable:
        wanted==3?s.cue_recovering:wanted==4?s.cue_unavailable:wanted==5?s.radio_recovering&&!s.armed&&!s.turns&&!s.submitted_mic:
        wanted==6?s.cue_active:wanted==7?s.mic_near_maximum&&!s.mic_overload:
        wanted==8?s.mic_overload:wanted==9?s.mic_meter_ready&&!s.mic_near_maximum&&!s.mic_overload:
        wanted==11?s.state==FD_USB_RX&&!s.armed&&!s.radio_recovering&&s.pressed&&s.rf_valid&&!s.rf_tx&&s.configuration_ready&&s.rf_clear_confirmed:
        s.state==FD_USB_RX&&s.armed&&!s.radio_recovering&&s.rf_valid&&!s.rf_tx&&s.configuration_ready&&s.rf_clear_confirmed;
        if(found)return s;pause_ms(2);
    }while(ms()<until);failed_wait(audio,"hardening-health-timeout",s);
}
static fd_usb_snapshot hardening_cue_completed(fd_usb_audio *audio,uint64_t before){
    uint64_t until=ms()+6000;fd_usb_snapshot s={0};
    do{s=healthy_snapshot(audio,"hardening-cue-fault");if(s.cue_completed>before&&!s.cue_active)return s;pause_ms(5);}while(ms()<until);
    failed_wait(audio,"hardening-cue-completion-timeout",s);
}
static fd_usb_snapshot hardening_fault(fd_usb_audio *audio,unsigned expected,const char *origin,unsigned timeout){
    uint64_t until=ms()+timeout;fd_usb_snapshot s={0};
    do{s=fd_usb_audio_snapshot(audio);
        /* Fault is an immediate atomic gate; its immutable origin is published
         * separately. Wait for both observations rather than asserting an old
         * cached snapshot at the admission boundary. */
        if(s.fault&&s.first_fault_code==s.fault&&s.first_fault_origin[0]){
            assert(s.fault==expected&&!strcmp(s.first_fault_origin,origin));return s;
        }pause_ms(5);
    }while(ms()<until);
    failed_wait(audio,"hardening-expected-fault-timeout",s);
}
static void hardening_freeze(fake *f,unsigned duration){
    pthread_mutex_lock(&f->packet_mutex);atomic_store(&f->freeze,true);atomic_store(&f->freeze_until,duration?ms()+duration:0);pthread_mutex_unlock(&f->packet_mutex);
}
static void hardening_cases(fd_usb_audio *audio,fake *f,fd_usb_snapshot ready,const char *scenario,const char *directory){
    const char *name=scenario+24; /* --operational-hardening- */
    assert(!strncmp(scenario,"--operational-hardening-",24));
    assert(ready.headset_volume_initialized&&ready.headset_volume_percent==37&&!ready.headset_muted);
    hardening_phase(audio,"initial");bool finish_turn=true;unsigned expected_fault=0;
    if(!strcmp(name,"volume-paced-rounded")){
        assert(usb_test_volume_mode);usb_test_volume_mode(4);
        assert(fd_usb_audio_set_headset_volume(audio,0,false));hardening_volume(audio,0,0,4000);hardening_phase(audio,"low0");
        const unsigned targets[]={1,5,9,10,15,100};const char *labels[]={"up1","up5","up9","up10","up15","up100"};unsigned desired=0;
        for(unsigned point=0;point<6;++point){
            while(desired<targets[point]){assert(fd_usb_audio_adjust_headset_volume(audio,1));++desired;pause_ms(desired<16?70:4);}
            hardening_volume(audio,desired,((desired+2)/5)*5,4000);fd_usb_snapshot observed=hardening_phase(audio,labels[point]);
            controls_phase(audio,f,directory,labels[point],observed);
        }
        while(desired>9){assert(fd_usb_audio_adjust_headset_volume(audio,-1));--desired;pause_ms(desired<16?70:4);}
        hardening_volume(audio,9,10,4000);hardening_phase(audio,"down9");
        while(desired<15){assert(fd_usb_audio_adjust_headset_volume(audio,1));++desired;pause_ms(70);}
        hardening_volume(audio,15,15,4000);hardening_phase(audio,"final15");usb_test_volume_mode(0);
    }else if(!strcmp(name,"volume-retry")){
        assert(usb_test_volume_mode);usb_test_volume_mode(5);assert(fd_usb_audio_set_headset_volume(audio,51,false));
        hardening_flag(audio,1,2000);hardening_phase(audio,"recovering");
        for(unsigned i=0;i<4;++i){assert(fd_usb_audio_adjust_headset_volume(audio,1));pause_ms(35);}
        hardening_volume(audio,55,55,5000);hardening_phase(audio,"rejected-recovered");
        usb_test_volume_mode(6);assert(fd_usb_audio_set_headset_volume(audio,54,false));hardening_flag(audio,1,2200);
        assert(fd_usb_audio_adjust_headset_volume(audio,1));hardening_volume(audio,55,55,5000);fd_usb_snapshot observed=hardening_phase(audio,"final55");controls_phase(audio,f,directory,"final55",observed);usb_test_volume_mode(0);
    }else if(!strcmp(name,"volume-unavailable-retry")){
        assert(usb_test_volume_mode);usb_test_volume_mode(2);assert(fd_usb_audio_set_headset_volume(audio,55,false));
        fd_usb_snapshot unavailable=hardening_flag(audio,2,6000);assert(!unavailable.headset_volume_ready&&unavailable.headset_volume_retries==2);
        hardening_phase(audio,"volume-unavailable");
        usb_test_volume_mode(0);assert(fd_usb_audio_retry_controls(audio));fd_usb_snapshot retried=hardening_volume(audio,55,55,5000);assert(retried.headset_volume_confirmed_revision>unavailable.headset_volume_failed_revision);fd_usb_snapshot observed=hardening_phase(audio,"final55");controls_phase(audio,f,directory,"final55",observed);
    }else if(!strcmp(name,"volume-external-zero")){
        puts("HARDENING_EXTERNAL_VOLUME");fflush(stdout);hardening_volume(audio,51,51,5000);fd_usb_snapshot external=hardening_phase(audio,"external51");
        controls_phase(audio,f,directory,"external51",external);
        assert(fd_usb_audio_set_headset_volume(audio,0,false));hardening_volume(audio,0,0,4000);hardening_phase(audio,"zero");
        assert(fd_usb_audio_adjust_headset_volume(audio,30));hardening_volume(audio,30,30,4000);fd_usb_snapshot restored=hardening_phase(audio,"restored30");
        controls_phase(audio,f,directory,"restored30",restored);
    }else if(!strncmp(name,"cue-",4)){
        assert(usb_test_cue_mode);unsigned earlier=!strcmp(name,"cue-final-gesture")?50:41;
        hardening_phase(audio,"earlier-gesture-start");assert(fd_usb_audio_set_headset_volume(audio,earlier,false));hardening_volume(audio,earlier,earlier,4000);
        hardening_cue_completed(audio,ready.cue_completed);fd_usb_snapshot earlier_preview=hardening_phase(audio,"earlier-preview");
        if(!strcmp(name,"cue-final-gesture"))controls_phase(audio,f,directory,"earlier-preview",earlier_preview);
        pause_ms(400);
        if(!strcmp(name,"cue-recovery"))usb_test_cue_mode(1);
        else if(!strcmp(name,"cue-unavailable-retry"))usb_test_cue_mode(2);
        else usb_test_cue_mode(3);
        hardening_phase(audio,"gesture-start");
        assert(fd_usb_audio_set_headset_volume(audio,70,false));hardening_volume(audio,70,70,4000);
        if(strcmp(name,"cue-final-gesture")){
            if(!strcmp(name,"cue-unavailable-retry")){
                fd_usb_snapshot unavailable=hardening_flag(audio,4,6000);assert(unavailable.cue_retries==2);hardening_phase(audio,"cue-unavailable");
                usb_test_cue_mode(0);assert(fd_usb_audio_retry_controls(audio));
            }else{
                hardening_flag(audio,3,2500);hardening_phase(audio,"cue-recovering");
                uint64_t completed=fd_usb_audio_snapshot(audio).cue_completed;
                atomic_store(&f->configuration_ready,false);atomic_store(&f->tuning_revision,2);
                wait_state(audio,FD_USB_TUNING,2500);pause_ms(700);atomic_store(&f->configuration_ready,true);
                hardening_flag(audio,10,4000);pause_ms(750);
                fd_usb_snapshot canceled=hardening_phase(audio,"backoff-canceled");
                assert(!canceled.cue_active&&canceled.cue_completed==completed&&!atomic_load(&f->keys));
            }
        }else{
            fd_usb_snapshot playing=hardening_flag(audio,6,2500);pause_ms(75);
            assert(fd_usb_audio_adjust_headset_volume(audio,1));pause_ms(35);assert(fd_usb_audio_adjust_headset_volume(audio,-1));pause_ms(25);
            fd_usb_snapshot edited=hardening_phase(audio,"active-preview-edit");
            assert(edited.cue_active&&edited.cue_canceled==playing.cue_canceled&&!atomic_load(&f->keys));
        }
        /* The measured final gesture starts after all earlier playback has
         * drained. Its oracle cannot borrow the earlier successful cue. */
        pause_ms(1600);
        fd_usb_snapshot before=healthy_snapshot(audio,"hardening-final-gesture-fault");
        assert(!before.cue_active&&!before.cue_recovering&&!before.cue_unavailable);
        hardening_phase(audio,"final-gesture-start");assert(fd_usb_audio_set_headset_volume(audio,100,false));hardening_volume(audio,100,100,4000);
        hardening_cue_completed(audio,before.cue_completed);pause_ms(200);hardening_phase(audio,"final-preview");
        fd_usb_snapshot final=healthy_snapshot(audio,"hardening-final-preview-fault");assert(!final.submitted_mic&&!atomic_load(&f->keys)&&final.cue_available&&!final.cue_unavailable);
        if(!strcmp(name,"cue-final-gesture")){
            assert(usb_test_cue_pauses&&usb_test_cue_pauses()==1&&usb_test_cue_pause_duration_ms&&usb_test_cue_pause_duration_ms()>=50&&usb_test_cue_pause_duration_ms()<=150);
            puts("RX_FIXTURE_READY");fflush(stdout);uint64_t decode_until=ms()+12000;
            while(healthy_snapshot(audio,"hardening-cue-rx-fault").engine.synthesis_blocks<4&&ms()<decode_until)pause_ms(10);
            fd_usb_snapshot speaking=healthy_snapshot(audio,"hardening-cue-rx-fault");assert(speaking.engine.synthesis_blocks>=4);
            hardening_phase(audio,"decoded-before-edit");uint64_t completed=speaking.cue_completed;
            assert(fd_usb_audio_set_headset_volume(audio,99,false));hardening_volume(audio,99,99,4000);
            assert(fd_usb_audio_set_headset_volume(audio,100,false));hardening_volume(audio,100,100,4000);
            fd_usb_snapshot during=hardening_phase(audio,"decoded-after-edit");assert(!during.cue_active&&during.cue_completed==completed);
            puts("RX_SOURCE_TO_SILENCE");fflush(stdout);char ack[PATH_MAX];snprintf(ack,sizeof(ack),"%s/rx-source-silenced",directory);
            unsigned long fixture_ms=strtoul(getenv("USB_TEST_RX_FIXTURE_MS"),NULL,10);assert(fixture_ms&&fixture_ms<=30000);
            decode_until=ms()+fixture_ms+5000;while(access(ack,F_OK)&&ms()<decode_until){(void)healthy_snapshot(audio,"hardening-cue-rx-source-fault");pause_ms(5);}assert(!access(ack,F_OK));
            pause_ms(2000);uint64_t synthesis=fd_usb_audio_snapshot(audio).engine.synthesis_blocks;pause_ms(500);
            fd_usb_snapshot settled=healthy_snapshot(audio,"hardening-cue-rx-settle-fault");assert(!settled.output_queue&&!settled.engine.output_depth&&settled.engine.synthesis_blocks==synthesis);
            assert(settled.cue_completed==completed);hardening_phase(audio,"decoded-settled");
        }
        atomic_store(&cue_press_armed,true);assert(fd_usb_audio_set_headset_volume(audio,99,false));
        uint64_t until=ms()+3000;while(!atomic_load(&cue_press_hits)&&ms()<until){(void)healthy_snapshot(audio,"hardening-cue-ptt-fault");pause_ms(2);}assert(atomic_load(&cue_press_hits)==1);
    }else if(!strcmp(name,"mic-overload")){
        finish_turn=false;assert(fd_usb_audio_set_mic_gain(audio,12));hardening_flag(audio,7,1500);hardening_phase(audio,"near-maximum");
        assert(fd_usb_audio_set_mic_gain(audio,24));hardening_flag(audio,8,1500);hardening_phase(audio,"overload");
        assert(!fd_usb_audio_snapshot(audio).mic_clipped_samples&&!atomic_load(&f->keys));
        assert(fd_usb_audio_set_mic_gain(audio,0));hardening_flag(audio,9,1500);hardening_phase(audio,"clean");
    }else if(!strcmp(name,"radio-loss-tx")){
        atomic_store(&f->pressed,true);wait_state(audio,FD_USB_TX,10000);puts("TX_ACTIVE");fflush(stdout);pause_ms(300);hardening_freeze(f,0);
        hardening_fault(audio,4,"radio-packet-stale",2500);expected_fault=4;hardening_phase(audio,"tx-status-lost");
    }else{
        bool concurrent=!strcmp(name,"radio-recover");uint64_t previews_before=ready.cue_completed;
        if(concurrent){
            assert(fd_usb_audio_set_headset_volume(audio,39,false));hardening_volume(audio,39,39,4000);
            hardening_flag(audio,6,2500);fd_usb_snapshot preview=hardening_phase(audio,"preview-before-status");
            assert(preview.cue_active&&!preview.submitted_mic&&!atomic_load(&f->keys));previews_before=preview.cue_completed;
        }
        bool blocked=!strcmp(name,"radio-recover-clear-blocked");if(blocked)atomic_store(&f->block_clear,2500);
        bool operation_deadline=!strcmp(name,"radio-recover-operation-deadline");if(operation_deadline){atomic_store(&f->block_clear,1400);marker("USB_TEST_PA_TRIGGER");}
        hardening_freeze(f,!strcmp(name,"radio-recover-timeout")||blocked?0:650);
        hardening_flag(audio,5,1500);fd_usb_snapshot recovering=hardening_phase(audio,"radio-recovering");
        assert(!recovering.armed&&!atomic_load(&f->permitted)&&!atomic_load(&f->keys)&&!recovering.submitted_mic);
        if(concurrent){
            for(unsigned detent=0;detent<6;++detent){assert(fd_usb_audio_adjust_headset_volume(audio,1));pause_ms(20);}
            hardening_volume(audio,45,45,1500);fd_usb_snapshot controls=hardening_phase(audio,"controls-during-recovery");
            assert(controls.radio_recovering&&!controls.armed&&!controls.cue_active&&!atomic_load(&f->keys)&&!controls.submitted_mic);
        }
        if(!strcmp(name,"radio-recover-held"))atomic_store(&f->pressed,true);
        if(!strcmp(name,"radio-recover-unexpected-tx")){
            set_physical_tx(f,true);atomic_store(&f->freeze,false);atomic_store(&f->freeze_until,0);emit_packet(f);
            hardening_fault(audio,4,"unexpected-tx-during-radio-recovery",2500);expected_fault=4;
        }else if(!strcmp(name,"radio-recover-protection")){
            atomic_store(&f->protection,true);hardening_fault(audio,5,"radio-protection",2500);expected_fault=5;
        }else if(!strcmp(name,"radio-recover-timeout")){
            hardening_fault(audio,4,"radio-status-recovery-timeout",3000);expected_fault=4;
        }else if(operation_deadline){
            hardening_fault(audio,4,"radio-status-recovery-timeout",3000);atomic_store(&f->block_clear,0);expected_fault=4;
        }else if(blocked){
            hardening_fault(audio,20,"radio-recovery-clear-timeout",3000);atomic_store(&f->block_clear,0);expected_fault=20;
        }else if(!strcmp(name,"radio-recover-held")){
            hardening_flag(audio,11,3000);fd_usb_snapshot held=hardening_phase(audio,"held-restored");assert(!held.armed&&!atomic_load(&f->keys));
            release_ptt(f);hardening_flag(audio,10,2500);hardening_phase(audio,"radio-restored");
        }else{
            hardening_flag(audio,10,3000);hardening_phase(audio,"radio-restored");
            if(concurrent){pause_ms(750);fd_usb_snapshot quiet=hardening_phase(audio,"canceled-preview-settled");
                assert(!quiet.cue_active&&quiet.cue_completed==previews_before&&quiet.headset_volume_percent==45&&!atomic_load(&f->keys));}
            if(!strcmp(name,"radio-recover-flapping")){
                hardening_phase(audio,"first-restored");hardening_freeze(f,0);
                hardening_fault(audio,4,"radio-status-recovery-repeated-loss",2000);expected_fault=4;hardening_phase(audio,"repeated-loss");
            }
        }
    }
    if(expected_fault){
        hardening_phase(audio,"fault");
        fd_usb_snapshot last=wait_stopped(audio,7000);assert(last.fault==expected_fault&&!atomic_load(&f->physical_tx));
        assert(last.engine.rade_creations==ready.engine.rade_creations&&last.engine.encoder_creations==ready.engine.encoder_creations&&last.engine.decoder_creations==ready.engine.decoder_creations);
        atomic_store(&f->freeze,false);atomic_store(&f->freeze_until,0);atomic_store(&f->protection,false);emit_packet(f);
        hardening_phase(audio,"stopped");result(directory,scenario,f,last);fd_usb_audio_close(audio);return;
    }
    if(finish_turn){
        if(!atomic_load(&f->pressed))atomic_store(&f->pressed,true);wait_state(audio,FD_USB_TX,10000);puts("TX_ACTIVE");fflush(stdout);pause_ms(1700);release_ptt(f);
        fd_usb_snapshot returned=wait_completed_turn(audio,1,1,10000);assert(returned.turns==1&&returned.engine.eoo_count==1);hardening_phase(audio,"after-turn");
    }
    fd_usb_audio_leave(audio);fd_usb_snapshot last=wait_stopped(audio,10000);
    assert(!last.fault&&last.rf_clear_confirmed&&last.engine.rade_creations==ready.engine.rade_creations&&last.engine.encoder_creations==ready.engine.encoder_creations&&last.engine.decoder_creations==ready.engine.decoder_creations);
    result(directory,scenario,f,last);fd_usb_audio_close(audio);
}

int main(int argc,char **argv) {
    if(argc==2&&!strcmp(argv[1],"--release-lock-protocol-selftest"))return release_lock_protocol_selftest();
    assert(argc==2 || argc==3);const char *scenario=argc==3?argv[2]:"--normal";
    fake f={0};atomic_store(&f.permitted,true);atomic_store(&f.tx_eligible,true);atomic_store(&f.configuration_ready,true);atomic_store(&f.tuning_revision,1);run_directory=argv[1];run_scenario=scenario;run_control=&f;start_telemetry(&f);char prefix[512];
    snprintf(prefix,sizeof(prefix),"%s/%s",argv[1],!strcmp(scenario,"--reopen")?"freedv-usb-session-00":"freedv-usb-session");
    /* Fast observer calls must not manufacture arrival or freshness. */
    atomic_store(&f.freeze,true);pause_ms(30);uint64_t seq=atomic_load(&f.sequence),packet_at=atomic_load(&f.packet_at);fd_usb_inputs probe;
    for(unsigned i=0;i<200;++i)assert(observe(&f,&probe));
    assert(atomic_load(&f.sequence)==seq&&atomic_load(&f.packet_at)==packet_at);atomic_store(&f.freeze,false);
    /* The driver runs us inside the evidence directory with argv[1]="." so
     * trace filenames stay short. Persistence requires an absolute directory.
     * Preserve direct absolute-output invocations for diagnostic use too. */
    char incident_dir[PATH_MAX],working_directory[PATH_MAX];
    assert(getcwd(working_directory,sizeof(working_directory)));
    int incident_length=argv[1][0]=='/'?snprintf(incident_dir,sizeof(incident_dir),"%s/incidents",argv[1]):
        !strcmp(argv[1],".")?snprintf(incident_dir,sizeof(incident_dir),"%s/incidents",working_directory):
        snprintf(incident_dir,sizeof(incident_dir),"%s/%s/incidents",working_directory,argv[1]);
    assert(incident_length>0&&(size_t)incident_length<sizeof(incident_dir));
    bool operational=!strncmp(scenario,"--operational-",14);
    bool refine_mic=!strncmp(scenario,"--operational-mic-recover",25);
    bool refine_cue=!strncmp(scenario,"--operational-cue-",18);
    bool hardening=!strncmp(scenario,"--operational-hardening-",24);
    bool refinement=hardening||refine_mic||refine_cue||!strcmp(scenario,"--operational-volume-readback")||!strcmp(scenario,"--operational-volume-startup-fails");
    key_retune_case=!strcmp(scenario,"--operational-key-retune");
    open_prefix_case=!strcmp(scenario,"--operational-open-prefix");
    if(!strcmp(scenario,"--operational-held-ptt"))atomic_store(&f.pressed,true);
    if(!strcmp(scenario,"--operational-receive-only"))atomic_store(&f.tx_eligible,false);
    bool full_session=!strcmp(scenario,"--full-session");
    bool qualification=full_session||!strcmp(scenario,"--rf-disabled") || !strcmp(scenario,"--qualification-physical-tx") ||
                       !strcmp(scenario,"--qualification-held-ptt") || !strcmp(scenario,"--cold-qualification") || !strcmp(scenario,"--cold-idle") || !strncmp(scenario,"--storage-",10) || !strcmp(scenario,"--rx-pause150");
    cold_case=!strncmp(scenario,"--cold-",7)&&strcmp(scenario,"--cold-idle");
    cold_rx_case=!strcmp(scenario,"--cold-rx-resume");
    cold_late_tx_case=!strcmp(scenario,"--cold-prekey-unexpected-tx");
    fd_usb_config config={.source="alsa_input.usb-fixture",.sink="alsa_output.usb-fixture",
        .radio_source="radio_source_fixture",.radio_sink="radio_sink_fixture",.trace_prefix=prefix,
        .control={.context=&f,.observe=observe,.key=key,.clear=clear,.inhibit=inhibit,.permit=operational?permit:NULL},
        .operational=operational,.tx_timeout_seconds=operational?180:0,
        .refinement=refinement,.hardening=hardening,.cues_enabled=refine_cue||(hardening&&strstr(scenario,"-cue-"))||!strcmp(scenario,"--operational-hardening-radio-recover"),
        .headset_volume_initial_valid=refinement,.headset_volume_initial_percent=37,.headset_volume_initial_muted=false,
        .rf_disabled=qualification,.qualification_turns=3,.qualification_control_delay_ms=450,
        .qualification_idle_ms=!strcmp(scenario,"--cold-idle")?20000:0,.incident_dir=incident_dir,
        .trace_pcm_seconds=!strcmp(scenario,"--many-turns")?120:!strcmp(scenario,"--operational-timeout")?60:30};
    if(!strcmp(scenario,"--storage-start-fails")){assert(!mkdir(incident_dir,0500));}
    fd_usb_audio *audio=fd_usb_audio_open(&config);assert(audio);
    if(!strcmp(scenario,"--operational-volume-startup-fails")){
        fd_usb_snapshot last=wait_stopped(audio,7000);
        assert(last.fault==13&&!strcmp(last.first_fault_origin,"headset-initial-volume-not-confirmed")&&!last.startup_ready&&!last.turns&&!last.submitted_mic&&!atomic_load(&f.keys)&&!atomic_load(&f.physical_tx)&&last.rf_clear_confirmed);
        assert(usb_test_headset_uncorks&&usb_test_headset_payload_writes&&!usb_test_headset_uncorks()&&!usb_test_headset_payload_writes());
        result(argv[1],scenario,&f,last);fd_usb_audio_close(audio);return 0;
    }
    if(!strcmp(scenario,"--buffer-contract-reject")){
        fd_usb_snapshot last=wait_stopped(audio,10000);assert(last.fault==13&&!strcmp(last.first_fault_origin,"modem-buffer-budget"));
        assert(!atomic_load(&f.keys)&&!atomic_load(&f.physical_tx));result(argv[1],scenario,&f,last);fd_usb_audio_close(audio);return 0;
    }
    if(!strcmp(scenario,"--storage-start-fails")){
        fd_usb_snapshot last=wait_stopped(audio,10000);assert(last.fault==18&&!atomic_load(&f.keys)&&!last.qualification_complete);
        assert(!chmod(incident_dir,0700));result(argv[1],scenario,&f,last);fd_usb_audio_close(audio);return 0;
    }
    if(!strcmp(scenario,"--initial-pa-reject")||!strcmp(scenario,"--initial-pa-missing")){
        fd_usb_snapshot last=wait_stopped(audio,10000);assert(last.fault==(!strcmp(scenario,"--initial-pa-reject")?15u:14u));
        assert(!atomic_load(&f.keys)&&!atomic_load(&f.physical_tx));result(argv[1],scenario,&f,last);fd_usb_audio_close(audio);return 0;
    }
    wait_state(audio,!strcmp(scenario,"--operational-held-ptt")?FD_USB_TUNING:FD_USB_RX,20000);
    if(getenv("USB_TEST_OUTPUT_OBSERVER_BARRIER")){
        puts("FIXTURE_OUTPUT_READY");fflush(stdout);char acknowledged[PATH_MAX];int n=snprintf(acknowledged,sizeof(acknowledged),"%s/output-observer-ready",argv[1]);assert(n>0&&(size_t)n<sizeof(acknowledged));
        uint64_t until=ms()+5000;
        while(access(acknowledged,F_OK)&&ms()<until){(void)healthy_snapshot(audio,"unexpected-output-observer-barrier-fault");pause_ms(5);}
        fd_usb_snapshot ready=healthy_snapshot(audio,"unexpected-output-observer-barrier-fault");
        if(access(acknowledged,F_OK))failed_wait(audio,"output-observer-barrier-timeout",ready);
    }
    pause_ms(350);
    if(operational){
        if(!strcmp(scenario,"--operational-held-ptt")){
            pause_ms(500);fd_usb_snapshot held=healthy_snapshot(audio,"held-startup-fault");
            assert(!held.startup_ready&&!held.armed&&!atomic_load(&f.keys));release_ptt(&f);
        }
        uint64_t ready_until=ms()+20000;fd_usb_snapshot ready;
        do{ready=healthy_snapshot(audio,"operational-startup-fault");if(ready.startup_ready&&(ready.armed||!strcmp(scenario,"--operational-receive-only")))break;pause_ms(5);}while(ms()<ready_until);
        assert(ready.operational&&ready.startup_ready&&(ready.armed||!strcmp(scenario,"--operational-receive-only"))&&ready.configuration_ready&&!ready.qualification_complete&&!ready.qualification_running&&!ready.qualification_turns&&!ready.turns);
        assert(ready.tx_timeout_seconds==180&&!fd_usb_audio_set_tx_timeout(audio,29)&&!fd_usb_audio_set_tx_timeout(audio,601));
        assert(fd_usb_audio_set_tx_timeout(audio,30));assert(fd_usb_audio_set_tx_timeout(audio,600));assert(fd_usb_audio_set_tx_timeout(audio,180));
        if(hardening){hardening_cases(audio,&f,ready,scenario,argv[1]);return 0;}
        if(refine_cue||!strcmp(scenario,"--operational-volume-readback")){
            assert(ready.headset_volume_initialized&&ready.headset_volume_ready&&ready.headset_volume_percent==37&&!ready.headset_muted);
            refinement_phase(audio,"initial");
            if(!strcmp(scenario,"--operational-volume-readback")){
                controls_phase(audio,&f,argv[1],"volume-initial",ready);assert(usb_test_volume_mode);usb_test_volume_mode(1);
                assert(fd_usb_audio_set_headset_volume(audio,48,false));fd_usb_snapshot pending=healthy_snapshot(audio,"pending-volume-fault");
                assert(pending.headset_volume_pending&&pending.headset_volume_percent==37&&pending.headset_volume_requested_percent==48&&pending.headset_volume_requested_revision>pending.headset_volume_confirmed_revision);
                refinement_phase(audio,"volume-pending");controls_phase(audio,&f,argv[1],"volume-pending",pending);
                fd_usb_snapshot confirmed=confirmed_volume(audio,48,false);refinement_phase(audio,"volume-confirmed");controls_phase(audio,&f,argv[1],"volume-confirmed",confirmed);
                usb_test_volume_mode(2);assert(fd_usb_audio_set_headset_volume(audio,52,false));uint64_t until=ms()+3000;fd_usb_snapshot rejected;
                do{rejected=healthy_snapshot(audio,"rejected-volume-fault");if(rejected.headset_volume_failed_revision==rejected.headset_volume_requested_revision)break;pause_ms(5);}while(ms()<until);
                assert(rejected.headset_volume_failed_revision&&rejected.headset_volume_percent==48);
                refinement_phase(audio,"volume-failed");controls_phase(audio,&f,argv[1],"volume-failed",rejected);
                usb_test_volume_mode(0);until=ms()+3000;
                do{rejected=healthy_snapshot(audio,"volume-requery-fault");if(rejected.headset_volume_ready)break;pause_ms(5);}while(ms()<until);
                assert(rejected.headset_volume_ready);assert(fd_usb_audio_set_headset_volume(audio,55,false));confirmed=confirmed_volume(audio,55,false);
                refinement_phase(audio,"volume-recovered");controls_phase(audio,&f,argv[1],"volume-recovered",confirmed);
                assert(!confirmed.cue_requests&&!confirmed.cue_completed&&!confirmed.cue_failures);
            }else{
                assert(fd_usb_audio_set_headset_volume(audio,41,false));confirmed_volume(audio,41,false);
                uint64_t until=ms()+3000;fd_usb_snapshot cue;
                do{cue=healthy_snapshot(audio,"cue-preview-fault");if(cue.cue_completed||cue.cue_failures)break;pause_ms(2);}while(ms()<until);
                if(!strcmp(scenario,"--operational-cue-failure")){
                    assert(cue.cue_failures&&!cue.cue_available&&!cue.cue_active&&usb_test_cue_connect_failures&&usb_test_cue_connect_failures());
                }else assert(cue.cue_available&&cue.cue_completed&&!cue.cue_failures);
                refinement_phase(audio,"cue-preview");assert(!cue.submitted_mic&&!atomic_load(&f.keys));
                if(!strcmp(scenario,"--operational-cue-burst")){
                    for(unsigned i=0;i<32;++i)assert(fd_usb_audio_set_headset_volume(audio,50+i%20,false));
                    assert(fd_usb_audio_set_headset_volume(audio,100,false));confirmed_volume(audio,100,false);pause_ms(150);
                    refinement_phase(audio,"cue-burst");puts("RX_FIXTURE_READY");fflush(stdout);
                    until=ms()+12000;while(healthy_snapshot(audio,"cue-rx-decode-fault").engine.synthesis_blocks<4&&ms()<until)pause_ms(10);
                    fd_usb_snapshot speaking=healthy_snapshot(audio,"cue-rx-decode-fault");assert(speaking.engine.synthesis_blocks>=4);
                    refinement_phase(audio,"cue-decoded-before-edit");
                    uint64_t completed_before=speaking.cue_completed;
                    assert(fd_usb_audio_set_headset_volume(audio,99,false));confirmed_volume(audio,99,false);
                    assert(fd_usb_audio_set_headset_volume(audio,100,false));fd_usb_snapshot speech_edit=confirmed_volume(audio,100,false);
                    assert(!speech_edit.cue_active&&speech_edit.cue_completed==completed_before);refinement_phase(audio,"cue-during-decoded-speech");
                    puts("RX_SOURCE_TO_SILENCE");fflush(stdout);char ack[1024];snprintf(ack,sizeof(ack),"%s/rx-source-silenced",argv[1]);
                    const char *fixture=getenv("USB_TEST_RX_FIXTURE_MS");unsigned long fixture_ms=fixture?strtoul(fixture,NULL,10):30000;
                    assert(fixture_ms&&fixture_ms<=30000);until=ms()+fixture_ms+5000;
                    while(access(ack,F_OK)&&ms()<until){(void)healthy_snapshot(audio,"cue-rx-source-fault");pause_ms(5);}assert(!access(ack,F_OK));
                    pause_ms(2000);uint64_t synthesis=healthy_snapshot(audio,"cue-rx-settle-fault").engine.synthesis_blocks;pause_ms(500);
                    fd_usb_snapshot settled=healthy_snapshot(audio,"cue-rx-settle-fault");assert(!settled.output_queue&&!settled.engine.output_depth&&settled.engine.synthesis_blocks==synthesis);
                    atomic_store(&cue_press_armed,true);assert(fd_usb_audio_set_headset_volume(audio,99,false));
                    until=ms()+3000;while(!atomic_load(&cue_press_hits)&&ms()<until){(void)healthy_snapshot(audio,"cue-ptt-hook-fault");pause_ms(2);}assert(atomic_load(&cue_press_hits)==1);
                }
            }
            if(!atomic_load(&f.pressed))atomic_store(&f.pressed,true);
            wait_state(audio,FD_USB_TX,10000);puts("TX_ACTIVE");fflush(stdout);pause_ms(1700);release_ptt(&f);
            fd_usb_snapshot returned=wait_completed_turn(audio,1,1,10000);assert(returned.turns==1&&returned.engine.eoo_count==1);
            refinement_phase(audio,"after-turn");fd_usb_audio_leave(audio);fd_usb_snapshot last=wait_stopped(audio,10000);
            assert(!last.fault&&last.rf_clear_confirmed&&last.engine.rade_creations==ready.engine.rade_creations&&last.engine.encoder_creations==ready.engine.encoder_creations&&last.engine.decoder_creations==ready.engine.decoder_creations);
            result(argv[1],scenario,&f,last);fd_usb_audio_close(audio);return 0;
        }
        if(refine_mic){
            assert(ready.mic_health==FD_USB_MIC_HEALTHY&&ready.headset_volume_initialized&&ready.headset_volume_ready&&ready.headset_volume_percent==37&&!ready.headset_muted);
            refinement_phase(audio,"initial");marker("USB_TEST_STALE_TRIGGER");
            fd_usb_snapshot recovering=wait_mic_health(audio,FD_USB_MIC_RECOVERING,3000);
            assert(!recovering.fault&&!recovering.armed&&!recovering.mic_meter_ready&&!atomic_load(&f.keys)&&recovering.mic_recovery_attempts==1);
            refinement_phase(audio,"recovering");puts("MIC_RECOVERING");fflush(stdout);
            if(!strcmp(scenario,"--operational-mic-recover-stop")){
                fd_usb_audio_stop(audio);fd_usb_snapshot last=wait_stopped(audio,5000);
                assert(last.fault==1&&!last.turns&&!last.submitted_mic&&!atomic_load(&f.keys)&&last.rf_clear_confirmed);
                result(argv[1],scenario,&f,last);fd_usb_audio_close(audio);return 0;
            }
            if(!strcmp(scenario,"--operational-mic-recover-radio-loss")||!strcmp(scenario,"--operational-mic-recover-device-loss")){
                if(!strcmp(scenario,"--operational-mic-recover-radio-loss"))atomic_store(&f.freeze,true);
                fd_usb_snapshot last=wait_stopped(audio,6000);
                assert(last.fault==(!strcmp(scenario,"--operational-mic-recover-radio-loss")?4u:2u)&&!last.turns&&!last.submitted_mic&&!atomic_load(&f.keys)&&!atomic_load(&f.physical_tx));
                if(atomic_load(&f.freeze)){atomic_store(&f.freeze,false);emit_packet(&f);}
                result(argv[1],scenario,&f,last);fd_usb_audio_close(audio);return 0;
            }
            bool held=!strcmp(scenario,"--operational-mic-recover-held")||!strcmp(scenario,"--operational-mic-recover-retune");
            if(held){atomic_store(&f.pressed,true);assert(!fd_usb_audio_retry_microphone(audio));}
            if(!strcmp(scenario,"--operational-mic-recover-retune")){
                atomic_store(&f.configuration_ready,false);atomic_store(&f.tuning_revision,2);
                wait_state(audio,FD_USB_TUNING,3000);fd_usb_snapshot canceled=wait_mic_health(audio,FD_USB_MIC_UNAVAILABLE,1500);
                assert(!canceled.armed&&!atomic_load(&f.keys)&&!strcmp(canceled.mic_health_reason,"tuning-changed"));
                refinement_phase(audio,"retune-canceled");atomic_store(&f.configuration_ready,true);release_ptt(&f);
                wait_state(audio,FD_USB_RX,5000);assert(fd_usb_audio_retry_microphone(audio));
                wait_mic_health(audio,FD_USB_MIC_RECOVERING,1000);held=false;
            }
            if(!strcmp(scenario,"--operational-mic-recover-timeout")){
                fd_usb_snapshot unavailable=wait_mic_health(audio,FD_USB_MIC_UNAVAILABLE,2200);
                assert(unavailable.mic_retry_available&&!unavailable.armed&&!unavailable.mic_meter_ready&&!atomic_load(&f.keys));
                refinement_phase(audio,"unavailable");assert(fd_usb_audio_retry_microphone(audio));
                wait_mic_health(audio,FD_USB_MIC_RECOVERING,1000);wait_mic_health(audio,FD_USB_MIC_UNAVAILABLE,2200);
                refinement_phase(audio,"retry-timeout");assert(remove(getenv("USB_TEST_STALE_TRIGGER"))==0);
                assert(fd_usb_audio_retry_microphone(audio));wait_mic_health(audio,FD_USB_MIC_RECOVERING,1000);
            }
            fd_usb_snapshot recovered=wait_mic_health(audio,FD_USB_MIC_HEALTHY,2200);
            /* HEALTHY proves a fresh post-recovery capture frame. The cleared
             * visual RMS window needs its own subsequent 100ms of samples. */
            uint64_t meter_until=ms()+750;
            while(!recovered.mic_meter_ready&&ms()<meter_until){
                pause_ms(5);recovered=healthy_snapshot(audio,"recovered-mic-meter-fault");
                assert(recovered.mic_health==FD_USB_MIC_HEALTHY&&recovered.rf_valid&&!recovered.rf_tx&&!atomic_load(&f.keys));
            }
            assert(!recovered.fault&&recovered.mic_meter_ready&&recovered.mic_recovery_attempts==(!strcmp(scenario,"--operational-mic-recover-timeout")?3u:!strcmp(scenario,"--operational-mic-recover-retune")?2u:1u));
            refinement_phase(audio,"recovered");
            if(held){pause_ms(200);assert(!atomic_load(&f.keys)&&!healthy_snapshot(audio,"recovery-held-fault").armed);release_ptt(&f);}
            uint64_t until=ms()+3000;fd_usb_snapshot admitted;
            do{admitted=healthy_snapshot(audio,"recovery-admission-fault");if(admitted.armed)break;pause_ms(5);}while(ms()<until);
            assert(admitted.armed&&!atomic_load(&f.keys));atomic_store(&f.pressed,true);wait_state(audio,FD_USB_TX,10000);
            puts("TX_ACTIVE");fflush(stdout);pause_ms(1700);release_ptt(&f);fd_usb_snapshot returned=wait_completed_turn(audio,1,1,10000);
            assert(returned.turns==1&&returned.engine.eoo_count==1);fd_usb_audio_leave(audio);fd_usb_snapshot last=wait_stopped(audio,10000);
            assert(!last.fault&&last.rf_clear_confirmed&&last.engine.rade_creations==ready.engine.rade_creations&&last.engine.encoder_creations==ready.engine.encoder_creations&&last.engine.decoder_creations==ready.engine.decoder_creations);
            result(argv[1],scenario,&f,last);fd_usb_audio_close(audio);return 0;
        }
        if(!strcmp(scenario,"--operational-receive-only")){
            assert(!ready.armed&&ready.rf_disabled&&!ready.tx_eligible);atomic_store(&f.pressed,true);pause_ms(300);assert(!atomic_load(&f.keys));
            release_ptt(&f);atomic_store(&f.configuration_ready,false);atomic_store(&f.tuning_revision,2);wait_state(audio,FD_USB_TUNING,5000);
            atomic_store(&f.tx_eligible,true);atomic_store(&f.configuration_ready,true);wait_state(audio,FD_USB_RX,10000);
            assert(healthy_snapshot(audio,"eligible-retune-fault").armed);
        }
        if(!strcmp(scenario,"--operational-retune")){
            atomic_store(&f.configuration_ready,false);atomic_store(&f.tuning_revision,2);wait_state(audio,FD_USB_TUNING,5000);
            atomic_store(&f.pressed,true);pause_ms(250);assert(!atomic_load(&f.keys)&&!atomic_load(&f.permitted));
            atomic_store(&f.configuration_ready,true);pause_ms(250);assert(healthy_snapshot(audio,"retune-held-fault").state==FD_USB_TUNING&&!atomic_load(&f.keys));
            release_ptt(&f);wait_state(audio,FD_USB_RX,10000);pause_ms(150);fd_usb_snapshot tuned=healthy_snapshot(audio,"retune-ready-fault");
            assert(tuned.tuning_revision==2&&tuned.armed&&tuned.engine.rade_creations==ready.engine.rade_creations);
            puts("RX_FIXTURE_READY");fflush(stdout);uint64_t until=ms()+20000;
            while(healthy_snapshot(audio,"retune-rx-decode-fault").engine.synthesis_blocks<4&&ms()<until)pause_ms(5);
            assert(healthy_snapshot(audio,"retune-rx-decode-fault").engine.synthesis_blocks>=4);
            puts("RX_SOURCE_TO_SILENCE");fflush(stdout);char ack[1024];snprintf(ack,sizeof(ack),"%s/rx-source-silenced",argv[1]);
            const char *fixture_setting=getenv("USB_TEST_RX_FIXTURE_MS");unsigned long fixture_ms=fixture_setting?strtoul(fixture_setting,NULL,10):30000;
            assert(fixture_ms&&fixture_ms<=30000);until=ms()+fixture_ms+5000;
            while(access(ack,F_OK)&&ms()<until){(void)healthy_snapshot(audio,"retune-rx-source-fault");pause_ms(5);}assert(!access(ack,F_OK));
            pause_ms(2000);uint64_t synthesis=healthy_snapshot(audio,"retune-rx-settle-fault").engine.synthesis_blocks;pause_ms(500);
            tuned=healthy_snapshot(audio,"retune-rx-settle-fault");assert(!tuned.output_queue&&!tuned.engine.output_depth&&tuned.engine.synthesis_blocks==synthesis);
        }
        if(!strcmp(scenario,"--operational-ready")||!strcmp(scenario,"--operational-held-ptt")||!strcmp(scenario,"--operational-retune")||!strcmp(scenario,"--operational-receive-only")){
            fd_usb_audio_leave(audio);fd_usb_snapshot last=wait_stopped(audio,10000);
            assert(last.fault==0&&!last.turns&&!atomic_load(&f.keys)&&last.rf_clear_confirmed);
            result(argv[1],scenario,&f,last);fd_usb_audio_close(audio);return 0;
        }
        if(!strcmp(scenario,"--operational-open-release")){
            assert(usb_test_mic_release_action);usb_test_mic_release_action(release_from_callback,&f);atomic_store(&f.pressed,true);uint64_t until=ms()+10000;
            while(!atomic_load(&rx_ready_repress_hits)&&ms()<until){(void)healthy_snapshot(audio,"open-release-ack-fault");pause_ms(5);}assert(atomic_load(&f.release)==1&&atomic_load(&rx_ready_repress_hits)==1&&atomic_load(&rx_ready_repress_up));
            wait_state(audio,FD_USB_TX,10000);puts("TX_ACTIVE");fflush(stdout);pause_ms(1700);release_ptt(&f);
            fd_usb_snapshot returned=wait_completed_turn(audio,1,1,10000);assert(returned.turns==1&&returned.engine.eoo_count==1);
            fd_usb_audio_leave(audio);fd_usb_snapshot last=wait_stopped(audio,10000);assert(!last.fault&&last.rf_clear_confirmed&&atomic_load(&f.keys)==2&&atomic_load(&f.release)==2&&last.engine.rade_creations==ready.engine.rade_creations);
            result(argv[1],scenario,&f,last);fd_usb_audio_close(audio);return 0;
        }
        if(open_prefix_case||!strncmp(scenario,"--operational-open-",19)){
            if(!strcmp(scenario,"--operational-open-stop")){assert(usb_test_mic_stop_callback);usb_test_mic_stop_callback(stop_from_close,audio);}
            atomic_store(&f.pressed,true);
            if(!open_prefix_case){
                fd_usb_snapshot last=wait_stopped(audio,10000);assert(last.fault&&!last.turns&&!last.engine.eoo_count&&!last.submitted_mic&&!atomic_load(&f.physical_tx)&&last.rf_clear_confirmed);
                if(!strcmp(scenario,"--operational-open-stop"))assert(last.fault==1);
                else assert(last.fault>1);
                result(argv[1],scenario,&f,last);fd_usb_audio_close(audio);return 0;
            }
            wait_state(audio,FD_USB_TX,10000);puts("TX_ACTIVE");fflush(stdout);pause_ms(1700);release_ptt(&f);
            fd_usb_snapshot returned=wait_completed_turn(audio,1,1,10000);assert(returned.turns==1&&returned.engine.eoo_count==1);
            assert(usb_test_mic_open_prefix_released_ms()-usb_test_mic_open_prefix_started_ms()>=60&&usb_test_mic_open_prefix_hidden_peeks());
            fd_usb_audio_leave(audio);fd_usb_snapshot last=wait_stopped(audio,10000);assert(!last.fault&&last.rf_clear_confirmed&&last.engine.rade_creations==ready.engine.rade_creations);
            result(argv[1],scenario,&f,last);fd_usb_audio_close(audio);return 0;
        }
        if(key_retune_case){
            atomic_store(&f.pressed,true);uint64_t until=ms()+10000;
            while(!atomic_load(&f.key_denials)&&ms()<until){(void)healthy_snapshot(audio,"key-retune-denial-fault");pause_ms(5);}
            assert(atomic_load(&f.key_denials)==1);fd_usb_snapshot cancelled=wait_state(audio,FD_USB_TUNING,10000);
            assert(!cancelled.fault&&!cancelled.submitted_mic&&!cancelled.turns&&!cancelled.engine.eoo_count&&!atomic_load(&f.keys)&&!atomic_load(&f.physical_tx));
            assert(atomic_load(&f.pressed)&&!atomic_load(&f.release));atomic_store(&f.configuration_ready,true);pause_ms(250);
            assert(healthy_snapshot(audio,"key-retune-held-fault").state==FD_USB_TUNING&&!atomic_load(&f.keys));
            release_ptt(&f);fd_usb_snapshot reacquired=wait_state(audio,FD_USB_RX,10000);
            assert(reacquired.armed&&reacquired.tuning_revision==2&&reacquired.engine.rade_creations==ready.engine.rade_creations);
            atomic_store(&f.pressed,true);wait_state(audio,FD_USB_TX,10000);puts("TX_ACTIVE");fflush(stdout);pause_ms(1700);release_ptt(&f);
            fd_usb_snapshot returned=wait_completed_turn(audio,1,1,10000);assert(returned.turns==1&&returned.engine.eoo_count==1&&atomic_load(&f.keys)==1);
            fd_usb_audio_leave(audio);fd_usb_snapshot last=wait_stopped(audio,10000);
            assert(!last.fault&&last.rf_clear_confirmed&&!atomic_load(&f.physical_tx)&&atomic_load(&f.key_attempts)==2&&atomic_load(&f.key_denials)==1&&atomic_load(&f.release)==2);
            assert(last.engine.rade_creations==ready.engine.rade_creations&&last.engine.encoder_creations==ready.engine.encoder_creations&&last.engine.decoder_creations==ready.engine.decoder_creations);
            result(argv[1],scenario,&f,last);fd_usb_audio_close(audio);return 0;
        }
        if(!strcmp(scenario,"--operational-timeout"))assert(fd_usb_audio_set_tx_timeout(audio,30));
        atomic_store(&f.pressed,true);wait_state(audio,FD_USB_TX,10000);puts("TX_ACTIVE");fflush(stdout);
        if(!strcmp(scenario,"--operational-timeout")){
            uint64_t tx_started=ms();assert(!fd_usb_audio_set_tx_timeout(audio,600));pause_ms(22000);
            fd_usb_snapshot still=healthy_snapshot(audio,"operational-timeout-before30");assert(still.state==FD_USB_TX&&still.tx_timeout_seconds==30&&atomic_load(&f.physical_tx));
            fd_usb_snapshot last=wait_stopped(audio,12000);uint64_t elapsed=ms()-tx_started;
            assert(last.fault==10&&!strcmp(last.first_fault_origin,"turn-deadline")&&elapsed>=29500&&elapsed<=31500);
            assert(!last.turns&&!last.engine.eoo_count&&atomic_load(&f.pressed)&&!atomic_load(&f.release)&&atomic_load(&f.halted));
            assert(!atomic_load(&f.physical_tx)&&last.rf_clear_confirmed&&last.engine.rade_creations==ready.engine.rade_creations);
            result(argv[1],scenario,&f,last);fd_usb_audio_close(audio);return 0;
        }
        pause_ms(1700);
        if(!strcmp(scenario,"--operational-device-loss")){
            fd_usb_snapshot last=wait_stopped(audio,10000);assert(last.fault==2&&!atomic_load(&f.physical_tx)&&last.rf_clear_confirmed);
            result(argv[1],scenario,&f,last);fd_usb_audio_close(audio);return 0;
        }
        uint64_t real_release=atomic_load(&f.release);fd_usb_audio_leave(audio);
        if(!strcmp(scenario,"--operational-abort")){fd_usb_audio_stop(audio);}
        fd_usb_snapshot last=wait_stopped(audio,10000);
        assert(atomic_load(&f.release)==real_release&&atomic_load(&f.pressed)&&!atomic_load(&f.physical_tx)&&last.rf_clear_confirmed);
        if(!strcmp(scenario,"--operational-leave-tx"))assert(last.fault==0&&last.turns==1&&last.engine.eoo_count==1);
        else assert(last.fault==1&&!last.turns);
        assert(last.engine.rade_creations==ready.engine.rade_creations&&last.engine.encoder_creations==ready.engine.encoder_creations&&last.engine.decoder_creations==ready.engine.decoder_creations);
        result(argv[1],scenario,&f,last);fd_usb_audio_close(audio);return 0;
    }
    if(!strcmp(scenario,"--audio-controls")){
        audio_controls(audio,&f,argv[1]);fd_usb_audio_stop(audio);
        fd_usb_snapshot last=wait_stopped(audio,10000);
        assert(last.fault==1&&!atomic_load(&f.keys)&&!last.turns&&!last.submitted_mic&&!last.mic_clipped_samples);
        result(argv[1],scenario,&f,last);fd_usb_audio_close(audio);
        puts("PASS live RX microphone preview and selected headset master controls; no RF admission");return 0;
    }
    if(!strcmp(scenario,"--backend-timing-missing")||!strcmp(scenario,"--backend-timing-invalid")||!strcmp(scenario,"--backend-stop-before-timing")||!strcmp(scenario,"--backend-stop-during-wait")){
        if(!strcmp(scenario,"--backend-stop-before-timing")){assert(usb_test_mic_stop_callback);usb_test_mic_stop_callback(stop_from_close,audio);}
        fd_usb_audio_arm(audio,true);atomic_store(&f.pressed,true);wait_state(audio,FD_USB_TX,10000);puts("TX_ACTIVE");fflush(stdout);pause_ms(1700);release_ptt(&f);
        if(!strcmp(scenario,"--backend-stop-during-wait")){
            assert(usb_test_backend_wait_started_ms&&usb_test_backend_wait_until_us);uint64_t until=ms()+10000;
            while(!usb_test_backend_wait_started_ms()&&ms()<until){(void)healthy_snapshot(audio,"unexpected-backend-wait-fault");pause_ms(1);}
            assert(usb_test_backend_wait_started_ms()&&monotonic_ns()/1000<usb_test_backend_wait_until_us());
            uint64_t before=monotonic_ns();fd_usb_audio_stop(audio);uint64_t after=monotonic_ns();assert(after-before<100000000);
            printf("BACKEND_STOP_DURING_WAIT request_before_ns=%llu request_after_ns=%llu wait_started_ms=%llu wait_until_us=%llu\n",(unsigned long long)before,(unsigned long long)after,usb_test_backend_wait_started_ms(),usb_test_backend_wait_until_us());fflush(stdout);
        }
        fd_usb_snapshot last=wait_stopped(audio,10000);
        if(!strcmp(scenario,"--backend-timing-missing"))assert(last.fault==14&&!strcmp(last.first_fault_origin,"modem-backend-timing"));
        else if(!strcmp(scenario,"--backend-timing-invalid"))assert(last.fault==9&&!strcmp(last.first_fault_origin,"modem-backend-timing-invalid"));
        else assert(last.fault==1&&!strcmp(last.first_fault_origin,"local-stop"));
        assert(last.turns==0&&last.engine.eoo_count==1&&atomic_load(&f.keys)==1&&!atomic_load(&f.physical_tx)&&last.rf_clear_confirmed);
        result(argv[1],scenario,&f,last);fd_usb_audio_close(audio);return 0;
    }
    if(!strcmp(scenario,"--backend-default")||!strcmp(scenario,"--backend-fixed-pipe")||!strcmp(scenario,"--backend-monitor-competition")){
        fd_usb_audio_arm(audio,true);atomic_store(&f.pressed,true);wait_state(audio,FD_USB_TX,10000);puts("TX_ACTIVE");fflush(stdout);pause_ms(1700);release_ptt(&f);
        fd_usb_snapshot last=wait_completed_turn(audio,1,1,10000);assert(last.turns==1&&last.engine.eoo_count==1&&last.rf_clear_confirmed);
        fd_usb_audio_stop(audio);last=wait_stopped(audio,10000);assert(last.fault==1&&atomic_load(&f.keys)==1&&!atomic_load(&f.physical_tx));
        result(argv[1],scenario,&f,last);fd_usb_audio_close(audio);return 0;
    }
    if(!strcmp(scenario,"--capture-close-missing")||!strcmp(scenario,"--capture-close-invalid")||!strcmp(scenario,"--capture-close-stop")){
        if(!strcmp(scenario,"--capture-close-stop")){assert(usb_test_mic_stop_callback);usb_test_mic_stop_callback(stop_from_close,audio);}
        fd_usb_audio_arm(audio,true);atomic_store(&f.pressed,true);wait_state(audio,FD_USB_TX,10000);puts("TX_ACTIVE");fflush(stdout);pause_ms(1250);release_ptt(&f);
        fd_usb_snapshot last=wait_stopped(audio,10000);
        if(!strcmp(scenario,"--capture-close-stop"))assert(last.fault==1&&!strcmp(last.first_fault_origin,"local-stop"));
        else if(!strcmp(scenario,"--capture-close-invalid"))assert(last.fault==23&&!strcmp(last.first_fault_origin,"microphone-close-frontier-invalid"));
        else assert(last.fault==6||last.fault==9||last.fault==17);
        assert(!last.turns&&!last.engine.eoo_count&&atomic_load(&f.keys)==1&&!atomic_load(&f.physical_tx)&&last.rf_clear_confirmed);
        result(argv[1],scenario,&f,last);fd_usb_audio_close(audio);return 0;
    }
    if(!strcmp(scenario,"--capture-close-tail120")){
        /* Test-only reported pipeline latency; actual ordered microphone PCM
         * still has to arrive through the finite indexed production boundary. */
        fd_usb_audio_arm(audio,true);atomic_store(&f.pressed,true);wait_state(audio,FD_USB_TX,10000);puts("TX_ACTIVE");fflush(stdout);pause_ms(1700);
        release_ptt(&f);fd_usb_snapshot last=wait_completed_turn(audio,1,1,10000);
        assert(last.turns==1&&last.engine.eoo_count==1&&last.rf_clear_confirmed&&!last.mic_holes&&!last.rx_holes);
        fd_usb_audio_stop(audio);last=wait_stopped(audio,10000);assert(last.fault==1&&atomic_load(&f.keys)==1&&!atomic_load(&f.physical_tx));
        result(argv[1],scenario,&f,last);fd_usb_audio_close(audio);return 0;
    }
    if(!strcmp(scenario,"--capture-release-split")||!strcmp(scenario,"--capture-release-held150")){
        assert(usb_test_mic_release_hold_started_ms&&usb_test_mic_barrier_started_ms);
        if(!strcmp(scenario,"--capture-release-split")){assert(usb_test_mic_release_action&&usb_test_mic_release_posted_ms);usb_test_mic_release_action(release_from_callback,&f);}
        fd_usb_audio_arm(audio,true);atomic_store(&f.pressed,true);wait_state(audio,FD_USB_TX,10000);puts("TX_ACTIVE");fflush(stdout);pause_ms(1700);
        marker("USB_TEST_MIC_RELEASE_TRIGGER");uint64_t until=ms()+2000;
        while(!usb_test_mic_release_hold_started_ms()&&ms()<until){(void)healthy_snapshot(audio,"unexpected-capture-release-hold-fault");pause_ms(1);}
        assert(usb_test_mic_release_hold_started_ms());
        if(!strcmp(scenario,"--capture-release-split")){
            while(!usb_test_mic_barrier_started_ms()&&ms()<until){(void)healthy_snapshot(audio,"unexpected-capture-release-barrier-fault");pause_ms(1);}
            assert(usb_test_mic_barrier_started_ms());
            while(!usb_test_mic_release_posted_ms()&&ms()<until){(void)healthy_snapshot(audio,"unexpected-capture-release-action-fault");pause_ms(1);}
            assert(usb_test_mic_release_posted_ms()&&!atomic_load(&f.pressed)&&atomic_load(&f.release)==1);
        }else {pause_ms(50);release_ptt(&f);}
        fd_usb_snapshot last=wait_completed_turn(audio,1,1,10000);
        assert(last.turns==1&&last.engine.eoo_count==1&&last.rf_clear_confirmed&&!last.mic_holes&&!last.rx_holes);
        fd_usb_audio_stop(audio);last=wait_stopped(audio,10000);result(argv[1],scenario,&f,last);fd_usb_audio_close(audio);return 0;
    }
    if(!strcmp(scenario,"--storage-completion-fails")){
        assert(!chmod(incident_dir,0500));fd_usb_audio_stop(audio);fd_usb_snapshot last=wait_stopped(audio,10000);
        assert(last.fault==1&&!last.qualification_complete&&!atomic_load(&f.keys));assert(!chmod(incident_dir,0700));
        result(argv[1],scenario,&f,last);fd_usb_audio_close(audio);return 0;
    }
    if(!strcmp(scenario,"--rx-pause150")){
        assert(usb_test_io_pause_count&&usb_test_io_pause_duration_ms&&usb_test_io_pause_started_ms);
        puts("RX_FIXTURE_READY");fflush(stdout);uint64_t until=ms()+20000;
        while((!usb_test_io_pause_count()||!usb_test_io_pause_duration_ms())&&ms()<until){(void)healthy_snapshot(audio,"unexpected-runtime-fault");pause_ms(5);}
        assert(usb_test_io_pause_count()==1&&usb_test_io_pause_duration_ms()>=150&&usb_test_io_pause_duration_ms()<500);
        puts("RX_SOURCE_TO_SILENCE");fflush(stdout);char ack[1024];snprintf(ack,sizeof(ack),"%s/rx-source-silenced",argv[1]);
        const char *fixture_setting=getenv("USB_TEST_RX_FIXTURE_MS");unsigned long fixture_ms=fixture_setting?strtoul(fixture_setting,NULL,10):30000;if(fixture_ms>30000)fixture_ms=30000;until=ms()+fixture_ms+5000;
        while(access(ack,F_OK)&&ms()<until){(void)healthy_snapshot(audio,"unexpected-runtime-fault");pause_ms(5);}assert(!access(ack,F_OK));
        pause_ms(1500);uint64_t synthesis=healthy_snapshot(audio,"unexpected-rx-settle-fault").engine.synthesis_blocks;pause_ms(500);
        fd_usb_snapshot last=healthy_snapshot(audio,"unexpected-rx-settle-fault");assert(!last.output_queue&&!last.engine.output_depth&&last.engine.synthesis_blocks>=4&&last.engine.synthesis_blocks==synthesis);
        fd_usb_audio_stop(audio);last=wait_stopped(audio,10000);assert(last.fault==1&&!atomic_load(&f.keys)&&!atomic_load(&f.physical_tx));
        result(argv[1],scenario,&f,last);fd_usb_audio_close(audio);return 0;
    }
    if(!strcmp(scenario,"--eoo-backpressure")){
        assert(usb_test_render_hold_started_ms&&usb_test_render_hold_ended_ms&&usb_test_render_hold_hits);
        fd_usb_audio_arm(audio,true);atomic_store(&f.pressed,true);wait_state(audio,FD_USB_TX,10000);puts("TX_ACTIVE");fflush(stdout);pause_ms(1700);
        marker("USB_TEST_RENDER_HOLD_TRIGGER");release_ptt(&f);fd_usb_snapshot last=wait_completed_turn(audio,1,1,10000);
        assert(last.turns==1&&last.engine.eoo_count==1&&last.rf_clear_confirmed);
        assert(usb_test_render_hold_ended_ms()>=usb_test_render_hold_started_ms()+150&&usb_test_render_hold_hits());
        assert(last.render_deferred_count&&last.render_queue_high_water>FD_USB_TX_QUEUE_FRAMES-FD_USB_RENDER_OUTPUT_FRAMES&&last.render_queue_high_water<=FD_USB_TX_QUEUE_FRAMES);
        assert(last.render_final_flush_calls>=2&&last.transport_tail_frames&&last.modem_written_frames==last.rendered_modem+last.transport_tail_frames);
        fd_usb_audio_stop(audio);last=wait_stopped(audio,10000);result(argv[1],scenario,&f,last);fd_usb_audio_close(audio);return 0;
    }
    if(!strcmp(scenario,"--io-pause150")||!strcmp(scenario,"--short-turn200")){
        fd_usb_audio_arm(audio,true);atomic_store(&f.pressed,true);wait_state(audio,FD_USB_TX,10000);puts("TX_ACTIVE");fflush(stdout);
        pause_ms(!strcmp(scenario,"--short-turn200")?200:2000);release_ptt(&f);fd_usb_snapshot last=wait_completed_turn(audio,1,1,10000);
        assert(last.turns==1&&last.engine.eoo_count==1&&last.rf_clear_confirmed);
        if(!strcmp(scenario,"--io-pause150"))assert(usb_test_io_pause_count&&usb_test_io_pause_duration_ms&&usb_test_io_pause_count()==1&&usb_test_io_pause_duration_ms()>=150&&usb_test_io_pause_duration_ms()<500);
        fd_usb_audio_stop(audio);last=wait_stopped(audio,10000);assert(last.fault==1&&atomic_load(&f.keys)==1&&!atomic_load(&f.physical_tx));
        result(argv[1],scenario,&f,last);fd_usb_audio_close(audio);return 0;
    }
    if(!strcmp(scenario,"--many-turns")||!strcmp(scenario,"--reopen")){
        unsigned sessions=!strcmp(scenario,"--reopen")?10:1,turns=sessions==1?20:1;fd_usb_snapshot last={0};
        for(unsigned j=0;j<sessions;++j){
            if(j){snprintf(prefix,sizeof(prefix),"%s/freedv-usb-session-%02u",argv[1],j);atomic_store(&f.halted,false);release_ptt(&f);
                audio=fd_usb_audio_open(&config);assert(audio);wait_state(audio,FD_USB_RX,20000);pause_ms(100);}
            const char *base=strrchr(prefix,'/');base=base?base+1:prefix;snprintf(session_prefixes[j],sizeof(session_prefixes[j]),"%s",base);
            fd_usb_audio_arm(audio,true);for(unsigned turn=0;turn<turns;++turn)durable_turn(audio,&f,base,turn+1,sessions==1&&turn==0?8000:1250);
            fd_usb_audio_stop(audio);last=wait_stopped(audio,10000);assert(last.fault==1&&!atomic_load(&f.physical_tx));fd_usb_audio_close(audio);audio=NULL;
            closed_fds[j]=resources("/proc/self/fd");closed_tasks[j]=resources("/proc/self/task");
            if(j){assert(closed_fds[j]<=closed_fds[0]);assert(closed_tasks[j]<=closed_tasks[0]);}
            ++durability_sessions;
        }
        assert(total_turns==sessions*turns&&total_eoo==total_turns&&atomic_load(&f.keys)==total_turns);
        result(argv[1],scenario,&f,last);return 0;
    }
    if(!strncmp(scenario,"--return-pa-",12)){
        fd_usb_audio_arm(audio,true);atomic_store(&f.pressed,true);wait_state(audio,FD_USB_TX,10000);pause_ms(1250);release_ptt(&f);
        fd_usb_snapshot last;
        if(!strcmp(scenario,"--return-pa-delay")){last=wait_completed_turn(audio,1,1,10000);assert(last.turns==1&&last.engine.eoo_count==1);fd_usb_audio_stop(audio);last=wait_stopped(audio,10000);}
        else {last=wait_stopped(audio,10000);assert(last.fault==(!strcmp(scenario,"--return-pa-reject")?15u:14u));}
        assert(atomic_load(&f.keys)==1&&!atomic_load(&f.physical_tx));result(argv[1],scenario,&f,last);fd_usb_audio_close(audio);return 0;
    }
    if(!strcmp(scenario,"--mic-valid-drop300")){
        fd_usb_audio_arm(audio,true);atomic_store(&f.pressed,true);wait_state(audio,FD_USB_TX,10000);marker("USB_TEST_STALE_TRIGGER");pause_ms(1250);release_ptt(&f);
        fd_usb_snapshot last=wait_stopped(audio,10000);assert(last.fault==23&&!strcmp(last.first_fault_origin,"tx-microphone-index-gap"));
        assert(!last.turns&&!last.engine.eoo_count&&!last.mic_holes&&!last.rx_holes&&!atomic_load(&f.physical_tx));
        result(argv[1],scenario,&f,last);fd_usb_audio_close(audio);return 0;
    }
    if(!strcmp(scenario,"--drain-tx-loss")){
        fd_usb_audio_arm(audio,true);atomic_store(&f.pressed,true);wait_state(audio,FD_USB_TX,10000);pause_ms(1250);release_ptt(&f);
        wait_state(audio,FD_USB_DRAIN,10000);assert(usb_test_trigger_started_ms&&usb_test_trigger_ack_ms);uint64_t until=ms()+5000;
        while(!usb_test_trigger_started_ms()&&ms()<until){(void)healthy_snapshot(audio,"unexpected-trigger-wait-fault");pause_ms(1);}
        assert(usb_test_trigger_started_ms());pause_ms(50);
        uint64_t dropped=ms();assert(!usb_test_trigger_ack_ms());set_physical_tx(&f,false);
        fd_usb_snapshot last=wait_stopped(audio,10000);assert(last.fault==4&&!strcmp(last.first_fault_origin,"tx-status-disappeared-during-drain"));
        uint64_t started=usb_test_trigger_started_ms(),acked=usb_test_trigger_ack_ms();assert(acked>=started+650&&started<dropped&&dropped<acked);
        printf("RF_DROP_DURING_TRIGGER_WAIT started_ms=%llu drop_ms=%llu ack_ms=%llu\n",(unsigned long long)started,(unsigned long long)dropped,(unsigned long long)acked);
        assert(!last.turns&&!atomic_load(&f.physical_tx));result(argv[1],scenario,&f,last);fd_usb_audio_close(audio);return 0;
    }
    if(!strcmp(scenario,"--rx-signal")) {
        uint64_t until=ms()+12000;
        while(healthy_snapshot(audio,"unexpected-rx-signal-fault").engine.synthesis_blocks<4 && ms()<until)pause_ms(10);
        assert(healthy_snapshot(audio,"unexpected-rx-signal-fault").engine.synthesis_blocks>=4);
    } else assert(!healthy_snapshot(audio,"unexpected-initial-rx-fault").engine.synchronized);
    if(qualification) {
        assert(!fd_usb_audio_enable_rf(audio));
        bool qualification_prior_gap=!strcmp(scenario,"--cold-qualification");
        if(qualification_prior_gap)marker("USB_TEST_STALE_TRIGGER");
        bool requested=fd_usb_audio_start_qualification(audio);(void)healthy_snapshot(audio,"unexpected-qualification-start-fault");assert(requested);
        qualification_status_freeze(audio,&f);
        if(!full_session&&strcmp(scenario,"--rf-disabled")&&strcmp(scenario,"--cold-qualification")&&strcmp(scenario,"--cold-idle")) {
            pause_ms(300);
            if(!strcmp(scenario,"--qualification-physical-tx"))set_physical_tx(&f,true);
            else atomic_store(&f.pressed,true);
            fd_usb_snapshot s=wait_stopped(audio,10000);
            assert(s.fault && !s.qualification_complete && !atomic_load(&f.keys));
            assert(!atomic_load(&f.physical_tx));
            result(argv[1],scenario,&f,s);fd_usb_audio_close(audio);return 0;
        }
        uint64_t until=ms()+60000;fd_usb_snapshot s;
        do{
            s=healthy_snapshot(audio,"unexpected-qualification-fault");assert(!atomic_load(&f.keys));
            if(qualification_prior_gap&&usb_test_cold_ack_ms&&usb_test_cold_ack_ms()){
                assert(remove(getenv("USB_TEST_STALE_TRIGGER"))==0);qualification_prior_gap=false;
            }
            if(s.qualification_complete)break;
            pause_ms(10);
        }while(ms()<until);
        assert(s.qualification_complete && s.qualification_turns==3 && s.turns==3 && s.engine.eoo_count==3);
        assert(s.rf_disabled && !s.rf_tx && !atomic_load(&f.physical_tx));
        atomic_store(&f.pressed,true);pause_ms(50);assert(!fd_usb_audio_enable_rf(audio));release_ptt(&f);pause_ms(100);
        if(!strcmp(scenario,"--cold-idle")){
            fd_usb_audio_stop(audio);s=wait_stopped(audio,10000);assert(s.rf_disabled&&!atomic_load(&f.keys));
            result(argv[1],scenario,&f,s);fd_usb_audio_close(audio);return 0;
        }
        assert(fd_usb_audio_enable_rf(audio));assert(!atomic_load(&f.keys));
        if(!full_session){fd_usb_audio_stop(audio);s=wait_stopped(audio,10000);
            result(argv[1],scenario,&f,s);fd_usb_audio_close(audio);return 0;}
        /* Continue through the same held/released panel and complete RF-turn
         * path below, using this retained qualified codec and all streams. */
    }
    if(!strcmp(scenario,"--inactive-hole")) {
        marker("USB_TEST_HOLE_TRIGGER");pause_ms(250);(void)healthy_snapshot(audio,"unexpected-runtime-fault");
    }
    if(!strcmp(scenario,"--inactive-stale")) {
        marker("USB_TEST_STALE_TRIGGER");pause_ms(800);
        (void)healthy_snapshot(audio,"unexpected-runtime-fault");
        assert(remove(getenv("USB_TEST_STALE_TRIGGER"))==0);pause_ms(600);
    }
    fd_usb_audio_arm(audio,true);
    if(!strcmp(scenario,"--pa-reject") || !strcmp(scenario,"--pa-missing")){
        atomic_store(&f.pressed,true);fd_usb_snapshot s=wait_stopped(audio,10000);
        assert(s.fault==(!strcmp(scenario,"--pa-reject")?15u:14u));
        assert(!atomic_load(&f.keys)&&!atomic_load(&f.physical_tx));
        assert(s.first_fault_origin[0]&&s.first_fault_stream[0]);
        result(argv[1],scenario,&f,s);fd_usb_audio_close(audio);return 0;
    }

    if(cold_case){
        assert(usb_test_cold_ack_ms&&usb_test_cold_recovered);
        atomic_store(&f.pressed,true);
        if(cold_rx_case){
            wait_state(audio,FD_USB_TX,10000);pause_ms(1250);release_ptt(&f);
            uint64_t until=ms()+5000;while(!usb_test_cold_ack_ms()&&ms()<until){(void)healthy_snapshot(audio,"unexpected-cold-ack-fault");pause_ms(1);}
            assert(usb_test_cold_ack_ms()>0&&!atomic_load(&f.physical_tx)&&atomic_load(&f.keys)==1);
            wait_completed_turn(audio,1,1,10000);pause_ms(100);
            fd_usb_snapshot s=healthy_snapshot(audio,"unexpected-completed-turn-fault");assert(s.turns==1&&s.engine.eoo_count==1);
            fd_usb_audio_stop(audio);s=wait_stopped(audio,10000);
            assert(atomic_load(&f.keys)==1&&!atomic_load(&f.physical_tx));
            result(argv[1],scenario,&f,s);fd_usb_audio_close(audio);return 0;
        }
        uint64_t until=ms()+5000;
        while(!usb_test_cold_ack_ms()&&ms()<until){(void)healthy_snapshot(audio,"unexpected-cold-ack-fault");pause_ms(1);}
        assert(usb_test_cold_ack_ms()>0&&!atomic_load(&f.keys)&&!atomic_load(&f.physical_tx));
        if(cold_late_tx_case){
            fd_usb_snapshot s=wait_stopped(audio,10000);
            assert(s.fault==4&&!strcmp(s.first_fault_origin,"unexpected-tx-during-preparation"));
            assert(atomic_load(&recovered_observations)>=2&&!atomic_load(&f.keys)&&!atomic_load(&f.physical_tx));
            result(argv[1],scenario,&f,s);fd_usb_audio_close(audio);return 0;
        }
        if(!strcmp(scenario,"--cold-stop")){
            uint64_t before=ms();fd_usb_audio_stop(audio);assert(ms()-before<100);
            fd_usb_snapshot s=wait_stopped(audio,10000);assert(s.fault==1&&!atomic_load(&f.keys));
            result(argv[1],scenario,&f,s);fd_usb_audio_close(audio);return 0;
        }
        if(!strcmp(scenario,"--cold-release")){
            /* Retain release even when a press follows before readiness. */
            release_ptt(&f);atomic_store(&f.pressed,true);
            wait_state(audio,FD_USB_RX,10000);pause_ms(400);
            assert(!atomic_load(&f.keys)&&!atomic_load(&f.physical_tx));
            fd_usb_audio_stop(audio);fd_usb_snapshot s=wait_stopped(audio,10000);
            result(argv[1],scenario,&f,s);fd_usb_audio_close(audio);return 0;
        }
        if(!strcmp(scenario,"--cold-recovery-fails")){
            fd_usb_snapshot s=wait_stopped(audio,10000);
            assert(s.fault==17&&!strcmp(s.first_fault_origin,"capture-not-resumed-after-preparation"));
            assert(s.first_fault_operation_ms>=1500&&!atomic_load(&f.keys)&&!atomic_load(&f.physical_tx));
            result(argv[1],scenario,&f,s);fd_usb_audio_close(audio);return 0;
        }
        wait_state(audio,FD_USB_TX,10000);assert(atomic_load(&f.keys)==1);puts("TX_ACTIVE");fflush(stdout);
        pause_ms(1250);release_ptt(&f);wait_completed_turn(audio,1,1,10000);pause_ms(100);
        assert(!atomic_load(&f.physical_tx)&&atomic_load(&f.keys)==1);
        fd_usb_snapshot s=healthy_snapshot(audio,"unexpected-completed-turn-fault");assert(s.turns==1&&s.engine.eoo_count==1);
        fd_usb_audio_stop(audio);s=wait_stopped(audio,10000);
        result(argv[1],scenario,&f,s);fd_usb_audio_close(audio);return 0;
    }
    if(!strcmp(scenario,"--prekey-stale")) {
        marker("USB_TEST_STALE_TRIGGER");pause_ms(900);(void)healthy_snapshot(audio,"unexpected-runtime-fault");
        atomic_store(&f.pressed,true);fd_usb_snapshot s=wait_stopped(audio,10000);
        assert(s.fault==17 && !atomic_load(&f.keys) && !atomic_load(&f.physical_tx));
        result(argv[1],scenario,&f,s);fd_usb_audio_close(audio);return 0;
    }
    if(!strcmp(scenario,"--stop-during-key") || !strcmp(scenario,"--release-during-key")) {
        atomic_store(&f.block_key,900);atomic_store(&f.pressed,true);wait_key_inflight(audio,&f);pause_ms(100);
        if(!strcmp(scenario,"--stop-during-key")) {
            uint64_t before=ms();fd_usb_audio_stop(audio);assert(ms()-before<100);assert(atomic_load(&f.halted));
            fd_usb_snapshot s=wait_stopped(audio,10000);assert(s.fault==1);
            pause_ms(300);assert(!atomic_load(&f.physical_tx) && atomic_load(&f.keys)==1);
            result(argv[1],scenario,&f,s);fd_usb_audio_close(audio);return 0;
        }
        release_ptt(&f);wait_state(audio,FD_USB_RX,10000);pause_ms(400);
        assert(!atomic_load(&f.physical_tx) && atomic_load(&f.keys)==1);
        assert(healthy_snapshot(audio,"unexpected-release-during-key-fault").engine.eoo_count==0);
        fd_usb_audio_stop(audio);fd_usb_snapshot s=wait_stopped(audio,10000);
        result(argv[1],scenario,&f,s);fd_usb_audio_close(audio);return 0;
    }
    /* Release before delayed TX acknowledgement: no accepted speech or EOO. */
    atomic_store(&f.delay,300);atomic_store(&f.pressed,true);
    uint64_t until=ms()+5000;while(!atomic_load(&f.keys) && ms()<until){(void)healthy_snapshot(audio,"unexpected-key-count-wait-fault");pause_ms(5);}assert(atomic_load(&f.keys)==1);
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
    bool gain_turn=!strcmp(scenario,"--audio-gain-turn");
    if(gain_turn){assert(fd_usb_audio_set_mic_gain(audio,6));(void)controls_snapshot(audio,6,40,true);}
    atomic_store(&f.delay,!strcmp(scenario,"--block-key")?20:650);
    atomic_store(&f.block_key,!strcmp(scenario,"--block-key")?900:0);
    atomic_store(&f.pressed,true);wait_state(audio,FD_USB_TX,6000);
    atomic_store(&f.delay,0);atomic_store(&f.block_key,0);puts("TX_ACTIVE");fflush(stdout);
    if(gain_turn){
        for(unsigned i=0;i<3;++i)assert(fd_usb_audio_adjust_headset_volume(audio,1));
        controls_phase(audio,&f,argv[1],"gain-turn-up",controls_snapshot(audio,6,43,false));
    }
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
        marker("USB_TEST_STALE_TRIGGER");pause_ms(800);(void)healthy_snapshot(audio,"unexpected-runtime-fault");
        assert(remove(getenv("USB_TEST_STALE_TRIGGER"))==0);pause_ms(900);
    }else pause_ms(1700);
    if(gain_turn){
        uint64_t generation=healthy_snapshot(audio,"unexpected-live-gain-fault").generation;
        assert(fd_usb_audio_set_mic_gain(audio,-6));
        for(unsigned i=0;i<3;++i)assert(fd_usb_audio_adjust_headset_volume(audio,-1));
        fd_usb_snapshot changed=controls_snapshot(audio,-6,40,false);assert(changed.generation==generation);
        controls_phase(audio,&f,argv[1],"gain-turn-down",changed);
    }
    atomic_fetch_add(&f.release,1);wait_completed_turn(audio,full_session?4:1,full_session?4:1,6000);
    pause_ms(300);assert(atomic_load(&f.keys)==2 && !atomic_load(&f.physical_tx));
    release_ptt(&f);pause_ms(100);atomic_store(&f.pressed,true);wait_state(audio,FD_USB_TX,6000);pause_ms(1200);
    release_ptt(&f);wait_completed_turn(audio,full_session?5:2,full_session?5:2,6000);
    if(gain_turn){
        uint64_t generation=healthy_snapshot(audio,"unexpected-rx-gain-fault").generation;
        assert(fd_usb_audio_set_mic_gain(audio,0));fd_usb_snapshot changed=controls_snapshot(audio,0,40,false);
        assert(changed.generation==generation);controls_phase(audio,&f,argv[1],"gain-turn-rx",changed);
    }
    pause_ms(50);fd_usb_snapshot s=healthy_snapshot(audio,"unexpected-completed-turn-fault");
    fprintf(stderr,"COMPLETED turns=%llu eoo=%llu\n",(unsigned long long)s.turns,(unsigned long long)s.engine.eoo_count);
    assert(s.turns==(full_session?5u:2u) && s.engine.eoo_count==(full_session?5u:2u));
    assert(s.engine.rade_creations==1 && s.engine.encoder_creations==1 && s.engine.decoder_creations==1);
    if(strcmp(scenario,"--inactive-stale"))assert(s.mic_max_gap_ms<500);
    if(strcmp(scenario,"--inactive-radio-stale"))assert(s.rx_max_gap_ms<500);
    fd_usb_audio_stop(audio);s=wait_stopped(audio,10000);
    assert(!atomic_load(&f.physical_tx) && atomic_load(&f.keys)==3);
    result(argv[1],scenario,&f,s);fd_usb_audio_close(audio);
    puts("PASS complete four-stream turns, retained release, fresh RX, no rekey and unchanged codec lifetime; fake RF only");
    return 0;
}
