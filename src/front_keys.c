/*
 *  SPDX-License-Identifier: LGPL-2.1-or-later
 *
 *  Xiegu X6100 LVGL GUI
 *
 *  Copyright (c) 2026 Giovanni Ricciardi IU1WXY
 */

/* The radio's buttons (front and top side, the knob presses) from their input devices.
 * A button goes to the screen as EVENT_KEYPAD: press, release, and after the hold time
 * long and its release, each button on its own while others are down too. The VOL press
 * is an LVGL key of its own (KEY_VOL_PRESS), once a press however long it is held; the
 * MFK press is the button of the MFK encoder (encoder.c). */

#include "freedv/features.h"
#include "front_keys.h"
#include "freedv/mode.h"
#include "freedv/input_drain.h"

#include "screen_light.h"
#include "control_owner.h"
#include "key_input.h"
#include "main.h"

#include <fcntl.h>
#include <linux/input.h>
#include <stdio.h>
#include <stdlib.h>
#include <time.h>
#include <unistd.h>

#define HOLD_MS 1000

static const struct {
    uint16_t     code;
    keypad_key_t key;
} buttons[] = {
#ifdef BOARD_X6200
    /* The X6200's matrix (as the stock X6200 GUI reads it): the same codes as the X6100's front
     * row, APP/KEY and MSG/DFN swapped, no PRE, VM, AGC or LOCK key, ATU and FST moved up */
    {KEY_POWER, KEYPAD_POWER},          {BTN_TRIGGER_HAPPY1, KEYPAD_GEN},  {BTN_TRIGGER_HAPPY2, KEYPAD_APP},
    {BTN_TRIGGER_HAPPY3, KEYPAD_KEY},   {BTN_TRIGGER_HAPPY7, KEYPAD_MSG},  {BTN_TRIGGER_HAPPY8, KEYPAD_DFN},
    {BTN_TRIGGER_HAPPY9, KEYPAD_DFL},   {BTN_TRIGGER_HAPPY13, KEYPAD_F1},  {BTN_TRIGGER_HAPPY14, KEYPAD_F2},
    {BTN_TRIGGER_HAPPY15, KEYPAD_F3},   {BTN_TRIGGER_HAPPY19, KEYPAD_F4},  {BTN_TRIGGER_HAPPY20, KEYPAD_F5},
    /* top side */
    {BTN_TRIGGER_HAPPY4, KEYPAD_PTT},   {BTN_TRIGGER_HAPPY5, KEYPAD_BAND_DOWN},
    {BTN_TRIGGER_HAPPY6, KEYPAD_BAND_UP}, {BTN_TRIGGER_HAPPY10, KEYPAD_MODE_AM},
    {BTN_TRIGGER_HAPPY11, KEYPAD_MODE_CW}, {BTN_TRIGGER_HAPPY12, KEYPAD_MODE_SSB},
    {BTN_TRIGGER_HAPPY16, KEYPAD_AB},   {BTN_TRIGGER_HAPPY17, KEYPAD_ATU}, {BTN_TRIGGER_HAPPY18, KEYPAD_FST},
#else
    /* front */
    {KEY_POWER, KEYPAD_POWER},          {BTN_TRIGGER_HAPPY1, KEYPAD_GEN},  {BTN_TRIGGER_HAPPY7, KEYPAD_APP},
    {BTN_TRIGGER_HAPPY2, KEYPAD_KEY},   {BTN_TRIGGER_HAPPY8, KEYPAD_MSG},  {BTN_TRIGGER_HAPPY3, KEYPAD_DFN},
    {BTN_TRIGGER_HAPPY9, KEYPAD_DFL},   {BTN_TRIGGER_HAPPY13, KEYPAD_F1},  {BTN_TRIGGER_HAPPY14, KEYPAD_F2},
    {BTN_TRIGGER_HAPPY15, KEYPAD_F3},   {BTN_TRIGGER_HAPPY19, KEYPAD_F4},  {BTN_TRIGGER_HAPPY20, KEYPAD_F5},
    {BTN_TRIGGER_HAPPY25, KEYPAD_LOCK},
    /* top side */
    {BTN_TRIGGER_HAPPY4, KEYPAD_PTT},   {BTN_TRIGGER_HAPPY5, KEYPAD_BAND_DOWN},
    {BTN_TRIGGER_HAPPY6, KEYPAD_BAND_UP}, {BTN_TRIGGER_HAPPY10, KEYPAD_MODE_AM},
    {BTN_TRIGGER_HAPPY11, KEYPAD_MODE_CW}, {BTN_TRIGGER_HAPPY12, KEYPAD_MODE_SSB},
    {BTN_TRIGGER_HAPPY16, KEYPAD_AB},   {BTN_TRIGGER_HAPPY17, KEYPAD_PRE}, {BTN_TRIGGER_HAPPY18, KEYPAD_ATU},
    {BTN_TRIGGER_HAPPY22, KEYPAD_VM},   {BTN_TRIGGER_HAPPY23, KEYPAD_AGC}, {BTN_TRIGGER_HAPPY24, KEYPAD_FST},
#endif
};

