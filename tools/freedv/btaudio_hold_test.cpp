// SPDX-License-Identifier: LGPL-2.1-or-later
#include "../../src/btaudio.h"
#include <cassert>
#include <fstream>
#include <string>
#include <thread>
#include <chrono>
#include <sys/stat.h>
#include <signal.h>
#include <unistd.h>
static std::string helper_path,lease_path;
#define BTAUDIO_RX_HOLD_COMMAND helper_path.c_str()
#define BTAUDIO_RX_HOLD_LOCK lease_path.c_str()
#include "../../src/btaudio_rx_hold.inc"

static void write_file(const std::string &path,const std::string &value){std::ofstream f(path);f<<value;assert(f.good());}
static btaudio_rx_hold_status wait_status(btaudio_rx_hold *h){
    auto until=bt_hold_clock::now()+std::chrono::seconds(3);btaudio_rx_hold_status s;
    do{s=btaudio_rx_hold_poll(h,nullptr,0);if(s!=BTAUDIO_RX_PENDING)return s;bt_hold_pause();}while(bt_hold_clock::now()<until);
    assert(false);return BTAUDIO_RX_FAILED;
}
static void close_hold(btaudio_rx_hold *h){
    auto until=bt_hold_clock::now()+std::chrono::seconds(3);
    while(!btaudio_rx_hold_close(h)){assert(bt_hold_clock::now()<until);bt_hold_pause();}
    assert(!native_rx_held.load());
}
int main(int argc,char **argv){
    if(argc==4 && !strcmp(argv[1],"--hold-only")){
        helper_path=argv[2];lease_path=argv[3];
        auto h=btaudio_rx_hold_begin();assert(h && wait_status(h)==BTAUDIO_RX_HELD);
        puts("HELD");fflush(stdout);
        // The integration test kills this process to exercise kernel release.
        for(;;)pause();
    }
    char scratch[]="/tmp/btaudio-hold-test-XXXXXX";char *directory=mkdtemp(scratch);assert(directory);
    std::string root=directory;helper_path=root+"/helper";lease_path=root+"/lease";
    write_file(helper_path,R"SH(#!/bin/bash
root=${0%/*}
mode=$(cat "$root/mode")
echo "$1" >> "$root/calls"
for fd in /proc/$$/fd/*; do
  [ "$(readlink "$fd")" != "$root/lease" ] || { echo inherited-lease >&2; exit 2; }
done
case "$1" in
 protocol)
  case "$mode" in
   legacy) echo 'BTAUDIO_RX_V2 REBOOT_REQUIRED'; exit 78;;
   hang) trap '' TERM; sleep 30 & echo $! > "$root/child"; wait;;
  esac
  echo 'BTAUDIO_RX_V2 READY 123 456';;
 hold-sync)
  [ "$mode" != fail-hold ] || exit 1
  echo 'BTAUDIO_RX_V2 QUIET 123 456';;
 reconcile)
  [ "$mode" != fail-release ] || exit 1
  echo 'BTAUDIO_RX_V2 RESTORED 123 456';;
esac
)SH");assert(!chmod(helper_path.c_str(),0700));
    write_file(root+"/mode","normal\n");
    auto h=btaudio_rx_hold_begin();assert(h && wait_status(h)==BTAUDIO_RX_HELD);
    assert(!btaudio_rx_hold_begin());
    int probe=open(lease_path.c_str(),O_RDWR|O_CLOEXEC);assert(probe>=0 && flock(probe,LOCK_SH|LOCK_NB)<0);
    btaudio_rx_hold_release(h);assert(wait_status(h)==BTAUDIO_RX_RELEASED);close_hold(h);
    assert(!flock(probe,LOCK_SH|LOCK_NB));close(probe);
    for(auto mode:{"legacy","fail-hold","fail-release"}){
        write_file(root+"/mode",std::string(mode)+"\n");h=btaudio_rx_hold_begin();assert(h);
        if(!strcmp(mode,"fail-release")){assert(wait_status(h)==BTAUDIO_RX_HELD);btaudio_rx_hold_release(h);}
        auto status=wait_status(h);assert(status==(!strcmp(mode,"legacy")?BTAUDIO_RX_REBOOT_REQUIRED:BTAUDIO_RX_FAILED));
        btaudio_rx_hold_release(h);close_hold(h);
        probe=open(lease_path.c_str(),O_RDWR);assert(probe>=0 && !flock(probe,LOCK_SH|LOCK_NB));close(probe);
    }
    write_file(root+"/mode","hang\n");auto started=bt_hold_clock::now();h=btaudio_rx_hold_begin();assert(h);
    assert(wait_status(h)==BTAUDIO_RX_FAILED);close_hold(h);
    assert(bt_hold_clock::now()-started<std::chrono::milliseconds(2400));
    std::ifstream child_file(root+"/child");int child=0;child_file>>child;assert(child>1);
    // A killed descendant may briefly be an init-owned zombie, never running.
    std::ifstream stat("/proc/"+std::to_string(child)+"/stat");std::string row;getline(stat,row);
    assert(row.empty() || row.substr(row.rfind(')')+2,1)=="Z");
    write_file(root+"/mode","normal\n");h=btaudio_rx_hold_begin();assert(h);btaudio_rx_hold_release(h);close_hold(h);
    for(auto name:{"helper","lease","mode","calls","child"})unlink((root+"/"+name).c_str());
    assert(!rmdir(root.c_str()));
    puts("Bluetooth RX hold lifecycle, exclusion, CLOEXEC, failures and process deadlines passed");
}
