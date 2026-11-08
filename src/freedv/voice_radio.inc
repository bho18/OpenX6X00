/* SPDX-License-Identifier: LGPL-2.1-or-later
 * Production voice binding, included by radio.c to share its control lock,
 * packet tracker and protection state. It does not run diagnostic cases.
 * Profile overrides touch hardware only. Normal VFO/SSB/DIGI preferences remain
 * the source for restoration, so visiting a band cannot persist an override. */
#include "voice_radio.h"
#include "voice_audio.h"
#include "native_trace.h"
#include "../cfg/voice_settings.h"
#include "../tx_player.h"
#include <fcntl.h>
#include <sys/ioctl.h>
#include <linux/input.h>
#include <sys/random.h>

typedef struct {
    pthread_mutex_t audio_mutex, control_mutex, observation_mutex;
    hw_native_voice_state_t last_control_observation;
    fd_voice_audio *audio;
    audio_freedv_mixer_snapshot_t *mixer;
    const fd_qualified_profile *profile;
    hw_freedv_cached_snapshot_t saved;
    atomic_bool owned, cancelled, failed, restored;
    atomic_uint_fast64_t epoch;
    unsigned swr_at_entry, errors_at_entry;
    uint64_t restore_sequence;
    bool snapshot_valid, main_quiet, validation;
    atomic_int keypad_fd;
    atomic_uint_fast64_t radio_ptt_sequence;
    atomic_uint_fast64_t release_epoch;
    atomic_uint startup_abort_reason;
    atomic_bool control_failed, control_pending, recovery_required, control_releasing;
    atomic_uint_fast64_t lease_token, boot_id, route_generation, route_request;
    atomic_uint_fast64_t pending_generation, renew_after_ms;
    atomic_uint applied_route, pending_route;
    fd_voice_observation starting_observation;
    uint64_t starting_generation, starting_epoch, starting_release_epoch;
    bool starting_valid;
    /* Bounded context observations; restoration uses untouched preferences,
     * not an evictable diagnostic history. Exhaustion cannot terminate audio. */
    struct { int32_t band, mode_a, mode_b, filter_low, filter_high; unsigned vfo; } contexts[64];
    unsigned context_count, context_discarded;
} voice_radio_context;
static voice_radio_context voice_radio={.audio_mutex=PTHREAD_MUTEX_INITIALIZER,
    .control_mutex=PTHREAD_MUTEX_INITIALIZER,.observation_mutex=PTHREAD_MUTEX_INITIALIZER,.keypad_fd=-1};

static void voice_pause(void) { struct timespec t={0,5000000};nanosleep(&t,NULL); }
/* Serialize observations so an older concurrent reply cannot overwrite the
 * sequence/payload fence. No writes or lease renewals occur here. */
static bool voice_native_read(hw_native_voice_state_t *s) {
    pthread_mutex_lock(&voice_radio.observation_mutex);
    bool ok=hw_native_voice_observe(s);uint64_t time=get_time();
    hw_native_voice_state_t *old=&voice_radio.last_control_observation;
    ok=ok && s->valid && s->boot_id && s->sequence && s->monotonic_ms &&
        s->monotonic_ms<=time && time-s->monotonic_ms<=HW_NATIVE_ACK_MAX_AGE_MS;
    if(ok && old->valid && old->boot_id==s->boot_id){
        ok=s->sequence>=old->sequence && s->monotonic_ms>=old->monotonic_ms;
        if(ok && s->sequence==old->sequence)ok=s->monotonic_ms==old->monotonic_ms &&
            s->lease_token==old->lease_token && s->lease_deadline_ms==old->lease_deadline_ms &&
            s->route_generation==old->route_generation && s->applied_route==old->applied_route &&
            s->physical_keying_inhibited==old->physical_keying_inhibited &&
            s->autonomous_tx_inhibited==old->autonomous_tx_inhibited &&
            s->direct_mic_rf_inhibited==old->direct_mic_rf_inhibited &&
            s->raw_rx_speaker_inhibited==old->raw_rx_speaker_inhibited &&
            s->contract_id && old->contract_id && !strcmp(s->contract_id,old->contract_id);
    }
    if(ok)*old=*s;
    pthread_mutex_unlock(&voice_radio.observation_mutex);return ok;
}
/* Every accepted state is an MCU-applied acknowledgement, never a cached
 * command. The stock backends implement none of this protocol and stay blocked. */
static bool voice_native_state_valid(const hw_native_voice_state_t *s) {
    uint64_t time=get_time();const fd_qualified_profile *p=voice_radio.profile;
    return s && p && p->native_contract_id && s->valid && s->contract_id &&
        !strcmp(s->contract_id,p->native_contract_id) && s->boot_id &&
        s->boot_id==atomic_load(&voice_radio.boot_id) && s->sequence && s->monotonic_ms &&
        s->monotonic_ms<=time && time-s->monotonic_ms<=HW_NATIVE_ACK_MAX_AGE_MS &&
        s->lease_token && s->lease_token==atomic_load(&voice_radio.lease_token) &&
        s->lease_deadline_ms>time && s->lease_deadline_ms>s->monotonic_ms &&
        s->lease_deadline_ms-s->monotonic_ms<=HW_NATIVE_LEASE_MAX_MS &&
        s->physical_keying_inhibited && s->autonomous_tx_inhibited &&
        s->direct_mic_rf_inhibited && s->raw_rx_speaker_inhibited;
}
static void voice_native_fail(void) {
    atomic_store(&voice_radio.control_failed,true);atomic_store(&voice_radio.recovery_required,true);
}
bool radio_voice_control_recovery_required(void) { return atomic_load(&voice_radio.recovery_required); }
/* Caller holds control_mutex across the observation and comparison, so a
 * successful concurrent route change cannot invalidate this same snapshot. */
