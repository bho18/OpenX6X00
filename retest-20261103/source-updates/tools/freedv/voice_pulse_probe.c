/* Isolated device regression: real PulseAudio + production engine/adapter/recorders.
 * Stub only the unrelated DSP consumer. Uses private virtual devices, no RF. */
#include "src/audio.c"
#include "src/freedv/native_audio.h"
#include "src/freedv/voice_audio.h"
#include <assert.h>
#include <stdarg.h>
#include <sys/stat.h>
void dsp_put_audio_samples(size_t n,int16_t *p){(void)n;(void)p;}
void freedv_mode_device_fault(void){fprintf(stderr,"PA context failed\n");abort();}
void _lv_log_add(lv_log_level_t level,const char *file,int line,const char *func,const char *fmt,...){
    (void)level;(void)file;(void)line;(void)func;va_list a;va_start(a,fmt);vfprintf(stderr,fmt,a);va_end(a);fputc('\n',stderr);
}
int main(int argc,char **argv){
    assert(argc==2 && getenv("PULSE_SERVER"));
    assert(!strncmp(getenv("PULSE_SERVER"),"unix:/tmp/freedv-pulse-probe-",27));
    play_device="freedv_probe_sink";
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
        unsigned start=audio_clock_ms();
        while(!fd_native_audio_stop_requested(a)){assert(audio_clock_ms()-start<12000);usleep(2000);}
        assert(fd_native_audio_finish(a));fd_native_stats s=fd_native_audio_observe(a);
        fd_voice_audio_snapshot snapshot;assert(fd_voice_audio_snapshot_get(retained,&snapshot));
        fprintf(stderr,"TURN %u native_fail=%u adapter_fail=%u in=%llu out=%llu capture_queue=%u capture_holes=%u stream_failed=%u\n",
            turn,s.failures,snapshot.failed,(unsigned long long)s.input_samples,(unsigned long long)s.output_samples,
            snapshot.capture.queued_samples,snapshot.capture.hole_samples,snapshot.playback.stream_failed);
        char report[1100];snprintf(report,sizeof(report),"%s/result.json",dir);FILE *f=fopen(report,"w");assert(f);
        fprintf(f,"{\"turn\":%u,\"failures\":%u,\"first_audio_fault\":%u,\"native_failure_line\":%u,\"input_samples\":%llu,\"output_samples\":%llu,\"capture_queue\":%u,\"capture_holes\":%u,\"engine_input_depth\":%u,\"flush_acknowledged\":%s,\"eoo\":%llu,\"rade_creations\":%u,\"scope\":\"private virtual devices; no RF or listening qualification\"}\n",
            turn,s.failures,s.persistent_fault,s.failure_line,(unsigned long long)s.input_samples,(unsigned long long)s.output_samples,
            snapshot.capture.queued_samples,snapshot.capture.hole_samples,snapshot.engine.input_depth,snapshot.playback.flush_acknowledged?"true":"false",
            (unsigned long long)s.persistent.eoo_count,s.persistent.rade_creations);assert(!fclose(f));
        if(s.failures){fd_native_audio_destroy(a);fd_voice_audio_close(retained);return 1;}
        assert(s.persistent.finished);assert(s.persistent.eoo_count==turn);
        assert(fd_native_audio_restore(a));fd_native_audio_destroy(a);usleep(300000);
    }
    fd_voice_audio_close(retained);
    pa_threaded_mainloop_stop(mloop);pa_threaded_mainloop_free(mloop);
    return 0;
}
