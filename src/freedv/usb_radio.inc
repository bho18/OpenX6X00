/* SPDX-License-Identifier: LGPL-2.1-or-later
 * Stock-baseband USB binding. GUI settings are desired state only while owned;
 * one worker serializes complete profiles, coalesced tuning and restoration.
 * Cache completion followed by RX telemetry is not an MCU apply ACK. */
#include "usb_radio.h"
#include "usb_ptt.h"
#include "../cfg/voice_settings.h"
#include <linux/input.h>
#include <sys/ioctl.h>
#include <fcntl.h>

typedef struct {
    int32_t frequency[2], mode[2], agc[2], pre[2], att[2];
    int32_t power, vfo, tx_i, tx_q, atu_enabled, fft_decimation;
    uint32_t atu_network;
    float dac_gain;
    bool tx_eligible;
    /* Ordinary current-band settings, captured on GUI only for restoration. */
    int32_t rfg, sql, filter_low, filter_high, rit, xit, split, key_train;
    int32_t nr, nb, dnf, dnf_auto, comp, volume, monitor, if_shift, agc_time;
    uint64_t revision;
} usb_radio_profile;
static struct {
    atomic_bool owned,halted,restoring,rf_allowed,failed,worker_done,terminal,terminal_authorized;
    atomic_uint_fast64_t releases,requested_revision,applied_revision,applied_sequence;
    atomic_int power;
    atomic_int keypad;
    hw_freedv_cached_snapshot_t saved,expected;
    usb_radio_profile pending,restore_profile;
    bool profile_pending,power_pending,restore_pending,restore_inflight,restored;
    uint64_t restore_sequence,restore_retry_ms;
    unsigned swr,errors;
    pthread_mutex_t input_mutex,queue_mutex;
    pthread_cond_t queue_cond;
} usb_radio={.keypad=-1,.input_mutex=PTHREAD_MUTEX_INITIALIZER,
    .queue_mutex=PTHREAD_MUTEX_INITIALIZER,.queue_cond=PTHREAD_COND_INITIALIZER};
