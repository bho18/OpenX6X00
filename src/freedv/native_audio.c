// SPDX-License-Identifier: LGPL-2.1-or-later
#define _POSIX_C_SOURCE 200809L
#include "native_audio.h"
#include "capture_dispatch.h"
#include "wav_capture.h"
#include "../board.h"
#ifdef ENABLE_FREEDV_VOICE
#include "voice_audio.h"
#endif
#include <stdatomic.h>
#include <pthread.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>
#include <time.h>
#include <limits.h>
#include <sys/stat.h>
#ifdef ENABLE_FREEDV_NATIVE_CODEC
#include <samplerate.h>
#endif

struct fd_native_audio {
    fd_native_config config;
    pthread_mutex_t mutex;
    fd_native_stats stats;
    char saved_mixer[256], restored_mixer[256];
    atomic_bool stop, running, input_done;
    bool tx_finishing;
    double input_level_sum, input_level_square_sum;
    double level_sum,level_square;
    uint64_t level_seen;
    atomic_uint failures;
    uint64_t epoch_ms;
    pthread_t input_thread, playback_thread;
    bool input_started, playback_started, finished;
    bool capture, stereo;
    audio_freedv_mixer_snapshot_t *mixer;
    audio_freedv_playback_t *playback;
    fd_capture_dispatch dispatcher;
    freedv_capture_ring output_recording;
    freedv_capture_ring persistent_stereo;
    fd_voice_audio *persistent;
    atomic_uint persistent_written,persistent_peak;
    freedv_wav_capture capture_wav, output_wav, stereo_wav;
    FILE *fixture;
#ifdef ENABLE_FREEDV_NATIVE_CODEC
    fd_rade_stream *codec;
    SRC_STATE *fixture_src;
    float fixture_input[80];
    size_t fixture_used, fixture_count;
    bool fixture_eof;
#endif
};
static uint64_t now_ms(void) {
    struct timespec t; clock_gettime(CLOCK_MONOTONIC, &t);
    return (uint64_t)t.tv_sec*1000u+(uint64_t)t.tv_nsec/1000000u;
}
static void pause_ms(unsigned ms) {
    struct timespec t={(time_t)(ms/1000),(long)(ms%1000)*1000000L}; nanosleep(&t,NULL);
}
static bool codec_case(fd_native_kind kind) { return kind>=FD_NATIVE_RADE_FIXTURE&&kind<=FD_NATIVE_RADE_LIVE_RX; }
static void error_text(char *,size_t,const char *);
static bool open_excerpt(fd_native_audio *,freedv_wav_capture *,const char *,
    size_t (*)(void *,int16_t *,size_t),audio_freedv_capture_stats_t (*)(void *),unsigned);
