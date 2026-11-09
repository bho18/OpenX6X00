// SPDX-License-Identifier: LGPL-2.1-or-later
/* Actual production radio binding, hardware stub, control lease and packet
 * tracker. Audio and GUI settings are deterministic endpoints here; the
 * assembled host fixture separately exercises the real engine and PA device. */
#define ENABLE_FREEDV_VOICE 1
#define FREEDV_HOST_TEST_PROFILE 1
#define FREEDV_BUILD_ID "radio-binding-test"
#define hw_ptt_observe test_hw_ptt_observe
#define hw_native_voice_observe test_native_observe
#define hw_native_voice_apply test_native_apply
#define hw_native_voice_acquire test_native_acquire
#define hw_native_voice_renew test_native_renew
#define hw_native_voice_release test_native_release
#define hw_native_voice_capabilities test_native_capabilities
#include "src/radio.c"
#undef hw_ptt_observe
#undef hw_native_voice_observe
#undef hw_native_voice_apply
#undef hw_native_voice_acquire
#undef hw_native_voice_renew
#undef hw_native_voice_release
#undef hw_native_voice_capabilities
#include <assert.h>
#include <sys/file.h>
#include <sys/wait.h>
bool hw_ptt_observe(hw_ptt_source_t,hw_ptt_observation_t *);
bool hw_native_voice_observe(hw_native_voice_state_t *);
bool hw_native_voice_apply(uint64_t,uint64_t,hw_native_voice_route_t);
bool hw_native_voice_acquire(uint64_t);
bool hw_native_voice_renew(uint64_t);
bool hw_native_voice_release(uint64_t);
hw_native_voice_capabilities_t hw_native_voice_capabilities(void);
static unsigned native_fault,removed_capabilities,native_tx_requests;
static bool inject_native,inject_acquire,release_press,renew_no_apply,claim_tx_race;
static atomic_bool pause_native_read,native_read_paused;
static hw_native_voice_state_t last_native_read,old_native_ack;
hw_native_voice_capabilities_t test_native_capabilities(void) {
    hw_native_voice_capabilities_t c=hw_native_voice_capabilities();c.supported&=~removed_capabilities;return c;
}
bool test_native_observe(hw_native_voice_state_t *out) {
    if(atomic_exchange(&pause_native_read,false)){
        atomic_store(&native_read_paused,true);
        while(atomic_load(&native_read_paused))usleep(1000);
    }
    bool ok=hw_native_voice_observe(out);
    if(ok && inject_native)switch(native_fault){
    case 1:*out=old_native_ack;break; /* transport accepted, only cached old state */
    case 2:--out->route_generation;break;
    case 3:out->monotonic_ms-=HW_NATIVE_ACK_MAX_AGE_MS+1;break;
    case 4:++out->lease_token;break;
    case 5:++out->boot_id;break;
    case 6:out->contract_id="unmatched MCU contract";break;
    case 7:out->physical_keying_inhibited=false;break;
    case 8:out->autonomous_tx_inhibited=false;break;
    case 9:out->direct_mic_rf_inhibited=false;break;
    case 10:out->raw_rx_speaker_inhibited=false;break;
    case 11:out->lease_deadline_ms=get_time();break;
    case 12:out->monotonic_ms=get_time()+1000;break;
    case 13:out->sequence=old_native_ack.sequence;break; /* changed payload, same sequence */
    case 14:out->valid=false;break;
    case 15:out->monotonic_ms=old_native_ack.monotonic_ms-1;break;
    case 16:out->sequence=old_native_ack.sequence-1;break;
    default:break;
    }
    last_native_read=*out;return ok;
}
bool test_native_apply(uint64_t token,uint64_t generation,hw_native_voice_route_t route) {
    old_native_ack=last_native_read;
    bool ok=hw_native_voice_apply(token,generation,route);
    if(route==HW_NATIVE_ROUTE_TX)++native_tx_requests;
    inject_native=native_fault!=0;return ok;
}
bool test_native_acquire(uint64_t token) {
    old_native_ack=last_native_read;
    if(claim_tx_race)atomic_store(&state,RADIO_TX);
    bool ok=hw_native_voice_acquire(token);inject_native=inject_acquire;return ok;
}
bool test_native_renew(uint64_t token) { return renew_no_apply?true:hw_native_voice_renew(token); }
bool test_native_release(uint64_t token) {
    if(release_press)hw_stub_ptt_set(HW_PTT_SOURCE_HAND,true);
    return hw_native_voice_release(token);
}
static unsigned bad_hand_ptt,hand_observations,release_at_observation;
bool test_hw_ptt_observe(hw_ptt_source_t source,hw_ptt_observation_t *out){
    if(source==HW_PTT_SOURCE_HAND && ++hand_observations==release_at_observation)hw_stub_ptt_set(source,false);
    bool ok=hw_ptt_observe(source,out);
    if(source==HW_PTT_SOURCE_HAND){
        if(bad_hand_ptt==1)out->valid=false;
        if(bad_hand_ptt==2)out->monotonic_ms-=FD_RADIO_PACKET_MAX_AGE_MS+1;
        if(bad_hand_ptt==3)out->monotonic_ms+=FD_RADIO_PACKET_MAX_AGE_MS+1;
    }
    return ok;
}

