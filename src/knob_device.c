/*
 *  SPDX-License-Identifier: LGPL-2.1-or-later
 *
 *  Xiegu X6100 LVGL GUI
 *
 *  Copyright (c) 2026 Giovanni Ricciardi IU1WXY
 */

/* The VOL knob and the main knob. VOL turns become LVGL keys, one press and release per
 * detent, the key chosen by the knob's state (edit or select). The main knob goes to the
 * screen as EVENT_ROTARY with its speed. */

#include "freedv/features.h"
#include "gui_wake.h"
#include "knob_device.h"

#include "screen_light.h"
#include "control_owner.h"
#include "key_input.h"
#include "freedv/input_drain.h"

#include <fcntl.h>
#include <linux/input.h>
#include <poll.h>
#include <pthread.h>
#include <stdlib.h>
#ifdef ENABLE_FREEDV_CONTROL
#include <stdatomic.h>
#include <sys/eventfd.h>
#include <limits.h>
#endif
#include <time.h>
#include <unistd.h>

/* VOL: what is still to send of the last read, and whether the last key sent was down */
#ifdef ENABLE_FREEDV_CONTROL
static rotary_t *diagnostic_rotaries[4];
static unsigned diagnostic_rotary_count;
#endif
static int32_t pending;
static bool    key_down;

static void vol_read(lv_indev_drv_t *drv, lv_indev_data_t *data) {
    rotary_t *knob = drv->user_data;

    if (pending == 0) {
        struct input_event in;
        int32_t            sum = 0;
        bool               turned = false;

        while (read(knob->fd, &in, sizeof(in)) > 0) {
            if (in.type == EV_REL) {
                sum += in.value;
                turned = true;
            }
        }
        if (turned) {
#ifdef ENABLE_FREEDV_CONTROL
            if (ctl_diagnostic_input_blocked()) knob->diagnostic_generation = ctl_remote_input_generation();
#endif
            bool woke = backlight_knob_tick();     /* the turn that ends the screensaver only ends it */

            ctl_local_input();
            if (!woke) {
                pending                = sum;
                data->continue_reading = true;
            }
        }
    } else if (key_down) {
        data->state            = LV_INDEV_STATE_RELEASED;
        data->continue_reading = true;
    } else {
        int32_t step = pending > 0 ? 1 : -1;     /* positive counts are turns to the left */

        pending -= step;
        data->state            = LV_INDEV_STATE_PRESSED;
        data->key              = step > 0 ? knob->left[knob->state] : knob->right[knob->state];
        data->continue_reading = true;
    }
    key_down = data->state == LV_INDEV_STATE_PRESSED;
}

rotary_t *rotary_init(char *dev_name) {
    int fd = open(dev_name, O_RDWR | O_NOCTTY | O_NDELAY);

    if (fd == -1) {
        LV_LOG_ERROR("unable to open rotary interface:");
        return NULL;
    }
    fcntl(fd, F_SETFL, O_ASYNC | O_NONBLOCK);

    rotary_t *knob = calloc(1, sizeof(*knob));

#ifdef ENABLE_FREEDV_CONTROL
    if (diagnostic_rotary_count == sizeof(diagnostic_rotaries)/sizeof(diagnostic_rotaries[0])) {
        close(fd); free(knob); return NULL;
    }
    diagnostic_rotaries[diagnostic_rotary_count++] = knob;
#endif
    knob->fd = fd;
    lv_indev_drv_init(&knob->indev_drv);
    knob->indev_drv.type      = LV_INDEV_TYPE_KEYPAD;
    knob->indev_drv.read_cb   = vol_read;
    knob->indev_drv.user_data = knob;
    knob->indev = lv_indev_drv_register(&knob->indev_drv);
    lv_indev_set_group(knob->indev, keyboard_group);
    return knob;
}

/* Main knob: a thread waits for the detents and timestamps them as they come, so the
 * speed (ms per detent) does not depend on how busy the GUI loop is (upstream ae2af22) */

#define TURNS_MAX 16

static struct {
    int             fd;
    rotary_data_t   turns[TURNS_MAX];     /* a ring: the thread writes, the GUI reads */
    uint8_t         head, tail;
    pthread_mutex_t lock;
    lv_indev_drv_t  drv;
#ifdef ENABLE_FREEDV_CONTROL
    int reset_fd;
    atomic_uint reset_requested;
    unsigned reset_ack,reset_failed;
    bool reset_ok, reader_started;
    uint64_t reset_started_ms, reset_ack_ms;
#endif
#ifdef ENABLE_FREEDV_VOICE
    lv_indev_t *indev;
    unsigned voice_generation;
#endif
} knob_main = {.fd=-1, .lock = PTHREAD_MUTEX_INITIALIZER
#ifdef ENABLE_FREEDV_CONTROL
    , .reset_fd=-1, .reset_ok=true
#endif
};

