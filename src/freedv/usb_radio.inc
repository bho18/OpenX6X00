/* SPDX-License-Identifier: LGPL-2.1-or-later
 * Included in radio.c, using its single control lock and packet telemetry.
 * Deliberately independent of the unimplemented native MCU lease interface. */
#include "usb_radio.h"
#include "usb_ptt.h"
#include <linux/input.h>
#include <sys/ioctl.h>
#include <fcntl.h>
static struct {
    atomic_bool owned,halted,restoring,rf_allowed;
    atomic_uint_fast64_t releases;
    int keypad;
    hw_freedv_cached_snapshot_t saved;
    unsigned swr,errors;
    uint64_t restore_sequence;bool restored;
    pthread_mutex_t input_mutex;
} usb_radio={.keypad=-1,.input_mutex=PTHREAD_MUTEX_INITIALIZER};
bool radio_usb_owned(void){return atomic_load(&usb_radio.owned);}
bool radio_usb_quiet(void){return radio_usb_owned() && !atomic_load(&usb_radio.restoring);}
void radio_usb_inhibit(void *unused){
    (void)unused;
    atomic_store(&usb_radio.halted,true);
    atomic_store(&usb_radio.rf_allowed,false);
}
void radio_usb_edge(bool pressed){(void)pressed;/* GUI consumes edges; the separate evdev fd retains releases below. */}
bool radio_usb_observe(void *unused,fd_usb_inputs *out){
    (void)unused;memset(out,0,sizeof(*out));
    if(!atomic_load(&usb_radio.owned) || usb_radio.keypad<0)return false;
    pthread_mutex_lock(&usb_radio.input_mutex);
    bool pressed=false,valid=fd_usb_panel_poll(usb_radio.keypad,&usb_radio.releases,&pressed);
    uint64_t release=atomic_load(&usb_radio.releases);
    pthread_mutex_unlock(&usb_radio.input_mutex);if(!valid)return false;
    freedv_packet_tx_observation packet=freedv_packet_tx_read(&packet_tx_tracker,get_time());
    *out=(fd_usb_inputs){.valid=true,.pressed=pressed,
        .release=release,.rf_valid=packet.known && packet.age_ms<=250,.rf_tx=packet.tx,.sequence=packet.sequence,
        .protection=radio_swr_trips()!=usb_radio.swr || atomic_load(&freedv_base_errors)!=usb_radio.errors};
    return true;
}
bool radio_usb_allow_tx(bool allow){
    if(!allow){atomic_store(&usb_radio.rf_allowed,false);return true;}
    fd_usb_inputs in;
    if(!radio_usb_owned() || atomic_load(&usb_radio.halted) ||
       !radio_usb_observe(NULL,&in) || !in.valid || in.pressed || !in.rf_valid || in.rf_tx || in.protection)return false;
    atomic_store(&usb_radio.rf_allowed,true);
    /* A concurrent Stop wins even if it landed between observation and store. */
    if(atomic_load(&usb_radio.halted)){atomic_store(&usb_radio.rf_allowed,false);return false;}
    return true;
}
void radio_usb_clear(void *unused,bool halt){
    (void)unused;if(halt)radio_usb_inhibit(NULL);
    if(!atomic_load(&usb_radio.owned))return;
    /* Do not wait for a codec, PA drain, worker join or UI restoration. */
    radio_lock();hw_set_i(HW_MODEM,false);hw_set_i(HW_PTT,false);hw_pin_set(HW_PIN_MORSE_KEY,false);
    atomic_store(&modem_on,false);morse_on=false;radio_unlock();
}
bool radio_usb_key(void *unused,uint64_t expected_release){
    (void)unused;fd_usb_inputs in={0};
    if(!atomic_load(&usb_radio.rf_allowed) || atomic_load(&usb_radio.halted) ||
       !radio_usb_observe(NULL,&in) || !in.valid || !in.pressed || !in.rf_valid || in.rf_tx || in.protection)return false;
    if(!fd_writer_voice_begin())return false;
    radio_lock();hw_freedv_cached_snapshot_t current;
    bool ok=radio_usb_observe(NULL,&in) && in.valid && in.pressed && in.release==expected_release && in.rf_valid && !in.rf_tx && !in.protection &&
        atomic_load(&usb_radio.owned) && atomic_load(&usb_radio.rf_allowed) && !atomic_load(&usb_radio.halted) &&
        hw_get_freedv_cached_snapshot(&current) && hw_freedv_tuning_matches(&usb_radio.saved,&current) &&
        current.requested_power_tenths_watt==10 && !current.route.record && current.line_in==0 && current.monitor_level==0 && !current.vox_enabled &&
        !param_i_get(cfg_band_split) && !param_i_get(cfg_band_tx_shift) && !param_i_get(cfg_rit) && !param_i_get(cfg_xit) &&
        cfg_is_tx_freq(radio_tx_freq());
    /* saved tuning is updated to the temporary USB-DIG configuration at claim. */
    if(ok){
        ok=hw_set_i(HW_RECORD,false) && hw_set_i(HW_PTT,false);
        /* Both preceding writes may block. A Stop or cancelled qualification
         * cannot permit a queued MODEM-on write after that work completes. */
        ok=ok && atomic_load(&usb_radio.rf_allowed) && !atomic_load(&usb_radio.halted) &&
           radio_usb_observe(NULL,&in) && in.valid && in.pressed && in.release==expected_release &&
           in.rf_valid && !in.rf_tx && !in.protection;
        if(ok)ok=hw_set_i(HW_MODEM,true);
        if(atomic_load(&usb_radio.halted) || !atomic_load(&usb_radio.rf_allowed)){
            /* An in-flight MODEM-on transaction cannot be interrupted. If
             * Stop arrived during it, compensate before reporting completion;
             * the controller's priority OFF remains independently queued. */
            hw_set_i(HW_MODEM,false);hw_set_i(HW_PTT,false);ok=false;
        }
        atomic_store(&modem_on,ok);
    }
    radio_unlock();fd_writer_voice_end();return ok;
}
bool radio_usb_claim(void){
    if(radio_freedv_startup_waiting() || freedv_busy() || atomic_load(&usb_radio.owned) ||
       param_i_get(cfg_band_split) || param_i_get(cfg_band_tx_shift) || param_i_get(cfg_rit) || param_i_get(cfg_xit) ||
       cfg_transverter_shift_for(cparam_i_get(cfg_fg_freq)) || !cfg_is_tx_freq(radio_tx_freq()))return false;
    usb_radio.keypad=open(BOARD_DEV_KEYPAD,O_RDONLY|O_NONBLOCK|O_CLOEXEC);if(usb_radio.keypad<0)return false;
    radio_lock();fd_observation rx;
    bool ok=state==RADIO_RX && freedv_observe_locked(&rx) && freedv_rx(&rx) && !freedv_owner &&
        !hw_tx_keyed_by_gui() && hw_get_freedv_cached_snapshot(&usb_radio.saved) &&
        !usb_radio.saved.route.record && !usb_radio.saved.route.modem && !usb_radio.saved.route.ptt && ctl_freedv_acquire();
    if(ok){atomic_store(&usb_radio.owned,true);atomic_store(&usb_radio.halted,false);atomic_store(&usb_radio.rf_allowed,false);atomic_store(&usb_radio.restoring,false);usb_radio.restored=false;usb_radio.swr=radio_swr_trips();usb_radio.errors=atomic_load(&freedv_base_errors);}
    radio_unlock();if(!ok){close(usb_radio.keypad);usb_radio.keypad=-1;return false;}
    fd_usb_inputs in;if(!radio_usb_observe(NULL,&in) || in.pressed){radio_usb_clear(NULL,true);return false;}
    if(!fd_writer_voice_begin()){radio_usb_clear(NULL,true);return false;}
    radio_lock();radio_vfo_t vfo=param_i_get(cfg_band_current_vfo);
    hw_set_i(HW_MODEM,false);hw_set_i(HW_PTT,false);hw_set_i(HW_RECORD,false);hw_set_i(HW_SPLIT,false);
    hw_vfo_set_mode(vfo,radio_mode_usb_dig);hw_vfo_set_agc(vfo,radio_agc_fast);
    hw_vfo_set_pre(vfo,false);hw_vfo_set_att(vfo,false);hw_set_i(HW_RFG,100);hw_set_i(HW_SQL,0);
    hw_set_i(HW_FILTER1_LOW,100);hw_set_i(HW_FILTER1_HIGH,3100);
    hw_set_i(HW_NR,false);hw_set_i(HW_NB,false);hw_set_i(HW_DNF,false);hw_set_i(HW_DNF_AUTO,false);
    hw_set_i(HW_COMP_ON,true);hw_set_i(HW_VOX,false);hw_set_i(HW_KEY_TRAIN,true);
    hw_set_i(HW_IF_SHIFT_ON,false);hw_set_i(HW_IF_SHIFT_HZ,0);
    hw_set_i(HW_MONI,0);hw_set_i(HW_LINE_IN,0);hw_set_i(HW_LINE_OUT,10);hw_set_i(HW_RXVOL,0);
    hw_set_f(HW_TXPWR_F,1.0f);ok=hw_resend_all();
    /* Compare tuning against the effective temporary cached configuration;
     * preserve the untouched preferences for restoration, not this snapshot. */
    hw_freedv_cached_snapshot_t applied;
    if(ok && hw_get_freedv_cached_snapshot(&applied)){
        usb_radio.saved.backend_mode_a=applied.backend_mode_a;usb_radio.saved.backend_mode_b=applied.backend_mode_b;
        usb_radio.saved.rx_filter_low_hz=applied.rx_filter_low_hz;usb_radio.saved.rx_filter_high_hz=applied.rx_filter_high_hz;
    }else ok=false;
    radio_unlock();fd_writer_voice_end();if(!ok)radio_usb_clear(NULL,true);return ok;
}
bool radio_usb_restore(void){
    if(!atomic_load(&usb_radio.owned))return true;
    fd_usb_inputs in;if(!radio_usb_observe(NULL,&in) || in.pressed || !in.rf_valid || in.rf_tx)return false;
    if(usb_radio.restored){
        return in.sequence>usb_radio.restore_sequence;
    }
    if(!fd_writer_voice_begin())return false;
    /* Reuse the preference-based restoration routine, without native lease
     * operations. Hardware audio preferences were never changed persistently. */
    voice_radio.saved=usb_radio.saved;
    atomic_store(&usb_radio.restoring,true);
    bool ok=false;fd_writer_voice_end();voice_restore_on_gui(&ok);
    if(!ok || !radio_usb_observe(NULL,&in) || in.pressed || !in.rf_valid || in.rf_tx)return false;
    usb_radio.restored=true;usb_radio.restore_sequence=in.sequence;return false;
}
bool radio_usb_release(void){
    if(!radio_usb_owned())return true;
    fd_usb_inputs in;if(!usb_radio.restored || !radio_usb_observe(NULL,&in) || in.pressed || !in.rf_valid || in.rf_tx || in.sequence<=usb_radio.restore_sequence)return false;
    radio_lock();bool ok=ctl_freedv_release(true);radio_unlock();
    if(ok){atomic_store(&usb_radio.rf_allowed,false);atomic_store(&usb_radio.owned,false);close(usb_radio.keypad);usb_radio.keypad=-1;}return ok;
}
