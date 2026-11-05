// SPDX-License-Identifier: LGPL-2.1-or-later
#define _POSIX_C_SOURCE 200809L
#define ENABLE_FREEDV_DIAGNOSTICS 1
#undef HOST_BUILD
#include "../../src/freedv/voice_audio.c"
bool audio_freedv_voice_capture_open(void){return true;}
void audio_freedv_voice_capture_close(void){}
#include <assert.h>
#include <stdio.h>
/* Exercise the real production pump and lifecycle with deterministic device
 * boundaries. Real codec and real audio.c callback tests are separate targets. */
static atomic_uint engine_creates,stream_creates,capture_begins,engine_samples,accepted,closed,corks;
static atomic_bool capture_on,drain_requested,drain_completed,flush_complete;
static atomic_bool delay_capture_flush,entered_capture_flush;
static atomic_bool speech_input;
static unsigned generated,consumed;
static uint64_t fake_gen;
static bool engine_finished;
static int failure_mode;
static int token_engine,token_playback;
static unsigned submit_calls,sequence_checked;
static bool device_blocked,check_sequence;
static fd_voice_audio *cancel_during_submit;
static atomic_bool *startup_permission;
static fd_voice_audio *cancel_during_startup;
static unsigned reject_startup_step;
static void startup_step(unsigned step){
    if(reject_startup_step!=step)return;
    if(startup_permission)atomic_store(startup_permission,false);
    if(cancel_during_startup)fd_voice_audio_cancel(cancel_during_startup);
}
static atomic_uint fault_callbacks;
static void on_error(void *context){assert(context);atomic_fetch_add(&fault_callbacks,1);fd_voice_audio_cancel(context);}
fd_voice_engine *fd_voice_create(unsigned rate){assert(rate==44100);atomic_fetch_add(&engine_creates,1);return(fd_voice_engine *)&token_engine;}
bool fd_voice_transition(fd_voice_engine *e,fd_voice_direction dir,uint64_t gen){(void)e;assert(dir!=FD_VOICE_IDLE && gen>fake_gen);fake_gen=gen;generated=consumed=0;engine_finished=false;atomic_store(&drain_requested,false);atomic_store(&drain_completed,false);startup_step(1);return true;}
size_t fd_voice_submit(fd_voice_engine *e,uint64_t gen,const int16_t *p,size_t n){(void)e;assert(gen==fake_gen && p);atomic_fetch_add(&engine_samples,(unsigned)n);generated+=(unsigned)n;return n;}
size_t fd_voice_receive(fd_voice_engine *e,uint64_t gen,int16_t *p,size_t n){(void)e;assert(gen==fake_gen);if(n>generated-consumed)n=generated-consumed;for(size_t i=0;i<n;++i)p[i]=(int16_t)(fake_gen*100+consumed+i);consumed+=(unsigned)n;return n;}
bool fd_voice_finish(fd_voice_engine *e,uint64_t gen){(void)e;assert(gen==fake_gen);engine_finished=true;return true;}
void fd_voice_cancel(fd_voice_engine *e){(void)e;}
fd_voice_stats fd_voice_observe(fd_voice_engine *e){(void)e;return(fd_voice_stats){.input_samples=generated,.finished=engine_finished,.output_depth=generated-consumed,.input_depth=failure_mode==3?14000:0};}
void fd_voice_destroy(fd_voice_engine *e){assert(e);}
bool audio_freedv_capture_configure(unsigned channel,const char *mixer){assert(!channel && (!mixer || !strcmp(mixer,"fake")));return true;}
bool audio_freedv_capture_begin(void){atomic_store(&capture_on,true);atomic_fetch_add(&capture_begins,1);startup_step(3);return true;}
void audio_freedv_capture_end(void){atomic_store(&capture_on,false);}
bool audio_freedv_capture_flush_begin(void){assert(!atomic_load(&capture_on));atomic_store(&entered_capture_flush,true);return true;}
int audio_freedv_capture_flushed(void){return atomic_load(&delay_capture_flush)?0:1;}
void audio_freedv_capture_flush_close(void){}
size_t audio_freedv_capture_read(int16_t *out,size_t n){if(!atomic_load(&capture_on))return 0;for(size_t i=0;i<n;++i)out[i]=149+(atomic_load(&speech_input)?(i%2?1000:-1000):0);return n;}
size_t audio_freedv_capture_read_stereo(int16_t *out,size_t n){(void)out;(void)n;return 0;}
audio_freedv_capture_stats_t audio_freedv_capture_stats(void){return(audio_freedv_capture_stats_t){.capture_errors=failure_mode==1,.queued_samples=failure_mode==3?10000:0,
    .started_ms=failure_mode>=5?(unsigned)now()-2000:0,.callbacks=failure_mode==6?0:2,
    .last_callback_ms=(unsigned)now()-(failure_mode==5?501:0),.longest_inter_callback_gap_ms=failure_mode==7?773:0,
    .first_callback_delay_ms=failure_mode==8?1001:0};}
