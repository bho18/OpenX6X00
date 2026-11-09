/* SPDX-License-Identifier: LGPL-2.1-or-later */
/* Native LVGL encoder/slider events only; no audio, radio or hardware access. */
#define _POSIX_C_SOURCE 200809L
#include "src/whole_value_row.h"
#include "lv_tick_mono.h"
#include <assert.h>
#include <stdio.h>
#include <string.h>

static lv_indev_state_t button=LV_INDEV_STATE_RELEASED;
static lv_indev_state_t key_state=LV_INDEV_STATE_RELEASED;
static uint32_t key_code=LV_KEY_ENTER;
static int32_t turns;
static lv_color_t pixels[320*24];
static unsigned previews,commits,cancels;
static int32_t audible,stored;
static bool preview_ok=true,commit_ok=true,local=true;

static void flush(lv_disp_drv_t *display,const lv_area_t *area,lv_color_t *map) {
    (void)area;(void)map;lv_disp_flush_ready(display);
}
static void encoder_read(lv_indev_drv_t *driver,lv_indev_data_t *data) {
    (void)driver;data->state=button;data->enc_diff=turns;turns=0;
}
static void keypad_read(lv_indev_drv_t *driver,lv_indev_data_t *data) {
    (void)driver;data->state=key_state;data->key=key_code;
}
static void advance(uint32_t ms) {
    lv_tick_mono_set((uint32_t)(lv_tick_get()+ms));lv_timer_handler();
}
static void pump(void) {for(unsigned n=0;n<3;++n)advance(20);}
static void click(void) {button=LV_INDEV_STATE_PRESSED;pump();button=LV_INDEV_STATE_RELEASED;pump();}
static void key_click(uint32_t code) {key_code=code;key_state=LV_INDEV_STATE_PRESSED;pump();key_state=LV_INDEV_STATE_RELEASED;pump();}
static void turn(int32_t detents) {turns=detents;pump();}
static bool preview(int32_t value,void *context) {
    (void)context;++previews;if(preview_ok)audible=value;return preview_ok;
}
static bool commit(int32_t value,void *context) {
    (void)context;++commits;if(commit_ok)stored=value;return commit_ok;
}
static void cancel(int32_t original,void *context) {(void)context;++cancels;audible=original;}
static bool allowed(void *context) {(void)context;return local;}
static void format(char *text,size_t size,int32_t value,void *context) {
    (void)context;snprintf(text,size,"%+ld dB",(long)value);
}

