// SPDX-License-Identifier: LGPL-2.1-or-later
#include "src/cfg/settings_manager.h"
#include "src/cfg/freedv_preferences.h"
#include "src/freedv/voice_types.h"
#include <cassert>
#include <atomic>
#include <chrono>
#include <cstdio>
#include <thread>
#include <sys/wait.h>
#include <unistd.h>
extern "C" bool scheduler_on_gui_thread(void){return true;}
extern "C" bool freedv_mode_active(void){return false;}
extern "C" bool freedv_mode_request(fd_local_operation,int32_t){return false;}
static int reject_writes(void*,int action,const char*,const char*,const char*,const char*){
    return action==SQLITE_INSERT || action==SQLITE_UPDATE || action==SQLITE_DELETE?SQLITE_DENY:SQLITE_OK;
}
static void drain_volume(){
    const auto until=std::chrono::steady_clock::now()+std::chrono::seconds(3);
    do{
        cfg_freedv_usb_volume_poll();
        if(!cfg_freedv_usb_volume_busy()){cfg_freedv_usb_volume_poll();return;}
        std::this_thread::sleep_for(std::chrono::milliseconds(1));
    }while(std::chrono::steady_clock::now()<until);
    assert(!"volume storage did not finish its bounded test job");
}
static std::atomic<bool> storage_entered{false},release_storage{false};
static std::atomic<unsigned> volume_writes{0};
static int pause_write(void*,int action,const char *table,const char*,const char*,const char*){
    if(action==SQLITE_INSERT && table && !std::strcmp(table,"params") && !release_storage.load()){
        storage_entered=true;
        const auto until=std::chrono::steady_clock::now()+std::chrono::seconds(3);
        while(!release_storage.load() && std::chrono::steady_clock::now()<until)
            std::this_thread::sleep_for(std::chrono::milliseconds(1));
        assert(release_storage.load());
    }
    return SQLITE_OK;
}
static void count_write(void*,int,const char*,const char *table,sqlite3_int64){
    if(table && !std::strcmp(table,"params"))++volume_writes;
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
    assert(!cfg_freedv_usb_confirm_legacy_volume("legacy-selected",46,true));
    drain_volume();assert(cfg_freedv_usb_confirm_legacy_volume("legacy-selected",46,true));
    assert(cfg_freedv_usb_volume_target("legacy-selected",&v) && v.present && !v.legacy_import && v.percent==46 && v.muted);
    assert(cfg_freedv_usb_save_volume("new-headset",0,false));
    drain_volume();
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
    drain_volume();
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
    assert(cfg_freedv_usb_volume_observe("new-headset",5,true,81,false,2700));
    drain_volume();
    assert(!cfg_freedv_usb_volume_saved());
    assert(cfg_freedv_usb_volume_target("new-headset",&v) && v.percent==81 && !v.muted);
    auto stored_volume=storage_policy_for(StorageType::GLOBAL).load_text(0,"freedv_usb_volumes");
    assert(stored_volume && stored_volume->find("6e65772d68656164736574:72:1\n")!=std::string::npos);
    sqlite3_set_authorizer(db,nullptr,nullptr);
    assert(cfg_freedv_usb_volume_observe("new-headset",6,true,81,false,2800));
    assert(cfg_freedv_usb_volume_flush());drain_volume();assert(cfg_freedv_usb_volume_saved());
    assert(cfg_freedv_usb_volume_target("new-headset",&v) && v.percent==81 && !v.muted);
    stored_volume=storage_policy_for(StorageType::GLOBAL).load_text(0,"freedv_usb_volumes");
    assert(stored_volume && stored_volume->find("6e65772d68656164736574:81:0\n")!=std::string::npos);
    /* A changed selected preference never changes a readback's physical key. */
    assert(cfg_freedv_usb_select_headset("third-headset"));
    assert(cfg_freedv_usb_volume_observe("new-headset",7,true,82,false,3000));
    assert(cfg_freedv_usb_volume_flush());drain_volume();
    assert(cfg_freedv_usb_volume_target("third-headset",&v) && !v.present && v.percent==30);
    assert(cfg_freedv_usb_volume_target("new-headset",&v) && v.percent==82);
    assert(cfg_freedv_usb_preview_enabled());assert(cfg_freedv_usb_set_preview_enabled(false));
    assert(!cfg_freedv_usb_preview_enabled());assert(cfg_sm.p_volume.get()==27);
    /* Hold the real SQLite statement/transaction lock inside the worker.
     * Every GUI operation below must finish before that I/O is released.
     * Burst snapshots coalesce, and a second physical device is retained. */
    assert(cfg_freedv_usb_select_headset("new-headset"));
    sqlite3_update_hook(db,count_write,nullptr);sqlite3_set_authorizer(db,pause_write,nullptr);
    assert(cfg_freedv_usb_save_volume("other-headset",19,false));
    auto wait_until=std::chrono::steady_clock::now()+std::chrono::seconds(2);
    while(!storage_entered.load() && std::chrono::steady_clock::now()<wait_until)
        std::this_thread::sleep_for(std::chrono::milliseconds(1));
    assert(storage_entered.load());const auto gui_started=std::chrono::steady_clock::now();
    for(unsigned i=0;i<32;++i){
        assert(cfg_freedv_usb_volume_observe("new-headset",100+i,true,i%10,false,4000+i));
        assert(cfg_freedv_usb_volume_flush());cfg_freedv_usb_volume_poll();
        assert(!cfg_freedv_usb_volume_saved());
    }
    /* Ordinary mode exit can time out its GUI drain while this immutable job
     * keeps running. An immediate reentry must use the latest confirmed low
     * value, not restore the older/louder durable 82. Neither same-device
     * selection nor target lookup may wait behind the blocked statement. */
    assert(cfg_freedv_usb_select_headset("new-headset"));
    assert(cfg_freedv_usb_volume_target("new-headset",&v) && v.present && v.percent==1 && !v.muted);
    assert(std::chrono::steady_clock::now()-gui_started<std::chrono::milliseconds(100));
    assert(cfg_freedv_usb_volume_busy() && !release_storage.load());release_storage=true;drain_volume();
    assert(volume_writes.load()==2 && cfg_freedv_usb_volume_saved());
    assert(cfg_freedv_usb_volume_target("new-headset",&v) && v.percent==1 && !v.muted);
    assert(cfg_freedv_usb_volume_target("other-headset",&v) && v.percent==19);
    sqlite3_update_hook(db,nullptr,nullptr);sqlite3_set_authorizer(db,nullptr,nullptr);
    /* New profiles are bounded, while 0 remains an actual saved setting. */
    for(unsigned i=0;i<40;i++){char id[32];std::snprintf(id,sizeof(id),"bounded-%u",i);assert(cfg_freedv_usb_save_volume(id,i%101,false));}
    drain_volume();
    unsigned rows=0;for(char ch:cfg_sm.p_freedv_usb_volumes.get())rows+=ch=='\n';assert(rows==32);
    cfg_sm.pending_writes_.flush_storage(StorageType::GLOBAL,-1);
    cfg_freedv_usb_volume_storage_shutdown();cfg_freedv_usb_volume_storage_shutdown();
    assert(!cfg_freedv_usb_save_volume("after-shutdown",30,false));
    ParamsTable::Shutdown();assert(sqlite3_close(db)==SQLITE_OK);
    std::puts("PASS volume prefs: async blocked-storage/coalescing receipts, immutable migration, zero vs absent, latest settled debounce, failure, device keys, preview and bounds");
}