#ifdef ENABLE_FREEDV_CONTROL
static uint64_t rotary_reset_ms(void) {
    struct timespec now;
    clock_gettime(CLOCK_MONOTONIC, &now);
    return (uint64_t)now.tv_sec * 1000 + now.tv_nsec / 1000000;
}

/* The producer is the sole reader of the main knob, including boundary drains.
 * Reset requests invalidate a batch before waking it; publication checks the
 * same epoch under the ring lock, so a pre-boundary batch can never reappear. */
static bool main_knob_reset(struct timespec *last) {
    unsigned request=atomic_load(&knob_main.reset_requested);
    pthread_mutex_lock(&knob_main.lock);
    bool needed=knob_main.reset_ack!=request || !knob_main.reset_ok;
    pthread_mutex_unlock(&knob_main.lock);
    if(!needed)return false;
    uint64_t wake;
    while(read(knob_main.reset_fd,&wake,sizeof(wake))>0) {}
    bool ok=fd_diagnostic_drain_input(knob_main.fd);
    *last=(struct timespec){0};
    pthread_mutex_lock(&knob_main.lock);
    knob_main.head=knob_main.tail=0;
    knob_main.reset_ok=ok;
    if(!ok)knob_main.reset_failed=request;
    knob_main.reset_ack=request;knob_main.reset_ack_ms=rotary_reset_ms();
    pthread_mutex_unlock(&knob_main.lock);
    gui_wake();
    return true;
}
#endif

static void *main_knob_thread(void *arg) {
    (void)arg;
    struct pollfd pfd[] = {{.fd=knob_main.fd,.events=POLLIN}
#ifdef ENABLE_FREEDV_CONTROL
        , {.fd=knob_main.reset_fd,.events=POLLIN}
#endif
    };
    struct input_event in;
    struct timespec last={0},now;

    for (;;) {
#ifdef ENABLE_FREEDV_CONTROL
        if(main_knob_reset(&last))continue;
#endif
        if(poll(pfd,sizeof(pfd)/sizeof(pfd[0]),-1)<=0)continue;
#ifdef ENABLE_FREEDV_CONTROL
        if(main_knob_reset(&last))continue;
        unsigned epoch=atomic_load(&knob_main.reset_requested);
#endif
        if(!(pfd[0].revents&POLLIN))continue;
        int16_t detents=0;
        /* Native reads and acceleration calculation stay outside the ring
         * mutex. The bounded batch also lets a flooded input yield to reset. */
        for(unsigned n=0;n<4096 && read(knob_main.fd,&in,sizeof(in))>0;++n)
            if(in.type==EV_REL)detents+=in.value;
#ifdef KNOB_DEVICE_TEST_BATCH
        KNOB_DEVICE_TEST_BATCH();
#endif
        if(!detents)continue;
        clock_gettime(CLOCK_MONOTONIC,&now);
        float ms=(now.tv_sec-last.tv_sec)*1000.0f+(now.tv_nsec-last.tv_nsec)*1e-6f;
        last=now;
        ms=LV_MIN(ms,500.0f)/LV_ABS(detents);
        pthread_mutex_lock(&knob_main.lock);
#ifdef ENABLE_FREEDV_CONTROL
        bool current=epoch==atomic_load(&knob_main.reset_requested) && epoch==knob_main.reset_ack && knob_main.reset_ok;
#else
        bool current=true;
#endif
        uint8_t next=(knob_main.head+1)%TURNS_MAX;
        if(current && next!=knob_main.tail){
            knob_main.turns[knob_main.head]=(rotary_data_t){.diff=detents,.dt=LV_MAX((uint16_t)ms,1)};
            knob_main.head=next;
        }
        pthread_mutex_unlock(&knob_main.lock);
        gui_wake_input();
    }
    return NULL;
}

/* Everything queued since the last GUI loop becomes one event, so a fast turn moves the
 * frequency once per frame: the detents summed, the time per detent averaged over them.
 * A change of direction ends the batch */
static void main_knob_read(lv_indev_drv_t *drv, lv_indev_data_t *data) {
    int32_t  sum = 0;
    uint32_t count = 0, ms = 0;

    pthread_mutex_lock(&knob_main.lock);
    while (knob_main.tail != knob_main.head) {
        rotary_data_t *t = &knob_main.turns[knob_main.tail];

        if (sum && (t->diff > 0) != (sum > 0)) {
            data->continue_reading = true;
            break;
        }
        sum   += t->diff;
        count += LV_ABS(t->diff);
        ms    += t->dt * LV_ABS(t->diff);
        knob_main.tail = (knob_main.tail + 1) % TURNS_MAX;
    }
    pthread_mutex_unlock(&knob_main.lock);

    if (sum && !backlight_knob_tick()) {      /* the turn that ends the screensaver only ends it */
#ifdef ENABLE_FREEDV_VOICE
        knob_main.voice_generation=ctl_diagnostic_input_blocked()?ctl_remote_input_generation():0;
#endif
        rotary_data_t *ev = malloc(sizeof(*ev));

        ev->diff = sum;
        ev->dt   = LV_MAX(ms / count, 1);
        lv_event_send(lv_scr_act(), EVENT_ROTARY, ev);
    }
}