#define PARAM(name,value) static int32_t name##_value=value;ParamInt *name=(ParamInt *)&name##_value
PARAM(cfg_band_current_vfo,0);PARAM(cfg_band_id,7);PARAM(cfg_band_split,0);
PARAM(cfg_band_vfoa_freq,14100000);PARAM(cfg_band_vfob_freq,14100000);PARAM(cfg_band_if_shift,80);
PARAM(cfg_band_vfoa_mode,radio_mode_lsb);PARAM(cfg_band_vfob_mode,radio_mode_usb_dig);
PARAM(cfg_band_vfoa_agc,radio_agc_fast);PARAM(cfg_band_vfob_agc,radio_agc_slow);
PARAM(cfg_cw_decoder,0);
PARAM(cfg_band_vfoa_pre,0);PARAM(cfg_band_vfob_pre,1);PARAM(cfg_band_vfoa_att,1);PARAM(cfg_band_vfob_att,0);
PARAM(cfg_rfgain,64);PARAM(cfg_squelch,5);
PARAM(cfg_rit,12);PARAM(cfg_xit,24);PARAM(cfg_key_train,0);PARAM(cfg_nr,1);PARAM(cfg_nb,1);
PARAM(cfg_dnf_auto,1);PARAM(cfg_dnf,1);PARAM(cfg_comp,4);PARAM(cfg_volume,25);
PARAM(cfg_moni,15);PARAM(cfg_speaker_off,0);PARAM(cfg_vox_on,0);PARAM(cfg_band_tx_shift,0);
static float fixture_power=5.0f;ParamFloat *cfg_pwr=(ParamFloat *)&fixture_power;
static int32_t frequency=14100000,filter_low=250,filter_high=2800;
ComputedParamInt *cfg_cur_agc=(ComputedParamInt *)&cfg_band_vfob_agc_value;
ComputedParamInt *cfg_cur_mode=(ComputedParamInt *)&cfg_band_vfob_mode_value;
ComputedParamInt *cfg_fg_freq=(ComputedParamInt *)&frequency;
ComputedParamInt *cfg_cur_filter_low=(ComputedParamInt *)&filter_low;
ComputedParamInt *cfg_cur_filter_high=(ComputedParamInt *)&filter_high;
int32_t param_i_get(const ParamInt *p){assert(p);return *(const int32_t *)p;}
int32_t cparam_i_get(const ComputedParamInt *p){assert(p);return *(const int32_t *)p;}
float param_f_get(const ParamFloat *p){assert(p==cfg_pwr);return fixture_power;}
void param_i_set(ParamInt *p,int32_t value){FD_WRITE_GUARD(return);*(int32_t *)p=value;}
bool cfg_is_tx_freq(int32_t hz){return hz>=1800000 && hz<=30000000;}
int32_t cfg_transverter_shift_for(int32_t hz){(void)hz;return 0;}
bool cfg_voice_apply(fd_voice_tuning *t){assert(fd_writer_voice_authorized());frequency=t->frequency;cfg_band_current_vfo_value=(int32_t)t->vfo;
    if(t->vfo)cfg_band_vfob_freq_value=t->frequency;else cfg_band_vfoa_freq_value=t->frequency;
    hw_set_i(HW_VFO_CUR,t->vfo);hw_vfo_set_freq((radio_vfo_t)t->vfo,t->frequency);return true;}
