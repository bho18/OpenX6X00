// SPDX-License-Identifier: LGPL-2.1-or-later
#define _POSIX_C_SOURCE 200809L
#include "diagnostics.h"
#include "native_audio.h"
#include "radio_session.h"
#include "../../tools/freedv/session_controller.h"
#include "../app_window.h"
#include "../radio.h"
#include "../board.h"
#include "../main.h"
#include "../control_owner.h"
#include "../front_keys.h"
#include "../key_input.h"
#include "../screen_main.h"
#include "../gui_styles.h"
#include "../msg.h"
#include "../cfg/cfg_api.h"
#include "lvgl/lvgl.h"
#include <pthread.h>
#include <stdatomic.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/statvfs.h>
#include <sys/utsname.h>
#include <dirent.h>
#include <time.h>
#include <unistd.h>
#include <errno.h>

#ifndef FREEDV_DATA_DIR
#define FREEDV_DATA_DIR BOARD_SHARE "/freedv"
#endif
#define CASE_CAPTURE 0
#define CASE_ROUTE_FIRST 1
#define CASE_ROUTE_LAST 8
#define CASE_FIXTURE 9
#define CASE_MIC_TX 10
#define CASE_LIVE_RX 11
#define CASE_RX_TONE 12
#define CASE_STOP 13
#define CASE_TIMEOUT 14
#define CASE_FAILURE 15
#define CASE_CONFIRM_RX 16
#define CASE_CONFIRM_TX 17
#define CASE_CONFIRM_STOP 18
#define CASE_INTERRUPTED_FIXTURE 19
#define CASE_MODEM_TX 20
static const char *case_names[] = {"Capture 10s", "Route R0 M0 P0", "Route R0 M0 P1", "Route R0 M1 P0", "Route R0 M1 P1", "Route R1 M0 P0", "Route R1 M0 P1", "Route R1 M1 P0", "Route R1 M1 P1", "RADE fixture to speaker 120s", "Hand/built-in mic RADE TX 8s", "Live receive RADE 120s", "Local tone during receive 20s", "Manual Stop test", "Watchdog timeout test", "Audio failure test", "Confirm unkeyed RX route", "Confirm independent mic/TX route", "Confirm Stop/timeout/failure observations", "Interrupted RADE fixture 20s", "Known RADE waveform TX 8s"};
typedef enum { CMD_ACQUIRE, CMD_ARM, CMD_RUN, CMD_EXPORT } command_kind;
typedef struct { command_kind command; unsigned selected, route, mic, channel, mixer; } command;
typedef struct {
    fd_native_stats audio;
    bool audio_known, radio_owned, radio_restored, radio_readback_match;
    unsigned case_number, transport_failures, restoration_failures, radio_error;
    uint64_t sampled_ms;
} diagnostic_progress;
static struct {
    pthread_mutex_t mutex;
    pthread_t worker;
    bool started, controller_ready;
    fd_controller controller;
    fd_radio_session radio;
    fd_native_audio *audio;
    bool audio_joined, audio_restored;
    atomic_bool case_running, stop, close, input_release_pending, controller_failed, evidence_failed;
    atomic_uint clears, clear_ms, abort_ms;
    command commands[8]; unsigned head, count;
    char message[180], directory[256], case_directory[320];
    FILE *events;
    unsigned case_number, current_case, current_route, current_mic, current_channel, current_mixer, last_case, last_route, last_mic, last_channel, last_mixer;
    uint64_t generation, started_ms, last_resource_ms;
    unsigned tx_qualified[2][4][5], rx_qualified[2][4][5], stop_checks[2][4][5][8];
    bool stop_confirmed[2][4][5][8];
    bool seen_tx, manually_stopped, preparation_failed, run_started;
    fd_native_stats final_audio;
    bool have_result;
    fd_stop_reason observed_cause, last_stop_reason;
    diagnostic_progress progress;
    uint64_t last_progress_ms;
} d = {.mutex=PTHREAD_MUTEX_INITIALIZER};
static struct {
    lv_obj_t *panel,*status,*note,*cases,*route,*mic,*channel,*mixer;
    lv_timer_t *timer;
    lv_group_t *group,*previous_group;
    lv_indev_t *devices[16];lv_group_t *groups[16];unsigned device_count;
} ui;
static lv_timer_t *service_timer;
static bool input_fence_owned, safe_deleting;
static uint64_t milliseconds(void) { struct timespec t; clock_gettime(CLOCK_MONOTONIC,&t); return (uint64_t)t.tv_sec*1000u+t.tv_nsec/1000000u; }
static void message(const char *text) { pthread_mutex_lock(&d.mutex); snprintf(d.message,sizeof(d.message),"%s",text); pthread_mutex_unlock(&d.mutex); }
static void json_string(FILE *f,const char *s) {
    fputc('"',f); for(const unsigned char *p=(const unsigned char *)s;*p;++p) {
        if(*p=='"'||*p=='\\') { fputc('\\',f); fputc(*p,f); }
        else if(*p<32) fprintf(f,"\\u%04x",*p); else fputc(*p,f);
    } fputc('"',f);
}
static FILE *private_file(const char *path) { FILE *f=fopen(path,"wx"); if(f)fchmod(fileno(f),0600); return f; }
static void event(const char *name) {
    if(!d.events)return;
    fprintf(d.events,"{\"monotonic_ms\":%llu,\"case\":%u,\"event\":",(unsigned long long)milliseconds(),d.case_number);
    json_string(d.events,name);fputs("}\n",d.events);if(fflush(d.events)||ferror(d.events))atomic_store(&d.evidence_failed,true);
}
static uint64_t directory_bytes(const char *name) {
    DIR *dir=opendir(name);if(!dir)return 0; uint64_t total=0;struct dirent *entry;
    while((entry=readdir(dir))) {
        if(entry->d_name[0]=='.')continue;
        char path[512];if(snprintf(path,sizeof(path),"%s/%s",name,entry->d_name)>=(int)sizeof(path)) {total=UINT64_MAX;break;}
        struct stat st;if(lstat(path,&st)) {total=UINT64_MAX;break;}
        uint64_t n=S_ISDIR(st.st_mode)?directory_bytes(path):S_ISREG(st.st_mode)?(uint64_t)st.st_size:0;
        if(n>UINT64_MAX-total) {total=UINT64_MAX;break;}total+=n;
    } closedir(dir);return total;
}
static bool storage_ok(uint64_t additional) {
    struct statvfs fs;
    const uint64_t limit=64u*1024u*1024u;
    uint64_t used=d.directory[0]?directory_bytes(d.directory):0;
    return additional<=limit && used<=limit-additional && !statvfs("/tmp",&fs) &&
        (uint64_t)fs.f_bavail*fs.f_frsize>=limit+additional;
}
static void copy_selected(const char *from,const char *to) {
    FILE *in=fopen(from,"rb");if(!in)return; FILE *out=private_file(to);
    if(out) { char block[4096];size_t n,total=0;while((n=fread(block,1,sizeof(block),in))&&total<128u*1024u) {if(fwrite(block,1,n,out)!=n)break;total+=n;}fclose(out); }fclose(in);
}
static bool bundle_open(void) {
    if(d.directory[0])return d.events!=NULL;
    if(!storage_ok(0)) {message("Need 64 MiB free temporary storage; export old results first");return false;}
    time_t now=time(NULL);struct tm t;gmtime_r(&now,&t);char stamp[32];strftime(stamp,sizeof(stamp),"%Y%m%d-%H%M%S",&t);
    char folder[256];snprintf(folder,sizeof(folder),"/tmp/freedv-native-%s-%ld",stamp,(long)getpid());
    if(mkdir(folder,0700)) {message("Cannot create private results directory");return false;}
    pthread_mutex_lock(&d.mutex);snprintf(d.directory,sizeof(d.directory),"%s",folder);pthread_mutex_unlock(&d.mutex);
    char path[384];snprintf(path,sizeof(path),"%s/events.jsonl",d.directory);d.events=private_file(path);
    if(!d.events){message("Cannot create event log");return false;}
    snprintf(path,sizeof(path),"%s/build-manifest.json",d.directory);copy_selected(FREEDV_DATA_DIR "/build-manifest.json",path);
    snprintf(path,sizeof(path),"%s/cpuinfo.txt",d.directory);copy_selected("/proc/cpuinfo",path);
    snprintf(path,sizeof(path),"%s/memory-initial.txt",d.directory);copy_selected("/proc/meminfo",path);
    snprintf(path,sizeof(path),"%s/inventory.json",d.directory);FILE *f=private_file(path);struct utsname u;memset(&u,0,sizeof(u));uname(&u);
    if(f) {fputs("{\"schema_version\":1,\"application\":",f);json_string(f,VERSION);fputs(",\"baseband\":",f);json_string(f,radio_base_ver_str());fputs(",\"machine\":",f);json_string(f,u.machine);fputs(",\"kernel\":",f);json_string(f,u.release);fprintf(f,",\"codec_available\":%s,\"physical_qualification\":\"INCONCLUSIVE\"}\n",fd_native_audio_codec_available()?"true":"false");fclose(f);}
    event("session_open");return true;
}
/* Coordinator/export data. Cached commands and packet measurements deliberately
 * remain distinct, including when the newest packet is unknown or stale. */
