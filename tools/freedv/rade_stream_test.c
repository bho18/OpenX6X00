/* SPDX-License-Identifier: BSD-2-Clause */
#define _POSIX_C_SOURCE 200809L
#include "rade_stream.h"
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <math.h>
#include <time.h>
#include <string.h>
static void pause10(void) { struct timespec t={0,10000000}; nanosleep(&t,NULL); }
static uint64_t monotonic_ms(void) { struct timespec t; clock_gettime(CLOCK_MONOTONIC,&t); return (uint64_t)t.tv_sec*1000+t.tv_nsec/1000000; }
/* Exercise a producer burst below the 500ms input limit with its sink briefly
 * unavailable, without using host/QEMU speed as a performance qualification. */
static void tx_backpressure(bool abort_wait) {
    const uint64_t generation=abort_wait?91:92;
    fd_rade_stream *s=fd_stream_prepare(FD_STREAM_TX,generation);assert(s);
    assert(!fd_stream_tx_prefill_ready(s));
    short input[13230]={0},output[4410];
    assert(fd_stream_submit(s,generation,input,13230)==13230 && fd_stream_start(s));
    uint64_t deadline=monotonic_ms()+60000;
    while(!fd_stream_observe(s).output_wait_events) {
        assert(!fd_stream_observe(s).failures && monotonic_ms()<deadline);
        struct timespec t={0,1000000};nanosleep(&t,NULL);
    }
    fd_stream_stats st=fd_stream_observe(s);
    assert(!st.failures && st.input_max_depth<=22050 && st.output_max_depth<=6862);
    assert(st.tx_prepared_blocks>=2 && fd_stream_tx_prefill_ready(s));
    if(abort_wait) {
        fd_stream_abort(s);assert(fd_stream_join(s));
        assert(!fd_stream_tx_prefill_ready(s));
        assert(!fd_stream_observe(s).failures); /* no drain required to cancel */
    } else {
        assert(fd_stream_finish_tx(s));
        uint64_t delivered=0;
        while(!fd_stream_tx_finished(s)) {
            delivered+=fd_stream_receive(s,generation,output,4410);
            assert(!fd_stream_observe(s).failures && monotonic_ms()<deadline);
            struct timespec t={0,1000000};nanosleep(&t,NULL);
        }
        delivered+=fd_stream_receive(s,generation,output,4410);
        st=fd_stream_observe(s);
        assert(delivered>13230 && st.consumed_samples==st.submitted_samples && st.output_wait_ns>0);
    }
    fd_stream_destroy(s);
}
static void drain_correctness(fd_rade_stream *s,uint64_t generation,int16_t *output,uint64_t *delivered) {
    uint64_t deadline=monotonic_ms()+60000;
    for (;;) {
        *delivered+=fd_stream_receive(s,generation,output,4410);
        fd_stream_stats stats=fd_stream_observe(s);
        assert(!stats.failures);
        if(!stats.input_depth && stats.rendered_blocks==stats.blocks) return;
        assert(monotonic_ms()<deadline);
        struct timespec t={0,1000000}; nanosleep(&t,NULL);
    }
}
int main(int argc,char **argv) {
    bool unpaced=argc==2 && !strcmp(argv[1],"--unpaced");
    assert(argc==1 || unpaced);
    tx_backpressure(true);tx_backpressure(false);
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
            if(unpaced)drain_correctness(s,turn,output,&delivered);else pause10();
            assert(!fd_stream_observe(s).failures);
        }
        if(direction==FD_STREAM_TX)assert(fd_stream_finish_tx(s));
        for(int i=0;i<(unpaced?5:30);++i) { delivered+=fd_stream_receive(s,turn,output,4410); pause10(); }
        if(direction==FD_STREAM_TX) {
            uint64_t deadline=monotonic_ms()+60000;
            while(!fd_stream_tx_finished(s)){delivered+=fd_stream_receive(s,turn,output,4410);assert(!fd_stream_observe(s).failures);assert(monotonic_ms()<deadline);pause10();}
            delivered+=fd_stream_receive(s,turn,output,4410);
        }
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
