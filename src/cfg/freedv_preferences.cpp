// SPDX-License-Identifier: LGPL-2.1-or-later
#include "freedv_preferences.h"
#include "settings_manager.h"
#include "../freedv/writer_gate.h"
#include <algorithm>
#include <cstdio>
#include <cstdlib>
#include <cstring>
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
} volume_pending;
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
bool save_volume(const std::string &id,unsigned percent,bool muted) {
    if(id.empty() || percent>100)return false;
    auto list=volume_profiles();list.erase(std::remove_if(list.begin(),list.end(),[&](const VolumeEntry&e){return e.id==id;}),list.end());
    if(list.size()>=32)list.erase(list.begin());
    list.push_back({id,percent,muted});
    std::string data;for(const auto&e:list)data+=e.id+":"+std::to_string(e.percent)+":"+(e.muted?"1\n":"0\n");
    return durable(cfg_sm.p_freedv_usb_volumes,data);
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
    const std::string encoded=key(id);if(encoded.empty() || !out)return false;
    (void)cfg_freedv_usb_capture_legacy_volume();*out={false,false,30,false};
    for(const auto&e:volume_profiles())if(e.id==encoded){*out={true,false,e.percent,e.muted};return true;}
    out->legacy_import=!cfg_sm.p_freedv_usb_volume_migrated.get() && legacy_volume_id()==id;
    return true;
}
extern "C" bool cfg_freedv_usb_save_volume(const char *id,unsigned percent,bool muted){
    return scheduler_on_gui_thread() && save_volume(key(id),percent,muted);
}
extern "C" bool cfg_freedv_usb_confirm_legacy_volume(const char *id,unsigned percent,bool muted){
    if(!scheduler_on_gui_thread() || !id || percent>100)return false;
    if(cfg_sm.p_freedv_usb_volume_migrated.get())return true;
    (void)cfg_freedv_usb_capture_legacy_volume();if(legacy_volume_id()!=id)return false;
    cfg_freedv_usb_volume target;
    if(!cfg_freedv_usb_volume_target(id,&target))return false;
    if(!target.present && !cfg_freedv_usb_save_volume(id,percent,muted))return false;
    return durable(cfg_sm.p_freedv_usb_volume_migrated,int32_t(1));
}
extern "C" bool cfg_freedv_usb_volume_observe(const char *id,uint64_t revision,bool settled,
    unsigned percent,bool muted,uint64_t monotonic_ms){
    if(!scheduler_on_gui_thread())return false;
    if(!settled || !revision || key(id).empty() || percent>100){volume_pending.valid=false;volume_pending.saved=false;return true;}
    const bool changed=!volume_pending.valid || volume_pending.id!=id || volume_pending.revision!=revision ||
        volume_pending.percent!=percent || volume_pending.muted!=muted;
    if(changed){volume_pending={id,revision,monotonic_ms,percent,muted,true,false};return true;}
    if(volume_pending.saved || monotonic_ms-volume_pending.since<500)return true;
    volume_pending.since=monotonic_ms; /* failed persistence retries stay bounded */
    return cfg_freedv_usb_volume_flush();
}
extern "C" bool cfg_freedv_usb_volume_flush(void){
    if(!scheduler_on_gui_thread())return false;
    if(!volume_pending.valid || volume_pending.saved)return true;
    bool ok=cfg_freedv_usb_save_volume(volume_pending.id.c_str(),volume_pending.percent,volume_pending.muted);
    if(ok){
        if(!cfg_sm.p_freedv_usb_volume_migrated.get() && legacy_volume_id()==volume_pending.id)
            ok=cfg_freedv_usb_confirm_legacy_volume(volume_pending.id.c_str(),volume_pending.percent,volume_pending.muted);
    }
    volume_pending.saved=ok;return ok;
}
extern "C" bool cfg_freedv_usb_volume_saved(void){return volume_pending.valid && volume_pending.saved;}
extern "C" bool cfg_freedv_usb_preview_enabled(void){return cfg_sm.p_freedv_usb_preview_enabled.get()!=0;}
extern "C" bool cfg_freedv_usb_set_preview_enabled(bool enabled){
    return scheduler_on_gui_thread() && durable(cfg_sm.p_freedv_usb_preview_enabled,int32_t(enabled));
}
