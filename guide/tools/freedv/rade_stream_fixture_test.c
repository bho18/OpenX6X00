/* SPDX-License-Identifier: BSD-2-Clause
 * Unpaced correctness only: whole-fixture conversion is confined to this test.
 * The firmware adapter uses a bounded streaming file reader instead. */
#define _POSIX_C_SOURCE 200809L
#include "rade_stream.h"
#include <samplerate.h>
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <time.h>
#include <string.h>
static uint64_t millis(void){struct timespec t;clock_gettime(CLOCK_MONOTONIC,&t);return(uint64_t)t.tv_sec*1000+t.tv_nsec/1000000;}
static void pause1(void){struct timespec t={0,1000000};nanosleep(&t,NULL);}
static void drain(fd_rade_stream *s,uint64_t *samples,uint64_t *nonzero){
    int16_t pcm[4410];size_t n=fd_stream_receive(s,42,pcm,4410);*samples+=n;
    for(size_t i=0;i<n;++i)*nonzero+=pcm[i]!=0;
}
int main(int argc,char **argv){
    bool interrupt=argc==3&&!strcmp(argv[2],"--interrupt");assert(argc==2||interrupt);FILE *file=fopen(argv[1],"rb");assert(file);
    assert(!fseek(file,0,SEEK_END));long bytes=ftell(file);assert(bytes>0&&bytes<=32*1024*1024&&!(bytes%2));rewind(file);
    size_t n=(size_t)bytes/2;short *input=malloc((size_t)bytes);float *native=malloc(n*sizeof(float));
    size_t capacity=n*6+512;float *converted=calloc(capacity,sizeof(float));short *fixture=calloc(capacity,sizeof(short));
    assert(input&&native&&converted&&fixture&&fread(input,2,n,file)==n);fclose(file);
    src_short_to_float_array(input,native,(int)n);
    SRC_DATA data={.data_in=native,.input_frames=(long)n,.data_out=converted,.output_frames=(long)capacity,.src_ratio=44100./8000.,.end_of_input=1};
    assert(!src_simple(&data,SRC_SINC_FASTEST,1)&&data.output_frames_gen>0);
    size_t count=(size_t)data.output_frames_gen;src_float_to_short_array(converted,fixture,(int)count);
    fd_rade_stream *s=fd_stream_prepare(FD_STREAM_RX,42);assert(s&&fd_stream_start(s));
    uint64_t output_samples=0,nonzero=0,interrupted=0,recovery_features=0;size_t cursor=0;
    for(unsigned chunk=0;chunk<(interrupt?1400u:600u);++chunk){
        if(interrupt&&chunk==1200)recovery_features=fd_stream_observe(s).feature_blocks;
        short pcm[441];for(unsigned i=0;i<441;++i){pcm[i]=fixture[cursor++];if(cursor==count)cursor=0;if(interrupt&&((chunk*441u+i)/44100u)%12>=10){pcm[i]=0;++interrupted;}}
        assert(fd_stream_submit(s,42,pcm,441)==441);
        uint64_t deadline=millis()+60000;
        for(;;){
            drain(s,&output_samples,&nonzero);fd_stream_stats stats=fd_stream_observe(s);assert(!stats.failures);
            if(!stats.input_depth&&stats.rendered_blocks==stats.blocks)break;
            assert(millis()<deadline);pause1();
        }
    }
    uint64_t deadline=millis()+1000;
    while(millis()<deadline){drain(s,&output_samples,&nonzero);pause1();}
    fd_stream_stats stats=fd_stream_observe(s);
    assert(!stats.failures&&stats.blocks>30&&stats.synchronized_blocks>10&&stats.feature_blocks>10);
    assert(output_samples>44100&&nonzero>22050&&stats.feature_max_depth<=2);
    if(interrupt)assert(interrupted==88200&&stats.feature_blocks>recovery_features);
    fd_stream_abort(s);fd_stream_destroy(s);free(input);free(native);free(converted);free(fixture);
    if(interrupt)printf("PASS: deterministic interruption with88200 zeroed samples, features recovered=%llu\n",(unsigned long long)(stats.feature_blocks-recovery_features));
    printf("PASS: synchronized full streaming RX through FARGAN/resampling; blocks=%llu sync=%llu features=%llu output=%llu nonzero=%llu; hardware_qualified=false\n",
        (unsigned long long)stats.blocks,(unsigned long long)stats.synchronized_blocks,(unsigned long long)stats.feature_blocks,
        (unsigned long long)output_samples,(unsigned long long)nonzero);
}
