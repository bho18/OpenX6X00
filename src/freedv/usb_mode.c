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
void freedv_usb_abort(void){if(!freedv_usb_active())return;atomic_store(&abort_pending,true);radio_usb_clear(NULL,true);pthread_mutex_lock(&pointer_mutex);if(audio)fd_usb_audio_stop(audio);pthread_mutex_unlock(&pointer_mutex);}
bool freedv_usb_leave(void){if(!freedv_usb_active())return false;exit_requested=true;freedv_usb_abort();return true;}
static void clicked(lv_event_t *e){
    if(!front_keys_voice_local_event())return;
    unsigned action=(unsigned)(uintptr_t)lv_event_get_user_data(e);
    if(action==1){freedv_usb_abort();return;}
    if(action==2){freedv_usb_leave();return;}
    fd_usb_snapshot s=fd_usb_audio_snapshot(audio);fd_usb_inputs in;
    if(action==0 && audio && s.state==FD_USB_RX && radio_usb_observe(NULL,&in) && !in.pressed && in.rf_valid && !in.rf_tx){fd_usb_audio_arm(audio,!s.armed);lv_label_set_text(notice_label,!s.armed?"TX enabled: use panel PTT; maximum 20 seconds per turn":"Receive only; TX disabled");}
}
static void button(lv_obj_t *parent,const char *text,int x,unsigned action){lv_obj_t *b=lv_btn_create(parent);lv_obj_set_pos(b,x,212);lv_obj_set_size(b,180,40);lv_obj_t *l=lv_label_create(b);lv_label_set_text(l,text);lv_obj_center(l);dialog_item(&view,b);lv_obj_add_event_cb(b,clicked,LV_EVENT_CLICKED,(void *)(uintptr_t)action);}
static void construct(lv_obj_t *parent){
    view.obj=dialog_init(parent);lv_obj_set_pos(view.obj,20,100);lv_obj_set_size(view.obj,760,300);
    lv_obj_set_style_text_font(view.obj,&sony_14,0);
    lv_obj_t *l=lv_label_create(view.obj);lv_label_set_text(l,"USB FreeDV / RADE V1 - RF TEST");lv_obj_set_pos(l,12,8);
    status_label=lv_label_create(view.obj);lv_obj_set_pos(status_label,12,42);
    health_label=lv_label_create(view.obj);lv_obj_set_pos(health_label,12,76);
    l=lv_label_create(view.obj);lv_label_set_text_fmt(l,"%d Hz | USB-DIG | requested 1 W | panel PTT only",cparam_i_get(cfg_fg_freq));lv_obj_set_pos(l,12,110);
    l=lv_label_create(view.obj);lv_label_set_text(l,"Disconnect hand mic/paddles. Start with a dummy load and SDR.\nTuning is locked here. Exit before changing frequency or mode.");lv_obj_set_pos(l,12,140);
    notice_label=lv_label_create(view.obj);lv_obj_set_pos(notice_label,12,180);lv_label_set_text(notice_label,"Preparing receiver; TX starts disabled");
    button(view.obj,"Enable / disable TX",12,0);button(view.obj,"Stop",222,1);button(view.obj,"Exit",432,2);
}
static void destruct(void){status_label=health_label=notice_label=NULL;}
static void key(lv_event_t *e){(void)e;}
static void service(lv_timer_t *unused){
    (void)unused;fd_usb_snapshot s=fd_usb_audio_snapshot(audio);fd_usb_inputs in={0};radio_usb_observe(NULL,&in);
    static const char *states[]={"Preparing","Receiving","Switching direction","Transmitting","Finishing modem tail","Fault","Stopped"};
    lv_label_set_text_fmt(status_label,"%s | TX %s | turns %llu | %s",states[s.state],s.armed?"enabled":"disabled",(unsigned long long)s.turns,s.engine.synchronized?"RADE synchronized":"searching");
    lv_label_set_text_fmt(health_label,"Panel PTT %s | radio %s | audio gaps %u",in.valid?(in.pressed?"held":"released"):"unavailable",in.rf_valid?(in.rf_tx?"TX":"RX"):"status unavailable",s.gaps);
    if(s.fault)lv_label_set_text(notice_label,fd_usb_fault_text(s.fault));
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
        fd_usb_config c={.radio_source=BOARD_PA_SOURCE,.radio_sink=BOARD_PA_SINK,.trace_prefix=trace_prefix,.control={.observe=radio_usb_observe,.key=radio_usb_key,.clear=radio_usb_clear}};
        fd_usb_audio *created=fd_usb_audio_open(&c);
        pthread_mutex_lock(&pointer_mutex);audio=created;if(audio && atomic_load(&abort_pending))fd_usb_audio_stop(audio);pthread_mutex_unlock(&pointer_mutex);
    }
    if(!audio){radio_usb_clear(NULL,true);lv_label_set_text(notice_label,"Preparation failed; select Exit to restore");}
    timer=lv_timer_create(service,50,NULL);
}
