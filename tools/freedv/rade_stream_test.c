/* SPDX-License-Identifier: BSD-2-Clause */
#define _POSIX_C_SOURCE 200809L
#include "rade_stream.h"
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <math.h>
#include <time.h>
static void pause10(void) { struct timespec t={0,10000000}; nanosleep(&t,NULL); }
int main(void) {
    int16_t input[441],output[4410];
    for (int i=0;i<441;++i) input[i]=(short)(8000*sin(i*0.13));
    for (unsigned turn=1;turn<=4;++turn) {
        fd_stream_direction direction=(turn%2)?FD_STREAM_TX:FD_STREAM_RX;
        fd_rade_stream *s=fd_stream_prepare(direction,turn); assert(s);
        assert(!fd_stream_submit(s,turn+100,input,441));
        assert(!fd_stream_receive(s,turn+100,output,441));
        assert(fd_stream_start(s)); assert(!fd_stream_start(s));
        uint64_t delivered=0;
        for (int i=0;i<200;++i) {
            if (direction==FD_STREAM_RX) for(int j=0;j<441;++j) input[j]=0;
            else for(int j=0;j<441;++j) input[j]=(short)(8000*sin((i*441+j)*0.13));
            assert(fd_stream_submit(s,turn,input,441)==441);
            delivered+=fd_stream_receive(s,turn,output,4410);
            pause10();
            assert(!fd_stream_observe(s).failures);
        }
        for(int i=0;i<30;++i) { delivered+=fd_stream_receive(s,turn,output,4410); pause10(); }
        fd_stream_stats stats=fd_stream_observe(s);
        assert(stats.blocks>10 && !stats.failures && stats.feature_max_depth<=2);
        if(direction==FD_STREAM_TX) assert(delivered>44100);
        else assert(!stats.synchronized_blocks && !delivered);
        fd_stream_abort(s);
        assert(!fd_stream_submit(s,turn,input,441));
        assert(!fd_stream_receive(s,turn,output,441));
        fd_stream_destroy(s);
    }
    fd_rade_stream *s=fd_stream_prepare(FD_STREAM_RX,5); assert(s);
    assert(fd_stream_submit(s,5,input,441)==441);
    /* Reject an oversized block without dereferencing beyond input. */
    assert(!fd_stream_submit(s,5,input,22051)); assert(fd_stream_observe(s).failures);
    fd_stream_destroy(s);
    puts("PASS: full bounded RADE TX/RX, three acquisition participants, turn generations, abort and backlog failure");
}
