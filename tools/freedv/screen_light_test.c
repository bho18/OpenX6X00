// SPDX-License-Identifier: LGPL-2.1-or-later
/* Actual screen policy and LVGL invalidation API; GPIO, time and timers fake. */
#define _POSIX_C_SOURCE 200809L
#define open test_open
#include "src/screen_light.c"
#undef open
#include <assert.h>
#include <stdarg.h>
#include <string.h>

static lv_disp_t display;
static lv_obj_t screen;
static lv_timer_t timers[128];
static unsigned timer_count,event_count,invalidations;
static uint64_t fake_ms=100;
static fd_usb_display_event events[128];
params_t params={.brightness_normal=5,.brightness_idle=4,.brightness_timeout=30,.screen_off_min=5};
int test_open(const char *path,int flags,...){(void)path;(void)flags;return -1;}
int clock_gettime(clockid_t clock,struct timespec *out){(void)clock;out->tv_sec=fake_ms/1000;out->tv_nsec=(fake_ms%1000)*1000000;return 0;}
lv_disp_t *lv_disp_get_default(void){return &display;}
void lv_obj_invalidate(const lv_obj_t *object){assert(object==&screen);++invalidations;}
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
    screen_on=true;blanked=viewers=drawing_blocked=false;drawing_display=NULL;
    idle_timer=blank_timer=NULL;shown=5;observer=NULL;observer_context=NULL;display_generation=0;
    params.screen_off_always=false;timer_count=event_count=invalidations=0;
}
static void ordinary_cycles(void){
    reset_policy();backlight_set_observer(capture,&events);
    for(unsigned i=0;i<12;++i){
        fake_ms+=300000;blank_cb(NULL);
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
    fd_usb_display_event snapshot;backlight_snapshot(&snapshot);
    assert(snapshot.reason==FD_USB_DISPLAY_SNAPSHOT && !snapshot.dark && snapshot.drawing_enabled);
    backlight_set_observer(NULL,NULL);unsigned before=event_count;backlight_switch();assert(event_count==before);
}
static void viewers_and_nested_owner(void){
    reset_policy();
    for(unsigned i=0;i<12;++i){
        backlight_viewers(true);backlight_viewers(true);backlight_viewers(false);backlight_viewers(false);
        assert(display.inv_en_cnt==1);blank_cb(NULL);assert(display.inv_en_cnt==0);
        backlight_viewers(true);assert(display.inv_en_cnt==1);
        assert(backlight_tick());assert(display.inv_en_cnt==1);
        backlight_viewers(false);assert(display.inv_en_cnt==1);
    }
    lv_disp_enable_invalidation(&display,false);assert(display.inv_en_cnt==0);
    blank_cb(NULL);assert(display.inv_en_cnt==-1);
    assert(backlight_tick());assert(display.inv_en_cnt==0);
    lv_disp_enable_invalidation(&display,true);assert(display.inv_en_cnt==1);
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
int main(void){ordinary_cycles();viewers_and_nested_owner();manual_and_always_off();
    puts("Screen blank/wake, viewers, nested invalidation ownership and display chronology: PASS");return 0;}
