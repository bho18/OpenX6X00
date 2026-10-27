/* SPDX-License-Identifier: BSD-2-Clause */
/* Temporary host-only profiling interposer for the pinned single-threaded benchmark.
 * Measures inclusive wall time, including initialization/warm-up calls. No radio I/O.
 */
#define _GNU_SOURCE
#include <dlfcn.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <time.h>
#include "rade_api.h"
#include "rade_acq.h"
#include "rade_bpf.h"
#include "rade_core.h"

struct timing { const char *name; uint64_t calls, ns; };
static struct timing timings[] = {
    {"rade_rx",0,0}, {"rade_acq_detect_pilots",0,0}, {"rade_acq_refine",0,0},
    {"rade_acq_check_pilots",0,0}, {"rade_bpf_process",0,0}, {"rade_core_decoder",0,0}
};
static uint64_t now_ns(void) {
    struct timespec t;
    if(clock_gettime(CLOCK_MONOTONIC, &t)) abort();
    return (uint64_t)t.tv_sec*UINT64_C(1000000000)+(uint64_t)t.tv_nsec;
}
static void add(int i,uint64_t started) { timings[i].calls++; timings[i].ns += now_ns()-started; }
static void *symbol(const char *name) { void *p=dlsym(RTLD_NEXT,name); if(!p) { fprintf(stderr,"missing profile symbol %s\n",name); abort(); } return p; }
int rade_rx(struct rade *r,float *features,int *eoo,float *bits,RADE_COMP *input) {
    static int (*original)(struct rade*,float*,int*,float*,RADE_COMP*);
    if(!original) original=symbol("rade_rx");
    uint64_t t=now_ns(); int n=original(r,features,eoo,bits,input); add(0,t); return n;
}
int rade_acq_detect_pilots(rade_acq *a,const RADE_COMP *rx,int *t,float *f) {
    static int (*original)(rade_acq*,const RADE_COMP*,int*,float*);
    if(!original) original=symbol("rade_acq_detect_pilots");
    uint64_t start=now_ns(); int n=original(a,rx,t,f); add(1,start); return n;
}
void rade_acq_refine(rade_acq *a,const RADE_COMP *rx,int *t,float *f,int t0,int t1,float f0,float f1,float fs) {
    static void (*original)(rade_acq*,const RADE_COMP*,int*,float*,int,int,float,float,float);
    if(!original) original=symbol("rade_acq_refine");
    uint64_t start=now_ns(); original(a,rx,t,f,t0,t1,f0,f1,fs); add(2,start);
}
int rade_acq_check_pilots(rade_acq *a,const RADE_COMP *rx,int t,float f,int *valid,int *eoo) {
    static int (*original)(rade_acq*,const RADE_COMP*,int,float,int*,int*);
    if(!original) original=symbol("rade_acq_check_pilots");
    uint64_t start=now_ns(); int n=original(a,rx,t,f,valid,eoo); add(3,start); return n;
}
void rade_bpf_process(rade_bpf *b,RADE_COMP *y,const RADE_COMP *x,int n) {
    static void (*original)(rade_bpf*,RADE_COMP*,const RADE_COMP*,int);
    if(!original) original=symbol("rade_bpf_process");
    uint64_t start=now_ns(); original(b,y,x,n); add(4,start);
}
void rade_core_decoder(RADEDecState *s,const RADEDec *m,float *out,const float *in,int arch) {
    static void (*original)(RADEDecState*,const RADEDec*,float*,const float*,int);
    if(!original) original=symbol("rade_core_decoder");
    uint64_t start=now_ns(); original(s,m,out,in,arch); add(5,start);
}
__attribute__((destructor)) static void report(void) {
    const char *path=getenv("FREEDV_PROFILE_OUTPUT");
    if(!path) return;
    FILE *f=fopen(path,"wx");
    if(!f) { perror("profile output"); return; }
    fprintf(f,"{\"environment\":\"host-only LD_PRELOAD experiment\",\"includes_warmup\":true,\"inclusive_timings\":true,\"stages\":[");
    for(size_t i=0;i<sizeof(timings)/sizeof(timings[0]);++i)
        fprintf(f,"%s{\"name\":\"%s\",\"calls\":%llu,\"milliseconds\":%.6f}",i?",":"",timings[i].name,
                (unsigned long long)timings[i].calls,(double)timings[i].ns/1e6);
    fprintf(f,"]}\n"); fclose(f);
}
