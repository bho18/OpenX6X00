// SPDX-License-Identifier: LGPL-2.1-or-later
/* Host-only delivery experiment using the production playback adapter and a
 * private null sink. No radio backend, microphone, or physical sound device. */
#define ENABLE_FREEDV_DIAGNOSTICS 1
#include "src/audio.c"
#include <assert.h>

static uint64_t millis(void) { return diagnostic_audio_clock_ns()/1000000u; }
static void wait_to(uint64_t deadline) {
    while(millis()<deadline){struct timespec t={0,1000000};nanosleep(&t,NULL);}
}
static void context_state(pa_context *c,void *unused) {
    (void)c;(void)unused;pa_threaded_mainloop_signal(mloop,0);
}
void _lv_log_add(lv_log_level_t level,const char *file,int line,const char *function,const char *format,...) {
    (void)level;(void)file;(void)line;(void)function;(void)format;
}
int main(int argc,char **argv) {
    assert(argc==2&&(!strcmp(argv[1],"0")||!strcmp(argv[1],"1")));
    const char *server=getenv("FREEDV_TEST_PULSE_SERVER");
    assert(server&&!strncmp(server,"unix:/tmp/freedv-cadence-",sizeof("unix:/tmp/freedv-cadence-")-1));
    bool lead=argv[1][0]=='1';
    short pcm[20*5292];
    for(unsigned i=0;i<20*5292;++i)pcm[i]=(short)(3276*sin(i*6.283185307179586*735/44100));
    mloop=pa_threaded_mainloop_new();assert(mloop);
    ctx=pa_context_new(pa_threaded_mainloop_get_api(mloop),"FreeDV private cadence probe");assert(ctx);
    pa_context_set_state_callback(ctx,context_state,NULL);
    assert(!pa_threaded_mainloop_start(mloop));pa_threaded_mainloop_lock(mloop);
    assert(!pa_context_connect(ctx,server,PA_CONTEXT_NOFLAGS,NULL));
    while(pa_context_get_state(ctx)!=PA_CONTEXT_READY){assert(PA_CONTEXT_IS_GOOD(pa_context_get_state(ctx)));pa_threaded_mainloop_wait(mloop);}
    pa_threaded_mainloop_unlock(mloop);play_device="freedv_probe";
    audio_freedv_playback_t *p=audio_freedv_playback_open();assert(p);
    uint64_t first=millis()+300;
    if(lead)wait_to(first+240); /* second encoded block prepared before playback */
    for(unsigned frame=0;frame<20;++frame) {
        wait_to(first+(frame+1)*120+(frame%4==1?6:0));
        size_t offset=0;
        while(offset<5292) {
            ssize_t n=audio_freedv_playback_submit(p,pcm+frame*5292+offset,5292-offset);assert(n>=0);
            offset+=(size_t)n;if(!n)wait_to(millis()+1);assert(millis()<first+10000);
        }
    }
    audio_freedv_playback_stats_t st=audio_freedv_playback_observe(p);
    assert(st.accepted_samples==105840&&st.maxlength_bytes<=17640);
    printf("{\"one_frame_lead\":%s,\"frames\":20,\"accepted_samples\":%llu,"
        "\"underflows_before_end\":%llu,\"underflows_before_first_write\":%llu,"
        "\"target_bytes\":%u,\"prebuffer_bytes\":%u,\"maxlength_bytes\":%u}\n",
        lead?"true":"false",(unsigned long long)st.accepted_samples,(unsigned long long)st.underflows,
        (unsigned long long)st.underflows_before_first_write,st.target_bytes,st.prebuffer_bytes,st.maxlength_bytes);
    wait_to(millis()+300);audio_freedv_playback_close(p);
    pa_threaded_mainloop_stop(mloop);pa_context_unref(ctx);pa_threaded_mainloop_free(mloop);
    return 0;
}
