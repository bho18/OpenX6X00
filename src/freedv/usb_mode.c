// SPDX-License-Identifier: LGPL-2.1-or-later
#define _POSIX_C_SOURCE 200809L
#include "usb_voice.h"
#include "../whole_value_row.h"
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
#include "../vol_control.h"
#include "../knob_info.h"
#include "../screen_light.h"
#include "../btaudio.h"
#include "../process_lifecycle.h"
#include "main_audio_recovery.h"
#include <stdio.h>
#include <string.h>
#include <time.h>
#include <pthread.h>

/* FreeDV owns radio/audio transactions, not the main screen. Temporary
 * controls use their own LVGL focus group; the spectrum and tuning dial stay
 * in the ordinary screen throughout the session. */
typedef enum { UI_INPUT,UI_NOTICE,UI_DISCOVER,UI_CLAIM,UI_BT_HOLD,UI_QUIET,UI_RUNNING,UI_FAILED,
    UI_STOP,UI_RESTORE,UI_UNQUIET,UI_RELEASE } ui_stage;
typedef enum { POP_NONE,POP_NOTICE,POP_MODE,POP_MIC,POP_AUDIO,POP_HELP } popup_kind;
static atomic_bool usb_active,abort_pending,deadline_abort_pending;
_Static_assert(ATOMIC_BOOL_LOCK_FREE==2,"USB deadline publication must be lock-free");
static pthread_mutex_t pointer_mutex=PTHREAD_MUTEX_INITIALIZER;
static fd_usb_audio *audio;
static ui_stage stage;
static bool exit_requested,restart_requested,terminal_exit,main_quiet,input_owned,volume_exit_queued,volume_exit_warned,volume_rejection_seen;
static uint32_t volume_exit_tick;
static lv_timer_t *timer;
static buttons_page_t *previous_page;
static lv_obj_t *status_box,*status_label,*compact_meter,*compact_clip,*volume_label,*mic_health_label;
static lv_obj_t *popup,*popup_message,*usage_check,*mic_level_label,*edit_hint,*preview_check;
static lv_obj_t *mic_rms_bar,*mic_peak_bar,*clip_label,*headset_label,*audio_list,*retry_button;
typedef enum { NUMBER_NONE,NUMBER_GAIN,NUMBER_TIMEOUT } number_kind;
static struct {lv_obj_t *row,*value;whole_value_row_t *widget;number_kind kind;int draft,original;bool editing;const char *error;} number;
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
static uint32_t overload_tick,near_maximum_tick;
static bool overload_seen,near_maximum_seen,updating_controls,volume_legacy_import,audio_placeholder;
/* A confirmed dropdown selection is intent. An active audio device only
 * changes after released PTT and fresh RX have made teardown safe. */
static struct {char id[256],name[160];bool pending;} device_choice;
static fd_usb_snapshot latest;
static struct {pthread_t thread;atomic_bool done;bool running,target;audio_main_quiet_result_t result;} quiet_job;
static fd_main_recovery quiet_recovery;
static btaudio_rx_hold_t *bt_hold;
static bool bt_release_started,bt_hold_accepted,bt_release_warned,input_release_started,audio_restart_needed,audio_restart_sent;
static bool detailed_capture;
static ui_stage entry_stage;
static void service(lv_timer_t *);
static void popup_open(popup_kind);
static void popup_close(void);
static void number_cancel(void);
static void start_discovery(void);
static void request_restart(void);
static void audio_status(void);
static void retry_current_problem(void);
static void display_observed(const fd_usb_display_event *event,void *context){
    (void)context;if(audio)fd_usb_audio_note_display(audio,event);
}
static uint64_t monotonic_ms(void){struct timespec t;clock_gettime(CLOCK_MONOTONIC,&t);return (uint64_t)t.tv_sec*1000+(uint64_t)t.tv_nsec/1000000;}
static void status_layout(void){
    lv_area_t bounds;if(!status_box || !main_screen_freedv_bounds(&bounds))return;
    int height=bounds.y2-bounds.y1+1;
    lv_obj_set_pos(status_box,bounds.x1,bounds.y1);lv_obj_set_size(status_box,bounds.x2-bounds.x1+1,height);
    if(compact_meter)lv_obj_set_y(compact_meter,(height-7)/2);
}
static void controls_observed(const fd_usb_snapshot *snapshot){
    latest=*snapshot;
    knobs_usb_volume_update(true,snapshot->headset_volume_ready,snapshot->headset_volume_percent,
        snapshot->headset_muted,snapshot->headset_volume_pending);
    bool settled=snapshot->headset_volume_initialized && snapshot->headset_volume_ready && !snapshot->headset_volume_pending &&
        snapshot->headset_volume_confirmed_revision==snapshot->headset_volume_requested_revision &&
        snapshot->headset_volume_confirmed_revision!=snapshot->headset_volume_failed_revision;
    if(volume_legacy_import && settled && cfg_freedv_usb_confirm_legacy_volume(snapshot->headset_id,snapshot->headset_volume_percent,snapshot->headset_muted))volume_legacy_import=false;
    cfg_freedv_usb_volume_observe(snapshot->headset_id,snapshot->headset_volume_confirmed_revision,settled,
        snapshot->headset_volume_percent,snapshot->headset_muted,monotonic_ms());
}