bool cfg_voice_resolve(fd_local_operation op,int32_t value,fd_voice_tuning *t){*t=(fd_voice_tuning){value,0,op,value};return true;}
uint64_t get_time(void){struct timespec t;clock_gettime(CLOCK_MONOTONIC,&t);return(uint64_t)t.tv_sec*1000+t.tv_nsec/1000000;}
bool scheduler_call(scheduler_fn_t fn,void *arg){fn(arg);return true;}
bool scheduler_put_urgent(scheduler_fn_t fn,void *arg,size_t size){(void)size;fn(arg);return true;}
bool recorder_is_on(void){return false;}
bool screen_rec_is_on(void){return false;}
bool audio_freedv_other_audio_busy(void){return false;}
bool dialog_diagnostic_busy(void){return false;}
int tci_server_clients(void){return 0;}
cw_encoder_state_t cw_encoder_state(void){return CW_ENCODER_IDLE;}
rtty_state_t rtty_get_state(void){return RTTY_OFF;}
bool freedv_mode_active(void){return atomic_load(&voice_radio.owned);}
bool freedv_mode_request(fd_local_operation op,int32_t value){(void)op;(void)value;return false;}
bool btaudio_speaker_muted(void){return false;}
void msg_update_text_fmt(const char *fmt,...){(void)fmt;}
void msg_schedule_text_fmt(const char *fmt,...){(void)fmt;}
#include "radio_policy_fixture.h"
static unsigned audio_opens,audio_closes,audio_turns,mixer_restores,mixer_applies;
static unsigned startup_scenario,fence_pulse;
static atomic_bool *startup_cancel;
static int audio_token,mixer_token;
static void (*audio_failure)(void *);static void *audio_failure_context;
void fd_voice_audio_on_error(fd_voice_audio *a,void (*handler)(void *),void *context){assert(a);audio_failure=handler;audio_failure_context=context;}
bool audio_freedv_main_quiet(bool quiet){(void)quiet;return true;}
fd_voice_audio *fd_voice_audio_open(const fd_qualified_profile *p){assert(p);++audio_opens;return(fd_voice_audio *)&audio_token;}
bool fd_voice_audio_fence(fd_voice_audio *a){
    assert(a);
    if(fence_pulse==1){hw_stub_ptt_set(HW_PTT_SOURCE_HAND,false);hw_stub_ptt_set(HW_PTT_SOURCE_HAND,true);}
    if(fence_pulse==2){assert(radio_voice_local_ptt(false,NULL));assert(radio_voice_local_ptt(true,NULL));}
    return true;
}
bool fd_voice_audio_begin(fd_voice_audio *a,fd_voice_direction d,uint64_t gen){assert(a && gen && d!=FD_VOICE_IDLE);++audio_turns;return true;}
bool fd_voice_audio_begin_guarded(fd_voice_audio *a,fd_voice_direction d,uint64_t gen,fd_voice_audio_admit_fn admit,void *context){
    assert(admit && admit(context));
    switch(startup_scenario){
    case 1:
        /* Entire release/re-press occurs between observations. */
        hw_stub_ptt_set(HW_PTT_SOURCE_HAND,false);
        hw_stub_ptt_set(HW_PTT_SOURCE_HAND,true);break;
    case 2:
        assert(radio_voice_local_ptt(false,NULL));assert(radio_voice_local_ptt(true,NULL));break;
    case 3:bad_hand_ptt=1;break;
    case 4:bad_hand_ptt=2;break;
    case 5:bad_hand_ptt=3;break;
    case 6:atomic_fetch_add(&swr_trips,1);break;
    case 7:assert(startup_cancel);atomic_store(startup_cancel,true);break;
    default:return admit(context) && fd_voice_audio_begin(a,d,gen);
    }
    assert(!admit(context));return false;
}
bool fd_voice_audio_finish(fd_voice_audio *a,uint64_t gen){return a && gen;}
int fd_voice_audio_drained(fd_voice_audio *a){return a?1:-1;}
void fd_voice_audio_cancel(fd_voice_audio *a){assert(a);}
bool fd_voice_audio_failed(fd_voice_audio *a){return !a;}
bool fd_voice_audio_microphone_active(fd_voice_audio *a){return a!=NULL;}
fd_voice_stats fd_voice_audio_observe(fd_voice_audio *a){assert(a);return(fd_voice_stats){0};}
void fd_voice_audio_close(fd_voice_audio *a){assert(a);++audio_closes;}
audio_freedv_mixer_snapshot_t *audio_freedv_mixer_snapshot(void){return(audio_freedv_mixer_snapshot_t *)&mixer_token;}
bool audio_freedv_mixer_restore(audio_freedv_mixer_snapshot_t *m){assert(m);++mixer_restores;return true;}
bool audio_freedv_mixer_levels(const long play[2],const long capture[2]){assert(play && capture);++mixer_applies;return true;}
void audio_freedv_mixer_snapshot_free(audio_freedv_mixer_snapshot_t *m){assert(m);}
static void observed(bool tx){uint64_t now=get_time();freedv_packet_tx_note(&packet_tx_tracker,tx,now);atomic_store(&data_time,now);atomic_store(&state,tx?RADIO_TX:RADIO_RX);}
static bool start_turn(const fd_voice_driver *driver,uint64_t generation){
    fd_voice_observation admission;radio_voice_observation(&admission);
    return driver->begin_transmit(driver->context,generation,&admission);
}
static hw_freedv_cached_snapshot_t snapshot(void){hw_freedv_cached_snapshot_t s;assert(hw_get_freedv_cached_snapshot(&s));return s;}
static void ordinary_tx_policy_cases(void){
    /* Exercise the production radio setters with the real base-age guard and
     * upstream amateur-band policy, while external endpoints stay mocked. */
    observed(false);assert(!radio_no_data());
    atomic_store(&data_time,0);assert(radio_no_data());
    assert(!radio_set_ptt(true));radio_set_modem(true);
    assert(!snapshot().route.ptt && !snapshot().route.modem);
    atomic_store(&data_time,get_time()-NO_DATA_MS-1);assert(radio_no_data());
    assert(!radio_set_ptt(true));radio_set_modem(true);
    assert(!snapshot().route.ptt && !snapshot().route.modem);
    observed(false);assert(radio_set_ptt(true));assert(snapshot().route.ptt);
    assert(radio_set_ptt(false));
    fixture_tx_block="Fixture WR12 not in bypass";
    assert(!radio_set_ptt(true));radio_set_modem(true);
    assert(!snapshot().route.ptt && !snapshot().route.modem);
    fixture_tx_block=NULL;
    frequency=cfg_band_vfoa_freq_value=cfg_band_vfob_freq_value=14500000;
    assert(cfg_is_tx_freq(frequency) && cfg_tx_refusal(frequency));
    assert(!radio_set_ptt(true));radio_set_modem(true);
    assert(!snapshot().route.ptt && !snapshot().route.modem);
    fixture_tx_outside_bands=true;assert(!cfg_tx_refusal(frequency));
    assert(radio_set_ptt(true) && snapshot().route.ptt);assert(radio_set_ptt(false));
    fixture_tx_outside_bands=false;
    frequency=cfg_band_vfoa_freq_value=cfg_band_vfob_freq_value=14100000;
    observed(false);radio_set_modem(false);
}
static fd_qualified_profile test_profile(void){return(fd_qualified_profile){.native_contract_id="host-native-v2",.rx_audio={1,1},.tx_audio={1,1},
    .id="binding fixture only",.hardware="host",.build_id=FREEDV_BUILD_ID,.baseband="stub",.control_version="stub",
    .evidence_sha256="0000000000000000000000000000000000000000000000000000000000000000",
    .local_mode=FD_PROFILE_USB_DIG,.upper_sideband=true,.sample_rate=44100,.capture_device="fake",.playback_device="fake",.mixer="fake",
    .rx_route=FD_ROUTE_RECORD,.tx_route=FD_ROUTE_MODEM|FD_ROUTE_PTT,.rx_low_hz=100,.rx_high_hz=3100,.tx_low_hz=100,.tx_high_hz=3100,.rx2_low_hz=0,.rx2_high_hz=3300,
    .agc=radio_agc_auto,.mic_gain=22,.line_gain=19,.monitor_gain=0,.power_tenths_watt=10,
    .fixed_tx_filter_attested=true,.fixed_eq_bypass_attested=true,.capabilities=FD_CAP_ALL,.complete_pass=true};}