bool radio_usb_owned(void){return atomic_load(&usb_radio.owned);}
/* Remain suppressed throughout restoration, including its fresh-RX wait. */
bool radio_usb_quiet(void){return radio_usb_owned();}
int radio_usb_power_tenths(void){return atomic_load(&usb_radio.power);}
void radio_usb_inhibit(void *unused){
    (void)unused;atomic_store(&usb_radio.halted,true);atomic_store(&usb_radio.rf_allowed,false);
    pthread_cond_signal(&usb_radio.queue_cond);
}
bool radio_usb_terminal_authorize(void){
    if(!radio_usb_owned() || !scheduler_on_gui_thread() || !freedv_navigation_replaying())return false;
    radio_usb_inhibit(NULL);atomic_store(&usb_radio.terminal_authorized,true);return true;
}
bool radio_usb_terminal_hardware(bool reset){
    if(!radio_usb_owned() || !atomic_load(&usb_radio.terminal_authorized) || !fd_writer_voice_begin())return false;
    if(reset)hw_bb_reset();else hw_poweroff();
    fd_writer_voice_end();return true;
}
void radio_usb_edge(bool pressed){(void)pressed;/* separate evdev fd retains releases */}
bool radio_usb_observe(void *unused,fd_usb_inputs *out){
    (void)unused;memset(out,0,sizeof(*out));
    if(!radio_usb_owned() || usb_radio.keypad<0)return false;
    pthread_mutex_lock(&usb_radio.input_mutex);
    bool pressed=false,valid=fd_usb_panel_poll(usb_radio.keypad,&usb_radio.releases,&pressed);
    uint64_t release=atomic_load(&usb_radio.releases);
    pthread_mutex_unlock(&usb_radio.input_mutex);if(!valid)return false;
    freedv_packet_tx_observation packet=freedv_packet_tx_read(&packet_tx_tracker,get_time());
    uint64_t wanted=atomic_load(&usb_radio.requested_revision);
    pthread_mutex_lock(&usb_radio.queue_mutex);bool eligible=usb_radio.pending.tx_eligible;
    pthread_mutex_unlock(&usb_radio.queue_mutex);
    bool fresh=packet.known && packet.age_ms<=250;
    *out=(fd_usb_inputs){.valid=true,.pressed=pressed,.release=release,
        .rf_valid=fresh,.rf_tx=packet.tx,.sequence=packet.sequence,
        .configuration_ready=wanted && wanted==atomic_load(&usb_radio.applied_revision) &&
            packet.sequence>atomic_load(&usb_radio.applied_sequence) && fresh && !packet.tx &&
            !atomic_load(&usb_radio.restoring) && !atomic_load(&usb_radio.failed),
        .tuning_revision=wanted,
        .tx_eligible=eligible,
        .protection=atomic_load(&usb_radio.failed) || radio_swr_trips()!=usb_radio.swr ||
            atomic_load(&freedv_base_errors)!=usb_radio.errors};
    return true;
}
bool radio_usb_allow_tx(bool allow){
    if(!allow){atomic_store(&usb_radio.rf_allowed,false);return true;}
    fd_usb_inputs in;
    if(!radio_usb_owned() || atomic_load(&usb_radio.halted) || !radio_usb_observe(NULL,&in) ||
       !in.valid || in.pressed || !in.rf_valid || in.rf_tx || in.protection || !in.configuration_ready)return false;
    pthread_mutex_lock(&usb_radio.queue_mutex);bool eligible=usb_radio.pending.tx_eligible;
    pthread_mutex_unlock(&usb_radio.queue_mutex);if(!eligible)return false;
    atomic_store(&usb_radio.rf_allowed,true);
    if(atomic_load(&usb_radio.halted)){atomic_store(&usb_radio.rf_allowed,false);return false;}
    return true;
}
bool radio_usb_permit(void *unused,bool allow){(void)unused;return radio_usb_allow_tx(allow);}
void radio_usb_clear(void *unused,bool halt){
    (void)unused;if(halt)radio_usb_inhibit(NULL);
    if(!radio_usb_owned())return;
    radio_lock();hw_set_i(HW_MODEM,false);hw_set_i(HW_PTT,false);hw_pin_set(HW_PIN_MORSE_KEY,false);
    atomic_store(&modem_on,false);morse_on=false;radio_unlock();
}
bool radio_usb_key(void *unused,uint64_t expected_release){
    (void)unused;fd_usb_inputs in={0};
    if(!atomic_load(&usb_radio.rf_allowed) || atomic_load(&usb_radio.halted) ||
       !radio_usb_observe(NULL,&in) || !in.valid || !in.pressed || !in.configuration_ready ||
       !in.rf_valid || in.rf_tx || in.protection)return false;
    if(!fd_writer_voice_begin())return false;
    radio_lock();hw_freedv_cached_snapshot_t current,expected;
    pthread_mutex_lock(&usb_radio.queue_mutex);expected=usb_radio.expected;
    bool eligible=usb_radio.pending.tx_eligible;pthread_mutex_unlock(&usb_radio.queue_mutex);
    bool ok=radio_usb_observe(NULL,&in) && in.valid && in.pressed && in.release==expected_release &&
        in.configuration_ready && in.rf_valid && !in.rf_tx && !in.protection && eligible &&
        radio_usb_owned() && atomic_load(&usb_radio.rf_allowed) && !atomic_load(&usb_radio.halted) &&
        hw_get_freedv_cached_snapshot(&current) && hw_freedv_tuning_matches(&expected,&current) &&
        !current.route.record && current.line_in==0 && current.monitor_level==0 && !current.vox_enabled;
    if(ok){
        /* A power detent immediately before PTT must not fail admission merely
         * because its targeted mailbox write has not run yet. */
        hw_set_f(HW_TXPWR_F,radio_usb_power_tenths()/10.0f);
        hw_set_i(HW_RECORD,false);hw_set_i(HW_PTT,false);
        /* Stop and a retune both win while either preceding I/O was blocked. */
        ok=atomic_load(&usb_radio.rf_allowed) && !atomic_load(&usb_radio.halted) &&
           radio_usb_observe(NULL,&in) && in.valid && in.pressed && in.release==expected_release &&
           in.configuration_ready && in.rf_valid && !in.rf_tx && !in.protection;
        if(ok)ok=hw_set_i(HW_MODEM,true);
        bool retained=radio_usb_observe(NULL,&in) && in.valid && in.pressed && in.release==expected_release &&
            in.rf_valid && !in.protection &&
            atomic_load(&usb_radio.requested_revision)==atomic_load(&usb_radio.applied_revision);
        if(atomic_load(&usb_radio.halted) || !atomic_load(&usb_radio.rf_allowed) || !retained){
            hw_set_i(HW_MODEM,false);hw_set_i(HW_PTT,false);ok=false;
        }
        atomic_store(&modem_on,ok);
    }
    radio_unlock();fd_writer_voice_end();return ok;
}
/* Settings access and computed observers belong to the GUI thread. No worker
 * reads mutable cfg objects, and no cfg observer can write hardware while owned. */
