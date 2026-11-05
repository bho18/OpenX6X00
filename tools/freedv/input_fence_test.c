// SPDX-License-Identifier: LGPL-2.1-or-later
#define _POSIX_C_SOURCE 200809L
#include "src/control_owner.h"
#include "src/radio.h"
#include "src/freedv/input_drain.h"
#include <assert.h>
#include <pthread.h>
#include <stdatomic.h>
#include <fcntl.h>
#include <stdio.h>
#include <unistd.h>
#include <errno.h>
#include <sched.h>
static pthread_mutex_t mutex = PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t changed = PTHREAD_COND_INITIALIZER;
static bool admitted, release_writer;
static atomic_bool fence_attempted, fence_finished;
static unsigned queued_power_generation;
radio_state_t radio_get_state(void) { return RADIO_RX; }
static void *remote_writer(void *unused) {
    (void)unused;
    assert(ctl_remote_input_begin(&queued_power_generation));
    pthread_mutex_lock(&mutex); admitted=true; pthread_cond_broadcast(&changed);
    while(!release_writer) pthread_cond_wait(&changed,&mutex);
    pthread_mutex_unlock(&mutex);
    ctl_remote_input_end(); return NULL;
}
static void *fence(void *unused) {
    (void)unused; atomic_store(&fence_attempted,true);
    assert(ctl_diagnostic_input_block()); atomic_store(&fence_finished,true); return NULL;
}
int main(void) {
    pthread_t writer, fencer;
    assert(!pthread_create(&writer,NULL,remote_writer,NULL));
    pthread_mutex_lock(&mutex);while(!admitted)pthread_cond_wait(&changed,&mutex);pthread_mutex_unlock(&mutex);
    assert(!pthread_create(&fencer,NULL,fence,NULL));
    while(!atomic_load(&fence_attempted)) sched_yield();
    assert(!atomic_load(&fence_finished)); /* earlier write still admitted */
    pthread_mutex_lock(&mutex);release_writer=true;pthread_cond_broadcast(&changed);pthread_mutex_unlock(&mutex);
    assert(!pthread_join(writer,NULL));assert(!pthread_join(fencer,NULL));
    assert(ctl_diagnostic_input_blocked());
    assert(queued_power_generation!=ctl_remote_input_generation());
    assert(!ctl_remote_input_begin(NULL));
    assert(!ctl_diagnostic_input_block()); /* no accidental nested owner */
    int fds[2];assert(!pipe(fds));assert(fcntl(fds[0],F_SETFL,O_NONBLOCK)>=0);
    struct input_event events[2]={{.type=EV_KEY,.code=BTN_TRIGGER_HAPPY27,.value=1},{.type=EV_KEY,.code=BTN_TRIGGER_HAPPY27,.value=0}};
    assert(write(fds[1],events,sizeof(events))==(ssize_t)sizeof(events));
    assert(fd_diagnostic_drain_input(fds[0]));
    struct input_event event;assert(read(fds[0],&event,sizeof(event))==-1&&errno==EAGAIN);
    assert(write(fds[1],events,1)==1);assert(!fd_diagnostic_drain_input(fds[0])); /* malformed prefix refuses consent */
    ctl_diagnostic_input_unblock();assert(!ctl_diagnostic_input_blocked());
    unsigned generation;assert(ctl_remote_input_begin(&generation));ctl_remote_input_end();
    assert(generation!=queued_power_generation); /* old queued power work stays invalid after release */
    unsigned released=generation;ctl_diagnostic_input_unblock();assert(ctl_remote_input_generation()==released);
    close(fds[0]);close(fds[1]);
    pthread_cond_destroy(&changed);pthread_mutex_destroy(&mutex);
    puts("PASS: admitted-write fence, blocked injection, stale queued power generation and input drain");
}
