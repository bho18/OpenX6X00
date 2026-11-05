// SPDX-License-Identifier: LGPL-2.1-or-later
#define _POSIX_C_SOURCE 200809L
#include "mode.h"
#include "voice_radio.h"
#include "writer_gate.h"
#include "voice_audio.h"
#include "native_trace.h"
#include "../app_window.h"
#include "../radio.h"
#include "../hw/hw.h"
#include "../gui_events.h"
#include "../key_input.h"
#include "../board.h"
#include "../front_keys.h"
#include "../screen_main.h"
#include "../scheduler.h"
#include "../msg.h"
#include "../gui_styles.h"
#include "../freq_strip.h"
#include "../info_bar.h"
#include "../cfg/cfg_api.h"
#include "../cfg/digital_modes.h"
#include <pthread.h>
#include <stdatomic.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>

/* GUI owns the view and lifetime. The short pointer mutex protects asynchronous
 * protection callbacks; destruction and worker joins happen outside it. */
static pthread_mutex_t pointer_mutex=PTHREAD_MUTEX_INITIALIZER;
static fd_voice_session *session;
static atomic_bool active;
static bool completing, input_owned;
static bool validation_mode;
static const fd_qualified_profile *selected_profile;
static fd_validation_admission validation_admission;
static int destination;
static dialog_t *next_dialog;
static lv_obj_t *next_parent;
static lv_timer_t *service_timer;
static struct {
    lv_obj_t *state,*frequency,*health,*presets,*hz,*label,*timeout,*notice,*stop,*grant;
    cfg_digital_preset_t *bookmarks;
    int count,old_frequency;
} ui;
static void construct(lv_obj_t *parent);
static void destruct(void);
static void key(lv_event_t *e);
static dialog_t view={.construct_cb=construct,.destruct_cb=destruct,.key_cb=key};

