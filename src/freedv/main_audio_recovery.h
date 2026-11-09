// SPDX-License-Identifier: LGPL-2.1-or-later
#pragma once
#include "../audio.h"
#include <stdbool.h>
#include <stdint.h>

typedef enum {
    FD_MAIN_RECOVERY_WAIT,
    FD_MAIN_RECOVERY_START,
    FD_MAIN_RECOVERY_ACKNOWLEDGED,
    FD_MAIN_RECOVERY_FAILED,
    FD_MAIN_RECOVERY_STALLED
} fd_main_recovery_action;
typedef struct {
    uint64_t started_ms, retry_ms;
    unsigned attempts;
    bool quiet;
} fd_main_recovery;

static inline void fd_main_recovery_begin(fd_main_recovery *r, bool quiet, uint64_t now) {
    *r = (fd_main_recovery){.started_ms=now,.quiet=quiet};
}
/* `completed` means the one worker has returned and has been joined. A live
 * worker is never overwritten or abandoned by this policy. */
static inline fd_main_recovery_action fd_main_recovery_step(fd_main_recovery *r, uint64_t now,
                                                           bool running, bool completed,
                                                           audio_main_quiet_result_t result) {
    if (now - r->started_ms >= 2500)
        return running ? FD_MAIN_RECOVERY_STALLED : FD_MAIN_RECOVERY_FAILED;
    if (completed) {
        if (result == AUDIO_MAIN_ACKNOWLEDGED) return FD_MAIN_RECOVERY_ACKNOWLEDGED;
        if (result == AUDIO_MAIN_LOST || r->attempts >= 2) return FD_MAIN_RECOVERY_FAILED;
        r->retry_ms = now + 100;
    }
    if (running || now < r->retry_ms) return FD_MAIN_RECOVERY_WAIT;
    if (r->attempts >= 2) return FD_MAIN_RECOVERY_FAILED;
    ++r->attempts;
    return FD_MAIN_RECOVERY_START;
}
