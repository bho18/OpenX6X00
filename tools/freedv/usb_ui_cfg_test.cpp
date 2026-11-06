// SPDX-License-Identifier: LGPL-2.1-or-later
#include "src/cfg/settings_manager.h"
#include "src/cfg/cfg_api.h"
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
    cfg_volume=&cfg_sm.p_volume;
    cfg_fg_freq=&cfg_sm.cp_fg_freq;
    auto &gain=cfg_sm.p_freedv_usb_mic_gain;
    assert(gain.get()==0 && gain.storage_type()==StorageType::GLOBAL);
    assert(!strcmp(gain.db_name(),"freedv_usb_mic_gain"));
    assert(gain.load()==NOT_FOUND && gain.get()==0);
    gain.set(100);assert(gain.get()==24);
    gain.set(-100);assert(gain.get()==-12);
    gain.set(0);
    cfg_sm.p_volume.set(27);
}
void usb_ui_cfg_verify_saved(int expected){
    assert(!fd_writer_voice_owned());
    cfg_sm.pending_writes_.flush_storage(StorageType::GLOBAL,-1);
    auto &gain=cfg_sm.p_freedv_usb_mic_gain;
    gain.set_quiet(0);assert(gain.load()==SUCCESS && gain.get()==expected);
    assert(cfg_sm.p_volume.get()==27);
    sqlite3_stmt *query=nullptr;
    assert(sqlite3_prepare_v2(database,"SELECT val FROM params WHERE name='freedv_usb_mic_gain'",-1,&query,nullptr)==SQLITE_OK);
    assert(sqlite3_step(query)==SQLITE_ROW && sqlite3_column_int(query,0)==expected);
    sqlite3_finalize(query);ParamsTable::Shutdown();sqlite3_close(database);
}
}
