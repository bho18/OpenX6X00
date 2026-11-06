// SPDX-License-Identifier: LGPL-2.1-or-later
/* Real LVGL + production USB dialog, input driver/fence and settings API.
 * Only the unavailable audio/radio hardware and unrelated input devices are
 * replaced. No codec, PulseAudio, I2C, PTT or RF operation is performed. */
#define _POSIX_C_SOURCE 200809L
#include "src/freedv/usb_mode.c"
#include "src/front_keys.c"
#include "src/knob_device.c"
#include <assert.h>
#include <sys/stat.h>

void usb_ui_cfg_init(void);
void usb_ui_cfg_verify_saved(int expected);
lv_group_t *keyboard_group;
encoder_t *mfk;
rotary_t *vol;
lv_style_t dialog_style,dialog_item_style,dialog_item_focus_style,dialog_item_edited_style;
static fd_usb_snapshot observed;
static unsigned headset_changes,mic_changes,radio_operations;
static lv_point_t pointer_position;
static lv_indev_state_t pointer_state;
struct fd_usb_audio { unsigned placeholder; } fake_audio;
static lv_color_t draw_pixels[800*32];

uint32_t theme_hex(theme_color_t c){
    if(c==TC_track)return 0x303030;
    if(c==TC_err)return 0xff4040;
    if(c==TC_ok_bar)return 0x40c060;
    if(c==TC_warn)return 0xffc040;
    if(c==TC_text_dim)return 0xaaaaaa;
    return 0xffffff;
}
bool backlight_tick(void){return false;}
radio_state_t radio_get_state(void){return RADIO_RX;}
bool encoder_diagnostic_reset(void){return true;}
bool encoder_diagnostic_local_event(lv_indev_t *input,unsigned generation){(void)input;(void)generation;return false;}
bool keyboard_diagnostic_reset(void){return true;}
bool keyboard_voice_local_event(lv_indev_t *input,unsigned generation){(void)input;(void)generation;return false;}
fd_usb_snapshot fd_usb_audio_snapshot(fd_usb_audio *a){assert(a==&fake_audio);return observed;}
bool fd_usb_audio_set_mic_gain(fd_usb_audio *a,int gain){
    assert(a==&fake_audio && !fd_writer_voice_authorized());
    assert(gain>=FD_USB_MIC_GAIN_MIN_DB && gain<=FD_USB_MIC_GAIN_MAX_DB);
    ++mic_changes;observed.mic_gain_db=gain;return true;
}
bool fd_usb_audio_adjust_headset_volume(fd_usb_audio *a,int delta){
    assert(a==&fake_audio && fd_writer_voice_owned() && !fd_writer_voice_authorized());
    assert(delta==-1 || delta==1);++headset_changes;
    observed.headset_volume_percent=(unsigned)((int)observed.headset_volume_percent+delta);return true;
}
void fd_usb_audio_stop(fd_usb_audio *a){(void)a;assert(0);}
void fd_usb_audio_arm(fd_usb_audio *a,bool armed){(void)a;(void)armed;assert(0);}
bool fd_usb_audio_start_qualification(fd_usb_audio *a){(void)a;assert(0);return false;}
bool fd_usb_audio_enable_rf(fd_usb_audio *a){(void)a;assert(0);return false;}
bool radio_usb_observe(void *unused,fd_usb_inputs *in){(void)unused;++radio_operations;*in=(fd_usb_inputs){.valid=true,.rf_valid=true};return true;}
bool radio_usb_allow_tx(bool enable){(void)enable;assert(0);return false;}
void radio_usb_inhibit(void *unused){(void)unused;assert(0);}

