// SPDX-License-Identifier: LGPL-2.1-or-later
#define _POSIX_C_SOURCE 200809L
#include "usb_navigation.h"
#include <string.h>

bool fd_nav_is_terminal(fd_nav_kind kind){
    return kind==FD_NAV_POWEROFF || kind==FD_NAV_RESET || kind==FD_NAV_TERMINATE;
}
static unsigned terminal_priority(fd_nav_kind kind){
    return kind==FD_NAV_POWEROFF?3:kind==FD_NAV_TERMINATE?2:kind==FD_NAV_RESET?1:0;
}
fd_nav_offer fd_nav_offer_action(fd_nav_state *state,const fd_nav_action *action){
    if(!state || !action || action->kind<=FD_NAV_NONE || action->kind>FD_NAV_TERMINATE || state->replaying)return FD_NAV_REJECTED;
    if(state->pending && terminal_priority(state->action.kind)>terminal_priority(action->kind))return FD_NAV_UNCHANGED;
    if(state->pending && state->action.kind==action->kind && state->action.value==action->value &&
       state->action.detail==action->detail && !strncmp(state->action.text,action->text,sizeof(action->text)))return FD_NAV_UNCHANGED;
    bool was_pending=state->pending;state->action=*action;state->action.text[sizeof(state->action.text)-1]=0;state->pending=true;
    return was_pending?FD_NAV_UPDATED:FD_NAV_STARTED;
}
bool fd_nav_take_action(fd_nav_state *state,fd_nav_action *action){
    if(!state || !action || !state->pending || state->replaying)return false;
    *action=state->action;state->pending=false;state->replaying=true;return true;
}
void fd_nav_replay_finished(fd_nav_state *state){if(state)state->replaying=false;}
fd_nav_exit_result fd_nav_exit_key(fd_nav_exit_latch *latch,bool press,bool release,
    bool enabled,bool finishing,bool local,unsigned generation){
    if(!latch)return FD_NAV_EXIT_PASS;
    if(latch->held && latch->generation!=generation)latch->held=false;
    if(latch->held){if(release)latch->held=false;return FD_NAV_EXIT_CONSUME;}
    if(!press || !enabled)return FD_NAV_EXIT_PASS;
    if(!local)return FD_NAV_EXIT_CONSUME;
    latch->held=true;latch->generation=generation;
    /* Cleanup is already in progress. A later press must not interrupt the
     * accepted speech tail or bypass the normal RX/ownership restoration. */
    return finishing?FD_NAV_EXIT_CONSUME:FD_NAV_EXIT_LEAVE;
}

#if defined(ENABLE_FREEDV_USB_VOICE) || defined(ENABLE_FREEDV_USB_TEST)
#include "usb_voice.h"
#include "usb_ui.h"
#include "../front_keys.h"
#include "../scheduler.h"
#include "../process_lifecycle.h"
#include <signal.h>
#include <stdatomic.h>
#include <stdio.h>
#include <time.h>

static fd_nav_state navigation;
static void *dialog_descriptors[64];
static unsigned dialog_count;
static atomic_int terminal_queued;
static atomic_int terminal_committed;
static atomic_bool terminate_requested;
static uint64_t terminal_deadline_ms;
static uint64_t now_ms(void){struct timespec t;clock_gettime(CLOCK_MONOTONIC,&t);return (uint64_t)t.tv_sec*1000+t.tv_nsec/1000000;}

