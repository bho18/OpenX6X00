// SPDX-License-Identifier: LGPL-2.1-or-later
#define _POSIX_C_SOURCE 200809L
#include "usb_voice.h"
#include "usb_radio.h"
#include "mode.h"
#include "writer_gate.h"
#include "../app_window.h"
#include "../audio.h"
#include "../board.h"
#include "../front_keys.h"
#include "../scheduler.h"
#include "../radio.h"
#include "../msg.h"
#include "../gui_styles.h"
#include "../cfg/cfg_api.h"
#include "../info_bar.h"
#include "../freq_strip.h"
#include <stdio.h>
#include <string.h>
#include <time.h>
#include <pthread.h>
static atomic_bool usb_active;
static atomic_bool abort_pending;
static pthread_mutex_t pointer_mutex=PTHREAD_MUTEX_INITIALIZER;
static fd_usb_audio *audio;
static bool exit_requested,main_quiet,input_owned;
static lv_timer_t *timer;
static lv_obj_t *status_label,*health_label,*notice_label;
static char trace_prefix[160];
static void construct(lv_obj_t *);
static void destruct(void);
static void key(lv_event_t *);
static dialog_t view={.construct_cb=construct,.destruct_cb=destruct,.key_cb=key};
bool freedv_usb_available(void){return true;}
bool freedv_usb_active(void){return atomic_load(&usb_active);}
const fd_qualified_profile *freedv_usb_profile_view(void){
    /* Effective display values only. No native capability or qualification is
     * claimed, and this object is never admitted by the native controller. */
    static const fd_qualified_profile display={.id="USB headset RF development",.local_mode=FD_PROFILE_USB_DIG,.upper_sideband=true,.sample_rate=48000,.rx_low_hz=100,.rx_high_hz=3100,.agc=radio_agc_fast,.rf_gain=100,.power_tenths_watt=10};return &display;
}
bool freedv_usb_ptt(bool pressed){if(!freedv_usb_active())return false;radio_usb_edge(pressed);return true;}
void freedv_usb_abort(void){
    if(!freedv_usb_active())return;
    atomic_store(&abort_pending,true);
    /* This fence never waits for the radio mutex or an I2C transaction. The
     * retained controller queues OFF on its independent control worker. */
    radio_usb_inhibit(NULL);
    pthread_mutex_lock(&pointer_mutex);if(audio)fd_usb_audio_stop(audio);pthread_mutex_unlock(&pointer_mutex);
}
bool freedv_usb_leave(void){if(!freedv_usb_active())return false;exit_requested=true;freedv_usb_abort();return true;}
static void clicked(lv_event_t *e){
    if(!front_keys_voice_local_event())return;
    unsigned action=(unsigned)(uintptr_t)lv_event_get_user_data(e);
    if(action==1){freedv_usb_abort();return;}
    if(action==2){freedv_usb_leave();return;}
    fd_usb_snapshot s=fd_usb_audio_snapshot(audio);fd_usb_inputs in;
    if(!audio || s.state!=FD_USB_RX || !radio_usb_observe(NULL,&in) || !in.valid || in.pressed || !in.rf_valid || in.rf_tx || in.protection)return;
    if(action==3){
        if(s.rf_disabled && !s.qualification_running && !s.qualification_complete){
            radio_usb_allow_tx(false);
            if(fd_usb_audio_start_qualification(audio))lv_label_set_text(notice_label,"RF remains OFF. Automatic transition checks are running; leave every PTT released.");
        }
        return;
    }
    if(action==0){
        if(s.armed){radio_usb_allow_tx(false);fd_usb_audio_arm(audio,false);return;}
        /* Separate local promotion gates: the core requires completed audio
         * evaluation, while the radio adapter requires actual fresh RX and a
         * released physical PTT. A partial transaction remains unarmed. */
        if(!s.qualification_complete || !fd_usb_audio_enable_rf(audio))return;
        if(!radio_usb_allow_tx(true)){fd_usb_audio_arm(audio,false);return;}
        fd_usb_audio_arm(audio,true);
    }
}
static void button(lv_obj_t *parent,const char *text,int x,unsigned action){lv_obj_t *b=lv_btn_create(parent);lv_obj_set_pos(b,x,262);lv_obj_set_size(b,165,40);lv_obj_t *l=lv_label_create(b);lv_label_set_text(l,text);lv_obj_center(l);dialog_item(&view,b);lv_obj_add_event_cb(b,clicked,LV_EVENT_CLICKED,(void *)(uintptr_t)action);}
static void construct(lv_obj_t *parent){
    view.obj=dialog_init(parent);lv_obj_set_pos(view.obj,20,80);lv_obj_set_size(view.obj,760,330);
    lv_obj_set_style_text_font(view.obj,&sony_14,0);
    lv_obj_t *l=lv_label_create(view.obj);lv_label_set_text(l,"USB FreeDV / RADE V1 - RF TEST");lv_obj_set_pos(l,12,8);
    status_label=lv_label_create(view.obj);lv_obj_set_pos(status_label,12,42);
    lv_obj_set_width(status_label,710);
    health_label=lv_label_create(view.obj);lv_obj_set_pos(health_label,12,76);
    lv_obj_set_width(health_label,710);
    l=lv_label_create(view.obj);lv_label_set_text_fmt(l,"%d Hz | USB-DIG | requested 1 W | panel PTT only",cparam_i_get(cfg_fg_freq));lv_obj_set_pos(l,12,110);
    l=lv_label_create(view.obj);lv_label_set_text(l,"Disconnect hand mic/paddles. Use a dummy load and SDR.\nRun the RF-disabled transition check before enabling panel PTT.");lv_obj_set_pos(l,12,214);
    notice_label=lv_label_create(view.obj);lv_obj_set_pos(notice_label,12,144);lv_obj_set_width(notice_label,710);lv_label_set_long_mode(notice_label,LV_LABEL_LONG_WRAP);lv_label_set_text(notice_label,"Preparing receiver. RF is disabled; wait for Run transition check.");
    button(view.obj,"Run transition check",12,3);button(view.obj,"Enable / disable TX",190,0);button(view.obj,"Stop",368,1);button(view.obj,"Exit",546,2);
}
static void destruct(void){status_label=health_label=notice_label=NULL;}
static void key(lv_event_t *e){(void)e;}
static const char *state_text(fd_usb_state s){
    switch(s){
        case FD_USB_PREPARE:return "Preparing audio";
        case FD_USB_RX:return "Receiving";
        case FD_USB_START:return "Preparing direction";
        case FD_USB_KEY_PENDING:return "Waiting for TX acknowledgement";
        case FD_USB_TX:return "Transmitting";
        case FD_USB_DRAIN:return "Finishing modem tail";
        case FD_USB_UNKEY:return "Clearing TX";
        case FD_USB_RX_READY:return "Waiting for fresh RX";
        case FD_USB_FAULT:return "Fault";
        case FD_USB_DONE:return "Stopped";
        default:return "Working";
    }
}
static void service(lv_timer_t *unused){
    (void)unused;fd_usb_snapshot s=fd_usb_audio_snapshot(audio);fd_usb_inputs in={0};radio_usb_observe(NULL,&in);
    if(s.rf_disabled || s.state==FD_USB_PREPARE)lv_label_set_text_fmt(status_label,"%s | RF disabled | transition check %u/%u",s.qualification_running?"Checking direction changes":state_text(s.state),s.qualification_turns,s.qualification_target?s.qualification_target:3);
    else {
        uint64_t rf_turns=s.turns>s.qualification_turns?s.turns-s.qualification_turns:0;
        lv_label_set_text_fmt(status_label,"%s | TX %s | RF turns %llu",state_text(s.state),s.armed?"enabled":"disabled",(unsigned long long)rf_turns);
    }
    lv_label_set_text_fmt(health_label,"Panel PTT %s | actual radio %s",in.valid?(in.pressed?"held":"released"):"unavailable",in.rf_valid?(in.rf_tx?"TX":"RX"):"UNKNOWN");
    if(s.fault){
        lv_label_set_text_fmt(notice_label,"%s: %s / %s (%llu ms). %s",fd_usb_fault_text(s.fault),s.first_fault_origin[0]?s.first_fault_origin:"origin unavailable",s.first_fault_stream[0]?s.first_fault_stream:"controller",(unsigned long long)s.first_fault_operation_ms,s.rf_clear_confirmed && in.rf_valid && !in.rf_tx?"Fresh RX observed; select Exit.":"RF clear not confirmed. Select Stop; power off if TX persists.");
    }else if(s.state==FD_USB_PREPARE){
        lv_label_set_text(notice_label,"Preparing transition checks. RF remains disabled; wait for Run transition check.");
    }else if(s.rf_disabled){
        if(s.qualification_complete)lv_label_set_text(notice_label,"Transition check passed. RF remains disabled. Select Enable TX locally for the SDR test.");
        else if(s.qualification_running)lv_label_set_text(notice_label,"RF remains OFF. Automatic direction/control-delay checks are running; leave PTT released. No speaking is required.");
        else lv_label_set_text(notice_label,"RF is disabled. Select Run transition check and leave every PTT released. No speaking is required.");
    }else if(s.stopped && !s.rf_clear_confirmed){
        lv_label_set_text(notice_label,"Audio stopped; RF clear is not confirmed. Keep the dummy load connected and select Stop.");
    }else lv_label_set_text(notice_label,s.armed?"TX enabled: hold panel PTT, wait for Transmitting, then speak. Release to finish; maximum 20 seconds.":"TX disabled. Select Enable TX locally when ready for the SDR recording.");
    if(exit_requested && s.stopped){
        pthread_mutex_lock(&pointer_mutex);fd_usb_audio *old=audio;audio=NULL;pthread_mutex_unlock(&pointer_mutex);
        if(old)fd_usb_audio_close(old);
        if(!radio_usb_restore()){lv_label_set_text(notice_label,"Release PTT and wait for fresh RX before Exit");return;}
        if(main_quiet){if(!audio_freedv_main_quiet(false)){lv_label_set_text(notice_label,"Audio restore failed; restart radio");return;}main_quiet=false;}
        if(!radio_usb_release())return;
        atomic_store(&usb_active,false);if(input_owned){front_keys_diagnostic_input_release();input_owned=false;}
        lv_timer_del(timer);timer=NULL;dialog_destruct();freq_strip_set_app(NULL);info_refresh_effective();
    }
}
void freedv_usb_open(void){
    if(!scheduler_on_gui_thread() || freedv_usb_active())return;
    if(!front_keys_diagnostic_input_acquire()){msg_schedule_text_fmt("Release panel controls, then select FreeDV again");return;}input_owned=true;
    bool claimed=radio_usb_claim();
    if(!claimed && !radio_usb_owned()){front_keys_diagnostic_input_release();input_owned=false;msg_schedule_text_fmt("USB mode requires fresh RX, an allowed TX frequency, and no split/RIT/XIT");return;}
    exit_requested=false;audio=NULL;atomic_store(&abort_pending,false);
    if(fd_writer_voice_begin()){dialog_construct(&view,lv_scr_act());fd_writer_voice_end();}
    else {radio_usb_clear(NULL,true);msg_schedule_text_fmt("USB control reservation failed; restart radio");return;}
    atomic_store(&usb_active,true);freq_strip_set_app("USB RADE test");info_refresh_effective();
    main_quiet=claimed;bool quiet_ok=claimed && audio_freedv_main_quiet(true);
    if(claimed && quiet_ok){
        struct timespec t;clock_gettime(CLOCK_REALTIME,&t);snprintf(trace_prefix,sizeof(trace_prefix),"/tmp/freedv-usb-%lld-%09ld",(long long)t.tv_sec,t.tv_nsec);
        fd_usb_config c={.radio_source=BOARD_PA_SOURCE,.radio_sink=BOARD_PA_SINK,.trace_prefix=trace_prefix,.control={.observe=radio_usb_observe,.key=radio_usb_key,.clear=radio_usb_clear,.inhibit=radio_usb_inhibit},.rf_disabled=true,.qualification_turns=3,.qualification_control_delay_ms=450};
        fd_usb_audio *created=fd_usb_audio_open(&c);
        pthread_mutex_lock(&pointer_mutex);audio=created;if(audio && atomic_load(&abort_pending))fd_usb_audio_stop(audio);pthread_mutex_unlock(&pointer_mutex);
    }
    if(!audio){radio_usb_clear(NULL,true);lv_label_set_text(notice_label,"Preparation failed; select Exit to restore");}
    timer=lv_timer_create(service,50,NULL);
}
