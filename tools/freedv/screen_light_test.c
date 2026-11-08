// SPDX-License-Identifier: LGPL-2.1-or-later
/* Actual stage policy and LVGL invalidation API; hardware/time/timers fake. */
#define _POSIX_C_SOURCE 200809L
#define open test_open
#include "src/screen_light.c"
#include "src/screen_idle.c"
#undef open
#include <assert.h>
#include <stdarg.h>
#include <string.h>

static lv_disp_t display, replacement;
static lv_disp_t *selected_display=&display;
static lv_obj_t screen;
static lv_timer_t timers[128];
static unsigned timer_count,event_count,invalidations,waterfall_redraws,spectrum_redraws;
static uint64_t fake_ms=100;
static fd_usb_display_event events[128];
static bool recording,saver_visible;
static radio_state_t radio_state=RADIO_RX;
params_t params={.brightness_normal=5,.brightness_idle=4,.brightness_timeout=30,.screen_off_min=5};
int test_open(const char *path,int flags,...){(void)path;(void)flags;return -1;}
int clock_gettime(clockid_t clock,struct timespec *out){(void)clock;out->tv_sec=fake_ms/1000;out->tv_nsec=(fake_ms%1000)*1000000;return 0;}
lv_disp_t *lv_disp_get_default(void){return selected_display;}
void lv_obj_invalidate(const lv_obj_t *object){assert(object==&screen);++invalidations;}
void lv_refr_now(lv_disp_t *disp){(void)disp;}
void waterfall_redraw(void){++waterfall_redraws;}
void spectrum_redraw(void){++spectrum_redraws;}
void screen_saver_show(uint8_t kind){assert(kind==SCREEN_SAVER_MATRIX);saver_visible=true;}
void screen_saver_hide(void){saver_visible=false;}
bool screen_rec_is_on(void){return recording;}
radio_state_t radio_get_state(void){return radio_state;}
void radio_backlight_power(bool on){(void)on;}
void params_lock(void){}
void params_unlock(bool *dirty){(void)dirty;}
lv_timer_t *lv_timer_create(lv_timer_cb_t cb,uint32_t period,void *data){
    assert(timer_count<128);lv_timer_t *timer=&timers[timer_count++];memset(timer,0,sizeof(*timer));
    timer->timer_cb=cb;timer->period=period;timer->user_data=data;return timer;
}
void lv_timer_del(lv_timer_t *timer){assert(timer);}
void lv_timer_set_repeat_count(lv_timer_t *timer,int32_t count){timer->repeat_count=count;}
void lv_timer_set_period(lv_timer_t *timer,uint32_t period){timer->period=period;}
void lv_timer_reset(lv_timer_t *timer){assert(timer);}
void _lv_log_add(lv_log_level_t level,const char *file,int line,const char *func,const char *format,...){
    (void)level;(void)file;(void)line;(void)func;(void)format;
}
static void capture(const fd_usb_display_event *value,void *context){
    assert(context==&events && event_count<128);events[event_count++]=*value;
}
static void reset_policy(void){
    memset(&display,0,sizeof(display));display.inv_en_cnt=1;display.act_scr=&screen;
    selected_display=&display;drawing_display=NULL;drawing_blocked=false;drawing=true;
    idle_timer=NULL;shown=5;observer=NULL;observer_context=NULL;display_generation=0;
    viewers=recording=saver_visible=knob_woke=false;stage=SCREEN_LIT;radio_state=RADIO_RX;
    params.screen_off_always=false;params.screen_off_min=5;
    params.screen_saver.x=SCREEN_SAVER_NONE;params.screen_saver_min.x=1;
    idle=(screen_idle_t){0};idle_settings();screen_idle_start(&idle,fake_ms);
    timer_count=event_count=invalidations=waterfall_redraws=spectrum_redraws=0;
    power_fd=level_fd=-1;
}
static void elapsed(uint64_t ms){fake_ms+=ms;stage_cb(NULL);}
static void ordinary_cycles(void){
    reset_policy();backlight_set_observer(capture,&events);
    for(unsigned i=0;i<12;++i){
        elapsed(300000);
        assert(backlight_is_dark() && display.inv_en_cnt==0);
        drawing_update();drawing_update();assert(display.inv_en_cnt==0);
        fake_ms+=1000;assert(backlight_tick());
        assert(!backlight_is_dark() && display.inv_en_cnt==1);
    }
    assert(events[0].reason==FD_USB_DISPLAY_IDLE_BLANK && events[0].dark);
    for(unsigned i=1;i<event_count;++i){
        assert(events[i].generation>events[i-1].generation);
        assert(events[i].monotonic_ms>=events[i-1].monotonic_ms);
    }
    assert(waterfall_redraws>=12 && spectrum_redraws>=12);
    fd_usb_display_event snapshot;backlight_snapshot(&snapshot);
    assert(snapshot.reason==FD_USB_DISPLAY_SNAPSHOT && !snapshot.dark && snapshot.drawing_enabled);
    backlight_set_observer(NULL,NULL);unsigned before=event_count;backlight_switch();assert(event_count==before);
}
static void viewers_and_nested_owner(void){
    reset_policy();
    for(unsigned i=0;i<12;++i){
        backlight_viewers(true);backlight_viewers(true);backlight_viewers(false);backlight_viewers(false);
        assert(display.inv_en_cnt==1);elapsed(300000);assert(display.inv_en_cnt==0);
        backlight_viewers(true);assert(display.inv_en_cnt==1);
        assert(backlight_tick());assert(display.inv_en_cnt==1);
        backlight_viewers(false);assert(display.inv_en_cnt==1);
    }
    lv_disp_enable_invalidation(&display,false);assert(display.inv_en_cnt==0);
    elapsed(300000);assert(display.inv_en_cnt==-1);
    assert(backlight_tick());assert(display.inv_en_cnt==0);
    lv_disp_enable_invalidation(&display,true);assert(display.inv_en_cnt==1);
    elapsed(300000);assert(display.inv_en_cnt==0);
    memset(&replacement,0,sizeof(replacement));replacement.inv_en_cnt=1;replacement.act_scr=&screen;
    selected_display=&replacement;drawing_update();assert(replacement.inv_en_cnt==0);
    assert(backlight_tick());assert(replacement.inv_en_cnt==1 && display.inv_en_cnt==0);
}
static void manual_and_always_off(void){
    reset_policy();backlight_set_observer(capture,&events);
    backlight_switch();assert(backlight_is_dark() && display.inv_en_cnt==0);
    assert(!backlight_tick());assert(backlight_is_dark() && display.inv_en_cnt==0);
    backlight_switch();assert(!backlight_is_dark() && display.inv_en_cnt==1);
    assert(events[0].reason==FD_USB_DISPLAY_MANUAL_POWER);
    params.screen_off_always=true;backlight_settings_changed();assert(display.inv_en_cnt==0);
    assert(!backlight_tick());assert(display.inv_en_cnt==0);
    backlight_viewers(true);assert(display.inv_en_cnt==1);
    backlight_viewers(false);assert(display.inv_en_cnt==0);
    params.screen_off_always=false;backlight_settings_changed();assert(display.inv_en_cnt==1);
}
static void saver_recording_and_radio(void){
    reset_policy();params.screen_saver.x=SCREEN_SAVER_MATRIX;params.screen_off_min=0;
    backlight_settings_changed();backlight_set_observer(capture,&events);
    elapsed(60000);assert(backlight_saver_on() && saver_visible && !backlight_is_dark());
    assert(display.inv_en_cnt==0 && !backlight_seen());
    assert(backlight_knob_tick());assert(!saver_visible && display.inv_en_cnt==1);
    fake_ms+=250;assert(backlight_knob_tick());
    fake_ms+=501;assert(!backlight_knob_tick());
    elapsed(60000);assert(saver_visible);
    radio_state=RADIO_TX;stage_cb(NULL);assert(!saver_visible && display.inv_en_cnt==1);
    elapsed(180000);assert(!saver_visible && !backlight_is_dark());
    radio_state=RADIO_RX;stage_cb(NULL);elapsed(60000);assert(saver_visible);
    recording=true;stage_cb(NULL);assert(!saver_visible && display.inv_en_cnt==1 && backlight_seen());
    elapsed(60000);assert(!saver_visible);
    recording=false;elapsed(60000);assert(saver_visible && display.inv_en_cnt==0);
    backlight_notice();assert(!saver_visible && display.inv_en_cnt==1);
    params.screen_off_min=2;backlight_settings_changed();
    elapsed(60000);assert(saver_visible);
    elapsed(60000);assert(backlight_is_dark() && !saver_visible && display.inv_en_cnt==0);
    assert(backlight_tick() && display.inv_en_cnt==1);
    bool saw_saver=false;
    for(unsigned i=0;i<event_count;++i) if(events[i].reason==FD_USB_DISPLAY_IDLE_SAVER) saw_saver=true;
    assert(saw_saver);
}
int main(void){ordinary_cycles();viewers_and_nested_owner();manual_and_always_off();saver_recording_and_radio();
    puts("Screen saver/blank/wake, recording/viewers, radio activity, balanced invalidation and chronology: PASS");return 0;}
