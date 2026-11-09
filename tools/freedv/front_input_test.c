// SPDX-License-Identifier: LGPL-2.1-or-later
#define _POSIX_C_SOURCE 200809L
#include "src/front_keys.c"
#include "src/mfk_device.c"
#include <assert.h>
#include <errno.h>
#include <string.h>
#include <stdarg.h>
#include "src/radio.h"
static lv_indev_t *active_input;
static lv_indev_t pad_input, encoder_input;
static unsigned resets, waits, deleted_timers;
static lv_timer_t retained_timers[64];
static unsigned timer_count;
static unsigned presses[KEYS], releases[KEYS];
static lv_obj_t test_screen;
static bool kernel_held,long_was_local,release_on_press;
static uint16_t kernel_code;
static unsigned long_events;
encoder_t *mfk;
rotary_t *vol;
lv_group_t *keyboard_group;
lv_event_code_t EVENT_KEYPAD=123;
radio_state_t radio_get_state(void) { return RADIO_RX; }
bool backlight_tick(void) { return false; }
bool backlight_knob_tick(void) { return false; }
static rotary_reset_result reset_fixture=ROTARY_RESET_READY;
bool rotary_diagnostic_reset_begin(void) { return true; }
rotary_reset_result rotary_diagnostic_reset_poll(void) { return reset_fixture; }
bool rotary_diagnostic_reset(void) { return true; }
bool rotary_diagnostic_local_event(lv_indev_t *input,unsigned generation) { (void)input;(void)generation;return false; }
bool keyboard_diagnostic_reset(void) { return true; }
lv_indev_t *lv_indev_get_act(void) { return active_input; }
lv_obj_t *lv_group_get_focused(const lv_group_t *group) { (void)group;return NULL; }
bool lv_obj_check_type(const lv_obj_t *object,const lv_obj_class_t *type) { (void)object;(void)type;return false; }
bool lv_dropdown_is_open(lv_obj_t *object) { (void)object;return false; }
lv_indev_type_t lv_indev_get_type(const lv_indev_t *input) { return input==&encoder_input?LV_INDEV_TYPE_ENCODER:LV_INDEV_TYPE_KEYPAD; }
lv_indev_t *lv_indev_get_next(lv_indev_t *input) { return !input?&pad_input:input==&pad_input?&encoder_input:NULL; }
void lv_indev_reset(lv_indev_t *input,lv_obj_t *obj) { assert(input&&!obj);++resets; }
void lv_indev_wait_release(lv_indev_t *input) { assert(input);++waits; }
void lv_timer_del(lv_timer_t *timer) { assert(timer);++deleted_timers; }
lv_timer_t *lv_timer_create(lv_timer_cb_t callback,uint32_t period,void *arg) { assert(callback==held_cb && period && timer_count<64);lv_timer_t *t=&retained_timers[timer_count++];t->timer_cb=callback;t->user_data=arg;return t; }
void lv_timer_set_repeat_count(lv_timer_t *timer,int32_t count) { assert(timer && count==1); }
lv_disp_t *lv_disp_get_default(void) { return NULL; }
lv_obj_t *lv_disp_get_scr_act(lv_disp_t *disp) { (void)disp;return &test_screen; }
lv_res_t lv_event_send(lv_obj_t *object,lv_event_code_t code,void *param) {
    assert(object==&test_screen && code==EVENT_KEYPAD);event_keypad_t *key=param;
    if(key->state==KEYPAD_LONG){++long_events;long_was_local=front_keys_diagnostic_local_event();}
    if(key->state==KEYPAD_PRESS) {++presses[key->key];if(release_on_press)front_keys_diagnostic_input_release();}
    if(key->state==KEYPAD_RELEASE || key->state==KEYPAD_LONG_RELEASE) ++releases[key->key];
    return LV_RES_OK;
}
int ioctl(int fd,unsigned long request,...) {
    (void)fd;(void)request;va_list args;va_start(args,request);unsigned long *keys=va_arg(args,unsigned long *);va_end(args);
    memset(keys,0,(KEY_MAX+8*sizeof(long))/(8*sizeof(long))*sizeof(long));
    if(kernel_held)keys[kernel_code/(8*sizeof(long))]|=1UL<<(kernel_code%(8*sizeof(long)));
    return 0;
}
void _lv_log_add(lv_log_level_t level,const char *file,int line,const char *func,const char *format,...) { (void)level;(void)file;(void)line;(void)func;(void)format; }
static void post_key(int fd,int value) {
    struct input_event e={.type=EV_KEY,.code=BTN_TRIGGER_HAPPY27,.value=value};assert(write(fd,&e,sizeof(e))==(ssize_t)sizeof(e));
}
static void physical_key(keypad_t *pad,int writer,uint16_t code,int value) {
    struct input_event e={.type=EV_KEY,.code=code,.value=value};kernel_code=code;kernel_held=value!=0;
    assert(write(writer,&e,sizeof(e))==(ssize_t)sizeof(e));active_input=&pad_input;
    lv_indev_data_t data={0};keypad_read(&pad->indev_drv,&data);active_input=NULL;
}
int main(void) {
    int front[2],encoder[2];assert(!pipe(front)&&!pipe(encoder));
    assert(fcntl(front[0],F_SETFL,O_NONBLOCK)>=0&&fcntl(encoder[0],F_SETFL,O_NONBLOCK)>=0);
    keypad_t pad={.fd=front[0],.indev=&pad_input,.evdev_state=LV_INDEV_STATE_PRESSED};
    encoder_t knob={.fd=encoder[0],.indev=&encoder_input,.pressed=true};
    pad.indev_drv.user_data=&pad;knob.indev_drv.user_data=&knob;mfk=diagnostic_encoder=&knob;
    diagnostic_pads[0]=&pad;diagnostic_pad_count=1;
    key_st[KEYPAD_F3].hold_timer=(lv_timer_t *)&pad;
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
    assert(front_keys_diagnostic_input_acquire());
    physical_key(&pad,front[1],BTN_TRIGGER_HAPPY15,1);
    assert(key_st[KEYPAD_F3].hold_timer && !front_keys_diagnostic_local_event());
    held_cb(key_st[KEYPAD_F3].hold_timer);assert(long_events==1 && long_was_local);
    assert(!front_keys_diagnostic_local_event()); /* provenance is scoped to dispatch */
    physical_key(&pad,front[1],BTN_TRIGGER_HAPPY15,0);
    physical_key(&pad,front[1],BTN_TRIGGER_HAPPY15,1);kernel_held=false;
    held_cb(key_st[KEYPAD_F3].hold_timer);assert(long_events==1); /* release not yet read still wins */
    physical_key(&pad,front[1],BTN_TRIGGER_HAPPY15,0);
    physical_key(&pad,front[1],BTN_TRIGGER_HAPPY15,1);
    ctl_diagnostic_input_unblock();assert(ctl_diagnostic_input_block());
    held_cb(key_st[KEYPAD_F3].hold_timer);assert(long_events==1); /* generation changed */
    physical_key(&pad,front[1],BTN_TRIGGER_HAPPY15,0);
    physical_key(&pad,front[1],BTN_TRIGGER_HAPPY15,1);
    lv_timer_t *old_timer=key_st[KEYPAD_F3].hold_timer;
    front_keys_diagnostic_input_release();assert(front_keys_diagnostic_input_acquire());
    held_cb(old_timer);assert(long_events==1); /* entry/exit drained the held press */
    physical_key(&pad,front[1],BTN_TRIGGER_HAPPY15,1);
    physical_key(&pad,front[1],MFK_PRESS,1);held_cb(key_st[KEYPAD_F3].hold_timer);assert(long_events==1);
    physical_key(&pad,front[1],MFK_PRESS,0);
    keypad_power_set(true);assert(key_st[KEYPAD_POWER].hold_timer);
    held_cb(key_st[KEYPAD_POWER].hold_timer);assert(long_events==2 && !long_was_local);
    assert(!front_keys_diagnostic_local_event()); /* POWER is terminal, never F3 consent */
    keypad_power_set(false);
    front_keys_diagnostic_input_release();
    /* PTT release remains independent of another key's press/release and hold. */
    physical_key(&pad,front[1],BTN_TRIGGER_HAPPY4,1);
    physical_key(&pad,front[1],BTN_TRIGGER_HAPPY14,1);
    held_cb(key_st[KEYPAD_F2].hold_timer);
    physical_key(&pad,front[1],BTN_TRIGGER_HAPPY14,0);
    physical_key(&pad,front[1],BTN_TRIGGER_HAPPY4,0);
    assert(presses[KEYPAD_PTT]==1 && releases[KEYPAD_PTT]==1 && !key_st[KEYPAD_PTT].down);
    assert(presses[KEYPAD_F2]==1 && releases[KEYPAD_F2]==1 && !key_st[KEYPAD_F2].down);
    assert(front_keys_diagnostic_input_acquire());release_on_press=true;
    physical_key(&pad,front[1],BTN_TRIGGER_HAPPY20,1);
    assert(!key_st[KEYPAD_F5].down && !key_st[KEYPAD_F5].hold_timer);
    assert(!ctl_diagnostic_input_blocked());release_on_press=false;
    /* Denied asynchronous entry must release its temporary fence, while an
     * exit never releases ownership until the producer has acknowledged. */
    reset_fixture=ROTARY_RESET_PENDING;assert(front_keys_input_begin(false));
    assert(front_keys_input_poll()==FD_FRONT_INPUT_PENDING && ctl_diagnostic_input_blocked());
    reset_fixture=ROTARY_RESET_FAILED;assert(front_keys_input_poll()==FD_FRONT_INPUT_FAILED);
    front_keys_input_cancel_acquire();assert(!ctl_diagnostic_input_blocked());
    assert(ctl_claim(CTL_BROWSER,17,"after-denied-entry",true));
    struct input_event turn_event={.type=EV_REL,.value=-1};lv_indev_data_t turn_data={0};
    assert(write(encoder[1],&turn_event,sizeof(turn_event))==sizeof(turn_event));mfk_read(&knob.indev_drv,&turn_data);
    ctl_get(&owner);assert(owner.kind==CTL_BROWSER && owner.id==17 && turn_data.enc_diff==1); /* upstream inactive behavior */
    reset_fixture=ROTARY_RESET_READY;assert(front_keys_input_begin(false));
    assert(front_keys_input_poll()==FD_FRONT_INPUT_READY);
    assert(write(encoder[1],&turn_event,sizeof(turn_event))==sizeof(turn_event));mfk_read(&knob.indev_drv,&turn_data);
    ctl_get(&owner);assert(owner.kind==CTL_LOCAL && turn_data.enc_diff==1); /* fresh scoped input */
    reset_fixture=ROTARY_RESET_PENDING;assert(front_keys_input_begin(true));
    assert(front_keys_input_poll()==FD_FRONT_INPUT_PENDING && ctl_diagnostic_input_blocked());
    reset_fixture=ROTARY_RESET_FAILED;assert(front_keys_input_poll()==FD_FRONT_INPUT_FAILED && ctl_diagnostic_input_blocked());
    reset_fixture=ROTARY_RESET_READY;assert(front_keys_input_begin(true));
    assert(front_keys_input_poll()==FD_FRONT_INPUT_READY && !ctl_diagnostic_input_blocked());
    close(front[0]);close(front[1]);close(encoder[0]);close(encoder[1]);
    puts("PASS: fresh held-key provenance is dispatch-scoped; release, new press, generation and input drains revoke consent; independent keys retain PTT release");
}