#define VOL_PRESS BTN_TRIGGER_HAPPY21
#define MFK_PRESS BTN_TRIGGER_HAPPY27

/* Each key on its own: its press, its long press and its release, whatever else is pressed and let go meanwhile.
 * With one key remembered a release went missing: the side PTT held, a front key tapped, and the PTT's release
 * found the tapped key's state and sent nothing, the radio on the air until PTT was pressed again. The browser's
 * PTT lets go through here too: the end of its lease and its three minutes went missing the same way */
#define KEYS (KEYPAD_FST + 1)   /* FST is the last key */

#ifdef ENABLE_FREEDV_CONTROL
static keypad_t *diagnostic_pads[4];
static unsigned diagnostic_pad_count;
static bool held_dispatch_local, power_reported_down;
#ifdef HOST_BUILD
extern bool front_keys_test_held(int fd, uint16_t code) __attribute__((weak));
#endif
#endif

static struct {
    bool        down;
    bool        long_sent;      /* KEYPAD_LONG went: KEYPAD_LONG_RELEASE on the release */
    lv_timer_t *hold_timer;
#ifdef ENABLE_FREEDV_CONTROL
    keypad_t   *origin_pad;
    uint16_t    origin_code;
    unsigned    origin_generation;
    bool        origin_armed, legacy_power;
#endif
} key_st[KEYS];

static uint32_t hold_ms = HOLD_MS;

void keypad_set_long_time(uint32_t hold) {
    hold_ms = hold ? hold : HOLD_MS;
}

static void send(keypad_key_t key, keypad_state_t state) {
    event_keypad_t ev = {key, state};   /* sent by address: the receivers read it at once */

    lv_event_send(lv_scr_act(), EVENT_KEYPAD, &ev);
}

#ifdef ENABLE_FREEDV_CONTROL
/* A new physical UI press invalidates old held-key consent, without losing the
 * independent press/release state of PTT or any other key. */
static void hold_origins_clear(void) {
    for (unsigned key = 0; key < KEYS; ++key) key_st[key].origin_armed = false;
}

static bool hold_source_down(keypad_key_t key) {
    keypad_t *pad = key_st[key].origin_pad;
    uint16_t code = key_st[key].origin_code;
    if (!pad) return false;
#ifdef HOST_BUILD
    if (front_keys_test_held) return front_keys_test_held(pad->fd, code);
#endif
    unsigned long keys[(KEY_MAX + 8*sizeof(long))/(8*sizeof(long))] = {0};
    return code <= KEY_MAX && ioctl(pad->fd, EVIOCGKEY(sizeof(keys)), keys) >= 0 &&
        (keys[code/(8*sizeof(long))] & (1UL << (code%(8*sizeof(long)))));
}
#endif

static void held_cb(lv_timer_t *t) {
    keypad_key_t key = (keypad_key_t)(intptr_t)t->user_data;
    if ((unsigned)key >= KEYS || key_st[key].hold_timer != t) return;
    key_st[key].hold_timer = NULL;      /* one shot: LVGL deletes it */
#ifdef ENABLE_FREEDV_CONTROL
    bool scoped = ctl_diagnostic_input_blocked();
    bool verified = scoped && key_st[key].down && key_st[key].origin_armed &&
        key_st[key].origin_pad &&
        key_st[key].origin_generation == ctl_remote_input_generation() &&
        key_st[key].origin_pad->diagnostic_generation == key_st[key].origin_generation &&
        hold_source_down(key);
    /* Baseband POWER has no evdev level. Preserve its terminal hold without
     * granting local UI consent or recognizing an injected input as physical. */
    bool power = scoped && key == KEYPAD_POWER && key_st[key].down &&
        key_st[key].legacy_power && power_reported_down &&
        key_st[key].origin_generation == ctl_remote_input_generation();
    if (scoped && !verified && !power) return;
    held_dispatch_local = verified;
#endif
    key_st[key].long_sent = true;
    send(key, KEYPAD_LONG);
#ifdef ENABLE_FREEDV_CONTROL
    held_dispatch_local = false;
    key_st[key].origin_armed = false;
    key_st[key].legacy_power = false;
#endif
}

