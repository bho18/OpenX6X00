// SPDX-License-Identifier: LGPL-2.1-or-later
#pragma once
#include <stdbool.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* Destinations are copied values. No input event or LVGL widget survives in
 * this mailbox. DIALOG values name process-lifetime dialog descriptors. */
typedef enum {
    FD_NAV_NONE, FD_NAV_HOME, FD_NAV_PAGE, FD_NAV_APP, FD_NAV_ACTION,
    FD_NAV_MODE_KEY, FD_NAV_DIALOG, FD_NAV_THEME, FD_NAV_WALKIE,
    FD_NAV_POWEROFF, FD_NAV_RESET, FD_NAV_TERMINATE
} fd_nav_kind;
typedef struct {
    fd_nav_kind kind;
    int32_t value, detail;
    char text[64];
} fd_nav_action;
typedef struct {
    bool pending, replaying;
    fd_nav_action action;
} fd_nav_state;
typedef enum { FD_NAV_REJECTED, FD_NAV_STARTED, FD_NAV_UPDATED, FD_NAV_UNCHANGED } fd_nav_offer;

bool fd_nav_is_terminal(fd_nav_kind kind);
fd_nav_offer fd_nav_offer_action(fd_nav_state *, const fd_nav_action *);
bool fd_nav_take_action(fd_nav_state *, fd_nav_action *);
void fd_nav_replay_finished(fd_nav_state *);
typedef struct { bool held; unsigned generation; } fd_nav_exit_latch;
typedef enum { FD_NAV_EXIT_PASS, FD_NAV_EXIT_CONSUME, FD_NAV_EXIT_LEAVE } fd_nav_exit_result;
/* Called only for the physical F5 key. Long/repeat/release of an accepted
 * press and later presses during cleanup are consumed. */
fd_nav_exit_result fd_nav_exit_key(fd_nav_exit_latch *, bool press, bool release,
    bool enabled, bool finishing, bool local, unsigned generation);

#if defined(ENABLE_FREEDV_USB_VOICE) || defined(ENABLE_FREEDV_USB_TEST)
bool freedv_navigation_request(fd_nav_kind kind, int32_t value, int32_t detail, const char *text);
bool freedv_navigation_defer_dialog(void *descriptor);
bool freedv_navigation_terminal(fd_nav_kind kind);
bool freedv_navigation_pending(void);
bool freedv_navigation_replaying(void);
void freedv_navigation_completed(void);
void freedv_navigation_tick(void);
/* Signal handlers only set a sig_atomic_t flag; process it on the GUI thread. */
void freedv_navigation_signal_terminate(void);
/* The main screen owns action execution after all FreeDV ownership is gone. */
void freedv_navigation_dispatch(const fd_nav_action *);
void *freedv_navigation_dialog_descriptor(int32_t handle);
#else
static inline bool freedv_navigation_request(fd_nav_kind k,int32_t v,int32_t d,const char *t){(void)k;(void)v;(void)d;(void)t;return false;}
static inline bool freedv_navigation_defer_dialog(void *d){(void)d;return false;}
static inline bool freedv_navigation_terminal(fd_nav_kind k){(void)k;return false;}
static inline bool freedv_navigation_pending(void){return false;}
static inline bool freedv_navigation_replaying(void){return false;}
static inline void freedv_navigation_completed(void){}
static inline void freedv_navigation_tick(void){}
static inline void freedv_navigation_signal_terminate(void){}
#endif

#ifdef __cplusplus
}
#endif
