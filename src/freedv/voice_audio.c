// SPDX-License-Identifier: LGPL-2.1-or-later
#define _POSIX_C_SOURCE 200809L
#include "voice_audio.h"
#include "../audio.h"
#include "../board.h"
#include <pthread.h>
#include <stdatomic.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <limits.h>
#include <stdio.h>
struct fd_voice_audio {
    pthread_mutex_t lock;
    pthread_cond_t changed;
    pthread_t pump;
    bool started, shutdown, active, busy, finishing, finish_sent, drain_sent;
    uint64_t generation;
    unsigned rate;
    fd_voice_direction direction;
    atomic_bool cancelled, failed;
    atomic_uint microphone_peak;
    atomic_uint cancel_epoch;
    fd_voice_engine *engine;
    audio_freedv_playback_t *playback;
    int16_t pending[480];
    size_t count, offset;
    fd_voice_audio_snapshot snapshot;
    uint64_t previous_tx_generation, previous_tx_input_samples, previous_tx_first_write_ns, previous_tx_underflows;
    bool previous_tx_finished;
    void (*on_error)(void *);
    void *error_context;
    fd_voice_audio_taps taps;
};
static uint64_t now(void) {struct timespec t;clock_gettime(CLOCK_MONOTONIC,&t);return (uint64_t)t.tv_sec*1000+t.tv_nsec/1000000;}
static void pause_ms(void) {struct timespec t={0,2000000};nanosleep(&t,NULL);}
void fd_voice_audio_cancel(fd_voice_audio *a) {
    if(!a)return;
    atomic_fetch_add(&a->cancel_epoch,1);
    atomic_store(&a->cancelled,true);
    fd_voice_cancel(a->engine);
    audio_freedv_playback_request_cancel(a->playback);
}
static void failure_at(fd_voice_audio *a,unsigned reason) {
    if(!atomic_exchange(&a->failed,true)){
        fprintf(stderr,"FreeDV audio fault %u, generation %llu\n",reason,(unsigned long long)a->generation);
        if(a->on_error)a->on_error(a->error_context);
    }
    fd_voice_audio_cancel(a);
}
#define failure(a) failure_at(a,__LINE__)
static void *pump(void *arg) {
    fd_voice_audio *a=arg;pthread_mutex_lock(&a->lock);
    while(!a->shutdown) {
        while(!a->shutdown && (!a->active || atomic_load(&a->cancelled)))pthread_cond_wait(&a->changed,&a->lock);
        if(a->shutdown)break;
        a->busy=true;
        /* The lock is the admission fence for finish/fence. No radio lock is
         * taken here, and cancel is independent of this lock and PA progress. */
        int16_t input[480], stereo[960];
        size_t n=audio_freedv_capture_read(input,a->rate/100);
        unsigned peak=0;
        for(size_t i=0;i<n;++i){unsigned level=(unsigned)(input[i]<0?-(int)input[i]:input[i]);if(level>peak)peak=level;}
        atomic_store(&a->microphone_peak,a->direction==FD_VOICE_TX?peak:0);
        size_t stereo_frames=audio_freedv_capture_read_stereo(stereo,a->rate/100);
        if(stereo_frames && a->taps.stereo)a->taps.stereo(a->taps.context,stereo,stereo_frames*2);
        audio_freedv_capture_stats_t capture=audio_freedv_capture_stats();
        fd_voice_stats queued=fd_voice_observe(a->engine);
        if(capture.capture_errors || capture.dropped_samples || capture.hole_samples ||
            (capture.queued_samples!=UINT_MAX && (uint64_t)capture.queued_samples+queued.input_depth>a->rate/2))failure(a);
        if(atomic_load(&a->cancelled))goto telemetry;
        size_t submitted=n?fd_voice_submit(a->engine,a->generation,input,n):0;
        if(submitted && a->taps.input)a->taps.input(a->taps.context,input,submitted);
        if(submitted!=n && !atomic_load(&a->cancelled)){
            fd_voice_stats failed=fd_voice_observe(a->engine);
            fprintf(stderr,"FreeDV rejected %zu input samples; generation %llu engine %llu, depth %u, failures %u, cancelled %u\n",n,
                (unsigned long long)a->generation,(unsigned long long)failed.generation,failed.input_depth,failed.failures,failed.cancelled);
            failure(a);
        }
        if(atomic_load(&a->cancelled))goto telemetry;
        if(a->finishing && !n && !a->finish_sent) {
            if(!fd_voice_finish(a->engine,a->generation))failure(a);
            a->finish_sent=true;
        }
        if(a->offset==a->count) {
            a->count=fd_voice_receive(a->engine,a->generation,a->pending,a->rate/100);a->offset=0;
        }
        if(a->offset<a->count) {
            ssize_t accepted=audio_freedv_playback_submit_generation(a->playback,a->generation,a->pending+a->offset,a->count-a->offset);
            if(accepted<0 && !atomic_load(&a->cancelled))failure(a);
            else if(accepted>0){
                if(a->taps.output)a->taps.output(a->taps.context,a->pending+a->offset,(size_t)accepted);
                a->offset+=(size_t)accepted;
            }
        }
telemetry:;
        fd_voice_stats engine=fd_voice_observe(a->engine);
        if(engine.failures)failure(a);
        if(!atomic_load(&a->cancelled) && a->finish_sent && engine.finished && !engine.output_depth && a->offset==a->count && !a->drain_sent) {
            if(!audio_freedv_playback_drain(a->playback,a->generation))failure(a);
            a->drain_sent=true;
        }
        uint64_t sampled=now();
        if(atomic_load(&a->failed) || a->snapshot.generation!=a->generation || sampled-a->snapshot.sampled_ms>=100){
            a->snapshot=(fd_voice_audio_snapshot){.sampled_ms=sampled,.generation=a->generation,
                .cancelled=atomic_load(&a->cancelled),.failed=atomic_load(&a->failed),.finishing=a->finishing,
                .engine=engine,.capture=capture,.playback=audio_freedv_playback_observe(a->playback),
                .previous_tx_generation=a->previous_tx_generation,.previous_tx_input_samples=a->previous_tx_input_samples,
                .previous_tx_first_write_ns=a->previous_tx_first_write_ns,.previous_tx_underflows=a->previous_tx_underflows,
                .previous_tx_finished=a->previous_tx_finished};
            if(a->snapshot.playback.stream_failed){
                failure(a);a->snapshot.failed=true;a->snapshot.cancelled=true;
            }
        }
        a->busy=false;pthread_cond_broadcast(&a->changed);pthread_mutex_unlock(&a->lock);
        pause_ms();pthread_mutex_lock(&a->lock);
    }
    pthread_mutex_unlock(&a->lock);return NULL;
}
fd_voice_audio *fd_voice_audio_open(const fd_qualified_profile *profile) {
    /* The existing capture source is fixed by board.h. Reject incompatible
     * descriptors instead of silently using the wrong endpoint or boundary. */
    if(!profile || !profile->capture_device || !profile->playback_device || !profile->mixer)return NULL;
#if defined(FREEDV_HOST_TEST_PROFILE) || defined(HOST_BUILD)
    if(strcmp(profile->capture_device,"@default-source@") || strcmp(profile->playback_device,"@default-sink@") ||
        strcmp(profile->mixer,"host-default") || profile->sample_rate!=AUDIO_CAPTURE_RATE)return NULL;
#else
    if(
        !BOARD_PA_SOURCE || !BOARD_PA_SINK || profile->sample_rate!=AUDIO_CAPTURE_RATE ||
        strcmp(profile->capture_device,BOARD_PA_SOURCE) || strcmp(profile->playback_device,BOARD_PA_SINK))return NULL;
#endif
    fd_voice_audio *a=calloc(1,sizeof(*a));if(!a)return NULL;
    if(pthread_mutex_init(&a->lock,NULL)){free(a);return NULL;}
    if(pthread_cond_init(&a->changed,NULL)){pthread_mutex_destroy(&a->lock);free(a);return NULL;}
    atomic_init(&a->cancelled,true);atomic_init(&a->failed,false);atomic_init(&a->microphone_peak,0);atomic_init(&a->cancel_epoch,0);a->rate=profile->sample_rate;
#if defined(FREEDV_HOST_TEST_PROFILE) || defined(HOST_BUILD)
    if(!audio_freedv_capture_configure(profile->capture_channel,NULL))goto error;
#else
    if(!audio_freedv_capture_configure(profile->capture_channel,profile->mixer))goto error;
#endif
    a->engine=fd_voice_create(profile->sample_rate);a->playback=audio_freedv_playback_open();
    if(!a->engine || !a->playback)goto error;
    if(pthread_create(&a->pump,NULL,pump,a))goto error;
    a->started=true;return a;
error:
    fd_voice_audio_close(a);return NULL;
}
bool fd_voice_audio_fence(fd_voice_audio *a) {
    if(!a)return false;fd_voice_audio_cancel(a);
    pthread_mutex_lock(&a->lock);a->active=false;
    audio_freedv_capture_end();a->count=a->offset=0;
    if(a->direction==FD_VOICE_TX && a->generation){
        fd_voice_stats engine=fd_voice_observe(a->engine);
        audio_freedv_playback_stats_t playback=audio_freedv_playback_observe(a->playback);
        a->previous_tx_generation=a->generation;a->previous_tx_input_samples=engine.input_samples;
        a->previous_tx_first_write_ns=playback.generation_first_write_ns;
        a->previous_tx_underflows=playback.generation_underflows;a->previous_tx_finished=engine.finished;
    }
    pthread_mutex_unlock(&a->lock);
    if(!audio_freedv_playback_cork(a->playback,true) || !audio_freedv_playback_cancel(a->playback))return false;
    uint64_t deadline=now()+500;
    while(!audio_freedv_playback_quiescent(a->playback) && now()<deadline)pause_ms();
    return audio_freedv_playback_quiescent(a->playback);
}
bool fd_voice_audio_begin(fd_voice_audio *a,fd_voice_direction direction,uint64_t generation) {
    if(!a || direction==FD_VOICE_IDLE || atomic_load(&a->failed))return false;
    unsigned epoch=atomic_load(&a->cancel_epoch);
    /* Driver applies/key-selects the qualified route first. Flush at that
     * actual boundary, then admit only fresh microphone/RF samples. */
    if(!audio_freedv_capture_flush_begin()) { failure(a); return false; }
    uint64_t deadline=now()+500;
    while(!audio_freedv_capture_flushed() && now()<deadline)pause_ms();
    if(audio_freedv_capture_flushed()!=1) { failure(a); return false; }
    if(epoch!=atomic_load(&a->cancel_epoch))return false;
    pthread_mutex_lock(&a->lock);
    bool ok=!a->active && generation>a->generation &&
        fd_voice_transition(a->engine,direction,generation) && audio_freedv_playback_rearm(a->playback,generation) &&
        audio_freedv_playback_cork(a->playback,false) && audio_freedv_capture_begin();
    if(ok){
        a->generation=generation;a->direction=direction;a->finishing=a->finish_sent=a->drain_sent=false;
        atomic_store(&a->microphone_peak,0);atomic_store(&a->cancelled,false);
        /* Pump admission is still locked: a concurrent Stop cannot be erased
         * by rearm, even if it arrived during a PA callback/engine transition. */
        if(epoch!=atomic_load(&a->cancel_epoch)){fd_voice_audio_cancel(a);audio_freedv_capture_end();ok=false;}
        else {a->active=true;pthread_cond_broadcast(&a->changed);}
    }
    pthread_mutex_unlock(&a->lock);if(!ok && epoch==atomic_load(&a->cancel_epoch))failure(a);return ok;
}
bool fd_voice_audio_finish(fd_voice_audio *a,uint64_t generation) {
    if(!a)return false;pthread_mutex_lock(&a->lock);
    bool ok=a->active && a->direction==FD_VOICE_TX && generation==a->generation && !atomic_load(&a->cancelled);
    if(ok){audio_freedv_capture_end();a->finishing=true;}
    pthread_mutex_unlock(&a->lock);return ok;
}
int fd_voice_audio_drained(fd_voice_audio *a) {
    if(!a || atomic_load(&a->failed) || atomic_load(&a->cancelled))return -1;
    pthread_mutex_lock(&a->lock);int status=a->drain_sent?audio_freedv_playback_drained(a->playback,a->generation):0;
    pthread_mutex_unlock(&a->lock);return status;
}
bool fd_voice_audio_failed(fd_voice_audio *a) {return !a || atomic_load(&a->failed);}
bool fd_voice_audio_set_taps(fd_voice_audio *a,const fd_voice_audio_taps *taps) {
    if(!a)return false;pthread_mutex_lock(&a->lock);bool ok=!a->active;
    if(ok)a->taps=taps?*taps:(fd_voice_audio_taps){0};
    pthread_mutex_unlock(&a->lock);return ok;
}
void fd_voice_audio_on_error(fd_voice_audio *a,void (*handler)(void *),void *context) {
    if(!a)return;pthread_mutex_lock(&a->lock);a->on_error=handler;a->error_context=context;pthread_mutex_unlock(&a->lock);
}
bool fd_voice_audio_snapshot_get(fd_voice_audio *a,fd_voice_audio_snapshot *out) {
    if(!a || !out || pthread_mutex_trylock(&a->lock))return false;
    bool valid=a->snapshot.sampled_ms && a->snapshot.generation==a->generation;
    if(valid){*out=a->snapshot;out->cancelled=atomic_load(&a->cancelled);out->failed=atomic_load(&a->failed);}
    pthread_mutex_unlock(&a->lock);return valid;
}
bool fd_voice_audio_microphone_active(fd_voice_audio *a) {return a && !atomic_load(&a->cancelled) && atomic_load(&a->microphone_peak)>120;}
fd_voice_stats fd_voice_audio_observe(fd_voice_audio *a) {return fd_voice_observe(a?a->engine:NULL);}
void fd_voice_audio_close(fd_voice_audio *a) {
    if(!a)return;fd_voice_audio_cancel(a);pthread_mutex_lock(&a->lock);a->shutdown=true;
    pthread_cond_broadcast(&a->changed);pthread_mutex_unlock(&a->lock);
    if(a->started)pthread_join(a->pump,NULL);
    audio_freedv_capture_end();
    audio_freedv_capture_flush_close();
    (void)audio_freedv_capture_configure(0,NULL);
    if(a->playback)audio_freedv_playback_close(a->playback);
    fd_voice_destroy(a->engine);pthread_cond_destroy(&a->changed);pthread_mutex_destroy(&a->lock);free(a);
}
