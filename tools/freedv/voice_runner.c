// SPDX-License-Identifier: BSD-2-Clause
#define _POSIX_C_SOURCE 200809L
#include "../../src/freedv/voice_engine.h"
/* This operator runner uses checked operations, including I/O and transitions.
 * They must execute in release/SDK builds as well as host test builds. */
#undef NDEBUG
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
static uint64_t ns(void){struct timespec t;clock_gettime(CLOCK_MONOTONIC,&t);return(uint64_t)t.tv_sec*1000000000+t.tv_nsec;}
static void tick(void){struct timespec t={0,1000000};nanosleep(&t,NULL);}
static size_t drain(fd_voice_engine *e,uint64_t gen,FILE *f){int16_t out[4096];size_t n=fd_voice_receive(e,gen,out,4096);if(n)assert(fwrite(out,sizeof(*out),n,f)==n);return n;}
static void report(fd_voice_engine *e,FILE *f){
    fd_voice_stats s=fd_voice_observe(e);
    fprintf(f,"{\"generation\":%llu,\"turns\":%llu,\"speech_frames\":%llu,\"modem_blocks\":%llu,\"eoo_count\":%llu,\"input_high_water\":%u,\"output_high_water\":%u,\"failures\":%u,",
        (unsigned long long)s.generation,(unsigned long long)s.turns,(unsigned long long)s.speech_frames,(unsigned long long)s.modem_blocks,(unsigned long long)s.eoo_count,s.input_high_water,s.output_high_water,s.failures);
    fprintf(f,"\"rade_creations\":%u,\"encoder_creations\":%u,\"decoder_creations\":%u,\"resampler_creations\":%u,\"worker_creations\":%u,",
        s.rade_creations,s.encoder_creations,s.decoder_creations,s.resampler_creations,s.worker_creations);
    fprintf(f,"\"synchronized_blocks\":%llu,\"synthesis_blocks\":%llu,\"acquisitions\":%llu,",
        (unsigned long long)s.synchronized_blocks,(unsigned long long)s.synthesis_blocks,(unsigned long long)s.acquisitions);
    fprintf(f,"\"processing_ns\":%llu,\"synthesis_ns\":%llu,\"queue_wait_ns\":%llu,\"scheduling_delay_ns\":%llu,\"scheduling_max_ns\":%llu,\"scheduling_events\":%llu,\"duration_histogram\":[",
        (unsigned long long)s.processing_ns,(unsigned long long)s.synthesis_ns,(unsigned long long)s.queue_wait_ns,
        (unsigned long long)s.scheduling_delay_ns,(unsigned long long)s.scheduling_max_ns,(unsigned long long)s.scheduling_events);
    for(unsigned i=0;i<32;++i)fprintf(f,"%s%llu",i?",":"",(unsigned long long)s.duration_histogram[i]);
    fputs("],\"intelligibility\":\"UNASSESSED\",\"hardware_qualified\":false}\n",f);
}
/* Input/output paths are explicit; no production per-turn recording. Every raw
 * file is signed 16-bit mono PCM at the chosen audio boundary rate. */
