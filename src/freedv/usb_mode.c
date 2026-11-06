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
#include "../key_input.h"
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
static lv_obj_t *status_label,*health_label,*notice_label,*mic_gain_label;
static lv_obj_t *mic_level_label,*mic_rms_bar,*mic_peak_bar,*clip_label,*headset_label;
static lv_obj_t *check_button,*tx_button,*mic_down_button,*mic_up_button;
static uint64_t seen_mic_clips;
static uint32_t clip_tick;
static bool clip_seen;
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
    static const fd_qualified_profile display={.id="USB Digital Voice (Beta)",.local_mode=FD_PROFILE_USB_DIG,.upper_sideband=true,.sample_rate=48000,.rx_low_hz=100,.rx_high_hz=3100,.agc=radio_agc_fast,.rf_gain=100,.power_tenths_watt=10};return &display;
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
static void change_mic_gain(int delta){
    if(!audio)return;
    fd_usb_snapshot s=fd_usb_audio_snapshot(audio);
    int gain=s.mic_gain_db+delta;
    if(gain<FD_USB_MIC_GAIN_MIN_DB)gain=FD_USB_MIC_GAIN_MIN_DB;
    if(gain>FD_USB_MIC_GAIN_MAX_DB)gain=FD_USB_MIC_GAIN_MAX_DB;
    if(gain==s.mic_gain_db || !fd_usb_audio_set_mic_gain(audio,gain))return;
    /* This preference has no normal radio-volume or baseband callbacks. Only
     * its deferred settings write enters the voice owner's writer scope. */
    if(fd_writer_voice_begin()){
        param_i_set(cfg_freedv_usb_mic_gain,gain);fd_writer_voice_end();
    }
}
static void clicked(lv_event_t *e){
    if(!front_keys_voice_local_event())return;
    unsigned action=(unsigned)(uintptr_t)lv_event_get_user_data(e);
    if(action==1){freedv_usb_abort();return;}
    if(action==2){freedv_usb_leave();return;}
    if(action==4 || action==5){change_mic_gain(action==4?-1:1);return;}
    fd_usb_snapshot s=fd_usb_audio_snapshot(audio);fd_usb_inputs in;
    if(!audio || s.state!=FD_USB_RX || !radio_usb_observe(NULL,&in) || !in.valid || in.pressed || !in.rf_valid || in.rf_tx || in.protection)return;
    if(action==3){
        if(s.rf_disabled && !s.qualification_running && !s.qualification_complete){
            radio_usb_allow_tx(false);
            if(fd_usb_audio_start_qualification(audio))lv_label_set_text(notice_label,"Checking audio direction changes with TX disabled. Leave PTT released.");
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
static lv_obj_t *button(lv_obj_t *parent,const char *text,int x,int y,int w,int h,unsigned action){
    lv_obj_t *b=lv_btn_create(parent);lv_obj_set_pos(b,x,y);lv_obj_set_size(b,w,h);
    lv_obj_t *l=lv_label_create(b);lv_label_set_text(l,text);lv_obj_center(l);
    dialog_item(&view,b);lv_obj_add_event_cb(b,clicked,LV_EVENT_CLICKED,(void *)(uintptr_t)action);return b;
}
static lv_obj_t *label(lv_obj_t *parent,const char *text,int x,int y,int w){
    lv_obj_t *l=lv_label_create(parent);lv_label_set_text(l,text);lv_obj_set_pos(l,x,y);lv_obj_set_width(l,w);return l;
}
static lv_obj_t *meter(lv_obj_t *parent,int y,int h,lv_color_t color){
    lv_obj_t *b=lv_bar_create(parent);lv_obj_remove_style_all(b);
    lv_obj_set_pos(b,228,y);lv_obj_set_size(b,480,h);
    lv_obj_set_style_bg_color(b,TC(track),0);lv_obj_set_style_bg_opa(b,LV_OPA_COVER,0);
    lv_obj_set_style_radius(b,3,0);lv_obj_set_style_radius(b,3,LV_PART_INDICATOR);
    lv_obj_set_style_bg_color(b,color,LV_PART_INDICATOR);lv_obj_set_style_bg_opa(b,LV_OPA_COVER,LV_PART_INDICATOR);
    lv_bar_set_range(b,-600,0);lv_bar_set_value(b,-600,LV_ANIM_OFF);return b;
}
static void construct(lv_obj_t *parent){
    view.obj=dialog_init(parent);lv_obj_set_pos(view.obj,20,80);lv_obj_set_size(view.obj,760,330);
    lv_obj_set_style_text_font(view.obj,&sony_14,0);
    lv_obj_t *l=label(view.obj,"USB Digital Voice (Beta)",12,8,710);
    lv_obj_set_style_text_font(l,&sony_18,0);lv_obj_set_style_text_color(l,TC(text_head),0);
    l=label(view.obj,"",12,34,710);
    lv_label_set_text_fmt(l,"%d Hz | RADE V1 | USB-DIG | 1 W requested | panel PTT",cparam_i_get(cfg_fg_freq));
    lv_obj_set_style_text_color(l,TC(text_dim),0);
    status_label=label(view.obj,"Preparing audio | TX disabled",12,60,710);
    health_label=label(view.obj,"Panel PTT released | radio RX",12,82,710);
    label(view.obj,"Mic gain",12,112,180);
    mic_gain_label=label(view.obj,"",12,139,78);
    lv_label_set_text_fmt(mic_gain_label,"%+d dB",param_i_get(cfg_freedv_usb_mic_gain));
    mic_down_button=button(view.obj,"-",100,130,42,34,4);
    mic_up_button=button(view.obj,"+",152,130,42,34,5);
    mic_level_label=label(view.obj,"Mic RMS -- | Peak -- dBFS",228,112,480);
    mic_rms_bar=meter(view.obj,138,12,TC(ok_bar));
    mic_peak_bar=meter(view.obj,156,5,TC(warn));
    l=label(view.obj,"Mic preview after gain | VOL: headphone volume",228,168,480);
    lv_obj_set_style_text_color(l,TC(text_dim),0);
    headset_label=label(view.obj,"Headphones: checking USB volume",12,191,430);
    clip_label=label(view.obj,"Clipped samples: 0",480,191,230);
    lv_obj_set_style_text_color(clip_label,TC(text_dim),0);
    notice_label=label(view.obj,"Preparing receiver with TX disabled. Run check when ready.",12,216,710);
    lv_label_set_long_mode(notice_label,LV_LABEL_LONG_WRAP);
    check_button=button(view.obj,"Run check",12,262,165,40,3);
    tx_button=button(view.obj,"Enable TX",190,262,165,40,0);
    lv_obj_t *stop=button(view.obj,"Stop",368,262,165,40,1);
    button(view.obj,"Exit",546,262,165,40,2);
    seen_mic_clips=0;clip_tick=0;clip_seen=false;
    lv_group_focus_obj(stop);lv_group_set_editing(keyboard_group,false);
}
static void destruct(void){
    status_label=health_label=notice_label=mic_gain_label=NULL;
    mic_level_label=mic_rms_bar=mic_peak_bar=clip_label=headset_label=NULL;
    check_button=tx_button=mic_down_button=mic_up_button=NULL;
}
static void key(lv_event_t *e){
    uint32_t code=*(uint32_t *)lv_event_get_param(e);int delta=0;
    if(code==KEY_VOL_LEFT_EDIT || code==KEY_VOL_LEFT_SELECT)delta=-1;
    if(code==KEY_VOL_RIGHT_EDIT || code==KEY_VOL_RIGHT_SELECT)delta=1;
    if(!delta)return;
    /* Consume VOL while this dialog owns the panel, including rejected input.
     * Genuine current-generation local events alone may adjust the USB sink. */
    lv_event_stop_bubbling(e);
    if(front_keys_voice_local_event() && audio)fd_usb_audio_adjust_headset_volume(audio,delta);
}
static void dbfs_text(char *out,size_t size,int value){
    int magnitude=value<0?-value:value;
    snprintf(out,size,"%s%d.%d",value<0?"-":"",magnitude/10,magnitude%10);
}
static void audio_controls(const fd_usb_snapshot *s){
    lv_label_set_text_fmt(mic_gain_label,"%+d dB",s->mic_gain_db);
    if(s->mic_meter_ready){
        char rms[20],peak[20];dbfs_text(rms,sizeof(rms),s->mic_rms_dbfs10);dbfs_text(peak,sizeof(peak),s->mic_peak_dbfs10);
        lv_label_set_text_fmt(mic_level_label,"Mic RMS %s | Peak %s dBFS",rms,peak);
        lv_bar_set_value(mic_rms_bar,s->mic_rms_dbfs10,LV_ANIM_OFF);
        lv_bar_set_value(mic_peak_bar,s->mic_peak_dbfs10,LV_ANIM_OFF);
        lv_obj_set_style_bg_color(mic_peak_bar,s->mic_peak_dbfs10>=-10?TC(err):TC(warn),LV_PART_INDICATOR);
    }else{
        lv_label_set_text(mic_level_label,"Mic RMS -- | Peak -- dBFS");
        lv_bar_set_value(mic_rms_bar,-600,LV_ANIM_OFF);lv_bar_set_value(mic_peak_bar,-600,LV_ANIM_OFF);
    }
    if(s->mic_clipped_samples<seen_mic_clips)clip_seen=false;
    if(s->mic_clipped_samples>seen_mic_clips){clip_seen=true;clip_tick=lv_tick_get();}
    seen_mic_clips=s->mic_clipped_samples;
    bool clipping=clip_seen && lv_tick_elaps(clip_tick)<1500;
    if(clipping)lv_label_set_text(clip_label,"CLIP: lower mic gain");
    else lv_label_set_text_fmt(clip_label,"Clipped samples: %llu",(unsigned long long)s->mic_clipped_samples);
    lv_obj_set_style_text_color(clip_label,clipping?TC(err):TC(text_dim),0);
    if(s->headset_volume_ready)lv_label_set_text_fmt(headset_label,"Headphones: %u%%%s | turn VOL",s->headset_volume_percent,s->headset_muted?" (muted)":"");
    else lv_label_set_text(headset_label,"Headphones: USB volume unavailable");
    if(!audio || s->stopped || s->mic_gain_db<=FD_USB_MIC_GAIN_MIN_DB)lv_obj_add_state(mic_down_button,LV_STATE_DISABLED);else lv_obj_clear_state(mic_down_button,LV_STATE_DISABLED);
    if(!audio || s->stopped || s->mic_gain_db>=FD_USB_MIC_GAIN_MAX_DB)lv_obj_add_state(mic_up_button,LV_STATE_DISABLED);else lv_obj_clear_state(mic_up_button,LV_STATE_DISABLED);
}
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
    audio_controls(&s);
    bool ready=audio && s.state==FD_USB_RX && in.valid && !in.pressed && in.rf_valid && !in.rf_tx && !in.protection;
    if(ready && s.rf_disabled && !s.qualification_running && !s.qualification_complete)lv_obj_clear_state(check_button,LV_STATE_DISABLED);else lv_obj_add_state(check_button,LV_STATE_DISABLED);
    if(ready && s.qualification_complete)lv_obj_clear_state(tx_button,LV_STATE_DISABLED);else lv_obj_add_state(tx_button,LV_STATE_DISABLED);
    lv_label_set_text(lv_obj_get_child(tx_button,0),s.armed?"Disable TX":"Enable TX");
    if(s.rf_disabled || s.state==FD_USB_PREPARE)lv_label_set_text_fmt(status_label,"%s | TX disabled | check %u/%u",s.qualification_running?"Checking audio":state_text(s.state),s.qualification_turns,s.qualification_target?s.qualification_target:3);
    else {
        uint64_t rf_turns=s.turns>s.qualification_turns?s.turns-s.qualification_turns:0;
        lv_label_set_text_fmt(status_label,"%s | TX %s | transmissions %llu",state_text(s.state),s.armed?"enabled":"disabled",(unsigned long long)rf_turns);
    }
    lv_label_set_text_fmt(health_label,"Panel PTT %s | actual radio %s",in.valid?(in.pressed?"held":"released"):"unavailable",in.rf_valid?(in.rf_tx?"TX":"RX"):"UNKNOWN");
    if(s.fault){
        lv_label_set_text_fmt(notice_label,"Stopped: %s. %s",fd_usb_fault_text(s.fault),s.rf_clear_confirmed && in.rf_valid && !in.rf_tx?"Radio RX confirmed; select Exit.":"Radio RX is not confirmed. Select Stop; power off if TX persists.");
    }else if(s.state==FD_USB_PREPARE){
        lv_label_set_text(notice_label,"Preparing receiver with TX disabled. Run check when ready.");
    }else if(s.rf_disabled){
        if(s.qualification_complete)lv_label_set_text(notice_label,"Audio check passed. TX stays disabled until you select Enable TX.");
        else if(s.qualification_running&&s.qualification_idle_remaining_ms)lv_label_set_text_fmt(notice_label,"Checking audio stability: %u seconds. TX is disabled; leave PTT released.",(s.qualification_idle_remaining_ms+999)/1000);
        else if(s.qualification_running)lv_label_set_text(notice_label,"Checking audio direction changes. TX is disabled; leave PTT released.");
        else lv_label_set_text(notice_label,"Listening ready. Run check with PTT released before enabling TX.");
    }else if(s.stopped && !s.rf_clear_confirmed){
        lv_label_set_text(notice_label,"Audio stopped; radio RX is not confirmed. Select Stop; power off if TX persists.");
    }else lv_label_set_text(notice_label,s.armed?"Hold panel PTT, wait for Transmitting, then speak. Release to listen; maximum 20 seconds.":"TX disabled. Select Enable TX when ready to transmit.");
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
    atomic_store(&usb_active,true);freq_strip_set_app("USB Digital Voice");info_refresh_effective();
    main_quiet=claimed;bool quiet_ok=claimed && audio_freedv_main_quiet(true);
    if(claimed && quiet_ok){
        struct timespec t;clock_gettime(CLOCK_REALTIME,&t);snprintf(trace_prefix,sizeof(trace_prefix),"/tmp/freedv-usb-%lld-%09ld",(long long)t.tv_sec,t.tv_nsec);
        fd_usb_config c={.radio_source=BOARD_PA_SOURCE,.radio_sink=BOARD_PA_SINK,.trace_prefix=trace_prefix,.control={.observe=radio_usb_observe,.key=radio_usb_key,.clear=radio_usb_clear,.inhibit=radio_usb_inhibit},.rf_disabled=true,.qualification_turns=3,.qualification_control_delay_ms=450,.qualification_idle_ms=20000,.incident_dir=BOARD_DATA "/freedv-usb-incidents",.mic_gain_db=param_i_get(cfg_freedv_usb_mic_gain)};
        fd_usb_audio *created=fd_usb_audio_open(&c);
        pthread_mutex_lock(&pointer_mutex);audio=created;if(audio && atomic_load(&abort_pending))fd_usb_audio_stop(audio);pthread_mutex_unlock(&pointer_mutex);
    }
    if(!audio){radio_usb_clear(NULL,true);lv_label_set_text(notice_label,"Preparation failed; select Exit to restore");}
    timer=lv_timer_create(service,50,NULL);
}
