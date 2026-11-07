// SPDX-License-Identifier: BSD-2-Clause
#define _POSIX_C_SOURCE 200809L
#include "../../src/freedv/voice_engine.h"
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <math.h>
static void pause_ms(void){struct timespec t={0,1000000};nanosleep(&t,NULL);}
static uint64_t now(void){struct timespec t;clock_gettime(CLOCK_MONOTONIC,&t);return (uint64_t)t.tv_sec*1000+t.tv_nsec/1000000;}
int main(int argc,char **argv) {
    unsigned turns=4,cycles=1,rate=8000;
    const char *outdir=NULL;
    for(int i=1;i<argc;++i){
        if(!strcmp(argv[i],"--turns") && i+1<argc)turns=(unsigned)atoi(argv[++i]);
        else if(!strcmp(argv[i],"--cycles") && i+1<argc)cycles=(unsigned)atoi(argv[++i]);
        else if(!strcmp(argv[i],"--rate") && i+1<argc)rate=(unsigned)atoi(argv[++i]);
        else if(!strcmp(argv[i],"--output") && i+1<argc)outdir=argv[++i];
        else return 2;
    }
    assert(turns && cycles && (rate==8000 || rate==44100 || rate==48000));
    for(unsigned cycle=0;cycle<cycles;++cycle) {
        fd_voice_engine *e=fd_voice_create(rate);assert(e);
        fd_voice_stats preparation=fd_voice_observe(e);
        assert(preparation.warmup_runs==1 && preparation.warmup_reused==(cycle!=0));
        uint64_t gen=0;
        for(unsigned turn=0;turn<turns;++turn) {
            assert(fd_voice_transition(e,FD_VOICE_TX,++gen));
            int16_t mic[480],output[960];
            /* Distinct synthetic tones exercise generations; they do NOT meet
             * the numbered-speech/intelligibility acceptance gate. */
            size_t count=turn%4==0?rate/100:rate/5;
            for(size_t offset=0;offset<count;) {
                size_t n=count-offset;if(n>rate/100)n=rate/100;
                for(size_t i=0;i<n;++i)mic[i]=(int16_t)(3000*sin(2*3.141592653589793*(200+turn*17)*(offset+i)/rate));
                assert(!fd_voice_submit(e,gen-1,mic,n));
                assert(fd_voice_submit(e,gen,mic,n)==n);offset+=n;
            }
            assert(fd_voice_finish(e,gen));assert(fd_voice_finish(e,gen));
            size_t samples=0;uint64_t deadline=now()+15000;FILE *f=NULL;
            if(outdir){char name[1024];snprintf(name,sizeof(name),"%s/turn-%03u-%03u-s16le.raw",outdir,cycle,turn);f=fopen(name,"wb");assert(f);}
            for(;;) {
                size_t n=fd_voice_receive(e,gen,output,960);samples+=n;
                if(f)assert(fwrite(output,sizeof(*output),n,f)==n);
                fd_voice_stats s=fd_voice_observe(e);assert(!s.failures);
                if(s.finished && !s.output_depth)break;
                assert(now()<deadline);pause_ms();
            }
            if(f)assert(!fclose(f));assert(samples>rate/5);
            fd_voice_stats s=fd_voice_observe(e);
            assert(s.eoo_count==turn+1 && s.rade_creations==1 && s.encoder_creations==1 &&
                s.decoder_creations==1 && s.resampler_creations==4 && s.worker_creations==2);
            assert(fd_voice_transition(e,FD_VOICE_RX,++gen));
            assert(!fd_voice_receive(e,gen,output,960)); /* previous tail discarded */
            assert(!fd_voice_finish(e,gen));
            memset(mic,0,sizeof(mic));assert(fd_voice_submit(e,gen,mic,rate/100)==rate/100);
            fd_voice_cancel(e);
            assert(!fd_voice_receive(e,gen,output,960));assert(!fd_voice_submit(e,gen,mic,1));
        }
        fd_voice_destroy(e);
    }
    printf("PASS: %u synthetic alternating turns x %u lifecycles, rate=%u; speech assessment INCONCLUSIVE\n",turns,cycles,rate);
}
