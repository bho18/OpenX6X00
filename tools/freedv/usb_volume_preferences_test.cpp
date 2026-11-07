// SPDX-License-Identifier: LGPL-2.1-or-later
#include "src/cfg/settings_manager.h"
#include "src/cfg/freedv_preferences.h"
#include "src/freedv/voice_types.h"
#include <cassert>
#include <cstdio>
#include <sys/wait.h>
#include <unistd.h>
extern "C" bool scheduler_on_gui_thread(void){return true;}
extern "C" bool freedv_mode_active(void){return false;}
extern "C" bool freedv_mode_request(fd_local_operation,int32_t){return false;}
static int reject_writes(void*,int action,const char*,const char*,const char*,const char*){
    return action==SQLITE_INSERT || action==SQLITE_UPDATE || action==SQLITE_DELETE?SQLITE_DENY:SQLITE_OK;
}
/* SQLite's authorizer cannot inspect values bound to a shared params INSERT.
 * A key-selective trigger injects exactly the optional-metadata failure while
 * leaving the existing selected-headset write authorized. */
static void reject_optional_volume(sqlite3 *db,bool reject){
    const char *sql=reject?
        "CREATE TRIGGER reject_optional_volume BEFORE INSERT ON params "
        "WHEN NEW.name IN ('freedv_usb_volume_legacy_id','freedv_usb_volume_migrated') "
        "BEGIN SELECT RAISE(ABORT,'optional volume metadata failure'); END;":
        "DROP TRIGGER reject_optional_volume";
    assert(sqlite3_exec(db,sql,nullptr,nullptr,nullptr)==SQLITE_OK);
}
int main(){
    sqlite3 *db=nullptr;assert(sqlite3_open(":memory:",&db)==SQLITE_OK);
    assert(sqlite3_exec(db,"CREATE TABLE params(name TEXT PRIMARY KEY,val)",nullptr,nullptr,nullptr)==SQLITE_OK);
    assert(ParamsTable::Init(db));
    /* A failed no-legacy marker must not recapture a later new device. */
    pid_t child=fork();assert(child>=0);
    if(child==0){
        reject_optional_volume(db,true);assert(!cfg_freedv_usb_capture_legacy_volume());
        assert(cfg_freedv_usb_select_headset("first-new-device"));
        cfg_freedv_usb_volume fresh{};
        assert(cfg_freedv_usb_volume_target("first-new-device",&fresh) && !fresh.legacy_import && fresh.percent==30);
        reject_optional_volume(db,false);assert(cfg_freedv_usb_capture_legacy_volume());
        assert(cfg_sm.p_freedv_usb_volume_migrated.get()==1);_exit(0);
    }
    int child_status=0;assert(waitpid(child,&child_status,0)==child && WIFEXITED(child_status) && WEXITSTATUS(child_status)==0);
    cfg_sm.p_volume.set_quiet(27);cfg_sm.p_freedv_usb_headset.set_quiet("legacy-selected");
    assert(cfg_sm.p_freedv_usb_headset.save()==SUCCESS);
    reject_optional_volume(db,true);
    assert(!cfg_freedv_usb_capture_legacy_volume());
    assert(cfg_freedv_usb_select_headset("selected-during-metadata-failure"));
    assert(cfg_sm.p_freedv_usb_headset.get()=="selected-during-metadata-failure");
    cfg_freedv_usb_volume during_failure{};
    assert(cfg_freedv_usb_volume_target("legacy-selected",&during_failure) && during_failure.legacy_import);
    assert(cfg_freedv_usb_volume_target("selected-during-metadata-failure",&during_failure) && !during_failure.legacy_import && during_failure.percent==30);
    reject_optional_volume(db,false);
    assert(cfg_freedv_usb_capture_legacy_volume());
    assert(cfg_sm.p_freedv_usb_volume_legacy_id.get()=="legacy-selected");
    cfg_freedv_usb_volume v{};
    assert(cfg_freedv_usb_volume_target("legacy-selected",&v) && !v.present && v.legacy_import);
    assert(cfg_freedv_usb_select_headset("new-headset"));
    assert(cfg_sm.p_freedv_usb_volume_legacy_id.get()=="legacy-selected");
    assert(cfg_freedv_usb_volume_target("new-headset",&v) && !v.present && !v.legacy_import && v.percent==30 && !v.muted);
    assert(!cfg_freedv_usb_confirm_legacy_volume("new-headset",46,false));
    assert(cfg_freedv_usb_confirm_legacy_volume("legacy-selected",46,true));
    assert(cfg_freedv_usb_volume_target("legacy-selected",&v) && v.present && !v.legacy_import && v.percent==46 && v.muted);
    assert(cfg_freedv_usb_save_volume("new-headset",0,false));
    assert(cfg_freedv_usb_volume_target("new-headset",&v) && v.present && v.percent==0 && !v.muted);
    assert(!cfg_freedv_usb_save_volume("new-headset",101,false));
    assert(cfg_freedv_usb_volume_observe("new-headset",2,true,70,false,1000));
    assert(!cfg_freedv_usb_volume_saved());
    assert(cfg_freedv_usb_volume_observe("new-headset",2,true,70,false,1499));
    assert(cfg_freedv_usb_volume_target("new-headset",&v) && v.percent==0);
    assert(cfg_freedv_usb_volume_observe("new-headset",3,true,72,true,1500));
    assert(cfg_freedv_usb_volume_observe("new-headset",3,true,72,true,1999));
    assert(!cfg_freedv_usb_volume_saved());
    assert(cfg_freedv_usb_volume_observe("new-headset",3,true,72,true,2000));
    assert(cfg_freedv_usb_volume_saved());
    assert(cfg_freedv_usb_volume_target("new-headset",&v) && v.percent==72 && v.muted);
    /* Pending/failed requests cannot persist an optimistic target. */
    assert(cfg_freedv_usb_volume_observe("new-headset",4,false,95,false,2100));
    assert(cfg_freedv_usb_volume_flush() && !cfg_freedv_usb_volume_saved());
    assert(cfg_freedv_usb_volume_target("new-headset",&v) && v.percent==72 && v.muted);
    assert(cfg_freedv_usb_volume_observe("new-headset",5,true,81,false,2200));
    sqlite3_set_authorizer(db,reject_writes,nullptr);
    assert(!cfg_freedv_usb_select_headset("must-not-persist"));
    assert(cfg_sm.p_freedv_usb_headset.get()=="new-headset");
    assert(!cfg_freedv_usb_volume_observe("new-headset",5,true,81,false,2700));
    assert(!cfg_freedv_usb_volume_saved());
    assert(cfg_freedv_usb_volume_target("new-headset",&v) && v.percent==72);
    sqlite3_set_authorizer(db,nullptr,nullptr);assert(cfg_freedv_usb_volume_flush());
    assert(cfg_freedv_usb_volume_target("new-headset",&v) && v.percent==81 && !v.muted);
    /* A changed selected preference never changes a readback's physical key. */
    assert(cfg_freedv_usb_select_headset("third-headset"));
    assert(cfg_freedv_usb_volume_observe("new-headset",6,true,82,false,3000));
    assert(cfg_freedv_usb_volume_flush());
    assert(cfg_freedv_usb_volume_target("third-headset",&v) && !v.present && v.percent==30);
    assert(cfg_freedv_usb_volume_target("new-headset",&v) && v.percent==82);
    assert(cfg_freedv_usb_preview_enabled());assert(cfg_freedv_usb_set_preview_enabled(false));
    assert(!cfg_freedv_usb_preview_enabled());assert(cfg_sm.p_volume.get()==27);
    /* New profiles are bounded, while 0 remains an actual saved setting. */
    for(unsigned i=0;i<40;i++){char id[32];std::snprintf(id,sizeof(id),"bounded-%u",i);assert(cfg_freedv_usb_save_volume(id,i%101,false));}
    unsigned rows=0;for(char ch:cfg_sm.p_freedv_usb_volumes.get())rows+=ch=='\n';assert(rows==32);
    cfg_sm.pending_writes_.flush_storage(StorageType::GLOBAL,-1);
    ParamsTable::Shutdown();assert(sqlite3_close(db)==SQLITE_OK);
    std::puts("PASS volume prefs: immutable legacy migration, zero vs absent, latest settled debounce, failure, device key, preview and bounds");
}
