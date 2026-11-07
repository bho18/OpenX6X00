// SPDX-License-Identifier: LGPL-2.1-or-later
#define _POSIX_C_SOURCE 200809L
#include "usb_voice.h"
#include "usb_radio.h"
#include "usb_ui.h"
#include "usb_navigation.h"
#include "usb_headsets.h"
#include "mode.h"
#include "writer_gate.h"
#include "../audio.h"
#include "../board.h"
#include "../front_keys.h"
#include "../key_input.h"
#include "../gui_events.h"
#include "../scheduler.h"
#include "../radio.h"
#include "../msg.h"
#include "../gui_styles.h"
#include "../button_bar.h"
#include "../cfg/cfg_api.h"
#include "../cfg/freedv_preferences.h"
#include "../cfg/settings_manager_api.h"
#include "../info_bar.h"
#include "../freq_strip.h"
#include <stdio.h>
#include <string.h>
#include <time.h>
#include <pthread.h>

/* FreeDV owns radio/audio transactions, not the main screen. Temporary
 * controls use their own LVGL focus group; the spectrum and tuning dial stay
 * in the ordinary screen throughout the session. */
typedef enum { UI_NOTICE,UI_DISCOVER,UI_CLAIM,UI_QUIET,UI_RUNNING,UI_FAILED,
    UI_STOP,UI_RESTORE,UI_UNQUIET,UI_RELEASE } ui_stage;
typedef enum { POP_NONE,POP_NOTICE,POP_MODE,POP_MIC,POP_AUDIO,POP_HELP } popup_kind;
static atomic_bool usb_active,abort_pending;
static pthread_mutex_t pointer_mutex=PTHREAD_MUTEX_INITIALIZER;
static fd_usb_audio *audio;
static ui_stage stage;
static bool exit_requested,restart_requested,terminal_exit,main_quiet,input_owned;
static lv_timer_t *timer;
static buttons_page_t *previous_page;
static lv_obj_t *status_box,*status_label,*compact_meter,*compact_clip;
static lv_obj_t *popup,*popup_message,*usage_check,*mic_gain_spin,*mic_level_label;
static lv_obj_t *mic_rms_bar,*mic_peak_bar,*clip_label,*headset_label,*audio_list,*timeout_spin;
static popup_kind popup_type;
static lv_group_t *popup_group;
static lv_obj_t *previous_focus;
static bool previous_editing;
static struct {lv_indev_t *input;lv_group_t *group;} redirected[16];
static unsigned redirected_count;
static fd_usb_headset_scan *scan;
static fd_usb_headset_catalog catalog;
/* Only implemented codecs enter this registry. Extending the selector later
 * does not advertise a mode merely because a control library names it. */
static const struct {const char *name;} supported_modes[]={{"RADE V1"}};
static char selected_id[256],selected_name[160],trace_prefix[160],problem[192];
static int selected_gain;
static uint64_t seen_mic_clips;
static uint32_t clip_tick;
static bool clip_seen,updating_controls;
static struct {pthread_t thread;atomic_bool done;bool running,target,ok;} quiet_job;
static void service(lv_timer_t *);
static void popup_open(popup_kind);
static void popup_close(void);
static void start_discovery(void);
static void request_restart(void);

