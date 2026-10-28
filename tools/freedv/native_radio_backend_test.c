// SPDX-License-Identifier: LGPL-2.1-or-later
/* The production radio translation unit, actual cached stub, lease and packet
 * tracker; only external application/UI endpoints and clock are substituted. */
#define ENABLE_FREEDV_DIAGNOSTICS 1
#define hw_resend_all test_transport_resend
#define hw_set_i test_cached_set
#include "src/radio.c"
#undef hw_resend_all
#undef hw_set_i
extern bool hw_set_i(hw_param_t,int32_t);
static bool test_transport_failure;
static int test_ignored_setting=-1;
bool test_transport_resend(void) { return !test_transport_failure; }
bool test_cached_set(hw_param_t parameter,int32_t value) {
    return (int)parameter==test_ignored_setting ? false : hw_set_i(parameter,value);
}
#include <assert.h>

static uint64_t test_time = 10000;
static bool test_recording, test_screen_recording, test_audio, test_dialog, test_settings_dialog, test_acquire_race;
static unsigned test_audio_reads;
static void observed(bool tx);
static unsigned test_tci;
static int32_t test_frequency = 7074000, test_split, test_shift;
static int token_split, token_shift, token_vfo, token_fg, token_bg, token_hmic, token_imic, token_mic, token_vox, token_pwr;
ParamInt *cfg_vox_on=(ParamInt *)&token_vox;
ParamFloat *cfg_pwr=(ParamFloat *)&token_pwr;
ParamInt *cfg_hmic=(ParamInt *)&token_hmic;
ParamInt *cfg_imic=(ParamInt *)&token_imic;
ParamInt *cfg_mic=(ParamInt *)&token_mic;
ParamInt *cfg_band_split=(ParamInt *)&token_split;
ParamInt *cfg_band_tx_shift=(ParamInt *)&token_shift;
ParamInt *cfg_band_current_vfo=(ParamInt *)&token_vfo;
ComputedParamInt *cfg_fg_freq=(ComputedParamInt *)&token_fg;
ComputedParamInt *cfg_bg_freq=(ComputedParamInt *)&token_bg;
dialog_t *dialog_settings;
uint64_t get_time(void) { return test_time; }
void msg_schedule_text_fmt(const char *fmt, ...) { (void)fmt; }
static bool schedule_ok=true;
static scheduler_fn_t scheduled;
bool scheduler_put_urgent(scheduler_fn_t callback, void *arg, size_t size) {
    (void)arg; (void)size; if (schedule_ok) scheduled=callback; return schedule_ok;
}
Observer *subject_subscribe(Subject *subject, observer_cb callback, void *data) {
    (void)subject; (void)callback; (void)data; return NULL;
}
bool recorder_is_on(void) { return test_recording; }
bool screen_rec_is_on(void) { return test_screen_recording; }
bool audio_freedv_other_audio_busy(void) {
    if (test_acquire_race && ++test_audio_reads==2) { observed(true); return true; }
    return test_audio;
}
bool dialog_is_run(void) { return test_dialog; }
bool dialog_diagnostic_busy(void) { return test_dialog && !test_settings_dialog; }
bool dialog_type_is_run(dialog_t *d) { return d==dialog_settings && test_settings_dialog; }
int tci_server_clients(void) { return (int)test_tci; }
cw_encoder_state_t cw_encoder_state(void) { return CW_ENCODER_IDLE; }
rtty_state_t rtty_get_state(void) { return RTTY_OFF; }
int32_t subject_i_get(SubjectInt *p) {
    if ((void *)p==(void *)cfg_hmic) return 28;
    if ((void *)p==(void *)cfg_imic) return 17;
    if ((void *)p==(void *)cfg_vox_on) return 1;
    assert(0); return 0;
}
float subject_f_get(SubjectFloat *p) { assert(!p || (void *)p==(void *)cfg_pwr); return 5.0f; }
float param_f_get(const ParamFloat *p) { assert(p==cfg_pwr); return 5.0f; }
int32_t param_i_get(const ParamInt *p) {
    if (p==cfg_band_split) return test_split;
    if (p==cfg_band_tx_shift) return test_shift;
    if (p==cfg_band_current_vfo) return 0;
    if (p==cfg_hmic) return 28;
    if (p==cfg_imic) return 17;
    if (p==cfg_mic) return radio_mic_auto;
    assert(0); return 0;
}
int32_t cparam_i_get(const ComputedParamInt *p) { assert(p==cfg_fg_freq || p==cfg_bg_freq); return test_frequency; }
bool cfg_is_tx_freq(int32_t hz) { return hz>=100000 && hz<=30000000; }
static void observed(bool tx) {
    ++test_time;
    freedv_packet_tx_note(&packet_tx_tracker,tx,test_time);
    atomic_store(&state,tx?RADIO_TX:RADIO_RX);
}
static void snapshot(hw_freedv_cached_snapshot_t *s) { assert(hw_get_freedv_cached_snapshot(s)); }
int main(void) {
    ctl_init(); assert(hw_init()); hw_start();
    hw_set_i(HW_MIC,radio_mic_auto); hw_set_i(HW_HMIC,28); hw_set_i(HW_IMIC,17);
    hw_set_i(HW_RXVOL,30); hw_set_i(HW_MONI,12); hw_set_i(HW_LINE_IN,9); hw_set_i(HW_LINE_OUT,10);
    hw_set_i(HW_VOX,1); hw_set_i(HW_SPMODE,1); hw_set_f(HW_TXPWR_F,5.0f);
    fd_radio_session s={0}; fd_observation o;
    /* Startup is responsive while a held physical PTT prevents analog restore. */
    freedv_startup_begin();
    radio_set_ptt(true); /* startup also blocks keying before the writer gate is sealed */
    freedv_initial_subscribe((Subject *)cfg_hmic,on_change_i,(void *)HW_HMIC);
    freedv_initial_subscribe((Subject *)cfg_imic,on_change_i,(void *)HW_IMIC);
    freedv_initial_subscribe((Subject *)cfg_vox_on,on_change_i,(void *)HW_VOX);
    freedv_initial_subscribe(NULL,on_pwr_change,NULL);
    assert(radio_freedv_startup_waiting());
    hw_freedv_cached_snapshot_t preliminary; snapshot(&preliminary);
    assert(!preliminary.hand_mic_gain && !preliminary.internal_mic_gain && !preliminary.vox_enabled && !preliminary.route.ptt);
    freedv_startup_seal();
    assert(!fd_writer_enter());
    hw_freedv_cached_snapshot_t startup; snapshot(&startup);
    assert(!startup.hand_mic_gain && !startup.internal_mic_gain && startup.requested_power_tenths_watt==0);
    assert(!radio_freedv_session_acquire(&s,&o));
    observed(true); pack->tx=true; freedv_startup_packet(); assert(!scheduled);
    freedv_startup_restore_cb(NULL); assert(radio_freedv_startup_waiting());
    observed(false); pack->tx=false; schedule_ok=false;
    freedv_startup_packet(); assert(!atomic_load(&freedv_startup_queued));
    schedule_ok=true; freedv_startup_packet(); assert(scheduled==freedv_startup_restore_cb);
    scheduled(NULL); assert(!radio_freedv_startup_waiting());
    assert(fd_writer_enter()); fd_writer_exit();
    snapshot(&startup); assert(startup.hand_mic_gain==28 && startup.internal_mic_gain==17 &&
                              startup.requested_power_tenths_watt==50 && startup.vox_enabled);
    pthread_mutex_lock(&packet_tx_tracker.mutex); packet_tx_tracker.valid=false; pthread_mutex_unlock(&packet_tx_tracker.mutex);

    assert(!radio_freedv_session_acquire(&s,&o)); /* unknown status */
    observed(false); test_time+=251;
    assert(!radio_freedv_session_acquire(&s,&o)); /* stale status */
    observed(true); assert(!radio_freedv_session_acquire(&s,&o));
    observed(false);
    test_recording=true; assert(!radio_freedv_session_acquire(&s,&o)); test_recording=false;
    test_audio=true; assert(!radio_freedv_session_acquire(&s,&o)); test_audio=false;
    test_screen_recording=true; assert(!radio_freedv_session_acquire(&s,&o)); test_screen_recording=false;
    test_dialog=true; assert(!radio_freedv_session_acquire(&s,&o)); test_dialog=false;
    test_tci=1; assert(!radio_freedv_session_acquire(&s,&o)); test_tci=0;
    test_dialog=true; test_settings_dialog=true;
    assert(radio_freedv_session_acquire(&s,&o) && s.ready); /* Settings -> Info is allowed */
    assert(radio_freedv_session_release(&s));
    test_dialog=false; test_settings_dialog=false;
    hw_freedv_cached_snapshot_t before,after; snapshot(&before);
    test_acquire_race=true;
    assert(radio_freedv_session_acquire(&s,&o) && s.owned && !s.ready);
    assert(!radio_freedv_session_release(&s)); /* post-claim physical TX retains reachable ownership */
    test_acquire_race=false;
    uint64_t rollback_sequence;
    assert(radio_freedv_session_clear(&s,&rollback_sequence)); observed(false);
    assert(radio_freedv_session_restore(&s,rollback_sequence) && radio_freedv_session_release(&s));
    assert(radio_freedv_session_acquire(&s,&o) && s.owned && s.ready);
    radio_set_ptt(true); radio_set_play_mode(AUDIO_PLAY_ON); radio_set_mic_override(radio_mic_builtin);
    snapshot(&after); assert(hw_freedv_snapshot_matches(&before,&after)); /* competing radio setters rejected */
    fd_case chosen={FD_CASE_ROUTE,FD_R1_M1_P1};
    test_frequency=50000; assert(!radio_freedv_session_apply(&s,&chosen,radio_mic_handle)); test_frequency=7074000;
    test_split=1; assert(!radio_freedv_session_apply(&s,&chosen,radio_mic_handle)); test_split=0;
    test_shift=1000; assert(!radio_freedv_session_apply(&s,&chosen,radio_mic_handle)); test_shift=0;
    assert(radio_freedv_session_apply(&s,&chosen,radio_mic_handle));
    snapshot(&after); assert(after.route.record && after.route.modem && after.route.ptt);
    assert(after.requested_power_tenths_watt==10 && after.microphone==radio_mic_handle && !after.vox_enabled);
    assert(after.monitor_level==before.monitor_level);
    assert(!radio_freedv_session_release(&s));
    observed(true); uint64_t sequence;
    assert(radio_freedv_session_clear(&s,&sequence));
    snapshot(&after); assert(!after.route.modem && !after.route.ptt && !after.hand_mic_gain && !after.internal_mic_gain);
    assert(!radio_freedv_session_restore(&s,sequence)); /* hardware PTT independently held */
    assert(!radio_freedv_session_release(&s));
    observed(false); test_time+=251; assert(!radio_freedv_session_restore(&s,sequence));
    observed(false); assert(radio_freedv_session_restore(&s,sequence));
    snapshot(&after); assert(hw_freedv_snapshot_matches(&before,&after));
    assert(s.readback_match && s.restoration_complete && s.transport_attempts>=3 && !s.transport_failures);
    observed(true); assert(!radio_freedv_session_release(&s)); /* renewed physical PTT refuses release */
    observed(false);
    hw_set_i(HW_MONI,99); /* delayed owned-field drift between restoration and release */
    assert(!radio_freedv_session_release(&s) && !s.restoration_complete && s.owned);
    assert(s.restoration_failures==1 && s.last_error==FD_RADIO_READBACK);
    assert(radio_freedv_session_clear(&s,&sequence)); observed(false);
    assert(radio_freedv_session_restore(&s,sequence));
    assert(radio_freedv_session_release(&s) && !s.owned);
    assert(fd_writer_enter()); fd_writer_exit();
    assert(radio_freedv_session_acquire(&s,&o));
    test_transport_failure=true;
    assert(!radio_freedv_session_apply(&s,&chosen,radio_mic_handle));
    assert(s.route_applied && !s.transport_ok && s.transport_failures==1);
    assert(!radio_freedv_session_clear(&s,&sequence));
    test_transport_failure=false;
    assert(radio_freedv_session_clear(&s,&sequence)); observed(false);
    test_ignored_setting=HW_HMIC;
    assert(!radio_freedv_session_restore(&s,sequence) && !s.readback_match && s.owned);
    snapshot(&after); assert(!after.hand_mic_gain && !after.internal_mic_gain && !after.vox_enabled);
    assert(!radio_freedv_session_release(&s));
    test_ignored_setting=-1;
    assert(radio_freedv_session_clear(&s,&sequence)); observed(false);
    assert(radio_freedv_session_restore(&s,sequence) && radio_freedv_session_release(&s));
    assert(radio_freedv_session_acquire(&s,&o));
    chosen=(fd_case){FD_CASE_ROUTE,FD_R0_M0_P0}; assert(radio_freedv_session_apply(&s,&chosen,radio_mic_builtin));
    hw_vfo_set_freq(RADIO_VFO_A,7100000); assert(radio_freedv_session_clear(&s,&sequence)); observed(false);
    assert(!radio_freedv_session_restore(&s,sequence)); /* unexpected tuning is never silently overwritten */
    hw_vfo_set_freq(RADIO_VFO_A,before.vfo_a_hz); assert(radio_freedv_session_restore(&s,sequence));
    assert(radio_freedv_session_release(&s));
    assert(radio_freedv_session_acquire(&s,&o)); assert(radio_freedv_session_apply(&s,&chosen,radio_mic_handle));
    freedv_protection_trip(); /* radio-thread protection does not wait for LVGL */
    assert(s.abort_reason==FD_RADIO_TX_GUARD);
    fd_radio_session cached;
    radio_freedv_session_snapshot(&s,&cached);
    assert(cached.owned && cached.abort_reason==FD_RADIO_TX_GUARD && cached.last_error==FD_RADIO_TX_GUARD);
    cached.last_error=FD_RADIO_OK;assert(s.last_error==FD_RADIO_TX_GUARD); /* copy cannot mutate the owner */
    assert(!radio_freedv_session_observe(&o));
    snapshot(&after); assert(!after.route.modem && !after.route.ptt);
    assert(radio_freedv_session_clear(&s,&sequence)); observed(false);
    assert(radio_freedv_session_restore(&s,sequence) && radio_freedv_session_release(&s));
    assert(s.abort_reason==FD_RADIO_TX_GUARD); /* cleanup does not erase the abort cause */
    puts("PASS: real radio backend busy guards, route writes, held/stale TX, transient restoration and protection abort");
}
