// SPDX-License-Identifier: LGPL-2.1-or-later
#include "src/freedv/route_driver.h"
#include <assert.h>
#include <stdio.h>
static int values[256],keys[32],vals[32],n,sends,fail_at;
bool hw_set_i(hw_param_t p,int32_t v){assert(n<32);keys[n]=p;vals[n++]=v;values[p]=v;return true;}
bool hw_resend_all(void){return ++sends!=fail_at;}
int main(void){
 fd_route_settings r={radio_mic_handle,20,0,true,true,false};
 assert(fd_route_activate(&r));assert(sends==2);
 assert(keys[0]==HW_HMIC && vals[0]==0 && keys[1]==HW_IMIC && vals[1]==0);
 assert(values[HW_RECORD] && values[HW_MODEM] && !values[HW_PTT] && values[HW_HMIC]==20 && !values[HW_IMIC]);
 n=sends=0;fail_at=1;assert(!fd_route_activate(&r));assert(!values[HW_HMIC]);
 n=0;fd_route_stop();assert(!values[HW_MODEM] && !values[HW_PTT] && !values[HW_HMIC] && !values[HW_IMIC]);
 puts("PASS: shared route isolates gains before activation, admits hand input after transport, and propagates failure");
}
