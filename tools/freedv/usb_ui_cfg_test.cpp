// SPDX-License-Identifier: LGPL-2.1-or-later
#include "src/cfg/settings_manager.h"
#include "src/cfg/cfg_api.h"
#include "src/cfg/freedv_preferences.h"
#include "src/freedv/mode.h"
#include <cassert>
#include <cstring>
static sqlite3 *database;
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
    cfg_sm.p_volume.set(27);
    /* An actual previous-beta setting, rather than its fresh-image default. */
    cfg_sm.p_freedv_usb_mic_gain.set(16);assert(cfg_sm.p_freedv_usb_mic_gain.save()==SUCCESS);
}
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
void usb_ui_cfg_close(void){
    assert(!fd_writer_voice_owned());
    cfg_sm.pending_writes_.flush_storage(StorageType::GLOBAL,-1);
    ParamsTable::Shutdown();sqlite3_close(database);
}
}
