// SPDX-License-Identifier: LGPL-2.1-or-later
#include "src/remote_server.c"
#include <assert.h>
static scheduler_fn_t queued_fn;
static unsigned char queued_arg[64];
static unsigned jobs, power_calls;
static bool last_power;
radio_state_t radio_get_state(void) { return RADIO_RX; }
void keypad_power_set(bool down) { ++power_calls; last_power=down; }
bool scheduler_put_urgent(scheduler_fn_t fn,void *arg,size_t size) {
    assert(size<=sizeof(queued_arg));queued_fn=fn;memcpy(queued_arg,arg,size);++jobs;return true;
}
static void run_queued(void) { assert(queued_fn); queued_fn(queued_arg); }
int main(void) {
    int fds[2];assert(!pipe(fds));assert(fcntl(fds[0],F_SETFL,O_NONBLOCK)>=0);
    dev_keypad=dev_mfk=fds[1];dev_power=-1;
    key_emit(BTN_TRIGGER_HAPPY27,true);
    struct input_event events[2];assert(read(fds[0],events,sizeof(events))==(ssize_t)sizeof(events));
    assert(events[0].type==EV_KEY&&events[0].value==1);
    key_emit(KEY_POWER,true);assert(jobs==1);
    assert(ctl_diagnostic_input_block());
    key_emit(BTN_TRIGGER_HAPPY27,false);assert(!knob_emit("mfk",1));
    assert(read(fds[0],events,sizeof(events))==-1&&errno==EAGAIN);
    power_post();assert(jobs==1);
    run_queued();assert(!power_calls); /* pending before fence cannot execute while fenced */
    ctl_diagnostic_input_unblock();
    run_queued();assert(!power_calls); /* nor after fence releases: generation is stale */
    power_post();assert(jobs==1); /* old server-side power retry cannot resurrect it */
    key_emit(KEY_POWER,true);assert(jobs==2);run_queued();assert(power_calls==1&&last_power);
    assert(knob_emit("mfk",1));assert(read(fds[0],events,sizeof(events))==(ssize_t)sizeof(events));
    assert(events[0].type==EV_REL);
    close(fds[0]);close(fds[1]);
    puts("PASS: production remote key/knob admission, queued power invalidation and retry generation");
}
