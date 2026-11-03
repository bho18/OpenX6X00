/* SPDX-License-Identifier: LGPL-2.1-or-later
 * Production voice binding, included by radio.c to share its control lock,
 * packet tracker and protection state. It does not run diagnostic cases.
 * Profile overrides touch hardware only. Normal VFO/SSB/DIGI preferences remain
 * the source for restoration, so visiting a band cannot persist an override. */
#include "voice_radio.h"
#include "voice_audio.h"
#include "../cfg/voice_settings.h"
#include "../tx_player.h"
#include <fcntl.h>
#include <sys/ioctl.h>
#include <linux/input.h>

typedef struct {
    pthread_mutex_t audio_mutex;
    fd_voice_audio *audio;
    audio_freedv_mixer_snapshot_t *mixer;
    const fd_qualified_profile *profile;
    hw_freedv_cached_snapshot_t saved;
    atomic_bool owned, cancelled, failed, restored;
    atomic_uint_fast64_t epoch;
    unsigned swr_at_entry, errors_at_entry;
    uint64_t restore_sequence;
    bool snapshot_valid, main_quiet;
    atomic_int keypad_fd;
    atomic_uint_fast64_t radio_ptt_sequence;
    /* Bounded context observations; restoration uses untouched preferences,
     * not an evictable diagnostic history. Exhaustion cannot terminate audio. */
    struct { int32_t band, mode_a, mode_b, filter_low, filter_high; unsigned vfo; } contexts[64];
    unsigned context_count, context_discarded;
} voice_radio_context;
static voice_radio_context voice_radio={.audio_mutex=PTHREAD_MUTEX_INITIALIZER,.keypad_fd=-1};

