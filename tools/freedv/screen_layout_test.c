// SPDX-License-Identifier: LGPL-2.1-or-later
#include "../../src/freedv/screen_layout.h"
#include <assert.h>
#include <stdio.h>

int main(void){
    const int headers[]={116,158,168}; /* standard, deck, VISION */
    const int heights[]={60,76,160},fonts[]={14,18,22,28,34};
    unsigned cases=0;
    for(unsigned theme=0;theme<3;++theme)for(unsigned visible=0;visible<2;++visible)
    for(unsigned h=0;h<3;++h)for(unsigned f=0;f<5;++f){
        int header=headers[theme],axis=theme==1?0:36;bool deck=theme==1;
        fd_main_layout baseline=fd_main_layout_compute(header,visible,heights[h],deck,axis,false,fonts[f]);
        fd_main_layout voice=fd_main_layout_compute(header,visible,heights[h],deck,axis,true,fonts[f]);
        assert(voice.row_y==header && voice.row_height>=28 && voice.row_height>=fonts[f]+8);
        assert(voice.waterfall_y==baseline.waterfall_y+voice.row_height);
        assert(voice.waterfall_y<480-62); /* still visible above the tallest native buttons */
        assert(voice.axis_y>=voice.row_y+voice.row_height);
        if(visible){
            assert(voice.spectrum_y==header+voice.row_height);
            assert(voice.spectrum_height==heights[h]);
            assert(voice.axis_y==(deck?voice.spectrum_y:voice.spectrum_y+heights[h]));
        }else{
            assert(voice.spectrum_y==0 && voice.spectrum_height==header+voice.row_height);
        }
        fd_main_layout restored=fd_main_layout_compute(header,visible,heights[h],deck,axis,false,fonts[f]);
        assert(restored.spectrum_y==baseline.spectrum_y && restored.spectrum_height==baseline.spectrum_height &&
            restored.axis_y==baseline.axis_y && restored.waterfall_y==baseline.waterfall_y);
        ++cases;
    }
    for (unsigned f=0; f<5; ++f) {
        fd_main_layout baseline=fd_main_layout_amber(310,294,112,0,false,fonts[f]);
        fd_main_layout voice=fd_main_layout_amber(310,294,112,0,true,fonts[f]);
        assert(baseline.spectrum_y==294 && baseline.spectrum_height==112);
        assert(voice.row_y>=310 && voice.row_height>=fonts[f]+8);
        assert(voice.spectrum_y>=voice.row_y+voice.row_height && voice.spectrum_height>0);
        assert(voice.axis_y==voice.spectrum_y && voice.waterfall_y==baseline.waterfall_y);
        assert(voice.spectrum_y+voice.spectrum_height==406);
        ++cases;
    }
    printf("Production main-screen reserved-row geometry: %u cases PASS\n",cases);return 0;
}
