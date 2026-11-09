// SPDX-License-Identifier: LGPL-2.1-or-later
#define ENABLE_FREEDV_VOICE 1
#include "src/freedv/main_audio_recovery.h"
#include <assert.h>
#include <stdio.h>

int main(void) {
    fd_main_recovery r;
    fd_main_recovery_begin(&r,true,1000);
    assert(fd_main_recovery_step(&r,1000,false,false,AUDIO_MAIN_FAILED)==FD_MAIN_RECOVERY_START);
    assert(r.attempts==1 && r.quiet);
    assert(fd_main_recovery_step(&r,1500,false,true,AUDIO_MAIN_TIMED_OUT)==FD_MAIN_RECOVERY_WAIT);
    assert(fd_main_recovery_step(&r,1599,false,false,AUDIO_MAIN_FAILED)==FD_MAIN_RECOVERY_WAIT);
    assert(fd_main_recovery_step(&r,1600,false,false,AUDIO_MAIN_FAILED)==FD_MAIN_RECOVERY_START);
    assert(r.attempts==2);
    assert(fd_main_recovery_step(&r,1650,false,true,AUDIO_MAIN_ACKNOWLEDGED)==FD_MAIN_RECOVERY_ACKNOWLEDGED);

    fd_main_recovery_begin(&r,false,10000);
    assert(fd_main_recovery_step(&r,10000,false,false,AUDIO_MAIN_FAILED)==FD_MAIN_RECOVERY_START);
    assert(fd_main_recovery_step(&r,10001,false,true,AUDIO_MAIN_FAILED)==FD_MAIN_RECOVERY_WAIT);
    assert(fd_main_recovery_step(&r,10101,false,false,AUDIO_MAIN_FAILED)==FD_MAIN_RECOVERY_START);
    assert(fd_main_recovery_step(&r,10102,false,true,AUDIO_MAIN_FAILED)==FD_MAIN_RECOVERY_FAILED);
    assert(r.attempts==2 && !r.quiet);

    fd_main_recovery_begin(&r,false,20000);
    assert(fd_main_recovery_step(&r,20000,false,false,AUDIO_MAIN_FAILED)==FD_MAIN_RECOVERY_START);
    assert(fd_main_recovery_step(&r,22499,true,false,AUDIO_MAIN_FAILED)==FD_MAIN_RECOVERY_WAIT);
    assert(fd_main_recovery_step(&r,22500,true,false,AUDIO_MAIN_FAILED)==FD_MAIN_RECOVERY_STALLED);
    assert(fd_main_recovery_step(&r,22500,false,true,AUDIO_MAIN_ACKNOWLEDGED)==FD_MAIN_RECOVERY_FAILED);

    fd_main_recovery_begin(&r,true,30000);
    assert(fd_main_recovery_step(&r,30000,false,false,AUDIO_MAIN_FAILED)==FD_MAIN_RECOVERY_START);
    assert(fd_main_recovery_step(&r,30001,false,true,AUDIO_MAIN_LOST)==FD_MAIN_RECOVERY_FAILED);
    assert(r.attempts==1); /* no in-process reconnect or pointless lost-stream retry */
    puts("PASS: two bounded operations, retry backoff, lost context, and live-worker deadline");
}