bool freedv_navigation_pending(void){return navigation.pending;}
bool freedv_navigation_replaying(void){return navigation.replaying;}
static void dispatch_pending(void){
    fd_nav_action action;
    if(!fd_nav_take_action(&navigation,&action))return;
    terminal_deadline_ms=0;
    freedv_navigation_dispatch(&action);
    fd_nav_replay_finished(&navigation);
}
bool freedv_navigation_request(fd_nav_kind kind,int32_t value,int32_t detail,const char *text){
    if(process_lifecycle_stopping() && !fd_nav_is_terminal(kind))return true;
    if(!scheduler_on_gui_thread())return freedv_usb_active() || atomic_load(&terminal_committed)!=FD_NAV_NONE;
    if(navigation.replaying)return false;
    fd_nav_kind committed=(fd_nav_kind)atomic_load(&terminal_committed);
    if(committed && terminal_priority(kind)<=terminal_priority(committed) &&
       (!fd_nav_is_terminal(kind) || !navigation.pending))return true;
    if(!freedv_usb_active())return false;
    /* A rejected caller still cannot run an ordinary app mutation while the
     * current mode owns the hardware. System terminal requests use the
     * separate trusted queue below. */
    if(!fd_nav_is_terminal(kind) && !front_keys_voice_local_event())return true;
    fd_nav_action action={.kind=kind,.value=value,.detail=detail};
    if(text)snprintf(action.text,sizeof(action.text),"%s",text);
    fd_nav_offer offer=fd_nav_offer_action(&navigation,&action);
    if(offer==FD_NAV_REJECTED)return true;
    if(fd_nav_is_terminal(navigation.action.kind)){
        atomic_store(&terminal_committed,navigation.action.kind);
        if(!terminal_deadline_ms)terminal_deadline_ms=now_ms()+5000;
        if(navigation.action.kind==FD_NAV_TERMINATE)freedv_usb_leave();
        else freedv_usb_terminal_leave();
    }else if(offer==FD_NAV_STARTED){
        freedv_usb_leave();
    }
    return true;
}
bool freedv_navigation_defer_dialog(void *descriptor){
    if(!scheduler_on_gui_thread())return freedv_usb_active() || atomic_load(&terminal_committed)!=FD_NAV_NONE;
    if(navigation.replaying)return false;
    if(atomic_load(&terminal_committed)!=FD_NAV_NONE)return true;
    if(!freedv_usb_active())return false;
    if(!front_keys_voice_local_event())return true;
    unsigned handle;
    for(handle=0;handle<dialog_count;++handle)if(dialog_descriptors[handle]==descriptor)break;
    if(handle==dialog_count){
        if(dialog_count==sizeof(dialog_descriptors)/sizeof(dialog_descriptors[0]))return true;
        dialog_descriptors[dialog_count++]=descriptor;
    }
    return freedv_navigation_request(FD_NAV_DIALOG,(int32_t)handle,0,NULL);
}
void *freedv_navigation_dialog_descriptor(int32_t handle){return handle>=0 && (unsigned)handle<dialog_count?dialog_descriptors[handle]:NULL;}
bool freedv_navigation_terminal(fd_nav_kind kind){
    if(!fd_nav_is_terminal(kind))return true;
    if(scheduler_on_gui_thread()){
        if(navigation.replaying)return false;
        fd_nav_kind committed=(fd_nav_kind)atomic_load(&terminal_committed);
        if(committed && terminal_priority(kind)<=terminal_priority(committed))return true;
        return freedv_navigation_request(kind,0,0,NULL);
    }
    if(!freedv_usb_active() && !atomic_load(&terminal_committed))return false;
    int prior=atomic_load(&terminal_queued);
    while(terminal_priority(kind)>terminal_priority((fd_nav_kind)prior) &&
          !atomic_compare_exchange_weak(&terminal_queued,&prior,kind)){}
    return true;
}
void freedv_navigation_completed(void){
    if(!scheduler_on_gui_thread() || freedv_usb_active())return;
    dispatch_pending();
}
void freedv_navigation_signal_terminate(void){atomic_store_explicit(&terminate_requested,true,memory_order_release);}
void freedv_navigation_tick(void){
    int kind=atomic_exchange(&terminal_queued,FD_NAV_NONE);
    if(atomic_exchange_explicit(&terminate_requested,false,memory_order_acq_rel)){if(terminal_priority(FD_NAV_TERMINATE)>terminal_priority((fd_nav_kind)kind))kind=FD_NAV_TERMINATE;}
    if(kind){
        fd_nav_kind committed=(fd_nav_kind)atomic_load(&terminal_committed);
        if(terminal_priority((fd_nav_kind)kind)>terminal_priority(committed)){
            if(freedv_usb_active())freedv_navigation_request((fd_nav_kind)kind,0,0,NULL);
            else {
                fd_nav_action action={.kind=(fd_nav_kind)kind};
                atomic_store(&terminal_committed,kind);navigation.replaying=true;
                freedv_navigation_dispatch(&action);fd_nav_replay_finished(&navigation);
            }
        }
    }
    if(navigation.pending && !freedv_usb_active())dispatch_pending();
    if(navigation.pending && navigation.action.kind!=FD_NAV_TERMINATE && fd_nav_is_terminal(navigation.action.kind) && terminal_deadline_ms && now_ms()>=terminal_deadline_ms){
        /* Terminal shutdown cannot wait forever for a failed device or held
         * PTT. This is not an ordinary ownership transfer or an RF-clear ACK. */
        fprintf(stderr,"FreeDV terminal shutdown grace elapsed; RF clear is not confirmed\n");
        dispatch_pending();
    }
}
#endif
