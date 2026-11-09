/* SPDX-License-Identifier: LGPL-2.1-or-later
 * Included by btaudio.cpp, and by a focused test using the real process/lock
 * implementation. One worker exists only during an explicitly owned mode. */
#include <chrono>
#include <condition_variable>
#include <mutex>
#include <string>
#include <thread>
#include <atomic>
#include <cerrno>
#include <cstdio>
#include <cstring>
#include <fcntl.h>
#include <signal.h>
#include <spawn.h>
#include <sys/file.h>
#include <sys/wait.h>
#include <unistd.h>

#ifndef BTAUDIO_RX_HOLD_COMMAND
#define BTAUDIO_RX_HOLD_COMMAND "/usr/sbin/btaudio-ctl"
#endif
#ifndef BTAUDIO_RX_HOLD_LOCK
#define BTAUDIO_RX_HOLD_LOCK "/run/btaudio-rx-hold.lock"
#endif
extern char **environ;
static std::atomic_bool native_rx_held{false};
struct btaudio_rx_hold {
    std::mutex mutex;
    std::condition_variable changed;
    std::thread worker;
    std::atomic_bool release{false}, done{false};
    btaudio_rx_hold_status status=BTAUDIO_RX_PENDING;
    std::string reason;
};
using bt_hold_clock=std::chrono::steady_clock;
static void bt_hold_pause() { std::this_thread::sleep_for(std::chrono::milliseconds(5)); }
static void bt_hold_status(btaudio_rx_hold *h,btaudio_rx_hold_status status,const char *reason) {
    std::lock_guard<std::mutex> lock(h->mutex);h->status=status;h->reason=reason;
}
/* Each readiness transaction owns its own process group. A timeout kills only
 * that group's helper and pactl children, never the persistent monitor. */