static void voice_pause(void) { struct timespec t={0,5000000};nanosleep(&t,NULL); }
static bool voice_physical_ptt(hw_ptt_source_t source,hw_ptt_observation_t *o) {
#ifdef FREEDV_HOST_TEST_PROFILE
    return hw_ptt_observe(source,o);
#else
    if(source==HW_PTT_SOURCE_HAND)return hw_ptt_observe(source,o);
    *o=(hw_ptt_observation_t){.source=source};
    if(voice_radio.keypad_fd<0)return false;
    unsigned long bits[(KEY_MAX+8*sizeof(long))/(8*sizeof(long))];memset(bits,0,sizeof(bits));
    if(ioctl(voice_radio.keypad_fd,EVIOCGKEY(sizeof(bits)),bits)<0)return false;
    unsigned code=BTN_TRIGGER_HAPPY4;
    *o=(hw_ptt_observation_t){true,(bits[code/(8*sizeof(long))]&(1UL<<(code%(8*sizeof(long)))))!=0,source,
        atomic_fetch_add(&voice_radio.radio_ptt_sequence,1)+1,get_time()};
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
        if(voice_physical_ptt((hw_ptt_source_t)i,&p))o->ptt[i]=(fd_ptt_observation){p.valid,p.pressed,p.sequence,p.monotonic_ms};
    }
    o->protection=atomic_load(&voice_radio.owned) && (radio_swr_trips()!=voice_radio.swr_at_entry ||
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
    const fd_qualified_profile *p=fd_profile_production();
    if(!p || !fd_profile_valid(p,BOARD_NAME,FREEDV_BUILD_ID))return true;
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
bool radio_voice_supported(const fd_qualified_profile *p) {
#if defined(BOARD_X6200) || defined(FREEDV_HOST_TEST_PROFILE)
    if(!fd_profile_valid(p,BOARD_NAME,FREEDV_BUILD_ID) || strcmp(p->baseband,hw_base_ver_str()))return false;
#ifdef FREEDV_HOST_TEST_PROFILE
    if(strcmp(p->control_version,"stub"))return false;
#else
    if(strcmp(p->control_version,FREEDV_CONTROL_ID))return false;
#endif
    if(!(hw_independent_ptt_sources() & (1u<<HW_PTT_SOURCE_HAND)))return false;
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
    (void)p;return false;
#endif
}
static bool voice_profile_locked(bool transmit) {
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
    hw_set_i(HW_MIC,radio_mic_handle);hw_set_i(HW_IMIC,0);hw_set_i(HW_HMIC,0);
    hw_set_i(HW_LINE_IN,p->line_gain);hw_set_i(HW_LINE_OUT,p->line_out_gain);hw_set_i(HW_MONI,p->monitor_gain);
    hw_set_f(HW_TXPWR_F,p->power_tenths_watt/10.f);
    hw_set_i(HW_RECORD,((transmit?p->tx_route:p->rx_route)&FD_ROUTE_RECORD)!=0);
    hw_freedv_cached_snapshot_t readback;
    if(!hw_get_freedv_cached_snapshot(&readback) || !hw_vfo_mode_matches(vfo,mode) ||
        readback.rx_filter_low_hz!=p->rx_low_hz || readback.rx_filter_high_hz!=p->rx_high_hz ||
#ifdef FREEDV_HOST_TEST_PROFILE
        hw_get_i(HW_FILTER2_LOW)!=p->rx2_low_hz || hw_get_i(HW_FILTER2_HIGH)!=p->rx2_high_hz ||
#endif
        readback.microphone!=radio_mic_handle || readback.hand_mic_gain!=0 ||
        readback.internal_mic_gain || readback.vox_enabled || readback.line_in!=p->line_gain || readback.line_out!=p->line_out_gain ||
        readback.monitor_level!=p->monitor_gain || readback.requested_power_tenths_watt!=p->power_tenths_watt)return false;
    if(!transmit){
        fd_route_settings route={radio_mic_handle,0,0,(p->rx_route&FD_ROUTE_RECORD)!=0,false,false};
        return fd_route_activate(&route);
    }
    return hw_resend_all();
}
static void voice_clear(void *unused) {
    (void)unused;
    if(!atomic_load(&voice_radio.owned))return;
    atomic_store(&voice_radio.cancelled,true);atomic_fetch_add(&voice_radio.epoch,1);
    pthread_mutex_lock(&voice_radio.audio_mutex);
    if(voice_radio.audio)fd_voice_audio_cancel(voice_radio.audio);
    pthread_mutex_unlock(&voice_radio.audio_mutex);
    radio_lock();
    /* Safe-off writes are admitted even if an unrelated callback is winding
     * down. No codec, PA operation, disk or GUI join precedes software unkey. */
    fd_route_stop();hw_pin_set(HW_PIN_MORSE_KEY,false);
    atomic_store(&modem_on,false);morse_on=false;
    if(fd_writer_voice_begin()){
        hw_set_i(HW_HMIC,0);hw_set_i(HW_IMIC,0);hw_set_i(HW_VOX,false);hw_set_i(HW_RECORD,true);
        (void)hw_resend_all();fd_writer_voice_end();
    }
    voice_radio.restored=false;
    radio_unlock();
}
bool radio_voice_claim(const fd_qualified_profile *p) {
    if(!radio_voice_supported(p) || radio_freedv_startup_waiting() || freedv_busy() || atomic_load(&voice_radio.owned))return false;
#ifndef FREEDV_HOST_TEST_PROFILE
    voice_radio.keypad_fd=open(BOARD_DEV_KEYPAD,O_RDONLY|O_NONBLOCK|O_CLOEXEC);
    if(voice_radio.keypad_fd<0)return false;
#endif
    radio_lock();
    bool acquired=state==RADIO_RX && !hw_tx_keyed_by_gui() && !freedv_owner &&
        hw_get_freedv_cached_snapshot(&voice_radio.saved) && ctl_freedv_acquire();
    if(acquired){atomic_store(&voice_radio.owned,true);voice_radio.snapshot_valid=true;voice_radio.profile=p;}
    radio_unlock();
    if(!acquired && voice_radio.keypad_fd>=0){close(voice_radio.keypad_fd);voice_radio.keypad_fd=-1;}
    if(!acquired)return false;
    voice_clear(NULL);
    voice_radio.swr_at_entry=radio_swr_trips();voice_radio.errors_at_entry=atomic_load(&freedv_base_errors);
    return true;
}
static void voice_audio_failure(void *context) {
    (void)context;atomic_store(&voice_radio.failed,true);voice_clear(NULL);
}
static bool voice_prepare(void *unused,const fd_qualified_profile *p,const atomic_bool *cancel) {
    (void)unused;
    if(!atomic_load(&voice_radio.owned) && !radio_voice_claim(p))return false;
    if(p!=voice_radio.profile || atomic_load(cancel))return false;
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
    return audio && !cancelled;
}
static bool voice_fence(void *unused) {
    (void)unused;return !voice_radio.audio || fd_voice_audio_fence(voice_radio.audio);
}
static bool voice_receive(void *unused,uint64_t generation) {
    (void)unused;
    if(!atomic_load(&voice_radio.owned) || atomic_load(&voice_radio.failed) || !voice_radio.audio)return false;
    radio_lock();bool ok=fd_writer_voice_begin();
    if(ok){atomic_store(&voice_radio.cancelled,false);ok=voice_profile_locked(false);fd_writer_voice_end();}
    radio_unlock();
    return ok && fd_voice_audio_begin(voice_radio.audio,FD_VOICE_RX,generation);
}
static bool voice_transmit(void *unused,uint64_t generation,const atomic_bool *cancel) {
    (void)unused;
    if(!atomic_load(&voice_radio.owned) || !voice_radio.audio || atomic_load(cancel))return false;
    fd_voice_observation observed;radio_voice_observation(&observed);
    uint64_t now=get_time();bool held=false;
    for(unsigned i=0;i<FD_PTT_SOURCES;++i){
        if(!observed.ptt[i].valid || !observed.ptt[i].monotonic_ms || now-observed.ptt[i].monotonic_ms>FD_RADIO_PACKET_MAX_AGE_MS)return false;
        held|=observed.ptt[i].pressed;
    }
    radio_lock();
    bool ok=held && observed.valid && now-observed.monotonic_ms<=FD_RADIO_PACKET_MAX_AGE_MS &&
        !observed.protection && (state==RADIO_RX || state==RADIO_TX) && cfg_is_tx_freq(cparam_i_get(cfg_fg_freq)) &&
        !atomic_load(cancel) && !atomic_load(&voice_radio.cancelled) && fd_writer_voice_begin();
    if(ok){
        ok=voice_profile_locked(true);
        if(ok && !atomic_load(cancel) && !atomic_load(&voice_radio.cancelled)){
            fd_route_settings route={radio_mic_handle,voice_radio.profile->mic_gain,0,
                (voice_radio.profile->tx_route&FD_ROUTE_RECORD)!=0,
                (voice_radio.profile->tx_route&FD_ROUTE_MODEM)!=0,
                (voice_radio.profile->tx_route&FD_ROUTE_PTT)!=0};
            ok=fd_route_activate(&route);
            atomic_store(&modem_on,route.modem);
        }else ok=false;
        fd_writer_voice_end();
    }
    radio_unlock();
    /* The qualified route may not supply microphone capture before keying.
     * Admission and the 200 ms reserve start at the post-key capture boundary. */
    if(ok)ok=fd_voice_audio_begin(voice_radio.audio,FD_VOICE_TX,generation);
    if(atomic_load(cancel) || atomic_load(&voice_radio.cancelled))ok=false;
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
    if(!voice_radio.snapshot_valid)return false;
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
    pthread_mutex_lock(&voice_radio.audio_mutex);fd_voice_audio *audio=voice_radio.audio;voice_radio.audio=NULL;pthread_mutex_unlock(&voice_radio.audio_mutex);
    if(audio)fd_voice_audio_close(audio);
    if(atomic_load(&voice_radio.owned)){
        fd_voice_observation o;
        if(!voice_radio.restored || !voice_released_rx(&o)){voice_clear(NULL);return false;}
        if(voice_radio.main_quiet){if(!audio_freedv_main_quiet(false))return false;voice_radio.main_quiet=false;}
        if(!voice_released_rx(&o)){voice_clear(NULL);return false;}
        radio_lock();bool released=ctl_freedv_release(true);radio_unlock();
        if(!released)return false;
        atomic_store(&voice_radio.owned,false);
    }
    if(voice_radio.mixer){audio_freedv_mixer_snapshot_free(voice_radio.mixer);voice_radio.mixer=NULL;}
    if(voice_radio.keypad_fd>=0){close(voice_radio.keypad_fd);voice_radio.keypad_fd=-1;}
    return true;
}
static bool voice_observe(void *unused,fd_voice_observation *o,bool *sync,bool *mic) {
    (void)unused;radio_voice_observation(o);
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
static int voice_monitor_level(void) { return atomic_load(&voice_radio.owned) && voice_radio.profile ? voice_radio.profile->monitor_gain : param_i_get(cfg_moni); }
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
    hw_ptt_observation_t p;
    if(!voice_physical_ptt(HW_PTT_SOURCE_RADIO,&p) || !p.valid || p.pressed!=pressed)return false;
    if(sequence)*sequence=p.sequence;return true;
}
fd_voice_driver radio_voice_driver(void) {
    /* Called only for a new OFF session after the previous close returned true. */
    voice_radio.snapshot_valid=false;voice_radio.main_quiet=false;atomic_store(&voice_radio.restored,false);voice_radio.context_count=voice_radio.context_discarded=0;
    atomic_store(&voice_radio.cancelled,false);atomic_store(&voice_radio.failed,false);
    return (fd_voice_driver){.context=&voice_radio,.prepare=voice_prepare,.fence=voice_fence,.receive=voice_receive,.transmit=voice_transmit,
        .finish=voice_finish,.drained=voice_drained,.emergency_clear=voice_clear,.retune=voice_retune,.restore=voice_restore,.close=voice_close,.observe=voice_observe};
}
