// SPDX-License-Identifier: LGPL-2.1-or-later
#define _POSIX_C_SOURCE 200809L
#include "src/freedv/native_audio.h"
#ifdef ENABLE_FREEDV_VOICE
#include "src/freedv/voice_audio.h"
#endif
#include <assert.h>
#include <stdatomic.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <math.h>
#include <unistd.h>
struct audio_freedv_mixer_snapshot {int saved;};
struct audio_freedv_playback {atomic_bool cancelled,requested,draining;atomic_ullong samples,generation;};
static atomic_bool active, allow_ack;
static atomic_uint restored;
static audio_freedv_playback_t *current;
static unsigned playback_creations;
static bool synthetic_microphone;
static bool inject_capture_error;
static bool retained_capture_open;
static unsigned configured_channel,retained_channels;
static uint64_t capture_epoch, capture_read_samples;
static uint64_t millis(void){struct timespec t;clock_gettime(CLOCK_MONOTONIC,&t);return(uint64_t)t.tv_sec*1000+t.tv_nsec/1000000;}
static void pause_ms(unsigned ms){struct timespec t={0,(long)ms*1000000};nanosleep(&t,NULL);}
audio_freedv_mixer_snapshot_t *audio_freedv_mixer_snapshot(void){return calloc(1,sizeof(audio_freedv_mixer_snapshot_t));}
bool audio_freedv_mixer_snapshot_describe(const audio_freedv_mixer_snapshot_t *s,char *out,size_t capacity){(void)s;return snprintf(out,capacity,"{\"mock\":true}")>0;}
bool audio_freedv_mixer_restore(audio_freedv_mixer_snapshot_t *s){assert(s);atomic_fetch_add(&restored,1);return true;}
void audio_freedv_mixer_snapshot_free(audio_freedv_mixer_snapshot_t *s){free(s);}
bool audio_freedv_capture_configure(unsigned channel,const char *item){
    if(channel>3 || (item && *item) || (retained_capture_open && retained_channels!=(channel?2u:1u)))return false;
    configured_channel=channel;return true;
}
bool audio_freedv_voice_capture_open(void){assert(!retained_capture_open);retained_capture_open=true;retained_channels=configured_channel?2:1;return true;}
void audio_freedv_voice_capture_close(void){retained_capture_open=false;}
bool audio_freedv_capture_begin(void){capture_epoch=millis();capture_read_samples=0;return !atomic_exchange(&active,true);}
void audio_freedv_capture_end(void){atomic_store(&active,false);}
size_t audio_freedv_capture_read(int16_t *out,size_t capacity){
    if(!synthetic_microphone || !atomic_load(&active))return 0;
    uint64_t due=(millis()-capture_epoch)*44100u/1000u;
    size_t n=due>capture_read_samples?(size_t)(due-capture_read_samples):0;
    if(n>capacity)n=capacity;
    for(size_t i=0;i<n;++i)out[i]=(short)(149+2000*sin((capture_read_samples+i)*0.13));
    capture_read_samples+=n;return n;
}
size_t audio_freedv_capture_read_stereo(int16_t *out,size_t frames){(void)out;(void)frames;return 0;}
audio_freedv_capture_stats_t audio_freedv_capture_stats(void){return(audio_freedv_capture_stats_t){.capture_errors=inject_capture_error&&atomic_load(&active)};}
audio_freedv_playback_t *audio_freedv_playback_open(void){assert(!current);++playback_creations;current=calloc(1,sizeof(*current));return current;}
audio_freedv_playback_t *audio_freedv_playback_open_mapped(unsigned channel){(void)channel;assert(!"diagnostic adapter must not claim native mapped output");return NULL;}
bool audio_freedv_playback_select_channel(audio_freedv_playback_t *p,unsigned channel){(void)p;(void)channel;assert(!"diagnostic adapter must retain its measured format");return false;}
#ifdef ENABLE_FREEDV_VOICE
bool audio_freedv_capture_flush_begin(void){assert(!atomic_load(&active));return true;}
int audio_freedv_capture_flushed(void){return 1;}
void audio_freedv_capture_flush_close(void){}
bool audio_freedv_playback_cork(audio_freedv_playback_t *p,bool cork){(void)cork;return p!=NULL;}
bool audio_freedv_playback_rearm(audio_freedv_playback_t *p,uint64_t generation){
    assert(p && generation>atomic_load(&p->generation));atomic_store(&p->generation,generation);
    atomic_store(&p->cancelled,false);atomic_store(&p->requested,false);atomic_store(&p->draining,false);return true;
}
ssize_t audio_freedv_playback_submit_generation(audio_freedv_playback_t *p,uint64_t generation,const int16_t *samples,size_t count){
    assert(generation==atomic_load(&p->generation));return audio_freedv_playback_submit(p,samples,count);
}
bool audio_freedv_playback_drain(audio_freedv_playback_t *p,uint64_t generation){assert(generation==atomic_load(&p->generation));atomic_store(&p->draining,true);return true;}
int audio_freedv_playback_drained(audio_freedv_playback_t *p,uint64_t generation){assert(generation==atomic_load(&p->generation));return atomic_load(&p->draining)?1:0;}
#endif
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
#ifndef ENABLE_FREEDV_VOICE
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
    /* Real TX engine with paced synthetic capture and the existing short-write
     * playback mock. No device, RF, extra capture reader or extra output queue. */
    char tx_dir[]="/tmp/freedv-native-mic-tx-XXXXXX";assert(mkdtemp(tx_dir));
    synthetic_microphone=true;
    config=(fd_native_config){.kind=FD_NATIVE_RADE_MIC_TX,.output_directory=tx_dir,
        .generation=4,.duration_ms=1800};
    a=fd_native_audio_prepare(&config,error,sizeof(error));assert(a&&fd_native_audio_start(a));
    uint64_t tx_deadline=millis()+4000;
    while(!fd_native_audio_stop_requested(a)){assert(millis()<tx_deadline);pause_ms(2);}
    assert(fd_native_audio_finish(a));stats=fd_native_audio_observe(a);
    assert(!stats.failures&&stats.codec.tx_finished&&stats.codec.tx_prepared_blocks>=2);
    assert(stats.tx_prefill_wait_ms>0&&stats.output_samples==stats.playback.accepted_samples);
    assert(stats.input_level_samples>0&&stats.input_ac_rms>1000&&stats.input_peak>1500);
    assert(fd_native_audio_restore(a));fd_native_audio_destroy(a);cleanup(tx_dir);synthetic_microphone=false;
    char modem_dir[]="/tmp/freedv-native-modem-tx-XXXXXX";assert(mkdtemp(modem_dir));
    synthetic_microphone=true;
    config=(fd_native_config){.kind=FD_NATIVE_MODEM_TX,.fixture_path=argv[1],.output_directory=modem_dir,
        .generation=5,.duration_ms=600};
    a=fd_native_audio_prepare(&config,error,sizeof(error));assert(a&&fd_native_audio_start(a));
    wait_stopped(a);assert(fd_native_audio_finish(a));stats=fd_native_audio_observe(a);
    assert(!stats.failures&&stats.output_samples>10000&&!stats.codec.blocks);
    assert(stats.output_samples==stats.playback.accepted_samples);
    assert(fd_native_audio_restore(a));fd_native_audio_destroy(a);cleanup(modem_dir);synthetic_microphone=false;
