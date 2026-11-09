// SPDX-License-Identifier: LGPL-2.1-or-later
#include "../../src/process_lifecycle.h"
#include <assert.h>
#include <stdio.h>
#include <string.h>
static process_lifecycle_ops captured;
static unsigned cleanup_calls,abort_calls,bypass_calls;
static bool usb_active;
bool process_lifecycle_init(const process_lifecycle_ops *ops){captured=*ops;return true;}
bool freedv_usb_active(void){return usb_active;}
void freedv_navigation_signal_terminate(void){++cleanup_calls;}
void freedv_usb_deadline_abort(void){++abort_calls;}
void wr12_shutdown_bypass(void){++bypass_calls;}
/* This is the exact callback installation used by gui_main.c, not a copied
 * test definition. The actual publisher/worker contract has its own test. */
#include "../../src/process_lifecycle_gui.inc"
int main(void){
    assert(lifecycle_initialize());assert(!strcmp(captured.journal_path,PROCESS_LIFECYCLE_JOURNAL_PATH));
    assert(!captured.active(captured.context));usb_active=true;
#if defined(ENABLE_FREEDV_USB_VOICE) || defined(ENABLE_FREEDV_USB_TEST)
    assert(captured.active(captured.context));
#else
    assert(!captured.active(captured.context));
#endif
    captured.request_cleanup(captured.context);captured.abort_requested(captured.context);captured.bypass(captured.context);
    assert(cleanup_calls==1&&abort_calls==1&&bypass_calls==1);
    puts("PASS: production GUI installs lifecycle journal, graceful cleanup, lock-free USB abort publisher and WR12 bypass");
    return 0;
}
