// SPDX-License-Identifier: LGPL-2.1-or-later
#include "../../src/freedv/usb_audio_levels.h"
#include <assert.h>
#include <limits.h>
#include <stdio.h>

static void zero_is_identity(void){
    int16_t in[65536],out[65536];
    for(unsigned i=0;i<65536;++i)in[i]=(int16_t)((int)i-32768);
    assert(!fd_usb_pcm_gain(out,in,65536,0));
    assert(!memcmp(in,out,sizeof(in)));
    assert(!fd_usb_pcm_gain(in,in,65536,0));
    assert(!memcmp(in,out,sizeof(in)));
}
static void gain_and_saturation(void){
    const int16_t in[]={0,1,-1,1000,-1000,16000,-16000,INT16_MAX,INT16_MIN};int16_t out[9];
    assert(fd_usb_mic_gain_clamp(INT_MIN)==-12);
    assert(fd_usb_mic_gain_clamp(INT_MAX)==24);
    assert(fd_usb_pcm_gain(out,in,9,6)==2);
    assert(out[0]==0&&out[1]==2&&out[2]==-2&&out[3]==1995&&out[4]==-1995);
    assert(out[5]==31924&&out[6]==-31924&&out[7]==INT16_MAX&&out[8]==INT16_MIN);
    assert(fd_usb_pcm_gain(NULL,in,7,6)==0); /* Accepted-prefix clipping only. */
    assert(fd_usb_pcm_gain(out,in,9,24)==4);
    assert(out[3]==15849&&out[4]==-15849&&out[5]==INT16_MAX&&out[6]==INT16_MIN);
    int16_t clamped[9];assert(fd_usb_pcm_gain(clamped,in,9,INT_MAX)==4);assert(!memcmp(out,clamped,sizeof(out)));
    assert(fd_usb_pcm_gain(out,in,9,-12)==0);
    assert(out[3]==251&&out[4]==-251&&out[7]==8231&&out[8]==-8231);
    assert(fd_usb_pcm_gain(clamped,in,9,INT_MIN)==0);assert(!memcmp(out,clamped,sizeof(out)));
}
static void meters(void){
    int16_t square[4800],copy[4800];for(unsigned i=0;i<4800;++i)square[i]=i%2?16384:-16384;
    memcpy(copy,square,sizeof(copy));fd_usb_level_window w={0};
    fd_usb_level_add(&w,square,2400,0);fd_usb_level_add(&w,square+2400,2400,0);
    assert(w.samples==4800&&w.input_peak==16384&&w.output_peak==16384);
    assert(fd_usb_level_rms_dbfs10(w.input_squares,w.samples)==-60);
    assert(fd_usb_level_rms_dbfs10(w.output_squares,w.samples)==-60);
    assert(fd_usb_level_dbfs10(w.output_peak)==-60);
    assert(!memcmp(copy,square,sizeof(copy)));
    w=(fd_usb_level_window){0};fd_usb_level_add(&w,square,4800,6);
    assert(fd_usb_level_dbfs10(w.input_peak)==-60&&fd_usb_level_dbfs10(w.output_peak)==0);
    assert(fd_usb_level_rms_dbfs10(w.output_squares,w.samples)==0);
    w=(fd_usb_level_window){0};fd_usb_level_add(&w,square,4800,-12);
    assert(fd_usb_level_dbfs10(w.output_peak)==-180);
    assert(fd_usb_level_rms_dbfs10(w.output_squares,w.samples)==-180);
    for(unsigned i=0;i<4800;++i)square[i]=(int16_t)lround(16384*sin(2*3.14159265358979323846*i/48));
    w=(fd_usb_level_window){0};fd_usb_level_add(&w,square,4800,0);
    assert(fd_usb_level_rms_dbfs10(w.input_squares,w.samples)==-90);
    assert(fd_usb_level_dbfs10(w.input_peak)==-60);
    const int16_t edge[]={INT16_MIN,INT16_MAX};w=(fd_usb_level_window){0};fd_usb_level_add(&w,edge,2,0);
    assert(w.input_peak==32768&&w.output_peak==32768&&fd_usb_level_dbfs10(w.input_peak)==0);
    memset(square,0,sizeof(square));w=(fd_usb_level_window){0};fd_usb_level_add(&w,square,4800,24);
    assert(fd_usb_level_rms_dbfs10(w.input_squares,w.samples)==FD_USB_LEVEL_FLOOR_DBFS10);
    assert(fd_usb_level_rms_dbfs10(w.output_squares,w.samples)==FD_USB_LEVEL_FLOOR_DBFS10);
    assert(fd_usb_level_dbfs10(w.output_peak)==FD_USB_LEVEL_FLOOR_DBFS10);
    assert(fd_usb_level_rms_dbfs10(0,0)==FD_USB_LEVEL_FLOOR_DBFS10);
}
static void overload_is_distinct_from_peak(void){
    /* A near-full-scale sample is useful warning evidence, not proof of clipping. */
    const int16_t near[]={30000,-30000};
    fd_usb_level_window w={0};fd_usb_level_add(&w,near,2,0);
    assert(fd_usb_level_dbfs10(w.output_peak)>=-10 && !w.overload_samples);
    /* Already-railed USB input must remain visible even at zero or negative gain. */
    const int16_t rails[]={INT16_MAX,INT16_MIN,0};
    w=(fd_usb_level_window){0};fd_usb_level_add(&w,rails,3,0);
    assert(w.overload_samples==2 && fd_usb_level_dbfs10(w.output_peak)==0);
    w=(fd_usb_level_window){0};fd_usb_level_add(&w,rails,3,-12);
    assert(w.overload_samples==2 && fd_usb_level_dbfs10(w.output_peak)<-100);
    /* Digital gain can saturate otherwise clean input. Counting must not alter PCM. */
    int16_t copy[2];memcpy(copy,near,sizeof(copy));
    w=(fd_usb_level_window){0};fd_usb_level_add(&w,near,2,6);
    assert(w.overload_samples==2 && !memcmp(near,copy,sizeof(copy)));
    const int16_t quiet[]={1000,-1000};fd_usb_level_add(&w,quiet,2,0);
    assert(w.overload_samples==2 && w.samples==4);
}
int main(void){zero_is_identity();gain_and_saturation();meters();overload_is_distinct_from_peak();puts("PASS: USB speech gain, RMS/peak preview and live input/digital overload distinction");return 0;}