static void radio_observation_json(FILE *f,const radio_commanded_audio_route_t *route,bool supported,uint64_t begin_ms,uint64_t sampled_ms) {
    fprintf(f,"{\"sampled_monotonic_ms\":%llu,\"snapshot_window_ms\":%llu,\"cached_commands\":{\"available\":%s",
        (unsigned long long)sampled_ms,(unsigned long long)(sampled_ms-begin_ms),supported?"true":"false");
    if(supported)fprintf(f,",\"record\":%s,\"modem\":%s,\"software_ptt\":%s",route->commanded_record?"true":"false",route->commanded_modem?"true":"false",route->commanded_ptt?"true":"false");
    fputs("},\"physical_packet\":{\"source\":\"baseband_packet_tx_flag\",\"known\":",f);
    bool known=supported&&route->packet_tx_known;
    fputs(known?"true":"false",f);
    if(known) {
        fprintf(f,",\"tx\":%s,\"sequence\":%llu,\"age_ms\":%llu,\"fresh\":%s,\"received_monotonic_ms_estimate\":",route->packet_tx?"true":"false",(unsigned long long)route->packet_sequence,(unsigned long long)route->packet_age_ms,route->packet_age_ms<=FD_RADIO_PACKET_MAX_AGE_MS?"true":"false");
        if(route->packet_age_ms<=sampled_ms)fprintf(f,"%llu",(unsigned long long)(sampled_ms-route->packet_age_ms));else fputs("null",f);
        if(!route->packet_tx)fprintf(f,",\"rx_sequence\":%llu",(unsigned long long)route->packet_sequence);
    } else fputs(",\"tx\":null,\"sequence\":null,\"age_ms\":null,\"fresh\":false,\"received_monotonic_ms_estimate\":null",f);
    fputs("}}",f);
}
/* Only the coordinator reads d.audio. GUI readers get this bounded cached copy,
 * so slow joins, disk/ALSA operations or preparation never move into LVGL. */