static const fd_qualified_profile *production_profile(void) {
#ifdef FREEDV_HOST_TEST_PROFILE
    /* Explicit virtual fixture, compiled out of every radio build. This is
     * neither qualification evidence nor a production registry entry. */
    static const fd_qualified_profile simulated={.id="SIMULATION ONLY",.hardware="host",.build_id=FREEDV_BUILD_ID,
        .baseband="stub",.control_version="stub",.evidence_sha256="0000000000000000000000000000000000000000000000000000000000000000",
        .local_mode=FD_PROFILE_USB_DIG,.upper_sideband=true,.capture_device="@default-source@",.playback_device="@default-sink@",
        .mixer="host-default",.sample_rate=44100,.capture_channel=0,.rx_route=FD_ROUTE_RECORD,.tx_route=FD_ROUTE_MODEM|FD_ROUTE_PTT,
        .rx_low_hz=100,.rx_high_hz=3100,.tx_low_hz=100,.tx_high_hz=3100,.rx2_low_hz=0,.rx2_high_hz=3300,.agc=radio_agc_auto,
        .mic_gain=20,.line_gain=20,.line_out_gain=10,.rf_gain=100,.squelch=0,.mixer_levels_attested=true,
        .monitor_gain=0,.power_tenths_watt=10,
        .fixed_tx_filter_attested=true,.fixed_eq_bypass_attested=true,.capabilities=FD_CAP_ALL,.complete_pass=true};
    return &simulated;
#else
    return fd_profile_production();
#endif
}
static const fd_qualified_profile *profile(void) {return selected_profile?selected_profile:production_profile();}
bool freedv_mode_available(void) {
#ifdef ENABLE_FREEDV_NATIVE_VALIDATION
    /* This explicit candidate build always exercises validation admission,
     * including on the host where a simulated production fixture exists. */
    return false;
#else
    return radio_voice_supported(production_profile());
#endif
}
bool freedv_mode_validation_available(void) {
#ifdef ENABLE_FREEDV_NATIVE_VALIDATION
    return true;
#else
    return false;
#endif
}
bool freedv_mode_active(void) {return atomic_load(&active);}
const fd_qualified_profile *freedv_mode_profile(void) {return freedv_mode_active()?profile():NULL;}
bool freedv_mode_audio_stats(fd_voice_audio_snapshot *out) {return freedv_mode_active() && radio_voice_audio_stats(out);}
bool freedv_mode_status(fd_voice_status *out) {
    if(!out)return false;
    pthread_mutex_lock(&pointer_mutex);bool present=session!=NULL;
    *out=session?fd_session_observe(session):(fd_voice_status){0};pthread_mutex_unlock(&pointer_mutex);return present;
}
static void notice(const char *s) {if(ui.notice)lv_label_set_text(ui.notice,s);}
static bool local(void) {return scheduler_on_gui_thread() && front_keys_voice_local_event();}
static void save_trace(void) {
    fd_voice_status status=fd_session_observe(session);
    bool fault=status.state==FD_VS_FAULT || status.state==FD_VS_WAIT_PTT_RELEASE;
    bool receive=status.state==FD_VS_RX && !status.ptt_held[0] && !status.ptt_held[1] &&
        !status.ptt_unknown[0] && !status.ptt_unknown[1];
    if(!status.validation || (!receive && !fault) || status.bench_grant!=FD_BENCH_DISARMED) {
        notice("Disarm and wait for receive or a fault before saving");return;
    }
    if(mkdir("/mnt/freedv",0700)<0 && errno!=EEXIST){notice("Cannot create trace directory");return;}
    struct timespec stamp;clock_gettime(CLOCK_REALTIME,&stamp);
    char path[160];snprintf(path,sizeof(path),"/mnt/freedv/native-session-%lld-%09ld.jsonl",(long long)stamp.tv_sec,stamp.tv_nsec);
    int fd=open(path,O_WRONLY|O_CREAT|O_EXCL|O_CLOEXEC,0600);
    if(fd<0){notice("Cannot create a new trace file");return;}
    FILE *stream=fdopen(fd,"w");
    if(!stream){close(fd);unlink(path);notice("Cannot open trace output");return;}
    bool ok=fd_native_trace_export(stream);
    if(fclose(stream))ok=false;
    if(!ok){unlink(path);notice("Trace export failed");return;}
    notice(path);
}
static void leave(void) {
    pthread_mutex_lock(&pointer_mutex);if(session)fd_session_leave(session);pthread_mutex_unlock(&pointer_mutex);
    notice("Waiting for fresh RX and both PTT controls to release");
}
bool freedv_mode_defer_app(int action) {
    if(!freedv_mode_active() || completing)return false;
    if(local()){destination=action;next_dialog=NULL;next_parent=NULL;leave();}
    return true;
}
bool freedv_mode_defer_dialog(void *dialog,void *parent) {
    if(!freedv_mode_active() || completing || dialog==&view)return false;
    if(local()){next_dialog=dialog;next_parent=parent;destination=0;leave();}
    return true;
}
bool freedv_mode_request(fd_local_operation op,int32_t value) {
    if(!freedv_mode_active() || !local() || !session)return false;
    fd_voice_status s=fd_session_observe(session);
    if(s.state!=FD_VS_RX || s.exit_requested){notice("Control available while receiving");return false;}
    if(op==FD_LOCAL_VOLUME)return radio_voice_volume(value);
    fd_voice_tuning tuning;
    bool ok=radio_voice_tuning_request(op,value,&tuning) && fd_session_retune(session,tuning);
    notice(ok?"Preparing receiver at selected frequency":"Tuning rejected; receiver is busy");return ok;
}
bool freedv_mode_radio_ptt(bool pressed) {
    if(!freedv_mode_active())return false;
    /* Called by the physical front-key reader before ordinary analog handling.
     * The driver independently verifies the held state; no remote PTT path can
     * use this hook. A rejected edge is still consumed while we own routing. */
    uint64_t sequence;
    if(session && radio_voice_local_ptt(pressed,&sequence))fd_session_ptt(session,FD_PTT_RADIO,pressed,sequence);
    return true;
}
void freedv_mode_protection(void) {
    pthread_mutex_lock(&pointer_mutex);if(session)fd_session_abort(session,FD_ABORT_PROTECTION);pthread_mutex_unlock(&pointer_mutex);
}
void freedv_mode_device_fault(void) {
    pthread_mutex_lock(&pointer_mutex);if(session)fd_session_abort(session,FD_ABORT_DEVICE);pthread_mutex_unlock(&pointer_mutex);
}
static void select_bookmark(void) {
    if(!ui.presets)return;
    unsigned i=lv_dropdown_get_selected(ui.presets);
    if(i>=(unsigned)ui.count)return;
    char hz[24];snprintf(hz,sizeof(hz),"%d",ui.bookmarks[i].freq);
    lv_textarea_set_text(ui.hz,hz);lv_textarea_set_text(ui.label,ui.bookmarks[i].label);
    ui.old_frequency=ui.bookmarks[i].freq;
}
static void load_bookmarks(void) {
    free(ui.bookmarks);ui.bookmarks=NULL;ui.count=0;
    int count=cfg_digital_count(CFG_DIG_TYPE_FREEDV);
    /* The list is deliberately bounded; a large edited database remains
     * available through its editor instead of allocating unbounded GUI memory. */
    if(count<0 || count>1024){lv_dropdown_set_options(ui.presets,"List unavailable (limit 1024)");return;}
    ui.bookmarks=calloc(count?count:1,sizeof(*ui.bookmarks));
    char *options=calloc((size_t)(count?count:1),80);
    if(!ui.bookmarks || !options){free(options);notice("Cannot allocate bookmark list");return;}
    ui.count=cfg_digital_list(CFG_DIG_TYPE_FREEDV,ui.bookmarks,count);
    size_t used=0;
    for(int i=0;i<ui.count;++i)used+=(size_t)snprintf(options+used,(size_t)(count?count:1)*80-used,"%s%d Hz  %s",i?"\n":"",ui.bookmarks[i].freq,ui.bookmarks[i].label);
    lv_dropdown_set_options(ui.presets,ui.count?options:"No frequency bookmarks");free(options);select_bookmark();
}
static bool integer(lv_obj_t *field,int32_t *value) {
    const char *s=lv_textarea_get_text(field);char *end;long long n=strtoll(s,&end,10);
    if(!*s || *end || n<0 || n>INT32_MAX)return false;*value=(int32_t)n;return true;
}
static void clicked(lv_event_t *e) {
    int action=(int)(intptr_t)lv_event_get_user_data(e);
    if(!local())return;
    if(action==0){fd_session_abort(session,FD_ABORT_STOP);return;}
    if(action==1){destination=0;next_dialog=NULL;leave();return;}
    if(action==8){notice(fd_session_validation_arm(session)?"Armed: select Run with both PTT controls released":"Cannot arm: receive with both PTT controls released");return;}
    if(action==9){notice(fd_session_validation_run(session)?"One transmission granted: press physical PTT within 15 seconds":"Run rejected: Arm again with PTT released");return;}
    if(action==10){save_trace();return;}
    if(action==11){fd_session_validation_disarm(session);notice("Transmit grant cleared");return;}
    if(action==2){select_bookmark();return;}
    if(action==3){int32_t hz;if(integer(ui.hz,&hz))freedv_mode_request(FD_LOCAL_FREQUENCY,hz);return;}
    if(action==4){ui.old_frequency=0;char hz[24];snprintf(hz,sizeof(hz),"%d",cparam_i_get(cfg_fg_freq));lv_textarea_set_text(ui.hz,hz);lv_textarea_set_text(ui.label,"My frequency");notice("Edit frequency and label, then Save");return;}
    if(fd_session_observe(session).state!=FD_VS_RX){notice("Edit while receiving");return;}
    if(action==7){
        int32_t seconds;
        if(validation_mode){
            notice(integer(ui.timeout,&seconds) && fd_session_timeout(session,(unsigned)seconds)?
                "Validation timeout changed for this session":"Timeout exceeds the validation limit");return;
        }
        if(integer(ui.timeout,&seconds) && fd_session_timeout(session,(unsigned)seconds) && fd_writer_voice_begin()){
            param_i_set(cfg_freedv_timeout,seconds);fd_writer_voice_end();notice("TX timeout saved");
        }else notice("Timeout must be 30-600 seconds while receiving");return;
    }
    if(!fd_writer_voice_begin())return;
    bool ok=false;int32_t hz;
    if(action==5 && integer(ui.hz,&hz) && cfg_is_valid_hw_freq(hz))
        ok=cfg_digital_bookmark_save(ui.old_frequency,hz,lv_textarea_get_text(ui.label));
    if(action==6 && ui.old_frequency)ok=cfg_digital_bookmark_delete(ui.old_frequency);
    fd_writer_voice_end();
    if(ok){load_bookmarks();notice("Frequency bookmarks saved");}else notice("Bookmark unchanged; check frequency, label and duplicates");
}
static lv_obj_t *button(lv_obj_t *p,const char *text,int x,int y,int w,int action) {
    lv_obj_t *o=lv_btn_create(p);lv_obj_set_pos(o,x,y);lv_obj_set_size(o,w,32);
    lv_obj_t *label=lv_label_create(o);lv_label_set_text(label,text);lv_obj_center(label);
    lv_obj_set_style_text_color(label,TC(text),0);
    dialog_item(&view,o);lv_obj_add_event_cb(o,clicked,LV_EVENT_CLICKED,(void *)(intptr_t)action);return o;
}
static lv_obj_t *field(lv_obj_t *p,int x,int y,int w,unsigned max) {
    lv_obj_t *o=lv_textarea_create(p);lv_obj_set_pos(o,x,y);lv_obj_set_size(o,w,34);
    lv_textarea_set_one_line(o,true);lv_textarea_set_max_length(o,max);dialog_item(&view,o);return o;
}
static lv_obj_t *label(lv_obj_t *p,const char *text,int x,int y) {
    lv_obj_t *o=lv_label_create(p);lv_label_set_text(o,text);lv_obj_set_pos(o,x,y);return o;
}
static void construct(lv_obj_t *parent) {
    view.obj=dialog_init(parent);lv_obj_set_pos(view.obj,20,DIALOG_Y);lv_obj_set_size(view.obj,760,DIALOG_H);
    lv_obj_set_style_text_font(view.obj,&sony_14,0);
    label(view.obj,validation_mode?"FreeDV / RADE V1 - VALIDATION":
#ifdef FREEDV_HOST_TEST_PROFILE
        "FreeDV / RADE V1 - SIMULATION ONLY",
#else
        "FreeDV / RADE V1",
#endif
        10,4);
    ui.state=label(view.obj,"Preparing",10,26);ui.frequency=label(view.obj,"",10,50);
    ui.health=label(view.obj,"PTT health pending",10,73);
    ui.stop=button(view.obj,"Stop",560,4,80,0);button(view.obj,"Exit",650,4,80,1);
    label(view.obj,"Frequency bookmarks (Hz) - recall is explicit",10,99);
    ui.presets=lv_dropdown_create(view.obj);lv_obj_set_pos(ui.presets,10,122);lv_obj_set_size(ui.presets,460,32);
    lv_dropdown_set_symbol(ui.presets,"v");
    dialog_item(&view,ui.presets);lv_obj_add_event_cb(ui.presets,clicked,LV_EVENT_VALUE_CHANGED,(void *)2);
    button(view.obj,"Recall",480,122,90,3);button(view.obj,"New",580,122,70,4);
    ui.hz=field(view.obj,10,164,150,10);lv_textarea_set_accepted_chars(ui.hz,"0123456789");
    ui.label=field(view.obj,170,164,300,31);
    button(view.obj,"Save",480,164,90,5);button(view.obj,"Delete",580,164,90,6);
    label(view.obj,validation_mode?"Validation TX timeout (s)":"TX timeout (30-600 s)",10,214);
    ui.timeout=field(view.obj,210,207,90,3);lv_textarea_set_accepted_chars(ui.timeout,"0123456789");
    unsigned timeout=(unsigned)param_i_get(cfg_freedv_timeout);
    if(validation_mode && timeout>validation_admission.max_turn_seconds)timeout=validation_admission.max_turn_seconds;
    char seconds[12];snprintf(seconds,sizeof(seconds),"%u",timeout);lv_textarea_set_text(ui.timeout,seconds);
    button(view.obj,"Set",310,207,70,7);
    if(validation_mode){
        button(view.obj,"Arm",390,207,65,8);button(view.obj,"Run",465,207,65,9);
        button(view.obj,"Disarm",540,207,85,11);button(view.obj,"Save trace",635,207,100,10);
        ui.grant=label(view.obj,"Validation: Arm -> Run grants one physical PTT turn",10,246);
    }
    ui.notice=label(view.obj,validation_mode?"Unqualified candidate: recording taps are disabled":"Local tuning is available while receiving",10,validation_mode?274:261);load_bookmarks();
    lv_group_focus_obj(ui.stop);lv_group_set_editing(keyboard_group,false);
}
static void destruct(void) {
    free(ui.bookmarks);memset(&ui,0,sizeof(ui));
    if(!completing)leave();
}
static void key(lv_event_t *e) {
    if(!local())return;
    uint32_t code=*(uint32_t *)lv_event_get_param(e);
    if(code==LV_KEY_ESC){destination=0;next_dialog=NULL;leave();}
    if(code==KEY_VOL_LEFT_EDIT || code==KEY_VOL_LEFT_SELECT)freedv_mode_request(FD_LOCAL_VOLUME,-1);
    if(code==KEY_VOL_RIGHT_EDIT || code==KEY_VOL_RIGHT_SELECT)freedv_mode_request(FD_LOCAL_VOLUME,1);
}
static void service(lv_timer_t *timer) {
    (void)timer;if(!session)return;
    fd_voice_status s=fd_session_observe(session);
    if(s.state==FD_VS_OFF){
        pthread_mutex_lock(&pointer_mutex);fd_voice_session *old=session;session=NULL;pthread_mutex_unlock(&pointer_mutex);
        if(!fd_session_destroy(old))abort();
        completing=true;atomic_store(&active,false);
        info_refresh_effective();
        if(dialog_type_is_run(&view))dialog_destruct();
        if(input_owned){front_keys_diagnostic_input_release();input_owned=false;}
        int app=destination;dialog_t *dialog=next_dialog;lv_obj_t *parent=next_parent;
        destination=0;next_dialog=NULL;next_parent=NULL;completing=false;selected_profile=NULL;validation_mode=false;
        lv_timer_del(service_timer);service_timer=NULL;
        if(dialog)dialog_construct(dialog,parent);else if(app)main_screen_start_app((press_action_t)app);else main_screen_close_app();
        return;
    }
    if(!ui.state)return;
    fd_voice_audio_snapshot audio;
    if(radio_voice_audio_stats(&audio)) {
        fd_native_audio_trace sample={.generation=audio.generation,
            .adapter_instance=audio.adapter_instance,.engine_instance=audio.engine_instance,
            .capture_instance=audio.capture_instance,.playback_instance=audio.playback_instance,
            .input_samples=audio.engine.input_samples,.output_samples=audio.engine.output_samples,
            .delivered_samples=audio.engine.delivered_samples,.processing_max_ns=audio.engine.processing_max_ns,
            .synthesis_max_ns=audio.engine.synthesis_max_ns,.input_high_water=audio.engine.input_high_water,
            .output_high_water=audio.engine.output_high_water,.capture_queued=audio.capture.queued_samples,
            .capture_dropped=audio.capture.dropped_samples,.capture_holes=audio.capture.hole_samples,
            .capture_errors=audio.capture.capture_errors,
            .playback_underflows=audio.playback.underflows>UINT_MAX?UINT_MAX:(unsigned)audio.playback.underflows,
            .rade_creations=audio.engine.rade_creations,.encoder_creations=audio.engine.encoder_creations,
            .decoder_creations=audio.engine.decoder_creations,.resampler_creations=audio.engine.resampler_creations,
            .worker_creations=audio.engine.worker_creations,.capture_stream_creations=audio.capture.stream_creations,
            .failed=audio.failed || audio.engine.failures,.cancelled=audio.cancelled,.finished=audio.engine.finished};
        fd_native_trace_audio(&sample);
    }
    lv_label_set_text_fmt(ui.state,"%s | microphone %s",fd_voice_state_name(s),s.microphone_active?"active":"quiet");
    int32_t hz=s.tuning.frequency;
    lv_label_set_text_fmt(ui.frequency,"VFO %c  %d.%03d.%03d Hz | upper sideband | %s",'A'+s.tuning.vfo,hz/1000000,(hz/1000)%1000,hz%1000,
        profile()->local_mode==FD_PROFILE_USB_DIG?"USB-DIG":"USB");
    lv_label_set_text_fmt(ui.health,"Radio PTT: %s | Hand PTT: %s",s.ptt_unknown[0]?"unknown":s.ptt_held[0]?"held":"released",
        s.ptt_unknown[1]?"unknown":s.ptt_held[1]?"held":"released");
    if(ui.grant)lv_label_set_text_fmt(ui.grant,"Validation grant: %s | consumed: %llu",
        s.bench_grant==FD_BENCH_READY?"one press ready":s.bench_grant==FD_BENCH_ARMED?"armed; select Run":"disarmed",
        (unsigned long long)s.grants_consumed);
}
static void open_mode(bool validation) {
    if(!scheduler_on_gui_thread() || freedv_mode_active())return;
    const fd_qualified_profile *chosen=validation?radio_voice_validation_profile():production_profile();
    fd_validation_admission admission={0};const char *reason=NULL;
    if(validation){
        if(!freedv_mode_validation_available() || !local())return;
        if(!radio_voice_validation_status(chosen,&admission,&reason)){
            msg_schedule_text_fmt("Native validation blocked: %s",reason?reason:"hardware contract unavailable");return;
        }
    }else if(!radio_voice_supported(chosen))return;
    if(radio_get_state()!=RADIO_RX || hw_tx_keyed_by_gui()){msg_schedule_text_fmt("Release PTT before entering FreeDV");return;}
    if(!front_keys_diagnostic_input_acquire()){msg_schedule_text_fmt("Release local controls, then select FreeDV again");return;}
    input_owned=true;
    fd_voice_driver driver=radio_voice_driver();fd_voice_session *created=fd_session_create(&driver);
    fd_voice_tuning tuning={.frequency=cparam_i_get(cfg_fg_freq),.vfo=(unsigned)param_i_get(cfg_band_current_vfo)};
    unsigned timeout=(unsigned)param_i_get(cfg_freedv_timeout);
    if(validation && timeout>admission.max_turn_seconds)timeout=admission.max_turn_seconds;
    if(!created || tuning.frequency<=0 || tuning.vfo>1 || timeout<(validation?1u:30u) || timeout>600 ||
       !(validation?radio_voice_claim_validation(chosen,&admission):radio_voice_claim(chosen))){
        if(created)fd_session_destroy(created);front_keys_diagnostic_input_release();input_owned=false;msg_schedule_text_fmt("Cannot prepare FreeDV session");return;
    }
    selected_profile=chosen;validation_mode=validation;validation_admission=admission;
    /* No app memory or saved-frequency restoration belongs to this mode. */
    if(fd_writer_voice_begin()){dialog_construct(&view,lv_scr_act());fd_writer_voice_end();}
    pthread_mutex_lock(&pointer_mutex);session=created;pthread_mutex_unlock(&pointer_mutex);atomic_store(&active,true);
    /* All immutable arguments and the new OFF session were validated before
     * claiming. An invariant violation must preserve the mute/unkey fence. */
    bool entered=validation?fd_session_enter_validation(created,chosen,&admission,tuning,timeout):
        fd_session_enter(created,chosen,BOARD_NAME,FREEDV_BUILD_ID,tuning,timeout);
    if(!entered){driver.emergency_clear(driver.context);abort();}
    freq_strip_set_app(validation?"FreeDV test":"FreeDV");info_refresh_effective();service_timer=lv_timer_create(service,50,NULL);
}
void freedv_mode_open(void) {open_mode(false);}
void freedv_mode_validation_open(void) {open_mode(true);}
