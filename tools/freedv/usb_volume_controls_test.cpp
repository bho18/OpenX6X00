// SPDX-License-Identifier: LGPL-2.1-or-later
/* Production knob selectors, value dispatch and both displays. Only mode,
 * radio/audio and unrelated app endpoints are synthetic; no device is used. */
#define ENABLE_FREEDV_VOICE 1
#define ENABLE_FREEDV_USB_VOICE 1
#include "src/knob_info.cpp"
#include "src/radio_controls.cpp"
#include "src/vol_control.cpp"
#include "src/mfk_control.cpp"
#include <cassert>
#include <cstdarg>
#include <cstring>
static bool usb_active_test=false;
static unsigned headphone_adjustments,cue_requests,power_requests,native_volume_calls;
static int headphone_percent=46;
static rotary_t vol_device{};
rotary_t *vol=&vol_device;
encoder_t *mfk=nullptr;
extern "C" {
lv_style_t knobs_style;
theme_shape_t theme_shape{};
uint32_t theme_hex(theme_color_t){return 0;}
void buttons_refresh_marks(void){}
bool scheduler_on_gui_thread(void){return true;}
bool front_keys_voice_local_event(void){return true;}
bool freedv_usb_active(void){return usb_active_test;}
bool freedv_usb_finishing(void){return false;}
bool freedv_mode_active(void){return usb_active_test;}
bool freedv_mode_request(fd_local_operation operation,int32_t value){
    assert(operation==FD_LOCAL_POWER);++power_requests;
    assert(fd_writer_voice_begin());cfg_sm.p_pwr.set(value/10.0f);fd_writer_voice_end();return true;
}
bool freedv_usb_volume(int diff){
    assert(diff);++headphone_adjustments;++cue_requests;
    headphone_percent=std::clamp(headphone_percent+diff,0,100);
    knobs_usb_volume_update(true,true,(unsigned)headphone_percent,false,false);return true;
}
uint16_t radio_change_vol(int16_t diff){++native_volume_calls;cfg_sm.p_volume.set(cfg_sm.p_volume.get()+diff);return (uint16_t)cfg_sm.p_volume.get();}
uint32_t radio_caps(void){return 0;}
void msg_update_text_fmt(const char*,...){}
bool walkie_on(void){return false;}
void cluster_column_set_active(bool){}
const char *cluster_column_move(int){return "";}
const cluster_filter_t *cluster_filter(){static cluster_filter_t filter{};return &filter;}
bool cluster_filter_save(uint8_t,uint16_t,uint8_t){return true;}
char *params_comp_str_get(uint8_t){static char text[]="Off";return text;}
char *params_key_mode_str_get(radio_key_mode_t){static char text[]="Manual";return text;}
char *params_iambic_mode_str_ger(radio_iambic_mode_t){static char text[]="A";return text;}
float rtty_change_rate(int16_t){return 0;}
uint16_t rtty_change_shift(int16_t){return 0;}
uint16_t rtty_change_center(int16_t){return 0;}
bool rtty_change_reverse(int16_t){return false;}
}
int main(){
    lv_init();cfg_sm.p_volume.set_quiet(27);cfg_sm.p_pwr.set_quiet(5.0f);cfg_sm.p_waterfall_ref.set_quiet(-30);
    std::string binds=cfg_sm.p_encoder_bind.get();std::fill(binds.begin(),binds.end(),ENCODER_BIND_NONE);
    binds[CTRL_VOL]=ENCODER_BIND_MFK;binds[CTRL_WF_REF]=ENCODER_BIND_MFK;
    binds[CTRL_PWR]=ENCODER_BIND_VOL;binds[CTRL_SQL]=ENCODER_BIND_VOL;cfg_sm.p_encoder_bind.set_quiet(binds);
    vol_set_ctrl(CTRL_PWR);mfk_set_ctrl(CTRL_VOL);
    const unsigned native_before=native_volume_calls; /* ordinary entry display may read speaker level */
    vol->state=VOL_STATE_SELECT;knobs_set_vol_state(false);mfk_state=MFK_STATE_SELECT;knobs_set_mfk_state(false);
    assert(fd_writer_voice_reserve());usb_active_test=true;knobs_usb_volume_update(true,true,46,false,false);
    assert(vol_get_ctrl()==CTRL_PWR && mfk_ctrl==CTRL_VOL && vol->state==VOL_STATE_SELECT && mfk_state==MFK_STATE_SELECT);
    assert(cfg_sm.p_encoder_bind.get()==binds);
    unsigned hp=headphone_adjustments,cues=cue_requests,power=power_requests;
    vol_change_ctrl(1);mfk_change_ctrl(1); /* SELECT routes only through the actual binding list. */
    assert(vol_get_ctrl()==CTRL_SQL && mfk_ctrl==CTRL_WF_REF);
    vol_change_ctrl(-1);mfk_change_ctrl(-1);
    assert(vol_get_ctrl()==CTRL_PWR && mfk_ctrl==CTRL_VOL);
    assert(headphone_adjustments==hp && cue_requests==cues && power_requests==power && cfg_sm.p_volume.get()==27);
    mfk_state=MFK_STATE_EDIT;knobs_set_mfk_state(true);mfk_update(1);
    assert(headphone_adjustments==hp+1 && cue_requests==cues+1 && headphone_percent==47 && cfg_sm.p_volume.get()==27);
    mfk_update(0);assert(headphone_adjustments==hp+1 && cue_requests==cues+1);
    vol->state=VOL_STATE_EDIT;knobs_set_vol_state(true);vol_update(1);
    assert(power_requests==power+1 && std::fabs(cfg_sm.p_pwr.get()-5.1f)<.001f && headphone_adjustments==hp+1);
    vol_update(0);assert(power_requests==power+1 && cue_requests==cues+1);
    mfk_set_ctrl(CTRL_WF_REF);mfk_update(2);
    assert(cfg_sm.p_waterfall_ref.get()==-28 && cfg_sm.p_volume.get()==27 && headphone_adjustments==hp+1);
    /* Exit refresh changes the CTRL_VOL view only, preserving both live choices. */
    usb_active_test=false;knobs_usb_volume_update(false,false,0,false,false);fd_writer_release();
    assert(vol_get_ctrl()==CTRL_PWR && mfk_ctrl==CTRL_WF_REF && vol->state==VOL_STATE_EDIT && mfk_state==MFK_STATE_EDIT);
    assert(cfg_sm.p_encoder_bind.get()==binds && native_volume_calls==native_before && cfg_sm.p_volume.get()==27);
    std::puts("PASS production volume routers: native binding order, SELECT vs EDIT, HP-only volume, native power, WF reference and entry/exit preservation");
}