static keypad_key_t button_of(uint16_t code) {
    for (size_t i = 0; i < sizeof(buttons) / sizeof(buttons[0]); i++) {
        if (buttons[i].code == code) {
            return buttons[i].key;
        }
    }
    LV_LOG_WARN("Unknown key");
    return KEYPAD_UNKNOWN;
}

/* Independent state for every button: another key must never hide PTT release. */
static void keypad_button_from(keypad_key_t key, int value, keypad_t *source, uint16_t code) {
    if ((unsigned)key >= KEYS) return;
    if (key_st[key].hold_timer) {
        lv_timer_del(key_st[key].hold_timer);
        key_st[key].hold_timer = NULL;
    }
    if (value == 1) {
        key_st[key].down = true;
        key_st[key].long_sent = false;
#ifdef ENABLE_FREEDV_CONTROL
        key_st[key].origin_pad = source;
        key_st[key].origin_code = code;
        key_st[key].origin_generation = ctl_remote_input_generation();
        key_st[key].origin_armed = source && ctl_diagnostic_input_blocked() &&
            source->diagnostic_generation == key_st[key].origin_generation;
        key_st[key].legacy_power = !source && key == KEYPAD_POWER && ctl_diagnostic_input_blocked();
#else
        (void)source; (void)code;
#endif
        send(key, KEYPAD_PRESS);
        /* Entry/exit handlers may synchronously drain the inputs. Do not
         * resurrect a hold timer for a press that the barrier just revoked. */
        if (!key_st[key].down) return;
        key_st[key].hold_timer = lv_timer_create(held_cb, key == KEYPAD_POWER ? HOLD_MS : hold_ms,
                                                 (void *)(intptr_t)key);
        lv_timer_set_repeat_count(key_st[key].hold_timer, 1);
    } else if (key_st[key].down) {
        key_st[key].down = false;
#ifdef ENABLE_FREEDV_CONTROL
        key_st[key].origin_armed = key_st[key].legacy_power = false;
#endif
        send(key, key_st[key].long_sent ? KEYPAD_LONG_RELEASE : KEYPAD_RELEASE);
    }
}

void keypad_button(keypad_key_t key, int value) {
    keypad_button_from(key, value, NULL, 0);
}

static void button_event(const struct input_event *in, keypad_t *source) {
    if (in->code == BTN_TRIGGER_HAPPY4 && freedv_mode_radio_ptt(in->value != 0)) return;
    keypad_button_from(button_of(in->code), in->value, source, in->code);
}

static void keypad_read(lv_indev_drv_t *drv, lv_indev_data_t *data) {
    keypad_t          *pad = drv->user_data;
    struct input_event in;

    if (read(pad->fd, &in, sizeof(in)) > 0) {
        /* All the queued events in one pass: one per pass took 4 GUI loops for a key
         * (press, sync, release, sync), 100 ms and more while the screen is busy */
        data->continue_reading = true;

        if (in.type == EV_KEY) {
#ifdef ENABLE_FREEDV_CONTROL
            if (in.value == 1 && ctl_diagnostic_input_blocked()) {
                hold_origins_clear();
                pad->diagnostic_generation = ctl_remote_input_generation();
            }
#endif
            if (in.value)
                ctl_local_input();   /* a key at the radio: control is the operator's (unless injected) */
            if (backlight_tick() && in.value) {
                /* the press woke the screen: that was its job, the radio does not see it */
            } else if (in.code == VOL_PRESS) {
                if (in.value == 1) {    /* the press only: let go below, whatever the key does */
                    pad->evdev_key   = KEY_VOL_PRESS;
                    pad->evdev_state = LV_INDEV_STATE_PRESSED;
                }
            } else if (in.code == MFK_PRESS) {
                if (mfk) {
#ifdef ENABLE_FREEDV_CONTROL
                    if (ctl_diagnostic_input_blocked()) {
                        unsigned generation = ctl_remote_input_generation();
                        if (in.value == 1) mfk->diagnostic_generation = generation;
                        mfk->pressed = in.value != 0 && mfk->diagnostic_generation == generation;
                    } else
#endif
                        mfk->pressed = in.value != 0;
                }
                /* The encoder samples it at its next read: a quick press and release
                 * in the same pass would be lost */
                data->continue_reading = false;
            } else {
                button_event(&in, pad);
            }
        }
    }
    data->key   = pad->evdev_key;
    data->state = pad->evdev_state;
    /* LVGL sees the VOL press for one read: a key held past its long press time (HOLD_MS) is
     * sent again every 100 ms, and each one flipped VOL between choosing and changing */
    pad->evdev_state = LV_INDEV_STATE_RELEASED;
}

