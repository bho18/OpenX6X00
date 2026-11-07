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
#include <math.h>
#include <png.h>
void usb_ui_cfg_init(void);
void usb_ui_cfg_verify_hidden(bool);
void usb_ui_cfg_verify_profiles(void);
void usb_ui_cfg_close(void);
lv_group_t *keyboard_group;
encoder_t *mfk;
rotary_t *vol;
params_t params;
uint32_t EVENT_KEYPAD;
static keypad_t *panel;
static bool panel_down[KEY_MAX+1];
static rotary_t *inner;
static bool row_reserved;
static unsigned volume_edits,volume_selects,volume_presses,mic_retries,display_notes;
static unsigned native_power_actions;
static cfg_ctrl_t selected_volume_control=CTRL_VOL;
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
static lv_color_t rendered_pixels[800*480];
bool backlight_tick(void){return false;}
static backlight_observer_fn display_callback;
void backlight_set_observer(backlight_observer_fn fn,void *context){(void)context;display_callback=fn;}
void backlight_snapshot(fd_usb_display_event *event){*event=(fd_usb_display_event){.drawing_enabled=true};}
void fd_usb_audio_note_display(fd_usb_audio *a,const fd_usb_display_event *e){assert(a==&fake_audio && e);++display_notes;}
void main_screen_freedv_row(bool enabled){row_reserved=enabled;}
bool main_screen_freedv_bounds(lv_area_t *bounds){if(!row_reserved)return false;int h=sony_18.line_height+8;if(h<28)h=28;*bounds=(lv_area_t){0,TOP_BAR_HEIGHT+FREQ_STRIP_H+METER_ROW_H,799,TOP_BAR_HEIGHT+FREQ_STRIP_H+METER_ROW_H+h-1};return true;}
void knobs_usb_volume_update(bool active,bool ready,unsigned value,bool muted,bool pending){(void)active;(void)ready;(void)value;(void)muted;(void)pending;}
void vol_update(int16_t delta){++volume_edits;if(selected_volume_control==CTRL_VOL)freedv_usb_volume(delta);}
void vol_change_ctrl(int16_t delta){++volume_selects;selected_volume_control=delta>0?CTRL_PWR:CTRL_VOL;}
void main_screen_vol_press(void){++volume_presses;vol->state=vol->state==VOL_STATE_EDIT?VOL_STATE_SELECT:VOL_STATE_EDIT;}
bool freedv_mode_radio_ptt(bool pressed){return freedv_usb_ptt(pressed);}

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
    assert(config->operational && config->refinement && config->retain_runtime_traces && config->tx_timeout_seconds>=30 && !config->rf_disabled);
    assert(!config->qualification_turns && !config->qualification_idle_ms);
    assert(!strcmp(config->headset_id,"loop110-stable"));assert(config->mic_gain_db==16 || config->mic_gain_db==7);
    ++open_count;observed=(fd_usb_snapshot){.state=FD_USB_RX,.startup_ready=true,.tx_eligible=true,.armed=true,
        .mic_gain_db=config->mic_gain_db,.headset_volume_percent=40,.headset_volume_ready=true,.headset_volume_initialized=true,.headset_volume_requested_revision=1,.headset_volume_confirmed_revision=1,.mic_health=FD_USB_MIC_HEALTHY};
    snprintf(observed.headset_id,sizeof(observed.headset_id),"%s",config->headset_id);return &fake_audio;
}
fd_usb_snapshot fd_usb_audio_snapshot(fd_usb_audio *a){assert(a==&fake_audio);return observed;}
bool fd_usb_audio_set_mic_gain(fd_usb_audio *a,int gain){assert(a==&fake_audio && gain>=-12 && gain<=24);++mic_changes;observed.mic_gain_db=gain;return true;}
bool fd_usb_audio_adjust_headset_volume(fd_usb_audio *a,int delta){assert(a==&fake_audio && !fd_writer_voice_authorized());assert(delta==-1 || delta==1);++headset_changes;observed.headset_volume_percent+=delta;return true;}
bool fd_usb_audio_set_tx_timeout(fd_usb_audio *a,unsigned seconds){assert(a==&fake_audio && seconds>=30 && seconds<=600);return observed.state==FD_USB_RX;}
bool fd_usb_audio_set_cues_enabled(fd_usb_audio *a,bool enabled){assert(a==&fake_audio);(void)enabled;return true;}
bool fd_usb_audio_retry_microphone(fd_usb_audio *a){assert(a==&fake_audio);++mic_retries;return observed.state==FD_USB_RX && !observed.pressed;}

