// SPDX-License-Identifier: LGPL-2.1-or-later
#define _POSIX_C_SOURCE 200809L
#include "src/front_keys.c"
#include "src/mfk_device.c"
#include <assert.h>
#include <errno.h>
#include <string.h>
#include "src/radio.h"
static lv_indev_t *active_input;
static lv_indev_t pad_input, encoder_input;
static unsigned resets, waits, deleted_timers;
encoder_t *mfk;
rotary_t *vol;
lv_group_t *keyboard_group;
radio_state_t radio_get_state(void) { return RADIO_RX; }
bool backlight_tick(void) { return false; }
bool rotary_diagnostic_reset(void) { return true; }
bool rotary_diagnostic_local_event(lv_indev_t *input,unsigned generation) { (void)input;(void)generation;return false; }
bool keyboard_diagnostic_reset(void) { return true; }
lv_indev_t *lv_indev_get_act(void) { return active_input; }
lv_indev_type_t lv_indev_get_type(const lv_indev_t *input) { return input==&encoder_input?LV_INDEV_TYPE_ENCODER:LV_INDEV_TYPE_KEYPAD; }
lv_indev_t *lv_indev_get_next(lv_indev_t *input) { return !input?&pad_input:input==&pad_input?&encoder_input:NULL; }
void lv_indev_reset(lv_indev_t *input,lv_obj_t *obj) { assert(input&&!obj);++resets; }
void lv_indev_wait_release(lv_indev_t *input) { assert(input);++waits; }
void lv_timer_del(lv_timer_t *timer) { assert(timer);++deleted_timers; }
lv_timer_t *lv_timer_create(lv_timer_cb_t callback,uint32_t period,void *arg) { (void)callback;(void)period;(void)arg;assert(0);return NULL; }
void lv_timer_set_repeat_count(lv_timer_t *timer,int32_t count) { (void)timer;(void)count;assert(0); }
lv_disp_t *lv_disp_get_default(void) { assert(0);return NULL; }
lv_obj_t *lv_disp_get_scr_act(lv_disp_t *disp) { (void)disp;assert(0);return NULL; }
lv_res_t lv_event_send(lv_obj_t *object,lv_event_code_t code,void *param) { (void)object;(void)code;(void)param;assert(0);return LV_RES_OK; }
void _lv_log_add(lv_log_level_t level,const char *file,int line,const char *func,const char *format,...) { (void)level;(void)file;(void)line;(void)func;(void)format; }
static void post_key(int fd,int value) {
    struct input_event e={.type=EV_KEY,.code=BTN_TRIGGER_HAPPY27,.value=value};assert(write(fd,&e,sizeof(e))==(ssize_t)sizeof(e));
}
int main(void) {
    int front[2],encoder[2];assert(!pipe(front)&&!pipe(encoder));
    assert(fcntl(front[0],F_SETFL,O_NONBLOCK)>=0&&fcntl(encoder[0],F_SETFL,O_NONBLOCK)>=0);
    keypad_t pad={.fd=front[0],.indev=&pad_input,.evdev_state=LV_INDEV_STATE_PRESSED};
    encoder_t knob={.fd=encoder[0],.indev=&encoder_input,.pressed=true};
    pad.indev_drv.user_data=&pad;mfk=diagnostic_encoder=&knob;
    diagnostic_pads[0]=&pad;diagnostic_pad_count=1;
    hold_timer=(lv_timer_t *)&pad;
    assert(ctl_claim(CTL_BROWSER,9,"remote",false));
    ctl_injected(); /* fresh local consent must not depend on waiting150ms */
    post_key(front[1],1);post_key(front[1],0); /* pre-fence remote navigation */
    assert(front_keys_diagnostic_input_acquire());
    assert(!knob.pressed&&pad.evdev_state==LV_INDEV_STATE_RELEASED&&deleted_timers==1&&resets==2&&waits==2);
    assert(!front_keys_diagnostic_input_acquire());
    active_input=&encoder_input;
    assert(!front_keys_diagnostic_local_event());
    post_key(front[1],2);lv_indev_data_t data={0};keypad_read(&pad.indev_drv,&data);
    assert(!knob.pressed&&!front_keys_diagnostic_local_event()); /* old held repeat cannot consent */
    post_key(front[1],1);keypad_read(&pad.indev_drv,&data);
    assert(knob.pressed&&front_keys_diagnostic_local_event());
    ctl_info_t owner;ctl_get(&owner);assert(owner.kind==CTL_LOCAL);
    post_key(front[1],0);keypad_read(&pad.indev_drv,&data);
    assert(!knob.pressed&&front_keys_diagnostic_local_event()); /* LVGL CLICKED arrives on release */
    front_keys_diagnostic_input_release();assert(!front_keys_diagnostic_local_event());
    assert(front_keys_diagnostic_input_acquire());assert(!front_keys_diagnostic_local_event());
    post_key(front[1],2);keypad_read(&pad.indev_drv,&data);assert(!front_keys_diagnostic_local_event());
    front_keys_diagnostic_input_release();
    close(front[0]);close(front[1]);close(encoder[0]);close(encoder[1]);
    puts("PASS: queued inputs/hold state drained, only fresh front-panel press consents, release and reacquire expire consent");
}
