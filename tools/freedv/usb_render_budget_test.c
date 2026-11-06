// SPDX-License-Identifier: BSD-2-Clause
#undef NDEBUG
#include "src/freedv/usb_render_budget.h"
#include <samplerate.h>
#include <assert.h>
#include <math.h>
#include <stdio.h>
#include <string.h>

typedef struct {float pcm[16384];unsigned n;} result;
static unsigned step(SRC_STATE *src,const float *input,unsigned n,bool final,float *output){
    /* Match production: its input buffer remains non-NULL for zero frames.
     * libsamplerate treats a NULL zero-input call differently and skips the
     * buffered-tail behavior this regression must exercise. */
    float empty[1]={0};
    SRC_DATA d={.data_in=input?input:empty,.data_out=output,.input_frames=n,
        .output_frames=FD_USB_RENDER_OUTPUT_FRAMES,.src_ratio=44100.0/48000,.end_of_input=final};
    assert(!src_process(src,&d));assert(d.input_frames_used==(long)n);
    assert(d.output_frames_gen>=0&&d.output_frames_gen<=FD_USB_RENDER_OUTPUT_FRAMES);
    return(unsigned)d.output_frames_gen;
}
static void append(result *r,const float *samples,unsigned n){assert(r->n+n<=16384);memcpy(r->pcm+r->n,samples,n*sizeof(*samples));r->n+=n;}
static SRC_STATE *create(void){int error=0;SRC_STATE *s=src_new(SRC_SINC_FASTEST,1,&error);assert(s&&!error);return s;}
int main(void){
    float warm[4096],input[4096],out[FD_USB_RENDER_OUTPUT_FRAMES];
    for(unsigned i=0;i<4096;++i){warm[i]=sinf((float)i*.037f)*.3f;input[i]=cosf((float)i*.041f)*.2f;}
    for(unsigned queued=0;queued<=FD_USB_TX_QUEUE_FRAMES+1;++queued){
        if(fd_usb_tx_render_room(queued))assert(queued+FD_USB_RENDER_OUTPUT_FRAMES<=FD_USB_TX_QUEUE_FRAMES);
    }
    assert(fd_usb_tx_render_room(FD_USB_TX_QUEUE_FRAMES-FD_USB_RENDER_OUTPUT_FRAMES));
    assert(!fd_usb_tx_render_room(FD_USB_TX_QUEUE_FRAMES-FD_USB_RENDER_OUTPUT_FRAMES+1));
    /* Reproduce the previous stale-occupancy failure with the real converter:
     * the original 128-frame check ran before a regular conversion filled the
     * queue, then committed the SRC tail against that obsolete headroom. */
    unsigned old_queue=0,input_n=0,normal_n=0,tail_n=0;result reference={0};
    for(unsigned queued=FD_USB_TX_QUEUE_FRAMES-129;queued>FD_USB_TX_QUEUE_FRAMES-4096;--queued){
        SRC_STATE *s=create();result candidate={0};
        unsigned n=step(s,warm,4096,false,out);append(&candidate,out,n);
        unsigned take=FD_USB_TX_QUEUE_FRAMES-queued;
        unsigned body=step(s,input,take,false,out);append(&candidate,out,body);
        unsigned tail=0;
        for(unsigned calls=0;calls<8;++calls){n=step(s,NULL,0,true,out);append(&candidate,out,n);tail+=n;if(!n)break;assert(calls<7);}
        src_delete(s);
        if(queued<FD_USB_TX_QUEUE_FRAMES-128&&queued+body+tail>FD_USB_TX_QUEUE_FRAMES){
            old_queue=queued;input_n=take;normal_n=body;tail_n=tail;reference=candidate;break;
        }
    }
    assert(old_queue&&tail_n&&old_queue+normal_n+tail_n>FD_USB_TX_QUEUE_FRAMES);
    /* The production reservation must defer without touching SRC/input, then
     * retain exactly the same waveform through bounded regular/final calls. */
    SRC_STATE *s=create();result bounded={0};unsigned n=step(s,warm,4096,false,out);append(&bounded,out,n);
    unsigned queued=old_queue,deferred=0,pulled=0;
    if(!fd_usb_tx_render_room(queued)){++deferred;assert(!pulled);queued=FD_USB_TX_QUEUE_FRAMES-FD_USB_RENDER_OUTPUT_FRAMES;}
    assert(fd_usb_tx_render_room(queued));n=step(s,input,input_n,false,out);pulled+=input_n;append(&bounded,out,n);queued+=n;assert(queued<=FD_USB_TX_QUEUE_FRAMES);
    bool ended=false;unsigned final_calls=0;
    while(!ended&&final_calls<8){
        if(!fd_usb_tx_render_room(queued)){++deferred;queued=queued>FD_USB_RENDER_OUTPUT_FRAMES?queued-FD_USB_RENDER_OUTPUT_FRAMES:0;continue;}
        n=step(s,NULL,0,true,out);++final_calls;append(&bounded,out,n);queued+=n;
        assert(queued<=FD_USB_TX_QUEUE_FRAMES);ended=n==0;
    }
    assert(ended&&final_calls>=2&&deferred>=2&&pulled==input_n);
    assert(reference.n==bounded.n&&!memcmp(reference.pcm,bounded.pcm,reference.n*sizeof(float)));
    src_delete(s);
    printf("PASS real SRC boundary: stale=%u+%u+%u>%u; bounded capacity=%u, deferred=%u, final_calls=%u, conserved=%u frames\n",old_queue,normal_n,tail_n,FD_USB_TX_QUEUE_FRAMES,FD_USB_RENDER_OUTPUT_FRAMES,deferred,final_calls,bounded.n);
    return 0;
}
