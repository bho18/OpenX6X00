// SPDX-License-Identifier: LGPL-2.1-or-later
#define _POSIX_C_SOURCE 200809L
#include "../../src/freedv/voice_session.h"
#include <assert.h>
#include <pthread.h>
#include <stdio.h>
#include <string.h>
#include <time.h>

static uint64_t clock_ms(void) {
    struct timespec t;clock_gettime(CLOCK_MONOTONIC,&t);
    return (uint64_t)t.tv_sec*1000+t.tv_nsec/1000000;
}
static void pause_ms(unsigned ms) {
    struct timespec t={(time_t)(ms/1000),(long)(ms%1000)*1000000};nanosleep(&t,NULL);
}
static struct {
    atomic_bool held[FD_PTT_SOURCES],tx,stale,unknown,stall,startup,drain;
    atomic_bool inject_fence_release,block_observe,observer_blocked,freeze_aggregate,regress_release,protection;
    atomic_bool freeze_ptt,freeze_ptt_stamp;
    atomic_uint transmits,prepares;
    atomic_uint_fast64_t sequence,aggregate_sequence;
    atomic_uint_fast64_t release_sequence[FD_PTT_SOURCES];
    uint64_t admission_releases[FD_PTT_SOURCES];
    fd_abort_reason startup_reason;
    fd_ptt_observation cached_ptt[FD_PTT_SOURCES];
} fake;
static void set_held(unsigned source,bool held) {
    if(atomic_exchange(&fake.held[source],held) && !held)atomic_fetch_add(&fake.release_sequence[source],1);
}
static bool prepare(void *ctx,const fd_qualified_profile *p,const atomic_bool *cancel) {
    (void)ctx;assert(p && !p->complete_pass && !p->capabilities && !p->evidence_sha256);
    atomic_fetch_add(&fake.prepares,1);return !atomic_load(cancel);
}
static bool begin_transmit(void *ctx,uint64_t gen,const fd_voice_observation *admission) {
    (void)ctx;assert(gen && admission);fake.startup_reason=FD_ABORT_NONE;
    for(unsigned i=0;i<FD_PTT_SOURCES;++i)fake.admission_releases[i]=admission->ptt[i].release_sequence;
    return true;
}
static bool fence(void *ctx) {
    (void)ctx;
    if(atomic_exchange(&fake.inject_fence_release,false)) {
        /* The complete hand release/repress occurs while the coordinator is
         * inside fence, without a Linux/front-key edge callback. */
        set_held(FD_PTT_HAND,false);pause_ms(5);set_held(FD_PTT_HAND,true);
    }
    return true;
}
static bool receive(void *ctx,uint64_t gen) {(void)ctx;assert(gen);return true;}
static bool transmit(void *ctx,uint64_t gen,const atomic_bool *cancel) {
    (void)ctx;assert(gen);atomic_store(&fake.startup,true);
    while(atomic_load(&fake.stall) && !atomic_load(cancel))pause_ms(1);
    atomic_store(&fake.startup,false);
    if(atomic_load(cancel))return false;
    for(unsigned i=0;i<FD_PTT_SOURCES;++i)
        if(atomic_load(&fake.release_sequence[i])!=fake.admission_releases[i]) {
            fake.startup_reason=FD_ABORT_START_RELEASE;return false;
        }
    atomic_fetch_add(&fake.transmits,1);atomic_store(&fake.tx,true);return true;
}
static fd_abort_reason startup_abort_reason(void *ctx) {(void)ctx;return fake.startup_reason;}
static bool finish(void *ctx,uint64_t gen) {(void)ctx;assert(gen);return true;}
static int drained(void *ctx) {(void)ctx;return atomic_load(&fake.drain);}
static void clear(void *ctx) {(void)ctx;atomic_store(&fake.tx,false);}
static bool retune(void *ctx,fd_voice_tuning *t,const fd_qualified_profile *p) {(void)ctx;assert(t && p);return true;}
static bool restore(void *ctx) {(void)ctx;return !atomic_load(&fake.tx);}
static bool close_driver(void *ctx) {(void)ctx;return true;}
static bool observe(void *ctx,fd_voice_observation *o,bool *sync,bool *mic) {
    (void)ctx;uint64_t time=clock_ms(),seq=atomic_fetch_add(&fake.sequence,1)+1;
    uint64_t aggregate=atomic_load(&fake.freeze_aggregate)?atomic_load(&fake.aggregate_sequence):atomic_fetch_add(&fake.aggregate_sequence,1)+1;
    if(atomic_load(&fake.stale))time-=1000;
    *o=(fd_voice_observation){.valid=true,.tx=atomic_load(&fake.tx),.sequence=aggregate,.monotonic_ms=time,
        .protection=atomic_load(&fake.protection)};
    for(unsigned i=0;i<FD_PTT_SOURCES;++i)
        o->ptt[i]=(fd_ptt_observation){!atomic_load(&fake.unknown),atomic_load(&fake.held[i]),seq,time,
            atomic_load(&fake.release_sequence[i])};
    if(atomic_load(&fake.regress_release)) {
        assert(o->ptt[FD_PTT_HAND].release_sequence);
        --o->ptt[FD_PTT_HAND].release_sequence;
    }
    for(unsigned i=0;i<FD_PTT_SOURCES;++i) {
        if(atomic_load(&fake.freeze_ptt))o->ptt[i]=fake.cached_ptt[i];
        else if(atomic_load(&fake.freeze_ptt_stamp))o->ptt[i].monotonic_ms=fake.cached_ptt[i].monotonic_ms;
        else fake.cached_ptt[i]=o->ptt[i];
    }
    if(atomic_load(&fake.block_observe)) {
        atomic_store(&fake.observer_blocked,true);
        while(atomic_load(&fake.block_observe))pause_ms(1);
        atomic_store(&fake.observer_blocked,false);
    }
    *sync=true;*mic=true;return true;
}
static void wait_state(fd_voice_session *s,fd_voice_state expected) {
    uint64_t deadline=clock_ms()+3000;
    while(fd_session_observe(s).state!=expected) {
        if(clock_ms()>=deadline){fprintf(stderr,"expected %d, observed %d\n",expected,fd_session_observe(s).state);assert(false);}
        pause_ms(1);
    }
}
static void released(fd_voice_session *s) {
    for(unsigned i=0;i<FD_PTT_SOURCES;++i)set_held(i,false);
    wait_state(s,FD_VS_RX);
    for(unsigned tries=0;tries<100;++tries) {
        fd_voice_status status=fd_session_observe(s);
        if(!status.ptt_held[0] && !status.ptt_held[1] && !status.ptt_unknown[0] && !status.ptt_unknown[1])return;
        pause_ms(1);
    }
    assert(false);
}
static void grant(fd_voice_session *s) {
    assert(fd_session_validation_arm(s));
    assert(fd_session_observe(s).bench_grant==FD_BENCH_ARMED);
    assert(fd_session_validation_run(s));
    assert(fd_session_observe(s).bench_grant==FD_BENCH_READY);
}
struct run_request {fd_voice_session *session;bool result;};
static void *run_grant(void *ctx) {
    struct run_request *request=ctx;request->result=fd_session_validation_run(request->session);return NULL;
}
int main(void) {
    fd_qualified_profile candidate={.id="unqualified candidate",.hardware="fake",.build_id="test",
        .baseband="1",.control_version="test-control",.local_mode=FD_PROFILE_USB_DIG,.upper_sideband=true,
        .capture_device="fake-source",.playback_device="fake-sink",.mixer="fake",
        .sample_rate=44100,.rx_route=FD_ROUTE_RECORD,.tx_route=FD_ROUTE_MODEM,
        .rx_low_hz=100,.rx_high_hz=3100,.tx_low_hz=100,.tx_high_hz=3100,.power_tenths_watt=10};
    fd_validation_admission admission={FD_VALIDATION_ABI,"fake","test","1","test-control",3,true,3};
    assert(fd_profile_candidate_valid(&candidate,"fake","test"));
    assert(!fd_profile_valid(&candidate,"fake","test") && !fd_profile_production());
    assert(fd_validation_admission_valid(&candidate,&admission));
    fd_validation_admission bad=admission;
    bad.keying_owned=false;assert(!fd_validation_admission_valid(&candidate,&bad));
    bad=admission;bad.independent_ptt_sources=1;assert(!fd_validation_admission_valid(&candidate,&bad));
    bad=admission;bad.baseband="2";assert(!fd_validation_admission_valid(&candidate,&bad));
    bad=admission;bad.build_id="other";assert(!fd_validation_admission_valid(&candidate,&bad));
    bad=admission;bad.control_version="other";assert(!fd_validation_admission_valid(&candidate,&bad));
    bad=admission;bad.hardware="other";assert(!fd_validation_admission_valid(&candidate,&bad));
    bad=admission;bad.abi_version=2;assert(!fd_validation_admission_valid(&candidate,&bad));
    bad=admission;bad.max_turn_seconds=61;assert(!fd_validation_admission_valid(&candidate,&bad));
    candidate.capabilities=1u<<20;assert(!fd_profile_candidate_valid(&candidate,"fake","test"));candidate.capabilities=0;
    candidate.sample_rate=12345;assert(!fd_profile_candidate_valid(&candidate,"fake","test"));candidate.sample_rate=44100;
    candidate.rx_route=FD_ROUTE_MODEM;assert(!fd_profile_candidate_valid(&candidate,"fake","test"));candidate.rx_route=FD_ROUTE_RECORD;
    fd_voice_driver driver={.prepare=prepare,.fence=fence,.receive=receive,.transmit=transmit,.finish=finish,
        .drained=drained,.emergency_clear=clear,.retune=retune,.restore=restore,.close=close_driver,.observe=observe,
        .begin_transmit=begin_transmit,.startup_abort_reason=startup_abort_reason};
    fd_voice_session *s=fd_session_create(&driver);assert(s);
    fd_voice_tuning tuning={.frequency=14100000,.vfo=0};
    assert(!fd_session_enter(s,&candidate,"fake","test",tuning,30));
    assert(!fd_session_enter_validation(s,&candidate,&bad,tuning,2));
    assert(!fd_session_enter_validation(s,&candidate,&admission,tuning,4));
    assert(fd_session_enter_validation(s,&candidate,&admission,tuning,2));released(s);
    assert(fd_session_observe(s).validation && !fd_session_validation_run(s));
    assert(!fd_session_timeout(s,4) && fd_session_timeout(s,2));
    set_held(0,true);pause_ms(25);
    assert(atomic_load(&fake.transmits)==0 && !fd_session_validation_arm(s));released(s);
    /* A press before Run consumes no transmit permission. */
    assert(fd_session_validation_arm(s));set_held(1,true);pause_ms(25);
    assert(!fd_session_validation_run(s) && atomic_load(&fake.transmits)==0);released(s);
    /* Hold a released coordinator observation in flight, then physically
     * press before Run. The grant boundary must obtain a fresh independent
     * observation instead of trusting the cached released state. */
    assert(fd_session_validation_arm(s));atomic_store(&fake.block_observe,true);
    uint64_t blocked_deadline=clock_ms()+1000;
    while(!atomic_load(&fake.observer_blocked)){assert(clock_ms()<blocked_deadline);pause_ms(1);}
    set_held(1,true);
    struct run_request request={.session=s,.result=true};pthread_t run_thread;
    assert(!pthread_create(&run_thread,NULL,run_grant,&request));pause_ms(5);
    atomic_store(&fake.block_observe,false);assert(!pthread_join(run_thread,NULL));
    assert(!request.result && atomic_load(&fake.transmits)==0);released(s);
    /* Cached released PTT stays healthy briefly, but cannot authorize a new
     * turn. Both sample sequence and acquisition time must cross Run's request. */
    assert(fd_session_validation_arm(s));atomic_store(&fake.freeze_ptt,true);
    set_held(1,true);assert(!fd_session_validation_run(s));
    assert(atomic_load(&fake.transmits)==0);atomic_store(&fake.freeze_ptt,false);released(s);
    assert(fd_session_validation_arm(s));atomic_store(&fake.freeze_ptt_stamp,true);pause_ms(2);
    assert(!fd_session_validation_run(s));atomic_store(&fake.freeze_ptt_stamp,false);released(s);
    /* PTT advances independently between 35 ms baseband packets. Reusing an
     * aggregate sequence must not hide the press at the fresh Run boundary. */
    atomic_store(&fake.freeze_aggregate,true);assert(fd_session_validation_arm(s));
    set_held(1,true);assert(!fd_session_validation_run(s));
    assert(atomic_load(&fake.transmits)==0);atomic_store(&fake.freeze_aggregate,false);released(s);
    atomic_store(&fake.freeze_aggregate,true);assert(fd_session_validation_arm(s));
    atomic_store(&fake.protection,true);assert(!fd_session_validation_run(s));
    assert(atomic_load(&fake.transmits)==0);
    atomic_store(&fake.protection,false);atomic_store(&fake.freeze_aggregate,false);released(s);
    /* An entire PTT pulse between observations consumes the pending grant
     * even though both observed levels are released. Retained release history
     * is the evidence; the next press must require a new Arm -> Run. */
    grant(s);atomic_fetch_add(&fake.release_sequence[FD_PTT_HAND],1);
    uint64_t revoked_deadline=clock_ms()+1000;
    while(fd_session_observe(s).bench_grant!=FD_BENCH_DISARMED){assert(clock_ms()<revoked_deadline);pause_ms(1);}
    set_held(1,true);pause_ms(25);assert(atomic_load(&fake.transmits)==0);released(s);
    grant(s);set_held(0,true);wait_state(s,FD_VS_TX);
    assert(fd_session_observe(s).grants_consumed==1 && atomic_load(&fake.transmits)==1);
    assert(!fd_session_validation_run(s));
    set_held(1,true);set_held(0,false);pause_ms(25);
    assert(fd_session_observe(s).state==FD_VS_TX);
    set_held(1,false);wait_state(s,FD_VS_TX_DRAIN);
    /* Repress during drain cannot reuse the grant for another turn. */
    set_held(1,true);atomic_store(&fake.drain,true);wait_state(s,FD_VS_RX);pause_ms(25);
    assert(atomic_load(&fake.transmits)==1 && !fd_session_validation_arm(s));released(s);
    grant(s);atomic_store(&fake.stale,true);wait_state(s,FD_VS_FAULT);
    assert(fd_session_observe(s).bench_grant==FD_BENCH_DISARMED && !fd_session_validation_arm(s));
    atomic_store(&fake.stale,false);released(s);
    grant(s);atomic_store(&fake.unknown,true);wait_state(s,FD_VS_FAULT);
    atomic_store(&fake.unknown,false);released(s);
    assert(fd_session_observe(s).bench_grant==FD_BENCH_DISARMED);
    grant(s);atomic_store(&fake.regress_release,true);wait_state(s,FD_VS_FAULT);
    assert(fd_session_observe(s).ptt_unknown[FD_PTT_HAND]);
    atomic_store(&fake.regress_release,false);released(s);
    grant(s);assert(fd_session_retune(s,tuning));pause_ms(25);
    assert(fd_session_observe(s).bench_grant==FD_BENCH_DISARMED);released(s);
    grant(s);atomic_store(&fake.inject_fence_release,true);set_held(1,true);wait_state(s,FD_VS_FAULT);
    assert(fd_session_observe(s).fault==FD_ABORT_START_RELEASE && atomic_load(&fake.transmits)==1);
    released(s);assert(atomic_load(&fake.prepares)==1);
    /* Release/repress while the coordinator is blocked in TX startup latches
     * cancellation and keeps the reusable stream/session healthy. */
    grant(s);atomic_store(&fake.stall,true);set_held(0,true);
    uint64_t deadline=clock_ms()+1000;while(!atomic_load(&fake.startup)){assert(clock_ms()<deadline);pause_ms(1);}
    set_held(0,false);
    assert(fd_session_ptt(s,FD_PTT_RADIO,false,atomic_fetch_add(&fake.sequence,1)+1));
    set_held(0,true);
    (void)fd_session_ptt(s,FD_PTT_RADIO,true,atomic_fetch_add(&fake.sequence,1)+1);
    atomic_store(&fake.stall,false);wait_state(s,FD_VS_FAULT);
    assert(fd_session_observe(s).fault==FD_ABORT_START_RELEASE && atomic_load(&fake.transmits)==1);
    released(s);assert(atomic_load(&fake.prepares)==1);
    grant(s);atomic_store(&fake.stall,true);set_held(0,true);
    deadline=clock_ms()+1000;while(!atomic_load(&fake.startup)){assert(clock_ms()<deadline);pause_ms(1);}
    fd_session_validation_disarm(s);atomic_store(&fake.stall,false);wait_state(s,FD_VS_FAULT);
    assert(fd_session_observe(s).fault==FD_ABORT_STOP && atomic_load(&fake.transmits)==1);
    released(s);
    grant(s);set_held(1,true);wait_state(s,FD_VS_TX);
    wait_state(s,FD_VS_FAULT);assert(fd_session_observe(s).fault==FD_ABORT_TIMEOUT);released(s);
    grant(s);uint64_t expires=fd_session_observe(s).grant_deadline_ms;
    while(clock_ms()<=expires+10)pause_ms(5);
    assert(fd_session_observe(s).bench_grant==FD_BENCH_DISARMED);
    unsigned transmits=atomic_load(&fake.transmits);set_held(0,true);pause_ms(25);
    assert(atomic_load(&fake.transmits)==transmits);released(s);
    fd_session_leave(s);wait_state(s,FD_VS_OFF);assert(fd_session_destroy(s));
    assert(!candidate.complete_pass && !candidate.capabilities && !candidate.evidence_sha256 && !fd_profile_production());
    puts("PASS: truthful candidate admission, identities/control blockers, one-turn local grants, overlap/repress, stale controls, startup release, timeout and expiry");
}
