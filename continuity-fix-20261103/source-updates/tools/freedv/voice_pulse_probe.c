/* Isolated device regression: real PulseAudio + production engine/adapter/recorders.
 * Stub only the unrelated DSP consumer. Uses private virtual devices, no RF. */
#include "src/audio.c"
#include "src/freedv/native_audio.h"
#include "src/freedv/voice_audio.h"
#include <assert.h>
#include <stdarg.h>
#include <sys/stat.h>
static atomic_uint legacy_calls;
void dsp_put_audio_samples(size_t n,int16_t *p){
    (void)n;(void)p;atomic_fetch_add(&legacy_calls,1);
    if(getenv("FREEDV_PROBE_SLOW_LEGACY"))usleep(600000);
}
void freedv_mode_device_fault(void){fprintf(stderr,"PA context failed\n");abort();}
void _lv_log_add(lv_log_level_t level,const char *file,int line,const char *func,const char *fmt,...){
    (void)level;(void)file;(void)line;(void)func;va_list a;va_start(a,fmt);vfprintf(stderr,fmt,a);va_end(a);fputc('\n',stderr);
}
static uint64_t process_cpu_ns(void){struct timespec t;assert(!clock_gettime(CLOCK_PROCESS_CPUTIME_ID,&t));return (uint64_t)t.tv_sec*1000000000u+t.tv_nsec;}
int main(int argc,char **argv){
    assert(argc==2 && getenv("PULSE_SERVER"));
    assert(!strncmp(getenv("PULSE_SERVER"),"unix:/tmp/freedv-pulse-probe-",27));
    play_device="freedv_probe_sink";
    capture_device="freedv_probe_source";
    mloop=pa_threaded_mainloop_new();assert(!pa_threaded_mainloop_start(mloop));
    mlapi=pa_threaded_mainloop_get_api(mloop);context_connect();
    pa_threaded_mainloop_lock(mloop);
    pa_sample_spec spec={PA_SAMPLE_S16NE,44100,1};
    pa_buffer_attr attr;memset(&attr,255,sizeof(attr));attr.fragsize=8820;
    capture_stm=pa_stream_new(ctx,"production-capture",&spec,NULL);assert(capture_stm);
    pa_stream_set_state_callback(capture_stm,on_stream_state,NULL);
    pa_stream_set_read_callback(capture_stm,read_callback,NULL);
    assert(!pa_stream_connect_record(capture_stm,"freedv_probe_source",&attr,PA_STREAM_ADJUST_LATENCY|PA_STREAM_DONT_MOVE));
    while(pa_stream_get_state(capture_stm)==PA_STREAM_CREATING)pa_threaded_mainloop_wait(mloop);
    assert(pa_stream_get_state(capture_stm)==PA_STREAM_READY);
    pa_threaded_mainloop_unlock(mloop);
    fd_voice_audio *retained=NULL;
    for(unsigned turn=1;turn<=4;++turn){
        char dir[1024],error[160];snprintf(dir,sizeof(dir),"%s/turn-%u",argv[1],turn);assert(!mkdir(dir,0700));
        fd_native_config config={.kind=FD_NATIVE_PERSISTENT_TX,.duration_ms=8000,.excerpt_ms=9500,
            .generation=turn,.output_directory=dir,.persistent_slot=&retained};
        fd_native_audio *a=fd_native_audio_prepare(&config,error,sizeof(error));assert(a);
        assert(fd_native_audio_observe(a).prepared);assert(fd_native_audio_start(a));
        char trace_path[1100];snprintf(trace_path,sizeof(trace_path),"%s/trace.jsonl",dir);FILE *trace=fopen(trace_path,"w");assert(trace);
        unsigned start=audio_clock_ms(),last_trace=0,trace_count=0;uint64_t cpu_start=process_cpu_ns();
        while(!fd_native_audio_stop_requested(a)){
            unsigned elapsed=audio_clock_ms()-start;assert(elapsed<12000);
            if(elapsed>=last_trace+100 && trace_count<120){
                fd_voice_audio_snapshot t;
                if(fd_voice_audio_snapshot_get(retained,&t)){
                    fprintf(trace,"{\"elapsed_ms\":%u,\"generation\":%llu,\"input_samples\":%llu,\"capture_queue\":%u,\"engine_input_depth\":%u,\"engine_output_depth\":%u,\"engine_output_samples_total\":%llu,\"device_accepted_samples\":%llu,\"speech_frames_total\":%llu,\"modem_blocks_total\":%llu,\"processing_ns_total\":%llu,\"queue_wait_ns_total\":%llu,\"scheduling_delay_ns_total\":%llu,\"process_cpu_ns\":%llu}\n",
                        elapsed,(unsigned long long)t.generation,(unsigned long long)t.engine.input_samples,t.capture.queued_samples,t.engine.input_depth,t.engine.output_depth,
                        (unsigned long long)t.engine.output_samples,(unsigned long long)t.playback.generation_accepted_samples,(unsigned long long)t.engine.speech_frames,
                        (unsigned long long)t.engine.modem_blocks,(unsigned long long)t.engine.processing_ns,(unsigned long long)t.engine.queue_wait_ns,
                        (unsigned long long)t.engine.scheduling_delay_ns,(unsigned long long)(process_cpu_ns()-cpu_start));
                    assert(!fflush(trace));++trace_count;last_trace=elapsed;
                }
            }
            usleep(2000);
        }
        assert(!fclose(trace));
        assert(fd_native_audio_finish(a));fd_native_stats s=fd_native_audio_observe(a);
        fd_voice_audio_snapshot snapshot;assert(fd_voice_audio_snapshot_get(retained,&snapshot));
        fprintf(stderr,"CAPTURE turn=%u callbacks=%u max_chunk=%u max_gap_ms=%u pump_gap_ms=%u observe_ms=%u creations=%u max_bytes=%u frag_bytes=%u\n",
            turn,s.capture.callbacks,s.capture.largest_callback_samples,s.capture.longest_callback_gap_ms,
            s.pump_gap_ms,s.device_observe_ms,s.capture.stream_creations,s.capture.granted_maxlength_bytes,s.capture.granted_fragment_bytes);
        fprintf(stderr,"TURN %u native_fail=%u adapter_fail=%u in=%llu out=%llu capture_queue=%u capture_holes=%u stream_failed=%u\n",
            turn,s.failures,snapshot.failed,(unsigned long long)s.input_samples,(unsigned long long)s.output_samples,
            snapshot.capture.queued_samples,snapshot.capture.hole_samples,snapshot.playback.stream_failed);
        fprintf(stderr,"CONTINUITY turn=%u first_callback_ms=%u inter_callback_gap_ms=%u last_callback_ms=%u capture_start_ms=%u\n",
            turn,s.capture.first_callback_delay_ms,s.capture.longest_inter_callback_gap_ms,s.capture.last_callback_ms,s.capture.started_ms);
        char report[1100];snprintf(report,sizeof(report),"%s/result.json",dir);FILE *f=fopen(report,"w");assert(f);
        fprintf(f,"{\"turn\":%u,\"failures\":%u,\"first_audio_fault\":%u,\"fault_generation\":%llu,\"fault_monotonic_ms\":%llu,\"fault_capture_queue\":%u,\"fault_engine_input_depth\":%u,\"native_failure_line\":%u,\"input_samples\":%llu,\"output_samples\":%llu,\"capture_queue\":%u,\"capture_holes\":%u,\"capture_first_callback_ms\":%u,\"capture_inter_gap_ms\":%u,\"capture_granted_maxlength_bytes\":%u,\"capture_creations\":%u,\"engine_input_depth\":%u,\"engine_output_depth\":%u,\"engine_output_samples_total\":%llu,\"processing_ns_total\":%llu,\"queue_wait_ns_total\":%llu,\"scheduling_delay_ns_total\":%llu,\"process_cpu_ns\":%llu,\"elapsed_ms\":%u,\"trace_rows\":%u,\"flush_acknowledged\":%s,\"eoo\":%llu,\"rade_creations\":%u,\"scope\":\"private virtual devices; no RF or listening qualification\"}\n",
            turn,s.failures,s.persistent_fault,(unsigned long long)s.persistent_fault_generation,(unsigned long long)s.persistent_fault_ms,s.fault_capture_queued,s.fault_engine_input_depth,
            s.failure_line,(unsigned long long)s.input_samples,(unsigned long long)s.output_samples,snapshot.capture.queued_samples,snapshot.capture.hole_samples,s.capture.first_callback_delay_ms,s.capture.longest_inter_callback_gap_ms,s.capture.granted_maxlength_bytes,s.capture.stream_creations,
            snapshot.engine.input_depth,snapshot.engine.output_depth,(unsigned long long)snapshot.engine.output_samples,(unsigned long long)snapshot.engine.processing_ns,
            (unsigned long long)snapshot.engine.queue_wait_ns,(unsigned long long)snapshot.engine.scheduling_delay_ns,(unsigned long long)(process_cpu_ns()-cpu_start),
            audio_clock_ms()-start,trace_count,snapshot.playback.flush_acknowledged?"true":"false",(unsigned long long)s.persistent.eoo_count,s.persistent.rade_creations);assert(!fclose(f));
        if(s.failures){fd_native_audio_destroy(a);fd_voice_audio_close(retained);return 1;}
        assert(s.persistent.finished);assert(s.persistent.eoo_count==turn);
        assert(s.capture.stream_creations==1);
        assert(s.capture.granted_maxlength_bytes==88200);
        assert(fd_native_audio_restore(a));fd_native_audio_destroy(a);
        /* Match the physical retest's idle gap, including server idle suspend. */
        if(turn<4)usleep(getenv("FREEDV_PROBE_LONG_IDLE")?20000000:300000);
    }
    fd_voice_audio_close(retained);
    pa_threaded_mainloop_stop(mloop);pa_threaded_mainloop_free(mloop);
    return 0;
}
