// SPDX-License-Identifier: LGPL-2.1-or-later
/* Real LVGL, front input fence, VOL driver and persisted settings. Pulse,
 * radio and modem workers are replaced; no PTT or RF operation is performed. */
#define _POSIX_C_SOURCE 200809L
#include "src/freedv/usb_mode.c"
#include "src/front_keys.c"
#include "src/knob_device.c"
#include "src/mfk_device.c"
#include <assert.h>
#include <sys/stat.h>
#include <unistd.h>
#include <stdlib.h>
void usb_ui_cfg_init(void);
void usb_ui_cfg_verify_hidden(bool);
void usb_ui_cfg_verify_profiles(void);
void usb_ui_cfg_close(void);
lv_group_t *keyboard_group;
encoder_t *mfk;
rotary_t *vol;
lv_style_t dialog_item_style,dialog_item_focus_style,dialog_item_edited_style,popup_style;
int top_bar_h=66,freq_strip_h=50,meter_row_h=0,buttons_h=54;
button_data_t btn_tx_pwr={.type=BTN_TEXT,.label="TX power"};
buttons_page_t buttons_page_vol_1;
static buttons_page_t *shown_page=&buttons_page_vol_1;
static fd_usb_snapshot observed;
static unsigned headset_changes,mic_changes,claim_count,restore_count,open_count,close_count,leave_count,stop_count,completion_count;
static bool adapter_owned,leave_pending;
static pthread_t gui_thread;
static atomic_uint quiet_calls;
static atomic_bool quiet_block;
static lv_point_t pointer_position;
static lv_indev_state_t pointer_state;
struct fd_usb_audio {unsigned placeholder;} fake_audio;
struct fd_usb_headset_scan {bool cancelled;} fake_scan;
static lv_color_t draw_pixels[800*32];
uint32_t theme_hex(theme_color_t color){
    if(color==TC_track)return 0x303030;
    if(color==TC_err)return 0xff4040;
    if(color==TC_ok_bar)return 0x40c060;
    if(color==TC_warn)return 0xffc040;
    return 0xffffff;
}
void styles_dropdown_list(lv_obj_t *list){(void)list;}
bool backlight_tick(void){return false;}
radio_state_t radio_get_state(void){return RADIO_RX;}
bool keyboard_diagnostic_reset(void){return true;}
bool keyboard_voice_local_event(lv_indev_t *i,unsigned g){(void)i;(void)g;return false;}
void msg_schedule_text_fmt(const char *format,...){(void)format;}
void freq_strip_set_app(const char *name){(void)name;}
void info_refresh_effective(void){}
void buttons_load_page(buttons_page_t *page){shown_page=page;}
buttons_page_t *buttons_get_cur_page(void){return shown_page;}
void buttons_refresh(button_data_t *button){(void)button;}
bool freedv_navigation_request(fd_nav_kind kind,int32_t value,int32_t detail,const char *text){
    (void)value;(void)detail;(void)text;assert(kind==FD_NAV_HOME);return freedv_usb_leave();
}
void freedv_navigation_completed(void){assert(!adapter_owned && !input_owned);++completion_count;}
bool radio_usb_owned(void){return adapter_owned;}
int radio_usb_power_tenths(void){return 50;}
bool radio_usb_claim(void){assert(stage!=UI_NOTICE);++claim_count;assert(!adapter_owned);assert(fd_writer_voice_reserve());adapter_owned=true;return true;}
bool radio_usb_restore(void){++restore_count;return true;}
bool radio_usb_terminal_restore(void){return radio_usb_restore();}
bool radio_usb_release(void){if(adapter_owned){adapter_owned=false;fd_writer_release();}return true;}
bool radio_usb_observe(void *unused,fd_usb_inputs *in){(void)unused;*in=(fd_usb_inputs){.valid=true,.rf_valid=true,.configuration_ready=true,.tx_eligible=true};return true;}
bool radio_usb_key(void *unused,uint64_t release){(void)unused;(void)release;assert(0);return false;}
void radio_usb_clear(void *unused,bool halt){(void)unused;(void)halt;}
void radio_usb_inhibit(void *unused){(void)unused;}
bool radio_usb_permit(void *unused,bool allow){(void)unused;(void)allow;return true;}
void radio_usb_edge(bool pressed){(void)pressed;}
bool audio_freedv_main_quiet(bool quiet){
    (void)quiet;assert(!pthread_equal(pthread_self(),gui_thread));atomic_fetch_add(&quiet_calls,1);
    while(atomic_load(&quiet_block)){struct timespec tick={.tv_nsec=1000000};nanosleep(&tick,NULL);}
    return true;
}
fd_usb_audio *fd_usb_audio_open(const fd_usb_config *config){
    assert(config->operational && config->retain_runtime_traces && config->tx_timeout_seconds==180 && !config->rf_disabled);
    assert(!config->qualification_turns && !config->qualification_idle_ms);
    assert(!strcmp(config->headset_id,"loop110-stable"));assert(config->mic_gain_db==16 || config->mic_gain_db==7);
    ++open_count;observed=(fd_usb_snapshot){.state=FD_USB_RX,.startup_ready=true,.tx_eligible=true,.armed=true,
        .mic_gain_db=config->mic_gain_db,.headset_volume_percent=40,.headset_volume_ready=true};
    snprintf(observed.headset_id,sizeof(observed.headset_id),"%s",config->headset_id);return &fake_audio;
}
fd_usb_snapshot fd_usb_audio_snapshot(fd_usb_audio *a){assert(a==&fake_audio);return observed;}
bool fd_usb_audio_set_mic_gain(fd_usb_audio *a,int gain){assert(a==&fake_audio && gain>=-12 && gain<=24);++mic_changes;observed.mic_gain_db=gain;return true;}
bool fd_usb_audio_adjust_headset_volume(fd_usb_audio *a,int delta){assert(a==&fake_audio && !fd_writer_voice_authorized());assert(delta==-1 || delta==1);++headset_changes;observed.headset_volume_percent+=delta;return true;}
bool fd_usb_audio_set_tx_timeout(fd_usb_audio *a,unsigned seconds){assert(a==&fake_audio && seconds>=30 && seconds<=600);return true;}
void fd_usb_audio_stop(fd_usb_audio *a){assert(a==&fake_audio);++stop_count;observed.stopped=true;}
void fd_usb_audio_leave(fd_usb_audio *a){assert(a==&fake_audio);++leave_count;leave_pending=true;if(observed.state==FD_USB_RX)observed.stopped=true;}
void fd_usb_audio_close(fd_usb_audio *a){assert(a==&fake_audio && observed.stopped);++close_count;}
const char *fd_usb_fault_text(unsigned fault){(void)fault;return "injected fault";}
fd_usb_headset_scan *fd_usb_headsets_scan_open(void){fake_scan.cancelled=false;return &fake_scan;}
fd_usb_headset_catalog fd_usb_headsets_scan_snapshot(fd_usb_headset_scan *s){assert(s==&fake_scan);fd_usb_headset_catalog c={.count=s->cancelled?0:1};
    snprintf(c.devices[0].id,sizeof(c.devices[0].id),"loop110-stable");snprintf(c.devices[0].name,sizeof(c.devices[0].name),"Loop110 USB audio");return c;}
