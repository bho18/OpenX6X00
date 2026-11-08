// SPDX-License-Identifier: BSD-2-Clause
/* Production adapter, synthetic cfg/evdev and hardware. No radio accessed. */
#define _GNU_SOURCE
#define ENABLE_FREEDV_DIAGNOSTICS 1
#undef NDEBUG
#include "src/hw/hw.h"
#include "src/freedv/usb_radio.h"
#include "src/freedv/usb_ptt.h"
#include "src/freedv/packet_tx.h"
#include "src/cfg/voice_settings.h"
#include "src/cfg/ham_bands.h"
#include <assert.h>
#include <errno.h>
#include <math.h>
#include <pthread.h>
#include <stdio.h>
#include <string.h>
#include <time.h>
#include <unistd.h>
#define BOARD_DEV_KEYPAD "/dev/null"
#define FILTER_2_OFFSET_OUT 40
#define LV_MAX(a,b) ((a)>(b)?(a):(b))
enum {RADIO_RX,RADIO_TX};
enum {cfg_band_split=1,cfg_band_tx_shift,cfg_rit,cfg_xit,cfg_fg_freq,cfg_band_current_vfo,
 cfg_band_vfoa_freq,cfg_band_vfob_freq,cfg_pwr,cfg_band_tx_i_offset,cfg_band_tx_q_offset,
 cfg_band_dac_offset,cfg_atu_enabled,cfg_band_vfoa_mode,cfg_band_vfob_mode,
 cfg_band_vfoa_agc,cfg_band_vfob_agc,cfg_band_vfoa_pre,cfg_band_vfob_pre,
 cfg_band_vfoa_att,cfg_band_vfob_att,cfg_rfgain,cfg_squelch,cfg_cur_filter_low,
 cfg_cur_filter_high,cfg_key_train,cfg_nr,cfg_nb,cfg_dnf,cfg_dnf_auto,cfg_comp,
 cfg_speaker_off,cfg_volume,cfg_moni,cfg_band_if_shift,cfg_cw_decoder,cfg_mode_zoom,CFG_COUNT};
