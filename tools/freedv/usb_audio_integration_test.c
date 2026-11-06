// SPDX-License-Identifier: BSD-2-Clause
#define _POSIX_C_SOURCE 200809L
#undef NDEBUG
#include "../../src/freedv/usb_voice.h"
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
static bool cold_case,cold_rx_case,cold_late_tx_case;
static atomic_uint recovered_observations;
typedef struct {
    atomic_bool pressed,physical_tx,packet_tx,halted,stall,freeze,protection,inflight;
    atomic_uint keys,clears,delay,block_key,block_clear;
    atomic_uint_fast64_t release,sequence,packet_at,ack_at;
    pthread_t ticker;pthread_mutex_t packet_mutex;atomic_bool ticker_stop;atomic_uint packets_emitted,observations;
} fake;
static uint64_t ms(void) {
    struct timespec t;clock_gettime(CLOCK_MONOTONIC,&t);
    return (uint64_t)t.tv_sec*1000+t.tv_nsec/1000000;
}
static void pause_ms(unsigned n) {
    struct timespec t={n/1000,(n%1000)*1000000};nanosleep(&t,NULL);
}
/* Real packet arrival is a separate producer. Polling never refreshes age or
 * sequence, so a blocked observer cannot manufacture fresh telemetry. */
static void emit_packet(fake *f){
    pthread_mutex_lock(&f->packet_mutex);uint64_t now=ms(),ack=atomic_load(&f->ack_at);
    atomic_store(&f->packet_tx,atomic_load(&f->physical_tx)&&(!ack||now>=ack));
    atomic_store(&f->packet_at,now);atomic_fetch_add(&f->sequence,1);atomic_fetch_add(&f->packets_emitted,1);
    pthread_mutex_unlock(&f->packet_mutex);
}
static void *telemetry(void *context){fake *f=context;
    while(!atomic_load(&f->ticker_stop)){if(!atomic_load(&f->freeze))emit_packet(f);pause_ms(20);}return NULL;
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
        atomic_store(&f->physical_tx,true);emit_packet(f);
    }
    atomic_fetch_add(&f->observations,1);pthread_mutex_lock(&f->packet_mutex);uint64_t now=ms(),at=atomic_load(&f->packet_at);
    *out=(fd_usb_inputs){.valid=true,.pressed=atomic_load(&f->pressed),
        .rf_valid=now>=at&&now-at<=250,.rf_tx=atomic_load(&f->packet_tx),
        .protection=atomic_load(&f->protection),.release=atomic_load(&f->release),
        .sequence=atomic_load(&f->sequence)};
    pthread_mutex_unlock(&f->packet_mutex);
    return true;
}
static bool key(void *context,uint64_t release) {
    fake *f=context;
    if(cold_case&&!cold_rx_case){assert(usb_test_cold_ack_ms&&usb_test_cold_recovered);
        assert(usb_test_cold_ack_ms()>0&&usb_test_cold_recovered()==3);}
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
typedef struct {char session[32];unsigned turn;uint64_t accepted_mic,rendered_modem,modem_tap_start,modem_tap_end,started_ms,ended_ms;} turn_record;
static turn_record turn_records[32];static unsigned turn_record_n,durability_sessions,total_turns,total_eoo;
static unsigned closed_fds[10],closed_tasks[10];static char session_prefixes[10][32];
static unsigned resources(const char *name){DIR *d=opendir(name);assert(d);unsigned n=0;struct dirent *e;
    while((e=readdir(d)))if(strcmp(e->d_name,".")&&strcmp(e->d_name,".."))++n;closedir(d);return n;
}
static void durable_turn(fd_usb_audio *audio,fake *f,const char *session,unsigned turn,unsigned hold_ms){
    fd_usb_snapshot before=fd_usb_audio_snapshot(audio);assert(before.state==FD_USB_RX&&!before.rf_tx&&!before.fault);
    atomic_store(&f->pressed,true);wait_state(audio,FD_USB_TX,10000);uint64_t started=ms();puts("TX_ACTIVE");fflush(stdout);
    pause_ms(hold_ms);release_ptt(f);fd_usb_snapshot after=wait_state(audio,FD_USB_RX,10000);pause_ms(100);
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
    assert(!fseek(out,-2,SEEK_CUR));
    fprintf(out,",\"session_count\":%u,\"total_turns\":%u,\"total_eoo\":%u,\"telemetry_oracle\":{\"period_ms\":20,\"emitted\":%u,\"observations\":%u,\"poll_refresh_probe_pass\":true},\"session_prefixes\":[",
        durability_sessions?durability_sessions:1,durability_sessions?total_turns:(unsigned)s.turns,durability_sessions?total_eoo:(unsigned)s.engine.eoo_count,atomic_load(&f->packets_emitted),atomic_load(&f->observations));
    for(unsigned i=0;i<durability_sessions;++i)fprintf(out,"%s\"%s\"",i?",":"",session_prefixes[i]);
    fputs("],\"resource_samples\":[",out);
    for(unsigned i=0;i<durability_sessions;++i)fprintf(out,"%s{\"fds\":%u,\"threads\":%u}",i?",":"",closed_fds[i],closed_tasks[i]);
    fputs("],\"turn_records\":[",out);
    for(unsigned i=0;i<turn_record_n;++i){turn_record *r=&turn_records[i];fprintf(out,"%s{\"session\":\"%s\",\"turn\":%u,\"accepted_mic_samples\":%llu,\"rendered_modem_samples\":%llu,\"modem_tap_start\":%llu,\"modem_tap_end\":%llu,\"started_ms\":%llu,\"ended_ms\":%llu}",i?",":"",r->session,r->turn,(unsigned long long)r->accepted_mic,(unsigned long long)r->rendered_modem,(unsigned long long)r->modem_tap_start,(unsigned long long)r->modem_tap_end,(unsigned long long)r->started_ms,(unsigned long long)r->ended_ms);}
    fprintf(out,"],\"io_service_pause\":{\"count\":%u,\"started_ms\":%llu,\"duration_ms\":%llu}}\n",usb_test_io_pause_count?usb_test_io_pause_count():0,usb_test_io_pause_started_ms?usb_test_io_pause_started_ms():0,usb_test_io_pause_duration_ms?usb_test_io_pause_duration_ms():0);fclose(out);stop_telemetry(f);
}
int main(int argc,char **argv) {
    assert(argc==2 || argc==3);const char *scenario=argc==3?argv[2]:"--normal";
    fake f={0};start_telemetry(&f);char prefix[512];
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
    bool full_session=!strcmp(scenario,"--full-session");
    bool qualification=full_session||!strcmp(scenario,"--rf-disabled") || !strcmp(scenario,"--qualification-physical-tx") ||
                       !strcmp(scenario,"--qualification-held-ptt") || !strcmp(scenario,"--cold-qualification") || !strcmp(scenario,"--cold-idle") || !strncmp(scenario,"--storage-",10) || !strcmp(scenario,"--rx-pause150");
    cold_case=!strncmp(scenario,"--cold-",7)&&strcmp(scenario,"--cold-idle");
    cold_rx_case=!strcmp(scenario,"--cold-rx-resume");
    cold_late_tx_case=!strcmp(scenario,"--cold-prekey-unexpected-tx");
    fd_usb_config config={.source="alsa_input.usb-fixture",.sink="alsa_output.usb-fixture",
        .radio_source="radio_source_fixture",.radio_sink="radio_sink_fixture",.trace_prefix=prefix,
        .control={.context=&f,.observe=observe,.key=key,.clear=clear,.inhibit=inhibit},
        .rf_disabled=qualification,.qualification_turns=3,.qualification_control_delay_ms=450,
        .qualification_idle_ms=!strcmp(scenario,"--cold-idle")?20000:0,.incident_dir=incident_dir,
        .trace_pcm_seconds=!strcmp(scenario,"--many-turns")?120:30};
    if(!strcmp(scenario,"--storage-start-fails")){assert(!mkdir(incident_dir,0500));}
    fd_usb_audio *audio=fd_usb_audio_open(&config);assert(audio);
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
    wait_state(audio,FD_USB_RX,20000);pause_ms(350);
    if(!strcmp(scenario,"--storage-completion-fails")){
        assert(!chmod(incident_dir,0500));fd_usb_audio_stop(audio);fd_usb_snapshot last=wait_stopped(audio,10000);
        assert(last.fault==1&&!last.qualification_complete&&!atomic_load(&f.keys));assert(!chmod(incident_dir,0700));
        result(argv[1],scenario,&f,last);fd_usb_audio_close(audio);return 0;
    }
    if(!strcmp(scenario,"--rx-pause150")){
        assert(usb_test_io_pause_count&&usb_test_io_pause_duration_ms&&usb_test_io_pause_started_ms);
        puts("RX_FIXTURE_READY");fflush(stdout);uint64_t until=ms()+20000;
        while((!usb_test_io_pause_count()||!usb_test_io_pause_duration_ms())&&ms()<until){assert(!fd_usb_audio_snapshot(audio).fault);pause_ms(5);}
        assert(usb_test_io_pause_count()==1&&usb_test_io_pause_duration_ms()>=150&&usb_test_io_pause_duration_ms()<500);
        puts("RX_SOURCE_TO_SILENCE");fflush(stdout);char ack[1024];snprintf(ack,sizeof(ack),"%s/rx-source-silenced",argv[1]);
        const char *fixture_setting=getenv("USB_TEST_RX_FIXTURE_MS");unsigned long fixture_ms=fixture_setting?strtoul(fixture_setting,NULL,10):30000;if(fixture_ms>30000)fixture_ms=30000;until=ms()+fixture_ms+5000;
        while(access(ack,F_OK)&&ms()<until){assert(!fd_usb_audio_snapshot(audio).fault);pause_ms(5);}assert(!access(ack,F_OK));
        pause_ms(1500);uint64_t synthesis=fd_usb_audio_snapshot(audio).engine.synthesis_blocks;pause_ms(500);
        fd_usb_snapshot last=fd_usb_audio_snapshot(audio);assert(!last.fault&&!last.output_queue&&!last.engine.output_depth&&last.engine.synthesis_blocks>=4&&last.engine.synthesis_blocks==synthesis);
        fd_usb_audio_stop(audio);last=wait_stopped(audio,10000);assert(last.fault==1&&!atomic_load(&f.keys)&&!atomic_load(&f.physical_tx));
        result(argv[1],scenario,&f,last);fd_usb_audio_close(audio);return 0;
    }
    if(!strcmp(scenario,"--io-pause150")||!strcmp(scenario,"--short-turn200")){
        fd_usb_audio_arm(audio,true);atomic_store(&f.pressed,true);wait_state(audio,FD_USB_TX,10000);puts("TX_ACTIVE");fflush(stdout);
        pause_ms(!strcmp(scenario,"--short-turn200")?200:2000);release_ptt(&f);fd_usb_snapshot last=wait_state(audio,FD_USB_RX,10000);
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
        if(!strcmp(scenario,"--return-pa-delay")){last=wait_state(audio,FD_USB_RX,10000);assert(last.turns==1&&last.engine.eoo_count==1);fd_usb_audio_stop(audio);last=wait_stopped(audio,10000);}
        else {last=wait_stopped(audio,10000);assert(last.fault==(!strcmp(scenario,"--return-pa-reject")?15u:14u));}
        assert(atomic_load(&f.keys)==1&&!atomic_load(&f.physical_tx));result(argv[1],scenario,&f,last);fd_usb_audio_close(audio);return 0;
    }
    if(!strcmp(scenario,"--mic-valid-drop300")){
        fd_usb_audio_arm(audio,true);atomic_store(&f.pressed,true);wait_state(audio,FD_USB_TX,10000);marker("USB_TEST_STALE_TRIGGER");pause_ms(1250);release_ptt(&f);
        fd_usb_snapshot last=wait_stopped(audio,10000);assert(last.fault==23&&!strcmp(last.first_fault_origin,"tx-microphone-frame-deficit"));
        assert(!last.turns&&!last.engine.eoo_count&&!last.mic_holes&&!last.rx_holes&&!atomic_load(&f.physical_tx));
        result(argv[1],scenario,&f,last);fd_usb_audio_close(audio);return 0;
    }
    if(!strcmp(scenario,"--drain-tx-loss")){
        fd_usb_audio_arm(audio,true);atomic_store(&f.pressed,true);wait_state(audio,FD_USB_TX,10000);pause_ms(1250);release_ptt(&f);
        wait_state(audio,FD_USB_DRAIN,10000);assert(usb_test_trigger_started_ms&&usb_test_trigger_ack_ms);uint64_t until=ms()+5000;
        while(!usb_test_trigger_started_ms()&&ms()<until)pause_ms(1);assert(usb_test_trigger_started_ms());pause_ms(50);
        uint64_t dropped=ms();assert(!usb_test_trigger_ack_ms());atomic_store(&f.physical_tx,false);
        fd_usb_snapshot last=wait_stopped(audio,10000);assert(last.fault==4&&!strcmp(last.first_fault_origin,"tx-status-disappeared-during-drain"));
        uint64_t started=usb_test_trigger_started_ms(),acked=usb_test_trigger_ack_ms();assert(acked>=started+650&&started<dropped&&dropped<acked);
        printf("RF_DROP_DURING_TRIGGER_WAIT started_ms=%llu drop_ms=%llu ack_ms=%llu\n",(unsigned long long)started,(unsigned long long)dropped,(unsigned long long)acked);
        assert(!last.turns&&!atomic_load(&f.physical_tx));result(argv[1],scenario,&f,last);fd_usb_audio_close(audio);return 0;
    }
    if(!strcmp(scenario,"--rx-signal")) {
        uint64_t until=ms()+12000;
        while(fd_usb_audio_snapshot(audio).engine.synthesis_blocks<4 && ms()<until)pause_ms(10);
        assert(fd_usb_audio_snapshot(audio).engine.synthesis_blocks>=4);
    } else assert(!fd_usb_audio_snapshot(audio).engine.synchronized);
    if(qualification) {
        assert(!fd_usb_audio_enable_rf(audio));
        bool qualification_prior_gap=!strcmp(scenario,"--cold-qualification");
        if(qualification_prior_gap)marker("USB_TEST_STALE_TRIGGER");
        assert(fd_usb_audio_start_qualification(audio));
        if(!full_session&&strcmp(scenario,"--rf-disabled")&&strcmp(scenario,"--cold-qualification")&&strcmp(scenario,"--cold-idle")) {
            pause_ms(300);
            if(!strcmp(scenario,"--qualification-physical-tx"))atomic_store(&f.physical_tx,true);
            else atomic_store(&f.pressed,true);
            fd_usb_snapshot s=wait_stopped(audio,10000);
            assert(s.fault && !s.qualification_complete && !atomic_load(&f.keys));
            assert(!atomic_load(&f.physical_tx));
            result(argv[1],scenario,&f,s);fd_usb_audio_close(audio);return 0;
        }
        uint64_t until=ms()+60000;fd_usb_snapshot s;
        do{
            s=fd_usb_audio_snapshot(audio);assert(!s.fault && !atomic_load(&f.keys));
            if(qualification_prior_gap&&usb_test_cold_ack_ms&&usb_test_cold_ack_ms()){
                assert(remove(getenv("USB_TEST_STALE_TRIGGER"))==0);qualification_prior_gap=false;
            }
            if(s.qualification_complete)break;pause_ms(10);
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
        marker("USB_TEST_HOLE_TRIGGER");pause_ms(250);assert(!fd_usb_audio_snapshot(audio).fault);
    }
    if(!strcmp(scenario,"--inactive-stale")) {
        marker("USB_TEST_STALE_TRIGGER");pause_ms(800);
        assert(!fd_usb_audio_snapshot(audio).fault);
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
            uint64_t until=ms()+5000;while(!usb_test_cold_ack_ms()&&ms()<until)pause_ms(1);
            assert(usb_test_cold_ack_ms()>0&&!atomic_load(&f.physical_tx)&&atomic_load(&f.keys)==1);
            wait_state(audio,FD_USB_RX,10000);pause_ms(100);
            fd_usb_snapshot s=fd_usb_audio_snapshot(audio);assert(s.turns==1&&s.engine.eoo_count==1);
            fd_usb_audio_stop(audio);s=wait_stopped(audio,10000);
            assert(atomic_load(&f.keys)==1&&!atomic_load(&f.physical_tx));
            result(argv[1],scenario,&f,s);fd_usb_audio_close(audio);return 0;
        }
        uint64_t until=ms()+5000;
        while(!usb_test_cold_ack_ms()&&ms()<until)pause_ms(1);
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
        pause_ms(1250);release_ptt(&f);wait_state(audio,FD_USB_RX,10000);pause_ms(100);
        assert(!atomic_load(&f.physical_tx)&&atomic_load(&f.keys)==1);
        fd_usb_snapshot s=fd_usb_audio_snapshot(audio);assert(s.turns==1&&s.engine.eoo_count==1);
        fd_usb_audio_stop(audio);s=wait_stopped(audio,10000);
        result(argv[1],scenario,&f,s);fd_usb_audio_close(audio);return 0;
    }
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
