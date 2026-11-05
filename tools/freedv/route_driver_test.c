// SPDX-License-Identifier: LGPL-2.1-or-later
#include "src/freedv/route_driver.h"
#include <assert.h>
#include <stdio.h>
#include <string.h>
static int values[256],keys[32],vals[32],n,sends,fail_at;
static unsigned release_step,keys_after_release;
static bool permitted=true,rejected;
bool hw_set_i(hw_param_t p,int32_t v){
 assert(n<32);keys[n]=p;vals[n++]=v;values[p]=v;
 if(!permitted && v && (p==HW_MODEM || p==HW_PTT))++keys_after_release;
 if((release_step==1 && p==HW_RECORD) || (release_step==2 && p==HW_MODEM && v) ||
    (release_step==3 && p==HW_PTT && v) || (release_step==5 && p==HW_HMIC && v))permitted=false;
 return true;
}
bool hw_resend_all(void){
 ++sends;if((release_step==4 && sends==1) || (release_step==6 && sends==2))permitted=false;
 return sends!=fail_at;
}
static bool admit(void *context){assert(context==&permitted);if(!permitted)rejected=true;return !rejected;}
int main(void){
 fd_route_settings r={radio_mic_handle,20,0,true,true,false};
 assert(fd_route_activate(&r));assert(sends==2);
 assert(keys[0]==HW_HMIC && vals[0]==0 && keys[1]==HW_IMIC && vals[1]==0);
 assert(values[HW_RECORD] && values[HW_MODEM] && !values[HW_PTT] && values[HW_HMIC]==20 && !values[HW_IMIC]);
 n=sends=0;fail_at=1;assert(!fd_route_activate(&r));assert(!values[HW_HMIC]);
 n=0;fd_route_stop();assert(!values[HW_MODEM] && !values[HW_PTT] && !values[HW_HMIC] && !values[HW_IMIC]);
 r.ptt=true;fail_at=0;
 for(release_step=1;release_step<=6;++release_step){
  memset(values,0,sizeof(values));n=sends=0;permitted=true;rejected=false;keys_after_release=0;
  assert(!fd_route_activate_guarded(&r,admit,&permitted));
  assert(!values[HW_MODEM] && !values[HW_PTT] && !values[HW_HMIC] && !values[HW_IMIC]);
  assert(rejected && !keys_after_release);
  if(release_step==1)assert(!sends); /* release in prep precedes every key */
  if(release_step==2)for(int i=0;i<n;++i)assert(keys[i]!=HW_PTT || !vals[i]);
  if(release_step<=3)assert(!sends);
  permitted=true;assert(!admit(&permitted)); /* same attempt cannot revive */
 }
 puts("PASS: release during route preparation/key/gain/resend writes fences every later key and clears software TX");
 puts("PASS: shared route isolates gains before activation, admits hand input after transport, and propagates failure");
}
