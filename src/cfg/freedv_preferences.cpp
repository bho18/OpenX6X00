// SPDX-License-Identifier: LGPL-2.1-or-later
#include "freedv_preferences.h"
#include "settings_manager.h"
#include "../freedv/writer_gate.h"
#include <algorithm>
#include <cassert>
#include <condition_variable>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <mutex>
#include <thread>
#include <vector>
extern "C" {
#include "../scheduler.h"
}

namespace {
struct Entry {std::string id;int gain;};
struct VolumeEntry {std::string id;unsigned percent;bool muted;};
std::string legacy_session_id;
bool legacy_session_captured=false,legacy_capture_warned=false;
struct VolumePending {
    std::string id;
    uint64_t revision=0,since=0;
    unsigned percent=0;
    bool muted=false,valid=false,saved=false;
    uint64_t queued_generation=0;
} volume_pending;
struct VolumeJob {
    uint64_t generation=0;
    std::string profiles;
    bool migrate=false;
};
struct VolumeReceipt {
    VolumeJob job;
    bool profiles_saved=false,migration_saved=false;
};
/* Only immutable snapshots cross this queue. In particular the worker never
 * reads cfg_sm, a Parameter, the selected headset, or an LVGL object. The
 * queue mutex is released before acquiring the DB transaction/statement
 * mutexes, so a slow SD card cannot hold up a dial event or a GUI poll. */
struct VolumeStorage {
    std::mutex mutex;
    std::condition_variable changed;
    std::thread thread;
    VolumeJob pending;
    std::vector<VolumeReceipt> completed;
    bool queued=false,running=false,closed=false;
    ~VolumeStorage(){close();}
    void close(){
        {std::lock_guard<std::mutex> lock(mutex);closed=true;}
        changed.notify_all();
        if(thread.joinable())thread.join();
    }
} volume_storage;
std::vector<VolumeEntry> volume_shadow;
bool volume_shadow_loaded=false,legacy_migration_requested=false;
uint64_t volume_generation=0,legacy_volume_generation=0;
/* Hex IDs keep delimiters and device-supplied strings out of the storage
 * grammar. The bounded profile list also bounds startup parsing. */
std::string key(const char *id) {
    if(!id || !*id || std::strlen(id)>255)return {};
    static const char hex[]="0123456789abcdef";std::string out;
    for(const unsigned char *p=(const unsigned char *)id;*p;++p){out+=hex[*p>>4];out+=hex[*p&15];}
    return out;
}
std::vector<Entry> profiles() {
    std::vector<Entry> out;const std::string data=cfg_sm.p_freedv_usb_gains.get();
    if(data.size()>32768)return out;
    size_t start=0;
    while(start<data.size() && out.size()<32){
        size_t end=data.find('\n',start);if(end==std::string::npos)end=data.size();
        std::string row=data.substr(start,end-start);size_t colon=row.find(':');
        if(colon!=std::string::npos && colon>0 && colon<=510 && !(colon&1) &&
           row.find_first_not_of("0123456789abcdef",0)>=colon){
            char *tail=nullptr;long value=std::strtol(row.c_str()+colon+1,&tail,10);
            if(tail && tail!=row.c_str()+colon+1 && !*tail && value>=-12 && value<=24)
                out.push_back({row.substr(0,colon),(int)value});
        }
        start=end+1;
    }
    return out;
}
struct Scope {
    bool voice=fd_writer_voice_owned(),ok=false;
    Scope(){ok=voice?fd_writer_voice_begin():fd_writer_enter();}
    ~Scope(){if(ok){if(voice)fd_writer_voice_end();else fd_writer_exit();}}
};
template<class T> bool durable(Parameter<T> &param,const T &value) {
    Scope scope;if(!scope.ok)return false;
    auto &store=storage_policy_for(StorageType::GLOBAL);int rc;
    if constexpr(std::is_same_v<T,std::string>)rc=store.save_text(0,param.db_name(),value);
    else rc=store.save_int(0,param.db_name(),value);
    if(rc!=SUCCESS)return false;
    param.set_quiet(value);return param.get()==value;
}
bool save_profile(const std::string &id,int gain) {
    if(id.empty())return false;
    auto list=profiles();list.erase(std::remove_if(list.begin(),list.end(),[&](const Entry&e){return e.id==id;}),list.end());
    if(list.size()>=32)list.erase(list.begin());
    list.push_back({id,std::clamp(gain,-12,24)});std::string data;
    for(const auto &entry:list)data+=entry.id+":"+std::to_string(entry.gain)+"\n";
    return durable(cfg_sm.p_freedv_usb_gains,data);
}
std::vector<VolumeEntry> volume_profiles() {
    std::vector<VolumeEntry> out;const std::string data=cfg_sm.p_freedv_usb_volumes.get();
    if(data.size()>32768)return out;
    size_t start=0;
    while(start<data.size() && out.size()<32){
        size_t end=data.find('\n',start);if(end==std::string::npos)end=data.size();
        std::string row=data.substr(start,end-start);size_t colon=row.find(':');
        if(colon!=std::string::npos && colon>0 && colon<=510 && !(colon&1) &&
           row.find_first_not_of("0123456789abcdef",0)>=colon){
            unsigned percent=0,muted=0;int used=0;
            if(std::sscanf(row.c_str()+colon+1,"%u:%u%n",&percent,&muted,&used)==2 &&
               used>0 && row[colon+1+(size_t)used]=='\0' && percent<=100 && muted<=1){
                const std::string id=row.substr(0,colon);
                out.erase(std::remove_if(out.begin(),out.end(),[&](const VolumeEntry&e){return e.id==id;}),out.end());
                out.push_back({id,percent,muted!=0});
            }
        }
        start=end+1;
    }
    return out;
}
void volume_storage_worker(){
    for(;;){
        VolumeJob job;
        {
            std::unique_lock<std::mutex> lock(volume_storage.mutex);
            volume_storage.changed.wait(lock,[]{return volume_storage.queued || volume_storage.closed;});
            if(!volume_storage.queued)return;
            job=std::move(volume_storage.pending);volume_storage.queued=false;volume_storage.running=true;
        }
        VolumeReceipt result;result.job=std::move(job);
        cfg_db_tx_lock();
        /* Do not acknowledge somebody else's uncommitted transaction. The
         * ordinary deferred-write worker follows the same transaction rule. */
        if(!cfg_db_in_transaction()){
            auto &store=storage_policy_for(StorageType::GLOBAL);
            result.profiles_saved=store.save_text(0,"freedv_usb_volumes",result.job.profiles)==SUCCESS;
            if(result.profiles_saved && result.job.migrate)
                result.migration_saved=store.save_int(0,"freedv_usb_volume_migrated",1)==SUCCESS;
        }
        cfg_db_tx_unlock();
        {
            std::lock_guard<std::mutex> lock(volume_storage.mutex);
            /* A GUI enqueue first polls receipts, and at most one job runs
             * while one latest snapshot waits. Thus at most two can finish
             * between GUI polls, including an exit/shutdown drain. */
            assert(volume_storage.completed.size()<2);
            volume_storage.completed.push_back(std::move(result));volume_storage.running=false;
        }
    }
}
void volume_storage_poll(){
    Scope scope;if(!scope.ok)return;
    std::vector<VolumeReceipt> completed;
    {std::lock_guard<std::mutex> lock(volume_storage.mutex);completed.swap(volume_storage.completed);}
    for(const auto &result:completed){
        if(result.profiles_saved)cfg_sm.p_freedv_usb_volumes.set_quiet(result.job.profiles);
        if(result.migration_saved)cfg_sm.p_freedv_usb_volume_migrated.set_quiet(1);
        if(volume_pending.valid && volume_pending.queued_generation==result.job.generation)
            volume_pending.saved=result.profiles_saved && (!result.job.migrate || result.migration_saved);
    }
}
uint64_t queue_volume(const std::string &id,unsigned percent,bool muted){
    if(id.empty() || percent>100)return 0;
    Scope scope;if(!scope.ok)return 0;
    volume_storage_poll();
    if(!volume_shadow_loaded){volume_shadow=volume_profiles();volume_shadow_loaded=true;}
    volume_shadow.erase(std::remove_if(volume_shadow.begin(),volume_shadow.end(),[&](const VolumeEntry&e){return e.id==id;}),volume_shadow.end());
    if(volume_shadow.size()>=32)volume_shadow.erase(volume_shadow.begin());
    volume_shadow.push_back({id,percent,muted});
    std::string data;for(const auto&e:volume_shadow)data+=e.id+":"+std::to_string(e.percent)+":"+(e.muted?"1\n":"0\n");
    uint64_t generation=0;
    {
        std::lock_guard<std::mutex> lock(volume_storage.mutex);
        if(volume_storage.closed)return 0;
        if(!volume_storage.thread.joinable()){
            try{volume_storage.thread=std::thread(volume_storage_worker);}catch(...){return 0;}
        }
        generation=++volume_generation;
        volume_storage.pending={generation,std::move(data),legacy_migration_requested && !cfg_sm.p_freedv_usb_volume_migrated.get()};
        volume_storage.queued=true;
    }
    volume_storage.changed.notify_one();return generation;
}
std::string legacy_volume_id() {
    std::string id=cfg_sm.p_freedv_usb_volume_legacy_id.get();return id.empty()?legacy_session_id:id;
}
}
extern "C" bool cfg_freedv_usage_hidden(void){return cfg_sm.p_freedv_usage_hidden.get()!=0;}
extern "C" bool cfg_freedv_usage_set_hidden(bool hidden){return durable(cfg_sm.p_freedv_usage_hidden,int32_t(hidden));}
extern "C" void cfg_freedv_usb_headset_id(char *out,size_t size){if(out && size)std::snprintf(out,size,"%s",cfg_sm.p_freedv_usb_headset.get().c_str());}
extern "C" bool cfg_freedv_usb_select_headset(const char *id){
    if(!scheduler_on_gui_thread() || key(id).empty())return false;
    /* Volume migration is optional metadata. Retain its original identity in
     * this session and retry it, but never make it a device-selection gate. */
    (void)cfg_freedv_usb_capture_legacy_volume();
    /* This subject is only updated after a successful durable selection.
     * Reentering the same device must not wait behind pending volume I/O. */
    if(cfg_sm.p_freedv_usb_headset.get()==id)return true;
    return durable(cfg_sm.p_freedv_usb_headset,std::string(id));
}
extern "C" int cfg_freedv_usb_gain_for(const char *id,const char *name){
    const std::string encoded=key(id);if(encoded.empty())return 0;
    for(const auto&e:profiles())if(e.id==encoded)return e.gain;
    /* The previous beta had one global preference for the qualified Loop110.
     * Import only an actually persisted value and only for that device. */
    if(!cfg_sm.p_freedv_usb_gain_migrated.get() && name && std::strstr(name,"Loop110")){
        const auto previous=storage_policy_for(StorageType::GLOBAL).load_int(0,"freedv_usb_mic_gain");
        const int gain=previous?std::clamp((int)*previous,-12,24):0;
        if(save_profile(encoded,gain))durable(cfg_sm.p_freedv_usb_gain_migrated,int32_t(1));
        return gain;
    }
    /* Selecting a different first device ends legacy migration: a later
     * Loop110 must not inherit that new device's compatibility gain value. */
    if(!cfg_sm.p_freedv_usb_gain_migrated.get())durable(cfg_sm.p_freedv_usb_gain_migrated,int32_t(1));
    return 0;
}
extern "C" bool cfg_freedv_usb_save_gain(const char *id,int gain){
    if(gain< -12 || gain>24 || !save_profile(key(id),gain))return false;
    /* Keep the old preference useful for older images, without using it as a
     * default for any newly selected headset. */
    return durable(cfg_sm.p_freedv_usb_mic_gain,int32_t(gain));
}
extern "C" bool cfg_freedv_usb_capture_legacy_volume(void){
    if(!scheduler_on_gui_thread())return false;
    if(cfg_sm.p_freedv_usb_volume_migrated.get() || !cfg_sm.p_freedv_usb_volume_legacy_id.get().empty())return true;
    if(!legacy_session_captured){legacy_session_id=cfg_sm.p_freedv_usb_headset.get();legacy_session_captured=true;}
    /* The captured session identity is already sufficient for correct
     * migration. Optional metadata can be retried after the worker finishes;
     * reentry must not synchronously queue behind its card I/O. */
    if(cfg_freedv_usb_volume_busy())return false;
    const bool ok=key(legacy_session_id.c_str()).empty()?
        durable(cfg_sm.p_freedv_usb_volume_migrated,int32_t(1)):
        durable(cfg_sm.p_freedv_usb_volume_legacy_id,legacy_session_id);
    if(!ok && !legacy_capture_warned){
        std::fputs("FreeDV: optional listening-volume migration was not saved; retrying the original headset identity\n",stderr);
        legacy_capture_warned=true;
    }else if(ok)legacy_capture_warned=false;
    return ok;
}
extern "C" bool cfg_freedv_usb_volume_target(const char *id,cfg_freedv_usb_volume *out){
    if(!scheduler_on_gui_thread())return false;
    volume_storage_poll();
    const std::string encoded=key(id);if(encoded.empty() || !out)return false;
    (void)cfg_freedv_usb_capture_legacy_volume();*out={false,false,30,false};
    /* Every shadow entry came from an actual confirmed readback accepted for
     * saving, never an optimistic volume request. It remains the right level
     * for this process even if an earlier card write failed or is pending.
     * saved() and the completion receipt alone describe disk durability. */
    if(volume_shadow_loaded)for(const auto&e:volume_shadow)if(e.id==encoded){*out={true,false,e.percent,e.muted};return true;}
    for(const auto&e:volume_profiles())if(e.id==encoded){*out={true,false,e.percent,e.muted};return true;}
    out->legacy_import=!cfg_sm.p_freedv_usb_volume_migrated.get() && legacy_volume_id()==id;
    return true;
}
extern "C" bool cfg_freedv_usb_save_volume(const char *id,unsigned percent,bool muted){
    return scheduler_on_gui_thread() && queue_volume(key(id),percent,muted)!=0;
}
extern "C" bool cfg_freedv_usb_confirm_legacy_volume(const char *id,unsigned percent,bool muted){
    if(!scheduler_on_gui_thread() || !id || percent>100)return false;
    volume_storage_poll();
    if(cfg_sm.p_freedv_usb_volume_migrated.get())return true;
    /* Entry captures the legacy identity before selecting another headset.
     * Meter/readback observation must never retry synchronous SQLite here. */
    if(legacy_volume_id()!=id || !*id)return false;
    if(!legacy_volume_generation){
        legacy_migration_requested=true;
        legacy_volume_generation=queue_volume(key(id),percent,muted);
    }
    return false; /* Only a worker receipt can complete this migration. */
}
extern "C" bool cfg_freedv_usb_volume_observe(const char *id,uint64_t revision,bool settled,
    unsigned percent,bool muted,uint64_t monotonic_ms){
    if(!scheduler_on_gui_thread())return false;
    volume_storage_poll();
    if(!settled || !revision || key(id).empty() || percent>100){volume_pending.valid=false;volume_pending.saved=false;return true;}
    const bool changed=!volume_pending.valid || volume_pending.id!=id || volume_pending.revision!=revision ||
        volume_pending.percent!=percent || volume_pending.muted!=muted;
    if(changed){volume_pending={id,revision,monotonic_ms,percent,muted,true,false};return true;}
    if(volume_pending.saved || volume_pending.queued_generation || monotonic_ms-volume_pending.since<500)return true;
    return cfg_freedv_usb_volume_flush();
}
extern "C" bool cfg_freedv_usb_volume_flush(void){
    if(!scheduler_on_gui_thread())return false;
    volume_storage_poll();
    if(!volume_pending.valid || volume_pending.saved)return true;
    if(volume_pending.queued_generation)return true;
    if(!cfg_sm.p_freedv_usb_volume_migrated.get() && legacy_volume_id()==volume_pending.id)legacy_migration_requested=true;
    volume_pending.queued_generation=queue_volume(key(volume_pending.id.c_str()),volume_pending.percent,volume_pending.muted);
    return volume_pending.queued_generation!=0;
}
extern "C" bool cfg_freedv_usb_volume_saved(void){return volume_pending.valid && volume_pending.saved;}
extern "C" void cfg_freedv_usb_volume_poll(void){if(scheduler_on_gui_thread())volume_storage_poll();}
extern "C" bool cfg_freedv_usb_volume_busy(void){
    std::lock_guard<std::mutex> lock(volume_storage.mutex);return volume_storage.queued || volume_storage.running;
}
extern "C" void cfg_freedv_usb_volume_storage_shutdown(void){
    /* DB teardown owns the lifetime boundary. Ordinary mode exit uses poll /
     * busy instead, and never joins a worker that may be doing card I/O. */
    volume_storage.close();
    if(scheduler_on_gui_thread())volume_storage_poll();
}
extern "C" bool cfg_freedv_usb_preview_enabled(void){return cfg_sm.p_freedv_usb_preview_enabled.get()!=0;}
extern "C" bool cfg_freedv_usb_set_preview_enabled(bool enabled){
    return scheduler_on_gui_thread() && durable(cfg_sm.p_freedv_usb_preview_enabled,int32_t(enabled));
}
