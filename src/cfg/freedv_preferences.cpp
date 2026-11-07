// SPDX-License-Identifier: LGPL-2.1-or-later
#include "freedv_preferences.h"
#include "settings_manager.h"
#include "../freedv/writer_gate.h"
#include <algorithm>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <vector>

namespace {
struct Entry {std::string id;int gain;};
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
}
extern "C" bool cfg_freedv_usage_hidden(void){return cfg_sm.p_freedv_usage_hidden.get()!=0;}
extern "C" bool cfg_freedv_usage_set_hidden(bool hidden){return durable(cfg_sm.p_freedv_usage_hidden,int32_t(hidden));}
extern "C" void cfg_freedv_usb_headset_id(char *out,size_t size){if(out && size)std::snprintf(out,size,"%s",cfg_sm.p_freedv_usb_headset.get().c_str());}
extern "C" bool cfg_freedv_usb_select_headset(const char *id){return !key(id).empty() && durable(cfg_sm.p_freedv_usb_headset,std::string(id));}
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
