/* Offline acquisition-pool lifecycle checks; no physical devices. */
#include "rade_api.h"
#include "rade_acq_parallel.h"
#include <errno.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

extern int rade_acq_pool_test_fail_create_after(int);
extern unsigned rade_acq_pool_test_live_threads(void);
static unsigned cases, checks, failures;
static const char *name;
static unsigned start_checks, start_failures;
static void require(int good, const char *why) {
    checks++;
    if(!good) { failures++;fprintf(stderr,"%s: %s\n",name,why); }
}
static void finish(void) {
    if(!name)return;
    cases++;
    printf("{\"case\":\"%s\",\"checks\":%u,\"failures\":%u}\n",name,checks-start_checks,failures-start_failures);
    fflush(stdout);
}
static void begin(const char *label) { finish();name=label;start_checks=checks;start_failures=failures; }
static struct rade *open_v1(void) {
    char model[]="built-in";
    struct rade *r=rade_open(model,RADE_VERBOSE_0);
    if(!r)abort();
    return r;
}
static void receive_zero(struct rade *r) {
    RADE_COMP *pcm=calloc((size_t)rade_nin_max(r),sizeof(*pcm));
    float *features=calloc((size_t)rade_n_features_in_out(r),sizeof(*features));
    float *eoo=calloc((size_t)rade_n_eoo_bits(r),sizeof(*eoo));
    int has_eoo=-1;
    if(!pcm||!features||!eoo)abort();
    require(rade_rx(r,features,&has_eoo,eoo,pcm)==0 && has_eoo==0,"zero input unexpectedly generated features/EOO");
    free(pcm);free(features);free(eoo);
}
int main(void) {
    begin("null-public-arguments");
    struct rade_acquisition_stats invalid_stats;
    require(rade_set_acquisition_workers(NULL,1)==EINVAL,"null setter handle accepted");
    require(rade_get_acquisition_workers(NULL)==0,"null getter did not report unavailable");
    require(rade_get_acquisition_stats(NULL,&invalid_stats)==EINVAL,"null stats handle accepted");
    struct rade *null_test=open_v1();
    require(rade_get_acquisition_stats(null_test,NULL)==EINVAL,"null stats output accepted");
    rade_close(null_test);
    require(rade_acq_pool_test_live_threads()==0,"null argument checks left helpers");

    begin("v2-api-rejected");
    char model[]="built-in";
    struct rade *v2=rade_open(model,RADE_VERBOSE_0|RADE_MODE_V2);
    if(!v2)abort();
    for(unsigned workers=1;workers<=4;workers++)
        require(rade_set_acquisition_workers(v2,workers)==EINVAL,"V2 setter accepted unsupported mode");
    require(rade_get_acquisition_workers(v2)==0,"V2 getter did not report unavailable");
    require(rade_get_acquisition_stats(v2,&invalid_stats)==EINVAL,"V2 stats accepted unsupported mode");
    rade_close(v2);
    require(rade_acq_pool_test_live_threads()==0,"V2 API checks left helpers");

    begin("invalid-private-counts");
    struct rade_acq_pool *pool=(struct rade_acq_pool *)(uintptr_t)1;
    require(rade_acq_pool_create(0,&pool)==EINVAL && pool==NULL,"zero workers not rejected with null output");
    pool=(struct rade_acq_pool *)(uintptr_t)1;
    require(rade_acq_pool_create(5,&pool)==EINVAL && pool==NULL,"five workers not rejected with null output");
    require(rade_acq_pool_test_live_threads()==0,"invalid creation left helpers");

    begin("private-close-before-run");
    for(unsigned workers=1;workers<=4;workers++) {
        require(rade_acq_pool_create(workers,&pool)==0,"private pool creation failed");
        require(rade_acq_pool_workers(pool)==workers,"private reported count differs");
        require(rade_acq_pool_test_live_threads()==workers-1,"private helper count differs");
        rade_acq_pool_destroy(pool);
        require(rade_acq_pool_test_live_threads()==0,"private close did not join helpers");
    }
    rade_acq_pool_destroy(NULL);
    begin("public-pre-rx-reconfigure");
    struct rade *r=open_v1();
    require(rade_get_acquisition_workers(r)==1,"default did not select one worker");
    const unsigned counts[]={1,4,2,3,1,4,1};
    for(unsigned i=0;i<sizeof(counts)/sizeof(*counts);i++) {
        unsigned workers=counts[i];
        require(rade_set_acquisition_workers(r,workers)==0,"pre-RX setter failed");
        require(rade_get_acquisition_workers(r)==workers,"setter silently changed worker count");
        require(rade_acq_pool_test_live_threads()==workers-1,"reconfigure left old helpers");
    }
    require(rade_set_acquisition_workers(r,0)==EINVAL,"invalid public zero count accepted");
    require(rade_set_acquisition_workers(r,5)==EINVAL,"invalid public five count accepted");
    require(rade_get_acquisition_workers(r)==1,"invalid setter changed configuration");
    rade_close(r);
    require(rade_acq_pool_test_live_threads()==0,"close before first RX leaked helpers");

    for(int successful=0;successful<3;successful++) {
        char label[64];snprintf(label,sizeof(label),"create-failure-after-%d",successful);
        begin(label);
        r=open_v1();
        require(rade_set_acquisition_workers(r,2)==0,"old working configuration unavailable");
        require(rade_acq_pool_test_live_threads()==1,"old pool does not have one helper");
        rade_acq_pool_test_fail_create_after(successful);
        require(rade_set_acquisition_workers(r,4)==EAGAIN,"injected pthread_create failure not returned");
        require(rade_get_acquisition_workers(r)==2,"failed reconfigure destroyed old configuration");
        require(rade_acq_pool_test_live_threads()==1,"partial creation did not join all new helpers");
        rade_acq_pool_test_fail_create_after(-1);
        receive_zero(r);
        struct rade_acquisition_stats st;
        require(rade_get_acquisition_stats(r,&st)==0 && st.workers==2 && st.calls==1,"preserved old pool did not execute");
        rade_close(r);
        require(rade_acq_pool_test_live_threads()==0,"post-failure close leaked old helpers");
        finish();name=NULL;
    }
    begin("public-after-rx-busy");
    r=open_v1();
    require(rade_set_acquisition_workers(r,4)==0,"configure four workers failed");
    receive_zero(r);
    for(unsigned workers=1;workers<=4;workers++) {
        require(rade_set_acquisition_workers(r,workers)==EBUSY,"post-RX setter did not reject modification");
        require(rade_get_acquisition_workers(r)==4,"rejected setter altered count");
    }
    receive_zero(r);
    struct rade_acquisition_stats st;
    require(rade_get_acquisition_stats(r,&st)==0 && st.workers==4 && st.calls==2,"persistent pool lost accounting");
    rade_close(r);
    require(rade_acq_pool_test_live_threads()==0,"post-RX close leaked helpers");

    begin("multiple-live-instances");
    struct rade *r1=open_v1(), *r2=open_v1();
    require(rade_set_acquisition_workers(r1,2)==0,"first instance configure failed");
    require(rade_set_acquisition_workers(r2,4)==0,"second instance configure failed");
    require(rade_acq_pool_test_live_threads()==4,"instances do not own independent helper sets");
    for(unsigned i=0;i<4;i++){receive_zero(r1);receive_zero(r2);}
    struct rade_acquisition_stats s1,s2;
    require(rade_get_acquisition_stats(r1,&s1)==0 && s1.workers==2 && s1.calls==4,"first instance counters mixed");
    require(rade_get_acquisition_stats(r2,&s2)==0 && s2.workers==4 && s2.calls==4,"second instance counters mixed");
    rade_close(r1);
    require(rade_acq_pool_test_live_threads()==3,"first close affected second helpers");
    receive_zero(r2);rade_close(r2);
    require(rade_acq_pool_test_live_threads()==0,"second instance close leaked helpers");

    begin("repeated-open-close");
    for(unsigned i=0;i<8;i++) {
        r=open_v1();unsigned workers=1+i%4;
        require(rade_set_acquisition_workers(r,workers)==0,"repeated configure failed");
        if(i&1)receive_zero(r);
        rade_close(r);
        require(rade_acq_pool_test_live_threads()==0,"repeated close leaked helpers");
    }
    finish();
    printf("{\"summary\":true,\"cases\":%u,\"checks\":%u,\"failures\":%u,\"live_helpers\":%u,\"radio_qualified\":false}\n",cases,checks,failures,rade_acq_pool_test_live_threads());
    return failures?1:0;
}