bool freedv_usb_available(void){return true;}
bool freedv_usb_active(void){return atomic_load(&usb_active);}
bool freedv_usb_entry_pending(void){return freedv_usb_active() && (stage==UI_INPUT || stage==UI_NOTICE);}
bool freedv_usb_popup_active(void){return popup!=NULL;}
bool freedv_usb_finishing(void){return freedv_usb_active() && exit_requested;}
const fd_qualified_profile *freedv_usb_profile_view(void){
    if(!freedv_usb_active() || stage==UI_INPUT || stage==UI_NOTICE)return NULL;
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
    if(front_keys_voice_local_event() && audio && !exit_requested){
        bool accepted=fd_usb_audio_adjust_headset_volume(audio,delta);
        if(accepted)volume_rejection_seen=false;
        else if(!volume_rejection_seen){
            volume_rejection_seen=true;
            msg_schedule_text_fmt("Headphone volume unavailable. Open Audio to reconnect.");
        }
    }
    /* Consume the contextual control even when unavailable; falling through
     * would unexpectedly change the separate radio speaker setting. */
    return true;
}
bool freedv_usb_volume_percent(unsigned *percent,bool *muted){
    if(!freedv_usb_active() || !audio)return false;
    fd_usb_snapshot s=fd_usb_audio_snapshot(audio);if(!s.headset_volume_ready)return false;
    if(percent)*percent=s.headset_volume_percent;
    if(muted)*muted=s.headset_muted;
    return true;
}
void freedv_usb_deadline_abort(void){
    atomic_store_explicit(&deadline_abort_pending,true,memory_order_release);
    atomic_store_explicit(&abort_pending,true,memory_order_release);
}
static bool deadline_abort_requested(void *unused){(void)unused;return atomic_load_explicit(&deadline_abort_pending,memory_order_acquire);}
void freedv_usb_abort(void){
    if(!freedv_usb_active())return;
    atomic_store(&abort_pending,true);
    radio_usb_inhibit(NULL);
    pthread_mutex_lock(&pointer_mutex);if(audio)fd_usb_audio_stop(audio);pthread_mutex_unlock(&pointer_mutex);
}
bool freedv_usb_leave(void){
    if(!freedv_usb_active())return false;
    if(exit_requested)return true;
    exit_requested=true;restart_requested=false;device_choice.pending=false;cfg_freedv_usb_volume_flush();popup_close();
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
static const char *exit_button_text(void){return exit_requested?"Leaving":number.editing?"Cancel edit":popup?"Close":"Exit";}
static const char *audio_button_text(void){return "Audio";}
static void soft_button(button_data_t *button);
static void audio_hold(button_data_t *button);
static button_data_t mode_button={.type=BTN_TEXT_FN,.label_fn=mode_button_text,.press=soft_button,.ctrl=0};
static button_data_t gain_button={.type=BTN_TEXT_FN,.label_fn=gain_button_text,.press=soft_button,.ctrl=1};
static button_data_t audio_button={.type=BTN_TEXT_FN,.label_fn=audio_button_text,.press=soft_button,.hold=audio_hold,.ctrl=2};
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
    if(freedv_usb_popup_key(*(uint32_t *)lv_event_get_param(e)))lv_event_stop_processing(e);
}
static void popup_item(lv_obj_t *item){
    styles_popup_item(item);
    lv_group_add_obj(popup_group,item);
    lv_obj_add_event_cb(item,popup_key_event,LV_EVENT_KEY|LV_EVENT_PREPROCESS,NULL);
}
static void dropdown_ready(lv_event_t *event){
    lv_obj_t *list=lv_dropdown_get_list(lv_event_get_target(event));
    styles_dropdown_list(list);
    lv_obj_set_style_max_height(list,180,0);
}
static void popup_dropdown(lv_obj_t *item){
    popup_item(item);lv_dropdown_set_dir(item,LV_DIR_TOP);lv_dropdown_set_symbol(item,NULL);
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
    /* A microphone draft is already audible. Closing or navigating away
     * cancels it just like F5/ESC, before its rollback state is discarded. */
    number_cancel();
    /* Restore indev bindings before removing the widgets they may focus. Drop
     * their half-presses so a popup close cannot activate an underlying item. */
    for(unsigned i=0;i<redirected_count;++i){lv_indev_reset(redirected[i].input,NULL);lv_indev_set_group(redirected[i].input,redirected[i].group);}
    redirected_count=0;
    lv_obj_del(popup);popup=NULL;
    if(popup_group){lv_group_del(popup_group);popup_group=NULL;}
    if(previous_focus && lv_obj_is_valid(previous_focus))lv_group_focus_obj(previous_focus);
    lv_group_set_editing(keyboard_group,previous_editing);
    memset(&number,0,sizeof(number));
    popup_type=POP_NONE;popup_message=usage_check=mic_level_label=edit_hint=preview_check=NULL;
    mic_rms_bar=mic_peak_bar=clip_label=headset_label=audio_list=retry_button=NULL;
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
static bool preview_gain(int gain){
    if(gain<FD_USB_MIC_GAIN_MIN_DB)gain=FD_USB_MIC_GAIN_MIN_DB;
    if(gain>FD_USB_MIC_GAIN_MAX_DB)gain=FD_USB_MIC_GAIN_MAX_DB;
    if(gain==selected_gain)return true;
    if(!audio || stage!=UI_RUNNING || restart_requested || exit_requested || !fd_usb_audio_set_mic_gain(audio,gain))return false;
    selected_gain=gain;
    buttons_refresh(&gain_button);
    return true;
}
static bool commit_gain(int gain){
    if(!audio || stage!=UI_RUNNING || restart_requested || exit_requested || !preview_gain(gain))return false;
    if(gain!=number.original && !cfg_freedv_usb_save_gain(selected_id,gain))msg_schedule_text_fmt("Mic gain changed; could not save preference");
    return true;
}
static void number_draw(void){
    if(!number.widget)return;
    number.draft=whole_value_row_value(number.widget);number.original=whole_value_row_original(number.widget);
    number.editing=whole_value_row_editing(number.widget);
    if(edit_hint)lv_label_set_text(edit_hint,number.error?number.error:number.editing?
        number.kind==NUMBER_GAIN?"Gain and meter update live; click MFK to save. F5 restores previous gain.":"Turn to change whole value; click MFK to apply. F5 cancels edit.":
        "MFK: choose a setting; click to edit the whole value.");
    buttons_refresh(&exit_button);
}
static void number_changed(whole_value_row_t *widget,void *unused){(void)widget;(void)unused;number_draw();}
static bool number_allowed(void *unused){(void)unused;return front_keys_voice_local_event();}
static void number_format(char *text,size_t size,int32_t value,void *unused){
    (void)unused;snprintf(text,size,number.kind==NUMBER_GAIN?"%+d dB":"%d s",(int)value);
}
static bool number_preview(int32_t value,void *unused){
    (void)unused;number.error=NULL;
    if(number.kind!=NUMBER_GAIN || preview_gain(value))return true;
    number.error="Microphone unavailable. Open Audio to reconnect, then adjust gain.";return false;
}
static void number_rollback(int32_t value,void *unused){
    (void)unused;number.error=NULL;
    if(number.kind==NUMBER_GAIN){
        if(audio)fd_usb_audio_set_mic_gain(audio,value);
        selected_gain=value;buttons_refresh(&gain_button);
    }
}
static bool number_commit(int32_t value,void *unused){
    (void)unused;number.error=NULL;bool ok;
    if(number.kind==NUMBER_GAIN)ok=commit_gain(value);
    else {
        ok=!audio || fd_usb_audio_set_tx_timeout(audio,(unsigned)value);
        if(ok){bool voice=fd_writer_voice_owned();ok=voice?fd_writer_voice_begin():fd_writer_enter();
            if(ok){param_i_set(cfg_freedv_timeout,value);if(voice)fd_writer_voice_end();else fd_writer_exit();}
        }
    }
    if(!ok)number.error=number.kind==NUMBER_TIMEOUT?"Release PTT and wait for Receiving, then click to apply.":"Microphone unavailable. Open Audio to reconnect, then apply.";
    return ok;
}
static void number_cancel(void){if(number.widget){number.error=NULL;whole_value_row_cancel(number.widget);}}
static void number_step(int delta){if(number.widget)(void)whole_value_row_step(number.widget,delta);}
static lv_obj_t *number_row(number_kind kind,int value,int x,int y,int width){
    number.kind=kind;number.original=number.draft=value;number.editing=false;number.error=NULL;
    whole_value_row_callbacks_t callbacks={.preview=number_preview,.commit=number_commit,.cancel=number_rollback,
        .changed=number_changed,.format=number_format,.allowed=number_allowed};
    number.widget=whole_value_row_create(popup,popup_group,value,kind==NUMBER_GAIN?FD_USB_MIC_GAIN_MIN_DB:30,
        kind==NUMBER_GAIN?FD_USB_MIC_GAIN_MAX_DB:600,kind==NUMBER_GAIN?1:30,&callbacks);
    number.row=whole_value_row_object(number.widget);number.value=whole_value_row_label(number.widget);
    if(number.row){lv_obj_set_pos(number.row,x,y);lv_obj_set_size(number.row,width,44);popup_item(number.row);number_draw();}
    return number.row;
}
static int catalog_index(const char *id){for(unsigned i=0;i<catalog.count;++i)if(!strcmp(catalog.devices[i].id,id))return (int)i;return -1;}
static int selected_catalog_index(void){return catalog_index(selected_id);}
typedef enum { RETRY_NONE,RETRY_SESSION,RETRY_MIC,RETRY_CONTROLS } retry_kind;
static retry_kind current_retry(void){
    if(exit_requested || restart_requested || stage>=UI_STOP || catalog.pending)return RETRY_NONE;
    if(stage==UI_FAILED || (audio && latest.fault))return RETRY_SESSION;
    if(device_choice.pending)return RETRY_NONE;
    if(!audio || latest.radio_recovering)return RETRY_NONE;
    if(latest.mic_health==FD_USB_MIC_UNAVAILABLE && latest.mic_retry_available)return RETRY_MIC;
    if(latest.headset_volume_unavailable || (cfg_freedv_usb_preview_enabled() && latest.cue_unavailable))return RETRY_CONTROLS;
    return RETRY_NONE;
}
static const char *fault_message(const fd_usb_snapshot *snapshot){
    const char *origin=snapshot->first_fault_origin;
    if(!strcmp(origin,"radio-status-recovery-timeout") || !strcmp(origin,"radio-packet-stale"))return "radio connection lost";
    if(!strcmp(origin,"radio-status-recovery-repeated-loss"))return "Radio connection unstable.";
    if(!strcmp(origin,"radio-recovery-clear-timeout"))return "Radio TX clear did not complete.";
    if(strstr(origin,"unexpected-tx"))return "unexpected radio transmission";
    if(strstr(origin,"tx-status-disappeared"))return "transmit status lost";
    if(!strcmp(origin,"radio-protection"))return "radio protection triggered";
    if(!strcmp(origin,"context-state"))return "audio server disconnected";
    if(!strcmp(origin,"stream-state"))return "USB audio connection lost";
    return fd_usb_fault_text(snapshot->fault);
}
static const char *fault_banner(const fd_usb_snapshot *snapshot){
    const char *origin=snapshot->first_fault_origin;
    /* This row has 280 pixels. Keep the visible fault useful; the Audio
     * popup supplies the full explanation and contextual recovery action. */
    if(!strcmp(origin,"radio-status-recovery-timeout") || !strcmp(origin,"radio-packet-stale"))return "FreeDV: radio status lost";
    if(!strcmp(origin,"radio-status-recovery-repeated-loss"))return "FreeDV: radio link unstable";
    if(!strcmp(origin,"radio-recovery-clear-timeout"))return "FreeDV: TX clear failed";
    if(strstr(origin,"unexpected-tx"))return "FreeDV: unexpected TX";
    if(strstr(origin,"tx-status-disappeared"))return "FreeDV: TX status lost";
    if(!strcmp(origin,"radio-protection"))return "FreeDV: radio protection";
    if(!strcmp(origin,"context-state"))return "FreeDV: audio server lost";
    if(!strcmp(origin,"stream-state"))return "FreeDV: USB audio lost";
    return "FreeDV: audio stopped";
}
static void audio_status(void){
    if(popup_type!=POP_AUDIO || !popup_message)return;
    const char *message="";
    if(restart_requested || (stage>=UI_STOP && !exit_requested))message="Connecting USB headset...";
    else if(audio && latest.fault)message=fault_message(&latest);
    else if(device_choice.pending)message="Release radio PTT and wait for RX to change the headset.";
    else if(catalog.pending)message="Finding USB microphone and playback devices...";
    else if(catalog.error)message="USB discovery failed. Check HOST USB, then Try again.";
    else if(!catalog.count)message="Connect a USB microphone and headphones to HOST USB, then Try again.";
    else if(stage==UI_FAILED)message=problem;
    else if(audio && latest.radio_recovering)message="Recovering radio connection...";
    else if(audio && latest.mic_health==FD_USB_MIC_RECOVERING)message="Recovering microphone...";
    else if(audio && latest.mic_health==FD_USB_MIC_UNAVAILABLE)message="Microphone unavailable. Check the headset, then Try again.";
    else if(audio && latest.headset_volume_recovering)message="Recovering headphone volume control...";
    else if(audio && latest.headset_volume_unavailable)message="Headphone volume unavailable. Try again.";
    else if(audio && cfg_freedv_usb_preview_enabled() && latest.cue_recovering)message="Recovering volume preview...";
    else if(audio && cfg_freedv_usb_preview_enabled() && latest.cue_unavailable)message="Volume preview unavailable. Try again.";
    else if(selected_catalog_index()<0)message="Select a connected USB headset.";
    lv_label_set_text(popup_message,message);
    if(retry_button){
        bool show=current_retry()!=RETRY_NONE;
        if(show)lv_obj_clear_flag(retry_button,LV_OBJ_FLAG_HIDDEN);
        else {
            /* Never leave encoder focus on an invisible recovery action. */
            if(lv_group_get_focused(popup_group)==retry_button && audio_list)lv_group_focus_obj(audio_list);
            lv_obj_add_flag(retry_button,LV_OBJ_FLAG_HIDDEN);
        }
    }
}
static void populate_audio(void){
    if(!audio_list || !popup_message)return;
    char options[FD_USB_HEADSET_MAX*170+64];size_t used=0;options[0]=0;
    int selected=catalog_index(device_choice.pending?device_choice.id:selected_id);
    audio_placeholder=selected<0;
    if(audio_placeholder)used=(size_t)snprintf(options,sizeof(options),"Select USB headset...");
    for(unsigned i=0;i<catalog.count;++i){
        char name[160];snprintf(name,sizeof(name),"%s",catalog.devices[i].name);
        for(char *p=name;*p;++p)if(*p=='\n' || *p=='\r')*p=' ';
        int n=snprintf(options+used,sizeof(options)-used,"%s%s",used?"\n":"",name);
        if(n<0 || (size_t)n>=sizeof(options)-used)break;
        used+=(size_t)n;
    }
    updating_controls=true;
    lv_dropdown_set_options(audio_list,used?options:"Select USB headset...");
    lv_dropdown_set_selected(audio_list,selected>=0?(unsigned)selected+(audio_placeholder?1u:0u):0);
    updating_controls=false;
    if(catalog.pending || !catalog.count)lv_obj_add_state(audio_list,LV_STATE_DISABLED);else lv_obj_clear_state(audio_list,LV_STATE_DISABLED);
    audio_status();
}
static void audio_selected(lv_event_t *event){
    if(updating_controls || !front_keys_voice_local_event() || catalog.pending || !audio_list || exit_requested)return;
    unsigned index=lv_dropdown_get_selected(lv_event_get_target(event));
    if(audio_placeholder){if(!index)return;--index;}
    if(index>=catalog.count)return;
    fd_usb_headset *device=&catalog.devices[index];
    if(!strcmp(device->id,selected_id) && stage!=UI_FAILED && !(audio && latest.fault)){
        device_choice.pending=false;audio_status();return;
    }
    snprintf(device_choice.id,sizeof(device_choice.id),"%s",device->id);
    snprintf(device_choice.name,sizeof(device_choice.name),"%s",device->name);device_choice.pending=true;
    audio_status();
}
static const char usage_text[]=
    "FreeDV requires a USB audio device with microphone input and headphones or speaker output, connected to the HOST USB port. "
    "Built-in/hand microphones and the radio speaker are not supported.\n\n"
    "Use only PTT on the radio: hold to talk, release to listen. Hand-mic PTT does not work. "
    "Tune normally in RX. Select Volume for VOL headset level; TX power uses the usual radio controls.";
static int line_height(void){return lv_font_get_line_height(&sony_18);}
static int control_height(void){int h=line_height()+12;return h<40?40:h;}
static void popup_height(int height){
    int maximum=480-BUTTONS_H-12;if(height>maximum)height=maximum;
    lv_obj_set_height(popup,height);lv_obj_set_y(popup,480-BUTTONS_H-height-4);
}
static void preview_changed(lv_event_t *event){
    if(updating_controls || !front_keys_voice_local_event())return;
    bool enabled=lv_obj_has_state(lv_event_get_target(event),LV_STATE_CHECKED);
    if(!cfg_freedv_usb_set_preview_enabled(enabled)){
        updating_controls=true;
        if(!enabled)lv_obj_add_state(preview_check,LV_STATE_CHECKED);else lv_obj_clear_state(preview_check,LV_STATE_CHECKED);
        updating_controls=false;return;
    }
    if(audio)fd_usb_audio_set_cues_enabled(audio,enabled);
}
static void popup_open(popup_kind kind){
    if(!freedv_usb_active() || exit_requested)return;
    if(popup_type==kind)return; /* The same softkey never loses an edit draft. */
    int line=line_height(),control=control_height();
    int heading=lv_font_get_line_height(&sony_22)+10;
    if(kind==POP_NOTICE || kind==POP_HELP){
        popup_begin(kind,300,"Using FreeDV");
        lv_point_t text_size;
        lv_txt_get_size(&text_size,usage_text,&sony_18,0,2,590,LV_TEXT_FLAG_NONE);
        int tail=kind==POP_NOTICE?line+control+28:control+12;
        int available=480-BUTTONS_H-12-24-heading-tail;
        int body_height=text_size.y<available?text_size.y:available;
        lv_obj_t *body=lv_obj_create(popup);lv_obj_remove_style_all(body);
        lv_obj_set_pos(body,0,heading);lv_obj_set_size(body,590,body_height);
        lv_obj_set_scroll_dir(body,LV_DIR_VER);
        popup_message=label(body,usage_text,0,0,590);lv_obj_set_style_text_line_space(popup_message,2,0);
        int y=heading+body_height+12;
        if(kind==POP_NOTICE){
            usage_check=lv_checkbox_create(popup);lv_checkbox_set_text(usage_check,"Don't show this again");
            lv_obj_set_pos(usage_check,0,y);popup_item(usage_check);y+=line+16;
            lv_obj_t *go=popup_button("Continue",330,y,126,1);lv_obj_set_height(go,control);
            lv_obj_t *cancel=popup_button("Cancel",466,y,126,2);lv_obj_set_height(cancel,control);
            lv_group_focus_obj(go);
        }else {lv_obj_t *close=popup_button("Close",466,y,126,3);lv_obj_set_height(close,control);lv_group_focus_obj(close);}
        popup_height(y+control+24);
    }else if(kind==POP_MODE){
        popup_begin(kind,300,"FreeDV mode");int y=heading+4;
        label(popup,"Mode",0,y+8,160);lv_obj_t *mode=lv_dropdown_create(popup);
        lv_obj_set_pos(mode,270,y);lv_obj_set_size(mode,310,control);lv_dropdown_set_options(mode,supported_modes[0].name);popup_dropdown(mode);
        y+=control+12;label(popup,"Maximum TX",0,y+8,260);number_row(NUMBER_TIMEOUT,param_i_get(cfg_freedv_timeout),320,y,260);
        y+=control+14;edit_hint=label(popup,"",0,y,590);number_draw();y+=line*2+14;
        lv_obj_t *help=popup_button("Help",0,y,126,4);lv_obj_set_height(help,control);
        lv_obj_t *close=popup_button("Close",466,y,126,3);lv_obj_set_height(close,control);
        popup_height(y+control+24);lv_group_focus_obj(mode);
    }else if(kind==POP_MIC){
        popup_begin(kind,320,"Microphone gain");int y=heading+4;
        label(popup,"Speech gain",0,y+8,260);number_row(NUMBER_GAIN,selected_gain,320,y,260);y+=control+12;
        mic_level_label=label(popup,"Mic RMS -- | Peak -- dBFS",0,y,590);y+=line+6;
        mic_rms_bar=meter(popup,0,y,580,12,TC(ok_bar));mic_peak_bar=meter(popup,0,y+18,580,5,TC(warn));y+=30;
        clip_label=label(popup,"",0,y,590);y+=line+4;
        headset_label=label(popup,"",0,y,590);y+=line+8;
        edit_hint=label(popup,"",0,y,590);number_draw();y+=line*2+10;
        lv_obj_t *close=popup_button("Close",466,y,126,3);lv_obj_set_height(close,control);
        popup_height(y+control+24);lv_group_focus_obj(number.row);
    }else if(kind==POP_AUDIO){
        popup_begin(kind,320,"USB audio device");int y=heading+2;
        popup_message=label(popup,"",0,y,590);lv_obj_set_height(popup_message,line*2+2);y+=line*2+10;
        audio_list=lv_dropdown_create(popup);lv_obj_set_pos(audio_list,0,y);lv_obj_set_size(audio_list,590,control);
        popup_dropdown(audio_list);lv_obj_add_event_cb(audio_list,audio_selected,LV_EVENT_VALUE_CHANGED,NULL);populate_audio();y+=control+10;
        preview_check=lv_checkbox_create(popup);lv_checkbox_set_text(preview_check,"Volume preview");
        lv_obj_set_pos(preview_check,0,y);if(cfg_freedv_usb_preview_enabled())lv_obj_add_state(preview_check,LV_STATE_CHECKED);
        popup_item(preview_check);lv_obj_add_event_cb(preview_check,preview_changed,LV_EVENT_VALUE_CHANGED,NULL);y+=line+14;
        retry_button=popup_button("Try again",310,y,140,7);lv_obj_set_height(retry_button,control);
        lv_obj_t *close=popup_button("Close",466,y,126,3);lv_obj_set_height(close,control);
        audio_status();
        popup_height(y+control+24);lv_group_focus_obj(audio_list);
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
    if(action==3){if(number.editing)number_cancel();else popup_close();return;}
    if(action==4){popup_open(POP_HELP);return;}
    if(action==7)retry_current_problem();
}
bool freedv_usb_softkey(unsigned index,bool hold){
    if(!freedv_usb_active())return false;
    if(!front_keys_voice_local_event())return true;
    if(hold){
        if(index==3 && stage!=UI_NOTICE && !exit_requested){popup_close();return false;}
        if(index==2 && stage!=UI_NOTICE && !exit_requested)popup_open(POP_AUDIO);
        return true;
    }
    if(stage==UI_NOTICE){if(index==4)acknowledge();else if(index==3)freedv_navigation_request(FD_NAV_HOME,0,0,NULL);return true;}
    if(index==4){if(exit_requested)return true;else if(number.editing)number_cancel();else if(popup)popup_close();else freedv_navigation_request(FD_NAV_HOME,0,0,NULL);return true;}
    if(exit_requested)return true;
    if(index==0)popup_open(POP_MODE);else if(index==1)popup_open(POP_MIC);else if(index==2)popup_open(POP_AUDIO);
    else if(index==3){popup_close();return false;} /* ordinary TX-power action */
    return true;
}
static void soft_button(button_data_t *button){freedv_usb_softkey((unsigned)button->ctrl,false);}
static void audio_hold(button_data_t *button){(void)button;freedv_usb_softkey(2,true);}
bool freedv_usb_popup_key(uint32_t code){
    if(!popup)return false;
    if(!front_keys_voice_local_event())return true;
    if(code==KEY_VOL_LEFT_EDIT || code==KEY_VOL_RIGHT_EDIT){vol_update(code==KEY_VOL_LEFT_EDIT?-1:1);return true;}
    if(code==KEY_VOL_LEFT_SELECT || code==KEY_VOL_RIGHT_SELECT){vol_change_ctrl(code==KEY_VOL_LEFT_SELECT?-1:1);return true;}
    if(code==KEY_VOL_PRESS){main_screen_vol_press();return true;}
    if(code==KEY_MFK_PREV || code==KEY_MFK_NEXT){
        int delta=code==KEY_MFK_PREV?-1:1;
        if(number.editing)number_step(delta);
        else if(lv_obj_check_type(lv_group_get_focused(popup_group),&lv_dropdown_class) &&
                lv_dropdown_is_open(lv_group_get_focused(popup_group))){
            uint32_t key=delta<0?LV_KEY_UP:LV_KEY_DOWN;
            lv_event_send(lv_group_get_focused(popup_group),LV_EVENT_KEY,&key);
        }
        else if(delta<0)lv_group_focus_prev(popup_group);else lv_group_focus_next(popup_group);
        return true;
    }
    if(code==LV_KEY_ESC){
        if(number.editing){number_cancel();return true;}
        if(audio_list && lv_dropdown_is_open(audio_list)){
            lv_dropdown_close(audio_list);populate_audio();lv_group_set_editing(popup_group,false);return true;
        }
        if(stage==UI_NOTICE)freedv_navigation_request(FD_NAV_HOME,0,0,NULL);else popup_close();return true;
    }
    if(code>=KEYBOARD_F1 && code<=KEYBOARD_F5)return freedv_usb_softkey(code-KEYBOARD_F1,false);
    return false;
}
static void dbfs_text(char *out,size_t size,int value){int magnitude=value<0?-value:value;snprintf(out,size,"%s%d.%d",value<0?"-":"",magnitude/10,magnitude%10);}
static void audio_controls(const fd_usb_snapshot *s){
    bool meter_ready=s->mic_meter_ready && !s->fault && !s->stopped;
    if(meter_ready && s->mic_overload){overload_seen=true;overload_tick=lv_tick_get();}
    if(meter_ready && s->mic_near_maximum){near_maximum_seen=true;near_maximum_tick=lv_tick_get();}
    bool overload=!s->fault && overload_seen && lv_tick_elaps(overload_tick)<1500;
    bool near_maximum=!s->fault && near_maximum_seen && lv_tick_elaps(near_maximum_tick)<1500;
    if(compact_meter)lv_bar_set_value(compact_meter,meter_ready?s->mic_rms_dbfs10:-600,LV_ANIM_OFF);
    if(compact_clip){lv_label_set_text(compact_clip,overload?"CLIP":near_maximum?"HIGH":"");
        lv_obj_set_style_text_color(compact_clip,overload?TC(err):TC(warn),0);}
    if(!mic_level_label)return;
    if(meter_ready){char rms[20],peak[20];dbfs_text(rms,sizeof(rms),s->mic_rms_dbfs10);dbfs_text(peak,sizeof(peak),s->mic_peak_dbfs10);
        lv_label_set_text_fmt(mic_level_label,"Mic RMS %s | Peak %s dBFS",rms,peak);
        lv_bar_set_value(mic_rms_bar,s->mic_rms_dbfs10,LV_ANIM_OFF);lv_bar_set_value(mic_peak_bar,s->mic_peak_dbfs10,LV_ANIM_OFF);
        lv_obj_set_style_bg_color(mic_peak_bar,overload?TC(err):near_maximum?TC(warn):TC(ok_bar),LV_PART_INDICATOR);
    }else {lv_label_set_text(mic_level_label,"Mic RMS -- | Peak -- dBFS");lv_bar_set_value(mic_rms_bar,-600,LV_ANIM_OFF);lv_bar_set_value(mic_peak_bar,-600,LV_ANIM_OFF);}
    lv_label_set_text(clip_label,overload?"Mic overload - lower gain":near_maximum?"Near maximum":"");
    lv_obj_set_style_text_color(clip_label,overload?TC(err):near_maximum?TC(warn):TC(text),0);
    if(s->headset_volume_ready)lv_label_set_text_fmt(headset_label,"Headphones: %u%s | turn VOL",s->headset_volume_percent,s->headset_muted?" (muted)":"");
    else lv_label_set_text(headset_label,"Headphones: USB volume unavailable");
}
static const char *state_text(fd_usb_state value){
    switch(value){case FD_USB_PREPARE:return "Preparing audio";case FD_USB_RX:return "Receiving";
        case FD_USB_START:return "Preparing TX";case FD_USB_KEY_PENDING:return "Waiting for TX";
        case FD_USB_TX:return "Transmitting";case FD_USB_DRAIN:return "Finishing speech";
        case FD_USB_UNKEY:return "Clearing TX";case FD_USB_RX_READY:return "Waiting for RX";
        case FD_USB_TUNING:return "Tuning";case FD_USB_RECOVERING:return "Recovering radio connection";
        case FD_USB_FAULT:return "Stopped";case FD_USB_DONE:return "Stopped";default:return "Working";}
}
static void *quiet_worker(void *unused){
    (void)unused;quiet_job.result=audio_freedv_main_quiet_result(quiet_job.target);
    atomic_store_explicit(&quiet_job.done,true,memory_order_release);return NULL;
}
static void quiet_begin(bool target){fd_main_recovery_begin(&quiet_recovery,target,monotonic_ms());}
static fd_main_recovery_action quiet_progress(void){
    bool completed=quiet_job.running && atomic_load_explicit(&quiet_job.done,memory_order_acquire);
    audio_main_quiet_result_t result=completed?quiet_job.result:AUDIO_MAIN_FAILED;
    if(completed){pthread_join(quiet_job.thread,NULL);quiet_job.running=false;}
    fd_main_recovery_action action=fd_main_recovery_step(&quiet_recovery,monotonic_ms(),quiet_job.running,completed,result);
    if(action==FD_MAIN_RECOVERY_START){
        quiet_job.target=quiet_recovery.quiet;quiet_job.result=AUDIO_MAIN_FAILED;
        atomic_store(&quiet_job.done,false);
        if(pthread_create(&quiet_job.thread,NULL,quiet_worker,NULL))return FD_MAIN_RECOVERY_FAILED;
        quiet_job.running=true;return FD_MAIN_RECOVERY_WAIT;
    }
    return action;
}
static void fail_entry(const char *text){
    snprintf(problem,sizeof(problem),"%s",text);msg_schedule_text_fmt("%s",problem);
    exit_requested=true;restart_requested=false;device_choice.pending=false;
    if(scan)fd_usb_headsets_scan_cancel(scan);
    if(audio)fd_usb_audio_stop(audio);
    stage=UI_STOP;
}
static bool volume_preference_ready(void){
    if(!volume_exit_queued){cfg_freedv_usb_volume_flush();volume_exit_queued=true;volume_exit_tick=lv_tick_get();}
    cfg_freedv_usb_volume_poll();bool busy=cfg_freedv_usb_volume_busy();
    if(busy && lv_tick_elaps(volume_exit_tick)<2500)return false;
    if(busy && !volume_exit_warned){volume_exit_warned=true;msg_schedule_text_fmt("Headphone preference save is still pending.");}
    return true;
}
static bool request_audio_restart_if_safe(void){
    if(!audio_restart_needed)return false;
    bool safe=radio_usb_owned()?radio_usb_restore():radio_get_state()==RADIO_RX && !radio_no_data();
    if(!safe || !volume_preference_ready())return true;
    if(status_label)lv_label_set_text(status_label,"FreeDV: restarting audio...");
    if(!audio_restart_sent){
        cfg_freedv_usb_volume_flush();
        /* Keep ownership and a possibly blocked quiet worker alive until the
         * coordinator exits. A deadline is never an RF-clear acknowledgement. */
        audio_restart_sent=process_lifecycle_request_audio_restart();
    }
    return true;
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
    if(catalog.error){set_problem("USB discovery failed. Open Audio to reconnect.");return false;}
    int index=selected_catalog_index();
    if(index<0 && !*selected_id && catalog.count==1)index=0;
    if(index<0){set_problem(catalog.count?"Select a USB headset in Audio":"Connect a USB headset to HOST, then open Audio");return false;}
    fd_usb_headset *device=&catalog.devices[index];snprintf(selected_id,sizeof(selected_id),"%s",device->id);
    snprintf(selected_name,sizeof(selected_name),"%s",device->name);
    if(!cfg_freedv_usb_capture_legacy_volume() || !cfg_freedv_usb_select_headset(selected_id))msg_schedule_text_fmt("Headset selected for this session; preference was not saved");
    selected_gain=cfg_freedv_usb_gain_for(selected_id,selected_name);buttons_refresh(&gain_button);return true;
}
static bool open_audio(void){
    cfg_freedv_usb_volume volume;
    if(!cfg_freedv_usb_volume_target(selected_id,&volume)){
        volume=(cfg_freedv_usb_volume){.legacy_import=true};
        msg_schedule_text_fmt("Volume preference unavailable; keeping the headset's current level");
    }
    volume_legacy_import=volume.legacy_import;
    struct timespec now;clock_gettime(CLOCK_REALTIME,&now);
    snprintf(trace_prefix,sizeof(trace_prefix),"/tmp/freedv-usb-%lld-%09ld",(long long)now.tv_sec,now.tv_nsec);
    process_lifecycle_set_session(strrchr(trace_prefix,'/')+1);
    fd_usb_config config={.radio_source=BOARD_PA_SOURCE,.radio_sink=BOARD_PA_SINK,.trace_prefix=trace_prefix,
        .control={.observe=radio_usb_observe,.key=radio_usb_key,.clear=radio_usb_clear,.inhibit=radio_usb_inhibit,.permit=radio_usb_permit,.abort_requested=deadline_abort_requested},
        .operational=true,.refinement=true,.hardening=true,.cues_enabled=cfg_freedv_usb_preview_enabled(),.retain_runtime_traces=true,.detailed_capture=detailed_capture,
        .headset_volume_initial_valid=!volume.legacy_import,.headset_volume_initial_percent=volume.percent,.headset_volume_initial_muted=volume.muted,
        .tx_timeout_seconds=(unsigned)param_i_get(cfg_freedv_timeout),.headset_id=selected_id,
        .incident_dir=BOARD_DATA "/freedv-usb-incidents",.mic_gain_db=selected_gain};
    fd_usb_audio *created=fd_usb_audio_open(&config);
    pthread_mutex_lock(&pointer_mutex);audio=created;if(audio && atomic_load(&abort_pending))fd_usb_audio_stop(audio);pthread_mutex_unlock(&pointer_mutex);
    if(!audio){set_problem("Audio preparation failed. Open Audio to reconnect.");return false;}
    backlight_set_observer(display_observed,NULL);fd_usb_display_event event;backlight_snapshot(&event);fd_usb_audio_note_display(audio,&event);
    stage=UI_RUNNING;return true;
}
static void request_restart(void){
    if(exit_requested)return;
    restart_requested=true;cfg_freedv_usb_volume_flush();
    if(popup_type!=POP_AUDIO)popup_close();
    if(audio)fd_usb_audio_leave(audio);
    /* A device selection/retry can arrive while normal audio is still being
     * quieted. Let that worker acknowledge before restore/reclaim starts. */
    if(!quiet_job.running)stage=UI_STOP;
}
static void retry_current_problem(void){
    switch(current_retry()){
    case RETRY_SESSION:request_restart();break;
    case RETRY_MIC:
        if(!fd_usb_audio_retry_microphone(audio))msg_schedule_text_fmt("Release PTT and wait for RX before reconnecting the microphone");
        break;
    case RETRY_CONTROLS:
        if(!fd_usb_audio_retry_controls(audio))msg_schedule_text_fmt("Release PTT and wait for RX before reconnecting headphone controls");
        break;
    default:break;
    }
    audio_status();
}
static void apply_device_choice(const fd_usb_snapshot *snapshot){
    if(!device_choice.pending || exit_requested || restart_requested || stage>=UI_STOP || catalog.pending)return;
    if(radio_usb_owned()){
        fd_usb_inputs input={0};
        if(!radio_usb_observe(NULL,&input) || !input.valid || !input.rf_valid || input.rf_tx || input.pressed || input.protection)return;
        if(audio && !snapshot->stopped && (snapshot->state!=FD_USB_RX || snapshot->radio_recovering || snapshot->pressed))return;
    }else if(audio)return;
    /* A queued device choice may become safe while another popup is open.
     * Restore its live draft before switching the selected profile. */
    number_cancel();
    snprintf(selected_id,sizeof(selected_id),"%s",device_choice.id);
    snprintf(selected_name,sizeof(selected_name),"%s",device_choice.name);device_choice.pending=false;
    if(!cfg_freedv_usb_select_headset(selected_id))msg_schedule_text_fmt("Headset selected for this session; preference was not saved");
    selected_gain=cfg_freedv_usb_gain_for(selected_id,selected_name);buttons_refresh(&gain_button);
    request_restart();
}
static void finish(void){
    if(input_owned){
        if(!input_release_started){
            if(!front_keys_input_begin(true))return;
            input_release_started=true;
        }
        fd_front_input_result result=front_keys_input_poll();
        if(result==FD_FRONT_INPUT_PENDING)return;
        if(result==FD_FRONT_INPUT_FAILED){input_release_started=false;return;}
        input_owned=false;
    }
    backlight_set_observer(NULL,NULL);popup_close();if(status_box){lv_obj_del(status_box);status_box=status_label=compact_meter=compact_clip=volume_label=mic_health_label=NULL;}
    main_screen_freedv_row(false);knobs_usb_volume_update(false,false,0,false,false);
    if(timer){lv_timer_del(timer);timer=NULL;}
    buttons_load_page(previous_page?previous_page:&buttons_page_vol_1);previous_page=NULL;
    atomic_store(&usb_active,false);freq_strip_set_app(NULL);info_refresh_effective();
    freedv_navigation_completed();
}
static void service(lv_timer_t *unused){
    (void)unused;cfg_freedv_usb_volume_poll();poll_discovery();
    if(audio_freedv_main_lost() && !audio_restart_needed){
        audio_restart_needed=true;exit_requested=true;restart_requested=false;device_choice.pending=false;
        radio_usb_inhibit(NULL);if(audio)fd_usb_audio_stop(audio);
        if(stage!=UI_QUIET)stage=UI_STOP;
    }
    fd_usb_snapshot snapshot={.state=FD_USB_PREPARE,.stopped=true};if(audio)snapshot=fd_usb_audio_snapshot(audio);
    status_layout();
    if(audio){
        controls_observed(&snapshot);audio_controls(&snapshot);
        if(snapshot.headset_volume_ready)lv_label_set_text_fmt(volume_label,"Headphones %u%s",snapshot.headset_volume_percent,snapshot.headset_muted?" muted":"");
        else lv_label_set_text(volume_label,"HP unavailable");
        lv_label_set_text(mic_health_label,snapshot.fault || snapshot.stopped?"Audio stopped":snapshot.radio_recovering?"TX paused":
            snapshot.mic_health==FD_USB_MIC_RECOVERING?"Mic recovering":snapshot.mic_health==FD_USB_MIC_UNAVAILABLE?"Mic unavailable":snapshot.mic_meter_ready?"Mic ready":"Mic preparing");
    }
    if(stage==UI_FAILED && mic_health_label)lv_label_set_text(mic_health_label,"Audio unavailable");
    apply_device_choice(&snapshot);audio_status();
    if(status_label){
        if(exit_requested){
            fd_usb_inputs input={0};bool held=!terminal_exit && radio_usb_observe(NULL,&input) && input.valid && input.pressed;
            lv_label_set_text(status_label,audio && !snapshot.stopped?"FreeDV: leaving":held?"FreeDV: release radio PTT to finish":"FreeDV: restoring radio");
        }
        else if(stage==UI_INPUT)lv_label_set_text(status_label,"FreeDV: preparing controls");
        else if(stage==UI_NOTICE)lv_label_set_text(status_label,"FreeDV: acknowledgement required");
        else if(stage==UI_FAILED)lv_label_set_text_fmt(status_label,"FreeDV: %s",problem);
        else if(stage==UI_DISCOVER)lv_label_set_text(status_label,"FreeDV: finding USB headset");
        else if(stage==UI_CLAIM)lv_label_set_text(status_label,"FreeDV: release PTT; waiting for radio RX");
        else if(stage==UI_BT_HOLD)lv_label_set_text(status_label,"FreeDV: preparing Bluetooth audio");
        else if(stage==UI_QUIET)lv_label_set_text(status_label,"FreeDV: preparing audio");
        else if(restart_requested)lv_label_set_text(status_label,"FreeDV: changing audio device");
        else if(snapshot.fault)lv_label_set_text(status_label,fault_banner(&snapshot));
        else if(snapshot.radio_recovering)lv_label_set_text(status_label,"FreeDV: recovering radio connection");
        else if(snapshot.mic_health==FD_USB_MIC_RECOVERING)lv_label_set_text(status_label,"FreeDV: recovering microphone");
        else if(snapshot.mic_health==FD_USB_MIC_UNAVAILABLE)lv_label_set_text(status_label,"FreeDV: microphone unavailable");
        else if(snapshot.state==FD_USB_RX && !snapshot.armed && snapshot.pressed)lv_label_set_text(status_label,"FreeDV: release radio PTT to resume");
        else if(snapshot.startup_ready && !snapshot.tx_eligible)lv_label_set_text(status_label,"FreeDV: RX only (outside TX bands)");
        else lv_label_set_text_fmt(status_label,"FreeDV: %s%s",state_text(snapshot.state),snapshot.state==FD_USB_RX && !snapshot.armed?" [TX pending]":"");
    }
    buttons_refresh(&exit_button);buttons_refresh(&audio_button);
    /* Exit wins over a startup/retry. A pending quiet worker is polled before
     * restoration; no join, PulseAudio or radio I2C blocks the GUI thread. */
    if(exit_requested && stage!=UI_QUIET && stage<UI_STOP)stage=UI_STOP;
    if(stage==UI_INPUT){
        fd_front_input_result input=front_keys_input_poll();
        if(input==FD_FRONT_INPUT_FAILED){
            front_keys_input_cancel_acquire();input_owned=false;
            fail_entry("Release panel controls, then select FreeDV again");
        }else if(input==FD_FRONT_INPUT_READY){
            stage=entry_stage;buttons_load_page(stage==UI_NOTICE?&notice_page:&normal_page);
            if(stage==UI_NOTICE)popup_open(POP_NOTICE);else start_discovery();
        }
    }
    if(stage==UI_DISCOVER && choose_headset())stage=UI_CLAIM;
    if(stage==UI_CLAIM){
        if(process_lifecycle_stopping())fail_entry("Radio is shutting down");
        else if(cfg_transverter_shift_for(cparam_i_get(cfg_fg_freq)))
            set_problem("Transverters unsupported; Exit and choose a direct radio frequency");
        else if(radio_usb_claim()){
            info_refresh_effective();
            if(!bt_hold)bt_hold=btaudio_rx_hold_begin();
            if(!bt_hold)fail_entry("Could not prepare native Bluetooth audio");
            else stage=UI_BT_HOLD;
        }else if(radio_usb_owned())fail_entry("Radio preparation failed. Select FreeDV again to reconnect.");
    }
    if(stage==UI_BT_HOLD){
        char reason[192]={0};btaudio_rx_hold_status held=btaudio_rx_hold_poll(bt_hold,reason,sizeof(reason));
        if(held==BTAUDIO_RX_HELD){
            bt_hold_accepted=true;
            if(main_quiet)open_audio();
            else {main_quiet=true;quiet_begin(true);stage=UI_QUIET;}
        }else if(held==BTAUDIO_RX_FAILED || held==BTAUDIO_RX_REBOOT_REQUIRED)
            fail_entry(*reason?reason:"Could not prepare native Bluetooth audio");
    }
    if(stage==UI_QUIET){
        fd_main_recovery_action progress=quiet_progress();
        if(progress==FD_MAIN_RECOVERY_ACKNOWLEDGED){
            if(exit_requested || restart_requested)stage=UI_STOP;else open_audio();
        }else if(progress==FD_MAIN_RECOVERY_FAILED || progress==FD_MAIN_RECOVERY_STALLED){
            if(progress==FD_MAIN_RECOVERY_STALLED || audio_freedv_main_lost())audio_restart_needed=true;
            fail_entry("Could not prepare audio; restoring normal operation");
        }
    }
    if(stage==UI_STOP){
        if(scan){fd_usb_headsets_scan_cancel(scan);return;}
        if(audio){if(!snapshot.stopped)return;
            backlight_set_observer(NULL,NULL);cfg_freedv_usb_volume_flush();
            pthread_mutex_lock(&pointer_mutex);fd_usb_audio *old=audio;audio=NULL;pthread_mutex_unlock(&pointer_mutex);fd_usb_audio_close(old);
        }
        stage=UI_RESTORE;
    }
    if(stage==UI_RESTORE){
        bool restored=!radio_usb_owned() || (terminal_exit?radio_usb_terminal_restore():radio_usb_restore());
        if(!restored)return;
        if(request_audio_restart_if_safe())return;
        if(restart_requested && !exit_requested){
            if(!radio_usb_release())return;
            restart_requested=false;atomic_store(&abort_pending,false);stage=UI_DISCOVER;
            if(!scan)start_discovery();
            return;
        }
        if(main_quiet){quiet_begin(false);stage=UI_UNQUIET;}
        else stage=UI_RELEASE;
    }
    if(stage==UI_UNQUIET){
        fd_main_recovery_action progress=quiet_progress();
        if(progress==FD_MAIN_RECOVERY_WAIT || progress==FD_MAIN_RECOVERY_START)return;
        if(progress!=FD_MAIN_RECOVERY_ACKNOWLEDGED){
            audio_restart_needed=true;(void)request_audio_restart_if_safe();return;
        }
        main_quiet=false;stage=UI_RELEASE;
    }
    if(stage==UI_RELEASE){
        if(!volume_preference_ready())return;
        if(!radio_usb_release())return;
        if(bt_hold){
            if(!bt_release_started){btaudio_rx_hold_release(bt_hold);bt_release_started=true;}
            char reason[192]={0};btaudio_rx_hold_status held=btaudio_rx_hold_poll(bt_hold,reason,sizeof(reason));
            if(held==BTAUDIO_RX_FAILED && bt_hold_accepted && !bt_release_warned){
                bt_release_warned=true;msg_schedule_text_fmt("Bluetooth RX audio is reconnecting");
            }
            if(!btaudio_rx_hold_close(bt_hold))return;
            bt_hold=NULL;
        }
        finish();
    }
}
void freedv_usb_open(void){
    if(!scheduler_on_gui_thread() || freedv_usb_active() || process_lifecycle_stopping())return;
    if(!front_keys_input_begin(false)){msg_schedule_text_fmt("Release panel controls, then select FreeDV again");return;}
    input_owned=true;input_release_started=bt_release_started=bt_hold_accepted=bt_release_warned=audio_restart_needed=audio_restart_sent=false;
    exit_requested=restart_requested=terminal_exit=main_quiet=false;audio=NULL;bt_hold=NULL;
    detailed_capture=cfg_freedv_detailed_capture_enabled();memset(&device_choice,0,sizeof(device_choice));
    volume_exit_queued=volume_exit_warned=volume_rejection_seen=false;
    atomic_store(&abort_pending,false);atomic_store(&deadline_abort_pending,false);selected_name[0]=problem[0]=0;selected_gain=0;
    memset(&latest,0,sizeof(latest));volume_legacy_import=false;
    cfg_freedv_usb_capture_legacy_volume();
    cfg_freedv_usb_headset_id(selected_id,sizeof(selected_id));
    previous_page=buttons_get_cur_page();entry_stage=cfg_freedv_usage_hidden()?UI_DISCOVER:UI_NOTICE;stage=UI_INPUT;
    atomic_store(&usb_active,true);
    freq_strip_set_app("FreeDV");info_refresh_effective();
    main_screen_freedv_row(true);knobs_usb_volume_update(true,false,0,false,false);
    /* The main-screen layout reserves this row; it is never painted over
     * the spectrum or frequency scale. */
    status_box=lv_obj_create(lv_scr_act());lv_obj_remove_style_all(status_box);
    lv_obj_add_style(status_box,&popup_style,0);lv_obj_set_style_pad_all(status_box,0,0);
    lv_obj_set_style_border_width(status_box,0,0);lv_obj_set_style_radius(status_box,0,0);
    lv_obj_set_style_text_font(status_box,&sony_18,0);lv_obj_clear_flag(status_box,LV_OBJ_FLAG_SCROLLABLE);
    status_label=label(status_box,"FreeDV: preparing",6,4,280);lv_label_set_long_mode(status_label,LV_LABEL_LONG_DOT);
    volume_label=label(status_box,"HP unavailable",290,4,210);lv_label_set_long_mode(volume_label,LV_LABEL_LONG_DOT);lv_obj_set_style_text_font(volume_label,&sony_14,0);
    mic_health_label=label(status_box,"Mic preparing",506,4,168);lv_label_set_long_mode(mic_health_label,LV_LABEL_LONG_DOT);
    lv_obj_set_style_text_font(mic_health_label,&sony_14,0);
    compact_meter=meter(status_box,684,9,78,7,TC(ok_bar));compact_clip=label(status_box,"",768,4,30);
    lv_obj_set_style_text_font(compact_clip,&sony_14,0);
    status_layout();
    lv_obj_set_style_text_color(compact_clip,TC(err),0);
    overload_seen=near_maximum_seen=false;
    buttons_load_page(stage==UI_NOTICE?&notice_page:&normal_page);
    timer=lv_timer_create(service,50,NULL);
    service(NULL);
}