void fd_usb_audio_stop(fd_usb_audio *a){assert(a==&fake_audio);++stop_count;observed.stopped=true;}
void fd_usb_audio_leave(fd_usb_audio *a){assert(a==&fake_audio);++leave_count;leave_pending=true;if(observed.state==FD_USB_RX)observed.stopped=true;}
void fd_usb_audio_close(fd_usb_audio *a){assert(a==&fake_audio && observed.stopped);++close_count;}
const char *fd_usb_fault_text(unsigned fault){(void)fault;return "injected fault";}
fd_usb_headset_scan *fd_usb_headsets_scan_open(void){fake_scan.cancelled=false;return &fake_scan;}
fd_usb_headset_catalog fd_usb_headsets_scan_snapshot(fd_usb_headset_scan *s){assert(s==&fake_scan);fd_usb_headset_catalog c={.count=s->cancelled?0:1};
    snprintf(c.devices[0].id,sizeof(c.devices[0].id),"loop110-stable");snprintf(c.devices[0].name,sizeof(c.devices[0].name),"Loop110 USB audio");return c;}
void fd_usb_headsets_scan_cancel(fd_usb_headset_scan *s){s->cancelled=true;}
bool fd_usb_headsets_scan_close(fd_usb_headset_scan *s){(void)s;return true;}
static void flush(lv_disp_drv_t *d,const lv_area_t *a,lv_color_t *p){
    int width=a->x2-a->x1+1;
    for(int y=a->y1;y<=a->y2;++y)for(int x=a->x1;x<=a->x2;++x)rendered_pixels[y*800+x]=p[(y-a->y1)*width+x-a->x1];
    lv_disp_flush_ready(d);
}
static void screenshot(const char *theme,const char *state){
    const char *directory=getenv("FD_UI_SCREENSHOT_DIR");if(!directory)return;
    assert(!mkdir(directory,0700) || errno==EEXIST);lv_refr_now(NULL);
    unsigned char *rgb=malloc(800*480*3);assert(rgb);
    for(unsigned i=0;i<800*480;++i){uint32_t color=lv_color_to32(rendered_pixels[i]);rgb[i*3]=(color>>16)&255;rgb[i*3+1]=(color>>8)&255;rgb[i*3+2]=color&255;}
    char path[768];snprintf(path,sizeof(path),"%s/%s-%s.png",directory,theme,state);
    png_image image={.version=PNG_IMAGE_VERSION,.width=800,.height=480,.format=PNG_FORMAT_RGB};
    assert(png_image_write_to_file(&image,path,0,rgb,0,NULL));png_image_free(&image);free(rgb);
}
static void pointer_read(lv_indev_drv_t *d,lv_indev_data_t *data){(void)d;data->point=pointer_position;data->state=pointer_state;}
static void pump(void){for(unsigned i=0;i<5;++i){lv_tick_inc(20);lv_timer_handler();struct timespec t={.tv_nsec=1000000};nanosleep(&t,NULL);}}
static void turn(int direction){struct input_event e={.type=EV_REL,.code=REL_Y,.value=direction};assert(write(vol->fd,&e,sizeof(e))==(ssize_t)sizeof(e));pump();}
bool front_keys_test_held(int fd,uint16_t code){return panel && fd==panel->fd && code<=KEY_MAX && panel_down[code];}
static void panel_key(uint16_t code,int down){assert(code<=KEY_MAX);panel_down[code]=down!=0;struct input_event e={.type=EV_KEY,.code=code,.value=down};assert(write(panel->fd,&e,sizeof(e))==(ssize_t)sizeof(e));pump();}
static void mfk_click(void){panel_key(BTN_TRIGGER_HAPPY27,1);panel_key(BTN_TRIGGER_HAPPY27,0);}
static void inner_turn(int direction){struct input_event e={.type=EV_REL,.value=direction};assert(write(inner->fd,&e,sizeof(e))==(ssize_t)sizeof(e));pump();}
static void panel_event(lv_event_t *event){
    event_keypad_t *e=lv_event_get_param(event);
    if(e->key>=KEYPAD_F1 && e->key<=KEYPAD_F5 && (e->state==KEYPAD_LONG || e->state==KEYPAD_RELEASE)){
        bool consumed=freedv_usb_softkey(e->key-KEYPAD_F1,e->state==KEYPAD_LONG);
        if(!consumed && e->key==KEYPAD_F4){++native_power_actions;selected_volume_control=CTRL_PWR;}
    }
}
static void mfk_turn(int direction){struct input_event e={.type=EV_REL,.code=REL_Y,.value=direction};assert(write(mfk->fd,&e,sizeof(e))==(ssize_t)sizeof(e));pump();}
static void touch(lv_obj_t *button){lv_obj_update_layout(button);lv_area_t a;lv_obj_get_coords(button,&a);pointer_position=(lv_point_t){(a.x1+a.x2)/2,(a.y1+a.y2)/2};pointer_state=LV_INDEV_STATE_PRESSED;pump();pointer_state=LV_INDEV_STATE_RELEASED;pump();}
static lv_obj_t *find_button(const char *text){for(unsigned i=0;i<lv_obj_get_child_cnt(popup);++i){lv_obj_t *item=lv_obj_get_child(popup,i);if(lv_obj_check_type(item,&lv_btn_class) && !strcmp(lv_label_get_text(lv_obj_get_child(item,0)),text))return item;}assert(0);return NULL;}
static void fits(lv_obj_t *object){lv_area_t a,b;lv_obj_get_coords(object,&a);lv_obj_get_coords(popup,&b);assert(a.x1>=b.x1 && a.y1>=b.y1 && a.x2<=b.x2 && a.y2<=b.y2);assert(b.y2<480-BUTTONS_H);}
static void wait_running(void){for(unsigned n=0;n<30 && !audio;++n)pump();assert(audio && stage==UI_RUNNING && observed.armed);}
static void wait_closed(void){for(unsigned n=0;n<30 && freedv_usb_active();++n)pump();assert(!freedv_usb_active() && !timer && !popup && !audio);}
static double luminance(uint32_t rgb){
    double values[3];for(unsigned i=0;i<3;++i){double c=((rgb>>(16-i*8))&255)/255.;values[i]=c<=.04045?c/12.92:pow((c+.055)/1.055,2.4);}
    return .2126*values[0]+.7152*values[1]+.0722*values[2];
}
static double contrast(theme_color_t foreground,theme_color_t background){
    double a=luminance(theme_hex(foreground)),b=luminance(theme_hex(background));return (fmax(a,b)+.05)/(fmin(a,b)+.05);
}
static void mono_selected_pixels(void){
    lv_obj_t *button=find_button("Continue"),*text=lv_obj_get_child(button,0);
    lv_refr_now(NULL);lv_area_t box,glyphs;lv_obj_get_coords(button,&box);lv_obj_get_coords(text,&glyphs);
    uint32_t ink=theme_shape.ink,paper=theme_shape.paper;unsigned letters=0,ground=0;
    for(int x=box.x1+8;x<=box.x2-8;++x)assert((lv_color_to32(rendered_pixels[(box.y1+5)*800+x])&0xffffff)==ink);
    for(int y=glyphs.y1;y<=glyphs.y2;++y)for(int x=glyphs.x1;x<=glyphs.x2;++x){uint32_t color=lv_color_to32(rendered_pixels[y*800+x])&0xffffff;letters+=color==paper;ground+=color==ink;}
    assert(letters>8 && ground>8);
}
static void presentation_themes(void){
    static const char *names[]={"RF.Guru","Legacy","Classic","Outdoor","Outdoor-Negative","VISION","Black Metal","Total Black","1bit"};
    static const char *files[]={"rfguru","legacy","classic","outdoor","negative","vision","black-metal","total-black","mono"};
    for(unsigned i=0;i<sizeof(names)/sizeof(names[0]);++i){
        popup_close();styles_load(names[i]);lv_obj_report_style_change(NULL);popup_open(POP_NOTICE);pump();
        assert(lv_color_to32(lv_obj_get_style_text_color(popup_message,0))==lv_color_to32(TC(text)));
        assert(contrast(TC_text,TC_popup)>=4.5 && contrast(TC_sel_text,TC_sel_bg)>=4.5);
        fits(lv_obj_get_parent(popup_message));fits(usage_check);fits(find_button("Continue"));fits(find_button("Cancel"));
        lv_area_t body,check;lv_obj_get_coords(lv_obj_get_parent(popup_message),&body);lv_obj_get_coords(usage_check,&check);assert(body.y2<check.y1);
        assert(!claim_count && !open_count);
        if(theme_shape.mono)mono_selected_pixels();
        screenshot(files[i],"ack");popup_open(POP_MIC);pump();screenshot(files[i],"mic");
        number_step(1);screenshot(files[i],"edit");popup_open(POP_NOTICE);pump();
    }
    assert(!mkdir(BOARD_DATA,0700) || errno==EEXIST);
    char directory[512],path[560];snprintf(directory,sizeof(directory),"%s/themes",BOARD_DATA);assert(!mkdir(directory,0700) || errno==EEXIST);
    snprintf(path,sizeof(path),"%s/FreeDV-UI-test.json",directory);FILE *file=fopen(path,"w");assert(file);
    assert(fputs("{\"base\":\"Classic\",\"colors\":{\"popup\":\"#fff0c0\",\"text\":\"#003300\",\"sel_bg\":\"#000080\",\"sel_text\":\"#ffff00\",\"edited\":\"#ff00ff\"},\"radius_popup\":13,\"border\":3,\"font\":\"dejavu\"}",file)>=0);assert(!fclose(file));
    popup_close();styles_load("FreeDV-UI-test");lv_obj_report_style_change(NULL);popup_open(POP_NOTICE);pump();
    assert(theme_hex(TC_text)==0x003300 && theme_hex(TC_popup)==0xfff0c0);
    assert(lv_obj_get_style_radius(popup,0)==13);
    assert(lv_color_to32(lv_obj_get_style_text_color(popup_message,0))==lv_color_to32(TC(text)));
    assert(lv_color_to32(lv_obj_get_style_text_color(find_button("Continue"),0))==lv_color_to32(TC(sel_text)));
    fits(usage_check);screenshot("custom","ack");popup_open(POP_MIC);pump();screenshot("custom","mic");number_step(1);screenshot("custom","edit");assert(!unlink(path));
    file=fopen(path,"w");assert(file);assert(fputs("{\"base\":\"1bit\",\"ink\":\"#005500\",\"paper\":\"#ffeecc\"}",file)>=0);assert(!fclose(file));
    popup_close();styles_load("FreeDV-UI-test");lv_obj_report_style_change(NULL);popup_open(POP_NOTICE);pump();mono_selected_pixels();screenshot("custom-mono","ack");assert(!unlink(path));
    popup_close();styles_load("Legacy");lv_obj_report_style_change(NULL);popup_open(POP_NOTICE);pump();
}
int main(void){
    gui_thread=pthread_self();lv_init();usb_ui_cfg_init();
    lv_disp_draw_buf_t draw;lv_disp_draw_buf_init(&draw,draw_pixels,NULL,800*32);
    lv_disp_drv_t display;lv_disp_drv_init(&display);display.hor_res=800;display.ver_res=480;display.draw_buf=&draw;display.flush_cb=flush;assert(lv_disp_drv_register(&display));
    keyboard_group=lv_group_create();
    styles_load("Legacy");lv_obj_add_style(lv_scr_act(),&background_style,0);
    EVENT_KEYPAD=lv_event_register_id();lv_obj_add_event_cb(lv_scr_act(),panel_event,EVENT_KEYPAD,NULL);
    char path[]="/tmp/x6200-usb-ui-XXXXXX";int temp=mkstemp(path);assert(temp>=0);close(temp);assert(!unlink(path)&&!mkfifo(path,0600));
    vol=rotary_init(path);assert(vol);assert(!unlink(path));vol->left[VOL_STATE_EDIT]=KEY_VOL_LEFT_EDIT;vol->right[VOL_STATE_EDIT]=KEY_VOL_RIGHT_EDIT;
    vol->left[VOL_STATE_SELECT]=KEY_VOL_LEFT_SELECT;vol->right[VOL_STATE_SELECT]=KEY_VOL_RIGHT_SELECT;vol->state=VOL_STATE_EDIT;
    char encoder_path[]="/tmp/x6200-usb-mfk-XXXXXX";temp=mkstemp(encoder_path);assert(temp>=0);close(temp);assert(!unlink(encoder_path)&&!mkfifo(encoder_path,0600));
    mfk=encoder_init(encoder_path);assert(mfk && !unlink(encoder_path));
    char panel_path[]="/tmp/x6200-usb-panel-XXXXXX";temp=mkstemp(panel_path);assert(temp>=0);close(temp);assert(!unlink(panel_path)&&!mkfifo(panel_path,0600));
    panel=keypad_init(panel_path);assert(panel && !unlink(panel_path));
    char inner_path[]="/tmp/x6200-usb-inner-XXXXXX";temp=mkstemp(inner_path);assert(temp>=0);close(temp);assert(!unlink(inner_path)&&!mkfifo(inner_path,0600));
    inner=rotary_init(inner_path);assert(inner && !unlink(inner_path));for(unsigned i=0;i<3;++i){inner->left[i]=KEY_MFK_PREV;inner->right[i]=KEY_MFK_NEXT;}
    lv_indev_drv_t pointer;lv_indev_drv_init(&pointer);pointer.type=LV_INDEV_TYPE_POINTER;pointer.read_cb=pointer_read;assert(lv_indev_drv_register(&pointer));
    lv_obj_t *normal_focus=lv_btn_create(lv_scr_act());lv_group_add_obj(keyboard_group,normal_focus);lv_group_focus_obj(normal_focus);
    freedv_usb_open();pump();assert(freedv_usb_entry_pending() && !claim_count && !open_count && !atomic_load(&quiet_calls));
    presentation_themes();
    fits(popup_message);fits(usage_check);
    lv_area_t text_area,check_area;lv_obj_get_coords(popup_message,&text_area);lv_obj_get_coords(usage_check,&check_area);
    assert(text_area.y2<check_area.y1);
    assert(freedv_usb_ptt(true) && !claim_count);assert(!lv_obj_has_state(usage_check,LV_STATE_CHECKED));
    touch(usage_check);assert(lv_obj_has_state(usage_check,LV_STATE_CHECKED));touch(find_button("Cancel"));wait_closed();assert(!cfg_freedv_usage_hidden() && !claim_count);
    freedv_usb_open();pump();assert(freedv_usb_entry_pending() && !lv_obj_has_state(usage_check,LV_STATE_CHECKED));
    touch(find_button("Continue"));wait_running();usb_ui_cfg_verify_hidden(false);
    assert(open_count==1 && selected_gain==16 && shown_page->items[3]==&btn_tx_pwr && lv_group_get_focused(keyboard_group)==normal_focus);
    assert(!fd_writer_enter());param_i_set(cfg_volume,99);assert(param_i_get(cfg_volume)==27);
    popup_open(POP_MIC);pump();assert(vol->indev->group==popup_group && !number.editing);
    assert(lv_color_to32(lv_obj_get_style_text_color(popup,0))==lv_color_to32(TC(text)));
    assert(lv_color_to32(lv_obj_get_style_text_color(number.row,0))==lv_color_to32(TC(sel_text)));
    mfk_click();assert(number.editing);mfk_turn(-1);inner_turn(-1);
    assert(number.draft==18 && observed.mic_gain_db==16 && mic_changes==0);
    panel_key(BTN_TRIGGER_HAPPY14,1);panel_key(BTN_TRIGGER_HAPPY14,0);assert(number.editing && number.draft==18); /* no softkey reset */
    mfk_click();assert(!number.editing && observed.mic_gain_db==18 && mic_changes==1);
    mfk_click();inner_turn(-1);assert(number.draft==19 && observed.mic_gain_db==18);
    touch(find_button("Close"));assert(popup && !number.editing && observed.mic_gain_db==18);touch(find_button("Close"));assert(!popup);
    popup_open(POP_MIC);pump();
    uint32_t injected=KEY_VOL_RIGHT_EDIT;lv_event_send(lv_group_get_focused(popup_group),LV_EVENT_KEY,&injected);assert(!headset_changes);
    turn(-1);turn(1);assert(volume_edits==2 && headset_changes==2 && observed.headset_volume_percent==40);
    vol->state=VOL_STATE_SELECT;turn(-1);turn(1);assert(volume_selects==2 && headset_changes==2);
    panel_key(BTN_TRIGGER_HAPPY21,1);panel_key(BTN_TRIGGER_HAPPY21,0);assert(volume_presses==1 && vol->state==VOL_STATE_EDIT);
    assert(param_i_get(cfg_volume)==27);
    for(unsigned i=0;i<30;++i)touch(find_button("+"));
    assert(number.draft==24 && observed.mic_gain_db==18);
    touch(number.row);assert(observed.mic_gain_db==24 && !number.editing);
    for(unsigned i=0;i<50;++i)touch(find_button("-"));
    assert(number.draft==-12 && observed.mic_gain_db==24);
    touch(number.row);assert(observed.mic_gain_db==-12);
    for(unsigned i=0;i<19;++i)touch(find_button("+"));
    touch(number.row);assert(observed.mic_gain_db==7);
    observed.mic_meter_ready=true;observed.mic_rms_dbfs10=-241;observed.mic_peak_dbfs10=-68;observed.mic_clipped_samples=3;pump();
    assert(strstr(lv_label_get_text(mic_level_label),"-24.1") && strstr(lv_label_get_text(mic_level_label),"-6.8"));assert(strstr(lv_label_get_text(clip_label),"CLIP"));
    fits(number.row);fits(mic_level_label);fits(mic_rms_bar);fits(mic_peak_bar);fits(clip_label);fits(headset_label);
    touch(find_button("Close"));assert(vol->indev->group==keyboard_group && open_count==1 && !close_count && !leave_count);
    popup_open(POP_MODE);pump();fits(number.row);lv_group_focus_obj(number.row);mfk_click();inner_turn(-1);assert(number.draft==210 && param_i_get(cfg_freedv_timeout)==180);
    observed.state=FD_USB_TX;mfk_click();assert(number.editing && param_i_get(cfg_freedv_timeout)==180 && strstr(lv_label_get_text(edit_hint),"Release PTT"));
    observed.state=FD_USB_RX;mfk_click();assert(!number.editing && param_i_get(cfg_freedv_timeout)==210);
    touch(find_button("Help"));assert(strstr(lv_label_get_text(popup_message),"HOST USB"));touch(find_button("Close"));
    popup_open(POP_MIC);pump();mfk_click();inner_turn(-1);assert(number.editing && number.draft==8);
    panel_key(BTN_TRIGGER_HAPPY19,1);panel_key(BTN_TRIGGER_HAPPY19,0);assert(!popup && native_power_actions==1 && observed.mic_gain_db==7);
    observed.tx_eligible=false;pump();assert(strstr(lv_label_get_text(status_label),"RX only"));observed.tx_eligible=true;
    popup_open(POP_AUDIO);pump();fits(audio_list);assert(lv_dropdown_get_option_cnt(audio_list)==1);touch(find_button("Close"));assert(open_count==1 && !close_count);
    observed.mic_health=FD_USB_MIC_UNAVAILABLE;observed.mic_retry_available=true;pump();keypad_set_long_time(100);
    panel_key(BTN_TRIGGER_HAPPY15,1);pump();panel_key(BTN_TRIGGER_HAPPY15,0);assert(popup_type==POP_AUDIO && mic_retries==0);touch(find_button("Close"));
    panel_key(BTN_TRIGGER_HAPPY15,1);panel_key(BTN_TRIGGER_HAPPY15,0);assert(mic_retries==1 && !popup);observed.mic_health=FD_USB_MIC_HEALTHY;pump();

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
    usb_ui_cfg_verify_profiles();usb_ui_cfg_close();close(vol->fd);close(mfk->fd);close(panel->fd);close(inner->fd);
    puts("PASS: native-screen FreeDV notice precedes profile/audio; cancellation and durable skip; paired-device gain migration; local VOL/mic/meters; warm popups; RX-only; graceful and terminal teardown");
    return 0;
}
