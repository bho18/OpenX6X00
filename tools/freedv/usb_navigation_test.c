// SPDX-License-Identifier: LGPL-2.1-or-later
#define _POSIX_C_SOURCE 200809L
#include "../../src/freedv/usb_navigation.h"
#include <assert.h>
#include <stdio.h>
#include <string.h>
#include <time.h>
#include <unistd.h>
#include <sys/wait.h>

static bool active,gui=true,local=true;
static unsigned leaves,terminals,dispatches;
static uint64_t test_ms=100;
static fd_nav_action delivered;
bool freedv_usb_active(void){return active;}
bool scheduler_on_gui_thread(void){return gui;}
bool front_keys_voice_local_event(void){return local;}
bool freedv_usb_leave(void){++leaves;return true;}
void freedv_usb_terminal_leave(void){++terminals;}
void freedv_navigation_dispatch(const fd_nav_action *action){
    delivered=*action;++dispatches;
    /* Replaying an app close must not enqueue itself recursively. */
    assert(!freedv_navigation_request(FD_NAV_HOME,0,0,NULL));
}
int clock_gettime(clockid_t clock,struct timespec *out){
    (void)clock;out->tv_sec=(time_t)(test_ms/1000);out->tv_nsec=(long)(test_ms%1000)*1000000;return 0;
}
static void finish(void){active=false;freedv_navigation_completed();assert(!freedv_navigation_pending());}
static void pure_policy(void){
    for(int kind=FD_NAV_HOME;kind<=FD_NAV_TERMINATE;++kind){
        fd_nav_state state={0};fd_nav_action first={.kind=kind,.value=41,.detail=2},out;
        strcpy(first.text,"copied destination");
        assert(fd_nav_offer_action(&state,&first)==FD_NAV_STARTED);
        assert(fd_nav_offer_action(&state,&first)==FD_NAV_UNCHANGED);
        first.text[0]='x';assert(state.action.text[0]=='c');
        assert(fd_nav_take_action(&state,&out));
        assert(out.kind==(fd_nav_kind)kind && out.value==41 && out.detail==2);
        assert(!fd_nav_take_action(&state,&out));
        assert(fd_nav_offer_action(&state,&first)==FD_NAV_REJECTED);
        fd_nav_replay_finished(&state);
        assert(fd_nav_offer_action(&state,&first)==FD_NAV_STARTED);
    }
    fd_nav_state state={0};fd_nav_action out;
    fd_nav_action home={.kind=FD_NAV_HOME},app={.kind=FD_NAV_APP,.value=7};
    fd_nav_action reset={.kind=FD_NAV_RESET},terminate={.kind=FD_NAV_TERMINATE},off={.kind=FD_NAV_POWEROFF};
    assert(fd_nav_offer_action(&state,&home)==FD_NAV_STARTED);
    assert(fd_nav_offer_action(&state,&app)==FD_NAV_UPDATED);
    assert(fd_nav_offer_action(&state,&reset)==FD_NAV_UPDATED);
    assert(fd_nav_offer_action(&state,&home)==FD_NAV_UNCHANGED);
    assert(fd_nav_offer_action(&state,&terminate)==FD_NAV_UPDATED);
    assert(fd_nav_offer_action(&state,&reset)==FD_NAV_UNCHANGED);
    assert(fd_nav_offer_action(&state,&off)==FD_NAV_UPDATED);
    assert(fd_nav_offer_action(&state,&terminate)==FD_NAV_UNCHANGED);
    assert(fd_nav_take_action(&state,&out) && out.kind==FD_NAV_POWEROFF);
}
static void physical_exit_edges(void){
    fd_nav_exit_latch latch={0};
    /* Notice and owned popup F5 retain their own acknowledgement/Close. */
    assert(fd_nav_exit_key(&latch,true,false,false,false,true,1)==FD_NAV_EXIT_PASS);
    assert(fd_nav_exit_key(&latch,false,true,false,false,true,1)==FD_NAV_EXIT_PASS);
    assert(fd_nav_exit_key(&latch,true,false,true,false,false,1)==FD_NAV_EXIT_CONSUME);
    assert(fd_nav_exit_key(&latch,true,false,true,false,true,1)==FD_NAV_EXIT_LEAVE);
    assert(fd_nav_exit_key(&latch,true,false,true,true,true,1)==FD_NAV_EXIT_CONSUME);
    assert(fd_nav_exit_key(&latch,false,false,true,true,true,1)==FD_NAV_EXIT_CONSUME);
    assert(fd_nav_exit_key(&latch,false,true,true,true,true,1)==FD_NAV_EXIT_CONSUME);
    /* A second distinct press while leaving cannot force an abort. The same
     * rule covers a navigation exit whose first F5 arrives during cleanup. */
    assert(fd_nav_exit_key(&latch,true,false,true,true,true,1)==FD_NAV_EXIT_CONSUME);
    assert(fd_nav_exit_key(&latch,false,true,true,true,true,1)==FD_NAV_EXIT_CONSUME);
    fd_nav_exit_latch navigation_latch={0};
    assert(fd_nav_exit_key(&navigation_latch,true,false,true,true,true,1)==FD_NAV_EXIT_CONSUME);
    assert(fd_nav_exit_key(&navigation_latch,false,true,true,true,true,1)==FD_NAV_EXIT_CONSUME);
    assert(fd_nav_exit_key(&latch,true,false,true,false,true,1)==FD_NAV_EXIT_LEAVE);
    /* Input-drain barrier may discard the initial release. Its new generation
     * makes the next physical press independent, without an ignored press. */
    assert(fd_nav_exit_key(&latch,true,false,true,false,true,2)==FD_NAV_EXIT_LEAVE);
}
static void runtime_routes(void){
    /* Every ordinary destination is retained until both fresh RX and input
     * release have allowed the UI coordinator to report fully inactive. */
    for(int kind=FD_NAV_HOME;kind<=FD_NAV_WALKIE;++kind){
        active=true;unsigned before=dispatches,old_leaves=leaves;
        char name[]="theme-name";
        assert(freedv_navigation_request(kind,kind*3,1,name));
        name[0]='X';
        assert(leaves==old_leaves+1);
        assert(freedv_navigation_request(kind,kind*3,1,"theme-name"));
        assert(leaves==old_leaves+1);
        freedv_navigation_completed();freedv_navigation_tick();
        assert(dispatches==before);
        finish();assert(dispatches==before+1 && delivered.kind==(fd_nav_kind)kind);
        assert(delivered.value==kind*3 && !strcmp(delivered.text,"theme-name"));
        freedv_navigation_completed();assert(dispatches==before+1);
    }
    active=true;unsigned before=leaves;
    local=false;assert(freedv_navigation_request(FD_NAV_APP,7,0,NULL));
    assert(!freedv_navigation_pending() && leaves==before);
    local=true;gui=false;assert(freedv_navigation_request(FD_NAV_HOME,0,0,NULL));
    assert(!freedv_navigation_pending() && leaves==before);gui=true;
    assert(freedv_navigation_request(FD_NAV_APP,3,0,NULL));
    assert(freedv_navigation_request(FD_NAV_PAGE,4,1,NULL));
    assert(leaves==before+1);finish();assert(delivered.kind==FD_NAV_PAGE && delivered.value==4);
    /* Dialog descriptors are static definitions, never transient widget or
     * parent pointers. Only their stable handle is saved. */
    static int descriptor;
    active=true;assert(freedv_navigation_defer_dialog(&descriptor));finish();
    assert(delivered.kind==FD_NAV_DIALOG);
    assert(freedv_navigation_dialog_descriptor(delivered.value)==&descriptor);
}
static void terminal_routes(void){
    active=true;unsigned before=dispatches;
    assert(freedv_navigation_request(FD_NAV_APP,7,0,NULL));
    assert(freedv_navigation_terminal(FD_NAV_RESET));
    assert(freedv_navigation_request(FD_NAV_HOME,0,0,NULL));
    finish();assert(delivered.kind==FD_NAV_RESET && dispatches==before+1);
    /* Background terminal requests are marshalled onto GUI. */
    active=true;gui=false;local=false;before=terminals;
    assert(freedv_navigation_terminal(FD_NAV_POWEROFF));assert(terminals==before);
    gui=true;freedv_navigation_tick();assert(terminals==before+1);
    test_ms+=4999;freedv_navigation_tick();assert(freedv_navigation_pending());
    test_ms+=1;freedv_navigation_tick();assert(delivered.kind==FD_NAV_POWEROFF);
    assert(!freedv_navigation_pending());before=dispatches;local=true;
    assert(freedv_navigation_request(FD_NAV_APP,4,0,NULL));
    assert(!freedv_navigation_pending() && dispatches==before);
    active=false;assert(freedv_navigation_request(FD_NAV_HOME,0,0,NULL));
    assert(!freedv_navigation_pending() && dispatches==before);
    freedv_navigation_signal_terminate();freedv_navigation_tick();
    assert(dispatches==before); /* committed poweroff has higher priority */
}
static void inactive_signal(void){
    /* A real terminal dispatch exits the process. Isolate that lifecycle so
     * its intentionally permanent terminal latch cannot affect other cases. */
    pid_t child=fork();assert(child>=0);
    if(!child){
        active=false;unsigned before=dispatches;
        freedv_navigation_signal_terminate();assert(dispatches==before);
        freedv_navigation_tick();assert(dispatches==before+1 && delivered.kind==FD_NAV_TERMINATE);
        assert(freedv_navigation_request(FD_NAV_APP,4,0,NULL));_exit(0);
    }
    int status;assert(waitpid(child,&status,0)==child && WIFEXITED(status) && WEXITSTATUS(status)==0);
}
int main(void){inactive_signal();pure_policy();physical_exit_edges();runtime_routes();terminal_routes();puts("USB navigation policy, physical Exit edges and deferred runtime routes: PASS");return 0;}
