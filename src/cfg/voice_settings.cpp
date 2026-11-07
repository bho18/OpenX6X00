// SPDX-License-Identifier: LGPL-2.1-or-later
#include "../freedv/features.h"
#ifdef ENABLE_FREEDV_VOICE
#include "voice_settings.h"
#include "settings_manager.h"
#include "settings_manager_api.h"
#include "../freedv/writer_gate.h"
extern "C" {
#include "../scheduler.h"
}
static bool memory_frequency(int32_t id,int32_t &frequency) {
    int32_t mode=0,agc=0,att=0,pre=0;bool hf=false,hm=false,ha=false,ht=false,hp=false;
    return MemoryTable::Load(id,frequency,hf,mode,hm,agc,ha,att,ht,pre,hp) && hf && cfg_is_valid_hw_freq(frequency);
}
bool cfg_voice_resolve(fd_local_operation op,int32_t value,fd_voice_tuning *out) {
    if(!out || !scheduler_on_gui_thread())return false;
    fd_voice_tuning tuning={cfg_sm.cp_fg_freq.get(),(unsigned)cfg_sm.p_band_current_vfo.get(),op,value};
    switch(op) {
    case FD_LOCAL_FREQUENCY:
        if(!cfg_is_valid_hw_freq(value))return false;
        tuning.frequency=value;break;
    case FD_LOCAL_VFO:
        if(value<0 || value>1)return false;
        tuning.vfo=(unsigned)value;
        if(value!=cfg_sm.p_band_current_vfo.get())tuning.frequency=cfg_sm.cp_bg_freq.get();
        break;
    case FD_LOCAL_BAND: {
        auto band=BandsTable::get_by_id(value);
        if(band.rc!=SUCCESS || cfg_sm.band_use(band.value.start_freq,band.value.stop_freq)==BAND_USE_NONE ||
            cfg_transverter_shift_for(band.value.start_freq) || cfg_transverter_shift_for(band.value.stop_freq))return false;
        break;
    }
    case FD_LOCAL_MEMORY:
        if(!memory_frequency(value,tuning.frequency))return false;
        break;
    default:return false;
    }
    *out=tuning;return true;
}
bool cfg_voice_apply(fd_voice_tuning *tuning) {
    if(!tuning || !scheduler_on_gui_thread() || !fd_writer_voice_authorized())return false;
    switch(tuning->operation) {
    case FD_LOCAL_FREQUENCY:cfg_sm.cp_fg_freq.set(tuning->frequency);break;
    case FD_LOCAL_BAND:cfg_sm.p_band_id.set(tuning->request_value);break;
    case FD_LOCAL_VFO:
        if((unsigned)cfg_sm.p_band_current_vfo.get()!=tuning->vfo)cfg_sm.vfo_toggle();
        break;
    case FD_LOCAL_MEMORY:
        /* A memory is a frequency bookmark here. Saved analog mode/processing
         * cannot override the session's qualified hardware profile. */
        cfg_sm.cp_fg_freq.set(tuning->frequency);break;
    default:return false;
    }
    tuning->frequency=cfg_sm.cp_fg_freq.get();
    tuning->vfo=(unsigned)cfg_sm.p_band_current_vfo.get();
    return cfg_is_valid_hw_freq(tuning->frequency);
}
#endif