static bool voice_native_healthy_locked(void) {
    hw_native_voice_state_t s={0};
    if(atomic_load(&voice_radio.control_failed))return false;
    if(!voice_native_read(&s) || !voice_native_state_valid(&s)){
        if(!atomic_load(&voice_radio.control_releasing))voice_native_fail();return false;
    }
    bool applied=s.route_generation==atomic_load(&voice_radio.route_generation) &&
        s.applied_route==atomic_load(&voice_radio.applied_route);
    bool pending=atomic_load(&voice_radio.control_pending) &&
        s.route_generation==atomic_load(&voice_radio.pending_generation) &&
        s.applied_route==atomic_load(&voice_radio.pending_route);
    if(!applied && !pending && !atomic_load(&voice_radio.control_releasing))voice_native_fail();
    return applied || pending;
}
static bool voice_native_healthy(void) {
    pthread_mutex_lock(&voice_radio.control_mutex);
    bool ok=voice_native_healthy_locked();
    pthread_mutex_unlock(&voice_radio.control_mutex);return ok;
}
static bool voice_native_ack(const char *operation,uint64_t generation,hw_native_voice_route_t route,
        uint64_t after_sequence,uint64_t requested_ms,bool requested) {
    hw_native_voice_state_t s={0};
    bool read=voice_native_read(&s);
    bool ok=requested && read && voice_native_state_valid(&s) && s.sequence>after_sequence &&
        s.monotonic_ms>=requested_ms && s.route_generation==generation && s.applied_route==route;
    fd_native_trace_control(ok?operation:"ack_rejected",atomic_load(&voice_radio.lease_token),generation,route,read?&s:NULL);
    if(ok){atomic_store(&voice_radio.route_generation,generation);atomic_store(&voice_radio.applied_route,route);
        if(!generation)atomic_store(&voice_radio.renew_after_ms,s.monotonic_ms+(s.lease_deadline_ms-s.monotonic_ms)/2);}
    else voice_native_fail();
    atomic_store(&voice_radio.control_pending,false);
    return ok;
}
static bool voice_native_apply(hw_native_voice_route_t route,uint64_t expected_epoch) {
    pthread_mutex_lock(&voice_radio.control_mutex);
    uint64_t token=atomic_load(&voice_radio.lease_token);
    hw_native_voice_state_t before={0};bool observed=voice_native_read(&before);
    bool ok=token && (route==HW_NATIVE_ROUTE_QUIET ||
        (observed && voice_native_state_valid(&before) && !atomic_load(&voice_radio.control_failed) &&
         !atomic_load(&voice_radio.cancelled) && expected_epoch==atomic_load(&voice_radio.epoch)));
    if(ok){
        uint64_t generation=atomic_fetch_add(&voice_radio.route_request,1)+1,requested_ms=get_time();
        atomic_store(&voice_radio.pending_generation,generation);atomic_store(&voice_radio.pending_route,route);
        atomic_store(&voice_radio.control_pending,true);
        fd_native_trace_control("route_request",token,generation,route,NULL);
        /* QUIET and TX share this ordering lock. A clear latches cancellation
         * before waiting; a superseded TX can never allocate a later request
         * after the clear's QUIET acknowledgement. */
        ok=(route==HW_NATIVE_ROUTE_QUIET || (!atomic_load(&voice_radio.cancelled) &&
            expected_epoch==atomic_load(&voice_radio.epoch))) && hw_native_voice_apply(token,generation,route);
        ok=voice_native_ack("route_applied",generation,route,observed?before.sequence:0,requested_ms,ok);
    }
    pthread_mutex_unlock(&voice_radio.control_mutex);return ok;
}
static bool voice_maintain(void *unused) {
    (void)unused;
    if(!atomic_load(&voice_radio.owned) || atomic_load(&voice_radio.control_failed))return true;
    /* Only the coordinator renews; a stalled coordinator cannot be kept alive
     * by GUI observations, audio callbacks, or unrelated control traffic. */
    pthread_mutex_lock(&voice_radio.control_mutex);
    hw_native_voice_state_t before={0};uint64_t token=atomic_load(&voice_radio.lease_token);
    bool ok=voice_native_read(&before) && voice_native_state_valid(&before) && voice_native_healthy_locked();
    hw_native_voice_state_t after={0};uint64_t requested_ms=get_time();
    if(ok && requested_ms>=atomic_load(&voice_radio.renew_after_ms)){
        ok=hw_native_voice_renew(token) && voice_native_read(&after) && voice_native_state_valid(&after) &&
            after.sequence>before.sequence && after.monotonic_ms>=requested_ms &&
            after.lease_deadline_ms>before.lease_deadline_ms &&
            after.route_generation==before.route_generation && after.applied_route==before.applied_route;
        if(ok)atomic_store(&voice_radio.renew_after_ms,after.monotonic_ms+(after.lease_deadline_ms-after.monotonic_ms)/2);
    }
    if(!ok){voice_native_fail();
        fd_native_trace_control("lease_renew_failed",token,atomic_load(&voice_radio.route_generation),
            (hw_native_voice_route_t)atomic_load(&voice_radio.applied_route),&after);}
    pthread_mutex_unlock(&voice_radio.control_mutex);return ok;
}
static bool voice_physical_ptt(hw_ptt_source_t source,hw_ptt_observation_t *o) {
#ifdef FREEDV_HOST_TEST_PROFILE
    return hw_ptt_observe(source,o);
#else
    if(source==HW_PTT_SOURCE_HAND)return hw_ptt_observe(source,o);
    *o=(hw_ptt_observation_t){.source=source};
    if(voice_radio.keypad_fd<0)return false;
    unsigned long bits[(KEY_MAX+8*sizeof(long))/(8*sizeof(long))];memset(bits,0,sizeof(bits));
    uint64_t release_sequence=atomic_load(&voice_radio.release_epoch);
    if(ioctl(voice_radio.keypad_fd,EVIOCGKEY(sizeof(bits)),bits)<0)return false;
    if(release_sequence!=atomic_load(&voice_radio.release_epoch))return false;
    unsigned code=BTN_TRIGGER_HAPPY4;
    *o=(hw_ptt_observation_t){true,(bits[code/(8*sizeof(long))]&(1UL<<(code%(8*sizeof(long)))))!=0,source,
        atomic_fetch_add(&voice_radio.radio_ptt_sequence,1)+1,get_time(),release_sequence};
    return true;
#endif
}
void radio_voice_observation(fd_voice_observation *o) {
    memset(o,0,sizeof(*o));
    uint64_t now=get_time();
    freedv_packet_tx_observation packet=freedv_packet_tx_read(&packet_tx_tracker,now);
    o->valid=packet.known;o->tx=packet.tx;o->sequence=packet.sequence;
    o->monotonic_ms=packet.known && packet.age_ms<=now ? now-packet.age_ms : 0;
    for(unsigned i=0;i<FD_PTT_SOURCES;++i){
        hw_ptt_observation_t p={0};
        if(voice_physical_ptt((hw_ptt_source_t)i,&p))o->ptt[i]=(fd_ptt_observation){p.valid,p.pressed,p.sequence,p.monotonic_ms,p.release_sequence};
    }
    o->protection=atomic_load(&voice_radio.owned) && (cfg_tx_block() || radio_swr_trips()!=voice_radio.swr_at_entry ||
        atomic_load(&freedv_base_errors)!=voice_radio.errors_at_entry || atomic_load(&voice_radio.failed));
}
static bool voice_released_rx(fd_voice_observation *o) {
    radio_voice_observation(o);uint64_t now=get_time();
    if(!o->valid || o->tx || !o->monotonic_ms || now<o->monotonic_ms || now-o->monotonic_ms>FD_RADIO_PACKET_MAX_AGE_MS)return false;
    for(unsigned i=0;i<FD_PTT_SOURCES;++i)
        if(!o->ptt[i].valid || o->ptt[i].pressed || !o->ptt[i].monotonic_ms || now<o->ptt[i].monotonic_ms || now-o->ptt[i].monotonic_ms>FD_RADIO_PACKET_MAX_AGE_MS)return false;
    return true;
}
static bool voice_startup_safe(void) {
#ifdef ENABLE_FREEDV_NATIVE_VALIDATION
    const fd_qualified_profile *p=radio_voice_validation_profile();
    if(!radio_voice_validation_status(p,NULL,NULL))return true;
#else
    const fd_qualified_profile *p=fd_profile_production();
    if(!p || !fd_profile_valid(p,BOARD_NAME,FREEDV_BUILD_ID))return true;
#endif
    int saved_fd=voice_radio.keypad_fd;
#ifndef FREEDV_HOST_TEST_PROFILE
    int fd=open(BOARD_DEV_KEYPAD,O_RDONLY|O_NONBLOCK|O_CLOEXEC);
    if(fd<0)return false;voice_radio.keypad_fd=fd;
#endif
    fd_voice_observation observed;bool released=voice_released_rx(&observed);
#ifndef FREEDV_HOST_TEST_PROFILE
    close(fd);
#endif
    voice_radio.keypad_fd=saved_fd;return released;
}
static bool voice_profile_supported(const fd_qualified_profile *p,bool production) {
#if defined(BOARD_X6200) || defined(FREEDV_HOST_TEST_PROFILE)
    if(!(production?fd_profile_valid(p,BOARD_NAME,FREEDV_BUILD_ID):
         fd_profile_candidate_valid(p,BOARD_NAME,FREEDV_BUILD_ID)) || strcmp(p->baseband,hw_base_ver_str()))return false;
#ifdef FREEDV_HOST_TEST_PROFILE
    if(strcmp(p->control_version,"stub"))return false;
#else
    if(strcmp(p->control_version,FREEDV_CONTROL_ID))return false;
#endif
    hw_native_voice_capabilities_t contract=hw_native_voice_capabilities();
    if(!(hw_independent_ptt_sources() & (1u<<HW_PTT_SOURCE_HAND)) ||
       !hw_native_voice_has(contract,HW_NATIVE_CONTROL) || !contract.contract_id ||
       strcmp(p->native_contract_id,contract.contract_id))return false;
#if defined(BOARD_X6200) && !defined(FREEDV_HOST_TEST_PROFILE)
    /* This backend exposes one RX filter and no IF-shift command. Do not claim
     * shared X6100-only setters applied a second bank or a baseband IF offset. */
    if(!p->fixed_rx2_filter_attested || p->rx2_low_hz || p->rx2_high_hz || !p->fixed_if_shift_attested || !p->mixer_levels_attested)return false;
#endif
    if(p->sample_rate!=AUDIO_CAPTURE_RATE || p->rx_route&~FD_ROUTE_RECORD ||
        p->tx_route&~7u || !(p->tx_route&(FD_ROUTE_MODEM|FD_ROUTE_PTT)) ||
        p->power_tenths_watt>TX_PLAYER_MAX_PWR_W*10 ||
        p->compression || p->nr || p->notch || p->eq || p->microphone_mixing ||
        !p->fixed_tx_filter_attested || !p->fixed_eq_bypass_attested)return false;
    return true;
#else
    (void)p;(void)production;return false;
#endif
}
bool radio_voice_supported(const fd_qualified_profile *p) {
    return !radio_voice_control_recovery_required() && voice_profile_supported(p,true) &&
        hw_native_voice_has(hw_native_voice_capabilities(),HW_NATIVE_ALL);
}
const fd_qualified_profile *radio_voice_validation_profile(void) {
#ifdef FREEDV_HOST_TEST_PROFILE
    /* Explicit virtual route fixture, never compiled into a radio candidate. */
    static const fd_qualified_profile p={.id="Native validation: SIMULATION ONLY",
        .hardware="host",.build_id=FREEDV_BUILD_ID,.baseband="stub",.control_version="stub",
        .native_contract_id="host-native-v2",.rx_audio={1,1},.tx_audio={1,1},
        .local_mode=FD_PROFILE_USB_DIG,.upper_sideband=true,
        .capture_device="@default-source@",.playback_device="@default-sink@",.mixer="host-default",
        .sample_rate=AUDIO_CAPTURE_RATE,.capture_channel=0,.rx_route=FD_ROUTE_RECORD,
        .tx_route=FD_ROUTE_MODEM|FD_ROUTE_PTT,.rx_low_hz=100,.rx_high_hz=3100,
        .tx_low_hz=100,.tx_high_hz=3100,.rx2_low_hz=0,.rx2_high_hz=3300,
        .agc=radio_agc_auto,.mic_gain=20,.line_gain=20,.line_out_gain=10,.rf_gain=100,
        .power_tenths_watt=10,.mixer_levels_attested=true,
        .fixed_tx_filter_attested=true,.fixed_eq_bypass_attested=true,
        .capabilities=0,.complete_pass=false};
    return &p;
#else
    /* Add a version-bound route only after the baseband investigation supplies
     * its real selectors and mixer settings. No speculative bit combinations. */
    return NULL;
#endif
}
bool radio_voice_validation_status(const fd_qualified_profile *p,fd_validation_admission *out,const char **reason) {
    if(out)memset(out,0,sizeof(*out));
    if(reason)*reason=NULL;
#if defined(ENABLE_FREEDV_NATIVE_VALIDATION) || defined(FREEDV_HOST_TEST_PROFILE)
    hw_native_voice_capabilities_t caps=hw_native_voice_capabilities();
    const char *blocked=radio_voice_control_recovery_required()?"BLOCKED: native control lost; device recovery required; RF-off unverified":NULL;
    unsigned sources=hw_independent_ptt_sources();
#ifndef FREEDV_HOST_TEST_PROFILE
    sources|=1u<<HW_PTT_SOURCE_RADIO; /* opened/checked independently on claim */
    if(caps.simulation)blocked="A simulated backend cannot authorize radio validation";
#endif
    if(!blocked && (!(sources&(1u<<HW_PTT_SOURCE_HAND)) || !hw_native_voice_has(caps,HW_NATIVE_HAND_PTT)))
        blocked="BLOCKED: independent hand PTT is unavailable; baseband keying ownership is unestablished";
    if(!blocked && !hw_native_voice_has(caps,HW_NATIVE_KEYING_OWNERSHIP))
        blocked="BLOCKED: native control of physical keying is unestablished";
    if(!blocked && !hw_native_voice_has(caps,HW_NATIVE_APPLIED_ROUTE_ACK))
        blocked="BLOCKED: no versioned applied-route acknowledgement; command cache is not applied state";
    if(!blocked && !hw_native_voice_has(caps,HW_NATIVE_FAIL_CLOSED_LEASE))
        blocked="BLOCKED: no independent fail-closed native lease/inhibit protocol";
    if(!blocked && !p)blocked="BLOCKED: no reviewed native audio route and mixer profile for this baseband";
    if(!blocked && !voice_profile_supported(p,false))blocked="BLOCKED: candidate identity, endpoint or settings do not match this radio";
    if(blocked){if(reason)*reason=blocked;return false;}
    fd_validation_admission admission={.abi_version=FD_VALIDATION_ABI,
        .hardware=BOARD_NAME,.build_id=FREEDV_BUILD_ID,.baseband=hw_base_ver_str(),
#ifdef FREEDV_HOST_TEST_PROFILE
        .control_version="stub",
#else
        .control_version=FREEDV_CONTROL_ID,
#endif
        .independent_ptt_sources=sources,.keying_owned=true,.max_turn_seconds=FD_VALIDATION_MAX_TURN_SECONDS};
    if(!fd_validation_admission_valid(p,&admission)){if(reason)*reason="BLOCKED: incomplete native validation admission";return false;}
    if(out)*out=admission;return true;
#else
    (void)p;if(reason)*reason="Native validation is not included in this build";return false;
#endif
}
static bool voice_profile_locked(void) {
    const fd_qualified_profile *p=voice_radio.profile;
    radio_vfo_t vfo=(radio_vfo_t)param_i_get(cfg_band_current_vfo);
    radio_mode_t mode=p->local_mode==FD_PROFILE_USB_DIG?radio_mode_usb_dig:radio_mode_usb;
    if(voice_radio.context_count<64){
        unsigned i=voice_radio.context_count++;
        voice_radio.contexts[i].band=param_i_get(cfg_band_id);
        voice_radio.contexts[i].mode_a=param_i_get(cfg_band_vfoa_mode);
        voice_radio.contexts[i].mode_b=param_i_get(cfg_band_vfob_mode);
        voice_radio.contexts[i].filter_low=cparam_i_get(cfg_cur_filter_low);
        voice_radio.contexts[i].filter_high=cparam_i_get(cfg_cur_filter_high);
        voice_radio.contexts[i].vfo=(unsigned)vfo;
    }else ++voice_radio.context_discarded;
    hw_set_i(HW_MODEM,false);hw_set_i(HW_PTT,false);hw_set_i(HW_SPLIT,false);
    hw_vfo_set_mode(vfo,mode);
    hw_vfo_set_pre(vfo,p->preamp);hw_vfo_set_att(vfo,p->attenuation);
    hw_set_i(HW_RFG,p->rf_gain);hw_set_i(HW_SQL,p->squelch);
    /* An ordinary saved IF offset must not offset the native modem waveform.
     * The displayed dial stays unchanged; use its transverter-corrected RF. */
    hw_set_i(HW_IF_SHIFT_ON,false);hw_set_i(HW_IF_SHIFT_HZ,0);
    hw_vfo_set_freq(vfo,cparam_i_get(cfg_fg_freq)-cfg_transverter_shift_for(cparam_i_get(cfg_fg_freq)));
    hw_set_i(HW_FILTER1_LOW,p->rx_low_hz);hw_set_i(HW_FILTER1_HIGH,p->rx_high_hz);
#ifdef FREEDV_HOST_TEST_PROFILE
    hw_set_i(HW_FILTER2_LOW,p->rx2_low_hz);hw_set_i(HW_FILTER2_HIGH,p->rx2_high_hz);
#endif
    hw_vfo_set_agc(vfo,(radio_agc_t)p->agc);
    hw_set_i(HW_AGC_TIME,p->agc==radio_agc_fast?100:p->agc==radio_agc_auto?500:1000);
    hw_set_i(HW_COMP_ON,true); /* this API's inverted flag means compression off */
    hw_set_i(HW_NR,false);hw_set_i(HW_NB,false);hw_set_i(HW_DNF,false);hw_set_i(HW_DNF_AUTO,false);
    hw_set_i(HW_VOX,false);hw_set_i(HW_KEY_TRAIN,true);
    hw_set_i(HW_RIT,0);hw_set_i(HW_XIT,0);
    /* Configure actual capture gains under the acknowledged native inhibit.
     * Zero is an ordinary PGA setting, never the source of isolation. */
    hw_set_i(HW_MIC,radio_mic_handle);hw_set_i(HW_HMIC,p->mic_gain);hw_set_i(HW_IMIC,0);
    hw_set_i(HW_LINE_IN,p->line_gain);hw_set_i(HW_LINE_OUT,p->line_out_gain);hw_set_i(HW_MONI,p->monitor_gain);
    hw_set_f(HW_TXPWR_F,p->power_tenths_watt/10.f);
    hw_freedv_cached_snapshot_t command_cache;
    if(!hw_get_freedv_cached_snapshot(&command_cache) || !hw_vfo_mode_matches(vfo,mode) ||
        command_cache.rx_filter_low_hz!=p->rx_low_hz || command_cache.rx_filter_high_hz!=p->rx_high_hz ||
#ifdef FREEDV_HOST_TEST_PROFILE
        hw_get_i(HW_FILTER2_LOW)!=p->rx2_low_hz || hw_get_i(HW_FILTER2_HIGH)!=p->rx2_high_hz ||
#endif
        command_cache.microphone!=radio_mic_handle || command_cache.hand_mic_gain!=p->mic_gain ||
        command_cache.internal_mic_gain!=0 || command_cache.vox_enabled || command_cache.line_in!=p->line_gain || command_cache.line_out!=p->line_out_gain ||
        command_cache.monitor_level!=p->monitor_gain || command_cache.requested_power_tenths_watt!=p->power_tenths_watt)return false;
    /* This comparison checks only intended commands; the subsequent native
     * route acknowledgement is the separate effective-configuration gate. */
    return hw_resend_all();
}
static void voice_clear(void *unused) {
    (void)unused;
    if(!atomic_load(&voice_radio.owned))return;
    atomic_store(&voice_radio.cancelled,true);atomic_fetch_add(&voice_radio.epoch,1);
    /* Independent native QUIET request and software-unkey request precede
     * audio waits. Neither cached gains nor RECORD=true can establish quiet. */
    (void)voice_native_apply(HW_NATIVE_ROUTE_QUIET,0);
    radio_lock();hw_set_i(HW_MODEM,false);hw_set_i(HW_PTT,false);hw_pin_set(HW_PIN_MORSE_KEY,false);
    fd_native_trace_command(0,false,false);atomic_store(&modem_on,false);morse_on=false;
    voice_radio.restored=false;radio_unlock();
    pthread_mutex_lock(&voice_radio.audio_mutex);
    if(voice_radio.audio)fd_voice_audio_cancel(voice_radio.audio);
    pthread_mutex_unlock(&voice_radio.audio_mutex);
}
static bool voice_claim(const fd_qualified_profile *p,bool validation) {
    if(!(validation?radio_voice_validation_status(p,NULL,NULL):radio_voice_supported(p)) ||
       radio_freedv_startup_waiting() || freedv_busy() || atomic_load(&voice_radio.owned))return false;
#ifndef FREEDV_HOST_TEST_PROFILE
    voice_radio.keypad_fd=open(BOARD_DEV_KEYPAD,O_RDONLY|O_NONBLOCK|O_CLOEXEC);
    if(voice_radio.keypad_fd<0)return false;
#endif
    radio_lock();
    fd_voice_observation observation;
    bool released=voice_released_rx(&observation);
    bool acquired=released && state==RADIO_RX && !hw_tx_keyed_by_gui() && !freedv_owner &&
        hw_get_freedv_cached_snapshot(&voice_radio.saved) && ctl_freedv_acquire();
    if(acquired){atomic_store(&voice_radio.owned,true);voice_radio.snapshot_valid=true;voice_radio.profile=p;voice_radio.validation=validation;}
    radio_unlock();
    if(!acquired && voice_radio.keypad_fd>=0){close(voice_radio.keypad_fd);voice_radio.keypad_fd=-1;}
    if(!acquired)return false;
    pthread_mutex_lock(&voice_radio.control_mutex);
    hw_native_voice_state_t before={0};uint64_t time=0,token=0;
    bool baseline=voice_native_read(&before) && before.contract_id &&
        !strcmp(before.contract_id,p->native_contract_id) && !before.lease_token;
    bool random=getrandom(&token,sizeof(token),GRND_NONBLOCK)==sizeof(token) && token;
    atomic_store(&voice_radio.lease_token,token);atomic_store(&voice_radio.boot_id,before.boot_id);
    atomic_store(&voice_radio.route_request,0);atomic_store(&voice_radio.control_failed,false);
    atomic_store(&voice_radio.pending_generation,0);atomic_store(&voice_radio.pending_route,HW_NATIVE_ROUTE_QUIET);
    atomic_store(&voice_radio.control_pending,true);time=get_time();
    fd_native_trace_control("lease_acquire_request",token,0,HW_NATIVE_ROUTE_QUIET,NULL);
    bool native=baseline && random && hw_native_voice_acquire(token);
    native=voice_native_ack("lease_acquired",0,HW_NATIVE_ROUTE_QUIET,before.sequence,time,native);
    pthread_mutex_unlock(&voice_radio.control_mutex);
    if(!native){
        /* Never release an unacknowledged MCU inhibit. Its independent expiry
         * remains responsible for fail-closed behavior; no renewals follow. */
        if(baseline && random)(void)hw_native_voice_apply(token,1,HW_NATIVE_ROUTE_QUIET);
        radio_lock();hw_set_i(HW_MODEM,false);hw_set_i(HW_PTT,false);
        bool released=ctl_freedv_release(true);radio_unlock();
        /* A concurrent non-RX state can refuse Linux reservation release.
         * Retain tracked ownership and the recovery latch in that case; never
         * pretend ordinary control was restored or renew the failed lease. */
        if(released){atomic_store(&voice_radio.owned,false);atomic_store(&voice_radio.lease_token,0);}
        if(voice_radio.keypad_fd>=0){close(voice_radio.keypad_fd);voice_radio.keypad_fd=-1;}
        return false;
    }
    /* acquire already acknowledged QUIET. Do not create a second fallible
     * request before returning ownership to a coordinator that can close it. */
    atomic_store(&voice_radio.cancelled,true);
    voice_radio.swr_at_entry=radio_swr_trips();voice_radio.errors_at_entry=atomic_load(&freedv_base_errors);
    return !atomic_load(&voice_radio.control_failed);
}
bool radio_voice_claim(const fd_qualified_profile *p) {return voice_claim(p,false);}
bool radio_voice_claim_validation(const fd_qualified_profile *p,const fd_validation_admission *admission) {
    /* Recompute from hardware; a caller-supplied admission cannot grant missing
     * capabilities, a different identity, or a larger timeout. */
    fd_validation_admission actual;
    return fd_validation_admission_valid(p,admission) &&
        radio_voice_validation_status(p,&actual,NULL) &&
        admission->max_turn_seconds<=actual.max_turn_seconds && voice_claim(p,true);
}
static void voice_audio_failure(void *context) {
    (void)context;atomic_store(&voice_radio.failed,true);voice_clear(NULL);
}
static bool voice_prepare(void *unused,const fd_qualified_profile *p,const atomic_bool *cancel) {
    (void)unused;
    if(!atomic_load(&voice_radio.owned) && !radio_voice_claim(p))return false;
    if(p!=voice_radio.profile || atomic_load(cancel) || !voice_native_healthy())return false;
    hw_native_voice_state_t initial={0};
    if(voice_native_read(&initial))fd_native_trace_control("lease_at_prepare",initial.lease_token,
        initial.route_generation,initial.applied_route,&initial);
    if(voice_radio.validation && !radio_voice_validation_status(p,NULL,NULL))return false;
    atomic_store(&voice_radio.cancelled,false);atomic_store(&voice_radio.failed,false);
    voice_radio.mixer=audio_freedv_mixer_snapshot();
    if(!voice_radio.mixer || atomic_load(cancel))return false;
    voice_radio.main_quiet=true; /* partial failures also require safe restoration */
    if(!audio_freedv_main_quiet(true) || atomic_load(cancel))return false;
    if(!audio_freedv_mixer_levels(p->mixer_playback,p->mixer_capture) || atomic_load(cancel))return false;
    fd_voice_audio *audio=fd_voice_audio_open(p);
    if(audio)fd_voice_audio_on_error(audio,voice_audio_failure,&voice_radio);
    pthread_mutex_lock(&voice_radio.audio_mutex);voice_radio.audio=audio;
    bool cancelled=atomic_load(cancel) || atomic_load(&voice_radio.cancelled);
    if(audio && cancelled)fd_voice_audio_cancel(audio);
    pthread_mutex_unlock(&voice_radio.audio_mutex);
    return audio && !cancelled && voice_native_healthy();
}
static bool voice_fence(void *unused) {
    (void)unused;return !voice_radio.audio || fd_voice_audio_fence(voice_radio.audio);
}
static bool voice_receive(void *unused,uint64_t generation) {
    (void)unused;
    if(!atomic_load(&voice_radio.owned) || atomic_load(&voice_radio.failed) || !voice_radio.audio)return false;
    uint64_t epoch=atomic_load(&voice_radio.epoch);
    radio_lock();bool ok=fd_writer_voice_begin();
    if(ok){atomic_store(&voice_radio.cancelled,false);ok=voice_profile_locked() && voice_native_apply(HW_NATIVE_ROUTE_RX,epoch);fd_writer_voice_end();}
    radio_unlock();
    ok=ok && fd_voice_audio_begin(voice_radio.audio,FD_VOICE_RX,generation) &&
        voice_native_healthy() && epoch==atomic_load(&voice_radio.epoch) && !atomic_load(&voice_radio.cancelled);
    if(!ok)voice_clear(NULL);return ok;
}
typedef struct {
    const atomic_bool *cancel;
    uint64_t epoch, release_epoch, sequence[FD_PTT_SOURCES], release_sequence[FD_PTT_SOURCES];
    unsigned held_sources;
    bool rejected;
} voice_tx_admission;
/* This guard also runs under the audio admission lock. It reads independent
 * observations only: no radio writes or audio calls. Its bounded control
 * observation is serialized against route changes. Once
 * any held source releases, this attempt stays rejected even after a re-press.
 * A backend must retain physical edges to detect pulses between observations. */