audio_freedv_playback_t *audio_freedv_playback_open(void){atomic_fetch_add(&stream_creates,1);return(audio_freedv_playback_t *)&token_playback;}
audio_freedv_playback_stats_t audio_freedv_playback_observe(audio_freedv_playback_t *p){assert(p);return(audio_freedv_playback_stats_t){.generation=fake_gen,.accepted_samples=atomic_load(&accepted),.stream_failed=failure_mode==4};}
ssize_t audio_freedv_playback_submit_generation(audio_freedv_playback_t *p,uint64_t gen,const int16_t *pcm,size_t n){
    assert(p && gen==fake_gen && pcm);++submit_calls;
    if(failure_mode==2)return -1;if(device_blocked)return 0;if(n>13)n=13;
    if(check_sequence)for(size_t i=0;i<n;++i)assert(pcm[i]==(int16_t)(fake_gen*100+sequence_checked++));
    atomic_fetch_add(&accepted,(unsigned)n);
    if(cancel_during_submit)atomic_store(&cancel_during_submit->cancelled,true);
    return(ssize_t)n;
}
void audio_freedv_playback_request_cancel(audio_freedv_playback_t *p){(void)p;}
bool audio_freedv_playback_cancel(audio_freedv_playback_t *p){assert(p);atomic_store(&flush_complete,true);return true;}
bool audio_freedv_playback_cork(audio_freedv_playback_t *p,bool cork){(void)cork;atomic_fetch_add(&corks,1);return p!=NULL;}
bool audio_freedv_playback_quiescent(audio_freedv_playback_t *p){return p && atomic_load(&flush_complete);}
bool audio_freedv_playback_rearm(audio_freedv_playback_t *p,uint64_t gen){assert(p && gen==fake_gen);atomic_store(&flush_complete,false);startup_step(2);return true;}
bool audio_freedv_playback_drain(audio_freedv_playback_t *p,uint64_t gen){assert(p && gen==fake_gen);atomic_store(&drain_requested,true);return true;}
int audio_freedv_playback_drained(audio_freedv_playback_t *p,uint64_t gen){assert(p && gen==fake_gen);return atomic_load(&drain_completed)?1:0;}
void audio_freedv_playback_close(audio_freedv_playback_t *p){assert(p);atomic_fetch_add(&closed,1);}
static void *cancelled_begin(void *arg){assert(!fd_voice_audio_begin(arg,FD_VOICE_TX,101));return NULL;}
typedef struct {fd_voice_audio *audio;atomic_bool allowed,rejected;atomic_uint visits;} test_admission;
static bool test_admit(void *context){
    test_admission *g=context;atomic_fetch_add(&g->visits,1);
    if(!atomic_load(&g->allowed))atomic_store(&g->rejected,true);
    return !atomic_load(&g->rejected);
}
static void *guarded_begin(void *arg){
    test_admission *g=arg;assert(!fd_voice_audio_begin_guarded(g->audio,FD_VOICE_TX,1,test_admit,g));return NULL;
}
static void startup_admission_test(const fd_qualified_profile *profile){
    failure_mode=0;
    for(unsigned step=0;step<=4;++step){
        fake_gen=generated=consumed=0;
        fd_voice_audio *a=fd_voice_audio_open(profile);assert(a && fd_voice_audio_fence(a));
        test_admission guard={.audio=a};atomic_init(&guard.allowed,step!=0);
        atomic_init(&guard.rejected,false);atomic_init(&guard.visits,0);
        startup_permission=&guard.allowed;reject_startup_step=step;
        unsigned input_before=atomic_load(&engine_samples),output_before=atomic_load(&accepted);
        if(step==4){
            atomic_store(&delay_capture_flush,true);atomic_store(&entered_capture_flush,false);
            pthread_t thread;assert(!pthread_create(&thread,NULL,guarded_begin,&guard));
            while(!atomic_load(&entered_capture_flush))pause_ms();
            atomic_store(&guard.allowed,false);
            pthread_join(thread,NULL); /* guard must stop a still-pending flush */
            assert(atomic_load(&delay_capture_flush));atomic_store(&delay_capture_flush,false);
        }else assert(!fd_voice_audio_begin_guarded(a,FD_VOICE_TX,1,test_admit,&guard));
        assert(atomic_load(&guard.rejected) && !atomic_load(&capture_on));
        assert(!a->active && !fd_voice_audio_failed(a));
        assert(atomic_load(&engine_samples)==input_before && atomic_load(&accepted)==output_before);
        /* Re-press cannot revive the rejected turn. A new guarded turn after
         * fencing can reuse the healthy retained engine and streams. */
        atomic_store(&guard.allowed,true);assert(!test_admit(&guard));
        startup_permission=NULL;assert(fd_voice_audio_fence(a));
        atomic_store(&guard.rejected,false);
        assert(fd_voice_audio_begin_guarded(a,FD_VOICE_TX,2,test_admit,&guard));
        uint64_t deadline=now()+1000;
        while(atomic_load(&engine_samples)==input_before){assert(now()<deadline);pause_ms();}
        assert(fd_voice_audio_fence(a));fd_voice_audio_close(a);
    }
    for(unsigned step=1;step<=3;++step){
        fake_gen=generated=consumed=0;
        fd_voice_audio *a=fd_voice_audio_open(profile);assert(a && fd_voice_audio_fence(a));
        cancel_during_startup=a;reject_startup_step=step;
        unsigned input_before=atomic_load(&engine_samples),output_before=atomic_load(&accepted);
        assert(!fd_voice_audio_begin(a,FD_VOICE_TX,1));
        assert(!a->active && atomic_load(&a->cancelled) && !atomic_load(&capture_on) && !fd_voice_audio_failed(a));
        assert(atomic_load(&engine_samples)==input_before && atomic_load(&accepted)==output_before);
        cancel_during_startup=NULL;assert(fd_voice_audio_fence(a));fd_voice_audio_close(a);
    }
    puts("PASS: startup release before/during flush, engine transition, playback rearm and capture begin admits no codec input/output; healthy streams remain reusable");
}
static void ready_output_test(void){
    fd_voice_audio direct={.engine=(fd_voice_engine *)&token_engine,.playback=(audio_freedv_playback_t *)&token_playback,
        .generation=1,.rate=44100};
    atomic_init(&direct.cancelled,false);
    fake_gen=1;generated=5292;consumed=0;sequence_checked=submit_calls=0;check_sequence=true;
    atomic_store(&accepted,0);
    submit_ready_output(&direct);
    assert(submit_calls==64 && atomic_load(&accepted)>441); /* more than one short write per wake */
    while(consumed<generated || direct.offset<direct.count)submit_ready_output(&direct);
    assert(atomic_load(&accepted)==5292 && sequence_checked==5292);
    generated+=441;unsigned before=submit_calls;device_blocked=true;
    submit_ready_output(&direct);
    assert(submit_calls==before+1 && direct.count>direct.offset && sequence_checked==5292);
    device_blocked=false;cancel_during_submit=&direct;before=submit_calls;
    submit_ready_output(&direct);
    assert(submit_calls==before+1 && atomic_load(&direct.cancelled));
    cancel_during_submit=NULL;check_sequence=false;
    fake_gen=generated=consumed=0;atomic_store(&accepted,0);
    puts("PASS: ready PCM submission is bounded, preserves partial-write order, stops on backpressure and observes concurrent cancellation");
}
int main(int argc,char **argv){
    ready_output_test();
    if(argc==2 && !strcmp(argv[1],"--submission"))return 0;
    fd_qualified_profile profile={.sample_rate=44100,.capture_device=BOARD_PA_SOURCE,.playback_device=BOARD_PA_SINK,.mixer="fake"};
    fd_voice_audio *a=fd_voice_audio_open(&profile);assert(a);
    uint64_t adapter_id=a->adapter_instance,engine_id=a->engine_instance,
        capture_id=a->capture_instance,playback_id=a->playback_instance;
    assert(adapter_id && engine_id && capture_id && playback_id && adapter_id!=engine_id &&
        engine_id!=capture_id && capture_id!=playback_id);
    fd_voice_audio_on_error(a,on_error,a);
    for(unsigned turn=1;turn<=100;++turn){
        atomic_store(&speech_input,turn==2);
        assert(fd_voice_audio_fence(a));assert(fd_voice_audio_begin(a,FD_VOICE_TX,turn));
        unsigned before=atomic_load(&engine_samples);uint64_t deadline=now()+2000;
        while(atomic_load(&engine_samples)==before){assert(now()<deadline);pause_ms();}
        if(turn<=2){
            while(atomic_load(&engine_samples)-before<10000){assert(now()<deadline);pause_ms();}
            assert(fd_voice_audio_microphone_active(a)==(turn==2)); /* observed 149 DC is not speech */
        }
        assert(fd_voice_audio_finish(a,turn));assert(!atomic_load(&capture_on));
        /* The mock accepts 13 samples per device write; bounded submission
         * drains already-ready output without a sleep between each write. */
        deadline=now()+5000;
        while(!atomic_load(&drain_requested)){assert(now()<deadline);pause_ms();}
        assert(fd_voice_audio_drained(a)==0);atomic_store(&drain_completed,true);assert(fd_voice_audio_drained(a)==1);
        assert(atomic_load(&engine_creates)==1 && atomic_load(&stream_creates)==1);
        assert(!fd_voice_audio_failed(a));
        fd_voice_audio_snapshot snapshot;
        while(!fd_voice_audio_snapshot_get(a,&snapshot)){assert(now()<deadline);pause_ms();}
        assert(snapshot.generation==turn && snapshot.playback.generation==turn);
        assert(snapshot.adapter_instance==adapter_id && snapshot.engine_instance==engine_id &&
            snapshot.capture_instance==capture_id && snapshot.playback_instance==playback_id);
        if(turn>1)assert(snapshot.previous_tx_generation==turn-1 && snapshot.previous_tx_finished);
    }
    assert(atomic_load(&accepted)==atomic_load(&engine_samples));
    assert(fd_voice_audio_fence(a));
    atomic_store(&delay_capture_flush,true);atomic_store(&entered_capture_flush,false);pthread_t beginning;
    assert(!pthread_create(&beginning,NULL,cancelled_begin,a));
    while(!atomic_load(&entered_capture_flush))pause_ms();
    fd_voice_audio_cancel(a);atomic_store(&delay_capture_flush,false);pthread_join(beginning,NULL);
    assert(!fd_voice_audio_failed(a) && !atomic_load(&capture_on));
    assert(fd_voice_audio_fence(a));failure_mode=1;
    assert(fd_voice_audio_begin(a,FD_VOICE_RX,101));uint64_t deadline=now()+1000;
    while(!fd_voice_audio_failed(a)){assert(now()<deadline);pause_ms();}
    assert(fd_voice_audio_fence(a));fd_voice_audio_snapshot fault;
    assert(fd_voice_audio_snapshot_get(a,&fault) && fault.first_fault==FD_AUDIO_CAPTURE && fault.fault_generation==101);
    unsigned began=atomic_load(&capture_begins);
    assert(!fd_voice_audio_begin(a,FD_VOICE_TX,102) && atomic_load(&capture_begins)==began);
    assert(fd_voice_audio_drained(a)==-1);fd_voice_audio_close(a);assert(atomic_load(&closed)==1);
    assert(atomic_load(&fault_callbacks)==1);
    failure_mode=3;fake_gen=0;a=fd_voice_audio_open(&profile);assert(a);fd_voice_audio_on_error(a,on_error,a);
    assert(a->adapter_instance!=adapter_id && a->engine_instance!=engine_id &&
        a->capture_instance!=capture_id && a->playback_instance!=playback_id);
    assert(fd_voice_audio_fence(a) && fd_voice_audio_begin(a,FD_VOICE_RX,1));deadline=now()+1000;
    while(!fd_voice_audio_failed(a)){assert(now()<deadline);pause_ms();}
    assert(fd_voice_audio_fence(a));
    assert(fd_voice_audio_snapshot_get(a,&fault) && fault.first_fault==FD_AUDIO_BACKLOG);
    assert(fault.fault_capture_queued==10000 && fault.fault_engine_input_depth==14000);
    fd_voice_audio_close(a);assert(atomic_load(&fault_callbacks)==2 && atomic_load(&closed)==2);
    failure_mode=4;fake_gen=0;a=fd_voice_audio_open(&profile);assert(a);fd_voice_audio_on_error(a,on_error,a);
    assert(fd_voice_audio_fence(a) && fd_voice_audio_begin(a,FD_VOICE_RX,1));deadline=now()+1000;
    while(!fd_voice_audio_failed(a)){assert(now()<deadline);pause_ms();}
    fd_voice_audio_close(a);assert(atomic_load(&fault_callbacks)==3 && atomic_load(&closed)==3);
    /* No reported PA holes/drops and no backlog: late/missing capture must
     * still fault, including a callback that resumes before the worker. */
    for(failure_mode=5;failure_mode<=8;++failure_mode){
        fake_gen=0;a=fd_voice_audio_open(&profile);assert(a);fd_voice_audio_on_error(a,on_error,a);
        assert(fd_voice_audio_fence(a) && fd_voice_audio_begin(a,FD_VOICE_TX,1));deadline=now()+1000;
        while(!fd_voice_audio_failed(a)){assert(now()<deadline);pause_ms();}
        assert(fd_voice_audio_fence(a));
        assert(fd_voice_audio_snapshot_get(a,&fault) && fault.first_fault==FD_AUDIO_CAPTURE_STALL);
        assert(!fault.capture.hole_samples && !fault.capture.dropped_samples && !fault.fault_capture_queued);
        fd_voice_audio_close(a);
    }
    audio_freedv_capture_stats_t idle={.started_ms=(unsigned)now()-2000,.callbacks=10,.last_callback_ms=(unsigned)now()-800};
    assert(!capture_stalled(idle,false)); /* normal tail/device drain */
    idle.longest_inter_callback_gap_ms=773;
    assert(capture_stalled(idle,false)); /* finishing cannot erase a known gap */
    assert(atomic_load(&fault_callbacks)==7 && atomic_load(&closed)==7);
    assert(!atomic_load(&corks)); /* flush fences writes without idle-suspending */
    startup_admission_test(&profile);
    puts("PASS: actual voice adapter, 100 retained turns, partial writes, drain/fence, backlog, device failure and unreported capture stalls");
}