/* The POWER key as the base reports it in its packets (the X6200, hw_pack_t.power_key) or as
 * the browser sends it where there is no device for it: the same path as a press on a device,
 * so the wake, the hold and the control owner work the same. GUI thread */
void keypad_power_set(bool down) {
    struct input_event in = {.type = EV_KEY, .code = KEY_POWER, .value = down ? 1 : 0};

#ifdef ENABLE_FREEDV_CONTROL
    power_reported_down = down;
#endif
    if (down) {
        ctl_local_input();
    }
    if (backlight_tick() && down) {
        return;     /* the press woke the screen: that was its job */
    }
    button_event(&in, NULL);
}

keypad_t *keypad_init(char *dev_name) {
    if (!dev_name) {
        return NULL;    /* a board without this device (BOARD_DEV_POWER on the X6200) */
    }
    int fd = open(dev_name, O_RDWR | O_NOCTTY | O_NDELAY);

    if (fd == -1) {
        perror("unable to open keypad interface:");
        return NULL;
    }
    fcntl(fd, F_SETFL, O_ASYNC | O_NONBLOCK);

    keypad_t *pad = calloc(1, sizeof(*pad));

#ifdef ENABLE_FREEDV_CONTROL
    if (diagnostic_pad_count == sizeof(diagnostic_pads)/sizeof(diagnostic_pads[0])) {
        close(fd); free(pad); return NULL;
    }
    diagnostic_pads[diagnostic_pad_count++] = pad;
#endif
    pad->fd          = fd;
    pad->evdev_state = LV_INDEV_STATE_RELEASED;
    lv_indev_drv_init(&pad->indev_drv);
    pad->indev_drv.type            = LV_INDEV_TYPE_KEYPAD;
    pad->indev_drv.read_cb         = keypad_read;
    pad->indev_drv.user_data       = pad;
    pad->indev_drv.long_press_time = HOLD_MS;
    pad->indev = lv_indev_drv_register(&pad->indev_drv);
    lv_indev_set_group(pad->indev, keyboard_group);
    return pad;
}