static bool bt_hold_command(btaudio_rx_hold *h,const char *command,
        bt_hold_clock::time_point deadline,bool cancellable,std::string &out) {
    int pipes[2];if(pipe2(pipes,O_CLOEXEC))return false;
    if(fcntl(pipes[0],F_SETFL,O_NONBLOCK)<0){close(pipes[0]);close(pipes[1]);return false;}
    posix_spawn_file_actions_t files;posix_spawn_file_actions_init(&files);
    posix_spawn_file_actions_adddup2(&files,pipes[1],STDOUT_FILENO);
    posix_spawn_file_actions_adddup2(&files,pipes[1],STDERR_FILENO);
    posix_spawn_file_actions_addclose(&files,pipes[0]);
    posix_spawn_file_actions_addclose(&files,pipes[1]);
    posix_spawnattr_t attributes;posix_spawnattr_init(&attributes);
    posix_spawnattr_setflags(&attributes,POSIX_SPAWN_SETPGROUP);
    posix_spawnattr_setpgroup(&attributes,0);
    char *args[]={const_cast<char *>(BTAUDIO_RX_HOLD_COMMAND),const_cast<char *>(command),nullptr};
    pid_t child=-1;int spawned=posix_spawn(&child,BTAUDIO_RX_HOLD_COMMAND,&files,&attributes,args,environ);
    posix_spawn_file_actions_destroy(&files);posix_spawnattr_destroy(&attributes);close(pipes[1]);
    if(spawned){close(pipes[0]);return false;}
    bool exited=false,eof=false,failed=false;int status=0;out.clear();
    while(!exited || !eof){
        char bytes[512];ssize_t n=read(pipes[0],bytes,sizeof(bytes));
        if(n>0){if(out.size()+static_cast<size_t>(n)>4096){failed=true;break;}out.append(bytes,n);}
        else if(!n)eof=true;
        else if(errno!=EAGAIN && errno!=EWOULDBLOCK && errno!=EINTR){failed=true;break;}
        if(!exited){pid_t got=waitpid(child,&status,WNOHANG);if(got==child)exited=true;else if(got<0 && errno!=EINTR){failed=true;break;}}
        if(exited && eof)break;
        if(bt_hold_clock::now()>=deadline || (cancellable && h->release.load())){failed=true;break;}
        bt_hold_pause();
    }
    if(failed){
        kill(-child,SIGTERM);auto grace=bt_hold_clock::now()+std::chrono::milliseconds(100);
        while(!exited && bt_hold_clock::now()<grace){if(waitpid(child,&status,WNOHANG)==child)exited=true;else bt_hold_pause();}
        kill(-child,SIGKILL);
    }
    if(!exited){while(waitpid(child,&status,0)<0 && errno==EINTR){}}
    close(pipes[0]);return !failed && WIFEXITED(status) && WEXITSTATUS(status)==0;
}
static bool bt_hold_reply(const std::string &reply,const char *word,unsigned long &pid,unsigned long long &start) {
    char prefix[32],state[32],extra;unsigned long p;unsigned long long s;
    if(sscanf(reply.c_str(),"%31s %31s %lu %llu %c",prefix,state,&p,&s,&extra)!=4 ||
       strcmp(prefix,"BTAUDIO_RX_V2") || strcmp(state,word) || !p || !s)return false;
    pid=p;start=s;return true;
}
static void bt_hold_run(btaudio_rx_hold *h) {
    int lease=-1;bool held=false,admitted=false;std::string reply;
    auto deadline=bt_hold_clock::now()+std::chrono::seconds(2);
    unsigned long monitor=0,verified=0;unsigned long long started=0,verified_start=0;
    bool protocol=bt_hold_command(h,"protocol",deadline,true,reply);
    if(reply=="BTAUDIO_RX_V2 REBOOT_REQUIRED\n"){
        bt_hold_status(h,BTAUDIO_RX_REBOOT_REQUIRED,"Restart radio to finish the Bluetooth audio update");
    }else if(!protocol || !bt_hold_reply(reply,"READY",monitor,started)){
        bt_hold_status(h,BTAUDIO_RX_FAILED,"Bluetooth audio monitor unavailable");
    }else{
        lease=open(BTAUDIO_RX_HOLD_LOCK,O_RDWR|O_CREAT|O_CLOEXEC|O_NOFOLLOW,0600);
        if(lease>=0){
            while(!h->release.load() && bt_hold_clock::now()<deadline){
                if(!flock(lease,LOCK_EX|LOCK_NB)){held=true;break;}
                if(errno!=EWOULDBLOCK && errno!=EAGAIN)break;
                bt_hold_pause();
            }
        }
        if(held && bt_hold_command(h,"hold-sync",deadline,true,reply) &&
           bt_hold_reply(reply,"QUIET",verified,verified_start) && monitor==verified && started==verified_start){
            std::unique_lock<std::mutex> lock(h->mutex);
            admitted=!h->release.load();
            if(admitted){h->status=BTAUDIO_RX_HELD;h->reason.clear();}
            h->changed.wait(lock,[h]{return h->release.load();});
            h->status=BTAUDIO_RX_PENDING;
        }else bt_hold_status(h,BTAUDIO_RX_FAILED,"Could not pause native Bluetooth RX audio");
    }
    if(lease>=0)close(lease);
    /* Also reconcile a partially completed quiet operation. Preferences were
     * never changed; monitor lease detection remains the crash fallback. */
    if(held){
        bool restored=bt_hold_command(h,"reconcile",bt_hold_clock::now()+std::chrono::seconds(2),false,reply) &&
            bt_hold_reply(reply,"RESTORED",verified,verified_start);
        bt_hold_status(h,restored && admitted?BTAUDIO_RX_RELEASED:BTAUDIO_RX_FAILED,
            restored?(admitted?"":"Could not pause native Bluetooth RX audio"):"Bluetooth RX audio is reconnecting");
    }
    h->done.store(true,std::memory_order_release);
}
btaudio_rx_hold_t *btaudio_rx_hold_begin(void) {
    bool expected=false;if(!native_rx_held.compare_exchange_strong(expected,true))return nullptr;
    btaudio_rx_hold *h=nullptr;
    try{h=new btaudio_rx_hold;h->worker=std::thread(bt_hold_run,h);return h;}
    catch(...){delete h;native_rx_held.store(false);return nullptr;}
}
btaudio_rx_hold_status btaudio_rx_hold_poll(btaudio_rx_hold_t *h,char *reason,size_t size) {
    if(!h){if(reason && size)snprintf(reason,size,"Bluetooth audio could not start");return BTAUDIO_RX_FAILED;}
    std::lock_guard<std::mutex> lock(h->mutex);if(reason && size)snprintf(reason,size,"%s",h->reason.c_str());return h->status;
}
void btaudio_rx_hold_release(btaudio_rx_hold_t *h) {
    if(h){
        {std::lock_guard<std::mutex> lock(h->mutex);h->release.store(true);if(h->status==BTAUDIO_RX_HELD)h->status=BTAUDIO_RX_PENDING;}
        h->changed.notify_all();
    }
}
bool btaudio_rx_hold_close(btaudio_rx_hold_t *h) {
    if(!h)return true;
    if(!h->done.load(std::memory_order_acquire))return false;
    h->worker.join();delete h;native_rx_held.store(false,std::memory_order_release);return true;
}
