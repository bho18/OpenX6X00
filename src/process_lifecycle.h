// SPDX-License-Identifier: LGPL-2.1-or-later
#pragma once
#include <stdbool.h>

#ifdef __cplusplus
extern "C" {
#endif

/* Called once, before creating ANY worker. One minimal process signal handler
 * publishes TERM/INT to a sleeping coordinator through a nonblocking eventfd.
 * Worker/exec-child signal masks retain upstream behavior. A sigwait design
 * would leak blocked TERM/INT into the app's existing system/popen children.
 * active/request_cleanup may run before the GUI or radio is initialized.
 * request_cleanup and abort_requested must only publish lock-free requests.
 * bypass must be bounded and must not depend on the GUI/audio/storage locks. */
typedef struct {
    bool (*active)(void *);
    void (*request_cleanup)(void *);
    void (*abort_requested)(void *);
    void (*bypass)(void *);
    void *context;
    /* NULL disables the diagnostic journal. Production uses the fixed /tmp
     * path; it survives one GUI restart, not a radio reboot/power loss. */
    const char *journal_path;
} process_lifecycle_ops;

#define PROCESS_LIFECYCLE_JOURNAL_PATH "/tmp/x6200-process-lifecycle.bin"

bool process_lifecycle_init(const process_lifecycle_ops *ops);
bool process_lifecycle_stopping(void);
bool process_lifecycle_aborting(void);
bool process_lifecycle_incomplete(void);
/* Normal GUI path: bind the exact audio session basename before its workers
 * start. No-op without an initialized journal. Not a deadline/handler API. */
void process_lifecycle_set_session(const char *session_id);
/* GUI acknowledges only after normal restoration and finalization. */
void process_lifecycle_cleanup_complete(void);
/* Trusted GUI path only: the caller has stopped USB streams, restored ordinary
 * radio state and observed fresh RX. A timeout alone never authorizes this.
 * The daemon's established code-3 path recreates the GUI and normal PA streams.
 * Ownership may remain held until process death if an audio worker is stuck. */
bool process_lifecycle_request_audio_restart(void);

#ifdef __cplusplus
}
#endif