bool freedv_usb_available(void){return true;}
bool freedv_usb_active(void){return atomic_load(&usb_active);}
bool freedv_usb_entry_pending(void){return freedv_usb_active() && stage==UI_NOTICE;}
bool freedv_usb_popup_active(void){return popup!=NULL;}
bool freedv_usb_finishing(void){return freedv_usb_active() && exit_requested;}
const fd_qualified_profile *freedv_usb_profile_view(void){
    if(!freedv_usb_active() || stage==UI_NOTICE)return NULL;
    /* Effective display only; this is not a native MCU capability claim. */
    static fd_qualified_profile display={.id="FreeDV",.local_mode=FD_PROFILE_USB_DIG,
        .upper_sideband=true,.sample_rate=48000,.rx_low_hz=100,.rx_high_hz=3100,
        .agc=radio_agc_fast,.rf_gain=100};
    display.power_tenths_watt=radio_usb_power_tenths();return &display;
}
bool freedv_usb_ptt(bool pressed){
    if(!freedv_usb_active())return false;
    /* Entry consumes PTT too, before any profile/audio starts. The adapter
     * and operational core require release and a subsequent new press. */
    if(radio_usb_owned())radio_usb_edge(pressed);
    return true;
}
bool freedv_usb_volume(int delta){
    if(!freedv_usb_active())return false;
    if(front_keys_voice_local_event() && audio && !exit_requested)
        fd_usb_audio_adjust_headset_volume(audio,delta);
    return true;
}
bool freedv_usb_volume_percent(unsigned *percent,bool *muted){
    if(!freedv_usb_active() || !audio)return false;
    fd_usb_snapshot s=fd_usb_audio_snapshot(audio);if(!s.headset_volume_ready)return false;
    if(percent)*percent=s.headset_volume_percent;
    if(muted)*muted=s.headset_muted;
    return true;
}
void freedv_usb_abort(void){
    if(!freedv_usb_active())return;
    atomic_store(&abort_pending,true);
    radio_usb_inhibit(NULL);
    pthread_mutex_lock(&pointer_mutex);if(audio)fd_usb_audio_stop(audio);pthread_mutex_unlock(&pointer_mutex);
}
bool freedv_usb_leave(void){
    if(!freedv_usb_active())return false;
    exit_requested=true;restart_requested=false;popup_close();
    if(scan)fd_usb_headsets_scan_cancel(scan);
    pthread_mutex_lock(&pointer_mutex);if(audio)fd_usb_audio_leave(audio);pthread_mutex_unlock(&pointer_mutex);
    return true;
}
void freedv_usb_terminal_leave(void){
    if(!freedv_usb_active())return;
    terminal_exit=true;freedv_usb_leave();freedv_usb_abort();
}
static const char *mode_button_text(void){static char text[40];snprintf(text,sizeof(text),"Mode:\n%s",supported_modes[0].name);return text;}
static const char *gain_button_text(void){static char text[32];snprintf(text,sizeof(text),"Mic gain:\n%+d dB",selected_gain);return text;}
static const char *exit_button_text(void){return exit_requested?"Stop now":popup?"Close":"Exit";}
static void soft_button(button_data_t *button);
static button_data_t mode_button={.type=BTN_TEXT_FN,.label_fn=mode_button_text,.press=soft_button,.ctrl=0};
static button_data_t gain_button={.type=BTN_TEXT_FN,.label_fn=gain_button_text,.press=soft_button,.ctrl=1};
static button_data_t audio_button={.type=BTN_TEXT,.label="Audio",.press=soft_button,.ctrl=2};
static button_data_t exit_button={.type=BTN_TEXT_FN,.label_fn=exit_button_text,.press=soft_button,.ctrl=4};
static buttons_page_t normal_page={.items={&mode_button,&gain_button,&audio_button,&btn_tx_pwr,&exit_button}};
static button_data_t notice_empty={.type=BTN_EMPTY};
static button_data_t notice_continue={.type=BTN_TEXT,.label="Continue",.press=soft_button,.ctrl=4};
static button_data_t notice_cancel={.type=BTN_TEXT,.label="Cancel",.press=soft_button,.ctrl=3};
static buttons_page_t notice_page={.items={&notice_empty,&notice_empty,&notice_empty,&notice_cancel,&notice_continue}};

