// SPDX-License-Identifier: LGPL-2.1-or-later
#include "src/cfg/settings_manager.h"
#include "src/cfg/cfg_api.h"
#include "src/cfg/freedv_preferences.h"
#include "src/freedv/mode.h"
#include <cassert>
#include <cstring>
#include <chrono>
#include <thread>
#include <atomic>
static sqlite3 *database;
static std::atomic<unsigned> gain_profile_writes{0};
static int count_gain_profile_writes(unsigned,void*,void *statement,void*){
    auto *query=static_cast<sqlite3_stmt *>(statement);
    if(sqlite3_stmt_readonly(query))return 0;
    char *expanded=sqlite3_expanded_sql(query);assert(expanded);
    if(std::strstr(expanded,"'freedv_usb_gains'"))++gain_profile_writes;
    sqlite3_free(expanded);return 0;
}
static std::atomic<bool> volume_storage_paused{false},volume_storage_released{false};
static int pause_volume_storage(void*,int action,const char *table,const char*,const char*,const char*){
    if(action==SQLITE_INSERT && table && !std::strcmp(table,"params") && !volume_storage_released.load()){
        volume_storage_paused=true;
        const auto deadline=std::chrono::steady_clock::now()+std::chrono::seconds(5);
        while(!volume_storage_released.load() && std::chrono::steady_clock::now()<deadline)
            std::this_thread::sleep_for(std::chrono::milliseconds(1));
        assert(volume_storage_released.load());
    }
    return SQLITE_OK;
}
extern "C" {
bool scheduler_on_gui_thread(void){return true;}
bool freedv_mode_active(void){return false;}
bool freedv_mode_request(fd_local_operation,int32_t){return false;}
void usb_ui_cfg_init(void){
    assert(sqlite3_open(":memory:",&database)==SQLITE_OK);
    assert(sqlite3_exec(database,"CREATE TABLE params(name TEXT PRIMARY KEY,val)",nullptr,nullptr,nullptr)==SQLITE_OK);
    assert(ParamsTable::Init(database));
    cfg_freedv_usb_mic_gain=&cfg_sm.p_freedv_usb_mic_gain;
    cfg_freedv_timeout=&cfg_sm.p_freedv_timeout;
    cfg_volume=&cfg_sm.p_volume;cfg_fg_freq=&cfg_sm.cp_fg_freq;
    assert(cfg_sm.p_freedv_usb_mic_gain.get()==0);
    assert(!cfg_freedv_usage_hidden());
    assert(!cfg_freedv_detailed_capture_enabled());
    cfg_sm.p_volume.set(27);
    /* An actual previous-beta setting, rather than its fresh-image default. */
    cfg_sm.p_freedv_usb_mic_gain.set(16);assert(cfg_sm.p_freedv_usb_mic_gain.save()==SUCCESS);
    assert(sqlite3_trace_v2(database,SQLITE_TRACE_STMT,count_gain_profile_writes,nullptr)==SQLITE_OK);
}
void usb_ui_cfg_detailed(bool enabled){
    assert(cfg_freedv_set_detailed_capture(enabled));
    auto value=storage_policy_for(StorageType::GLOBAL).load_int(0,"freedv_detailed_capture");
    assert(value && bool(*value)==enabled);
}
unsigned usb_ui_cfg_gain_writes(void){return gain_profile_writes.load();}
void usb_ui_cfg_verify_hidden(bool expected){
    auto value=storage_policy_for(StorageType::GLOBAL).load_int(0,"freedv_usage_hidden");
    assert(value && bool(*value)==expected);
}
void usb_ui_cfg_verify_profiles(void){
    assert(cfg_freedv_usb_gain_for("loop110-stable","Loop110 USB audio")==7);
    assert(cfg_freedv_usb_gain_for("new-usb-device","Other headset")==0);
    assert(cfg_freedv_usb_save_gain("new-usb-device",-3));
    assert(cfg_freedv_usb_gain_for("new-usb-device","Other headset")==-3);
    assert(cfg_freedv_usb_gain_for("loop110-stable","Loop110 USB audio")==7);
    assert(!cfg_freedv_usb_save_gain("bad",25));
    assert(cfg_sm.p_volume.get()==27);
}
void usb_ui_cfg_pause_volume_storage(void){
    const auto deadline=std::chrono::steady_clock::now()+std::chrono::seconds(3);
    while(cfg_freedv_usb_volume_busy()){
        cfg_freedv_usb_volume_poll();assert(std::chrono::steady_clock::now()<deadline);
        std::this_thread::sleep_for(std::chrono::milliseconds(1));
    }
    volume_storage_paused=false;volume_storage_released=false;
    assert(sqlite3_set_authorizer(database,pause_volume_storage,nullptr)==SQLITE_OK);
    assert(cfg_freedv_usb_save_volume("loop110-stable",41,false));
    while(!volume_storage_paused.load()){
        assert(std::chrono::steady_clock::now()<deadline);
        std::this_thread::sleep_for(std::chrono::milliseconds(1));
    }
    assert(cfg_freedv_usb_volume_busy());
}
void usb_ui_cfg_release_volume_storage(void){
    volume_storage_released=true;
    const auto deadline=std::chrono::steady_clock::now()+std::chrono::seconds(3);
    while(cfg_freedv_usb_volume_busy()){
        cfg_freedv_usb_volume_poll();assert(std::chrono::steady_clock::now()<deadline);
        std::this_thread::sleep_for(std::chrono::milliseconds(1));
    }
    cfg_freedv_usb_volume_poll();assert(sqlite3_set_authorizer(database,nullptr,nullptr)==SQLITE_OK);
}
void usb_ui_cfg_close(void){
    assert(!fd_writer_voice_owned());
    cfg_freedv_usb_volume_flush();
    const auto deadline=std::chrono::steady_clock::now()+std::chrono::seconds(5);
    while(cfg_freedv_usb_volume_busy()){
        cfg_freedv_usb_volume_poll();assert(std::chrono::steady_clock::now()<deadline);
        std::this_thread::sleep_for(std::chrono::milliseconds(1));
    }
    cfg_freedv_usb_volume_storage_shutdown();
    cfg_sm.pending_writes_.flush_storage(StorageType::GLOBAL,-1);
    ParamsTable::Shutdown();sqlite3_close(database);
}
}
