// SPDX-License-Identifier: LGPL-2.1-or-later
#define _POSIX_C_SOURCE 200809L
#include "voice_session.h"
#include "watchdog.h"
#include <pthread.h>
#include <stdlib.h>
#include <time.h>
#include <string.h>
#define HEALTH_MS 250u
#define DRAIN_MS 1500u
struct fd_voice_session {
    pthread_mutex_t lock;
    pthread_cond_t changed;
    pthread_t worker;
    fd_voice_driver driver;
    const fd_qualified_profile *profile;
    fd_voice_status status;
    fd_voice_observation hardware;
    uint64_t edge_sequence[FD_PTT_SOURCES], invalid_sequence[FD_PTT_SOURCES];
    bool held[FD_PTT_SOURCES], released[FD_PTT_SOURCES];
    bool shutdown, enter, retune, prepared, recovery, exit_unsafe;
    fd_voice_tuning requested_tuning;
    uint64_t deadline, clear_sequence, clear_time;
    atomic_bool cancel, leave, active;
    atomic_uint abort, watchdog_reason;
    freedv_watchdog watchdog;
};
static uint64_t now(void) { struct timespec t; clock_gettime(CLOCK_MONOTONIC,&t); return (uint64_t)t.tv_sec*1000+t.tv_nsec/1000000; }
static bool fresh(bool valid, uint64_t stamp, uint64_t time) { return valid && stamp && stamp <= time && time-stamp <= HEALTH_MS; }
static void watchdog_expired(void *context, freedv_watchdog_reason reason) {
    fd_voice_session *s=context;
    fd_session_abort(s, reason==FREEDV_WATCHDOG_TIMEOUT ? (fd_abort_reason)atomic_load(&s->watchdog_reason) : FD_ABORT_DEVICE);
}
static void wake(fd_voice_session *s) { pthread_cond_broadcast(&s->changed); }
static void invalidate(fd_voice_session *s) {
    for (unsigned i=0; i<FD_PTT_SOURCES; ++i) {
        s->released[i] = false;
        s->invalid_sequence[i] = s->edge_sequence[i];
    }
}
static void fault(fd_voice_session *s, fd_abort_reason reason) {
    /* No coordinator mutex, codec progress or device drain precedes RF clear. */
    atomic_store(&s->cancel,true);
    s->driver.emergency_clear(s->driver.context);
    freedv_watchdog_cancel(&s->watchdog);
    pthread_mutex_lock(&s->lock);
    if (s->status.fault == FD_ABORT_NONE) s->status.fault = reason;
    if(reason==FD_ABORT_DEVICE)s->prepared=false; /* an unusable stream needs exit/re-entry */
    s->status.state = FD_VS_FAULT; s->recovery = true; s->status.synchronized = false;
    s->clear_sequence = s->hardware.sequence; s->clear_time = now();
    invalidate(s); pthread_mutex_unlock(&s->lock);
}
static void operation_failed(fd_voice_session *s) {
    unsigned reason=atomic_exchange(&s->abort,FD_ABORT_NONE);
    fault(s,reason?(fd_abort_reason)reason:FD_ABORT_DEVICE);
}
static bool safe_rx(fd_voice_session *s, uint64_t time) {
    return fresh(s->hardware.valid,s->hardware.monotonic_ms,time) && !s->hardware.tx &&
        s->hardware.sequence > s->clear_sequence && s->hardware.monotonic_ms >= s->clear_time;
}
static bool all_released(fd_voice_session *s) {
    for (unsigned i=0;i<FD_PTT_SOURCES;++i)
        if (s->status.ptt_unknown[i] || s->held[i] || !s->released[i]) return false;
    return true;
}
static bool any_held(fd_voice_session *s) { return s->held[0] || s->held[1]; }
static bool health(fd_voice_session *s) {
    fd_voice_observation observation = {0}; bool sync=false,mic=false;
    bool ok=s->driver.observe(s->driver.context,&observation,&sync,&mic);
    uint64_t time=now(); pthread_mutex_lock(&s->lock);
    if (ok && observation.sequence > s->hardware.sequence) {
        for(unsigned i=0;i<FD_PTT_SOURCES;++i)
            if(observation.ptt[i].sequence<=s->hardware.ptt[i].sequence)
                observation.ptt[i]=s->hardware.ptt[i];
        s->hardware=observation;
    }
    s->status.synchronized = s->status.state==FD_VS_RX && sync;
    s->status.microphone_active = (s->status.state==FD_VS_TX || s->status.state==FD_VS_TX_STARTING) && mic;
    bool good=fresh(ok && s->hardware.valid,s->hardware.monotonic_ms,time);
    for(unsigned i=0;i<FD_PTT_SOURCES;++i) {
        fd_ptt_observation p=ok?s->hardware.ptt[i]:(fd_ptt_observation){0};
        bool known=fresh(p.valid,p.monotonic_ms,time) && p.sequence>=s->edge_sequence[i];
        s->status.ptt_unknown[i]=!known;
        if(known) {
            s->edge_sequence[i]=p.sequence;s->held[i]=p.pressed;
            if(!p.pressed && p.sequence>s->invalid_sequence[i])s->released[i]=true;
        }
        s->status.ptt_held[i]=s->held[i]; good &= known;
    }
    good &= !s->hardware.protection && !s->hardware.device_error;
    pthread_mutex_unlock(&s->lock);return good;
}
static bool receive(fd_voice_session *s) {
    pthread_mutex_lock(&s->lock); uint64_t gen=++s->status.generation; pthread_mutex_unlock(&s->lock);
    return s->driver.fence(s->driver.context) && s->driver.receive(s->driver.context,gen);
}
static void *coordinate(void *arg) {
    fd_voice_session *s=arg; void *ctx=s->driver.context;
    for(;;) {
        pthread_mutex_lock(&s->lock);
        while(!s->shutdown && s->status.state==FD_VS_OFF && !s->enter)pthread_cond_wait(&s->changed,&s->lock);
        if(s->shutdown){pthread_mutex_unlock(&s->lock);break;}
        bool entering=s->enter;s->enter=false;pthread_mutex_unlock(&s->lock);
        if(entering) {
            s->driver.emergency_clear(ctx);
            bool ok=s->driver.prepare(ctx,s->profile,&s->cancel);
            pthread_mutex_lock(&s->lock);s->prepared=ok;s->clear_time=now();s->clear_sequence=s->hardware.sequence;
            s->status.state=FD_VS_WAIT_PTT_RELEASE;s->recovery=true;pthread_mutex_unlock(&s->lock);
            if(!ok && !atomic_load(&s->cancel))fault(s,FD_ABORT_DEVICE);
        }
        unsigned abort=atomic_exchange(&s->abort,FD_ABORT_NONE);
        if(abort)fault(s,(fd_abort_reason)abort);
        bool good=health(s);
        pthread_mutex_lock(&s->lock);
        fd_voice_state state=s->status.state; bool leaving=atomic_load(&s->leave);
        bool clear=safe_rx(s,now()), released=all_released(s), held=any_held(s);
        bool recovery=s->recovery, tuning=s->retune;
        fd_voice_tuning target=s->requested_tuning;
        bool expired=(state==FD_VS_TX || state==FD_VS_TX_STARTING || state==FD_VS_TX_DRAIN) && now()>=s->deadline;
        bool protection=s->hardware.protection;
        bool device_error=s->hardware.device_error;
        pthread_mutex_unlock(&s->lock);
        if(expired){fault(s,state==FD_VS_TX_DRAIN?FD_ABORT_DRAIN:FD_ABORT_TIMEOUT);continue;}
        if(!good && (state==FD_VS_RX || state==FD_VS_TX || state==FD_VS_TX_DRAIN || state==FD_VS_TX_STARTING)) {
            fault(s,device_error?FD_ABORT_DEVICE:protection?FD_ABORT_PROTECTION:FD_ABORT_HEALTH);continue;
        }
        if(leaving || recovery) {
            if(leaving && state!=FD_VS_EXITING) {
                atomic_store(&s->cancel,true);s->driver.emergency_clear(ctx);
                pthread_mutex_lock(&s->lock);
                s->status.state=FD_VS_EXITING;
                s->exit_unsafe=true;
                if(!s->recovery){s->clear_sequence=s->hardware.sequence;s->clear_time=now();s->recovery=true;invalidate(s);clear=false;released=false;}
                pthread_mutex_unlock(&s->lock);
            }
            if(!clear || !released) {
                /* Restoration can have completed between polls. A newly held
                 * or unknown control remutes analog routing immediately and
                 * starts another fresh-RX fence before any further restore. */
                if(leaving && !s->exit_unsafe){
                    s->driver.emergency_clear(ctx);
                    pthread_mutex_lock(&s->lock);s->clear_sequence=s->hardware.sequence;s->clear_time=now();invalidate(s);s->exit_unsafe=true;pthread_mutex_unlock(&s->lock);
                }
                pthread_mutex_lock(&s->lock);
                if(!leaving && s->status.state!=FD_VS_FAULT)s->status.state=FD_VS_WAIT_PTT_RELEASE;
                pthread_mutex_unlock(&s->lock);
            } else if(leaving) {
                s->exit_unsafe=false;
                if(s->driver.fence(ctx) && s->driver.restore(ctx)) {
                    if (!s->driver.close(ctx)) { struct timespec retry={0,5000000}; nanosleep(&retry,NULL); continue; }
                    pthread_mutex_lock(&s->lock);s->status.state=FD_VS_OFF;s->prepared=false;s->recovery=false;atomic_store(&s->active,false);pthread_mutex_unlock(&s->lock);
                }
            } else if(s->prepared) {
                atomic_store(&s->cancel,false);
                if(receive(s)) {
                    pthread_mutex_lock(&s->lock);s->status.state=FD_VS_RX;s->status.fault=FD_ABORT_NONE;s->recovery=false;pthread_mutex_unlock(&s->lock);
                } else fault(s,FD_ABORT_DEVICE);
            }
        } else if(state==FD_VS_RX && tuning) {
            pthread_mutex_lock(&s->lock);s->status.state=FD_VS_PREPARING;s->retune=false;pthread_mutex_unlock(&s->lock);
            bool ok=s->driver.fence(ctx) && s->driver.retune(ctx,&target,s->profile) && receive(s);
            pthread_mutex_lock(&s->lock);if(ok){s->status.tuning=target;s->status.synchronized=false;s->status.state=FD_VS_RX;}pthread_mutex_unlock(&s->lock);
            if(!ok)operation_failed(s);
        } else if(state==FD_VS_RX && held) {
            pthread_mutex_lock(&s->lock);
            /* Each source must have a release observation after entry/recovery. */
            bool armed=true;for(unsigned i=0;i<FD_PTT_SOURCES;++i)if(s->held[i] && !s->released[i])armed=false;
            s->status.state=armed?FD_VS_TX_STARTING:FD_VS_WAIT_PTT_RELEASE;
            uint64_t gen=++s->status.generation;s->deadline=now()+(uint64_t)s->status.timeout_seconds*1000;
            pthread_mutex_unlock(&s->lock);
            if(armed) {
                atomic_store(&s->watchdog_reason,FD_ABORT_TIMEOUT);
                if(!freedv_watchdog_arm_voice(&s->watchdog,s->status.timeout_seconds*1000u)){fault(s,FD_ABORT_DEVICE);continue;}
                bool ok=s->driver.fence(ctx) && s->driver.transmit(ctx,gen,&s->cancel);
                if(!ok || atomic_load(&s->cancel))operation_failed(s);
                else {pthread_mutex_lock(&s->lock);s->status.state=FD_VS_TX;++s->status.turns;pthread_mutex_unlock(&s->lock);}
            }
        } else if(state==FD_VS_TX && !held) {
            pthread_mutex_lock(&s->lock);s->status.state=FD_VS_TX_DRAIN;s->deadline=now()+DRAIN_MS;uint64_t gen=s->status.generation;pthread_mutex_unlock(&s->lock);
            freedv_watchdog_cancel(&s->watchdog);
            atomic_store(&s->watchdog_reason,FD_ABORT_DRAIN);
            if(!freedv_watchdog_arm_voice(&s->watchdog,DRAIN_MS) || !s->driver.finish(ctx,gen))operation_failed(s);
        } else if(state==FD_VS_TX_DRAIN) {
            int drained=s->driver.drained(ctx);
            if(drained<0)fault(s,FD_ABORT_DEVICE);
            else if(drained) {
                freedv_watchdog_cancel(&s->watchdog);
                s->driver.emergency_clear(ctx);
                pthread_mutex_lock(&s->lock);s->clear_sequence=s->hardware.sequence;s->clear_time=now();s->status.state=FD_VS_PREPARING;pthread_mutex_unlock(&s->lock);
            }
        } else if(state==FD_VS_PREPARING && clear) {
            /* A re-press held through drain is considered only after confirmed RX. */
            if(receive(s)){pthread_mutex_lock(&s->lock);s->status.state=FD_VS_RX;pthread_mutex_unlock(&s->lock);}
            else fault(s,FD_ABORT_DEVICE);
        }
        pthread_mutex_lock(&s->lock);
        struct timespec t;clock_gettime(CLOCK_MONOTONIC,&t);t.tv_nsec+=5000000;
        if(t.tv_nsec>=1000000000){++t.tv_sec;t.tv_nsec-=1000000000;}
        pthread_cond_timedwait(&s->changed,&s->lock,&t);pthread_mutex_unlock(&s->lock);
    }
    return NULL;
}
fd_voice_session *fd_session_create(const fd_voice_driver *d) {
    if(!d || !d->prepare || !d->fence || !d->receive || !d->transmit || !d->finish ||
        !d->drained || !d->emergency_clear || !d->retune || !d->restore || !d->close || !d->observe)return NULL;
    fd_voice_session *s=calloc(1,sizeof(*s));if(!s)return NULL;s->driver=*d;
    if(pthread_mutex_init(&s->lock,NULL)){free(s);return NULL;}
    pthread_condattr_t a;pthread_condattr_init(&a);pthread_condattr_setclock(&a,CLOCK_MONOTONIC);
    int rc=pthread_cond_init(&s->changed,&a);pthread_condattr_destroy(&a);
    if(rc){pthread_mutex_destroy(&s->lock);free(s);return NULL;}
    atomic_init(&s->cancel,true);atomic_init(&s->leave,false);atomic_init(&s->active,false);atomic_init(&s->abort,FD_ABORT_NONE);atomic_init(&s->watchdog_reason,FD_ABORT_TIMEOUT);
    if(!freedv_watchdog_open(&s->watchdog,watchdog_expired,s)){pthread_cond_destroy(&s->changed);pthread_mutex_destroy(&s->lock);free(s);return NULL;}
    if(pthread_create(&s->worker,NULL,coordinate,s)){freedv_watchdog_close(&s->watchdog);pthread_cond_destroy(&s->changed);pthread_mutex_destroy(&s->lock);free(s);return NULL;}
    return s;
}
bool fd_session_enter(fd_voice_session *s,const fd_qualified_profile *p,const char *hw,const char *build,fd_voice_tuning tuning,unsigned timeout) {
    if(!s || !fd_profile_valid(p,hw,build) || timeout<30 || timeout>600 || tuning.frequency<=0 || tuning.vfo>1)return false;
    pthread_mutex_lock(&s->lock);bool ok=s->status.state==FD_VS_OFF && !s->enter;
    if(ok){s->profile=p;s->status=(fd_voice_status){.state=FD_VS_PREPARING,.timeout_seconds=timeout,.tuning=tuning,.generation=s->status.generation};
        memset(&s->hardware,0,sizeof(s->hardware));memset(s->edge_sequence,0,sizeof(s->edge_sequence));memset(s->invalid_sequence,0,sizeof(s->invalid_sequence));
        memset(s->held,0,sizeof(s->held));memset(s->released,0,sizeof(s->released));
        s->enter=true;atomic_store(&s->active,true);s->retune=false;s->recovery=false;atomic_store(&s->leave,false);atomic_store(&s->cancel,false);wake(s);}
    pthread_mutex_unlock(&s->lock);return ok;
}
bool fd_session_ptt(fd_voice_session *s,fd_ptt_source source,bool pressed,uint64_t sequence) {
    if(!s || source<FD_PTT_RADIO || source>=FD_PTT_SOURCES || !sequence)return false;
    pthread_mutex_lock(&s->lock);bool ok=s->status.state!=FD_VS_OFF && sequence>s->edge_sequence[source];
    if(ok){s->edge_sequence[source]=sequence;s->held[source]=pressed;
        s->hardware.ptt[source]=(fd_ptt_observation){true,pressed,sequence,now()};
        if(!pressed && sequence>s->invalid_sequence[source])s->released[source]=true;wake(s);}
    pthread_mutex_unlock(&s->lock);return ok;
}
bool fd_session_retune(fd_voice_session *s,fd_voice_tuning tuning) {
    if(!s || tuning.frequency<=0 || tuning.vfo>1)return false;
    pthread_mutex_lock(&s->lock);bool ok=s->status.state==FD_VS_RX && !s->retune && !any_held(s);
    if(ok){s->requested_tuning=tuning;s->retune=true;wake(s);}pthread_mutex_unlock(&s->lock);return ok;
}
bool fd_session_timeout(fd_voice_session *s,unsigned seconds) {
    if(!s || seconds<30 || seconds>600)return false;
    pthread_mutex_lock(&s->lock);
    bool ok=s->status.state==FD_VS_RX && !s->retune && !any_held(s) && !atomic_load(&s->leave);
    if(ok)s->status.timeout_seconds=seconds;
    pthread_mutex_unlock(&s->lock);return ok;
}
void fd_session_abort(fd_voice_session *s,fd_abort_reason reason) {
    if(!s || reason==FD_ABORT_NONE || !atomic_load(&s->active))return;
    atomic_store(&s->cancel,true);
    unsigned expected=FD_ABORT_NONE;atomic_compare_exchange_strong(&s->abort,&expected,reason);
    s->driver.emergency_clear(s->driver.context);
}
void fd_session_leave(fd_voice_session *s) {if(s && atomic_load(&s->active)){atomic_store(&s->leave,true);atomic_store(&s->cancel,true);s->driver.emergency_clear(s->driver.context);freedv_watchdog_cancel(&s->watchdog);}}
fd_voice_status fd_session_observe(fd_voice_session *s) {
    if(!s)return(fd_voice_status){0};pthread_mutex_lock(&s->lock);fd_voice_status status=s->status;
    status.exit_requested=atomic_load(&s->leave);pthread_mutex_unlock(&s->lock);return status;
}
bool fd_session_destroy(fd_voice_session *s) {
    if(!s)return true;pthread_mutex_lock(&s->lock);
    if(s->status.state!=FD_VS_OFF || s->enter){pthread_mutex_unlock(&s->lock);return false;}
    s->shutdown=true;wake(s);pthread_mutex_unlock(&s->lock);pthread_join(s->worker,NULL);freedv_watchdog_close(&s->watchdog);
    pthread_cond_destroy(&s->changed);pthread_mutex_destroy(&s->lock);free(s);return true;
}
const char *fd_voice_state_name(fd_voice_status s) {
    switch(s.state){case FD_VS_OFF:return "Off";case FD_VS_PREPARING:return "Preparing";
    case FD_VS_RX:return s.synchronized?"Synchronized":"Receiving";case FD_VS_TX_STARTING:return "Preparing transmission";
    case FD_VS_TX:return "Transmitting";case FD_VS_TX_DRAIN:return "Finishing";case FD_VS_FAULT:return "Fault - release all PTT";
    case FD_VS_WAIT_PTT_RELEASE:return "Release PTT / awaiting PTT health";case FD_VS_EXITING:return "Exiting - awaiting fresh RX and released PTT";}
    return "Fault";
}
