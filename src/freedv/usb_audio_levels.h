// SPDX-License-Identifier: LGPL-2.1-or-later
#pragma once
#include "usb_voice.h"
#include <math.h>
#include <stddef.h>
#include <string.h>

#define FD_USB_LEVEL_FLOOR_DBFS10 (-960)

static inline int fd_usb_mic_gain_clamp(int gain_db){
    return gain_db<FD_USB_MIC_GAIN_MIN_DB?FD_USB_MIC_GAIN_MIN_DB:
           gain_db>FD_USB_MIC_GAIN_MAX_DB?FD_USB_MIC_GAIN_MAX_DB:gain_db;
}
static inline double fd_usb_mic_gain_factor(int gain_db){
    return pow(10.0,fd_usb_mic_gain_clamp(gain_db)/20.0);
}
static inline int16_t fd_usb_gain_sample(int16_t sample,double factor,bool *clipped){
    double value=sample*factor;
    if(value>32767.0){if(clipped)*clipped=true;return INT16_MAX;}
    if(value<-32768.0){if(clipped)*clipped=true;return INT16_MIN;}
    return (int16_t)lround(value);
}
/* out may be NULL to count saturation on an accepted prefix after submission.
 * The zero-gain path does no rounding, scaling, or saturation. */
static inline uint64_t fd_usb_pcm_gain(int16_t *out,const int16_t *in,size_t n,int gain_db){
    gain_db=fd_usb_mic_gain_clamp(gain_db);
    if(!gain_db){if(out&&out!=in)memcpy(out,in,n*sizeof(*in));return 0;}
    double factor=fd_usb_mic_gain_factor(gain_db);uint64_t clipped=0;
    for(size_t i=0;i<n;++i){bool clip=false;int16_t value=fd_usb_gain_sample(in[i],factor,&clip);if(out)out[i]=value;clipped+=clip;}
    return clipped;
}
typedef struct {
    uint64_t samples,input_squares,output_squares;
    uint32_t input_peak,output_peak;
} fd_usb_level_window;
static inline uint32_t fd_usb_sample_magnitude(int16_t sample){return sample<0?(uint32_t)-(int32_t)sample:(uint32_t)sample;}
/* Preview the exact fixed-gain/saturation path without altering capture PCM. */
static inline void fd_usb_level_add(fd_usb_level_window *w,const int16_t *in,size_t n,int gain_db){
    gain_db=fd_usb_mic_gain_clamp(gain_db);double factor=gain_db?fd_usb_mic_gain_factor(gain_db):1.0;
    for(size_t i=0;i<n;++i){
        uint32_t raw=fd_usb_sample_magnitude(in[i]);
        int16_t processed=gain_db?fd_usb_gain_sample(in[i],factor,NULL):in[i];
        uint32_t out=fd_usb_sample_magnitude(processed);
        w->input_squares+=(uint64_t)raw*raw;w->output_squares+=(uint64_t)out*out;
        if(raw>w->input_peak)w->input_peak=raw;
        if(out>w->output_peak)w->output_peak=out;
    }
    w->samples+=n;
}
static inline int fd_usb_level_dbfs10(double amplitude){
    if(amplitude<=0)return FD_USB_LEVEL_FLOOR_DBFS10;
    long level=lround(200.0*log10(amplitude/32768.0));
    return level<FD_USB_LEVEL_FLOOR_DBFS10?FD_USB_LEVEL_FLOOR_DBFS10:(int)level;
}
static inline int fd_usb_level_rms_dbfs10(uint64_t squares,uint64_t samples){
    return samples?fd_usb_level_dbfs10(sqrt((double)squares/samples)):FD_USB_LEVEL_FLOOR_DBFS10;
}