bool fd_native_audio_codec_available(void) {
#ifdef ENABLE_FREEDV_NATIVE_CODEC
    return true;
#else
    return false;
#endif
}
void fd_native_audio_request_stop(fd_native_audio *a) {
    if (!a) return;
    atomic_store_explicit(&a->stop,true,memory_order_release);
    if (a->playback) audio_freedv_playback_request_cancel(a->playback);
#ifdef ENABLE_FREEDV_NATIVE_CODEC
    if (a->codec) fd_stream_abort(a->codec);
#endif
#ifdef ENABLE_FREEDV_VOICE
    if(a->persistent)fd_voice_audio_cancel(a->persistent);
#endif
}
bool fd_native_audio_stop_requested(fd_native_audio *a) { return a && atomic_load_explicit(&a->stop,memory_order_acquire); }
bool fd_native_audio_failed(fd_native_audio *a) { return a && atomic_load_explicit(&a->failures,memory_order_acquire)!=0; }
static void failed_at(fd_native_audio *a,unsigned line) {
    pthread_mutex_lock(&a->mutex);if(!a->stats.failure_line)a->stats.failure_line=line;pthread_mutex_unlock(&a->mutex);
    atomic_fetch_add(&a->failures,1);fd_native_audio_request_stop(a);
}
#define failed(a) failed_at(a,__LINE__)
static void capture_failed(void *context, fd_capture_failure reason) { (void)reason; failed(context); }
static void wav_failed(void *context, freedv_wav_error reason) {
    fd_native_audio *a=context;pthread_mutex_lock(&a->mutex);a->stats.wav_error=reason;pthread_mutex_unlock(&a->mutex);failed(a);
}
static size_t capture_read(void *context,int16_t *samples,size_t count) {
    fd_native_audio *a=context;
    size_t n=fd_capture_dispatch_read(&a->dispatcher,FD_CAPTURE_RECORDING,samples,count);
    return atomic_load_explicit(&a->running,memory_order_acquire)?n:0;
}
static audio_freedv_capture_stats_t capture_stats(void *context) {
    fd_native_audio *a=context;
    if (!atomic_load_explicit(&a->running,memory_order_acquire)) return (audio_freedv_capture_stats_t){0};
    audio_freedv_capture_stats_t s=audio_freedv_capture_stats();
    freedv_capture_stats q=freedv_capture_ring_stats(&a->dispatcher.queues[FD_CAPTURE_RECORDING]);
    s.dropped_samples+=q.dropped_samples;
    if(a->persistent)s.dropped_samples+=freedv_capture_ring_stats(&a->persistent_stereo).dropped_samples;
    return s;
}
static size_t stereo_read(void *context,int16_t *samples,size_t count) {
    fd_native_audio *a=context;
    if(a->persistent)return freedv_capture_ring_pop(&a->persistent_stereo,samples,count);
    size_t frames=audio_freedv_capture_read_stereo(samples,count/2);
    return atomic_load_explicit(&a->running,memory_order_acquire)?frames*2:0;
}
static size_t output_read(void *context,int16_t *samples,size_t count) {
    fd_native_audio *a=context; return freedv_capture_ring_pop(&a->output_recording,samples,count);
}
static audio_freedv_capture_stats_t output_stats(void *context) {
    fd_native_audio *a=context;
    freedv_capture_stats s=freedv_capture_ring_stats(&a->output_recording);
    return (audio_freedv_capture_stats_t){s.queued_samples,s.dropped_samples,s.hole_samples,0};
}
#ifdef ENABLE_FREEDV_VOICE
static void persistent_input(void *context,const int16_t *samples,size_t n) {
    fd_native_audio *a=context;
    freedv_capture_ring_push(&a->dispatcher.queues[FD_CAPTURE_RECORDING],samples,n);
    unsigned peak=atomic_load(&a->persistent_peak);
    for(size_t i=0;i<n;++i){unsigned value=samples[i]<0?-(int)samples[i]:samples[i];if(value>peak)peak=value;}
    atomic_store(&a->persistent_peak,peak);
    /* DC offsets and route-switch impulses are not microphone activity. The
     * first 200 ms remains in capture.wav; steady AC levels exclude it. */
    pthread_mutex_lock(&a->mutex);
    for(size_t i=0;i<n;++i){
        if(a->level_seen++ < AUDIO_CAPTURE_RATE/5)continue;
        double x=samples[i];a->level_sum+=x;a->level_square+=x*x;++a->stats.input_level_samples;
    }
    double count=a->stats.input_level_samples;
    if(count){double mean=a->level_sum/count;double variance=a->level_square/count-mean*mean;
        a->stats.input_ac_rms=sqrt(variance>0?variance:0);}
    pthread_mutex_unlock(&a->mutex);
}
static void persistent_output(void *context,const int16_t *samples,size_t n) {
    fd_native_audio *a=context;freedv_capture_ring_push(&a->output_recording,samples,n);
    atomic_fetch_add(&a->persistent_written,(unsigned)n);
}
static void persistent_stereo(void *context,const int16_t *samples,size_t n) {
    fd_native_audio *a=context;freedv_capture_ring_push(&a->persistent_stereo,samples,n);
}
static void persistent_error(void *context) {failed(context);}
static void persistent_stats(fd_native_audio *a) {
    fd_voice_audio_snapshot snapshot;
    if(!fd_voice_audio_snapshot_get(a->persistent,&snapshot))return;
    pthread_mutex_lock(&a->mutex);
    a->stats.persistent_engine=true;a->stats.persistent=fd_voice_audio_observe(a->persistent);
    a->stats.snapshot_generation=snapshot.generation;
    a->stats.persistent_fault=snapshot.first_fault;
    a->stats.pump_gap_ms=snapshot.pump_gap_ms;a->stats.device_observe_ms=snapshot.device_observe_ms;
    a->stats.fault_capture_queued=snapshot.fault_capture_queued;a->stats.fault_engine_input_depth=snapshot.fault_engine_input_depth;
    a->stats.persistent_fault_generation=snapshot.fault_generation;a->stats.persistent_fault_ms=snapshot.fault_ms;
    a->stats.capture=snapshot.capture;a->stats.playback=snapshot.playback;
    a->stats.input_samples=a->stats.persistent.input_samples;a->stats.output_samples=atomic_load(&a->persistent_written);
    a->stats.input_peak=atomic_load(&a->persistent_peak);
    a->stats.elapsed_ms=now_ms()-a->epoch_ms;
    pthread_mutex_unlock(&a->mutex);
}
static void *persistent_worker(void *context) {
    fd_native_audio *a=context;
    while(!atomic_load(&a->running) && !fd_native_audio_stop_requested(a))pause_ms(2);
    if(!fd_native_audio_stop_requested(a) &&
       !fd_voice_audio_begin(a->persistent,a->config.kind==FD_NATIVE_PERSISTENT_TX?FD_VOICE_TX:FD_VOICE_RX,a->config.generation))failed(a);
    uint64_t drain_deadline=0;
    while(!fd_native_audio_stop_requested(a)) {
        persistent_stats(a);
        if(fd_voice_audio_failed(a->persistent)){failed(a);break;}
        if(a->tx_finishing){
            int drained=fd_voice_audio_drained(a->persistent);
            if(drained<0 || now_ms()>=drain_deadline){failed(a);break;}
            if(drained==1){persistent_stats(a);fd_native_audio_request_stop(a);break;}
        }else if(now_ms()-a->epoch_ms>=a->config.duration_ms){
            if(a->config.kind==FD_NATIVE_PERSISTENT_RX){fd_native_audio_request_stop(a);break;}
            if(!fd_voice_audio_finish(a->persistent,a->config.generation)){failed(a);break;}
            a->tx_finishing=true;drain_deadline=now_ms()+1500;
        }
        pause_ms(2);
    }
    fd_voice_audio_cancel(a->persistent);return NULL;
}
static bool prepare_persistent(fd_native_audio *a,char *error,size_t size) {
    if(!a->config.persistent_slot){error_text(error,size,"Persistent session storage is required");return false;}
    if(!*a->config.persistent_slot){
        fd_qualified_profile candidate={.sample_rate=AUDIO_CAPTURE_RATE,.capture_device=BOARD_PA_SOURCE,
            .playback_device=BOARD_PA_SINK,.capture_channel=a->config.capture_channel,
            .mixer=a->config.mixer_item?a->config.mixer_item:""};
#if defined(FREEDV_HOST_TEST_PROFILE) || defined(HOST_BUILD)
        candidate.capture_device="@default-source@";candidate.playback_device="@default-sink@";candidate.mixer="host-default";
#endif
        *a->config.persistent_slot=fd_voice_audio_open_diagnostic(&candidate);
    }
    a->persistent=*a->config.persistent_slot;
    if(a->persistent && fd_voice_audio_failed(a->persistent)){
        error_text(error,size,"Audio fault is latched. Export, Close, then Acquire before another run.");return false;
    }
    if(!a->persistent || !fd_voice_audio_fence(a->persistent) ||
       !audio_freedv_capture_configure(a->config.capture_channel,a->config.mixer_item)){
        error_text(error,size,"Cannot prepare retained production audio adapter");return false;
    }
    a->dispatcher.enabled[FD_CAPTURE_RECORDING]=true;
    fd_voice_audio_taps taps={persistent_input,persistent_output,persistent_stereo,a};
    if(!fd_voice_audio_set_taps(a->persistent,&taps))return false;
    fd_voice_audio_on_error(a->persistent,persistent_error,a);
    if(!open_excerpt(a,&a->capture_wav,"capture.wav",capture_read,capture_stats,1) ||
       !open_excerpt(a,&a->output_wav,"output.wav",output_read,output_stats,1) ||
       (a->stereo && !open_excerpt(a,&a->stereo_wav,"capture-stereo.wav",stereo_read,capture_stats,2))){
        error_text(error,size,"Cannot open bounded persistent-case recordings");return false;
    }
    if(pthread_create(&a->input_thread,NULL,persistent_worker,a))return false;
    a->input_started=true;a->stats.persistent_engine=true;a->stats.prepared=true;
    return true;
}
#endif
static bool open_excerpt(fd_native_audio *a,freedv_wav_capture *writer,const char *name,
                         size_t (*read)(void *,int16_t *,size_t),
                         audio_freedv_capture_stats_t (*stats)(void *),unsigned channels) {
    char path[PATH_MAX];
    int n=snprintf(path,sizeof(path),"%s/%s",a->config.output_directory,name);
    if (n<0 || (size_t)n>=sizeof(path)) return false;
    freedv_wav_source source={.read=read,.stats=stats,.context=a,.channels=channels};
    unsigned ms=a->config.excerpt_ms?a->config.excerpt_ms:4000u;
    writer->max_samples=(uint64_t)ms*AUDIO_CAPTURE_RATE*channels/1000u;
    return freedv_wav_capture_start_source(writer,path,wav_failed,a,&source);
}
#ifdef ENABLE_FREEDV_NATIVE_CODEC
/* A bounded, stateful fixture feeder. Files are signed16LE mono at8kHz. */
static size_t fixture_read(fd_native_audio *a,int16_t *out,size_t wanted) {
    float converted[441]; size_t produced=0;
    while(produced<wanted && !fd_native_audio_stop_requested(a)) {
        if(a->fixture_used==a->fixture_count && !a->fixture_eof) {
            unsigned char bytes[160]; size_t n=fread(bytes,1,sizeof(bytes),a->fixture);
            if(ferror(a->fixture)||n%2) {failed(a);break;}
            a->fixture_count=n/2; a->fixture_used=0;
            if(!n) a->fixture_eof=true;
            for(size_t i=0;i<n/2;++i) a->fixture_input[i]=(int16_t)((unsigned)bytes[2*i]|((unsigned)bytes[2*i+1]<<8))/32768.f;
        }
        SRC_DATA d={.data_in=a->fixture_input+a->fixture_used,
            .input_frames=(long)(a->fixture_count-a->fixture_used),.data_out=converted+produced,
            .output_frames=(long)(wanted-produced),.src_ratio=44100./8000.,.end_of_input=a->fixture_eof};
        if(src_process(a->fixture_src,&d)) {failed(a);break;}
        a->fixture_used+=(size_t)d.input_frames_used; produced+=(size_t)d.output_frames_gen;
        if(!d.input_frames_used && !d.output_frames_gen) {
            if(a->fixture_eof)break;
            failed(a);break;
        }
    }
    src_float_to_short_array(converted,out,(int)produced);
    return produced;
}
#endif
static void cache_stats(fd_native_audio *a) {
    audio_freedv_capture_stats_t capture={0};
    audio_freedv_playback_stats_t playback={0};
    fd_stream_stats codec={0};
    if(a->capture) capture=audio_freedv_capture_stats();
    if(a->playback) playback=audio_freedv_playback_observe(a->playback);
#ifdef ENABLE_FREEDV_NATIVE_CODEC
    if(a->codec) codec=fd_stream_observe(a->codec);
#endif
    pthread_mutex_lock(&a->mutex);
    a->stats.capture=capture; a->stats.playback=playback; a->stats.codec=codec;
    a->stats.aborted_codec_output_samples=fd_native_audio_stop_requested(a)&&codec.output_samples>codec.delivered_samples?codec.output_samples-codec.delivered_samples:0;
    a->stats.elapsed_ms=atomic_load(&a->running)?now_ms()-a->epoch_ms:0;
    pthread_mutex_unlock(&a->mutex);
    if(!fd_native_audio_stop_requested(a) && (capture.dropped_samples || capture.hole_samples || capture.capture_errors || codec.failures || playback.write_errors))
        failed(a);
}
static void *input_worker(void *context) {
    fd_native_audio *a=context;
    int16_t samples[441]; uint64_t submitted=0,last_capture=0;
    while(!fd_native_audio_stop_requested(a)) {
        bool running=atomic_load_explicit(&a->running,memory_order_acquire);
        if(!running) {
            if(a->capture) (void)fd_capture_dispatch_read(&a->dispatcher,FD_CAPTURE_CODEC,samples,441);
            pause_ms(2);continue;
        }
        if(a->config.inject_failure_ms && now_ms()-a->epoch_ms>=a->config.inject_failure_ms){failed(a);break;}
        if(now_ms()-a->epoch_ms>=a->config.duration_ms) {fd_native_audio_request_stop(a);break;}
#ifdef ENABLE_FREEDV_NATIVE_CODEC
        if(a->config.kind==FD_NATIVE_RADE_MIC_TX && a->config.duration_ms>1000 &&
           now_ms()-a->epoch_ms>=a->config.duration_ms-1000) {
            if(!a->tx_finishing){a->tx_finishing=true;if(!fd_stream_finish_tx(a->codec))failed(a);}
            (void)fd_capture_dispatch_read(&a->dispatcher,FD_CAPTURE_CODEC,samples,441);
            pause_ms(2);continue;
        }
#endif
        size_t n=0;uint64_t interrupted=0;
        if(a->capture && a->config.kind!=FD_NATIVE_RADE_FIXTURE) {
            n=fd_capture_dispatch_read(&a->dispatcher,FD_CAPTURE_CODEC,samples,441);
            if(n)last_capture=now_ms();
            else if(now_ms()-a->epoch_ms>500 && (!last_capture || now_ms()-last_capture>500)){failed(a);break;}
        }
#ifdef ENABLE_FREEDV_NATIVE_CODEC
        if(a->config.kind==FD_NATIVE_RADE_FIXTURE) {
            uint64_t release=a->epoch_ms+submitted*1000u/AUDIO_CAPTURE_RATE;
            if(now_ms()<release){pause_ms(1);continue;}
            n=fixture_read(a,samples,441);
            if(!n){fd_native_audio_request_stop(a);break;}
            if(a->config.interrupt_fixture)for(size_t i=0;i<n;++i)
                if(((submitted+i)/AUDIO_CAPTURE_RATE)%12>=10){samples[i]=0;++interrupted;}
        }
        if(a->codec && n && fd_stream_submit(a->codec,a->config.generation,samples,n)!=n){if(!fd_native_audio_stop_requested(a))failed(a);break;}
#endif
        if(a->capture && a->config.kind==FD_NATIVE_RADE_FIXTURE)
            (void)fd_capture_dispatch_read(&a->dispatcher,FD_CAPTURE_CODEC,samples,441);
        submitted+=n;
        /* Input activity, not microphone identity. Exclude initial switching
         * transients and remove DC in the reported AC level. No gain changes. */
        double sum=0,squares=0;unsigned peak=0;
        bool level=n&&a->capture&&a->config.kind!=FD_NATIVE_RADE_FIXTURE&&now_ms()-a->epoch_ms>=200;
        if(level)for(size_t i=0;i<n;++i) {
            int v=samples[i];unsigned magnitude=(unsigned)(v<0?-v:v);
            sum+=v;squares+=(double)v*v;if(magnitude>peak)peak=magnitude;
        }
        pthread_mutex_lock(&a->mutex); a->stats.input_samples+=n;a->stats.interrupted_input_samples+=interrupted;
        if(level) {
            a->input_level_sum+=sum;a->input_level_square_sum+=squares;a->stats.input_level_samples+=n;
            double mean=a->input_level_sum/a->stats.input_level_samples;
            double variance=a->input_level_square_sum/a->stats.input_level_samples-mean*mean;
            a->stats.input_ac_rms=sqrt(fmax(0,variance));
            if(peak>a->stats.input_peak)a->stats.input_peak=peak;
        }
        pthread_mutex_unlock(&a->mutex);
        if(!n)pause_ms(2);
    }
    if(a->capture)fd_capture_dispatch_cancel(&a->dispatcher);
    atomic_store(&a->input_done,true);
    return NULL;
}
static void *playback_worker(void *context) {
    fd_native_audio *a=context;
    int16_t pending[441]; size_t have=0,offset=0;
    uint64_t generated=0,last_stats=0;
#ifdef ENABLE_FREEDV_NATIVE_CODEC
    uint64_t tx_drained_at=0;
    bool tx_prefill=a->config.kind==FD_NATIVE_RADE_MIC_TX;
#endif
    bool prebuffer=a->config.kind==FD_NATIVE_RADE_FIXTURE || a->config.kind==FD_NATIVE_RADE_LIVE_RX;
    while(!fd_native_audio_stop_requested(a)) {
        if(!atomic_load_explicit(&a->running,memory_order_acquire)){pause_ms(2);continue;}
        if(now_ms()-a->epoch_ms>=a->config.duration_ms){fd_native_audio_request_stop(a);break;}
        if(now_ms()-last_stats>=100){cache_stats(a);last_stats=now_ms();}
        if(!a->playback){pause_ms(2);continue;}
        if(offset==have) {
            have=offset=0;
            if(a->config.kind==FD_NATIVE_TONE) {
                uint64_t release=a->epoch_ms+generated*1000u/AUDIO_PLAY_RATE;
                if(now_ms()<release){pause_ms(1);continue;}
                have=441;
                for(size_t i=0;i<have;++i)pending[i]=(int16_t)(3276.*sin((generated+i)*6.283185307179586*735./AUDIO_PLAY_RATE));
                generated+=have;
            }
#ifdef ENABLE_FREEDV_NATIVE_CODEC
            else if(a->config.kind==FD_NATIVE_MODEM_TX) {
                uint64_t release=a->epoch_ms+generated*1000u/AUDIO_PLAY_RATE;
                if(now_ms()<release){pause_ms(1);continue;}
                have=fixture_read(a,pending,441);generated+=have;
                if(!have){fd_native_audio_request_stop(a);break;}
            }
            else if(a->codec) {
                if(tx_prefill) {
                    if(!fd_stream_tx_prefill_ready(a->codec)){pause_ms(2);continue;}
                    tx_prefill=false;
                    pthread_mutex_lock(&a->mutex);a->stats.tx_prefill_wait_ms=now_ms()-a->epoch_ms;pthread_mutex_unlock(&a->mutex);
                }
                if(prebuffer) {
                    if(fd_stream_output_pending(a->codec)<10584){pause_ms(2);continue;}
                    prebuffer=false;
                }
                have=fd_stream_receive(a->codec,a->config.generation,pending,441);
            }
#endif
            if(!have){
#ifdef ENABLE_FREEDV_NATIVE_CODEC
                if(a->config.kind==FD_NATIVE_RADE_MIC_TX && fd_stream_tx_finished(a->codec)) {
                    if(!tx_drained_at)tx_drained_at=now_ms();
                    /* PA has a finite200ms queue; keep RF route until the tail
                     * has been delivered, subject to the outer hard deadline. */
                    if(now_ms()-tx_drained_at>=200)fd_native_audio_request_stop(a);
                }
#endif
                pause_ms(2);continue;
            }
        }
        ssize_t accepted=audio_freedv_playback_submit(a->playback,pending+offset,have-offset);
        if(accepted<0){if(!fd_native_audio_stop_requested(a))failed(a);break;}
        if(!accepted){pause_ms(1);continue;}
        unsigned recording_depth=freedv_capture_ring_stats(&a->output_recording).queued_samples;
        if(recording_depth==UINT_MAX || recording_depth+(size_t)accepted>AUDIO_PLAY_RATE ||
           freedv_capture_ring_push(&a->output_recording,pending+offset,(size_t)accepted)!=(size_t)accepted){failed(a);break;}
        offset+=(size_t)accepted;
        pthread_mutex_lock(&a->mutex);a->stats.output_samples+=(uint64_t)accepted;pthread_mutex_unlock(&a->mutex);
    }
    pthread_mutex_lock(&a->mutex);a->stats.aborted_playback_samples=have-offset;pthread_mutex_unlock(&a->mutex);
    if(a->playback) {
        (void)audio_freedv_playback_cancel(a->playback);
        uint64_t deadline=now_ms()+2000;
        while(!audio_freedv_playback_quiescent(a->playback) && now_ms()<deadline)pause_ms(2);
        if(!audio_freedv_playback_quiescent(a->playback)) atomic_fetch_add(&a->failures,1);
    }
    cache_stats(a);
    return NULL;
}
static void error_text(char *error,size_t size,const char *text){if(error&&size)snprintf(error,size,"%s",text);}
fd_native_audio *fd_native_audio_prepare(const fd_native_config *config,char *error,size_t size) {
    if(!config || !config->generation || !config->duration_ms || config->duration_ms>120000 ||
       config->capture_channel>3 || config->inject_failure_ms>config->duration_ms || !config->output_directory ||
       config->excerpt_ms>20000 || config->excerpt_ms>config->duration_ms+(config->kind==FD_NATIVE_PERSISTENT_TX?1500:0) ||
       config->kind<FD_NATIVE_CAPTURE || config->kind>FD_NATIVE_PERSISTENT_RX ||
       (config->interrupt_fixture && config->kind!=FD_NATIVE_RADE_FIXTURE)) {
        error_text(error,size,"Invalid diagnostic audio case");return NULL;
    }
    if((codec_case(config->kind)||config->kind==FD_NATIVE_MODEM_TX)&&!fd_native_audio_codec_available()) {
        error_text(error,size,"This build has no native RADE engine");return NULL;
    }
    fd_native_audio *a=calloc(1,sizeof(*a));if(!a){error_text(error,size,"Audio allocation failed");return NULL;}
    a->config=*config;
    if(pthread_mutex_init(&a->mutex,NULL)){free(a);return NULL;}
    atomic_init(&a->stop,false);atomic_init(&a->running,false);atomic_init(&a->input_done,false);atomic_init(&a->failures,0);
    atomic_init(&a->persistent_written,0);atomic_init(&a->persistent_peak,0);
    fd_capture_dispatch_init(&a->dispatcher);freedv_capture_ring_init(&a->output_recording);freedv_capture_ring_init(&a->persistent_stereo);
    freedv_wav_capture_init(&a->capture_wav);freedv_wav_capture_init(&a->output_wav);freedv_wav_capture_init(&a->stereo_wav);
    a->stats.kind=config->kind;a->stats.generation=config->generation;a->stats.native_codec_available=fd_native_audio_codec_available();
    a->stats.recording_excerpt_limit_ms=config->excerpt_ms?config->excerpt_ms:4000;
    a->capture=config->kind!=FD_NATIVE_RADE_FIXTURE || config->capture_input;
    a->stereo=a->capture && config->capture_channel!=0;
    strcpy(a->saved_mixer,"null");strcpy(a->restored_mixer,"null");
    a->mixer=audio_freedv_mixer_snapshot();
    if(!a->mixer){error_text(error,size,"Cannot snapshot audio mixer");failed(a);return a;}
    if(!audio_freedv_mixer_snapshot_describe(a->mixer,a->saved_mixer,sizeof(a->saved_mixer))){error_text(error,size,"Cannot describe audio mixer");failed(a);return a;}
    if(config->kind==FD_NATIVE_PERSISTENT_TX || config->kind==FD_NATIVE_PERSISTENT_RX){
#ifdef ENABLE_FREEDV_VOICE
        if(!prepare_persistent(a,error,size))failed(a);
#else
        error_text(error,size,"This build has no persistent voice adapter");failed(a);
#endif
        return a;
    }
#ifdef ENABLE_FREEDV_NATIVE_CODEC
    if(codec_case(config->kind)) {
        a->codec=fd_stream_prepare(config->kind==FD_NATIVE_RADE_MIC_TX?FD_STREAM_TX:FD_STREAM_RX,config->generation);
        if(!a->codec){error_text(error,size,"Cannot prepare RADE engine");failed(a);return a;}
    }
    if(config->kind==FD_NATIVE_RADE_FIXTURE||config->kind==FD_NATIVE_MODEM_TX) {
        int err;
        a->fixture=config->fixture_path?fopen(config->fixture_path,"rb"):NULL;
        a->fixture_src=src_new(SRC_SINC_FASTEST,1,&err);
        if(!a->fixture || !a->fixture_src){error_text(error,size,"Cannot open RADE fixture");failed(a);return a;}
    }
#endif
    if(config->kind!=FD_NATIVE_CAPTURE) {
        a->playback=audio_freedv_playback_open();
        if(!a->playback){error_text(error,size,"Cannot prepare playback");failed(a);return a;}
        if(!open_excerpt(a,&a->output_wav,"output.wav",output_read,output_stats,1)){error_text(error,size,"Cannot open output excerpt");failed(a);return a;}
    }
    if(a->capture) {
        if(!audio_freedv_capture_configure(config->capture_channel,config->mixer_item)){error_text(error,size,"Unsupported capture or mixer option");failed(a);return a;}
        if(!open_excerpt(a,&a->capture_wav,"capture.wav",capture_read,capture_stats,1) ||
           (a->stereo && !open_excerpt(a,&a->stereo_wav,"capture-stereo.wav",stereo_read,capture_stats,2))){error_text(error,size,"Cannot open capture excerpts");failed(a);return a;}
        if(!fd_capture_dispatch_start(&a->dispatcher,true,true,capture_failed,a)){error_text(error,size,"Cannot start capture dispatcher");failed(a);return a;}
    }
#ifdef ENABLE_FREEDV_NATIVE_CODEC
    if(a->codec && !fd_stream_start(a->codec)){error_text(error,size,"Cannot start codec workers");failed(a);return a;}
#endif
    if(pthread_create(&a->input_thread,NULL,input_worker,a)){error_text(error,size,"Cannot start audio input worker");failed(a);return a;}
    a->input_started=true;
    if(pthread_create(&a->playback_thread,NULL,playback_worker,a)){error_text(error,size,"Cannot start playback worker");failed(a);return a;}
    a->playback_started=true;
    pthread_mutex_lock(&a->mutex);a->stats.prepared=true;pthread_mutex_unlock(&a->mutex);
    error_text(error,size,"");return a;
}
bool fd_native_audio_start(fd_native_audio *a) {
    if(!a||!a->stats.prepared||atomic_load(&a->running)||fd_native_audio_stop_requested(a))return false;
    a->epoch_ms=now_ms();
    pthread_mutex_lock(&a->mutex);a->stats.started=true;pthread_mutex_unlock(&a->mutex);
    atomic_store_explicit(&a->running,true,memory_order_release);return true;
}
bool fd_native_audio_quiescent(fd_native_audio *a) {
    return a && fd_native_audio_stop_requested(a) && (!a->playback || audio_freedv_playback_quiescent(a->playback));
}
fd_native_stats fd_native_audio_observe(fd_native_audio *a) {
    if(!a)return(fd_native_stats){0};
    pthread_mutex_lock(&a->mutex);fd_native_stats out=a->stats;pthread_mutex_unlock(&a->mutex);
    out.stop_requested=fd_native_audio_stop_requested(a);out.quiescent=fd_native_audio_quiescent(a);out.failures=atomic_load(&a->failures);return out;
}
bool fd_native_audio_finish(fd_native_audio *a) {
    if(!a)return false;
    if(a->finished)return true;
    fd_native_audio_request_stop(a);
    if(a->input_started){if(pthread_join(a->input_thread,NULL)){failed(a);return false;}a->input_started=false;}
#ifdef ENABLE_FREEDV_VOICE
    if(a->persistent){
        if(!fd_voice_audio_fence(a->persistent))return false;
        persistent_stats(a);
        fd_voice_audio_on_error(a->persistent,NULL,NULL);
        (void)fd_voice_audio_set_taps(a->persistent,NULL);
    }
#endif
    if(a->playback_started){if(pthread_join(a->playback_thread,NULL)){failed(a);return false;}a->playback_started=false;}
    else if(a->playback)(void)audio_freedv_playback_cancel(a->playback);
#ifdef ENABLE_FREEDV_NATIVE_CODEC
    if(a->codec && !fd_stream_join(a->codec)){failed(a);return false;}
#endif
    if(a->dispatcher.started && !fd_capture_dispatch_join(&a->dispatcher)){atomic_fetch_add(&a->failures,1);return false;}
    freedv_wav_capture *writers[]={&a->capture_wav,&a->output_wav,&a->stereo_wav};
    uint64_t recorded=0,discarded=0;
    for(unsigned i=0;i<3;++i)if(writers[i]->started){
        freedv_wav_error result=freedv_wav_capture_stop(writers[i]);
        if(result!=FREEDV_WAV_OK)atomic_fetch_add(&a->failures,1);
        if(result==FREEDV_WAV_JOIN_ERROR)return false;
        recorded+=writers[i]->samples;discarded+=writers[i]->discarded_samples;
    }
    if(!a->persistent)cache_stats(a);
    pthread_mutex_lock(&a->mutex);a->stats.recorder_samples=recorded;a->stats.recorder_discarded_samples=discarded;a->stats.finished=true;pthread_mutex_unlock(&a->mutex);
    a->finished=true;return true;
}
bool fd_native_audio_restore(fd_native_audio *a) {
    if(!a||!a->finished||!fd_native_audio_quiescent(a))return false;
    /* A retained adapter owns its mono/stereo stream format until session
     * close. Restoring the mixer after a case must not silently request a
     * stereo-to-mono stream change (or turn the correct format guard into a
     * false restoration failure). Nonpersistent diagnostics can return to the
     * application's ordinary mono capture here. */
    unsigned channel=a->persistent?a->config.capture_channel:0;
    bool ok=!a->mixer || (audio_freedv_capture_configure(channel,NULL) && audio_freedv_mixer_restore(a->mixer));
    char restored[256]="null";
    if(a->mixer){
        audio_freedv_mixer_snapshot_t *got=audio_freedv_mixer_snapshot();
        bool observed=got&&audio_freedv_mixer_snapshot_describe(got,restored,sizeof(restored));
        ok=ok&&observed;
        if(got)audio_freedv_mixer_snapshot_free(got);
    }
    pthread_mutex_lock(&a->mutex);memcpy(a->restored_mixer,restored,sizeof(restored));a->stats.mixer_restored=ok;pthread_mutex_unlock(&a->mutex);return ok;
}
bool fd_native_audio_mixer_describe(fd_native_audio *a,bool after,char *out,size_t capacity){
    if(!a||!out||!capacity)return false;
    pthread_mutex_lock(&a->mutex);const char *source=after?a->restored_mixer:a->saved_mixer;
    size_t n=strlen(source);bool ok=n<capacity;if(ok)memcpy(out,source,n+1);else out[0]=0;
    pthread_mutex_unlock(&a->mutex);return ok;
}
void fd_native_audio_destroy(fd_native_audio *a) {
    if(!a)return;
    if(!a->finished){(void)fd_native_audio_finish(a);if(!a->finished)return;}
    if(a->playback)audio_freedv_playback_close(a->playback);
#ifdef ENABLE_FREEDV_NATIVE_CODEC
    if(a->codec)fd_stream_destroy(a->codec);
    if(a->fixture_src)src_delete(a->fixture_src);
#endif
    if(a->fixture)fclose(a->fixture);
    if(a->mixer)audio_freedv_mixer_snapshot_free(a->mixer);
    pthread_mutex_destroy(&a->mutex);free(a);
}
