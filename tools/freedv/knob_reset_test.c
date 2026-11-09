// SPDX-License-Identifier: LGPL-2.1-or-later
#define _GNU_SOURCE
#include <assert.h>
#include <stdatomic.h>
#include <semaphore.h>
#include <pthread.h>
#include <unistd.h>
#include <linux/input.h>
#include <stdlib.h>
#include <stdio.h>
#include <time.h>
static void fixture_batch(void);
static ssize_t fixture_read(int fd,void *data,size_t size);
#define KNOB_DEVICE_TEST_BATCH() fixture_batch()
#define read fixture_read
#include "src/knob_device.c"
#undef read
static sem_t batch_read,allow_publish;
static atomic_bool gate_batch,flood;
static int writer;
static rotary_data_t received;
static unsigned delivered;
lv_group_t *keyboard_group;
uint32_t EVENT_ROTARY=11;
static lv_obj_t screen;
void gui_wake(void) {}
void gui_wake_input(void) {}
bool backlight_knob_tick(void) {return false;}
bool ctl_diagnostic_input_blocked(void) {return false;}
unsigned ctl_remote_input_generation(void) {return 1;}
lv_disp_t *lv_disp_get_default(void) {return NULL;}
lv_obj_t *lv_disp_get_scr_act(lv_disp_t *unused) {(void)unused;return &screen;}
lv_res_t lv_event_send(lv_obj_t *object,lv_event_code_t code,void *param) {
    assert(object==&screen && code==EVENT_ROTARY);received=*(rotary_data_t *)param;++delivered;free(param);return LV_RES_OK;
}
static ssize_t fixture_read(int fd,void *data,size_t size) {
    if(fd==knob_main.fd){
        /* Neither ordinary reads nor boundary drains own the publication lock. */
        assert(!pthread_mutex_lock(&knob_main.lock));pthread_mutex_unlock(&knob_main.lock);
        if(atomic_load(&flood)){assert(size==sizeof(struct input_event));*(struct input_event *)data=(struct input_event){.type=EV_REL,.value=1};return size;}
    }
    return read(fd,data,size);
}
static void fixture_batch(void) {
    if(!atomic_load(&gate_batch))return;
    sem_post(&batch_read);
    while(sem_wait(&allow_publish) && errno==EINTR){}
}
static void wait_batch(void) {struct timespec end;clock_gettime(CLOCK_REALTIME,&end);end.tv_sec+=2;assert(!sem_timedwait(&batch_read,&end));}
static void turn(int value) {struct input_event event={.type=EV_REL,.value=value};assert(write(writer,&event,sizeof(event))==sizeof(event));}
static rotary_reset_result wait_reset(void) {
    rotary_reset_result r;while((r=rotary_diagnostic_reset_poll())==ROTARY_RESET_PENDING)usleep(500);return r;
}
static void wait_ring(void) {
    for(unsigned n=0;n<2000;++n){pthread_mutex_lock(&knob_main.lock);bool ready=knob_main.head!=knob_main.tail;pthread_mutex_unlock(&knob_main.lock);if(ready)return;usleep(500);}assert(!"producer did not publish");
}
int main(void) {
    pthread_mutexattr_t lock_type;assert(!pthread_mutexattr_init(&lock_type));
    assert(!pthread_mutexattr_settype(&lock_type,PTHREAD_MUTEX_ERRORCHECK));
    assert(!pthread_mutex_destroy(&knob_main.lock));assert(!pthread_mutex_init(&knob_main.lock,&lock_type));pthread_mutexattr_destroy(&lock_type);
    int input[2];assert(!pipe(input));writer=input[1];knob_main.fd=input[0];assert(!fcntl(input[0],F_SETFL,O_NONBLOCK));
    knob_main.reset_fd=eventfd(0,EFD_CLOEXEC|EFD_NONBLOCK);assert(knob_main.reset_fd>=0);knob_main.reader_started=true;
    assert(!sem_init(&batch_read,0,0) && !sem_init(&allow_publish,0,0));
    atomic_store(&gate_batch,true);pthread_t producer;assert(!pthread_create(&producer,NULL,main_knob_thread,NULL));
    turn(7);wait_batch();assert(rotary_diagnostic_reset_begin());assert(rotary_diagnostic_reset_poll()==ROTARY_RESET_PENDING);
    atomic_store(&gate_batch,false);sem_post(&allow_publish);assert(wait_reset()==ROTARY_RESET_READY);
    assert(knob_main.head==knob_main.tail);lv_indev_data_t data={0};main_knob_read(NULL,&data);assert(!delivered);
    turn(2);wait_ring();main_knob_read(NULL,&data);assert(delivered==1 && received.diff==2 && received.dt==250);
    /* A flooded device must deny reset, then recover without changing readers. */
    atomic_store(&gate_batch,true);turn(1);wait_batch();atomic_store(&flood,true);assert(rotary_diagnostic_reset_begin());
    atomic_store(&gate_batch,false);sem_post(&allow_publish);assert(wait_reset()==ROTARY_RESET_FAILED);
    atomic_store(&flood,false);
    /* Cancelling denied entry must not orphan normal tuning: the producer
     * reaches quiet on its own, while the failed request remains failed. */
    for(unsigned n=0;n<2000;++n){pthread_mutex_lock(&knob_main.lock);bool quiet=knob_main.reset_ok;pthread_mutex_unlock(&knob_main.lock);if(quiet)break;usleep(500);}
    assert(rotary_diagnostic_reset_poll()==ROTARY_RESET_FAILED);
    assert(knob_main.head==knob_main.tail);turn(2);wait_ring();main_knob_read(NULL,&data);assert(delivered==2 && received.diff==2);
    assert(rotary_diagnostic_reset_begin());assert(wait_reset()==ROTARY_RESET_READY);
    /* A stalled producer never blocks the GUI or silently acknowledges late. */
    atomic_store(&gate_batch,true);turn(1);wait_batch();assert(rotary_diagnostic_reset_begin());usleep(120000);
    assert(rotary_diagnostic_reset_poll()==ROTARY_RESET_FAILED);
    assert(rotary_diagnostic_reset_begin());atomic_store(&gate_batch,false);sem_post(&allow_publish);assert(wait_reset()==ROTARY_RESET_READY);
    assert(knob_main.head==knob_main.tail);turn(-2);wait_ring();main_knob_read(NULL,&data);
    assert(delivered==3 && received.diff==-2 && received.dt==250);
    assert(!pthread_cancel(producer));assert(!pthread_join(producer,NULL));
    close(input[0]);close(input[1]);close(knob_main.reset_fd);sem_destroy(&batch_read);sem_destroy(&allow_publish);
    puts("PASS: producer-owned asynchronous resets reject in-flight batches, flood and timeout, recover, and preserve native detents/acceleration without I/O under publication mutex");
}