static bool voice_tx_admit(void *context) {
    voice_tx_admission *a=context;
    if(a->rejected)return false;
    fd_abort_reason reason=FD_ABORT_NONE;
    bool ok=atomic_load(&voice_radio.owned) && voice_native_healthy() && !atomic_load(a->cancel) &&
        !atomic_load(&voice_radio.cancelled) && a->epoch==atomic_load(&voice_radio.epoch) && !cfg_tx_block();
    if(a->release_epoch!=atomic_load(&voice_radio.release_epoch)){ok=false;reason=FD_ABORT_START_RELEASE;}
    fd_voice_observation o;radio_voice_observation(&o);uint64_t time=get_time();
    bool healthy=o.valid && o.monotonic_ms && time>=o.monotonic_ms &&
        time-o.monotonic_ms<=FD_RADIO_PACKET_MAX_AGE_MS;
    unsigned held=0;bool released=false;
    for(unsigned i=0;i<FD_PTT_SOURCES;++i){
        fd_ptt_observation p=o.ptt[i];
        healthy &= p.valid && p.sequence && p.sequence>=a->sequence[i] && p.monotonic_ms &&
            time>=p.monotonic_ms && time-p.monotonic_ms<=FD_RADIO_PACKET_MAX_AGE_MS;
        if(a->sequence[i] && p.release_sequence!=a->release_sequence[i])released=true;
        if(p.release_sequence<a->release_sequence[i])healthy=false;
        if(p.pressed)held|=1u<<i;
        a->sequence[i]=p.sequence;
        a->release_sequence[i]=p.release_sequence;
    }
    if(!healthy){ok=false;reason=FD_ABORT_HEALTH;}
    else if(released || !held || (a->held_sources&~held)){ok=false;reason=FD_ABORT_START_RELEASE;}
    if(o.protection || o.device_error){ok=false;reason=o.device_error?FD_ABORT_DEVICE:FD_ABORT_PROTECTION;}
    a->held_sources|=held;
    if(!ok){a->rejected=true;atomic_store(&voice_radio.startup_abort_reason,reason);}
    return ok;
}
static fd_abort_reason voice_startup_abort_reason(void *unused) {
    (void)unused;return (fd_abort_reason)atomic_load(&voice_radio.startup_abort_reason);
}
static voice_tx_admission voice_admission_from(const fd_voice_observation *o,const atomic_bool *cancel,
        uint64_t epoch,uint64_t release_epoch) {
    voice_tx_admission a={.cancel=cancel,.epoch=epoch,.release_epoch=release_epoch};
    for(unsigned i=0;i<FD_PTT_SOURCES;++i){
        a.sequence[i]=o->ptt[i].sequence;a.release_sequence[i]=o->ptt[i].release_sequence;
        if(o->ptt[i].pressed)a.held_sources|=1u<<i;
    }
    return a;
}
static bool voice_begin_transmit(void *unused,uint64_t generation,const fd_voice_observation *observation) {
    (void)unused;voice_radio.starting_valid=false;
    atomic_store(&voice_radio.startup_abort_reason,FD_ABORT_NONE);
    if(!generation || generation<=voice_radio.starting_generation){
        atomic_store(&voice_radio.startup_abort_reason,FD_ABORT_HEALTH);return false;
    }
    voice_radio.starting_generation=generation;
    uint64_t time=get_time();
    bool healthy=generation && observation && observation->valid && observation->sequence &&
        observation->monotonic_ms && time>=observation->monotonic_ms &&
        time-observation->monotonic_ms<=FD_RADIO_PACKET_MAX_AGE_MS;
    bool held=false;
    if(observation)for(unsigned i=0;i<FD_PTT_SOURCES;++i){
        fd_ptt_observation p=observation->ptt[i];
        healthy &= p.valid && p.sequence && p.monotonic_ms && time>=p.monotonic_ms &&
            time-p.monotonic_ms<=FD_RADIO_PACKET_MAX_AGE_MS;
        held|=p.pressed;
    }
    if(!healthy){atomic_store(&voice_radio.startup_abort_reason,FD_ABORT_HEALTH);return false;}
    if(observation->protection || observation->device_error){
        atomic_store(&voice_radio.startup_abort_reason,observation->device_error?FD_ABORT_DEVICE:FD_ABORT_PROTECTION);return false;
    }
    if(!held){atomic_store(&voice_radio.startup_abort_reason,FD_ABORT_START_RELEASE);return false;}
    /* The coordinator's admitted observation precedes the potentially slow
     * audio fence. Never replace its release counters with a later snapshot. */
    voice_radio.starting_observation=*observation;
    voice_radio.starting_epoch=atomic_load(&voice_radio.epoch);
    voice_radio.starting_release_epoch=atomic_load(&voice_radio.release_epoch);
    atomic_bool cancel=false;
    voice_tx_admission a=voice_admission_from(observation,&cancel,voice_radio.starting_epoch,voice_radio.starting_release_epoch);
    voice_radio.starting_valid=voice_tx_admit(&a);
    return voice_radio.starting_valid;
}
static bool voice_transmit(void *unused,uint64_t generation,const atomic_bool *cancel) {
    (void)unused;
    bool admitted=voice_radio.starting_valid && voice_radio.starting_generation==generation;
    voice_radio.starting_valid=false;
    if(!admitted){atomic_store(&voice_radio.startup_abort_reason,FD_ABORT_HEALTH);return false;}
    if(!atomic_load(&voice_radio.owned) || !voice_radio.audio || atomic_load(cancel))return false;
    voice_tx_admission admission=voice_admission_from(&voice_radio.starting_observation,cancel,
        voice_radio.starting_epoch,voice_radio.starting_release_epoch);
    if(!voice_tx_admit(&admission))return false;
    radio_lock();
    bool ok=(state==RADIO_RX || state==RADIO_TX) && !radio_no_data() && cfg_is_tx_freq(cparam_i_get(cfg_fg_freq)) &&
        !cfg_tx_refusal(cparam_i_get(cfg_fg_freq)) &&
        voice_tx_admit(&admission) && fd_writer_voice_begin();
    if(ok){
        ok=voice_profile_locked();
        if(ok && voice_tx_admit(&admission)){
            ok=voice_native_apply(HW_NATIVE_ROUTE_TX,admission.epoch) && voice_tx_admit(&admission);
            if(ok){atomic_store(&modem_on,true);fd_native_trace_command(generation,true,true);}
        }else ok=false;
        fd_writer_voice_end();
    }
    radio_unlock();
    /* The qualified route may not supply microphone capture before keying.
     * Admission and the 200 ms reserve start at the post-key capture boundary. */
    if(ok)ok=fd_voice_audio_begin_guarded(voice_radio.audio,FD_VOICE_TX,generation,voice_tx_admit,&admission);
    if(atomic_load(cancel) || atomic_load(&voice_radio.cancelled) || !voice_tx_admit(&admission))ok=false;
    if(!ok)voice_clear(NULL);
    return ok;
}
static bool voice_finish(void *unused,uint64_t generation) {(void)unused;return fd_voice_audio_finish(voice_radio.audio,generation);}
static int voice_drained(void *unused) {(void)unused;return fd_voice_audio_drained(voice_radio.audio);}
struct voice_tuning_job {fd_voice_tuning *tuning;uint64_t epoch;bool ok;};
static void voice_tuning_on_gui(void *arg) {
    struct voice_tuning_job *job=arg;
    fd_voice_observation observation;
    if(!voice_released_rx(&observation))return;
    if(job->epoch!=atomic_load(&voice_radio.epoch) || !fd_writer_voice_begin())return;
    job->ok=cfg_voice_apply(job->tuning);
    fd_writer_voice_end();
}
static bool voice_retune(void *unused,fd_voice_tuning *tuning,const fd_qualified_profile *p) {
    (void)unused;
    if(p!=voice_radio.profile || !atomic_load(&voice_radio.owned))return false;
    struct voice_tuning_job job={tuning,atomic_load(&voice_radio.epoch),false};
    return scheduler_call(voice_tuning_on_gui,&job) && job.ok;
}
static void voice_restore_on_gui(void *arg) {
    bool *ok=arg;
    if(!fd_writer_voice_begin())return;
    radio_lock();
    /* Frequencies and active VFO are never replayed from an entry snapshot.
     * Profile overrides did not write any SSB/DIGI settings or preferences. */
    hw_freedv_restore_owned(&voice_radio.saved);
    hw_set_i(HW_MODEM,false);hw_set_i(HW_PTT,false);
    hw_set_i(HW_VFO_CUR,param_i_get(cfg_band_current_vfo));
    hw_vfo_set_mode(RADIO_VFO_A,param_i_get(cfg_band_vfoa_mode));
    hw_vfo_set_mode(RADIO_VFO_B,param_i_get(cfg_band_vfob_mode));
    hw_vfo_set_agc(RADIO_VFO_A,param_i_get(cfg_band_vfoa_agc));
    hw_vfo_set_agc(RADIO_VFO_B,param_i_get(cfg_band_vfob_agc));
    hw_vfo_set_pre(RADIO_VFO_A,param_i_get(cfg_band_vfoa_pre));hw_vfo_set_pre(RADIO_VFO_B,param_i_get(cfg_band_vfob_pre));
    hw_vfo_set_att(RADIO_VFO_A,param_i_get(cfg_band_vfoa_att));hw_vfo_set_att(RADIO_VFO_B,param_i_get(cfg_band_vfob_att));
    hw_set_i(HW_RFG,param_i_get(cfg_rfgain));hw_set_i(HW_SQL,param_i_get(cfg_squelch));
    hw_set_i(HW_FILTER1_LOW,cparam_i_get(cfg_cur_filter_low));hw_set_i(HW_FILTER1_HIGH,cparam_i_get(cfg_cur_filter_high));
    hw_set_i(HW_RIT,param_i_get(cfg_rit));hw_set_i(HW_XIT,param_i_get(cfg_xit));
    hw_set_i(HW_SPLIT,param_i_get(cfg_band_split));
    hw_set_i(HW_KEY_TRAIN,param_i_get(cfg_key_train));
    hw_set_i(HW_NR,param_i_get(cfg_nr));hw_set_i(HW_NB,param_i_get(cfg_nb));
    hw_set_i(HW_DNF_AUTO,param_i_get(cfg_dnf_auto));hw_set_i(HW_DNF,param_i_get(cfg_dnf));
    hw_set_i(HW_COMP_ON,param_i_get(cfg_comp)<=1);
    if(param_i_get(cfg_comp)>1)hw_set_i(HW_COMP_LEVEL,param_i_get(cfg_comp)-2);
    hw_set_i(HW_RXVOL,param_i_get(cfg_volume));
    hw_set_i(HW_IF_SHIFT_HZ,param_i_get(cfg_band_if_shift));hw_set_i(HW_IF_SHIFT_ON,param_i_get(cfg_band_if_shift)!=0);
    radio_unlock();
    /* These callbacks use the current VFO's untouched settings group. */
    update_agc_time(NULL,NULL);on_low_filter_change(NULL,NULL);on_high_filter_change(NULL,NULL);
    on_vfo_freq_change(NULL,(void *)RADIO_VFO_A);on_vfo_freq_change(NULL,(void *)RADIO_VFO_B);
    radio_lock();*ok=hw_resend_all();radio_unlock();fd_writer_voice_end();
}
static bool voice_restore(void *unused) {
    (void)unused;
    if(!atomic_load(&voice_radio.owned))return true;
    if(!voice_radio.snapshot_valid || !voice_native_healthy() ||
        atomic_load(&voice_radio.applied_route)!=HW_NATIVE_ROUTE_QUIET)return false;
    fd_voice_observation o;
    if(!voice_released_rx(&o)){voice_clear(NULL);return false;}
    if(!voice_radio.restored){
        if(voice_radio.mixer && !audio_freedv_mixer_restore(voice_radio.mixer))return false;
        bool ok=false;if(!scheduler_call(voice_restore_on_gui,&ok) || !ok)return false;
        voice_radio.restore_sequence=o.sequence;voice_radio.restored=true;return false;
    }
    return o.sequence>voice_radio.restore_sequence;
}
static bool voice_close(void *unused) {
    (void)unused;
    /* Unknown applied state/expired authority requires device recovery. Keep
     * the application lease and do not repeatedly restore, clear, or renew. */
    if(atomic_load(&voice_radio.owned) && radio_voice_control_recovery_required())return false;
    pthread_mutex_lock(&voice_radio.audio_mutex);fd_voice_audio *audio=voice_radio.audio;voice_radio.audio=NULL;pthread_mutex_unlock(&voice_radio.audio_mutex);
    if(audio)fd_voice_audio_close(audio);
    if(atomic_load(&voice_radio.owned)){
        fd_voice_observation o;
        if(!voice_radio.restored || !voice_released_rx(&o)){voice_clear(NULL);return false;}
        if(voice_radio.main_quiet){if(!audio_freedv_main_quiet(false))return false;voice_radio.main_quiet=false;}
        if(!voice_released_rx(&o)){voice_clear(NULL);return false;}
        hw_native_voice_state_t before={0},after={0};
        if(!voice_native_read(&before) || !voice_native_state_valid(&before) ||
            before.applied_route!=HW_NATIVE_ROUTE_QUIET)return false;
        uint64_t token=atomic_load(&voice_radio.lease_token),requested_ms=get_time();
        fd_native_trace_control("lease_release_request",token,before.route_generation,HW_NATIVE_ROUTE_QUIET,NULL);
        pthread_mutex_lock(&voice_radio.control_mutex);atomic_store(&voice_radio.control_releasing,true);
        bool requested=hw_native_voice_release(token),observed=voice_native_read(&after);
        bool released=requested && observed && after.valid &&
            after.contract_id && !strcmp(after.contract_id,voice_radio.profile->native_contract_id) &&
            after.boot_id==before.boot_id && after.sequence>before.sequence &&
            after.monotonic_ms>=requested_ms && after.monotonic_ms<=get_time() && !after.lease_token &&
            after.applied_route==HW_NATIVE_ROUTE_QUIET;
        fd_native_trace_control(released?"lease_released":"lease_release_rejected",token,
            before.route_generation,HW_NATIVE_ROUTE_QUIET,&after);
        if(!released){
            /* A lease release may be atomically refused because a physical
             * control was pressed after our last poll. Retain the lease. */
            bool refused=!requested && observed && voice_native_state_valid(&after) &&
                after.applied_route==HW_NATIVE_ROUTE_QUIET && after.route_generation==before.route_generation;
            atomic_store(&voice_radio.control_releasing,false);pthread_mutex_unlock(&voice_radio.control_mutex);
            if(!refused)voice_native_fail();return false;
        }
        atomic_store(&voice_radio.lease_token,0);pthread_mutex_unlock(&voice_radio.control_mutex);
        radio_lock();released=ctl_freedv_release(true);radio_unlock();
        if(!released)return false;
        atomic_store(&voice_radio.owned,false);atomic_store(&voice_radio.control_releasing,false);
    }
    if(voice_radio.mixer){audio_freedv_mixer_snapshot_free(voice_radio.mixer);voice_radio.mixer=NULL;}
    if(voice_radio.keypad_fd>=0){close(voice_radio.keypad_fd);voice_radio.keypad_fd=-1;}
    return true;
}
static bool voice_observe(void *unused,fd_voice_observation *o,bool *sync,bool *mic) {
    (void)unused;radio_voice_observation(o);
    if(atomic_load(&voice_radio.owned) && !voice_native_healthy())o->device_error=true;
    pthread_mutex_lock(&voice_radio.audio_mutex);
    if(voice_radio.audio){fd_voice_stats s=fd_voice_audio_observe(voice_radio.audio);*sync=s.synchronized;*mic=fd_voice_audio_microphone_active(voice_radio.audio);if(fd_voice_audio_failed(voice_radio.audio))o->device_error=true;}
    pthread_mutex_unlock(&voice_radio.audio_mutex);
    return o->valid;
}
bool radio_voice_audio_stats(fd_voice_audio_snapshot *out) {
    pthread_mutex_lock(&voice_radio.audio_mutex);
    bool ok=voice_radio.audio && fd_voice_audio_snapshot_get(voice_radio.audio,out);
    pthread_mutex_unlock(&voice_radio.audio_mutex);return ok;
}
bool radio_voice_tuning_request(fd_local_operation op,int32_t value,fd_voice_tuning *out) {return cfg_voice_resolve(op,value,out);}
static int voice_monitor_level(void) {
#if defined(ENABLE_FREEDV_USB_TEST) || defined(ENABLE_FREEDV_USB_VOICE)
    if(radio_usb_quiet())return 0;
#endif
    return atomic_load(&voice_radio.owned) && voice_radio.profile ? voice_radio.profile->monitor_gain : param_i_get(cfg_moni);
}
bool radio_voice_volume(int32_t delta) {
    if(!fd_writer_voice_begin())return false;
    (void)radio_change_vol((int16_t)delta);fd_writer_voice_end();return true;
}
bool radio_voice_local_ptt(bool pressed,uint64_t *sequence) {
#ifdef FREEDV_HOST_TEST_PROFILE
    extern void hw_stub_ptt_set(hw_ptt_source_t,bool);
    hw_stub_ptt_set(HW_PTT_SOURCE_RADIO,pressed);
#endif
    if(!atomic_load(&voice_radio.owned))return false;
    /* Preserve the physical front-key release even if a new press arrives
     * before the coordinator's next level observation. */
    if(!pressed)atomic_fetch_add(&voice_radio.release_epoch,1);
    hw_ptt_observation_t p;
    if(!voice_physical_ptt(HW_PTT_SOURCE_RADIO,&p) || !p.valid || p.pressed!=pressed)return false;
    if(sequence)*sequence=p.sequence;return true;
}
fd_voice_driver radio_voice_driver(void) {
    /* Called only for a new OFF session after the previous close returned true. */
    voice_radio.snapshot_valid=false;voice_radio.main_quiet=false;atomic_store(&voice_radio.restored,false);voice_radio.context_count=voice_radio.context_discarded=0;
    atomic_store(&voice_radio.cancelled,false);atomic_store(&voice_radio.failed,false);
    voice_radio.starting_valid=false;voice_radio.starting_generation=0;
    pthread_mutex_lock(&voice_radio.observation_mutex);voice_radio.last_control_observation=(hw_native_voice_state_t){0};pthread_mutex_unlock(&voice_radio.observation_mutex);
    atomic_store(&voice_radio.lease_token,0);atomic_store(&voice_radio.boot_id,0);
    atomic_store(&voice_radio.control_failed,false);atomic_store(&voice_radio.control_pending,false);
    atomic_store(&voice_radio.applied_route,HW_NATIVE_ROUTE_NONE);atomic_store(&voice_radio.route_generation,0);
    return (fd_voice_driver){.context=&voice_radio,.prepare=voice_prepare,.fence=voice_fence,.receive=voice_receive,.transmit=voice_transmit,
        .finish=voice_finish,.drained=voice_drained,.emergency_clear=voice_clear,.retune=voice_retune,.restore=voice_restore,.close=voice_close,.observe=voice_observe,
        .startup_abort_reason=voice_startup_abort_reason,.begin_transmit=voice_begin_transmit,.maintain=voice_maintain};
}