static void cache_progress(void) {
    uint64_t now=milliseconds();if(now-d.last_progress_ms<100)return;d.last_progress_ms=now;
    diagnostic_progress progress={.case_number=d.case_number,.sampled_ms=now};
    fd_radio_session radio;
    pthread_mutex_lock(&d.controller.mutex);
    radio_freedv_session_snapshot(&d.radio,&radio);
    pthread_mutex_unlock(&d.controller.mutex);
    progress.radio_owned=radio.owned;progress.radio_restored=radio.restoration_complete;
    progress.radio_readback_match=radio.readback_match;
    progress.transport_failures=radio.transport_failures;
    progress.restoration_failures=radio.restoration_failures;progress.radio_error=radio.last_error;
    progress.audio_known=d.audio!=NULL||d.have_result;
    if(d.audio)progress.audio=fd_native_audio_observe(d.audio);
    else if(d.have_result)progress.audio=d.final_audio;
    pthread_mutex_lock(&d.mutex);d.progress=progress;pthread_mutex_unlock(&d.mutex);
}
static void resources(void) {
    uint64_t now=milliseconds();if(now-d.last_resource_ms<1000||!d.directory[0])return;d.last_resource_ms=now;
    char path[384];snprintf(path,sizeof(path),"%s/resources.jsonl",d.directory);FILE *f=fopen(path,"a");if(!f){atomic_store(&d.evidence_failed,true);return;}fchmod(fileno(f),0600);
    fprintf(f,"{\"monotonic_ms\":%llu,\"cpu_frequency_khz\":[",(unsigned long long)now);
    for(unsigned cpu=0;cpu<4;++cpu){snprintf(path,sizeof(path),"/sys/devices/system/cpu/cpu%u/cpufreq/scaling_cur_freq",cpu);FILE *in=fopen(path,"r");unsigned khz=0;bool ok=in&&fscanf(in,"%u",&khz)==1;if(in)fclose(in);if(cpu)fputc(',',f);if(ok)fprintf(f,"%u",khz);else fputs("null",f);}
    fputs("],\"rss_kib\":",f);FILE *status=fopen("/proc/self/status","r");unsigned rss=0;bool have_rss=false;
    if(status){char line[128];while(fgets(line,sizeof(line),status))if(sscanf(line,"VmRSS: %u",&rss)==1){have_rss=true;break;}fclose(status);}if(have_rss)fprintf(f,"%u",rss);else fputs("null",f);
    fputs(",\"temperature_millidegrees\":[",f);bool any=false;
    for(unsigned zone=0;zone<8;++zone){snprintf(path,sizeof(path),"/sys/class/thermal/thermal_zone%u/temp",zone);FILE *in=fopen(path,"r");long temp=0;if(in){if(fscanf(in,"%ld",&temp)==1){if(any)fputc(',',f);fprintf(f,"%ld",temp);any=true;}fclose(in);}}
    fprintf(f,"],\"case\":%u,\"radio\":",d.case_number);
    radio_commanded_audio_route_t route={0};uint64_t snapshot_begin=milliseconds();bool supported=radio_get_commanded_audio_route(&route);
    radio_observation_json(f,&route,supported,snapshot_begin,milliseconds());fputs("}\n",f);
    bool bad=ferror(f)!=0;if(fclose(f))bad=true;if(bad)atomic_store(&d.evidence_failed,true);
}
/* Controller callbacks must stay bounded and never do file I/O or worker joins. */
static bool acquire_snapshot(void *arg,bool *quiet,fd_observation *o) {
    (void)arg; bool acquired=radio_freedv_session_acquire(&d.radio,o);
    if(acquired)*quiet=d.radio.ready && !d.radio.saved.route.ptt && !d.radio.saved.route.modem;
    return acquired;
}
static bool release_radio(void *arg) {(void)arg;return radio_freedv_session_release(&d.radio);}
static bool observe_radio(void *arg,fd_observation *o) {
    (void)arg;d.observed_cause=FD_STOP_NONE;
    /* A worker ending is distinct from losing the radio observation. Keep the
     * fresh packet available for route qualification and post-clear recovery.
     * Controller callbacks run under its lock, so this state read is serialized. */
    bool ok=radio_freedv_session_observe(o);
    if(!ok||!o->known||!o->fresh){d.observed_cause=FD_STOP_OBSERVATION;return false;}
    if(atomic_load(&d.case_running)&&d.controller.state.state==FD_RUNNING) {
        if(atomic_load(&d.close))d.observed_cause=FD_STOP_CLOSE;
        else if(atomic_load(&d.stop))d.observed_cause=FD_STOP_OPERATOR;
        else if(atomic_load(&d.evidence_failed))d.observed_cause=FD_STOP_EVIDENCE;
        else if(d.audio&&fd_native_audio_stop_requested(d.audio))d.observed_cause=fd_native_audio_failed(d.audio)?FD_STOP_AUDIO:FD_STOP_NATURAL;
        if(d.observed_cause!=FD_STOP_NONE)return false;
    }
    return ok;
}
static fd_stop_reason observed_cause(void *arg) { (void)arg;return d.observed_cause; }
static bool tx_permitted(void *arg) {(void)arg;return radio_freedv_session_tx_permitted(&d.radio);}
static bool start_case(void *arg,const fd_case *selected) {
    (void)arg;
    if(atomic_load(&d.close)||atomic_load(&d.stop)||!d.audio||!radio_freedv_session_apply(&d.radio,selected,(radio_mic_sel_t)d.current_mic))return false;
    if(!fd_native_audio_start(d.audio))return false;
    d.run_started=true;atomic_store(&d.case_running,true);return true;
}
static bool clear_case(void *arg,uint64_t *sequence) {
    (void)arg;unsigned zero=0;atomic_compare_exchange_strong(&d.abort_ms,&zero,(unsigned)milliseconds());atomic_store(&d.case_running,false);
    if(d.audio)fd_native_audio_request_stop(d.audio);
    bool ok=radio_freedv_session_clear(&d.radio,sequence);
    atomic_store(&d.clear_ms,(unsigned)milliseconds());atomic_fetch_add(&d.clears,1);
    return ok;
}
static bool audio_quiet(void *arg) {(void)arg;return !d.audio||(d.audio_joined&&d.audio_restored&&fd_native_audio_quiescent(d.audio));}
static bool restore_radio(void *arg,uint64_t sequence) {(void)arg;return radio_freedv_session_restore(&d.radio,sequence);}
static bool case_is_tx(unsigned selected) {return (selected>=CASE_ROUTE_FIRST&&selected<=CASE_ROUTE_LAST)||selected==CASE_MIC_TX||selected==CASE_MODEM_TX||(selected>=CASE_STOP&&selected<=CASE_FAILURE);}
static const char *state_name(fd_state s) {switch(s){case FD_IDLE:return "Idle";case FD_READY:return "Ready";case FD_ARMED:return "Armed";case FD_RUNNING:return "Running";case FD_WAIT_RX:return "Waiting for RX / restoration";case FD_RESTORE:return "Restoring";case FD_PREPARING:return "Preparing while unkeyed";}return "Unknown";}
static void export_summary(void) {
    if(!d.directory[0])return;
    char path[384],temporary[384];snprintf(path,sizeof(path),"%s/session.json",d.directory);snprintf(temporary,sizeof(temporary),"%s/session.json.tmp",d.directory);
    FILE *f=fopen(temporary,"w");if(!f)return;fchmod(fileno(f),0600);fd_session status={0};fd_controller_status(&d.controller,&status);
    fprintf(f,"{\"schema_version\":1,\"cases_started\":%u,\"state\":",d.case_number);json_string(f,state_name(status.state));
    fprintf(f,",\"hand_mic_tx_routes_confirmed\":%u,\"built_in_tx_routes_confirmed\":%u,\"rx_routes_confirmed\":%u,\"stop_checks_completed\":%u,\"physical_qualification\":\"INCONCLUSIVE\",\"rf_receive_interoperability\":\"INCONCLUSIVE\",\"original_70_percent_margin\":\"FAIL: retained synchronized RX result\",\"operator_observations_required\":true}\n",d.tx_qualified[radio_mic_handle][0][0],d.tx_qualified[radio_mic_builtin][0][0],status.qualified_rx_routes,d.stop_checks[d.current_mic][d.current_channel][d.current_mixer][d.current_route]);
    if(fclose(f)==0)rename(temporary,path);else unlink(temporary);
    snprintf(path,sizeof(path),"%s/memory-latest.txt",d.directory);copy_selected("/proc/meminfo",path);
    if(d.events)fflush(d.events);
    message("Results saved locally. Copy the displayed /tmp folder to the PC before reboot.");
}
static void number(FILE *f,const char *key,uint64_t value) {fprintf(f,",\"%s\":%llu",key,(unsigned long long)value);}
static void snapshot_json(FILE *f,const hw_freedv_cached_snapshot_t *s) {
    fputs("{\"source\":\"cached_commands_not_measurements\"",f);
    number(f,"record",s->route.record);number(f,"modem",s->route.modem);number(f,"software_ptt",s->route.ptt);
    number(f,"microphone",s->microphone);number(f,"hand_mic_gain",s->hand_mic_gain);number(f,"internal_mic_gain",s->internal_mic_gain);
    number(f,"vox",s->vox_enabled);number(f,"line_in",s->line_in);number(f,"line_out",s->line_out);
    number(f,"speaker_mode",s->speaker_mode);number(f,"speaker_level",s->speaker_level);number(f,"monitor_level",s->monitor_level);
    number(f,"power_tenths_watt",s->requested_power_tenths_watt);number(f,"active_vfo",s->active_vfo);
    number(f,"vfo_a_hz",s->vfo_a_hz);number(f,"vfo_b_hz",s->vfo_b_hz);number(f,"mode_a",s->backend_mode_a);number(f,"mode_b",s->backend_mode_b);
    fprintf(f,",\"filter_low_hz\":%d,\"filter_high_hz\":%d",s->rx_filter_low_hz,s->rx_filter_high_hz);
    number(f,"protected_route_bits",s->protected_route_bits);fputc('}',f);
}
static void audio_json(FILE *f,const fd_native_stats *a) {
    fputs("{\"timing_scope\":\"all_active_blocks_including_acquisition_and_startup\"",f);
    number(f,"kind",a->kind);number(f,"generation",a->generation);number(f,"input_samples",a->input_samples);number(f,"output_samples",a->output_samples);
    number(f,"recording_excerpt_limit_ms",a->recording_excerpt_limit_ms);
    number(f,"tx_prefill_wait_ms",a->tx_prefill_wait_ms);number(f,"input_level_samples",a->input_level_samples);number(f,"input_peak",a->input_peak);
    fprintf(f,",\"input_ac_rms\":%.6f",a->input_ac_rms);
    number(f,"interrupted_input_samples",a->interrupted_input_samples);number(f,"recorder_samples",a->recorder_samples);number(f,"recorder_discarded_samples",a->recorder_discarded_samples);
    number(f,"aborted_codec_output_samples",a->aborted_codec_output_samples);number(f,"aborted_playback_samples",a->aborted_playback_samples);
    number(f,"capture_dropped_samples",a->capture.dropped_samples);number(f,"capture_hole_samples",a->capture.hole_samples);number(f,"capture_errors",a->capture.capture_errors);
    number(f,"playback_accepted_samples",a->playback.accepted_samples);number(f,"playback_write_errors",a->playback.write_errors);
    number(f,"playback_underflows_including_startup",a->playback.underflows);number(f,"playback_flushes",a->playback.flushes);number(f,"playback_flush_acknowledged",a->playback.flush_acknowledged);
    number(f,"playback_first_write_monotonic_ns",a->playback.first_write_monotonic_ns);
    number(f,"playback_underflows_before_first_write",a->playback.underflows_before_first_write);
    number(f,"playback_underflows_after_first_write",a->playback.underflows_after_first_write);
    number(f,"playback_underflow_events_discarded",a->playback.underflow_events_discarded);
    fputs(",\"playback_underflow_events\":[",f);
    for(unsigned i=0;i<a->playback.underflow_events_stored;++i) {
        const audio_freedv_underflow_event_t *e=&a->playback.underflow_events[i];
        if(i)fputc(',',f);
        fprintf(f,"{\"monotonic_ns\":%llu,\"accepted_samples\":%llu,\"cancellation_requested\":%s}",
            (unsigned long long)e->monotonic_ns,(unsigned long long)e->accepted_samples,e->cancellation_requested?"true":"false");
    }
    fputc(']',f);
    number(f,"playback_latency_known",a->playback.latency_known);number(f,"playback_queued_latency_us",a->playback.queued_latency_us);
    number(f,"pa_maxlength_bytes",a->playback.maxlength_bytes);number(f,"pa_target_bytes",a->playback.target_bytes);number(f,"pa_prebuffer_bytes",a->playback.prebuffer_bytes);number(f,"pa_min_request_bytes",a->playback.min_request_bytes);
    fputs(",\"codec\":{\"radio_io\":false",f);const fd_stream_stats *c=&a->codec;
    number(f,"opus_arch",c->opus_arch);number(f,"initialization_ns",c->initialization_ns);number(f,"represented_audio_ns",c->represented_audio_ns);
    number(f,"submitted_samples",c->submitted_samples);number(f,"consumed_samples",c->consumed_samples);number(f,"output_samples",c->output_samples);number(f,"delivered_samples",c->delivered_samples);
    number(f,"blocks",c->blocks);number(f,"rendered_blocks",c->rendered_blocks);number(f,"synchronized_blocks",c->synchronized_blocks);number(f,"feature_blocks",c->feature_blocks);
    number(f,"receiver_service_ns",c->receiver_service_ns);number(f,"speech_service_ns",c->speech_service_ns);
    number(f,"receiver_deadline_misses",c->receiver_deadline_misses);number(f,"speech_deadline_misses",c->speech_deadline_misses);
    number(f,"receiver_p99_ns",c->receiver_p99_ns);number(f,"receiver_max_ns",c->receiver_max_ns);number(f,"speech_p99_ns",c->speech_p99_ns);number(f,"speech_max_ns",c->speech_max_ns);
    fprintf(f,",\"receiver_p99_frame_ratio\":%.9f,\"speech_p99_frame_ratio\":%.9f",c->receiver_p99_frame_ratio,c->speech_p99_frame_ratio);
    number(f,"input_depth",c->input_depth);number(f,"output_depth",c->output_depth);number(f,"input_max_depth",c->input_max_depth);number(f,"output_max_depth",c->output_max_depth);
    number(f,"feature_max_depth",c->feature_max_depth);number(f,"failures",c->failures);number(f,"padded_native_samples",c->padded_native_samples);number(f,"tx_finished",c->tx_finished);
    number(f,"output_wait_events",c->output_wait_events);number(f,"output_wait_ns",c->output_wait_ns);
    number(f,"tx_prepared_blocks",c->tx_prepared_blocks);
    fputs("}}",f);
}
static void save_case(void) {
    char path[384];snprintf(path,sizeof(path),"%s/coordinator.json",d.case_directory);FILE *f=private_file(path);if(!f){atomic_store(&d.evidence_failed,true);return;}
    fd_native_stats a=d.final_audio;
    fprintf(f,"{\"schema_version\":1,\"case_number\":%u,\"name\":",d.case_number);json_string(f,case_names[d.current_case]);
    fprintf(f,",\"matrix_case\":%u,\"microphone\":%u,\"capture_channel\":%u,\"mixer_choice\":%u,\"seen_packet_tx\":%s,\"manual_stop\":%s,\"started_monotonic_ms\":%llu,\"abort_requested_monotonic_ms_low32\":%u,\"command_clear_monotonic_ms_low32\":%u,\"command_clear_elapsed_ms\":%u,\"preparation_failed\":%s,\"run_started\":%s,\"first_stop_reason\":%u,\"audio_failures\":%u,\"restoration_complete\":%s,\"transport_attempts\":%u,\"transport_failures\":%u,\"rf_result\":\"INCONCLUSIVE: attach SDR observation\"",d.current_route,d.current_mic,d.current_channel,d.current_mixer,d.seen_tx?"true":"false",d.manually_stopped?"true":"false",(unsigned long long)d.started_ms,atomic_load(&d.abort_ms),atomic_load(&d.clear_ms),atomic_load(&d.clear_ms)-atomic_load(&d.abort_ms),d.preparation_failed?"true":"false",d.run_started?"true":"false",(unsigned)d.last_stop_reason,a.failures,d.radio.restoration_complete?"true":"false",d.radio.transport_attempts,d.radio.transport_failures);
    number(f,"acquired_packet_sequence",d.radio.acquired_sequence);
    number(f,"command_clear_packet_sequence",d.radio.clear_sequence);
    radio_commanded_audio_route_t route={0};uint64_t snapshot_begin=milliseconds();bool supported=radio_get_commanded_audio_route(&route);
    fputs(",\"radio_at_result_export\":",f);radio_observation_json(f,&route,supported,snapshot_begin,milliseconds());
    number(f,"restoration_failures",d.radio.restoration_failures);number(f,"readback_match",d.radio.readback_match);number(f,"last_radio_error",d.radio.last_error);number(f,"radio_abort_reason",d.radio.abort_reason);number(f,"base_error_events",d.radio.base_error_events);
    fputs(",\"radio_before\":",f);snapshot_json(f,&d.radio.saved);fputs(",\"radio_after\":",f);snapshot_json(f,&d.radio.restored);
    fputs(",\"audio\":",f);audio_json(f,&a);
    char mixer[8192];
    fputs(",\"alsa_before\":",f);if(d.audio&&fd_native_audio_mixer_describe(d.audio,false,mixer,sizeof(mixer)))fputs(mixer,f);else fputs("null",f);
    fputs(",\"alsa_after\":",f);if(d.audio&&fd_native_audio_mixer_describe(d.audio,true,mixer,sizeof(mixer)))fputs(mixer,f);else fputs("null",f);
    fputs("}\n",f);bool bad=ferror(f)!=0;if(fclose(f))bad=true;if(bad)atomic_store(&d.evidence_failed,true);
}
static bool select_case(command cmd) {
    if(cmd.selected==CASE_CAPTURE)return fd_controller_select_passive_rx(&d.controller);
    if(cmd.selected>=CASE_ROUTE_FIRST&&cmd.selected<=CASE_ROUTE_LAST)return fd_controller_select_matrix(&d.controller,(fd_matrix_case)(cmd.selected-CASE_ROUTE_FIRST));
    if(cmd.selected==CASE_MIC_TX||cmd.selected==CASE_MODEM_TX)return fd_controller_select_mic_tx(&d.controller,(fd_matrix_case)cmd.route);
    if(cmd.selected>=CASE_STOP&&cmd.selected<=CASE_FAILURE)return fd_controller_select_matrix(&d.controller,(fd_matrix_case)cmd.route);
    return fd_controller_select_rx_playback(&d.controller,(fd_matrix_case)cmd.route);
}
static bool prepare_case(command cmd) {
    if(atomic_load(&d.evidence_failed)){message("Result storage failed; export this incomplete session and restart diagnostics");return false;}
    unsigned excerpt_ms=cmd.selected==CASE_RX_TONE||cmd.selected==CASE_INTERRUPTED_FIXTURE?20000:
        cmd.selected==CASE_CAPTURE?10000:cmd.selected==CASE_FIXTURE||cmd.selected==CASE_LIVE_RX?4000:8000;
    /* Reserve full bounded excerpts plus metadata before preparation. Audio
     * callbacks never write files; no evidence is deleted to recover space. */
    unsigned channels=1+(cmd.channel?2:0)+(cmd.selected!=CASE_CAPTURE);
    uint64_t reserve=(uint64_t)excerpt_ms*44100u*2u*channels/1000u+1024u*1024u;
    if(!storage_ok(reserve)){message("Storage reserve/capture budget reached. Export and close this session.");return false;}
    if(cmd.selected==CASE_MIC_TX&&(!(d.tx_qualified[cmd.mic][cmd.channel][cmd.mixer]&(1u<<cmd.route))||!d.stop_confirmed[cmd.mic][cmd.channel][cmd.mixer][cmd.route])){message("First confirm independent mic/TX routing and all three Stop checks");return false;}
    if(cmd.selected==CASE_MODEM_TX&&!d.stop_confirmed[cmd.mic][cmd.channel][cmd.mixer][cmd.route]){message("First complete and confirm all three functional Stop checks");return false;}
    if((cmd.selected==CASE_FIXTURE||cmd.selected==CASE_INTERRUPTED_FIXTURE||cmd.selected==CASE_MIC_TX||cmd.selected==CASE_LIVE_RX||cmd.selected==CASE_MODEM_TX)&&!fd_native_audio_codec_available()){message("This build has no native codec; diagnostic image build must enable it");return false;}
    if((cmd.selected==CASE_FIXTURE||cmd.selected==CASE_INTERRUPTED_FIXTURE||cmd.selected==CASE_LIVE_RX||cmd.selected==CASE_RX_TONE)&&!(d.rx_qualified[cmd.mic][cmd.channel][cmd.mixer]&(1u<<cmd.route))){message("Confirm an unkeyed route for this exact microphone/channel/mixer first");return false;}
    if(!select_case(cmd)){message("Case selection refused; restore the previous case or confirm its unkeyed RX route");return false;}
    if(!fd_controller_begin_prepare(&d.controller)){message("Preparation refused: need fresh RX and an exclusive ready session");return false;}
    d.current_case=cmd.selected;d.current_route=cmd.selected>=CASE_ROUTE_FIRST&&cmd.selected<=CASE_ROUTE_LAST?cmd.selected-CASE_ROUTE_FIRST:cmd.route;d.current_mic=cmd.mic;d.current_channel=cmd.channel;d.current_mixer=cmd.mixer;
    atomic_store(&d.abort_ms,0);atomic_store(&d.clear_ms,0);
    ++d.case_number;++d.generation;d.seen_tx=d.manually_stopped=false;d.have_result=false;d.preparation_failed=false;d.run_started=false;d.started_ms=0;d.audio_joined=d.audio_restored=false;memset(&d.final_audio,0,sizeof(d.final_audio));
    snprintf(d.case_directory,sizeof(d.case_directory),"%s/case-%03u",d.directory,d.case_number);
    event("case_preparation_requested");
    if(mkdir(d.case_directory,0700)){d.preparation_failed=true;message("Cannot create case output directory");fd_controller_stop(&d.controller);return false;}
    fd_native_kind kind=cmd.selected==CASE_CAPTURE?FD_NATIVE_CAPTURE:(cmd.selected==CASE_FIXTURE||cmd.selected==CASE_INTERRUPTED_FIXTURE)?FD_NATIVE_RADE_FIXTURE:cmd.selected==CASE_MIC_TX?FD_NATIVE_RADE_MIC_TX:cmd.selected==CASE_MODEM_TX?FD_NATIVE_MODEM_TX:cmd.selected==CASE_LIVE_RX?FD_NATIVE_RADE_LIVE_RX:FD_NATIVE_TONE;
    static const char *mixers[]={NULL,"Stereo","Reverse Stereo","Sum Mono","Mix Mono"};
    fd_native_config config={.kind=kind,.fixture_path=FREEDV_DATA_DIR "/fixtures/rade-continuous.raw",.output_directory=d.case_directory,.generation=d.generation,.duration_ms=cmd.selected==CASE_FIXTURE||cmd.selected==CASE_LIVE_RX?120000:(cmd.selected==CASE_RX_TONE||cmd.selected==CASE_INTERRUPTED_FIXTURE)?20000:cmd.selected==CASE_CAPTURE?10000:8000,.capture_channel=cmd.channel,.mixer_item=mixers[cmd.mixer],.capture_input=true,.inject_failure_ms=cmd.selected==CASE_FAILURE?1500:0,.interrupt_fixture=cmd.selected==CASE_INTERRUPTED_FIXTURE};
    config.excerpt_ms=excerpt_ms>config.duration_ms?config.duration_ms:excerpt_ms;
    char error[180]={0};d.audio=fd_native_audio_prepare(&config,error,sizeof(error));
    event("case_preparation");
    if(!d.audio||!fd_native_audio_observe(d.audio).prepared){d.preparation_failed=true;message(error[0]?error:"Audio preparation failed; stopping and restoring");fd_controller_stop(&d.controller);return false;}
    if(!fd_controller_finish_prepare(&d.controller)){d.preparation_failed=true;message("RX changed during preparation; waiting for restoration");return false;}
    return true;
}
static void command_execute(command cmd) {
    if(atomic_load(&d.close)&&cmd.command!=CMD_EXPORT)return;
    fd_session state={0};fd_controller_status(&d.controller,&state);
    if(cmd.command==CMD_ACQUIRE){
        if(state.state!=FD_IDLE){message("A diagnostic session already owns the radio");return;}
        if(!bundle_open())return;
        if(fd_controller_acquire(&d.controller)){memset(d.tx_qualified,0,sizeof(d.tx_qualified));memset(d.rx_qualified,0,sizeof(d.rx_qualified));memset(d.stop_checks,0,sizeof(d.stop_checks));memset(d.stop_confirmed,0,sizeof(d.stop_confirmed));d.have_result=false;message("Session acquired. Use dummy load; requested TX power is 1 W.");event("radio_session_acquired");}
        else message("Session refused or awaiting RX: close other audio/digital apps, release PTT and retry");
        return;
    }
    if(cmd.command==CMD_EXPORT){event("operator_export");export_summary();return;}
    if(state.state!=FD_READY&&!(cmd.command==CMD_RUN&&state.state==FD_ARMED)){message("Finish Stop/restoration before preparing another case");return;}
    if(cmd.selected>=CASE_CONFIRM_RX&&cmd.selected<=CASE_CONFIRM_STOP){
        if(cmd.command!=CMD_RUN||state.state!=FD_READY){message("Select Run to record the selected local confirmation");return;}
        bool ok=false;
        bool same=!atomic_load(&d.evidence_failed)&&d.have_result&&d.last_route==cmd.route&&d.last_mic==cmd.mic&&d.last_channel==cmd.channel&&d.last_mixer==cmd.mixer&&!d.final_audio.failures&&!d.final_audio.capture.dropped_samples&&!d.final_audio.capture.hole_samples&&!d.final_audio.capture.capture_errors&&!d.final_audio.playback.write_errors&&!d.final_audio.codec.failures;
        if(cmd.selected==CASE_CONFIRM_RX&&same){ok=fd_controller_confirm_rx_route(&d.controller,(fd_matrix_case)cmd.route,true);if(ok)d.rx_qualified[cmd.mic][cmd.channel][cmd.mixer]|=1u<<cmd.route;}
        else if(cmd.selected==CASE_CONFIRM_TX&&same&&d.last_case>=CASE_ROUTE_FIRST&&d.last_case<=CASE_ROUTE_LAST&&d.last_route==cmd.route&&d.last_mic==cmd.mic&&d.seen_tx&&!d.final_audio.failures){d.tx_qualified[cmd.mic][cmd.channel][cmd.mixer]|=1u<<cmd.route;ok=true;}
        else if(cmd.selected==CASE_CONFIRM_STOP&&!atomic_load(&d.evidence_failed)&&d.stop_checks[cmd.mic][cmd.channel][cmd.mixer][cmd.route]==7){d.stop_confirmed[cmd.mic][cmd.channel][cmd.mixer][cmd.route]=true;ok=true;}
        event(ok?"operator_confirmation_recorded":"operator_confirmation_refused");message(ok?"Observation confirmed locally; see exported events":"Confirmation refused: complete the corresponding measurements first");return;
    }
    bool tx=case_is_tx(cmd.selected);
    if(cmd.command==CMD_ARM){
        if(!tx){message("This receive case does not require RF arming; use Run");return;}
        if(prepare_case(cmd)&&fd_controller_arm(&d.controller,true))message("Armed locally. Run explicitly; every RF case is bounded to 10 seconds.");
        return;
    }
    if(tx){
        if(state.state!=FD_ARMED||cmd.selected!=d.current_case||(cmd.selected>=CASE_ROUTE_FIRST&&cmd.selected<=CASE_ROUTE_LAST?cmd.selected-CASE_ROUTE_FIRST:cmd.route)!=d.current_route||cmd.mic!=d.current_mic||cmd.channel!=d.current_channel||cmd.mixer!=d.current_mixer){message("Select Arm for this exact case/route/microphone/channel/mixer, then Run");return;}
    }else if(!prepare_case(cmd))return;
    d.started_ms=milliseconds();unsigned timeout=cmd.selected==CASE_TIMEOUT?3000:tx?8000:cmd.selected==CASE_CAPTURE?10000:(cmd.selected==CASE_RX_TONE||cmd.selected==CASE_INTERRUPTED_FIXTURE)?20000:120000;
    if(fd_controller_run(&d.controller,d.started_ms,timeout)){message("Running. Stop is always available.");event("case_run");}
    else {message("Run refused by RX/frequency/protection guards; restoring prepared resources");fd_controller_stop(&d.controller);}
}
/* Worker joins and export stay outside the watchdog/controller lock. */
static void restore_case_iteration(const fd_session *status) {
    if(d.audio&&!d.audio_joined&&fd_native_audio_finish(d.audio)){d.audio_joined=true;d.final_audio=fd_native_audio_observe(d.audio);event("audio_workers_joined");}
    fd_observation o={0};bool rx=radio_freedv_session_observe(&o)&&o.known&&o.fresh&&!o.tx&&status->software_cleared&&o.sequence>status->clear_sequence;
    if(rx&&d.audio&&d.audio_joined&&!d.audio_restored&&fd_native_audio_quiescent(d.audio)){d.audio_restored=fd_native_audio_restore(d.audio);if(d.audio_restored)event("mixer_restored");}
    if(fd_controller_poll_restore(&d.controller)){
        fd_session restored;fd_controller_status(&d.controller,&restored);d.last_stop_reason=restored.stop_reason;
        event("radio_restored");
        if(d.audio){d.final_audio=fd_native_audio_observe(d.audio);save_case();d.last_case=d.current_case;d.last_route=d.current_route;d.last_mic=d.current_mic;d.last_channel=d.current_channel;d.last_mixer=d.current_mixer;d.have_result=d.run_started&&!d.preparation_failed&&!atomic_load(&d.evidence_failed);
            if(d.seen_tx){if(d.current_case==CASE_STOP&&d.last_stop_reason==FD_STOP_OPERATOR&&!d.final_audio.failures)d.stop_checks[d.current_mic][d.current_channel][d.current_mixer][d.current_route]|=1;if(d.current_case==CASE_TIMEOUT&&d.last_stop_reason==FD_STOP_TIMEOUT&&!d.final_audio.failures)d.stop_checks[d.current_mic][d.current_channel][d.current_mixer][d.current_route]|=2;if(d.current_case==CASE_FAILURE&&d.last_stop_reason==FD_STOP_AUDIO&&d.final_audio.failures)d.stop_checks[d.current_mic][d.current_channel][d.current_mixer][d.current_route]|=4;}
            fd_native_audio_destroy(d.audio);d.audio=NULL;
        }
        if(!d.audio&&d.preparation_failed)save_case();
        message("Restored. Record operator/SDR observations; continue with another case.");export_summary();
    }else if(o.known&&o.tx)message("Release microphone PTT. Restoration remains blocked while TX is observed.");
}
static void *coordinator(void *unused) {
    (void)unused;
    fd_controller_backend backend={.acquire_snapshot=acquire_snapshot,.release=release_radio,.observe=observe_radio,.tx_permitted=tx_permitted,.start=start_case,.quiesce=clear_case,.audio_quiescent=audio_quiet,.restore_if_safe=restore_radio,.abort_reason=observed_cause};
    if(!fd_controller_init(&d.controller,&backend,NULL)){message("Cannot initialize independent diagnostic watchdog");atomic_store(&d.controller_failed,true);return NULL;}
    pthread_mutex_lock(&d.mutex);d.controller_ready=true;pthread_mutex_unlock(&d.mutex);
    unsigned seen_clears=0;
    for(;;){
        if(atomic_exchange(&d.stop,false)){d.manually_stopped=!atomic_load(&d.close);fd_controller_stop_with_reason(&d.controller,atomic_load(&d.close)?FD_STOP_CLOSE:FD_STOP_OPERATOR);event(d.manually_stopped?"operator_stop":"operator_close");}
        fd_session status={0};fd_controller_status(&d.controller,&status);
        unsigned clears=atomic_load(&d.clears);if(clears!=seen_clears){seen_clears=clears;event("software_commands_cleared_or_retry");}
        if(status.state==FD_RUNNING){fd_observation o={0};if(radio_freedv_session_observe(&o)&&o.known&&o.fresh&&o.tx)d.seen_tx=true;}
        if(status.state==FD_WAIT_RX)restore_case_iteration(&status);
        fd_controller_status(&d.controller,&status);
        if(atomic_load(&d.close)){
            if(status.state==FD_READY){if(fd_controller_release(&d.controller)){atomic_store(&d.input_release_pending,true);event("radio_session_released");export_summary();atomic_store(&d.close,false);message("Radio restored and released. Results remain in the /tmp folder.");}}
            else if(status.state==FD_IDLE){atomic_store(&d.input_release_pending,true);atomic_store(&d.close,false);}
            else if(status.state!=FD_WAIT_RX&&status.state!=FD_RESTORE)fd_controller_stop_with_reason(&d.controller,FD_STOP_CLOSE);
        }
        command cmd;bool have=false;
        pthread_mutex_lock(&d.mutex);if(d.count){cmd=d.commands[d.head];d.head=(d.head+1)%8;--d.count;have=true;}pthread_mutex_unlock(&d.mutex);
        if(have)command_execute(cmd);
        cache_progress();resources();struct timespec delay={0,20000000};nanosleep(&delay,NULL);
    }
    return NULL;
}
static bool local_input(void) {return front_keys_diagnostic_local_event();}
static void enqueue(command_kind kind) {
    if(!local_input()&&kind!=CMD_EXPORT){message("Use local MFK/front-panel controls for Acquire, Arm, Run and confirmations");return;}
    if(kind==CMD_ACQUIRE)ctl_local_input();
    command cmd={kind,lv_dropdown_get_selected(ui.cases),lv_dropdown_get_selected(ui.route),lv_dropdown_get_selected(ui.mic)==0?radio_mic_handle:radio_mic_builtin,lv_dropdown_get_selected(ui.channel),lv_dropdown_get_selected(ui.mixer)};
    pthread_mutex_lock(&d.mutex);if(d.count<8){d.commands[(d.head+d.count)%8]=cmd;++d.count;}else snprintf(d.message,sizeof(d.message),"Command queue is full; Stop remains available");pthread_mutex_unlock(&d.mutex);
}
static void discard_commands(void) {
    pthread_mutex_lock(&d.mutex);d.count=0;d.head=0;pthread_mutex_unlock(&d.mutex);
}
static void button_event(lv_event_t *e) {
    intptr_t action=(intptr_t)lv_event_get_user_data(e);
    if(action==4){discard_commands();unsigned zero=0;atomic_compare_exchange_strong(&d.abort_ms,&zero,(unsigned)milliseconds());atomic_store(&d.stop,true);return;}
    if(action==5){discard_commands();atomic_store(&d.close,true);atomic_store(&d.stop,true);message("Closing after audio flush, fresh RX and exact restoration");return;}
    enqueue(action==0?CMD_ACQUIRE:action==1?CMD_ARM:action==2?CMD_RUN:CMD_EXPORT);
}
static void deleted(lv_event_t *e) {
    (void)e;discard_commands();
    if(!safe_deleting){atomic_store(&d.stop,true);atomic_store(&d.close,true);}
    for(unsigned i=0;i<ui.device_count;++i){lv_indev_reset(ui.devices[i],NULL);lv_indev_set_group(ui.devices[i],ui.groups[i]);}
    keyboard_group=ui.previous_group;
    if(ui.group)lv_group_del(ui.group);
    main_screen_keys_enable(true);
    if(ui.timer){lv_timer_del(ui.timer);ui.timer=NULL;}memset(&ui,0,sizeof(ui));
}
static void refresh(lv_timer_t *timer) {
    (void)timer;if(!ui.panel)return;
    char text[1100],info[180],directory[256],route_text[160],counters[420];bool ready;diagnostic_progress progress;
    pthread_mutex_lock(&d.mutex);snprintf(info,sizeof(info),"%s",d.message);snprintf(directory,sizeof(directory),"%s",d.directory);ready=d.controller_ready;progress=d.progress;pthread_mutex_unlock(&d.mutex);
    fd_session state={0};if(ready)fd_controller_status(&d.controller,&state);
    radio_commanded_audio_route_t route={0};bool observed=radio_get_commanded_audio_route(&route);
    if(!observed)snprintf(route_text,sizeof(route_text),"Commands unavailable | packet unknown");
    else if(!route.packet_tx_known)snprintf(route_text,sizeof(route_text),"Cmd R%d M%d P%d | packet unknown",route.commanded_record,route.commanded_modem,route.commanded_ptt);
    else snprintf(route_text,sizeof(route_text),"Cmd R%d M%d P%d | %s #%llu age %llu ms%s",route.commanded_record,route.commanded_modem,route.commanded_ptt,route.packet_tx?"TX":"RX",(unsigned long long)route.packet_sequence,(unsigned long long)route.packet_age_ms,route.packet_age_ms>FD_RADIO_PACKET_MAX_AGE_MS?" STALE":"");
    if(progress.audio_known) {
        fd_native_stats *a=&progress.audio;fd_stream_stats *c=&a->codec;
        const char *restoration=!progress.radio_owned?"released":progress.radio_restored&&progress.radio_readback_match&&a->mixer_restored?"verified":"pending";
        char capture_level[80];
        if(a->input_level_samples)snprintf(capture_level,sizeof(capture_level),"Capture AC %.1f / peak %u",a->input_ac_rms,a->input_peak);
        else snprintf(capture_level,sizeof(capture_level),"Capture AC not sampled");
        uint64_t now=milliseconds(),age=now>=progress.sampled_ms?now-progress.sampled_ms:0;
        snprintf(counters,sizeof(counters),"Input %llu / play %llu | audio faults %u\nCodec %llu blocks / sync %llu | uf(all) %llu\n%s\nQ in/out/f %u/%u/%u cap %u | D/H %u/%u\nRestore %s | bus errors %u | c%u cache %llu ms",
            (unsigned long long)a->input_samples,(unsigned long long)a->output_samples,a->failures,
            (unsigned long long)c->blocks,(unsigned long long)c->synchronized_blocks,(unsigned long long)a->playback.underflows,
            capture_level,
            c->input_depth,c->output_depth,c->feature_depth,a->capture.queued_samples,a->capture.dropped_samples,a->capture.hole_samples,
            restoration,progress.transport_failures,progress.case_number,(unsigned long long)age);
    } else snprintf(counters,sizeof(counters),"Audio counters: preparing / not sampled");
    snprintf(text,sizeof(text),"%s\n%s\n%s\n%s",radio_freedv_startup_waiting()?"Release PTT: startup waiting for fresh RX":ready?state_name(state.state):"Starting diagnostics",info,route_text,counters);
    lv_label_set_text(ui.status,text);lv_label_set_text(ui.note,directory[0]?directory:"Use a dummy load and the receive-only SDR. Choose USB before acquiring.");
}
/* This GUI timer remains alive after a forced panel deletion. Remote injection
 * is unblocked only after radio ownership has actually been released. */