static bool usb_radio_capture(usb_radio_profile *p,bool restore){
    memset(p,0,sizeof(*p));p->vfo=param_i_get(cfg_band_current_vfo);
    p->frequency[0]=param_i_get(cfg_band_vfoa_freq);p->frequency[1]=param_i_get(cfg_band_vfob_freq);
    if(p->vfo<0 || p->vfo>1 || !cfg_is_valid_hw_freq(p->frequency[p->vfo]) ||
       (!restore && cfg_transverter_shift_for(p->frequency[p->vfo])))return false;
    p->power=radio_usb_power_tenths();p->tx_eligible=cfg_is_tx_freq(p->frequency[p->vfo]);
    p->tx_i=param_i_get(cfg_band_tx_i_offset);p->tx_q=param_i_get(cfg_band_tx_q_offset);
    for(int32_t zoom=param_i_get(cfg_mode_zoom);zoom>1;zoom>>=1)p->fft_decimation++;
    p->dac_gain=param_f_get(cfg_band_dac_offset);p->atu_enabled=param_i_get(cfg_atu_enabled);p->atu_network=cfg_atu_get_network();
    if(!restore)return true;
    p->mode[0]=param_i_get(cfg_band_vfoa_mode);p->mode[1]=param_i_get(cfg_band_vfob_mode);
    p->agc[0]=param_i_get(cfg_band_vfoa_agc);p->agc[1]=param_i_get(cfg_band_vfob_agc);
    p->pre[0]=param_i_get(cfg_band_vfoa_pre);p->pre[1]=param_i_get(cfg_band_vfob_pre);
    p->att[0]=param_i_get(cfg_band_vfoa_att);p->att[1]=param_i_get(cfg_band_vfob_att);
    p->rfg=param_i_get(cfg_rfgain);p->sql=param_i_get(cfg_squelch);
    p->filter_low=cparam_i_get(cfg_cur_filter_low);p->filter_high=cparam_i_get(cfg_cur_filter_high);
    p->rit=param_i_get(cfg_rit);p->xit=param_i_get(cfg_xit);p->split=param_i_get(cfg_band_split);
    p->key_train=param_i_get(cfg_key_train);p->nr=param_i_get(cfg_nr);p->nb=param_i_get(cfg_nb);
    p->dnf=param_i_get(cfg_dnf);p->dnf_auto=param_i_get(cfg_dnf_auto);p->comp=param_i_get(cfg_comp);
    bool muted=mute || tci_mute || btaudio_speaker_muted() || param_i_get(cfg_speaker_off);
    p->volume=muted?0:param_i_get(cfg_volume);p->monitor=muted || !p->volume?0:param_i_get(cfg_moni);
    p->if_shift=param_i_get(cfg_band_if_shift);
    radio_mode_t mode=p->mode[p->vfo];radio_agc_t agc=p->agc[p->vfo];
    if(agc==radio_agc_auto && (mode==radio_mode_cw || mode==radio_mode_cwr) && param_i_get(cfg_cw_decoder))p->agc[p->vfo]=agc=radio_agc_fast;
    p->agc_time=agc==radio_agc_fast?100:agc==radio_agc_auto?
        ((mode==radio_mode_cw || mode==radio_mode_cwr)?100:
         (mode==radio_mode_am || mode==radio_mode_sam || mode==radio_mode_nfm || mode==radio_mode_wfm)?1000:500):1000;
    for(unsigned i=0;i<2;i++)p->frequency[i]-=cfg_transverter_shift_for(p->frequency[i])+p->if_shift;
    return true;
}
static bool usb_radio_continue(bool restore,uint64_t revision){
    return radio_usb_owned() && (restore || (!atomic_load(&usb_radio.halted) &&
        !atomic_load(&usb_radio.restoring) && revision==atomic_load(&usb_radio.requested_revision)));
}
/* A cancelled worker checks between every possibly blocking hardware operation.
 * Clear has its own control worker and obtains this same lock next. */
