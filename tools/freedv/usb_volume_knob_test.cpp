// SPDX-License-Identifier: LGPL-2.1-or-later
/* Include the production view binding, preserving its actual Subject and
 * per-line subscriptions. GUI endpoints unrelated to CTRL_VOL are stubs. */
#include "src/knob_info.cpp"
#include "src/freedv/voice_types.h"
#include <cassert>
#include <cmath>
static bool on_gui=true;
static cfg_ctrl_t selected_vol=CTRL_VOL;
mfk_state_t mfk_state=MFK_STATE_EDIT;
cfg_ctrl_t mfk_ctrl=CTRL_VOL;
cfg_ctrl_t vol_get_ctrl(){return selected_vol;}
extern "C" {
lv_style_t knobs_style;
theme_shape_t theme_shape{};
uint32_t theme_hex(theme_color_t){return 0;}
void buttons_refresh_marks(void){}
bool scheduler_on_gui_thread(void){return on_gui;}
bool freedv_mode_active(void){return false;}
bool freedv_mode_request(fd_local_operation,int32_t){return false;}
char *params_comp_str_get(uint8_t){static char off[]="Off";return off;}
}
const char *cluster_column_move(int){return "";}
extern "C" const cluster_filter_t *cluster_filter(){static cluster_filter_t filter{};return &filter;}
static unsigned notifications;
static void notified(Subject*,void*){++notifications;}
int main(){
    lv_init();cfg_sm.p_volume.set_quiet(27);
    knobs_set_vol_param(CTRL_VOL);knobs_set_mfk_param(CTRL_VOL);
    knobs_set_vol_state(false);knobs_set_mfk_state(true);
    const char *name=nullptr;char value[96];float level=-1;bool editing=true;
    assert(knobs_info(true,&name,value,sizeof(value),&level,&editing));
    assert(!std::strcmp(name,"Volume") && !std::strcmp(value,"27") && !editing);
    Subscription changes(headphone_volume.subscribe(notified,nullptr));
    knobs_usb_volume_update(true,false,0,false,false);assert(notifications==1);
    assert(knobs_info(true,&name,value,sizeof(value),&level,&editing));
    assert(!std::strcmp(name,"Headphones") && !std::strcmp(value,"--") && level==-1 && !editing);
    knobs_usb_volume_update(true,true,46,false,false);assert(notifications==2);
    assert(knobs_info(false,&name,value,sizeof(value),&level,&editing));
    assert(!std::strcmp(value,"46%") && std::fabs(level-.46f)<.001f && editing);
    knobs_usb_volume_update(true,true,46,false,true);
    assert(knobs_usb_volume_text(value,sizeof(value)) && std::strstr(value,"46%") && std::strstr(value,"pending"));
    unsigned percent=0;bool muted=false,pending=false;
    assert(knobs_usb_volume_get(&percent,&muted,&pending) && percent==46 && pending && !muted);
    knobs_usb_volume_update(true,true,0,true,false);
    assert(knobs_info(true,&name,value,sizeof(value),&level,&editing) && level==0 && std::strstr(value,"muted") && !editing);
    on_gui=false;unsigned before=notifications;knobs_usb_volume_update(true,true,99,false,false);
    assert(notifications==before && knobs_usb_volume_get(&percent,&muted,&pending) && percent==0);
    on_gui=true;selected_vol=CTRL_PWR;knobs_set_vol_param(CTRL_PWR);
    knobs_usb_volume_update(false,false,0,false,false);
    assert(selected_vol==CTRL_PWR && mfk_ctrl==CTRL_VOL && mfk_state==MFK_STATE_EDIT);
    assert(knobs_info(false,&name,value,sizeof(value),&level,&editing));
    assert(!std::strcmp(name,"Volume") && !std::strcmp(value,"27") && editing);
    knobs_set_vol_param(CTRL_VOL);
    assert(knobs_info(true,&name,value,sizeof(value),&level,&editing) && !editing);
    assert(cfg_sm.p_volume.get()==27);
    std::puts("PASS effective volume: both CTRL_VOL views/scale, actual pending/mute, observer, GUI thread and selection/edit preservation");
}