void rotary_main_init(char *dev_name) {
    pthread_t thread;

    knob_main.fd = open(dev_name, O_RDWR | O_NOCTTY | O_NDELAY);
    if (knob_main.fd == -1) {
        LV_LOG_ERROR("unable to open main knob");
        return;
    }
    fcntl(knob_main.fd, F_SETFL, O_NONBLOCK);
#ifdef ENABLE_FREEDV_CONTROL
    knob_main.reset_fd=eventfd(0,EFD_CLOEXEC|EFD_NONBLOCK);
#endif
    if(pthread_create(&thread,NULL,main_knob_thread,NULL)){
        close(knob_main.fd);knob_main.fd=-1;
#ifdef ENABLE_FREEDV_CONTROL
        if(knob_main.reset_fd>=0)close(knob_main.reset_fd);
        knob_main.reset_fd=-1;
#endif
        LV_LOG_ERROR("unable to start main knob reader");return;
    }
#ifdef ENABLE_FREEDV_CONTROL
    knob_main.reader_started=true;
#endif
    pthread_detach(thread);

    lv_indev_drv_init(&knob_main.drv);
    knob_main.drv.type    = LV_INDEV_TYPE_KEYPAD;
    knob_main.drv.read_cb = main_knob_read;
    lv_indev_t *input=lv_indev_drv_register(&knob_main.drv);
#ifdef ENABLE_FREEDV_VOICE
    knob_main.indev=input;
#endif
    lv_indev_set_group(input, keyboard_group);
}

#ifdef ENABLE_FREEDV_CONTROL
#ifdef ENABLE_FREEDV_VOICE
bool rotary_voice_local_event(lv_indev_t *input,unsigned generation) {
    return input==knob_main.indev && knob_main.voice_generation==generation;
}
#endif
bool rotary_diagnostic_reset_begin(void) {
#ifdef ENABLE_FREEDV_VOICE
    knob_main.voice_generation=0;
#endif
    bool ok=true;
    pending=0;key_down=false;
    for(unsigned i=0;i<diagnostic_rotary_count;++i){
        diagnostic_rotaries[i]->diagnostic_generation=0;
        if(!fd_diagnostic_drain_input(diagnostic_rotaries[i]->fd))ok=false;
    }
    pthread_mutex_lock(&knob_main.lock);
    unsigned request=atomic_load(&knob_main.reset_requested);
    if(request==UINT_MAX){pthread_mutex_unlock(&knob_main.lock);return false;}
    ++request;atomic_store(&knob_main.reset_requested,request);
    knob_main.head=knob_main.tail=0;
    knob_main.reset_started_ms=rotary_reset_ms();
    bool absent=knob_main.fd<0;
    if(absent){knob_main.reset_ack=request;knob_main.reset_ok=true;knob_main.reset_ack_ms=knob_main.reset_started_ms;}
    bool available=absent || (knob_main.reader_started && knob_main.reset_fd>=0);
    pthread_mutex_unlock(&knob_main.lock);
    if(!absent && available){uint64_t wake=1;if(write(knob_main.reset_fd,&wake,sizeof(wake))<0 && errno!=EAGAIN)available=false;}
    return ok && available;
}
rotary_reset_result rotary_diagnostic_reset_poll(void) {
    pthread_mutex_lock(&knob_main.lock);
    unsigned request=atomic_load(&knob_main.reset_requested);
    bool done=knob_main.reset_ack==request,ok=knob_main.reset_ok,failed=knob_main.reset_failed==request;
    uint64_t started=knob_main.reset_started_ms,acknowledged=knob_main.reset_ack_ms;
    pthread_mutex_unlock(&knob_main.lock);
    if(failed)return ROTARY_RESET_FAILED;
    if(done)return ok && acknowledged-started<=100?ROTARY_RESET_READY:ROTARY_RESET_FAILED;
    return rotary_reset_ms()-started>=100?ROTARY_RESET_FAILED:ROTARY_RESET_PENDING;
}
bool rotary_diagnostic_reset(void) {
    if(!rotary_diagnostic_reset_begin())return false;
    rotary_reset_result result;
    while((result=rotary_diagnostic_reset_poll())==ROTARY_RESET_PENDING){struct timespec wait={.tv_nsec=1000000};nanosleep(&wait,NULL);}
    return result==ROTARY_RESET_READY;
}

bool rotary_diagnostic_local_event(lv_indev_t *input, unsigned generation) {
    for (unsigned i = 0; i < diagnostic_rotary_count; ++i)
        if (diagnostic_rotaries[i]->indev == input && diagnostic_rotaries[i]->diagnostic_generation == generation) return true;
    return false;
}
#endif