#define USB_WRITE(call) do { if(!usb_radio_continue(restore,p->revision))return false; (void)(call); } while(0)
static bool usb_radio_apply(const usb_radio_profile *p,bool restore){
    USB_WRITE(hw_set_i(HW_MODEM,false));USB_WRITE(hw_set_i(HW_PTT,false));USB_WRITE(hw_set_i(HW_RECORD,false));
    USB_WRITE(hw_set_i(HW_SPLIT,restore?p->split:false));
    USB_WRITE(hw_set_i(HW_RIT,restore?p->rit:0));USB_WRITE(hw_set_i(HW_XIT,restore?p->xit:0));
    USB_WRITE(hw_set_i(HW_IF_SHIFT_ON,restore && p->if_shift!=0));USB_WRITE(hw_set_i(HW_IF_SHIFT_HZ,restore?p->if_shift:0));
    USB_WRITE(hw_set_i(HW_VFO_CUR,p->vfo));
    /* Reapply both cached frequencies explicitly; no IF/RIT/XIT shift in USB mode. */
    for(unsigned i=0;i<2;i++){
        USB_WRITE(hw_vfo_set_freq((radio_vfo_t)i,p->frequency[i]));
        if(restore || i==(unsigned)p->vfo){
            USB_WRITE(hw_vfo_set_mode((radio_vfo_t)i,restore?p->mode[i]:radio_mode_usb_dig));
            USB_WRITE(hw_vfo_set_agc((radio_vfo_t)i,restore?p->agc[i]:radio_agc_fast));
            USB_WRITE(hw_vfo_set_pre((radio_vfo_t)i,restore?p->pre[i]:false));
            USB_WRITE(hw_vfo_set_att((radio_vfo_t)i,restore?p->att[i]:false));
        }
    }
    /* Force a change before the real offset on each band/profile application. */
    USB_WRITE(hw_set_i(HW_TX_I_OFFSET,p->tx_i==hw_get_i(HW_TX_I_OFFSET)?p->tx_i+1:p->tx_i));
    USB_WRITE(hw_set_i(HW_TX_I_OFFSET,p->tx_i));
    USB_WRITE(hw_set_i(HW_TX_Q_OFFSET,p->tx_q==hw_get_i(HW_TX_Q_OFFSET)?p->tx_q+1:p->tx_q));
    USB_WRITE(hw_set_i(HW_TX_Q_OFFSET,p->tx_q));USB_WRITE(hw_set_f(HW_DAC_GAIN_F,p->dac_gain));
    USB_WRITE(hw_set_i(HW_ATU_ENABLED,p->atu_enabled));USB_WRITE(hw_set_i(HW_ATU_NETWORK,p->atu_network));
    USB_WRITE(hw_set_i(HW_FFT_DEC,p->fft_decimation));
    USB_WRITE(hw_set_i(HW_RFG,restore?p->rfg:100));USB_WRITE(hw_set_i(HW_SQL,restore?p->sql:0));
    int32_t low=restore?p->filter_low:100,high=restore?p->filter_high:3100;
    if(restore && (p->mode[p->vfo]==radio_mode_am || p->mode[p->vfo]==radio_mode_sam ||
        p->mode[p->vfo]==radio_mode_nfm || p->mode[p->vfo]==radio_mode_wfm))low=-high;
    USB_WRITE(hw_set_i(HW_FILTER1_LOW,low));USB_WRITE(hw_set_i(HW_FILTER1_HIGH,high));
    USB_WRITE(hw_set_i(HW_FILTER2_LOW,low<0?low-FILTER_2_OFFSET_OUT:LV_MAX(0,low-FILTER_2_OFFSET_OUT)));
    USB_WRITE(hw_set_i(HW_FILTER2_HIGH,high+FILTER_2_OFFSET_OUT));USB_WRITE(hw_set_i(HW_AGC_TIME,restore?p->agc_time:100));
    USB_WRITE(hw_set_i(HW_NR,restore?p->nr:false));USB_WRITE(hw_set_i(HW_NB,restore?p->nb:false));
    USB_WRITE(hw_set_i(HW_DNF,restore?p->dnf:false));USB_WRITE(hw_set_i(HW_DNF_AUTO,restore?p->dnf_auto:false));
    USB_WRITE(hw_set_i(HW_COMP_ON,restore?p->comp<=1:true));
    if(restore && p->comp>1)USB_WRITE(hw_set_i(HW_COMP_LEVEL,p->comp-2));
    USB_WRITE(hw_set_i(HW_KEY_TRAIN,restore?p->key_train:true));
    if(restore){
        const hw_freedv_cached_snapshot_t *s=&usb_radio.saved;
        USB_WRITE(hw_set_i(HW_MIC,s->microphone));USB_WRITE(hw_set_i(HW_HMIC,s->hand_mic_gain));
        USB_WRITE(hw_set_i(HW_IMIC,s->internal_mic_gain));USB_WRITE(hw_set_i(HW_LINE_IN,s->line_in));
        USB_WRITE(hw_set_i(HW_LINE_OUT,s->line_out));USB_WRITE(hw_set_i(HW_SPMODE,s->speaker_mode));
        USB_WRITE(hw_set_i(HW_VOX,s->vox_enabled));
    }else{
        USB_WRITE(hw_set_i(HW_VOX,false));USB_WRITE(hw_set_i(HW_LINE_IN,0));USB_WRITE(hw_set_i(HW_LINE_OUT,10));
    }
    USB_WRITE(hw_set_i(HW_RXVOL,restore?p->volume:0));USB_WRITE(hw_set_i(HW_MONI,restore?p->monitor:0));
    USB_WRITE(hw_set_f(HW_TXPWR_F,p->power/10.0f));
    if(!usb_radio_continue(restore,p->revision))return false;
    return hw_resend_all();
}
#undef USB_WRITE
static void *usb_radio_worker(void *unused){
    (void)unused;
    for(;;){
        pthread_mutex_lock(&usb_radio.queue_mutex);
        while(!usb_radio.restore_pending && !usb_radio.profile_pending && !usb_radio.power_pending)
            pthread_cond_wait(&usb_radio.queue_cond,&usb_radio.queue_mutex);
        bool restore=usb_radio.restore_pending;
        if(!restore && usb_radio.profile_pending){
            /* A short mailbox window combines a burst of encoder detents. */
            struct timespec until;clock_gettime(CLOCK_REALTIME,&until);until.tv_nsec+=20000000;
            if(until.tv_nsec>=1000000000){until.tv_nsec-=1000000000;until.tv_sec++;}
            while(!usb_radio.restore_pending && !atomic_load(&usb_radio.halted) &&
                pthread_cond_timedwait(&usb_radio.queue_cond,&usb_radio.queue_mutex,&until)!=ETIMEDOUT){}
            restore=usb_radio.restore_pending;
        }
        bool profile=usb_radio.profile_pending;
        usb_radio_profile p=restore?usb_radio.restore_profile:usb_radio.pending;
        usb_radio.restore_pending=false;usb_radio.profile_pending=false;usb_radio.power_pending=false;
        if(restore)usb_radio.restore_inflight=true;
        pthread_mutex_unlock(&usb_radio.queue_mutex);
        if(!usb_radio_continue(restore,p.revision))continue;
        bool scope=fd_writer_voice_begin(),ok=false;
        if(scope){
            radio_lock();
            if(usb_radio_continue(restore,p.revision)){
                if(restore || profile)ok=usb_radio_apply(&p,restore);
                else {hw_set_f(HW_TXPWR_F,p.power/10.0f);
                    ok=usb_radio_continue(false,p.revision) && hw_resend_all();}
                if(ok && !restore && profile){
                    hw_freedv_cached_snapshot_t expected;
                    ok=hw_get_freedv_cached_snapshot(&expected);
                    if(ok){
                        pthread_mutex_lock(&usb_radio.queue_mutex);usb_radio.expected=expected;pthread_mutex_unlock(&usb_radio.queue_mutex);
                        freedv_packet_tx_observation o=freedv_packet_tx_read(&packet_tx_tracker,get_time());
                        atomic_store(&usb_radio.applied_sequence,o.sequence);
                        atomic_store(&usb_radio.applied_revision,p.revision);
                    }
                }
            }
            radio_unlock();fd_writer_voice_end();
        }
        if(restore){
            freedv_packet_tx_observation o=freedv_packet_tx_read(&packet_tx_tracker,get_time());
            pthread_mutex_lock(&usb_radio.queue_mutex);usb_radio.restored=ok;usb_radio.restore_sequence=o.sequence;
            usb_radio.restore_inflight=false;usb_radio.restore_retry_ms=get_time()+500;
            pthread_mutex_unlock(&usb_radio.queue_mutex);
            if(!ok){atomic_store(&usb_radio.failed,true);continue;}
            atomic_store(&usb_radio.worker_done,true);return NULL;
        }
        if(!ok && usb_radio_continue(false,p.revision)){
            atomic_store(&usb_radio.failed,true);radio_usb_inhibit(NULL);radio_usb_clear(NULL,true);
        }
    }
}
bool radio_usb_claim(void){
    if(!scheduler_on_gui_thread() || radio_freedv_startup_waiting() || freedv_busy() || radio_usb_owned() ||
       cfg_transverter_shift_for(cparam_i_get(cfg_fg_freq)))return false;
    int keypad=open(BOARD_DEV_KEYPAD,O_RDONLY|O_NONBLOCK|O_CLOEXEC);if(keypad<0)return false;
    /* A busy control transaction delays mode entry; it must never freeze LVGL. */
    if(!radio_try_lock()){close(keypad);return false;}
    fd_observation rx;
    bool ok=state==RADIO_RX && freedv_observe_locked(&rx) && freedv_rx(&rx) && !freedv_owner &&
        !hw_tx_keyed_by_gui() && hw_get_freedv_cached_snapshot(&usb_radio.saved) &&
        !usb_radio.saved.route.record && !usb_radio.saved.route.modem && !usb_radio.saved.route.ptt && ctl_freedv_acquire();
    radio_unlock();if(!ok){close(keypad);return false;}
    usb_radio.keypad=keypad;usb_radio.swr=radio_swr_trips();usb_radio.errors=atomic_load(&freedv_base_errors);
    atomic_store(&usb_radio.halted,false);atomic_store(&usb_radio.rf_allowed,false);atomic_store(&usb_radio.failed,false);
    atomic_store(&usb_radio.restoring,false);atomic_store(&usb_radio.worker_done,false);
    atomic_store(&usb_radio.terminal,false);
    atomic_store(&usb_radio.terminal_authorized,false);
    atomic_store(&usb_radio.requested_revision,1);atomic_store(&usb_radio.applied_revision,0);
    atomic_store(&usb_radio.applied_sequence,0);atomic_store(&usb_radio.releases,0);
    int power=(int)lroundf(param_f_get(cfg_pwr)*10.0f);if(power<1)power=1;if(power>100)power=100;
    atomic_store(&usb_radio.power,power);atomic_store(&usb_radio.owned,true);
    fd_usb_inputs in;usb_radio_profile p;
    ok=radio_usb_observe(NULL,&in) && !in.pressed && usb_radio_capture(&p,false);
    if(ok){
        p.revision=1;pthread_mutex_lock(&usb_radio.queue_mutex);usb_radio.pending=p;
        usb_radio.profile_pending=true;usb_radio.power_pending=false;usb_radio.restore_pending=false;
        usb_radio.restore_inflight=false;usb_radio.restore_retry_ms=0;usb_radio.restored=false;
        pthread_mutex_unlock(&usb_radio.queue_mutex);pthread_t worker;
        ok=pthread_create(&worker,NULL,usb_radio_worker,NULL)==0;if(ok)pthread_detach(worker);
    }
    if(!ok){atomic_store(&usb_radio.owned,false);ctl_freedv_release(true);close(keypad);usb_radio.keypad=-1;}
    return ok;
}
bool radio_usb_request(fd_local_operation op,int32_t value){
    if(!scheduler_on_gui_thread() || !radio_usb_owned() || atomic_load(&usb_radio.halted) || atomic_load(&usb_radio.restoring))return false;
    fd_usb_inputs in;if(!radio_usb_observe(NULL,&in) || !in.valid || !in.rf_valid || in.protection)return false;
    bool power=op==FD_LOCAL_POWER,step=op==FD_LOCAL_STEP;
    if(!power && (in.pressed || in.rf_tx || atomic_load(&modem_on)))return false;
    if(power && (value<1 || value>100))return false;
    fd_voice_tuning tuning;
    if(!power && !step && (!cfg_voice_resolve(op,value,&tuning) || cfg_transverter_shift_for(tuning.frequency)))return false;
    if(!fd_writer_voice_begin())return false;
    uint64_t revision=atomic_load(&usb_radio.requested_revision);
    if(!power && !step){
        atomic_store(&usb_radio.rf_allowed,false);
        revision=atomic_fetch_add(&usb_radio.requested_revision,1)+1;
    }
    bool ok=true;
    if(power){cfg_pwr_set(value/10.0f);atomic_store(&usb_radio.power,value);}
    else if(step)cfg_mode_change_freq_step(value>0);
    else ok=cfg_voice_apply(&tuning);
    usb_radio_profile p;
    if(ok && !step)ok=usb_radio_capture(&p,false);
    fd_writer_voice_end();if(!ok){radio_usb_inhibit(NULL);return false;}if(step)return true;
    pthread_mutex_lock(&usb_radio.queue_mutex);
    if(!power){p.revision=revision;usb_radio.profile_pending=true;}
    else {p.revision=atomic_load(&usb_radio.requested_revision);usb_radio.power_pending=true;}
    usb_radio.pending=p;pthread_cond_signal(&usb_radio.queue_cond);pthread_mutex_unlock(&usb_radio.queue_mutex);
    return true;
}
bool radio_usb_restore(void){
    if(!radio_usb_owned())return true;
    fd_usb_inputs in;if(!radio_usb_observe(NULL,&in) ||
        (in.pressed && !atomic_load(&usb_radio.terminal)) || !in.rf_valid || in.rf_tx)return false;
    if(atomic_load(&usb_radio.restoring)){
        pthread_mutex_lock(&usb_radio.queue_mutex);bool ok=usb_radio.restored && in.sequence>usb_radio.restore_sequence;
        if(!usb_radio.restored && !usb_radio.restore_pending && !usb_radio.restore_inflight && get_time()>=usb_radio.restore_retry_ms){
            usb_radio.restore_pending=true;pthread_cond_signal(&usb_radio.queue_cond);
        }
        pthread_mutex_unlock(&usb_radio.queue_mutex);return ok;
    }
    usb_radio_profile p;if(!usb_radio_capture(&p,true))return false;
    atomic_store(&usb_radio.restoring,true);atomic_store(&usb_radio.rf_allowed,false);
    pthread_mutex_lock(&usb_radio.queue_mutex);usb_radio.restore_profile=p;usb_radio.restore_pending=true;
    pthread_cond_signal(&usb_radio.queue_cond);pthread_mutex_unlock(&usb_radio.queue_mutex);return false;
}
bool radio_usb_terminal_restore(void){
    atomic_store(&usb_radio.terminal,true);return radio_usb_restore();
}
bool radio_usb_release(void){
    if(!radio_usb_owned())return true;
    if(!radio_usb_restore() || !atomic_load(&usb_radio.worker_done))return false;
    bool ok=ctl_freedv_release(true);
    if(ok){atomic_store(&usb_radio.rf_allowed,false);atomic_store(&usb_radio.owned,false);
        pthread_mutex_lock(&usb_radio.input_mutex);close(usb_radio.keypad);usb_radio.keypad=-1;pthread_mutex_unlock(&usb_radio.input_mutex);}
    return ok;
}