void fd_usb_headsets_scan_cancel(fd_usb_headset_scan *s){s->cancelled=true;}
bool fd_usb_headsets_scan_close(fd_usb_headset_scan *s){(void)s;return true;}
static void flush(lv_disp_drv_t *d,const lv_area_t *a,lv_color_t *p){(void)a;(void)p;lv_disp_flush_ready(d);}
static void pointer_read(lv_indev_drv_t *d,lv_indev_data_t *data){(void)d;data->point=pointer_position;data->state=pointer_state;}
static void pump(void){for(unsigned i=0;i<5;++i){lv_tick_inc(20);lv_timer_handler();struct timespec t={.tv_nsec=1000000};nanosleep(&t,NULL);}}
static void turn(int direction){struct input_event e={.type=EV_REL,.code=REL_Y,.value=direction};assert(write(vol->fd,&e,sizeof(e))==(ssize_t)sizeof(e));pump();}
static void mfk_turn(int direction){struct input_event e={.type=EV_REL,.code=REL_Y,.value=direction};assert(write(mfk->fd,&e,sizeof(e))==(ssize_t)sizeof(e));pump();}
static void touch(lv_obj_t *button){lv_obj_update_layout(button);lv_area_t a;lv_obj_get_coords(button,&a);pointer_position=(lv_point_t){(a.x1+a.x2)/2,(a.y1+a.y2)/2};pointer_state=LV_INDEV_STATE_PRESSED;pump();pointer_state=LV_INDEV_STATE_RELEASED;pump();}
static lv_obj_t *find_button(const char *text){for(unsigned i=0;i<lv_obj_get_child_cnt(popup);++i){lv_obj_t *item=lv_obj_get_child(popup,i);if(lv_obj_check_type(item,&lv_btn_class) && !strcmp(lv_label_get_text(lv_obj_get_child(item,0)),text))return item;}assert(0);return NULL;}
static void fits(lv_obj_t *object){lv_area_t a,b;lv_obj_get_coords(object,&a);lv_obj_get_coords(popup,&b);assert(a.x1>=b.x1 && a.y1>=b.y1 && a.x2<=b.x2 && a.y2<=b.y2);assert(b.y2<480-BUTTONS_H);}
static void wait_running(void){for(unsigned n=0;n<30 && !audio;++n)pump();assert(audio && stage==UI_RUNNING && observed.armed);}
static void wait_closed(void){for(unsigned n=0;n<30 && freedv_usb_active();++n)pump();assert(!freedv_usb_active() && !timer && !popup && !audio);}
int main(void){
    gui_thread=pthread_self();lv_init();usb_ui_cfg_init();
    lv_disp_draw_buf_t draw;lv_disp_draw_buf_init(&draw,draw_pixels,NULL,800*32);
    lv_disp_drv_t display;lv_disp_drv_init(&display);display.hor_res=800;display.ver_res=480;display.draw_buf=&draw;display.flush_cb=flush;assert(lv_disp_drv_register(&display));
    keyboard_group=lv_group_create();
    lv_style_init(&popup_style);lv_style_set_bg_color(&popup_style,lv_color_black());lv_style_set_bg_opa(&popup_style,LV_OPA_COVER);lv_style_set_text_color(&popup_style,lv_color_white());
    lv_style_init(&dialog_item_style);lv_style_init(&dialog_item_focus_style);lv_style_init(&dialog_item_edited_style);
    char path[]="/tmp/x6200-usb-ui-XXXXXX";int temp=mkstemp(path);assert(temp>=0);close(temp);assert(!unlink(path)&&!mkfifo(path,0600));
    vol=rotary_init(path);assert(vol);assert(!unlink(path));vol->left[VOL_STATE_EDIT]=KEY_VOL_LEFT_EDIT;vol->right[VOL_STATE_EDIT]=KEY_VOL_RIGHT_EDIT;
    vol->left[VOL_STATE_SELECT]=KEY_VOL_LEFT_SELECT;vol->right[VOL_STATE_SELECT]=KEY_VOL_RIGHT_SELECT;vol->state=VOL_STATE_EDIT;
    char encoder_path[]="/tmp/x6200-usb-mfk-XXXXXX";temp=mkstemp(encoder_path);assert(temp>=0);close(temp);assert(!unlink(encoder_path)&&!mkfifo(encoder_path,0600));
    mfk=encoder_init(encoder_path);assert(mfk && !unlink(encoder_path));
    lv_indev_drv_t pointer;lv_indev_drv_init(&pointer);pointer.type=LV_INDEV_TYPE_POINTER;pointer.read_cb=pointer_read;assert(lv_indev_drv_register(&pointer));
    lv_obj_t *normal_focus=lv_btn_create(lv_scr_act());lv_group_add_obj(keyboard_group,normal_focus);lv_group_focus_obj(normal_focus);
    freedv_usb_open();pump();assert(freedv_usb_entry_pending() && !claim_count && !open_count && !atomic_load(&quiet_calls));
    fits(popup_message);fits(usage_check);
    lv_area_t text_area,check_area;lv_obj_get_coords(popup_message,&text_area);lv_obj_get_coords(usage_check,&check_area);
    assert(text_area.y2<check_area.y1);
    assert(freedv_usb_ptt(true) && !claim_count);assert(!lv_obj_has_state(usage_check,LV_STATE_CHECKED));
    touch(usage_check);assert(lv_obj_has_state(usage_check,LV_STATE_CHECKED));touch(find_button("Cancel"));wait_closed();assert(!cfg_freedv_usage_hidden() && !claim_count);
    freedv_usb_open();pump();assert(freedv_usb_entry_pending() && !lv_obj_has_state(usage_check,LV_STATE_CHECKED));
    touch(find_button("Continue"));wait_running();usb_ui_cfg_verify_hidden(false);
    assert(open_count==1 && selected_gain==16 && shown_page->items[3]==&btn_tx_pwr && lv_group_get_focused(keyboard_group)==normal_focus);
    assert(!fd_writer_enter());param_i_set(cfg_volume,99);assert(param_i_get(cfg_volume)==27);
    popup_open(POP_MIC);pump();assert(vol->indev->group==popup_group);
    lv_group_set_editing(popup_group,true);mfk_turn(-1);assert(observed.mic_gain_db==17 && mic_changes==1);
    lv_group_set_editing(popup_group,false);
    uint32_t injected=KEY_VOL_RIGHT_EDIT;lv_event_send(lv_group_get_focused(popup_group),LV_EVENT_KEY,&injected);assert(!headset_changes);
    turn(-1);turn(1);vol->state=VOL_STATE_SELECT;turn(-1);turn(1);assert(headset_changes==4 && observed.headset_volume_percent==40 && param_i_get(cfg_volume)==27);
    for(unsigned i=0;i<30;++i)touch(find_button("+"));
    assert(observed.mic_gain_db==24);
    for(unsigned i=0;i<40;++i)touch(find_button("-"));
    assert(observed.mic_gain_db==-12);
    for(unsigned i=0;i<19;++i)touch(find_button("+"));
    assert(observed.mic_gain_db==7);
    observed.mic_meter_ready=true;observed.mic_rms_dbfs10=-241;observed.mic_peak_dbfs10=-68;observed.mic_clipped_samples=3;pump();
    assert(strstr(lv_label_get_text(mic_level_label),"-24.1") && strstr(lv_label_get_text(mic_level_label),"-6.8"));assert(strstr(lv_label_get_text(clip_label),"CLIP"));
    fits(mic_gain_spin);fits(mic_level_label);fits(mic_rms_bar);fits(mic_peak_bar);fits(clip_label);fits(headset_label);
    touch(find_button("Close"));assert(vol->indev->group==keyboard_group && open_count==1 && !close_count && !leave_count);
    popup_open(POP_MODE);pump();assert(lv_dropdown_get_option_cnt(lv_obj_get_child(popup,2))==1);fits(timeout_spin);touch(find_button("Help"));assert(strstr(lv_label_get_text(popup_message),"HOST USB"));touch(find_button("Close"));
    observed.tx_eligible=false;pump();assert(strstr(lv_label_get_text(status_label),"RX only"));observed.tx_eligible=true;
    popup_open(POP_AUDIO);pump();fits(audio_list);assert(lv_dropdown_get_option_cnt(audio_list)==1);touch(find_button("Close"));assert(open_count==1 && !close_count);
    observed.state=FD_USB_TX;observed.stopped=false;assert(freedv_usb_leave());pump();assert(leave_pending && freedv_usb_active() && !stop_count && !close_count);
    observed.state=FD_USB_RX;observed.stopped=true;wait_closed();assert(close_count==1 && completion_count==2 && atomic_load(&quiet_calls)==2);
    freedv_usb_open();pump();assert(freedv_usb_entry_pending());touch(usage_check);touch(find_button("Continue"));wait_running();usb_ui_cfg_verify_hidden(true);
    assert(selected_gain==7);freedv_usb_leave();pump();wait_closed();freedv_usb_open();wait_running();assert(!freedv_usb_entry_pending());
    freedv_usb_terminal_leave();wait_closed();assert(stop_count==1 && close_count==3);
    /* Retry during a delayed normal-audio acknowledgement must not restore,
     * release ownership or open another session over that pending worker. */
    atomic_store(&quiet_block,true);freedv_usb_open();
    for(unsigned n=0;n<20 && stage!=UI_QUIET;++n)pump();
    assert(stage==UI_QUIET && quiet_job.running && open_count==3);
    unsigned before_restore=restore_count;request_restart();pump();
    assert(stage==UI_QUIET && adapter_owned && restore_count==before_restore && open_count==3);
    atomic_store(&quiet_block,false);wait_running();assert(open_count==4);
    freedv_usb_leave();wait_closed();assert(close_count==4);
    usb_ui_cfg_verify_profiles();usb_ui_cfg_close();close(vol->fd);close(mfk->fd);
    puts("PASS: native-screen FreeDV notice precedes profile/audio; cancellation and durable skip; paired-device gain migration; local VOL/mic/meters; warm popups; RX-only; graceful and terminal teardown");
    return 0;
}