static void feed(fd_voice_engine *e,uint64_t gen,FILE *input,FILE *output,unsigned rate,uint64_t samples,bool loop,bool paced){
    uint64_t count=0,start=ns(),progress=ns();int16_t pcm[480];
    while(count<samples){
        while(drain(e,gen,output))progress=ns();
        fd_voice_stats s=fd_voice_observe(e);assert(!s.failures && !s.cancelled);
        if(s.input_depth>rate/5 || (paced && ns()-start<count*1000000000/rate)){tick();assert(ns()-progress<15000000000ull);continue;}
        size_t wanted=rate/100;if(wanted>samples-count)wanted=(size_t)(samples-count);
        size_t n=fread(pcm,sizeof(*pcm),wanted,input);
        if(!n){assert(!ferror(input));if(!loop)break;rewind(input);n=fread(pcm,sizeof(*pcm),wanted,input);assert(n);}
        assert(fd_voice_submit(e,gen,pcm,n)==n);count+=n;progress=ns();
    }
}
int main(int argc,char **argv){
    const char *speech_list=NULL,*rx=NULL,*outdir=NULL;unsigned rate=8000,turns=100,seconds=600;bool paced=true;
    for(int i=1;i<argc;++i){
        if(!strcmp(argv[i],"--speech-list") && i+1<argc)speech_list=argv[++i];
        else if(!strcmp(argv[i],"--rx") && i+1<argc)rx=argv[++i];
        else if(!strcmp(argv[i],"--output") && i+1<argc)outdir=argv[++i];
        else if(!strcmp(argv[i],"--rate") && i+1<argc)rate=(unsigned)atoi(argv[++i]);
        else if(!strcmp(argv[i],"--turns") && i+1<argc)turns=(unsigned)atoi(argv[++i]);
        else if(!strcmp(argv[i],"--seconds") && i+1<argc)seconds=(unsigned)atoi(argv[++i]);
        else if(!strcmp(argv[i],"--unpaced"))paced=false;else return 2;
    }
    if(!outdir || (!!speech_list==!!rx) || !seconds || !turns)return 2;
    fd_voice_engine *e=fd_voice_create(rate);assert(e);uint64_t gen=0;char path[2048];
    if(rx){
        FILE *in=fopen(rx,"rb");assert(in);snprintf(path,sizeof(path),"%s/decoded-s16le.raw",outdir);FILE *out=fopen(path,"wb");assert(out);
        assert(fd_voice_transition(e,FD_VOICE_RX,++gen));feed(e,gen,in,out,rate,(uint64_t)seconds*rate,true,paced);
        uint64_t until=ns()+500000000;while(ns()<until){while(drain(e,gen,out)){}tick();}
        assert(!fclose(in) && !fclose(out));
    }else{
        FILE *list=fopen(speech_list,"r");assert(list);char input_path[1024];
        for(unsigned turn=0;turn<turns;++turn){
            assert(fgets(input_path,sizeof(input_path),list));input_path[strcspn(input_path,"\r\n")]=0;
            FILE *in=fopen(input_path,"rb");assert(in);assert(!fseek(in,0,SEEK_END));long bytes=ftell(in);assert(bytes>0 && !(bytes%2));rewind(in);
            snprintf(path,sizeof(path),"%s/turn-%03u-modem-s16le.raw",outdir,turn);FILE *out=fopen(path,"wb");assert(out);
            assert(fd_voice_transition(e,FD_VOICE_TX,++gen));feed(e,gen,in,out,rate,(uint64_t)bytes/2,false,paced);assert(fd_voice_finish(e,gen));
            uint64_t deadline=ns()+15000000000ull;
            for(;;){while(drain(e,gen,out)){}fd_voice_stats s=fd_voice_observe(e);assert(!s.failures);if(s.finished && !s.output_depth)break;assert(ns()<deadline);tick();}
            assert(!fclose(in) && !fclose(out));
            /* Decode this exact output with the retained RX context. */
            in=fopen(path,"rb");assert(in);assert(!fseek(in,0,SEEK_END));bytes=ftell(in);rewind(in);
            snprintf(path,sizeof(path),"%s/turn-%03u-decoded-s16le.raw",outdir,turn);out=fopen(path,"wb");assert(out);
            assert(fd_voice_transition(e,FD_VOICE_RX,++gen));feed(e,gen,in,out,rate,(uint64_t)bytes/2,false,paced);
            int16_t zero[480]={0};
            for(unsigned tail=0;tail<100;++tail){while(drain(e,gen,out)){}fd_voice_stats s=fd_voice_observe(e);if(s.input_depth<rate/5){assert(fd_voice_submit(e,gen,zero,rate/100)==rate/100);}tick();}
            while(drain(e,gen,out)){}assert(!fclose(in) && !fclose(out));
        }
        fclose(list);
    }
    snprintf(path,sizeof(path),"%s/engine-stats.json",outdir);FILE *f=fopen(path,"w");assert(f);report(e,f);fclose(f);
    fd_voice_destroy(e);puts("PASS: runner completed; listening acceptance UNASSESSED; RF qualification unavailable");return 0;
}