#endif
#ifdef ENABLE_FREEDV_VOICE
    fd_voice_audio *retained=NULL;unsigned before=playback_creations,tx_turns=0;
    bool stereo_check=argc==2 && !strcmp(argv[1],"--retained-stereo");
    unsigned turns=stereo_check?2:100;
    synthetic_microphone=true;
    for(unsigned turn=0;turn<turns;++turn){
        char persistent_dir[]="/tmp/freedv-persistent-case-XXXXXX";assert(mkdtemp(persistent_dir));
        config=(fd_native_config){.kind=turn<3 || !(turn%2)?FD_NATIVE_PERSISTENT_TX:FD_NATIVE_PERSISTENT_RX,
            .output_directory=persistent_dir,.generation=6+turn,.duration_ms=turn<3?800:80,.persistent_slot=&retained,
            .capture_channel=stereo_check?1:0};
        a=fd_native_audio_prepare(&config,error,sizeof(error));assert(a && fd_native_audio_observe(a).prepared);
        assert(retained && playback_creations==before+1);
        uint64_t accepted_before=atomic_load(&current->samples);
        assert(fd_native_audio_start(a));wait_stopped(a);assert(fd_native_audio_finish(a));
        stats=fd_native_audio_observe(a);
        if(stats.failures || stats.persistent.failures)fprintf(stderr,"persistent turn %u: native failures %u, engine failures %u, in=%llu out=%llu finished=%u eoo=%llu\n",turn,stats.failures,stats.persistent.failures,(unsigned long long)stats.input_samples,(unsigned long long)stats.output_samples,stats.persistent.finished,(unsigned long long)stats.persistent.eoo_count);
        assert(stats.persistent_engine && !stats.failures && !stats.persistent.failures);
        assert(stats.snapshot_generation==config.generation && stats.playback.flush_acknowledged);
        assert(stats.persistent.rade_creations==1 && stats.persistent.encoder_creations==1 && stats.persistent.decoder_creations==1);
        assert(stats.persistent.resampler_creations==4 && stats.persistent.worker_creations==2);
        assert(stats.output_samples==atomic_load(&current->samples)-accepted_before);
        if(config.kind==FD_NATIVE_PERSISTENT_TX){
            assert(stats.persistent.finished && stats.persistent.eoo_count==++tx_turns);
            if(turn<3){assert(stats.input_level_samples>20000 && stats.input_ac_rms>1000);}
        }
        char output[256];snprintf(output,sizeof(output),"%s/output.wav",persistent_dir);
        FILE *wav=fopen(output,"rb");assert(wav);assert(!fseek(wav,0,SEEK_END));
        assert((uint64_t)ftell(wav)==44+stats.output_samples*2);fclose(wav);
        assert(fd_native_audio_restore(a));fd_native_audio_destroy(a);cleanup(persistent_dir);
        assert(configured_channel==(stereo_check?1u:0u));
    }
    char fault_dir[]="/tmp/freedv-persistent-fault-XXXXXX";assert(mkdtemp(fault_dir));
    config=(fd_native_config){.kind=FD_NATIVE_PERSISTENT_TX,.output_directory=fault_dir,
        .generation=106,.duration_ms=800,.persistent_slot=&retained,.capture_channel=stereo_check?1:0};
    a=fd_native_audio_prepare(&config,error,sizeof(error));assert(a&&fd_native_audio_observe(a).prepared);
    inject_capture_error=true;assert(fd_native_audio_start(a));wait_stopped(a);assert(fd_native_audio_finish(a));
    assert(fd_voice_audio_failed(retained));fd_native_audio_destroy(a);cleanup(fault_dir);inject_capture_error=false;
    char retry_dir[]="/tmp/freedv-persistent-retry-XXXXXX";assert(mkdtemp(retry_dir));
    config.output_directory=retry_dir;config.generation=107;
    uint64_t last_generation=fd_voice_audio_observe(retained).generation;
    a=fd_native_audio_prepare(&config,error,sizeof(error));assert(a && !fd_native_audio_observe(a).prepared);
    assert(strstr(error,"latched") && !fd_native_audio_start(a));
    assert(fd_voice_audio_observe(retained).generation==last_generation && !atomic_load(&active));
    assert(fd_native_audio_finish(a));fd_native_audio_destroy(a);cleanup(retry_dir);
    fd_voice_audio_close(retained);assert(!current && !retained_capture_open && !configured_channel);synthetic_microphone=false;
    printf("PASS: %u diagnostic turns use the actual retained production engine/adapter; bounded recording sample accounting, one EOO per TX and retained %s format\n",turns,stereo_check?"stereo":"mono");
#endif
#else
    config.kind=FD_NATIVE_RADE_FIXTURE;assert(!fd_native_audio_codec_available());
    assert(!fd_native_audio_prepare(&config,error,sizeof(error)));
#endif
    puts("PASS: unkeyed prepare silence, partial output accounting, explicit flush acknowledgement, deferred mixer restore and injected worker failure");
}
