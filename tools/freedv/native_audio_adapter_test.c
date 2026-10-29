// SPDX-License-Identifier: LGPL-2.1-or-later
#define _POSIX_C_SOURCE 200809L
#include "src/freedv/native_audio.h"
#include <assert.h>
#include <stdatomic.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>
struct audio_freedv_mixer_snapshot {int saved;};
struct audio_freedv_playback {atomic_bool cancelled,requested;atomic_ullong samples;};
static atomic_bool active, allow_ack;
static atomic_uint restored;
static audio_freedv_playback_t *current;
static uint64_t millis(void){struct timespec t;clock_gettime(CLOCK_MONOTONIC,&t);return(uint64_t)t.tv_sec*1000+t.tv_nsec/1000000;}
static void pause_ms(unsigned ms){struct timespec t={0,(long)ms*1000000};nanosleep(&t,NULL);}
audio_freedv_mixer_snapshot_t *audio_freedv_mixer_snapshot(void){return calloc(1,sizeof(audio_freedv_mixer_snapshot_t));}
bool audio_freedv_mixer_snapshot_describe(const audio_freedv_mixer_snapshot_t *s,char *out,size_t capacity){(void)s;return snprintf(out,capacity,"{\"mock\":true}")>0;}
bool audio_freedv_mixer_restore(audio_freedv_mixer_snapshot_t *s){assert(s);atomic_fetch_add(&restored,1);return true;}
void audio_freedv_mixer_snapshot_free(audio_freedv_mixer_snapshot_t *s){free(s);}
bool audio_freedv_capture_configure(unsigned channel,const char *item){return !channel&&!item;}
bool audio_freedv_capture_begin(void){return !atomic_exchange(&active,true);}
void audio_freedv_capture_end(void){atomic_store(&active,false);}
size_t audio_freedv_capture_read(int16_t *out,size_t capacity){(void)out;(void)capacity;return 0;}
size_t audio_freedv_capture_read_stereo(int16_t *out,size_t frames){(void)out;(void)frames;return 0;}
audio_freedv_capture_stats_t audio_freedv_capture_stats(void){return(audio_freedv_capture_stats_t){0};}
audio_freedv_playback_t *audio_freedv_playback_open(void){assert(!current);current=calloc(1,sizeof(*current));return current;}
void audio_freedv_playback_request_cancel(audio_freedv_playback_t *p){atomic_store(&p->cancelled,true);}
bool audio_freedv_playback_cancel(audio_freedv_playback_t *p){audio_freedv_playback_request_cancel(p);atomic_store(&p->requested,true);return true;}
bool audio_freedv_playback_quiescent(audio_freedv_playback_t *p){return atomic_load(&p->cancelled)&&atomic_load(&p->requested)&&atomic_load(&allow_ack);}
ssize_t audio_freedv_playback_submit(audio_freedv_playback_t *p,const int16_t *samples,size_t count){
    assert(samples);if(atomic_load(&p->cancelled))return -1;
    size_t n=count>137?137:count;atomic_fetch_add(&p->samples,n);return(ssize_t)n;
}
audio_freedv_playback_stats_t audio_freedv_playback_observe(audio_freedv_playback_t *p){
    return(audio_freedv_playback_stats_t){.accepted_samples=atomic_load(&p->samples),.cancelled=atomic_load(&p->cancelled),.flush_acknowledged=audio_freedv_playback_quiescent(p)};
}
void audio_freedv_playback_close(audio_freedv_playback_t *p){assert(p==current);free(p);current=NULL;}
static void cleanup(const char *dir){char p[256];const char *names[]={"capture.wav","output.wav","capture-stereo.wav"};for(unsigned i=0;i<3;++i){snprintf(p,sizeof(p),"%s/%s",dir,names[i]);unlink(p);}rmdir(dir);}
static void wait_stopped(fd_native_audio *a){uint64_t end=millis()+3000;while(!fd_native_audio_stop_requested(a)){assert(millis()<end);pause_ms(2);}}
int main(int argc,char **argv){
#ifndef ENABLE_FREEDV_NATIVE_CODEC
    (void)argc;(void)argv;
#endif
    char dir[]="/tmp/freedv-native-audio-XXXXXX";assert(mkdtemp(dir));
    fd_native_config config={.kind=FD_NATIVE_TONE,.output_directory=dir,.generation=1,.duration_ms=120};
    char error[160];fd_native_audio *a=fd_native_audio_prepare(&config,error,sizeof(error));
    assert(a&&fd_native_audio_observe(a).prepared);
    pause_ms(20);assert(!atomic_load(&current->samples)); /* preparation emits no waveform */
    assert(fd_native_audio_start(a));assert(!fd_native_audio_start(a));
    wait_stopped(a);assert(atomic_load(&current->samples)>0);
    assert(!fd_native_audio_quiescent(a)); /* request is not server acknowledgement */
    atomic_store(&allow_ack,true);
    uint64_t end=millis()+1000;while(!fd_native_audio_quiescent(a)){assert(millis()<end);pause_ms(2);}
    assert(fd_native_audio_finish(a));assert(!atomic_load(&restored)); /* never automatically restore analog mixer */
    assert(fd_native_audio_restore(a));assert(atomic_load(&restored)==1);
    fd_native_stats stats=fd_native_audio_observe(a);assert(stats.finished&&stats.output_samples==stats.playback.accepted_samples);
    fd_native_audio_destroy(a);cleanup(dir);
    config.excerpt_ms=20001;assert(!fd_native_audio_prepare(&config,error,sizeof(error)));
    config.excerpt_ms=121;assert(!fd_native_audio_prepare(&config,error,sizeof(error)));
    config.excerpt_ms=0;
    char inject_dir[]="/tmp/freedv-native-failure-XXXXXX";assert(mkdtemp(inject_dir));
    config.output_directory=inject_dir;config.generation=2;config.duration_ms=500;config.inject_failure_ms=50;
    a=fd_native_audio_prepare(&config,error,sizeof(error));assert(a&&fd_native_audio_start(a));
    wait_stopped(a);assert(fd_native_audio_observe(a).failures);
    (void)fd_native_audio_finish(a);assert(fd_native_audio_restore(a));fd_native_audio_destroy(a);cleanup(inject_dir);
#ifdef ENABLE_FREEDV_NATIVE_CODEC
    assert(argc==2 && fd_native_audio_codec_available());
    char codec_dir[]="/tmp/freedv-native-codec-XXXXXX";assert(mkdtemp(codec_dir));
    config=(fd_native_config){.kind=FD_NATIVE_RADE_FIXTURE,.fixture_path=argv[1],.output_directory=codec_dir,
        .generation=3,.duration_ms=4000,.capture_input=false};
    a=fd_native_audio_prepare(&config,error,sizeof(error));assert(a&&fd_native_audio_observe(a).prepared&&fd_native_audio_start(a));
    uint64_t codec_deadline=millis()+7000;while(!fd_native_audio_stop_requested(a)){assert(millis()<codec_deadline);pause_ms(2);}
    assert(fd_native_audio_finish(a));stats=fd_native_audio_observe(a);
    assert(!stats.failures&&stats.codec.synchronized_blocks>5&&stats.codec.feature_blocks>5&&stats.output_samples>44100);
    printf("PASS: full fixture-to-mocked-playback adapter sync=%llu features=%llu accepted=%llu output=%llu\n",
        (unsigned long long)stats.codec.synchronized_blocks,(unsigned long long)stats.codec.feature_blocks,
        (unsigned long long)stats.playback.accepted_samples,(unsigned long long)stats.output_samples);
    assert(fd_native_audio_restore(a));fd_native_audio_destroy(a);cleanup(codec_dir);
#else
    config.kind=FD_NATIVE_RADE_FIXTURE;assert(!fd_native_audio_codec_available());
    assert(!fd_native_audio_prepare(&config,error,sizeof(error)));
#endif
    puts("PASS: unkeyed prepare silence, partial output accounting, explicit flush acknowledgement, deferred mixer restore and injected worker failure");
}