typedef struct {bool known,fresh,tx;} fd_observation;
static atomic_int state=RADIO_RX;
static void *freedv_owner;
static atomic_uint freedv_base_errors;
static atomic_bool modem_on;
static bool morse_on,mute,tci_mute;
static pthread_mutex_t radio_mutex=PTHREAD_MUTEX_INITIALIZER;
static freedv_packet_tx_tracker packet_tx_tracker=FREEDV_PACKET_TX_TRACKER_INIT;
static atomic_uint_fast64_t clock_ms=10000;
static hw_freedv_cached_snapshot_t cached;
static atomic_bool panel_pressed,panel_valid=true,writer_allowed=true,legal_frequency=true;
static atomic_bool global_tx_block,outside_bands_allowed,block_during_ptt,block_during_modem;
static atomic_bool calibration_fail_once;
static atomic_uint calibration_calls;
static unsigned policy_release_calls;
static atomic_bool calibration_open,key_train;
static atomic_uint swr_count,modem_on_writes,ptt_on_writes,pin_on_writes,all_writes,gui_writes;
static atomic_int stop_during_write=-1;
static atomic_bool stop_during_modem_on;
static atomic_bool release_during_modem_on;
static atomic_bool resend_fail_once,press_during_resolve,press_during_voice_begin;
static atomic_uint resends;
static int32_t cfg[CFG_COUNT];
static pthread_t gui;
static bool terminal_replaying;
static _Thread_local unsigned voice_scope;
static unsigned terminal_poweroffs,terminal_resets;
static bool scheduler_on_gui_thread(void){return pthread_equal(gui,pthread_self());}
static bool freedv_navigation_replaying(void){return terminal_replaying;}
static bool test_panel_poll(int fd,atomic_uint_fast64_t *releases,bool *pressed){(void)fd;(void)releases;*pressed=panel_pressed;return panel_valid;}
#define fd_usb_panel_poll test_panel_poll
static void radio_lock(void){assert(!pthread_mutex_lock(&radio_mutex));}
static bool radio_try_lock(void){return pthread_mutex_trylock(&radio_mutex)==0;}
static void radio_unlock(void){assert(!pthread_mutex_unlock(&radio_mutex));}
static uint64_t get_time(void){return clock_ms;}
static unsigned radio_swr_trips(void){return swr_count;}
static bool radio_freedv_startup_waiting(void){return false;}
static bool freedv_busy(void){return false;}
bool hw_tx_keyed_by_gui(void){return cached.route.modem || cached.route.ptt;}
static bool ctl_freedv_acquire(void){return true;}
static bool ctl_freedv_release(bool restored){return restored;}
static bool fd_writer_voice_begin(void){if(!writer_allowed)return false;++voice_scope;if(scheduler_on_gui_thread()&&atomic_exchange(&press_during_voice_begin,false))panel_pressed=true;return true;}
static void fd_writer_voice_end(void){assert(voice_scope);--voice_scope;}
void hw_poweroff(void){assert(voice_scope);++terminal_poweroffs;}
void hw_bb_reset(void){assert(voice_scope);++terminal_resets;}
static int32_t param_i_get(int key){return cfg[key];}
static int32_t cparam_i_get(int key){return key==cfg_fg_freq?cfg[cfg_band_current_vfo]?cfg[cfg_band_vfob_freq]:cfg[cfg_band_vfoa_freq]:cfg[key];}
static float param_f_get(int key){return key==cfg_pwr?cfg[key]/10.0f:0.0f;}
static int cfg_transverter_shift_for(int32_t hz){(void)hz;return 0;}
static bool cfg_is_valid_hw_freq(int32_t hz){return hz>=100000 && hz<=55000000;}
static bool cfg_is_tx_freq(int32_t hz){return legal_frequency && hz>=100000 && hz<=54000000;}
static const char *cfg_tx_block(void){return global_tx_block?"External receiver not in bypass":NULL;}
static const char *cfg_tx_refusal(int32_t hz){
    assert(scheduler_on_gui_thread());
    const char *block=cfg_tx_block();if(block)return block;
    switch(ham_tx_check(hz,cfg_is_tx_freq(hz),false,outside_bands_allowed)){
    case HAM_TX_OK:return NULL;case HAM_TX_RX_ONLY:return "Receive only";
    case HAM_TX_OFF_BAND:return "Outside amateur bands";
    }assert(false);return "Invalid policy";
}
static bool btaudio_speaker_muted(void){return false;}
static uint32_t cfg_atu_get_network(void){return 123;}
static void cfg_pwr_set(float watts){assert(scheduler_on_gui_thread());cfg[cfg_pwr]=(int)lroundf(watts*10);}
static int cfg_mode_change_freq_step(bool up){return up?100:10;}
bool cfg_voice_resolve(fd_local_operation op,int32_t value,fd_voice_tuning *out){
    *out=(fd_voice_tuning){cparam_i_get(cfg_fg_freq),(unsigned)cfg[cfg_band_current_vfo],op,value};
    if(atomic_exchange(&press_during_resolve,false))panel_pressed=true;
    if(op==FD_LOCAL_FREQUENCY){out->frequency=value;return cfg_is_valid_hw_freq(value);}
    if(op==FD_LOCAL_VFO && value>=0 && value<=1){out->vfo=(unsigned)value;out->frequency=cfg[value?cfg_band_vfob_freq:cfg_band_vfoa_freq];return true;}
    return false;
}
bool cfg_voice_apply(fd_voice_tuning *t){
    assert(scheduler_on_gui_thread());cfg[cfg_band_current_vfo]=(int)t->vfo;
    cfg[t->vfo?cfg_band_vfob_freq:cfg_band_vfoa_freq]=t->frequency;return true;
}
static bool freedv_observe_locked(fd_observation *out){
    freedv_packet_tx_observation p=freedv_packet_tx_read(&packet_tx_tracker,clock_ms);
    *out=(fd_observation){p.known,p.known && p.age_ms<=250,p.tx};return out->known;
}
static bool freedv_rx(const fd_observation *o){return o->known && o->fresh && !o->tx;}
bool hw_get_freedv_cached_snapshot(hw_freedv_cached_snapshot_t *out){*out=cached;return true;}
bool hw_resend_all(void){++resends;return !atomic_exchange(&resend_fail_once,false);}
int32_t hw_get_i(hw_param_t p){(void)p;return 0;}
static void wrote(void){++all_writes;if(scheduler_on_gui_thread())++gui_writes;}
bool hw_set_i(hw_param_t p,int32_t value){
    wrote();
    if(p==HW_MODEM){cached.route.modem=value!=0;if(value)++modem_on_writes;}
    if(p==HW_PTT){cached.route.ptt=value!=0;if(value)++ptt_on_writes;}
    if(p==HW_RECORD)cached.route.record=value!=0;
    if(p==HW_LINE_IN)cached.line_in=(uint8_t)value;
    if(p==HW_MONI)cached.monitor_level=(uint8_t)value;
    if(p==HW_VOX)cached.vox_enabled=value!=0;
    if(p==HW_KEY_TRAIN)key_train=value!=0;
    if(p==HW_OPEN_TX)calibration_open=value!=0;
    if(p==HW_FILTER1_LOW)cached.rx_filter_low_hz=(int16_t)value;
    if(p==HW_FILTER1_HIGH)cached.rx_filter_high_hz=(int16_t)value;
    if(p==HW_VFO_CUR)cached.active_vfo=(uint8_t)value;
    if(((int)p==stop_during_write && !value) || (p==HW_MODEM && value && stop_during_modem_on))radio_usb_inhibit(NULL);
    if(p==HW_MODEM && value && release_during_modem_on)panel_pressed=false;
    if(p==HW_PTT && !value && atomic_exchange(&block_during_ptt,false))global_tx_block=true;
    if(p==HW_MODEM && value && atomic_exchange(&block_during_modem,false))global_tx_block=true;
    return true;
}
bool hw_set_f(hw_param_t p,float value){wrote();if(p==HW_TXPWR_F)cached.requested_power_tenths_watt=(uint8_t)lroundf(value*10);return true;}
bool hw_vfo_set_freq(radio_vfo_t vfo,int32_t freq){wrote();if(vfo==RADIO_VFO_A)cached.vfo_a_hz=(uint32_t)freq;else cached.vfo_b_hz=(uint32_t)freq;return true;}
bool hw_vfo_set_mode(radio_vfo_t vfo,radio_mode_t mode){wrote();if(vfo==RADIO_VFO_A)cached.backend_mode_a=mode;else cached.backend_mode_b=mode;return true;}
void hw_vfo_set_agc(radio_vfo_t vfo,radio_agc_t agc){wrote();(void)vfo;(void)agc;}
void hw_vfo_set_pre(radio_vfo_t vfo,bool on){wrote();(void)vfo;(void)on;}
void hw_vfo_set_att(radio_vfo_t vfo,bool on){wrote();(void)vfo;(void)on;}
void hw_pin_set(hw_pin_t pin,bool on){assert(pin==HW_PIN_MORSE_KEY);if(on)++pin_on_writes;}
/* The production core performs bounded base-calibration I/O before declaring
 * the worker profile ready. This endpoint records admission/failure ordering. */
