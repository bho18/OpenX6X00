// SPDX-License-Identifier: LGPL-2.1-or-later
#define _POSIX_C_SOURCE 200809L
#include "native_trace.h"
#include <pthread.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#define TRACE_ROWS 2048
typedef struct {
    uint64_t ns;
    enum { TRANSITION, COMMAND, AUDIO, CONTROL } kind;
    union {
        struct {fd_voice_status s; fd_voice_observation o;} transition;
        struct {uint64_t generation; bool modem,ptt;} command;
        struct {
            char operation[32], contract_id[128];
            uint64_t lease_token, generation;
            hw_native_voice_route_t requested_route;
            bool observation_present;
            hw_native_voice_state_t observed;
        } control;
        fd_native_audio_trace audio;
    } data;
} row;
static pthread_mutex_t lock=PTHREAD_MUTEX_INITIALIZER;
static row rows[TRACE_ROWS];
static unsigned count,discarded;
static uint64_t session_id,last_audio_ns;
static bool active,simulation,have_status;
static fd_voice_status previous_status;
static fd_voice_observation previous_observation;
static char identity[6][128];
static fd_profile_audio_map rx_audio, tx_audio;
static unsigned sample_rate;

static uint64_t clock_ns(void) {
    struct timespec t;clock_gettime(CLOCK_MONOTONIC,&t);
    return (uint64_t)t.tv_sec*1000000000u+t.tv_nsec;
}
static row *next_row(void) {
    if(!active)return NULL;
    if(count==TRACE_ROWS){++discarded;return NULL;}
    row *r=&rows[count++];memset(r,0,sizeof(*r));r->ns=clock_ns();return r;
}
void fd_native_trace_begin(const fd_qualified_profile *p,bool simulated) {
    pthread_mutex_lock(&lock);
    count=discarded=0;last_audio_ns=0;have_status=false;simulation=simulated;
    uint64_t stamp=clock_ns();session_id=stamp>session_id?stamp:session_id+1;
    const char *fields[]={p?p->id:NULL,p?p->hardware:NULL,p?p->build_id:NULL,
        p?p->baseband:NULL,p?p->control_version:NULL,p?p->native_contract_id:NULL};
    for(unsigned i=0;i<6;++i)snprintf(identity[i],sizeof(identity[i]),"%s",fields[i]?fields[i]:"");
    rx_audio=p?p->rx_audio:(fd_profile_audio_map){0};
    tx_audio=p?p->tx_audio:(fd_profile_audio_map){0};sample_rate=p?p->sample_rate:0;
    active=true;pthread_mutex_unlock(&lock);
}
static bool same_state(const fd_voice_status *s,const fd_voice_observation *o) {
    if(!have_status)return false;
    if(s->state!=previous_status.state || s->fault!=previous_status.fault ||
       s->generation!=previous_status.generation || s->turns!=previous_status.turns ||
       s->bench_grant!=previous_status.bench_grant || s->grants_consumed!=previous_status.grants_consumed ||
       s->synchronized!=previous_status.synchronized || s->exit_requested!=previous_status.exit_requested ||
       o->valid!=previous_observation.valid || o->tx!=previous_observation.tx ||
       o->protection!=previous_observation.protection || o->device_error!=previous_observation.device_error)return false;
    for(unsigned i=0;i<FD_PTT_SOURCES;++i)
        if(s->ptt_unknown[i]!=previous_status.ptt_unknown[i] ||
           s->ptt_held[i]!=previous_status.ptt_held[i] ||
           o->ptt[i].valid!=previous_observation.ptt[i].valid ||
           o->ptt[i].release_sequence!=previous_observation.ptt[i].release_sequence)return false;
    return true;
}
void fd_native_trace_note(const fd_voice_status *s,const fd_voice_observation *o) {
    if(!s || !o)return;
    pthread_mutex_lock(&lock);
    if(!same_state(s,o)){
        row *r=next_row();if(r){r->kind=TRANSITION;r->data.transition.s=*s;r->data.transition.o=*o;}
        previous_status=*s;previous_observation=*o;have_status=true;
    }
    pthread_mutex_unlock(&lock);
}
void fd_native_trace_command(uint64_t generation,bool modem,bool ptt) {
    pthread_mutex_lock(&lock);row *r=next_row();
    if(r){r->kind=COMMAND;r->data.command.generation=generation;r->data.command.modem=modem;r->data.command.ptt=ptt;}
    pthread_mutex_unlock(&lock);
}
void fd_native_trace_control(const char *operation,uint64_t lease_token,uint64_t generation,
        hw_native_voice_route_t requested_route,const hw_native_voice_state_t *observed) {
    pthread_mutex_lock(&lock);row *r=next_row();
    if(r){
        r->kind=CONTROL;
        snprintf(r->data.control.operation,sizeof(r->data.control.operation),"%s",operation?operation:"");
        r->data.control.lease_token=lease_token;r->data.control.generation=generation;
        r->data.control.requested_route=requested_route;r->data.control.observation_present=observed!=NULL;
        if(observed){
            r->data.control.observed=*observed;
            snprintf(r->data.control.contract_id,sizeof(r->data.control.contract_id),"%s",observed->contract_id?observed->contract_id:"");
            r->data.control.observed.contract_id=NULL; /* no borrowed pointer in retained evidence */
        }
    }
    pthread_mutex_unlock(&lock);
}
void fd_native_trace_audio(const fd_native_audio_trace *a) {
    if(!a)return;
    pthread_mutex_lock(&lock);
    uint64_t stamp=clock_ns();
    if(!last_audio_ns || stamp-last_audio_ns>=1000000000u || a->failed){
        row *r=next_row();if(r){r->kind=AUDIO;r->data.audio=*a;}last_audio_ns=stamp;
    }
    pthread_mutex_unlock(&lock);
}
static const char *boolean(bool value){return value?"true":"false";}
static void quoted(FILE *out,const char *value) {
    fputc('"',out);
    for(const unsigned char *p=(const unsigned char *)value;*p;++p){
        if(*p=='"' || *p=='\\'){fputc('\\',out);fputc(*p,out);}
        else if(*p<32)fprintf(out,"\\u%04x",*p);
        else fputc(*p,out);
    }
    fputc('"',out);
}
bool fd_native_trace_export(FILE *out) {
    if(!out)return false;
    row *copy=malloc(sizeof(rows));if(!copy)return false;
    char ids[6][128];unsigned n,lost,rate;uint64_t id;bool simulated,started;
    fd_profile_audio_map rx,tx;
    pthread_mutex_lock(&lock);n=count;lost=discarded;id=session_id;simulated=simulation;started=active;
    memcpy(copy,rows,n*sizeof(*copy));memcpy(ids,identity,sizeof(ids));
    rx=rx_audio;tx=tx_audio;rate=sample_rate;pthread_mutex_unlock(&lock);
    if(!started){free(copy);return false;}
    fprintf(out,"{\"schema\":1,\"kind\":\"native_session\",\"session_id\":\"%llu\",\"simulation\":%s,\"profile\":",(unsigned long long)id,boolean(simulated));
    quoted(out,ids[0]);fputs(",\"hardware\":",out);quoted(out,ids[1]);
    fputs(",\"build_id\":",out);quoted(out,ids[2]);fputs(",\"baseband\":",out);quoted(out,ids[3]);
    fputs(",\"control_version\":",out);quoted(out,ids[4]);
    fputs(",\"native_contract_id\":",out);quoted(out,ids[5]);
    fprintf(out,",\"audio_slots\":{\"sample_rate\":%u,\"rx\":{\"capture\":%u,\"playback\":%u},"
                "\"tx\":{\"capture\":%u,\"playback\":%u},\"physical_endpoint_mapping_proven\":false}",
                rate,rx.capture_channel,rx.playback_channel,tx.capture_channel,tx.playback_channel);
    fputs(",\"physical_rf_evidence\":false,\"hardware_qualified\":false,"
          "\"observation_semantics\":{\"software_tx_command\":\"requested\","
          "\"aggregate_tx\":\"baseband_aggregate_telemetry\","
          "\"aggregate_tx_proves_rf_off\":false,\"aggregate_tx_proves_ptt_release\":false,"
          "\"host_cache_proves_route_applied\":false,\"i2c_presence_proves_native_lease\":false}}\n",out);
    for(unsigned i=0;i<n;++i){
        row *r=&copy[i];fprintf(out,"{\"monotonic_ns\":%llu,",(unsigned long long)r->ns);
        if(r->kind==TRANSITION){
            fd_voice_status *s=&r->data.transition.s;fd_voice_observation *o=&r->data.transition.o;
            fprintf(out,"\"kind\":\"transition\",\"state\":%u,\"fault\":%u,\"generation\":%llu,\"turns\":%llu,\"validation\":%s,\"grant\":%u,\"grants_consumed\":%llu,\"synchronized\":%s,\"aggregate_tx_valid\":%s,\"aggregate_tx\":%s,\"aggregate_sequence\":%llu,\"aggregate_stamp_ms\":%llu,\"protection\":%s,\"device_error\":%s,\"ptt\":[",
                s->state,s->fault,(unsigned long long)s->generation,(unsigned long long)s->turns,
                boolean(s->validation),s->bench_grant,(unsigned long long)s->grants_consumed,
                boolean(s->synchronized),boolean(o->valid),boolean(o->tx),(unsigned long long)o->sequence,
                (unsigned long long)o->monotonic_ms,boolean(o->protection),boolean(o->device_error));
            for(unsigned j=0;j<FD_PTT_SOURCES;++j)
                fprintf(out,"%s{\"source\":%u,\"known\":%s,\"held\":%s,\"valid\":%s,\"sequence\":%llu,\"stamp_ms\":%llu,\"release_sequence\":%llu}",
                    j?",":"",j,boolean(!s->ptt_unknown[j]),boolean(s->ptt_held[j]),boolean(o->ptt[j].valid),
                    (unsigned long long)o->ptt[j].sequence,(unsigned long long)o->ptt[j].monotonic_ms,(unsigned long long)o->ptt[j].release_sequence);
            fputs("]",out);
        }else if(r->kind==COMMAND){
            fprintf(out,"\"kind\":\"software_tx_command\",\"generation\":%llu,\"modem\":%s,\"ptt\":%s,\"physical_readback\":false,\"applied_acknowledgement\":false",
                (unsigned long long)r->data.command.generation,boolean(r->data.command.modem),boolean(r->data.command.ptt));
        }else if(r->kind==CONTROL){
            fputs("\"kind\":\"native_control\",\"operation\":",out);quoted(out,r->data.control.operation);
            fprintf(out,",\"requested\":{\"lease_token\":%llu,\"generation\":%llu,\"route\":%u},"
                        "\"physical_rf_evidence\":false,\"applied_state\":",
                        (unsigned long long)r->data.control.lease_token,
                        (unsigned long long)r->data.control.generation,r->data.control.requested_route);
            if(!r->data.control.observation_present)fputs("null",out);
            else{
                hw_native_voice_state_t *a=&r->data.control.observed;
                fprintf(out,"{\"valid\":%s,\"boot_id\":%llu,\"sequence\":%llu,\"stamp_ms\":%llu,"
                            "\"lease_token\":%llu,\"lease_deadline_ms\":%llu,\"generation\":%llu,\"route\":%u,"
                            "\"physical_keying_inhibited\":%s,\"autonomous_tx_inhibited\":%s,\"direct_mic_rf_inhibited\":%s,"
                            "\"raw_rx_speaker_inhibited\":%s,\"contract_id\":",
                            boolean(a->valid),(unsigned long long)a->boot_id,(unsigned long long)a->sequence,
                            (unsigned long long)a->monotonic_ms,(unsigned long long)a->lease_token,
                            (unsigned long long)a->lease_deadline_ms,(unsigned long long)a->route_generation,
                            a->applied_route,boolean(a->physical_keying_inhibited),boolean(a->autonomous_tx_inhibited),boolean(a->direct_mic_rf_inhibited),
                            boolean(a->raw_rx_speaker_inhibited));
                quoted(out,r->data.control.contract_id);fputc('}',out);
            }
        }else{
            fd_native_audio_trace *a=&r->data.audio;
            fprintf(out,"\"capture_channel\":%u,\"playback_channel\":%u,",a->capture_channel,a->playback_channel);
            fprintf(out,"\"adapter_instance\":%llu,\"engine_instance\":%llu,\"capture_instance\":%llu,\"playback_instance\":%llu,",
                (unsigned long long)a->adapter_instance,(unsigned long long)a->engine_instance,
                (unsigned long long)a->capture_instance,(unsigned long long)a->playback_instance);
            fprintf(out,"\"kind\":\"audio\",\"generation\":%llu,\"input_samples\":%llu,\"output_samples\":%llu,\"delivered_samples\":%llu,\"processing_max_ns\":%llu,\"synthesis_max_ns\":%llu,\"input_high_water\":%u,\"output_high_water\":%u,\"capture_queued\":%u,\"capture_dropped\":%u,\"capture_holes\":%u,\"capture_errors\":%u,\"playback_underflows\":%u,\"rade_creations\":%u,\"encoder_creations\":%u,\"decoder_creations\":%u,\"resampler_creations\":%u,\"worker_creations\":%u,\"capture_stream_creations\":%u,\"failed\":%s,\"cancelled\":%s,\"finished\":%s",
                (unsigned long long)a->generation,(unsigned long long)a->input_samples,(unsigned long long)a->output_samples,
                (unsigned long long)a->delivered_samples,(unsigned long long)a->processing_max_ns,(unsigned long long)a->synthesis_max_ns,
                a->input_high_water,a->output_high_water,a->capture_queued,a->capture_dropped,a->capture_holes,a->capture_errors,a->playback_underflows,
                a->rade_creations,a->encoder_creations,a->decoder_creations,a->resampler_creations,a->worker_creations,a->capture_stream_creations,
                boolean(a->failed),boolean(a->cancelled),boolean(a->finished));
        }
        fputs("}\n",out);
    }
    fprintf(out,"{\"kind\":\"trace_end\",\"complete\":%s,\"discarded\":%u,\"rows\":%u}\n",boolean(!lost),lost,n);
    free(copy);return !ferror(out);
}