struct route_race {uint64_t epoch;bool result;};
static void *racing_tx(void *context){struct route_race *r=context;r->result=voice_native_apply(HW_NATIVE_ROUTE_TX,r->epoch);return NULL;}
static void *racing_clear(void *context){fd_voice_driver *d=context;d->emergency_clear(d->context);return NULL;}
static atomic_bool concurrent_stop,concurrent_failed;
static void *handoff_ptt(void *unused){
    (void)unused;
    while(!atomic_load(&concurrent_stop)){
        /* At least one source is held throughout every handoff. */
        hw_stub_ptt_set(HW_PTT_SOURCE_RADIO,true);
        hw_stub_ptt_set(HW_PTT_SOURCE_HAND,false);
        hw_stub_ptt_set(HW_PTT_SOURCE_HAND,true);
        hw_stub_ptt_set(HW_PTT_SOURCE_RADIO,false);
    }
    return NULL;
}
static void *observe_route_changes(void *unused){
    (void)unused;
    while(!atomic_load(&concurrent_stop))
        if(!voice_native_healthy()){atomic_store(&concurrent_failed,true);break;}
    return NULL;
}
static void contract_failure_cases(void){
    /* Each deliberately unrecoverable control fault lives in a separate
     * process: the real driver must not silently reset its recovery latch. */
    fflush(NULL);
    for(unsigned scenario=1;scenario<=29;++scenario){
        pid_t child=fork();assert(child>=0);
        if(!child){
            fd_voice_driver d=radio_voice_driver();fd_qualified_profile p=test_profile();atomic_bool cancel=false;
            observed(false);hw_stub_ptt_set(HW_PTT_SOURCE_HAND,false);hw_stub_ptt_set(HW_PTT_SOURCE_RADIO,false);
            if(scenario==28){
                frequency=cfg_band_vfoa_freq_value=cfg_band_vfob_freq_value=14500000;
                hw_vfo_set_freq(RADIO_VFO_A,frequency);hw_vfo_set_freq(RADIO_VFO_B,frequency);
            }
            if(scenario==29){
                fixture_tx_player_busy=true;
                assert(!d.prepare(d.context,&p,&cancel));
                assert(!fd_writer_voice_owned() && !atomic_load(&voice_radio.owned));
            }else if(scenario<=16){
                native_fault=scenario;inject_acquire=true;
                assert(!d.prepare(d.context,&p,&cancel));
                assert(!fd_writer_voice_owned() && !atomic_load(&voice_radio.owned) && voice_radio.keypad_fd<0);
                assert(radio_voice_control_recovery_required() && !radio_voice_supported(&p));
            }else if(scenario==24){
                native_fault=1;inject_acquire=true;claim_tx_race=true;
                assert(!d.prepare(d.context,&p,&cancel));
                /* A non-RX state refuses reservation release: retain tracked
                 * ownership and require recovery, never silently strand it. */
                assert(fd_writer_voice_owned() && atomic_load(&voice_radio.owned));
                assert(radio_voice_control_recovery_required() && !d.close(d.context));
            }else if(scenario==22){
                bad_hand_ptt=1;assert(!d.prepare(d.context,&p,&cancel));
                assert(!fd_writer_voice_owned() && !radio_voice_control_recovery_required());
            }else{
                assert(d.prepare(d.context,&p,&cancel));
                assert(d.receive(d.context,1));
                if(scenario==17){
                    native_fault=2;assert(!d.receive(d.context,2));
                    assert(radio_voice_control_recovery_required());
                    uint64_t requests=atomic_load(&voice_radio.route_request);
                    for(unsigned i=0;i<3;++i)assert(d.maintain(d.context) && !d.restore(d.context) && !d.close(d.context));
                    assert(atomic_load(&voice_radio.route_request)==requests && fd_writer_voice_owned());
                }else if(scenario==18){
                    hw_stub_ptt_set(HW_PTT_SOURCE_HAND,true);observed(false);
                    assert(start_turn(&d,2) && d.transmit(d.context,2,&cancel));
                    hw_native_voice_state_t before,after;assert(hw_native_voice_observe(&before));
                    usleep((HW_NATIVE_LEASE_MAX_MS+20)*1000);
                    assert(hw_native_voice_observe(&after) && !after.lease_token && after.applied_route==HW_NATIVE_ROUTE_QUIET);
                    assert(after.physical_keying_inhibited && after.autonomous_tx_inhibited && !snapshot().route.modem && !snapshot().route.ptt);
                    assert(!d.maintain(d.context) && d.maintain(d.context) && radio_voice_control_recovery_required());
                    assert(!d.restore(d.context) && !d.close(d.context));
                }else if(scenario==19){
                    usleep(550000);renew_no_apply=true;
                    assert(!d.maintain(d.context) && radio_voice_control_recovery_required());
                }else if(scenario==20){
                    d.emergency_clear(d.context);observed(false);assert(!d.restore(d.context));observed(false);assert(d.restore(d.context));
                    release_press=true;assert(!d.close(d.context) && fd_writer_voice_owned() && !radio_voice_control_recovery_required());
                    hw_native_voice_state_t held;assert(hw_native_voice_observe(&held) && held.lease_token && held.physical_keying_inhibited);
                    release_press=false;hw_stub_ptt_set(HW_PTT_SOURCE_HAND,false);observed(false);
                    assert(d.close(d.context) && !fd_writer_voice_owned());
                }else if(scenario==21){
                    hw_stub_ptt_set(HW_PTT_SOURCE_HAND,true);observed(false);assert(start_turn(&d,2));
                    unsigned before=native_tx_requests;pthread_t tx,clear;
                    struct route_race race={atomic_load(&voice_radio.epoch),true};
                    atomic_store(&pause_native_read,true);assert(!pthread_create(&tx,NULL,racing_tx,&race));
                    while(!atomic_load(&native_read_paused))usleep(1000);
                    assert(!pthread_create(&clear,NULL,racing_clear,&d));
                    while(!atomic_load(&voice_radio.cancelled))usleep(1000);
                    atomic_store(&native_read_paused,false);pthread_join(tx,NULL);pthread_join(clear,NULL);
                    assert(!race.result && native_tx_requests==before && !snapshot().route.modem && !snapshot().route.ptt);
                    hw_native_voice_state_t quiet;assert(hw_native_voice_observe(&quiet) && quiet.applied_route==HW_NATIVE_ROUTE_QUIET);
                }else if(scenario==23){
                    d.emergency_clear(d.context);
                    uint64_t token=atomic_load(&voice_radio.lease_token);
                    hw_stub_ptt_set(HW_PTT_SOURCE_HAND,true);
                    pthread_t input;assert(!pthread_create(&input,NULL,handoff_ptt,NULL));
                    for(unsigned i=0;i<10000;++i){
                        assert(!hw_native_voice_release(token));
                        if(!(i%100))assert(hw_native_voice_renew(token));
                    }
                    atomic_store(&concurrent_stop,true);pthread_join(input,NULL);
                    hw_stub_ptt_set(HW_PTT_SOURCE_RADIO,false);hw_stub_ptt_set(HW_PTT_SOURCE_HAND,false);
                    assert(hw_native_voice_release(token));
                }else if(scenario>=26){
                    /* Policy changes after a prepared RX session cannot be
                     * bypassed by the native FreeDV route/audio admission. */
                    hw_stub_ptt_set(HW_PTT_SOURCE_HAND,true);observed(false);
                    if(scenario==26)atomic_store(&data_time,0);
                    if(scenario==27)fixture_tx_block="Fixture WR12 not in bypass";
                    unsigned before=native_tx_requests;
                    bool started=start_turn(&d,2);
                    assert(!(started && d.transmit(d.context,2,&cancel)));
                    assert(native_tx_requests==before && !snapshot().route.modem && !snapshot().route.ptt);
                }else if(scenario==25){
                    uint64_t epoch=atomic_load(&voice_radio.epoch);
                    pthread_t observer;assert(!pthread_create(&observer,NULL,observe_route_changes,NULL));
                    for(unsigned i=0;i<500;++i){
                        assert(voice_native_apply(HW_NATIVE_ROUTE_QUIET,epoch));
                        assert(voice_native_apply(HW_NATIVE_ROUTE_RX,epoch));
                        assert(d.maintain(d.context));
                    }
                    atomic_store(&concurrent_stop,true);pthread_join(observer,NULL);
                    assert(!atomic_load(&concurrent_failed) && !radio_voice_control_recovery_required());
                }
            }
            _exit(0);
        }
        int status;assert(waitpid(child,&status,0)==child);
        if(!WIFEXITED(status) || WEXITSTATUS(status)!=0){fprintf(stderr,"native contract scenario %u failed\n",scenario);assert(0);}
    }
    puts("PASS: native contract rejects invalid ACKs and leases; failed claims retain truthful ownership; TX cannot overtake QUIET; coherent PTT release, concurrent observations and upstream TX policy preserve the control contract");
}
int main(void){
    ctl_init();assert(hw_init());hw_start();observed(false);
    /* Exercise the real ordinary timing observer with the decoder preference,
     * retaining desired AUTO rather than overwriting the saved setting. */
    cfg_band_vfob_agc_value=radio_agc_auto;
    for(int decoder=0;decoder<2;decoder++){
        cfg_cw_decoder_value=decoder;
        cfg_band_vfob_mode_value=radio_mode_cw;update_agc_time(NULL,NULL);
        assert(hw_get_i(HW_AGC_TIME)==100 && cfg_band_vfob_agc_value==radio_agc_auto);
        cfg_band_vfob_mode_value=radio_mode_cwr;update_agc_time(NULL,NULL);
        assert(hw_get_i(HW_AGC_TIME)==100);
        cfg_band_vfob_mode_value=radio_mode_am;update_agc_time(NULL,NULL);
        assert(hw_get_i(HW_AGC_TIME)==1000);
    }
    cfg_cw_decoder_value=0;cfg_band_vfob_agc_value=radio_agc_slow;cfg_band_vfob_mode_value=radio_mode_usb_dig;
    hw_set_i(HW_HMIC,31);hw_set_i(HW_IMIC,17);hw_set_i(HW_MIC,radio_mic_auto);hw_set_i(HW_VOX,1);
    hw_set_i(HW_RECORD,0);hw_set_i(HW_LINE_IN,7);hw_set_i(HW_LINE_OUT,9);hw_set_i(HW_MONI,15);hw_set_f(HW_TXPWR_F,5);
    hw_vfo_set_freq(RADIO_VFO_A,frequency);hw_vfo_set_mode(RADIO_VFO_A,radio_mode_lsb);hw_vfo_set_mode(RADIO_VFO_B,radio_mode_usb_dig);
    ordinary_tx_policy_cases();
    fd_qualified_profile p=test_profile();assert(radio_voice_supported(&p));
    removed_capabilities=HW_NATIVE_APPLIED_ROUTE_ACK;assert(!radio_voice_supported(&p));
    removed_capabilities=HW_NATIVE_FAIL_CLOSED_LEASE;assert(!radio_voice_supported(&p));removed_capabilities=0;
    p.native_contract_id="unmatched";assert(!radio_voice_supported(&p));p.native_contract_id="host-native-v2";
    p.fixed_eq_bypass_attested=false;assert(!radio_voice_supported(&p));p.fixed_eq_bypass_attested=true;
    p.capabilities&=~FD_CAP_HAND_PTT_RELEASE;assert(!radio_voice_supported(&p));p.capabilities=FD_CAP_ALL;
    fd_voice_driver d=radio_voice_driver();atomic_bool cancel=false;
    assert(d.prepare(d.context,&p,&cancel));assert(fd_writer_voice_owned());
    assert(!fd_writer_enter());assert(!ctl_claim(CTL_TCI,1,"remote",true));
    hw_freedv_cached_snapshot_t before=snapshot();assert(before.hand_mic_gain==31 && before.internal_mic_gain==17 && !before.route.record && !before.route.ptt);
    /* Both settings and raw hardware entry points reject independent writers. */
    param_i_set(cfg_band_id,99);assert(cfg_band_id_value==7);
    /* A TUNE preparation cannot change its SWR exception or cap inside the
     * established FreeDV lease, even when the low-level backend rejects it. */
    tune_guard_t old_tune=tune_guard;float old_cap=tune_cap;
    radio_set_tune(1.0f,1000);radio_set_tx_mode(radio_mode_lsb);
    assert(tune_guard.pwr_w==old_tune.pwr_w && tune_guard.start_ms==old_tune.start_ms &&
           tune_guard.limit_ms==old_tune.limit_ms && tune_cap==old_cap);
    hw_vfo_set_freq(RADIO_VFO_A,7000000);hw_set_i(HW_PTT,true);hw_set_i(HW_HMIC,99);
    assert(snapshot().vfo_a_hz==frequency && !snapshot().route.ptt && snapshot().hand_mic_gain==31);
    assert(d.fence(d.context) && d.receive(d.context,1));
    assert(hw_vfo_mode_matches(RADIO_VFO_A,radio_mode_usb_dig));
    assert(hw_get_i(HW_IF_SHIFT_HZ)==0 && cfg_band_if_shift_value==80);
    uint64_t gen=1;
    for(unsigned turn=0;turn<100;++turn){
        hw_stub_ptt_set(HW_PTT_SOURCE_HAND,true);observed(true); /* Aggregate observation is independent of raw physical level. */
        assert(start_turn(&d,++gen) && d.fence(d.context) && d.transmit(d.context,gen,&cancel));
        assert(snapshot().route.modem && snapshot().hand_mic_gain==22);
        hw_stub_ptt_set(HW_PTT_SOURCE_HAND,false);
        fd_voice_observation observation;radio_voice_observation(&observation);
        assert(observation.ptt[1].valid && !observation.ptt[1].pressed && observation.tx);
        assert(d.finish(d.context,gen) && d.drained(d.context)==1);
        d.emergency_clear(d.context);assert(!snapshot().route.modem && !snapshot().route.ptt && snapshot().hand_mic_gain==22);
        observed(false);assert(d.fence(d.context) && d.receive(d.context,++gen));
        assert(audio_opens==1 && !audio_closes);
    }
    unsigned extra_audio_turns=0;
    for(startup_scenario=1;startup_scenario<=7;++startup_scenario){
        hw_stub_ptt_set(startup_scenario==2?HW_PTT_SOURCE_RADIO:HW_PTT_SOURCE_HAND,true);observed(true);
        unsigned before=audio_turns,old_trips=atomic_load(&swr_trips);startup_cancel=&cancel;
        assert(start_turn(&d,++gen) && d.fence(d.context) && !d.transmit(d.context,gen,&cancel));
        assert(audio_turns==before && !snapshot().route.modem && !snapshot().route.ptt);
        fd_abort_reason expected=startup_scenario<=2?FD_ABORT_START_RELEASE:
            startup_scenario<=5?FD_ABORT_HEALTH:startup_scenario==6?FD_ABORT_PROTECTION:FD_ABORT_NONE;
        assert(d.startup_abort_reason(d.context)==expected);
        bad_hand_ptt=0;atomic_store(&swr_trips,old_trips);atomic_store(&cancel,false);
        hw_stub_ptt_set(HW_PTT_SOURCE_HAND,false);hw_stub_ptt_set(HW_PTT_SOURCE_RADIO,false);observed(false);
        assert(d.fence(d.context) && d.receive(d.context,++gen));++extra_audio_turns;
    }
    startup_scenario=0;startup_cancel=NULL;
    for(fence_pulse=1;fence_pulse<=2;++fence_pulse){
        hw_stub_ptt_set(fence_pulse==1?HW_PTT_SOURCE_HAND:HW_PTT_SOURCE_RADIO,true);observed(false);
        unsigned before=audio_turns;
        assert(start_turn(&d,++gen) && d.fence(d.context) && !d.transmit(d.context,gen,&cancel));
        assert(audio_turns==before && d.startup_abort_reason(d.context)==FD_ABORT_START_RELEASE);
        assert(!snapshot().route.modem && !snapshot().route.ptt);
        unsigned pulse=fence_pulse;fence_pulse=0;
        hw_stub_ptt_set(HW_PTT_SOURCE_HAND,false);hw_stub_ptt_set(HW_PTT_SOURCE_RADIO,false);observed(false);
        assert(d.fence(d.context) && d.receive(d.context,++gen));++extra_audio_turns;fence_pulse=pulse;
    }
    fence_pulse=0;
    /* A release during route preparation must be rechecked before keying. */
    hw_stub_ptt_set(HW_PTT_SOURCE_HAND,true);observed(false);
    assert(start_turn(&d,++gen));hand_observations=0;release_at_observation=3;
    unsigned before_start=audio_turns;
    assert(d.fence(d.context) && !d.transmit(d.context,gen,&cancel));
    assert(audio_turns==before_start && !snapshot().route.modem && !snapshot().route.ptt);
    assert(d.startup_abort_reason(d.context)==FD_ABORT_START_RELEASE);release_at_observation=0;
    observed(false);assert(d.fence(d.context) && d.receive(d.context,++gen));++extra_audio_turns;
    puts("PASS: actual radio startup retains hand/panel release-repress through the pre-transmit fence and audio startup; stale/unknown/future PTT, protection and Stop reject audio admission");
    fd_voice_tuning tune={7177100,1,FD_LOCAL_FREQUENCY,7177100};
    assert(d.fence(d.context) && d.retune(d.context,&tune,&p) && d.receive(d.context,++gen));
    assert(frequency==7177100 && cfg_band_current_vfo_value==1);
    hw_stub_ptt_set(HW_PTT_SOURCE_HAND,true);observed(true);
    assert(start_turn(&d,++gen) && d.fence(d.context) && d.transmit(d.context,gen,&cancel));
    assert(audio_failure);audio_failure(audio_failure_context);
    assert(!snapshot().route.modem && !snapshot().route.ptt && snapshot().hand_mic_gain==22);
    assert(!d.receive(d.context,++gen)); /* device faults cannot silently reopen RX */
    hw_stub_ptt_set(HW_PTT_SOURCE_HAND,false);observed(false);
    /* Exit holds ownership while either physical control is held, then waits
     * for an additional RX packet after restoring the current settings. */
    hw_stub_ptt_set(HW_PTT_SOURCE_RADIO,true);d.emergency_clear(d.context);
    assert(!d.restore(d.context) && fd_writer_voice_owned());
    hw_stub_ptt_set(HW_PTT_SOURCE_RADIO,false);observed(false);
    assert(!d.restore(d.context));observed(false);assert(d.restore(d.context));
    assert(snapshot().vfo_b_hz==7177020 && snapshot().active_vfo==1 && cfg_band_if_shift_value==80);
    assert(hw_vfo_mode_matches(RADIO_VFO_A,radio_mode_lsb) && hw_vfo_mode_matches(RADIO_VFO_B,radio_mode_usb_dig));
    assert(snapshot().rx_filter_low_hz==250 && snapshot().rx_filter_high_hz==2800);
    assert(snapshot().hand_mic_gain==31 && snapshot().internal_mic_gain==17 && snapshot().microphone==radio_mic_auto);
    assert(!snapshot().route.modem && !snapshot().route.ptt && cfg_nr_value==1 && cfg_comp_value==4);
    assert(d.close(d.context) && !fd_writer_voice_owned() && audio_closes==1 && mixer_restores==1 && mixer_applies==1);
    assert(frequency==7177100 && cfg_band_current_vfo_value==1 && audio_turns==203+extra_audio_turns);
    puts("PASS: production binding, 100 retained turns, independent PTT, raw/settings/remote guards, RX tune and held exit/current-VFO restoration");
    /* Candidate admission reaches this same driver without inventing a
     * complete production pass, and rechecks physical release on claim. */
    const fd_qualified_profile *candidate=radio_voice_validation_profile();
    assert(candidate && !candidate->complete_pass && !candidate->capabilities && !candidate->evidence_sha256);
    assert(!radio_voice_supported(candidate));
    fd_validation_admission admission;const char *reason=NULL;
    assert(radio_voice_validation_status(candidate,&admission,&reason) && !reason);
    fd_validation_admission bad=admission;bad.keying_owned=false;
    assert(!radio_voice_claim_validation(candidate,&bad) && !fd_writer_voice_owned());
    bad=admission;bad.control_version="unmatched";
    assert(!radio_voice_claim_validation(candidate,&bad) && !fd_writer_voice_owned());
    d=radio_voice_driver();hw_stub_ptt_set(HW_PTT_SOURCE_HAND,true);observed(false);
    assert(!radio_voice_claim_validation(candidate,&admission) && !fd_writer_voice_owned());
    hw_stub_ptt_set(HW_PTT_SOURCE_HAND,false);observed(false);
    assert(radio_voice_claim_validation(candidate,&admission));
    assert(d.prepare(d.context,candidate,&cancel));
    assert(d.fence(d.context) && d.receive(d.context,++gen));
    d.emergency_clear(d.context);observed(false);
    assert(!d.restore(d.context));observed(false);assert(d.restore(d.context));
    assert(d.close(d.context) && !fd_writer_voice_owned());
    puts("PASS: unqualified candidate uses native binding; identities, authority and held-at-entry stay enforced");
    contract_failure_cases();
}