int main(void) {
    lv_init();
    lv_disp_draw_buf_t buffer;lv_disp_draw_buf_init(&buffer,pixels,NULL,320*24);
    lv_disp_drv_t display;lv_disp_drv_init(&display);display.hor_res=320;display.ver_res=240;
    display.draw_buf=&buffer;display.flush_cb=flush;assert(lv_disp_drv_register(&display));
    lv_group_t *group=lv_group_create();
    lv_indev_drv_t input;lv_indev_drv_init(&input);input.type=LV_INDEV_TYPE_ENCODER;
    input.read_cb=encoder_read;input.long_press_time=300;
    lv_indev_t *encoder=lv_indev_drv_register(&input);assert(encoder);lv_indev_set_group(encoder,group);
    whole_value_row_callbacks_t callbacks={.preview=preview,.commit=commit,.cancel=cancel,.allowed=allowed,.format=format};
    whole_value_row_t *gain=whole_value_row_create(lv_scr_act(),group,16,-12,24,1,&callbacks);assert(gain);
    lv_obj_t *next=lv_btn_create(lv_scr_act());lv_group_add_obj(group,next);
    lv_group_focus_obj(whole_value_row_object(gain));pump();
    audible=stored=16;
    assert(lv_obj_check_type(whole_value_row_object(gain),&lv_slider_class));
    assert(!whole_value_row_editing(gain));click();assert(whole_value_row_editing(gain));
    turn(3);assert(whole_value_row_value(gain)==19 && audible==19 && stored==16 && previews==3);
    assert(!strcmp(lv_label_get_text(whole_value_row_label(gain)),"+19 dB"));
    click();assert(!whole_value_row_editing(gain) && stored==19 && commits==1);
    /* One native short click commits once; its subsequent CLICKED/ENTER events
     * must not re-enter or persist a second time. */
    pump();assert(!whole_value_row_editing(gain) && commits==1);
    click();turn(2);assert(audible==21);whole_value_row_cancel(gain);
    assert(!whole_value_row_editing(gain) && audible==19 && stored==19 && cancels==1);
    click();preview_ok=false;turn(1);
    assert(whole_value_row_value(gain)==19 && audible==19 && whole_value_row_editing(gain));
    preview_ok=true;turn(1);assert(audible==20);commit_ok=false;click();
    assert(whole_value_row_editing(gain) && lv_group_get_editing(group) && stored==19);
    assert(!lv_slider_is_dragged(whole_value_row_object(gain)));
    commit_ok=true;click();assert(!whole_value_row_editing(gain) && stored==20);
    click();turn(2);unsigned before=commits;
    button=LV_INDEV_STATE_PRESSED;pump();advance(360);pump();button=LV_INDEV_STATE_RELEASED;pump();
    assert(whole_value_row_editing(gain) && commits==before && stored==20);
    whole_value_row_cancel(gain);assert(audible==20);
    click();turn(100);assert(whole_value_row_value(gain)==24 && audible==24);
    turn(-100);assert(whole_value_row_value(gain)==-12 && audible==-12);
    whole_value_row_cancel(gain);assert(audible==20);
    local=false;click();assert(!whole_value_row_editing(gain));local=true;
    click();turn(1);assert(audible==21);whole_value_row_destroy(gain);assert(audible==20);
    /* A native slider position is a whole timeout step, with draft-only values. */
    whole_value_row_callbacks_t timeout_callbacks={.commit=commit};
    whole_value_row_t *timeout=whole_value_row_create(lv_scr_act(),group,60,30,600,30,&timeout_callbacks);assert(timeout);
    lv_group_focus_obj(whole_value_row_object(timeout));pump();stored=60;
    click();turn(2);assert(whole_value_row_value(timeout)==120 && stored==60);
    whole_value_row_cancel(timeout);assert(whole_value_row_value(timeout)==60 && stored==60);
    click();turn(-100);assert(whole_value_row_value(timeout)==30);click();assert(stored==30);
    turn(1);assert(lv_group_get_focused(group)==next && !lv_group_get_editing(group));
    whole_value_row_destroy(timeout);lv_obj_del(next);
    /* LVGL suppresses its long-press edit toggle when a group has one item;
     * that release still must not be mistaken for a confirming short click. */
    whole_value_row_t *single=whole_value_row_create(lv_scr_act(),group,10,-12,24,1,&callbacks);assert(single);
    lv_group_focus_obj(whole_value_row_object(single));pump();audible=stored=10;
    click();turn(1);before=commits;
    button=LV_INDEV_STATE_PRESSED;pump();advance(360);pump();button=LV_INDEV_STATE_RELEASED;pump();
    assert(whole_value_row_editing(single) && commits==before && stored==10);
    assert(lv_group_send_data(group,LV_KEY_ESC)==LV_RES_OK);
    assert(!whole_value_row_editing(single) && audible==10);
    /* The keyboard's ENTER press followed by CLICKED must also commit once,
     * and its long hold must leave the live draft uncommitted. */
    lv_indev_drv_t keys;lv_indev_drv_init(&keys);keys.type=LV_INDEV_TYPE_KEYPAD;
    keys.read_cb=keypad_read;keys.long_press_time=300;
    lv_indev_t *keyboard=lv_indev_drv_register(&keys);assert(keyboard);lv_indev_set_group(keyboard,group);
    key_click(LV_KEY_ENTER);assert(whole_value_row_editing(single));
    key_click(LV_KEY_RIGHT);assert(audible==11);before=commits;
    key_click(LV_KEY_ENTER);assert(!whole_value_row_editing(single) && stored==11 && commits==before+1);
    key_click(LV_KEY_ENTER);key_click(LV_KEY_RIGHT);before=commits;
    key_code=LV_KEY_ENTER;key_state=LV_INDEV_STATE_PRESSED;pump();advance(360);pump();key_state=LV_INDEV_STATE_RELEASED;pump();
    assert(whole_value_row_editing(single) && stored==11 && commits==before);
    key_click(LV_KEY_ESC);assert(!whole_value_row_editing(single) && audible==11);
    whole_value_row_destroy(single);lv_indev_delete(keyboard);lv_indev_delete(encoder);lv_group_del(group);
    puts("PASS: native LVGL whole-value slider, live preview, one-click commit, cancellation, failure, bounds and focus");
    return 0;
}