static lv_obj_t *label(lv_obj_t *parent,const char *text,int x,int y,int w){
    lv_obj_t *l=lv_label_create(parent);lv_label_set_text(l,text);lv_obj_set_pos(l,x,y);
    lv_obj_set_width(l,w);lv_label_set_long_mode(l,LV_LABEL_LONG_WRAP);return l;
}
static void set_problem(const char *text){snprintf(problem,sizeof(problem),"%s",text);stage=UI_FAILED;}
static void popup_key_event(lv_event_t *e){
    if(freedv_usb_popup_key(*(uint32_t *)lv_event_get_param(e)))lv_event_stop_bubbling(e);
}
static void popup_item(lv_obj_t *item){
    lv_obj_add_style(item,&dialog_item_style,LV_STATE_DEFAULT);
    lv_obj_add_style(item,&dialog_item_focus_style,LV_STATE_FOCUSED);
    lv_obj_add_style(item,&dialog_item_edited_style,LV_STATE_EDITED);
    lv_group_add_obj(popup_group,item);
    lv_obj_add_event_cb(item,popup_key_event,LV_EVENT_KEY,NULL);
}
static void dropdown_ready(lv_event_t *event){
    lv_obj_t *list=lv_dropdown_get_list(lv_event_get_target(event));
    styles_dropdown_list(list);
    lv_obj_set_style_max_height(list,180,0);
}
static void popup_dropdown(lv_obj_t *item){
    popup_item(item);lv_dropdown_set_dir(item,LV_DIR_TOP);
    lv_obj_add_event_cb(item,dropdown_ready,LV_EVENT_READY,NULL);
}
static void clicked(lv_event_t *e);
static lv_obj_t *popup_button(const char *text,int x,int y,int w,unsigned action){
    lv_obj_t *b=lv_btn_create(popup);lv_obj_set_pos(b,x,y);lv_obj_set_size(b,w,36);
    lv_obj_t *l=lv_label_create(b);lv_label_set_text(l,text);lv_obj_center(l);
    popup_item(b);lv_obj_add_event_cb(b,clicked,LV_EVENT_CLICKED,(void *)(uintptr_t)action);return b;
}
static lv_obj_t *meter(lv_obj_t *parent,int x,int y,int w,int h,lv_color_t color){
    lv_obj_t *b=lv_bar_create(parent);lv_obj_remove_style_all(b);
    lv_obj_set_pos(b,x,y);lv_obj_set_size(b,w,h);lv_obj_set_style_bg_color(b,TC(track),0);
    lv_obj_set_style_bg_opa(b,LV_OPA_COVER,0);lv_obj_set_style_radius(b,3,0);
    lv_obj_set_style_radius(b,3,LV_PART_INDICATOR);lv_obj_set_style_bg_color(b,color,LV_PART_INDICATOR);
    lv_obj_set_style_bg_opa(b,LV_OPA_COVER,LV_PART_INDICATOR);
    lv_bar_set_range(b,-600,0);lv_bar_set_value(b,-600,LV_ANIM_OFF);return b;
}
static void popup_close(void){
    if(!popup)return;
    /* Restore indev bindings before removing the widgets they may focus. Drop
     * their half-presses so a popup close cannot activate an underlying item. */
    for(unsigned i=0;i<redirected_count;++i){lv_indev_reset(redirected[i].input,NULL);lv_indev_set_group(redirected[i].input,redirected[i].group);}
    redirected_count=0;
    lv_obj_del(popup);popup=NULL;
    if(popup_group){lv_group_del(popup_group);popup_group=NULL;}
    if(previous_focus && lv_obj_is_valid(previous_focus))lv_group_focus_obj(previous_focus);
    lv_group_set_editing(keyboard_group,previous_editing);
    popup_type=POP_NONE;popup_message=usage_check=mic_gain_spin=mic_level_label=NULL;
    mic_rms_bar=mic_peak_bar=clip_label=headset_label=audio_list=timeout_spin=NULL;
}
static void popup_begin(popup_kind kind,int height,const char *title){
    popup_close();popup_type=kind;previous_focus=lv_group_get_focused(keyboard_group);
    previous_editing=lv_group_get_editing(keyboard_group);popup_group=lv_group_create();
    for(lv_indev_t *in=lv_indev_get_next(NULL);in;in=lv_indev_get_next(in)){
        if(in->group==keyboard_group && redirected_count<16){
            redirected[redirected_count].input=in;redirected[redirected_count++].group=keyboard_group;
            lv_indev_reset(in,NULL);lv_indev_set_group(in,popup_group);
        }
    }
    popup=lv_obj_create(lv_scr_act());lv_obj_remove_style_all(popup);lv_obj_add_style(popup,&popup_style,0);
    lv_obj_set_pos(popup,90,480-BUTTONS_H-height-4);lv_obj_set_size(popup,620,height);
    lv_obj_set_style_pad_all(popup,12,0);lv_obj_set_style_text_font(popup,&sony_18,0);
    lv_obj_clear_flag(popup,LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_t *heading=label(popup,title,0,0,590);lv_obj_set_style_text_font(heading,&sony_22,0);
    lv_obj_set_style_text_color(heading,TC(text_head),0);
}
static void change_gain(int gain){
    if(gain<FD_USB_MIC_GAIN_MIN_DB)gain=FD_USB_MIC_GAIN_MIN_DB;
    if(gain>FD_USB_MIC_GAIN_MAX_DB)gain=FD_USB_MIC_GAIN_MAX_DB;
    if(!audio || stage!=UI_RUNNING || restart_requested || exit_requested || gain==selected_gain || !fd_usb_audio_set_mic_gain(audio,gain))return;
    selected_gain=gain;
    if(!cfg_freedv_usb_save_gain(selected_id,gain))msg_schedule_text_fmt("Mic gain changed; could not save preference");
    buttons_refresh(&gain_button);
}
static void spin_changed(lv_event_t *e){
    if(updating_controls || !front_keys_voice_local_event())return;
    lv_obj_t *item=lv_event_get_target(e);int value=lv_spinbox_get_value(item);
    if(item==mic_gain_spin)change_gain(value);
    else if(item==timeout_spin){
        if(!audio || fd_usb_audio_set_tx_timeout(audio,(unsigned)value)){
            if(fd_writer_voice_begin()){param_i_set(cfg_freedv_timeout,value);fd_writer_voice_end();}
        }else{
            updating_controls=true;lv_spinbox_set_value(timeout_spin,param_i_get(cfg_freedv_timeout));updating_controls=false;
        }
    }
}
static lv_obj_t *spin(int value,int minimum,int maximum,int step,int x,int y,int width){
    lv_obj_t *s=lv_spinbox_create(popup);lv_obj_set_pos(s,x,y);lv_obj_set_size(s,width,38);
    lv_spinbox_set_range(s,minimum,maximum);lv_spinbox_set_digit_format(s,maximum>100?3:2,0);
    lv_spinbox_set_step(s,step);lv_spinbox_set_value(s,value);lv_spinbox_set_rollover(s,false);
    popup_item(s);lv_obj_add_event_cb(s,spin_changed,LV_EVENT_VALUE_CHANGED,NULL);
    lv_obj_add_event_cb(s,spin_changed,LV_EVENT_KEY,NULL);return s;
}
static int selected_catalog_index(void){for(unsigned i=0;i<catalog.count;++i)if(!strcmp(catalog.devices[i].id,selected_id))return (int)i;return -1;}
static void populate_audio(void){
    if(!audio_list || !popup_message)return;
    char options[FD_USB_HEADSET_MAX*170];size_t used=0;options[0]=0;
    for(unsigned i=0;i<catalog.count;++i){
        char name[160];snprintf(name,sizeof(name),"%s",catalog.devices[i].name);
        for(char *p=name;*p;++p)if(*p=='\n' || *p=='\r')*p=' ';
        int n=snprintf(options+used,sizeof(options)-used,"%s%s",i?"\n":"",name);
        if(n<0 || (size_t)n>=sizeof(options)-used)break;
        used+=(size_t)n;
    }
    lv_dropdown_set_options(audio_list,used?options:"No paired USB headset");
    int selected=selected_catalog_index();lv_dropdown_set_selected(audio_list,selected>=0?(unsigned)selected:0);
    if(catalog.pending)lv_label_set_text(popup_message,"Finding USB devices with both microphone and playback...");
    else if(catalog.error)lv_label_set_text(popup_message,"USB discovery failed. Check the HOST connection, then Retry.");
    else if(!catalog.count)lv_label_set_text(popup_message,"No paired USB audio device. Connect one to HOST USB, then Retry.");
    else if(*selected_id && selected<0)lv_label_set_text(popup_message,"Saved headset is absent. Select a connected headset and Use device.");
    else lv_label_set_text(popup_message,"Choose one physical USB headset. The radio modem connection stays fixed.");
    if(catalog.pending || !catalog.count)lv_obj_add_state(audio_list,LV_STATE_DISABLED);else lv_obj_clear_state(audio_list,LV_STATE_DISABLED);
}
static const char usage_text[]=
    "FreeDV requires a USB audio device with microphone input and headphones or speaker output, connected to the HOST USB port. "
    "Built-in/hand microphones and the radio speaker are not supported.\n\n"
    "Use only PTT on the radio: hold to talk, release to listen. Hand-mic PTT does not work. "
    "Tune normally in RX. Select Volume for VOL headset level; TX power uses the usual radio controls.";
static void popup_open(popup_kind kind){
    if(!freedv_usb_active() || exit_requested)return;
    if(kind==POP_NOTICE || kind==POP_HELP){
        popup_begin(kind,kind==POP_NOTICE?298:270,"Using FreeDV");
        popup_message=label(popup,usage_text,0,34,590);
        if(kind==POP_NOTICE){
            usage_check=lv_checkbox_create(popup);lv_checkbox_set_text(usage_check,"Don't show this again");
            lv_obj_set_pos(usage_check,0,220);popup_item(usage_check); /* intentionally unchecked */
            lv_obj_t *continue_button=popup_button("Continue",342,246,120,1);
            popup_button("Cancel",472,246,120,2);lv_group_focus_obj(continue_button);
        }else {lv_obj_t *close=popup_button("Close",472,218,120,3);lv_group_focus_obj(close);}
    }else if(kind==POP_MODE){
        popup_begin(kind,210,"FreeDV mode");
        label(popup,"Mode",0,42,150);lv_obj_t *mode=lv_dropdown_create(popup);
        lv_obj_set_pos(mode,174,34);lv_obj_set_size(mode,230,38);lv_dropdown_set_options(mode,supported_modes[0].name);popup_dropdown(mode);
        label(popup,"Maximum TX (seconds)",0,94,270);
        timeout_spin=spin(param_i_get(cfg_freedv_timeout),30,600,30,300,84,104);
        popup_button("Help",0,146,130,4);popup_button("Close",472,146,120,3);lv_group_focus_obj(mode);
    }else if(kind==POP_MIC){
        popup_begin(kind,238,"Microphone gain");
        label(popup,"Speech gain (dB)",0,42,210);
        mic_gain_spin=spin(selected_gain,FD_USB_MIC_GAIN_MIN_DB,FD_USB_MIC_GAIN_MAX_DB,1,238,34,120);
        popup_button("-",378,34,64,5);popup_button("+",454,34,64,6);
        mic_level_label=label(popup,"Mic RMS -- | Peak -- dBFS",0,89,588);
        mic_rms_bar=meter(popup,0,118,580,12,TC(ok_bar));mic_peak_bar=meter(popup,0,136,580,5,TC(warn));
        clip_label=label(popup,"Clipped TX samples: 0",0,153,420);
        headset_label=label(popup,"VOL controls USB listening volume",0,185,450);
        popup_button("Close",472,178,120,3);lv_group_focus_obj(mic_gain_spin);
    }else if(kind==POP_AUDIO){
        popup_begin(kind,232,"USB audio device");popup_message=label(popup,"",0,36,590);
        audio_list=lv_dropdown_create(popup);lv_obj_set_pos(audio_list,0,102);lv_obj_set_size(audio_list,590,38);
        popup_dropdown(audio_list);populate_audio();
        popup_button("Retry",0,166,130,7);popup_button("Use device",280,166,150,8);popup_button("Close",472,166,120,3);
        lv_group_focus_obj(audio_list);
    }
    lv_group_set_editing(popup_group,false);
}
static void acknowledge(void){
    if(stage!=UI_NOTICE || popup_type!=POP_NOTICE)return;
    bool hide=usage_check && lv_obj_has_state(usage_check,LV_STATE_CHECKED);
    if(!cfg_freedv_usage_set_hidden(hide)){lv_label_set_text(popup_message,"Could not save acknowledgement. Try again or Cancel.");return;}
    popup_close();buttons_load_page(&normal_page);stage=UI_DISCOVER;info_refresh_effective();start_discovery();
}
static void clicked(lv_event_t *e){
    if(!front_keys_voice_local_event())return;
    unsigned action=(unsigned)(uintptr_t)lv_event_get_user_data(e);
    if(action==1){acknowledge();return;}
    if(action==2){freedv_navigation_request(FD_NAV_HOME,0,0,NULL);return;}
    if(action==3){popup_close();return;}
    if(action==4){popup_open(POP_HELP);return;}
    if(action==5 || action==6){change_gain(selected_gain+(action==5?-1:1));return;}
    if(action==7){request_restart();return;}
    if(action==8 && !catalog.pending && catalog.count){
        unsigned index=lv_dropdown_get_selected(audio_list);if(index>=catalog.count)return;
        fd_usb_headset *device=&catalog.devices[index];
        if(!cfg_freedv_usb_select_headset(device->id)){lv_label_set_text(popup_message,"Could not save selected headset.");return;}
        bool changed=strcmp(selected_id,device->id)!=0;
        snprintf(selected_id,sizeof(selected_id),"%s",device->id);snprintf(selected_name,sizeof(selected_name),"%s",device->name);
        selected_gain=cfg_freedv_usb_gain_for(selected_id,selected_name);buttons_refresh(&gain_button);
        popup_close();if(changed || stage==UI_FAILED)request_restart();
    }
}
bool freedv_usb_softkey(unsigned index,bool hold){
    if(!freedv_usb_active())return false;
    if(hold)return !(index==3 && stage!=UI_NOTICE && !popup && !exit_requested);
    if(!front_keys_voice_local_event())return true;
    if(stage==UI_NOTICE){if(index==4)acknowledge();else if(index==3)freedv_navigation_request(FD_NAV_HOME,0,0,NULL);return true;}
    if(index==4){if(exit_requested)freedv_usb_abort();else if(popup)popup_close();else freedv_navigation_request(FD_NAV_HOME,0,0,NULL);return true;}
    if(exit_requested)return true;
    if(index==0)popup_open(POP_MODE);else if(index==1)popup_open(POP_MIC);else if(index==2)popup_open(POP_AUDIO);
    else if(index==3)return false; /* the ordinary TX-power button */
    return true;
}
static void soft_button(button_data_t *button){freedv_usb_softkey((unsigned)button->ctrl,false);}
bool freedv_usb_popup_key(uint32_t code){
    if(!popup)return false;
    if(code==KEY_VOL_LEFT_EDIT || code==KEY_VOL_LEFT_SELECT){freedv_usb_volume(-1);return true;}
    if(code==KEY_VOL_RIGHT_EDIT || code==KEY_VOL_RIGHT_SELECT){freedv_usb_volume(1);return true;}
    if(!front_keys_voice_local_event())return true;
    if(code==LV_KEY_ESC || code==KEY_VOL_PRESS){
        if(audio_list && lv_dropdown_is_open(audio_list)){lv_dropdown_close(audio_list);return true;}
        if(stage==UI_NOTICE)freedv_navigation_request(FD_NAV_HOME,0,0,NULL);else popup_close();return true;
    }
    if(code>=KEYBOARD_F1 && code<=KEYBOARD_F5)return freedv_usb_softkey(code-KEYBOARD_F1,false);
    return false;
}
static void dbfs_text(char *out,size_t size,int value){int magnitude=value<0?-value:value;snprintf(out,size,"%s%d.%d",value<0?"-":"",magnitude/10,magnitude%10);}
static void audio_controls(const fd_usb_snapshot *s){
    if(s->mic_clipped_samples<seen_mic_clips)clip_seen=false;
    if(s->mic_clipped_samples>seen_mic_clips){clip_seen=true;clip_tick=lv_tick_get();}
    seen_mic_clips=s->mic_clipped_samples;bool clipping=clip_seen && lv_tick_elaps(clip_tick)<1500;
    if(compact_meter)lv_bar_set_value(compact_meter,s->mic_meter_ready?s->mic_rms_dbfs10:-600,LV_ANIM_OFF);
    if(compact_clip)lv_label_set_text(compact_clip,clipping?"CLIP":"");
    if(!mic_gain_spin)return;
    updating_controls=true;lv_spinbox_set_value(mic_gain_spin,s->mic_gain_db);updating_controls=false;
    if(s->mic_meter_ready){char rms[20],peak[20];dbfs_text(rms,sizeof(rms),s->mic_rms_dbfs10);dbfs_text(peak,sizeof(peak),s->mic_peak_dbfs10);
        lv_label_set_text_fmt(mic_level_label,"Mic RMS %s | Peak %s dBFS",rms,peak);
        lv_bar_set_value(mic_rms_bar,s->mic_rms_dbfs10,LV_ANIM_OFF);lv_bar_set_value(mic_peak_bar,s->mic_peak_dbfs10,LV_ANIM_OFF);
        lv_obj_set_style_bg_color(mic_peak_bar,s->mic_peak_dbfs10>=-10?TC(err):TC(warn),LV_PART_INDICATOR);
    }else {lv_label_set_text(mic_level_label,"Mic RMS -- | Peak -- dBFS");lv_bar_set_value(mic_rms_bar,-600,LV_ANIM_OFF);lv_bar_set_value(mic_peak_bar,-600,LV_ANIM_OFF);}
    if(clipping)lv_label_set_text(clip_label,"CLIP: lower mic gain");else lv_label_set_text_fmt(clip_label,"Clipped TX samples: %llu",(unsigned long long)s->mic_clipped_samples);
    lv_obj_set_style_text_color(clip_label,clipping?TC(err):TC(text_dim),0);
    if(s->headset_volume_ready)lv_label_set_text_fmt(headset_label,"Headphones: %u%%%s | turn VOL",s->headset_volume_percent,s->headset_muted?" (muted)":"");
    else lv_label_set_text(headset_label,"Headphones: USB volume unavailable");
}
static const char *state_text(fd_usb_state value){
    switch(value){case FD_USB_PREPARE:return "Preparing audio";case FD_USB_RX:return "Receiving";
        case FD_USB_START:return "Preparing TX";case FD_USB_KEY_PENDING:return "Waiting for TX";
        case FD_USB_TX:return "Transmitting";case FD_USB_DRAIN:return "Finishing speech";
        case FD_USB_UNKEY:return "Clearing TX";case FD_USB_RX_READY:return "Waiting for RX";
        case FD_USB_TUNING:return "Tuning";case FD_USB_FAULT:return "Stopped";case FD_USB_DONE:return "Stopped";default:return "Working";}
}
static void *quiet_worker(void *unused){(void)unused;quiet_job.ok=audio_freedv_main_quiet(quiet_job.target);atomic_store(&quiet_job.done,true);return NULL;}
static bool quiet_start(bool target){
    if(quiet_job.running)return false;
    quiet_job.target=target;quiet_job.ok=false;atomic_store(&quiet_job.done,false);
    if(pthread_create(&quiet_job.thread,NULL,quiet_worker,NULL))return false;
    quiet_job.running=true;return true;
}
static bool quiet_finished(bool *ok){
    if(!quiet_job.running || !atomic_load(&quiet_job.done))return false;
    pthread_join(quiet_job.thread,NULL);quiet_job.running=false;*ok=quiet_job.ok;return true;
}
static void start_discovery(void){
    if(scan)return;
    memset(&catalog,0,sizeof(catalog));catalog.pending=true;scan=fd_usb_headsets_scan_open();
    if(!scan){catalog.pending=false;catalog.error=1;}populate_audio();
}
static void poll_discovery(void){
    if(!scan)return;
    fd_usb_headset_catalog value=fd_usb_headsets_scan_snapshot(scan);
    if(value.pending)return;
    catalog=value;if(fd_usb_headsets_scan_close(scan))scan=NULL;populate_audio();
}
static bool choose_headset(void){
    if(catalog.pending)return false;
    if(catalog.error){set_problem("USB discovery failed; open Audio and Retry");return false;}
    int index=selected_catalog_index();
    if(index<0 && !*selected_id && catalog.count==1)index=0;
    if(index<0){set_problem(catalog.count?"Select a USB headset in Audio":"Connect a USB headset to HOST; Audio > Retry");return false;}
    fd_usb_headset *device=&catalog.devices[index];snprintf(selected_id,sizeof(selected_id),"%s",device->id);
    snprintf(selected_name,sizeof(selected_name),"%s",device->name);
    if(!cfg_freedv_usb_select_headset(selected_id)){set_problem("Could not save USB headset selection");return false;}
    selected_gain=cfg_freedv_usb_gain_for(selected_id,selected_name);buttons_refresh(&gain_button);return true;
}
static bool open_audio(void){
    struct timespec now;clock_gettime(CLOCK_REALTIME,&now);
    snprintf(trace_prefix,sizeof(trace_prefix),"/tmp/freedv-usb-%lld-%09ld",(long long)now.tv_sec,now.tv_nsec);
    fd_usb_config config={.radio_source=BOARD_PA_SOURCE,.radio_sink=BOARD_PA_SINK,.trace_prefix=trace_prefix,
        .control={.observe=radio_usb_observe,.key=radio_usb_key,.clear=radio_usb_clear,.inhibit=radio_usb_inhibit,.permit=radio_usb_permit},
        .operational=true,.retain_runtime_traces=true,.tx_timeout_seconds=(unsigned)param_i_get(cfg_freedv_timeout),.headset_id=selected_id,
        .incident_dir=BOARD_DATA "/freedv-usb-incidents",.mic_gain_db=selected_gain};
    fd_usb_audio *created=fd_usb_audio_open(&config);
    pthread_mutex_lock(&pointer_mutex);audio=created;if(audio && atomic_load(&abort_pending))fd_usb_audio_stop(audio);pthread_mutex_unlock(&pointer_mutex);
    if(!audio){set_problem("Audio preparation failed; open Audio and Retry");return false;}
    stage=UI_RUNNING;return true;
}
static void request_restart(void){
    if(exit_requested)return;
    restart_requested=true;popup_close();if(audio)fd_usb_audio_leave(audio);
    /* A device selection/retry can arrive while normal audio is still being
     * quieted. Let that worker acknowledge before restore/reclaim starts. */
    if(!quiet_job.running)stage=UI_STOP;
}
static void finish(void){
    popup_close();if(status_box){lv_obj_del(status_box);status_box=status_label=compact_meter=compact_clip=NULL;}
    if(timer){lv_timer_del(timer);timer=NULL;}
    buttons_load_page(previous_page?previous_page:&buttons_page_vol_1);previous_page=NULL;
    atomic_store(&usb_active,false);freq_strip_set_app(NULL);info_refresh_effective();
    if(input_owned){front_keys_diagnostic_input_release();input_owned=false;}
    freedv_navigation_completed();
}
static void service(lv_timer_t *unused){
    (void)unused;poll_discovery();
    fd_usb_snapshot snapshot={.state=FD_USB_PREPARE,.stopped=true};if(audio)snapshot=fd_usb_audio_snapshot(audio);
    if(audio)audio_controls(&snapshot);
    if(status_label){
        if(exit_requested){
            fd_usb_inputs input={0};bool held=!terminal_exit && radio_usb_observe(NULL,&input) && input.valid && input.pressed;
            lv_label_set_text(status_label,audio && !snapshot.stopped?"FreeDV: finishing - F5 Stop now":held?"FreeDV: release radio PTT to finish":"FreeDV: restoring radio");
        }
        else if(stage==UI_NOTICE)lv_label_set_text(status_label,"FreeDV: acknowledgement required");
        else if(stage==UI_FAILED)lv_label_set_text_fmt(status_label,"FreeDV: %s",problem);
        else if(stage==UI_DISCOVER)lv_label_set_text(status_label,"FreeDV: finding USB headset");
        else if(stage==UI_CLAIM)lv_label_set_text(status_label,"FreeDV: release PTT; waiting for radio RX");
        else if(stage==UI_QUIET)lv_label_set_text(status_label,"FreeDV: preparing audio");
        else if(restart_requested)lv_label_set_text(status_label,"FreeDV: changing audio device");
        else if(snapshot.fault)lv_label_set_text_fmt(status_label,"FreeDV: %s - Audio > Retry",fd_usb_fault_text(snapshot.fault));
        else if(snapshot.startup_ready && !snapshot.tx_eligible)lv_label_set_text(status_label,"FreeDV RADE V1: receiving - RX only outside TX bands");
        else lv_label_set_text_fmt(status_label,"FreeDV %s: %s%s",supported_modes[0].name,state_text(snapshot.state),snapshot.state==FD_USB_RX && !snapshot.armed?" (waiting for readiness)":"");
    }
    buttons_refresh(&exit_button);
    /* Exit wins over a startup/retry. A pending quiet worker is polled before
     * restoration; no join, PulseAudio or radio I2C blocks the GUI thread. */
    if(exit_requested && stage!=UI_QUIET && stage<UI_STOP)stage=UI_STOP;
    if(stage==UI_DISCOVER && choose_headset())stage=UI_CLAIM;
    if(stage==UI_CLAIM){
        if(cfg_transverter_shift_for(cparam_i_get(cfg_fg_freq))){
            set_problem("Transverters unsupported; Exit and choose a direct radio frequency");
        }else if(radio_usb_claim()){
            info_refresh_effective();
            if(main_quiet)open_audio();
            else if(quiet_start(true)){main_quiet=true;stage=UI_QUIET;}
            else set_problem("Could not start audio preparation; Audio > Retry");
        }else if(radio_usb_owned())set_problem("Radio preparation failed; Audio > Retry");
    }
    if(stage==UI_QUIET){bool ok;if(quiet_finished(&ok)){
        if(exit_requested || restart_requested)stage=UI_STOP;
        else if(!ok)set_problem("Could not quiet normal audio; Audio > Retry");else open_audio();
    }}
    if(stage==UI_STOP){
        if(scan){fd_usb_headsets_scan_cancel(scan);return;}
        if(audio){if(!snapshot.stopped)return;
            pthread_mutex_lock(&pointer_mutex);fd_usb_audio *old=audio;audio=NULL;pthread_mutex_unlock(&pointer_mutex);fd_usb_audio_close(old);
        }
        stage=UI_RESTORE;
    }
    if(stage==UI_RESTORE){
        bool restored=!radio_usb_owned() || (terminal_exit?radio_usb_terminal_restore():radio_usb_restore());
        if(!restored)return;
        if(restart_requested && !exit_requested){
            if(!radio_usb_release())return;
            restart_requested=false;atomic_store(&abort_pending,false);stage=UI_DISCOVER;
            if(!scan)start_discovery();
            return;
        }
        if(main_quiet){if(!quiet_start(false)){set_problem("Could not restore normal audio; restart radio");return;}stage=UI_UNQUIET;}
        else stage=UI_RELEASE;
    }
    if(stage==UI_UNQUIET){bool ok;if(!quiet_finished(&ok))return;
        if(!ok){/* Retry restoration on later GUI ticks without blocking input. */stage=UI_RESTORE;return;}
        main_quiet=false;stage=UI_RELEASE;
    }
    if(stage==UI_RELEASE){if(radio_usb_release())finish();}
}
void freedv_usb_open(void){
    if(!scheduler_on_gui_thread() || freedv_usb_active())return;
    if(!front_keys_diagnostic_input_acquire()){msg_schedule_text_fmt("Release panel controls, then select FreeDV again");return;}
    input_owned=true;exit_requested=restart_requested=terminal_exit=main_quiet=false;audio=NULL;
    atomic_store(&abort_pending,false);selected_name[0]=problem[0]=0;selected_gain=0;
    cfg_freedv_usb_headset_id(selected_id,sizeof(selected_id));
    previous_page=buttons_get_cur_page();stage=cfg_freedv_usage_hidden()?UI_DISCOVER:UI_NOTICE;
    atomic_store(&usb_active,true);
    freq_strip_set_app("FreeDV");info_refresh_effective();
    /* Compact strip lives immediately under the frequency/meter rows, away
     * from the ordinary VOL/MFK labels and their standard button marks. */
    status_box=lv_obj_create(lv_scr_act());lv_obj_remove_style_all(status_box);
    lv_obj_set_pos(status_box,0,TOP_BAR_HEIGHT+FREQ_STRIP_H+METER_ROW_H+2);lv_obj_set_size(status_box,800,25);
    lv_obj_set_style_bg_color(status_box,TC(bg),0);lv_obj_set_style_bg_opa(status_box,LV_OPA_80,0);
    lv_obj_set_style_text_font(status_box,&sony_14,0);lv_obj_clear_flag(status_box,LV_OBJ_FLAG_SCROLLABLE);
    status_label=label(status_box,"FreeDV: preparing",6,3,610);
    compact_meter=meter(status_box,622,9,120,7,TC(ok_bar));compact_clip=label(status_box,"",750,3,48);
    lv_obj_set_style_text_color(compact_clip,TC(err),0);
    seen_mic_clips=0;clip_seen=false;
    buttons_load_page(stage==UI_NOTICE?&notice_page:&normal_page);
    timer=lv_timer_create(service,50,NULL);
    if(stage==UI_NOTICE)popup_open(POP_NOTICE);else start_discovery();
}
