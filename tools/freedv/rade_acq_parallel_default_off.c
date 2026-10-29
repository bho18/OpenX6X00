/* Validate disabled experiment's public API without relying on private pool APIs. */
#include "rade_api.h"
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#if RADE_ACQ_PARALLEL
#error "This check must compile with the experiment disabled"
#endif
int main(void) {
    char model[]="built-in";
    struct rade *r=rade_open(model,RADE_VERBOSE_0);
    if(!r)return 2;
    unsigned failures=0,checks=0;
#define CHECK(v) do { checks++; if(!(v)) { failures++;fprintf(stderr,"default-off check failed at line %d\n",__LINE__); } } while(0)
    CHECK(rade_get_acquisition_workers(r)==1);
    CHECK(rade_set_acquisition_workers(r,0)==EINVAL);
    CHECK(rade_set_acquisition_workers(r,5)==EINVAL);
    CHECK(rade_set_acquisition_workers(r,1)==0);
    for(unsigned n=2;n<=4;n++)CHECK(rade_set_acquisition_workers(r,n)==ENOTSUP);
    struct rade_acquisition_stats st;
    memset(&st,0xa5,sizeof(st));
    CHECK(rade_get_acquisition_stats(r,&st)==0 && st.workers==1);
    CHECK(st.calls==0 && st.parallel_calls==0 && st.serial_calls==0);
    for(unsigned i=0;i<3;i++)CHECK(st.helper_jobs[i]==0 && st.helper_cpu_ns[i]==0);
    RADE_COMP *pcm=calloc((size_t)rade_nin_max(r),sizeof(*pcm));
    float *features=calloc((size_t)rade_n_features_in_out(r),sizeof(*features));
    float *eoo=calloc((size_t)rade_n_eoo_bits(r),sizeof(*eoo));
    int has_eoo=-1;
    if(!pcm||!features||!eoo)abort();
    CHECK(rade_rx(r,features,&has_eoo,eoo,pcm)==0 && has_eoo==0);
    CHECK(rade_get_acquisition_workers(r)==1);
    free(pcm);free(features);free(eoo);rade_close(r);
    printf("{\"summary\":true,\"experiment_enabled\":false,\"checks\":%u,\"failures\":%u,\"radio_qualified\":false}\n",checks,failures);
    return failures?1:0;
}