static bool freedv_usb_open_tx_apply(bool allow){
    assert(voice_scope && !scheduler_on_gui_thread());
    freedv_packet_tx_observation packet=freedv_packet_tx_read(&packet_tx_tracker,get_time());
    assert(packet.known && packet.age_ms<=250 && !packet.tx && !hw_tx_keyed_by_gui() && !morse_on);
    ++calibration_calls;
    if(atomic_exchange(&calibration_fail_once,false))return false;
    return hw_set_i(HW_OPEN_TX,allow && !cfg_tx_block());
}
static void freedv_usb_policy_released(void){
    assert(scheduler_on_gui_thread() && !radio_usb_owned());++policy_release_calls;
}
#include "src/freedv/usb_radio.inc"
#undef fd_usb_panel_poll
static void observed(bool tx){uint64_t now=atomic_fetch_add(&clock_ms,1)+1;freedv_packet_tx_note(&packet_tx_tracker,tx,now);state=tx?RADIO_TX:RADIO_RX;}
static void ready(void){
    for(unsigned i=0;i<1000;i++){
        observed(false);fd_usb_inputs in;
        if(radio_usb_observe(NULL,&in) && in.configuration_ready)return;
        usleep(1000);
    }assert(!"radio profile did not become ready");
}
static void claim(void){
    panel_pressed=false;panel_valid=true;writer_allowed=true;legal_frequency=true;
    press_during_resolve=false;press_during_voice_begin=false;
    global_tx_block=false;outside_bands_allowed=false;block_during_ptt=false;block_during_modem=false;
    calibration_fail_once=false;calibration_open=false;key_train=false;
    memset(cfg,0,sizeof(cfg));cfg[cfg_band_vfoa_freq]=cfg[cfg_band_vfob_freq]=7074000;cfg[cfg_pwr]=50;
    cfg[cfg_cur_filter_low]=300;cfg[cfg_cur_filter_high]=2700;cfg[cfg_volume]=10;
    cfg[cfg_band_vfoa_mode]=cfg[cfg_band_vfob_mode]=radio_mode_lsb;cfg[cfg_band_if_shift]=250;
    cached=(hw_freedv_cached_snapshot_t){.vfo_a_hz=7073750,.vfo_b_hz=7073750,.requested_power_tenths_watt=50};
    stop_during_write=-1;stop_during_modem_on=false;release_during_modem_on=false;observed(false);
    /* Claim itself reserves/copies only; worker I/O cannot run under GUI lock. */
    unsigned calls=gui_writes;
    assert(radio_usb_claim());assert(radio_usb_owned());ready();assert(gui_writes==calls);
    assert(cached.vfo_a_hz==7074000 && cached.requested_power_tenths_watt==50);
    assert(cached.rx_filter_low_hz==100 && cached.rx_filter_high_hz==3100);
    assert(calibration_open && key_train && !cached.vox_enabled);
    assert(!atomic_load(&usb_radio.rf_allowed));
}
static void release(bool terminal){
    radio_usb_clear(NULL,true);panel_pressed=terminal;observed(false);
    unsigned calls=gui_writes;
    assert(!(terminal?radio_usb_terminal_restore():radio_usb_restore()));
    assert(gui_writes==calls);
    for(unsigned i=0;i<1000;i++){
        observed(false);if(radio_usb_release()){assert(!radio_usb_owned());panel_pressed=false;return;}
        usleep(1000);
    }assert(!"radio restoration did not complete");
}
int main(void){
    gui=pthread_self();radio_lock();assert(!radio_usb_claim());radio_unlock();
    claim();unsigned initial=modem_on_writes;
    /* A PTT press during resolution or scope admission must leave the current
     * profile, revision and hardware untouched; a later press is not a retune. */
    for(unsigned phase=0;phase<2;++phase){
        uint64_t before_revision=atomic_load(&usb_radio.requested_revision);unsigned before_writes=all_writes;
        int32_t before_frequency=cparam_i_get(cfg_fg_freq);
        if(!phase)press_during_resolve=true;else press_during_voice_begin=true;
        assert(!radio_usb_request(FD_LOCAL_FREQUENCY,7120000));
        assert(panel_pressed && !voice_scope && atomic_load(&usb_radio.requested_revision)==before_revision);
        assert(all_writes==before_writes && cparam_i_get(cfg_fg_freq)==before_frequency);
        pthread_mutex_lock(&usb_radio.queue_mutex);assert(!usb_radio.profile_pending);pthread_mutex_unlock(&usb_radio.queue_mutex);
        panel_pressed=false;observed(false);
    }
    panel_pressed=true;assert(!radio_usb_key(NULL,0));assert(modem_on_writes==initial);
    panel_pressed=false;clock_ms+=251;assert(!radio_usb_allow_tx(true));
    observed(true);assert(!radio_usb_allow_tx(true));observed(false);
    panel_pressed=true;assert(!radio_usb_allow_tx(true));panel_pressed=false;
    panel_valid=false;assert(!radio_usb_allow_tx(true));panel_valid=true;
    ++swr_count;assert(!radio_usb_allow_tx(true));--swr_count;
    assert(radio_usb_allow_tx(true));panel_pressed=true;
    assert(!radio_usb_key(NULL,1));assert(modem_on_writes==initial);
    writer_allowed=false;assert(!radio_usb_key(NULL,0));writer_allowed=true;
    unsigned before_calibration=calibration_calls;
    assert(radio_usb_key(NULL,0));assert(cached.route.modem && modem_on_writes==initial+1);
    assert(calibration_calls==before_calibration); /* no cold calibration in the PTT path */
    observed(true);assert(radio_usb_request(FD_LOCAL_POWER,34));
    assert(cfg[cfg_pwr]==34);assert(radio_usb_power_tenths()==34);
    for(unsigned i=0;i<1000;i++){
        radio_lock();bool applied=cached.requested_power_tenths_watt==34;radio_unlock();
        if(applied)break;
        usleep(1000);assert(i<999);
    }
    assert(cached.route.modem); /* live power never clears an active turn */
    assert(!radio_usb_request(FD_LOCAL_FREQUENCY,7100000));
    radio_usb_clear(NULL,false);panel_pressed=false;observed(false);
    uint64_t revision=atomic_load(&usb_radio.requested_revision);
    assert(radio_usb_request(FD_LOCAL_STEP,1));assert(atomic_load(&usb_radio.requested_revision)==revision);
    unsigned calls=gui_writes;
    for(int i=0;i<20;i++)assert(radio_usb_request(FD_LOCAL_FREQUENCY,7100000+i*100));
    assert(gui_writes==calls);
    assert(cparam_i_get(cfg_fg_freq)==7101900);assert(!atomic_load(&usb_radio.rf_allowed));
    ready();assert(cached.vfo_a_hz==7101900);assert(cfg[cfg_band_if_shift]==250);
    assert(radio_usb_request(FD_LOCAL_FREQUENCY,1000000));ready();assert(!radio_usb_allow_tx(true));
    assert(!calibration_open && key_train && !cached.vox_enabled && !cached.requested_power_tenths_watt);
    assert(radio_usb_request(FD_LOCAL_POWER,42));
    for(unsigned i=0;i<1000;i++){
        pthread_mutex_lock(&usb_radio.queue_mutex);bool queued=usb_radio.power_pending;pthread_mutex_unlock(&usb_radio.queue_mutex);
        if(!queued)break;usleep(1000);assert(i<999);
    }
    radio_lock();assert(!cached.requested_power_tenths_watt && key_train && !cached.vox_enabled);radio_unlock();
    assert(radio_usb_request(FD_LOCAL_FREQUENCY,7101900));ready();assert(radio_usb_allow_tx(true));
    release(false);
    assert(cached.vfo_a_hz==7101650 && cached.requested_power_tenths_watt==42);
    assert(cached.backend_mode_a==radio_mode_lsb && cached.rx_filter_low_hz==300);

    claim();initial=modem_on_writes;assert(radio_usb_allow_tx(true));panel_pressed=true;
    stop_during_write=HW_PTT;assert(!radio_usb_key(NULL,0));
    assert(usb_radio.halted && !usb_radio.rf_allowed && !modem_on && modem_on_writes==initial);release(false);
    claim();initial=modem_on_writes;assert(radio_usb_allow_tx(true));panel_pressed=true;
    stop_during_modem_on=true;assert(!radio_usb_key(NULL,0));
    assert(modem_on_writes==initial+1 && usb_radio.halted && !modem_on && !cached.route.modem && !cached.route.ptt);
    release(true); /* terminal exit does not require a held panel PTT to release */
    claim();assert(radio_usb_allow_tx(true));panel_pressed=true;release_during_modem_on=true;
    assert(!radio_usb_key(NULL,0));assert(!cached.route.modem && !cached.route.ptt);release(false);
    claim();stop_during_write=HW_PTT;
    unsigned before=all_writes;assert(radio_usb_request(FD_LOCAL_FREQUENCY,7120000));
    for(unsigned i=0;i<1000 && !usb_radio.halted;i++)usleep(1000);
    assert(usb_radio.halted && !usb_radio.rf_allowed);
    radio_lock();assert(all_writes==before+2);radio_unlock(); /* no writes after inhibited PTT-off */
    stop_during_write=-1;resend_fail_once=true;unsigned previous_resends=resends;
    release(false);assert(resends>=previous_resends+2); /* restore retries a failed submission */
    claim();assert(!radio_usb_terminal_authorize());assert(!radio_usb_terminal_hardware(false));
    terminal_replaying=true;assert(radio_usb_terminal_authorize());terminal_replaying=false;
    assert(radio_usb_terminal_hardware(false));assert(radio_usb_terminal_hardware(true));
    assert(terminal_poweroffs==1 && terminal_resets==1);release(true);

    /* Hardware TX range and software amateur-band policy are distinct. A
     * complete worker profile may open only after the GUI accepts that policy. */
    claim();initial=modem_on_writes;
    assert(radio_usb_request(FD_LOCAL_FREQUENCY,5060000));ready();
    assert(!radio_usb_allow_tx(true) && !calibration_open && !cached.requested_power_tenths_watt);
    panel_pressed=true;assert(!radio_usb_key(NULL,0));assert(modem_on_writes==initial);panel_pressed=false;
    outside_bands_allowed=true;assert(radio_usb_request(FD_LOCAL_FREQUENCY,5060000));ready();
    assert(calibration_open && radio_usb_allow_tx(true));
    outside_bands_allowed=false;usb_radio.saved.vox_enabled=true;release(false);
    assert(!calibration_open && !cached.requested_power_tenths_watt && key_train && !cached.vox_enabled);

    /* Restoring a permitted RX VFO must not restore RF power for a refused
     * ordinary split/shift TX target. */
    claim();cfg[cfg_band_split]=1;cfg[cfg_band_vfob_freq]=5060000;
    usb_radio.saved.vox_enabled=true;release(false);
    assert(!calibration_open && !cached.requested_power_tenths_watt && key_train && !cached.vox_enabled);

    claim();cfg[cfg_key_train]=1;cfg[cfg_band_vfoa_mode]=radio_mode_cw;
    release(false);assert(calibration_open && !cached.requested_power_tenths_watt && key_train);

    claim();initial=modem_on_writes;assert(radio_usb_allow_tx(true));
    global_tx_block=true;fd_usb_inputs blocked;assert(radio_usb_observe(NULL,&blocked));
    assert(blocked.protection && !blocked.tx_eligible);panel_pressed=true;
    assert(!radio_usb_key(NULL,0) && modem_on_writes==initial);
    radio_usb_clear(NULL,false);assert(!cached.requested_power_tenths_watt && key_train && !cached.vox_enabled);
    panel_pressed=false;release(false);assert(!calibration_open && !cached.requested_power_tenths_watt);

    claim();initial=modem_on_writes;assert(radio_usb_allow_tx(true));panel_pressed=true;block_during_ptt=true;
    assert(!radio_usb_key(NULL,0) && modem_on_writes==initial);
    assert(!cached.requested_power_tenths_watt && key_train && !cached.vox_enabled);release(false);
    claim();initial=modem_on_writes;assert(radio_usb_allow_tx(true));panel_pressed=true;block_during_modem=true;
    assert(!radio_usb_key(NULL,0) && modem_on_writes==initial+1);
    assert(!cached.route.modem && !cached.route.ptt && !cached.requested_power_tenths_watt);release(false);

    claim();initial=modem_on_writes;calibration_fail_once=true;
    assert(radio_usb_request(FD_LOCAL_FREQUENCY,7120000));
    for(unsigned i=0;i<1000 && !usb_radio.failed;i++)usleep(1000);
    assert(usb_radio.failed && usb_radio.halted && !usb_radio.rf_allowed);
    fd_usb_inputs unprepared;assert(radio_usb_observe(NULL,&unprepared));
    assert(!unprepared.configuration_ready && !radio_usb_allow_tx(true) && modem_on_writes==initial);release(false);

    assert(!ptt_on_writes && !pin_on_writes && all_writes && policy_release_calls);
    puts("PASS USB radio transactions: async full profile, unshifted tuning/coalescing, RX-only admission, live normal power, Stop races, upstream band/global policy, prepared base gate and receive-only restore; stub hardware only");
    return 0;
}
