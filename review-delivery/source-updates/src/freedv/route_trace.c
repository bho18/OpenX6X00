// SPDX-License-Identifier: LGPL-2.1-or-later
#define _POSIX_C_SOURCE 200809L
#include "route_trace.h"
#include <pthread.h>
#include <string.h>
#include <time.h>
/* No allocation, filesystem writes or unbounded history on control threads. */
static pthread_mutex_t lock=PTHREAD_MUTEX_INITIALIZER;
static struct row {uint64_t ns,generation;int command;uint32_t previous,requested;bool success;char phase[24];} rows[1024];
static unsigned count,discarded;
static uint64_t generation;
void fd_route_trace_begin(uint64_t value){pthread_mutex_lock(&lock);count=discarded=0;generation=value;pthread_mutex_unlock(&lock);}
void fd_route_trace_note(const char *phase,int cmd,uint32_t prev,uint32_t value,bool ok){
    struct timespec t;clock_gettime(CLOCK_MONOTONIC,&t);
    pthread_mutex_lock(&lock);
    if(count<1024){struct row *r=&rows[count++];*r=(struct row){.ns=(uint64_t)t.tv_sec*1000000000u+t.tv_nsec,.generation=generation,.command=cmd,.previous=prev,.requested=value,.success=ok};strncpy(r->phase,phase,sizeof(r->phase)-1);}
    else ++discarded;
    pthread_mutex_unlock(&lock);
}
void fd_route_trace_export(FILE *out){
    struct row copy[1024];unsigned n,d;
    pthread_mutex_lock(&lock);n=count;d=discarded;memcpy(copy,rows,n*sizeof(*rows));pthread_mutex_unlock(&lock);
    for(unsigned i=0;i<n;++i){
        struct row *r=&copy[i];
        bool command=!strcmp(r->phase,"command"),bulk=!strcmp(r->phase,"bulk_resend");
        fprintf(out,"{\"monotonic_ns\":%llu,\"generation\":%llu,\"kind\":\"%s\",\"phase\":\"%s\",\"command\":%d,\"cached_previous\":%u,\"requested\":%u,\"transport_success\":%s}\n",
            (unsigned long long)r->ns,(unsigned long long)r->generation,
            command?"control_write":bulk?"full_register_keepalive_or_explicit_resend":"route_phase",
            r->phase,r->command,r->previous,r->requested,(command||bulk)?(r->success?"true":"false"):"null");
    }
    fprintf(out,"{\"trace_complete\":%s,\"discarded\":%u,\"physical_readback\":false,\"scope\":\"case preparation through this export; inter-case idle excluded\"}\n",d?"false":"true",d);
}