static void flush(lv_disp_drv_t *driver,const lv_area_t *area,lv_color_t *pixels){(void)area;(void)pixels;lv_disp_flush_ready(driver);}
static void pointer_read(lv_indev_drv_t *driver,lv_indev_data_t *data){(void)driver;data->point=pointer_position;data->state=pointer_state;}
static void pump(void){for(unsigned i=0;i<5;++i){lv_tick_inc(20);lv_timer_handler();}}
static void turn(int direction){
    struct input_event e={.type=EV_REL,.code=REL_Y,.value=direction};
    assert(write(vol->fd,&e,sizeof(e))==(ssize_t)sizeof(e));pump();
}
static void touch(lv_obj_t *button){
    lv_obj_update_layout(button);lv_area_t a;lv_obj_get_coords(button,&a);
    pointer_position=(lv_point_t){(a.x1+a.x2)/2,(a.y1+a.y2)/2};
    pointer_state=LV_INDEV_STATE_PRESSED;pump();pointer_state=LV_INDEV_STATE_RELEASED;pump();
}
static void fits(lv_obj_t *object){
    lv_area_t a,b;lv_obj_get_coords(object,&a);lv_obj_get_coords(view.obj,&b);
    assert(a.x1>=b.x1 && a.y1>=b.y1 && a.x2<=b.x2 && a.y2<=b.y2);
}
int main(void){
    lv_init();usb_ui_cfg_init();
    lv_disp_draw_buf_t draw;lv_disp_draw_buf_init(&draw,draw_pixels,NULL,800*32);
    lv_disp_drv_t display;lv_disp_drv_init(&display);display.hor_res=800;display.ver_res=480;display.draw_buf=&draw;display.flush_cb=flush;assert(lv_disp_drv_register(&display));
    keyboard_group=lv_group_create();
    lv_style_init(&dialog_style);lv_style_set_bg_color(&dialog_style,lv_color_black());lv_style_set_bg_opa(&dialog_style,LV_OPA_COVER);lv_style_set_text_color(&dialog_style,lv_color_white());
    lv_style_init(&dialog_item_style);lv_style_init(&dialog_item_focus_style);lv_style_init(&dialog_item_edited_style);
    char path[]="/tmp/x6200-usb-ui-XXXXXX";int temporary=mkstemp(path);assert(temporary>=0);close(temporary);assert(!unlink(path)&&!mkfifo(path,0600));
    vol=rotary_init(path);assert(vol);assert(!unlink(path));
    vol->left[VOL_STATE_EDIT]=KEY_VOL_LEFT_EDIT;vol->right[VOL_STATE_EDIT]=KEY_VOL_RIGHT_EDIT;
    vol->left[VOL_STATE_SELECT]=KEY_VOL_LEFT_SELECT;vol->right[VOL_STATE_SELECT]=KEY_VOL_RIGHT_SELECT;
    vol->state=VOL_STATE_EDIT;
    lv_indev_drv_t pointer;lv_indev_drv_init(&pointer);pointer.type=LV_INDEV_TYPE_POINTER;pointer.read_cb=pointer_read;assert(lv_indev_drv_register(&pointer));
    /* A turn queued before mode ownership must be drained, not replayed. */
    struct input_event queued={.type=EV_REL,.value=-1};assert(write(vol->fd,&queued,sizeof(queued))==(ssize_t)sizeof(queued));
    assert(front_keys_diagnostic_input_acquire() && fd_writer_voice_reserve());
    observed=(fd_usb_snapshot){.state=FD_USB_RX,.mic_gain_db=0,.headset_volume_percent=40,.headset_volume_ready=true};audio=&fake_audio;
    construct(lv_scr_act());pump();assert(!headset_changes && !mic_changes);
    assert(!fd_writer_enter());param_i_set(cfg_volume,99);assert(param_i_get(cfg_volume)==27);
    uint32_t injected=KEY_VOL_RIGHT_EDIT;lv_event_send(lv_group_get_focused(keyboard_group),LV_EVENT_KEY,&injected);assert(!headset_changes);
    lv_event_send(mic_up_button,LV_EVENT_CLICKED,NULL);assert(!mic_changes && param_i_get(cfg_freedv_usb_mic_gain)==0);
    turn(-1);assert(headset_changes==1 && observed.headset_volume_percent==41);
    turn(1);assert(headset_changes==2 && observed.headset_volume_percent==40);
    vol->state=VOL_STATE_SELECT;turn(-1);turn(1);assert(headset_changes==4 && observed.headset_volume_percent==40 && vol->state==VOL_STATE_SELECT);
    assert(param_i_get(cfg_volume)==27 && !radio_operations);
    touch(mic_up_button);assert(observed.mic_gain_db==1 && param_i_get(cfg_freedv_usb_mic_gain)==1);
    for(unsigned i=0;i<40;++i)touch(mic_up_button);
    assert(observed.mic_gain_db==24 && param_i_get(cfg_freedv_usb_mic_gain)==24);
    for(unsigned i=0;i<50;++i)touch(mic_down_button);
    assert(observed.mic_gain_db==-12 && param_i_get(cfg_freedv_usb_mic_gain)==-12);
    for(unsigned i=0;i<19;++i)touch(mic_up_button);
    assert(observed.mic_gain_db==7 && param_i_get(cfg_freedv_usb_mic_gain)==7);
    observed.mic_meter_ready=true;observed.mic_rms_dbfs10=-241;observed.mic_peak_dbfs10=-68;observed.mic_clipped_samples=3;
    audio_controls(&observed);lv_obj_update_layout(view.obj);
    assert(strstr(lv_label_get_text(mic_level_label),"-24.1") && strstr(lv_label_get_text(mic_level_label),"-6.8"));
    assert(!strcmp(lv_label_get_text(clip_label),"CLIP: lower mic gain"));
    assert(lv_bar_get_value(mic_rms_bar)==-241 && lv_bar_get_value(mic_peak_bar)==-68);
    observed.headset_muted=true;audio_controls(&observed);assert(strstr(lv_label_get_text(headset_label),"40% (muted)"));
    fits(status_label);fits(health_label);fits(mic_gain_label);fits(mic_level_label);fits(mic_rms_bar);fits(mic_peak_bar);fits(headset_label);fits(clip_label);fits(notice_label);fits(check_button);fits(tx_button);
    lv_obj_del(view.obj);destruct();audio=NULL;
    front_keys_diagnostic_input_release();assert(!ctl_diagnostic_input_blocked() && vol->state==VOL_STATE_SELECT);
    fd_writer_release();assert(fd_writer_enter());fd_writer_exit();
    usb_ui_cfg_verify_saved(7);
    puts("PASS: real LVGL VOL events require fresh local input, route only USB while writer-owned, preserve radio volume; mic UI clamps and persists GLOBAL gain; meter/labels fit");
    close(vol->fd);return 0;
}
