// SPDX-License-Identifier: LGPL-2.1-or-later
/* Exercise the production coordinator callbacks/command dispatch/completion.
 * Only radio and physical-audio endpoints are substituted: controller, state
 * machine and independent watchdog/monitor are the real implementations. */
#define ENABLE_FREEDV_DIAGNOSTICS 1
#include "src/freedv/diagnostics.c"
#include <assert.h>

struct fd_native_audio {
    fd_native_config config;
    atomic_bool started, stopping, failed, acknowledged, finished, restored;
};
static struct {
    pthread_mutex_t mutex;
    fd_observation observation;
    bool owned;
    hw_commanded_audio_route_t commanded;
    unsigned apply_calls, key_edges, starts, finishes, mixer_restores, radio_restores, destroyed, snapshots;
} fake = {.mutex=PTHREAD_MUTEX_INITIALIZER,.observation={true,true,false,100}};
static bool verify_observe_unlocked;

const char *radio_base_ver_str(void) { return "coordinator-test-baseband"; }
void radio_freedv_session_snapshot(const fd_radio_session *session,fd_radio_session *out) {
    pthread_mutex_lock(&fake.mutex);*out=*session;++fake.snapshots;pthread_mutex_unlock(&fake.mutex);
}
bool radio_freedv_session_acquire(fd_radio_session *session,fd_observation *observation) {
    pthread_mutex_lock(&fake.mutex);
    bool ok=!fake.owned && fake.observation.known && fake.observation.fresh && !fake.observation.tx;
    if(ok){memset(session,0,sizeof(*session));session->owned=session->ready=session->restoration_complete=true;
        fake.owned=true;*observation=fake.observation;session->acquired_sequence=observation->sequence;}
    pthread_mutex_unlock(&fake.mutex);return ok;
}
bool radio_freedv_session_release(fd_radio_session *session) {
    pthread_mutex_lock(&fake.mutex);
    bool ok=fake.owned && session->restoration_complete && fake.observation.known && fake.observation.fresh && !fake.observation.tx;
    if(ok){fake.owned=false;session->owned=false;}
    pthread_mutex_unlock(&fake.mutex);return ok;
}
bool radio_freedv_session_observe(fd_observation *observation) {
    pthread_mutex_lock(&fake.mutex);*observation=fake.observation;
    pthread_mutex_unlock(&fake.mutex);return observation->known;
}
bool radio_get_commanded_audio_route(radio_commanded_audio_route_t *route) {
    pthread_mutex_lock(&fake.mutex);
    *route=(radio_commanded_audio_route_t){.commanded_record=fake.commanded.record,
        .commanded_modem=fake.commanded.modem,.commanded_ptt=fake.commanded.ptt,
        .observed_state=fake.observation.tx?RADIO_TX:RADIO_RX,.packet_tx_known=fake.observation.known,
        .packet_tx=fake.observation.tx,.packet_age_ms=fake.observation.fresh?0u:1000u,
        .packet_sequence=fake.observation.sequence};
    pthread_mutex_unlock(&fake.mutex);return true;
}
bool radio_freedv_session_tx_permitted(fd_radio_session *session) {
    (void)session;return true;
}
bool radio_freedv_session_apply(fd_radio_session *session,const fd_case *selected,radio_mic_sel_t mic) {
    assert(mic==radio_mic_handle);
    pthread_mutex_lock(&fake.mutex);assert(fake.owned && session->owned);
    bool record=false,modem=false,ptt=false;
    if(selected->kind!=FD_CASE_PASSIVE)assert(fd_matrix_bits((fd_matrix_case)selected->matrix_case,&record,&modem,&ptt));
    fake.commanded=(hw_commanded_audio_route_t){record,modem,ptt};
    ++fake.apply_calls;fake.key_edges+=modem||ptt;session->case_active=true;session->restoration_complete=false;
    pthread_mutex_unlock(&fake.mutex);return true;
}
bool radio_freedv_session_clear(fd_radio_session *session,uint64_t *sequence) {
    pthread_mutex_lock(&fake.mutex);assert(fake.owned);
    fake.commanded.modem=fake.commanded.ptt=false;
    *sequence=session->clear_sequence=fake.observation.sequence;session->software_cleared=true;session->case_active=false;
    pthread_mutex_unlock(&fake.mutex);return true;
}
bool radio_freedv_session_restore(fd_radio_session *session,uint64_t clear_sequence) {
    pthread_mutex_lock(&fake.mutex);
    bool ok=fake.owned && fake.observation.known && fake.observation.fresh && !fake.observation.tx &&
        fake.observation.sequence>clear_sequence;
    if(ok){session->restoration_complete=session->readback_match=true;session->restored=session->saved;fake.commanded=session->saved.route;++fake.radio_restores;}
    pthread_mutex_unlock(&fake.mutex);return ok;
}
bool fd_native_audio_codec_available(void) {return true;}
fd_native_audio *fd_native_audio_prepare(const fd_native_config *config,char *error,size_t size) {
    (void)error;(void)size;
    fd_native_audio *a=calloc(1,sizeof(*a));assert(a);a->config=*config;
    atomic_init(&a->started,false);atomic_init(&a->stopping,false);atomic_init(&a->failed,false);
    atomic_init(&a->acknowledged,false);atomic_init(&a->finished,false);atomic_init(&a->restored,false);
    return a;
}
bool fd_native_audio_start(fd_native_audio *a) {++fake.starts;return !atomic_exchange(&a->started,true);}
void fd_native_audio_request_stop(fd_native_audio *a) {atomic_store(&a->stopping,true);}
bool fd_native_audio_stop_requested(fd_native_audio *a) {return atomic_load(&a->stopping);}
bool fd_native_audio_failed(fd_native_audio *a) {return atomic_load(&a->failed);}
bool fd_native_audio_quiescent(fd_native_audio *a) {return atomic_load(&a->acknowledged);}
bool fd_native_audio_finish(fd_native_audio *a) {assert(atomic_load(&a->stopping));++fake.finishes;atomic_store(&a->finished,true);return true;}
bool fd_native_audio_restore(fd_native_audio *a) {assert(atomic_load(&a->finished)&&atomic_load(&a->acknowledged));++fake.mixer_restores;atomic_store(&a->restored,true);return true;}
fd_native_stats fd_native_audio_observe(fd_native_audio *a) {
    if(verify_observe_unlocked){
        struct timespec deadline;assert(!clock_gettime(CLOCK_REALTIME,&deadline));++deadline.tv_sec;
        /* Audio observation may wait for its own lock. The production progress
         * sampler must release the controller mutex before entering it. */
        assert(!pthread_mutex_timedlock(&d.controller.mutex,&deadline));
        pthread_mutex_unlock(&d.controller.mutex);
    }
    return (fd_native_stats){.kind=a->config.kind,.generation=a->config.generation,.prepared=true,
        .started=atomic_load(&a->started),.stop_requested=atomic_load(&a->stopping),.finished=atomic_load(&a->finished),
        .quiescent=atomic_load(&a->acknowledged),.mixer_restored=atomic_load(&a->restored),.failures=atomic_load(&a->failed)?1u:0u};
}
bool fd_native_audio_mixer_describe(fd_native_audio *a,bool restored,char *out,size_t size) {
    (void)a;(void)restored;return snprintf(out,size,"{\"test_mixer\":true}")>0;
}
void fd_native_audio_destroy(fd_native_audio *a) {assert(atomic_load(&a->finished)&&atomic_load(&a->restored));++fake.destroyed;free(a);}