static void service(lv_timer_t *timer) {
    (void)timer;
    if(atomic_load(&d.controller_failed)&&atomic_load(&d.close))atomic_store(&d.input_release_pending,true);
    if(!atomic_load(&d.input_release_pending))return;
    fd_session state={0};
    pthread_mutex_lock(&d.mutex);bool ready=d.controller_ready;pthread_mutex_unlock(&d.mutex);
    if(!atomic_load(&d.controller_failed)&&(!ready||!fd_controller_status(&d.controller,&state)||state.state!=FD_IDLE))return;
    atomic_store(&d.input_release_pending,false);
    if(ui.panel){safe_deleting=true;lv_obj_del(ui.panel);safe_deleting=false;}
    if(input_fence_owned){front_keys_diagnostic_input_release();input_fence_owned=false;}
    if(atomic_load(&d.controller_failed)){d.started=false;pthread_mutex_lock(&d.mutex);d.controller_ready=false;pthread_mutex_unlock(&d.mutex);atomic_store(&d.close,false);}
}
static lv_obj_t *choice(lv_obj_t *parent,const char *options,int x,int y,int w) {lv_obj_t *o=lv_dropdown_create(parent);lv_obj_set_pos(o,x,y);lv_obj_set_size(o,w,32);lv_dropdown_set_options(o,options);lv_group_add_obj(ui.group,o);return o;}
static void open_panel(void *unused) {
    (void)unused;if(ui.panel)return;
    if(!input_fence_owned){if(!front_keys_diagnostic_input_acquire()){msg_schedule_text_fmt("Cannot isolate local diagnostic controls; retry after releasing keys");return;}input_fence_owned=true;}
    if(!service_timer)service_timer=lv_timer_create(service,100,NULL);
    /* Settings closes before acquisition; the ordinary waterfall remains visible
     * beside this panel and keeps its normal DSP/rendering workload. */
    if(dialog_is_run())dialog_destruct();
    if(!d.started){atomic_init(&d.evidence_failed,false);atomic_init(&d.input_release_pending,false);atomic_init(&d.controller_failed,false);atomic_init(&d.case_running,false);atomic_init(&d.stop,false);atomic_init(&d.close,false);atomic_init(&d.clears,0);atomic_init(&d.clear_ms,0);atomic_init(&d.abort_ms,0);if(pthread_create(&d.worker,NULL,coordinator,NULL)){message("Cannot start coordinator");front_keys_diagnostic_input_release();input_fence_owned=false;return;}d.started=true;pthread_detach(d.worker);}
    ui.previous_group=keyboard_group;ui.group=lv_group_create();keyboard_group=ui.group;lv_group_set_wrap(ui.group,true);
    for(lv_indev_t *device=lv_indev_get_next(NULL);device;device=lv_indev_get_next(device)){
        lv_indev_type_t type=lv_indev_get_type(device);
        if((type==LV_INDEV_TYPE_KEYPAD||type==LV_INDEV_TYPE_ENCODER)&&ui.device_count<16){unsigned i=ui.device_count++;ui.devices[i]=device;ui.groups[i]=device->group;lv_indev_reset(device,NULL);lv_indev_set_group(device,ui.group);}
    }
    main_screen_keys_enable(false);
    ui.panel=lv_obj_create(lv_scr_act());lv_obj_set_size(ui.panel,390,440);lv_obj_set_pos(ui.panel,BOARD_DISP_W-395,20);lv_obj_set_style_pad_all(ui.panel,8,0);lv_obj_set_style_text_font(ui.panel,&sony_14,0);lv_obj_set_style_text_line_space(ui.panel,1,0);lv_obj_clear_flag(ui.panel,LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_t *title=lv_label_create(ui.panel);lv_label_set_text(title,"FreeDV Diagnostics | MFK turn / press");lv_obj_set_pos(title,0,0);
    char options[1100]={0};for(unsigned i=0;i<sizeof(case_names)/sizeof(case_names[0]);++i){if(i)strcat(options,"\n");strcat(options,case_names[i]);}
    ui.cases=choice(ui.panel,options,0,25,370);ui.route=choice(ui.panel,"R0 M0 P0\nR0 M0 P1\nR0 M1 P0\nR0 M1 P1\nR1 M0 P0\nR1 M0 P1\nR1 M1 P0\nR1 M1 P1",0,63,180);
    ui.mic=choice(ui.panel,"Hand mic\nBuilt-in mic",190,63,180);ui.channel=choice(ui.panel,"Existing mono\nLeft capture\nRight capture\nExplicit L/R mean",0,101,180);ui.mixer=choice(ui.panel,"Keep mixer\nStereo\nReverse Stereo\nSum Mono\nMix Mono",190,101,180);
    ui.status=lv_label_create(ui.panel);lv_obj_set_pos(ui.status,0,143);lv_obj_set_width(ui.status,370);lv_label_set_long_mode(ui.status,LV_LABEL_LONG_WRAP);
    const char *labels[]={"Acquire","Arm","Run","Export","Stop","Close"};for(int i=0;i<6;++i){lv_obj_t *b=lv_btn_create(ui.panel);lv_obj_set_pos(b,(i%3)*125,275+(i/3)*38);lv_obj_set_size(b,120,33);lv_group_add_obj(ui.group,b);lv_obj_add_event_cb(b,button_event,LV_EVENT_CLICKED,(void *)(intptr_t)i);lv_obj_t *l=lv_label_create(b);lv_label_set_text(l,labels[i]);lv_obj_center(l);}
    lv_group_focus_obj(ui.cases);
    ui.note=lv_label_create(ui.panel);lv_obj_set_pos(ui.note,0,356);lv_obj_set_width(ui.note,370);lv_label_set_long_mode(ui.note,LV_LABEL_LONG_WRAP);lv_obj_add_event_cb(ui.panel,deleted,LV_EVENT_DELETE,NULL);ui.timer=lv_timer_create(refresh,250,NULL);refresh(NULL);
}
void freedv_diagnostics_open(void) {lv_async_call(open_panel,NULL);}
bool freedv_diagnostics_is_open(void) {return ui.panel!=NULL;}