#ifdef ENABLE_FREEDV_CONTROL
static fd_front_input_result input_reset_result=FD_FRONT_INPUT_READY;
static bool input_reset_releasing,input_reset_acquiring;
static lv_timer_t *legacy_release_timer;
static bool diagnostic_inputs_reset_begin(void) {
    bool ok = true;
    held_dispatch_local = false;
    for (unsigned key = 0; key < KEYS; ++key) {
        if (key_st[key].hold_timer) lv_timer_del(key_st[key].hold_timer);
        memset(&key_st[key], 0, sizeof(key_st[key]));
    }
    for (unsigned i = 0; i < diagnostic_pad_count; ++i) {
        keypad_t *pad = diagnostic_pads[i];
        if (!fd_diagnostic_drain_input(pad->fd)) ok = false;
        pad->evdev_key = 0;
        pad->evdev_state = LV_INDEV_STATE_RELEASED;
        pad->diagnostic_generation = 0;
    }
    if (!rotary_diagnostic_reset_begin()) ok = false;
    if (!encoder_diagnostic_reset()) ok = false;
    if (!keyboard_diagnostic_reset()) ok = false;
    for (lv_indev_t *input = lv_indev_get_next(NULL); input; input = lv_indev_get_next(input)) {
        lv_indev_reset(input, NULL);
        lv_indev_wait_release(input);
    }
    return ok;
}
bool front_keys_input_begin(bool release) {
    if(input_reset_result==FD_FRONT_INPUT_PENDING)return false;
    if(!release && !input_reset_acquiring){
        if(!ctl_diagnostic_input_block())return false;
        input_reset_acquiring=true;
    }
    input_reset_releasing=release;
    if(release && !ctl_diagnostic_input_blocked()){
        input_reset_acquiring=false;input_reset_result=FD_FRONT_INPUT_READY;return true;
    }
    input_reset_result=diagnostic_inputs_reset_begin()?FD_FRONT_INPUT_PENDING:FD_FRONT_INPUT_FAILED;
    return true;
}
fd_front_input_result front_keys_input_poll(void) {
    if(input_reset_result!=FD_FRONT_INPUT_PENDING)return input_reset_result;
    rotary_reset_result result=rotary_diagnostic_reset_poll();
    if(result==ROTARY_RESET_PENDING)return FD_FRONT_INPUT_PENDING;
    if(result==ROTARY_RESET_FAILED)return input_reset_result=FD_FRONT_INPUT_FAILED;
    if(input_reset_releasing)ctl_diagnostic_input_unblock();
    input_reset_acquiring=false;
    return input_reset_result=FD_FRONT_INPUT_READY;
}
void front_keys_input_cancel_acquire(void) {
    if(!input_reset_acquiring || input_reset_releasing)return;
    /* No mode/radio ownership was granted. The producer still rejects the old
     * epoch until its reset completes, so denied entry cannot replay a batch. */
    ctl_diagnostic_input_unblock();input_reset_acquiring=false;
    input_reset_result=FD_FRONT_INPUT_FAILED;
}
static fd_front_input_result input_reset_wait(void) {
    fd_front_input_result result;
    while((result=front_keys_input_poll())==FD_FRONT_INPUT_PENDING){
        struct timespec wait={.tv_nsec=1000000};nanosleep(&wait,NULL);
    }
    return result;
}
static void legacy_release_poll(lv_timer_t *unused) {
    (void)unused;
    fd_front_input_result result=front_keys_input_poll();
    if(result==FD_FRONT_INPUT_FAILED){(void)front_keys_input_begin(true);return;}
    if(result==FD_FRONT_INPUT_READY){lv_timer_del(legacy_release_timer);legacy_release_timer=NULL;}
}
bool front_keys_diagnostic_input_acquire(void) {
    if(!front_keys_input_begin(false))return false;
    if(input_reset_wait()==FD_FRONT_INPUT_READY)return true;
    front_keys_input_cancel_acquire();return false;
}
void front_keys_diagnostic_input_release(void) {
    if(!ctl_diagnostic_input_blocked())return;
    if(front_keys_input_begin(true) && input_reset_wait()==FD_FRONT_INPUT_READY)return;
    /* Compatibility callers cannot poll their closed panel. Keep the fence and
     * retry in the GUI; production USB mode uses the nonblocking API directly. */
    if(!legacy_release_timer)legacy_release_timer=lv_timer_create(legacy_release_poll,10,NULL);
}
bool front_keys_diagnostic_local_event(void) {
    if (!ctl_diagnostic_input_blocked()) return false;
    if (held_dispatch_local) return true;
    lv_indev_t *input = lv_indev_get_act();
    if (!input) return false;
#ifdef HOST_BUILD
    /* SDL mouse is local to the simulator; no real baseband is present. */
    if (lv_indev_get_type(input) == LV_INDEV_TYPE_POINTER) return true;
#endif
    unsigned generation = ctl_remote_input_generation();
    if (encoder_diagnostic_local_event(input, generation) || rotary_diagnostic_local_event(input, generation)) return true;
    for (unsigned i = 0; i < diagnostic_pad_count; ++i)
        if (diagnostic_pads[i]->indev == input && diagnostic_pads[i]->diagnostic_generation == generation) return true;
    return false;
}
#endif

#ifdef ENABLE_FREEDV_VOICE
bool front_keys_voice_local_event(void) {
    if(front_keys_diagnostic_local_event())return true;
    if(!ctl_diagnostic_input_blocked())return false;
    lv_indev_t *input=lv_indev_get_act();
    if(!input)return false;
    unsigned generation=ctl_remote_input_generation();
    return rotary_voice_local_event(input,generation) || keyboard_voice_local_event(input,generation);
}
#endif