static void pause_short(void) {struct timespec delay={0,2000000};nanosleep(&delay,NULL);}
static void packet(bool known,bool fresh,bool tx,bool advance) {
    pthread_mutex_lock(&fake.mutex);
    fake.observation.known=known;fake.observation.fresh=fresh;fake.observation.tx=tx;
    if(advance)++fake.observation.sequence;
    pthread_mutex_unlock(&fake.mutex);
}
static fd_session status(void) {fd_session s={0};assert(fd_controller_status(&d.controller,&s));return s;}
static void wait_state(fd_state wanted) {
    for(unsigned i=0;i<2500;++i){if(status().state==wanted)return;pause_short();}
    assert(!"coordinator did not reach expected state");
}
static command route_command(command_kind kind) {
    return (command){kind,CASE_ROUTE_FIRST+FD_R1_M0_P0,FD_R1_M0_P0,radio_mic_handle,0,0};
}
static command receive_command(unsigned which) {
    return (command){CMD_RUN,which,FD_R1_M0_P0,radio_mic_handle,0,0};
}
static void acquire_and_run_probe(void) {
    packet(true,true,false,true);
    command_execute((command){.command=CMD_ACQUIRE});assert(status().state==FD_READY);
    unsigned applies=fake.apply_calls,starts=fake.starts,keys=fake.key_edges;
    command_execute(receive_command(CASE_CONFIRM_RX));assert(!status().qualified_rx_routes);
    command_execute(receive_command(CASE_RX_TONE));assert(!d.audio && fake.apply_calls==applies);
    command_execute(route_command(CMD_RUN));assert(status().state==FD_READY && !d.audio);
    command_execute(route_command(CMD_ARM));assert(status().state==FD_ARMED && d.audio);
    assert(fake.apply_calls==applies && fake.starts==starts && fake.key_edges==keys);
    unsigned snapshots=fake.snapshots;d.last_progress_ms=0;verify_observe_unlocked=true;
    cache_progress();verify_observe_unlocked=false;
    assert(fake.snapshots==snapshots+1 && d.progress.radio_owned && d.progress.audio_known && d.progress.audio.prepared);
    command wrong=route_command(CMD_RUN);wrong.selected=CASE_ROUTE_FIRST+FD_R0_M0_P1;
    command_execute(wrong);assert(status().state==FD_ARMED && fake.apply_calls==applies);
    command_execute(route_command(CMD_RUN));assert(status().state==FD_RUNNING);
    assert(fake.apply_calls==applies+1 && fake.starts==starts+1 && fake.key_edges==keys);
    packet(true,true,false,true); /* genuine newer packet after route application */
}
static void finish_actual_case(void) {
    fd_session s=status();assert(s.state==FD_WAIT_RX);
    unsigned finishes=fake.finishes,mixers=fake.mixer_restores,restores=fake.radio_restores;
    restore_case_iteration(&s); /* real join path, no post-clear RX yet */
    assert(d.audio && d.audio_joined && !d.audio_restored && !d.have_result);
    assert(fake.finishes==finishes+1 && fake.mixer_restores==mixers && fake.radio_restores==restores);
    packet(true,true,false,true);
    s=status();restore_case_iteration(&s); /* a request alone is not flush acknowledgement */
    assert(status().state==FD_WAIT_RX && !d.audio_restored && fake.radio_restores==restores);
    atomic_store(&d.audio->acknowledged,true);
    for(unsigned i=0;i<2500 && status().state!=FD_READY;++i){s=status();restore_case_iteration(&s);pause_short();}
    assert(status().state==FD_READY && !d.audio && d.have_result);
    assert(fake.mixer_restores==mixers+1 && fake.radio_restores==restores+1);
    char path[384],text[24000],expected[100];
    assert(snprintf(path,sizeof(path),"%s/coordinator.json",d.case_directory)<(int)sizeof(path));
    FILE *result=fopen(path,"rb");assert(result);size_t bytes=fread(text,1,sizeof(text)-1,result);
    assert(feof(result)&&!ferror(result));text[bytes]=0;fclose(result);
    snprintf(expected,sizeof(expected),"\"acquired_packet_sequence\":%llu,",(unsigned long long)d.radio.acquired_sequence);
    assert(strstr(text,expected));
    snprintf(expected,sizeof(expected),"\"command_clear_packet_sequence\":%llu,",(unsigned long long)d.radio.clear_sequence);
    assert(strstr(text,expected));
    assert(strstr(text,"\"radio_at_result_export\":"));
}
static void complete_scenario(fd_stop_reason requested,bool known,bool fresh,bool audio_failed,bool expect_unlock) {
    acquire_and_run_probe();
    /* Serialize with the real monitor so its first observation cannot consume
     * the intent before these production-callback assertions examine it. */
    pthread_mutex_lock(&d.controller.mutex);
    if(requested==FD_STOP_OPERATOR)atomic_store(&d.stop,true);
    else {atomic_store(&d.audio->stopping,true);atomic_store(&d.audio->failed,audio_failed);}
    packet(known,fresh,false,false);
    fd_observation seen={false,false,true,UINT64_MAX};
    assert(!observe_radio(NULL,&seen));
    assert(seen.known==known && seen.fresh==fresh && !seen.tx && seen.sequence!=UINT64_MAX);
    fd_stop_reason expected=(!known||!fresh)?FD_STOP_OBSERVATION:audio_failed?FD_STOP_AUDIO:requested;
    assert(d.observed_cause==expected);
    pthread_mutex_unlock(&d.controller.mutex);
    wait_state(FD_WAIT_RX);assert(status().stop_reason==expected);
    atomic_store(&d.stop,false); /* the production coordinator consumes the Stop flag */
    /* Stop intent/audio completion must no longer mask recovery observations. */
    packet(true,true,false,false);
    pthread_mutex_lock(&d.controller.mutex);
    assert(observe_radio(NULL,&seen) && seen.known && seen.fresh && !seen.tx && d.observed_cause==FD_STOP_NONE);
    pthread_mutex_unlock(&d.controller.mutex);
    finish_actual_case();assert(d.last_stop_reason==expected);
    pthread_mutex_lock(&d.controller.mutex);
    assert(d.controller.completed_probe==(expect_unlock?FD_R1_M0_P0:-1));
    pthread_mutex_unlock(&d.controller.mutex);
    command_execute(receive_command(CASE_CONFIRM_RX));
    unsigned bit=1u<<FD_R1_M0_P0;
    assert(!!(status().qualified_rx_routes&bit)==expect_unlock);
    assert(!!(d.rx_qualified[radio_mic_handle][0][0]&bit)==expect_unlock);
    if(expect_unlock){
        unsigned applies=fake.apply_calls;
        command_execute(receive_command(CASE_RX_TONE));assert(status().state==FD_RUNNING && fake.apply_calls==applies+1);
        assert(fd_controller_stop_with_reason(&d.controller,FD_STOP_OPERATOR));
        finish_actual_case();
    }
    assert(fd_controller_release(&d.controller));assert(status().state==FD_IDLE && !fake.owned);
}
static void remove_tree(const char *path) {
    DIR *directory=opendir(path);assert(directory);struct dirent *entry;
    while((entry=readdir(directory))){if(!strcmp(entry->d_name,".")||!strcmp(entry->d_name,".."))continue;
        char child[512];assert(snprintf(child,sizeof(child),"%s/%s",path,entry->d_name)<(int)sizeof(child));
        struct stat st;assert(!lstat(child,&st));if(S_ISDIR(st.st_mode))remove_tree(child);else assert(!unlink(child));}
    closedir(directory);assert(!rmdir(path));
}
int main(void) {
    fd_controller_backend backend={.acquire_snapshot=acquire_snapshot,.release=release_radio,.observe=observe_radio,
        .tx_permitted=tx_permitted,.start=start_case,.quiesce=clear_case,.audio_quiescent=audio_quiet,
        .restore_if_safe=restore_radio,.abort_reason=observed_cause};
    assert(fd_controller_init(&d.controller,&backend,NULL));d.controller_ready=true;
    complete_scenario(FD_STOP_OPERATOR,true,true,false,true);
    complete_scenario(FD_STOP_NATURAL,true,true,false,true);
    complete_scenario(FD_STOP_OPERATOR,false,false,false,false);
    complete_scenario(FD_STOP_NATURAL,true,false,false,false);
    complete_scenario(FD_STOP_NATURAL,true,true,true,false);
    assert(fake.key_edges==0);
    command_execute((command){.command=CMD_ACQUIRE});assert(status().state==FD_READY);
    command fixed={CMD_ARM,CASE_MODEM_TX,FD_R1_M1_P0,radio_mic_handle,0,0};
    unsigned applies=fake.apply_calls;
    command_execute(fixed);assert(status().state==FD_READY&&!d.audio&&fake.apply_calls==applies);
    /* A known-file delivery probe must remain usable when microphone capture
     * is unresolved, but never without the exact-profile Stop qualification. */
    d.tx_qualified[radio_mic_handle][0][0]=0;
    d.stop_confirmed[radio_mic_handle][0][0][FD_R1_M1_P0]=true;
    fixed.command=CMD_RUN;command_execute(fixed);assert(status().state==FD_READY&&fake.apply_calls==applies);
    fixed.command=CMD_ARM;command_execute(fixed);
    assert(status().state==FD_ARMED&&fake.apply_calls==applies&&d.audio->config.kind==FD_NATIVE_MODEM_TX);
    assert(d.audio->config.duration_ms==8000&&d.audio->config.excerpt_ms==8000);
    fixed.command=CMD_RUN;command_execute(fixed);assert(status().state==FD_RUNNING&&fake.key_edges==1);
    packet(true,true,true,true);d.seen_tx=true;
    assert(fd_controller_stop(&d.controller));finish_actual_case();
    assert(fd_controller_release(&d.controller));
    assert(fd_controller_close(&d.controller));assert(fake.key_edges==1);
    assert(fake.starts==fake.destroyed && fake.finishes==fake.destroyed && fake.mixer_restores==fake.destroyed);
    if(d.events)assert(!fclose(d.events));assert(d.directory[0]);remove_tree(d.directory);
    pthread_mutex_destroy(&fake.mutex);
    puts("PASS: production coordinator Acquire/Arm/Run, benign-stop physical observations, exact cleanup and RX unlock; bad observations/audio cannot unlock");
}
