/* Research-only receiver differential test; no radio/device access.
 * Reference receiver/acquisition sources are compiled with reference_* names.
 * Both implementations use the original receiver/acquisition header layout.
 * Every original observable receiver/acquisition field is compared byte-for-byte
 * after each call; the candidate enters through the public receiver API. */
#include "rade_api.h"
#include "rade_rx.h"
#include <fenv.h>
#include <float.h>
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

extern int reference_rade_rx_init(rade_rx_state *, const RADEDec *, int, int, int);
extern void reference_rade_rx_reset(rade_rx_state *);
extern int reference_rade_rx_process(rade_rx_state *, float *, float *, const RADE_COMP *);
extern int reference_rade_acq_detect_pilots(rade_acq *, const RADE_COMP *, int *, float *);
extern void reference_rade_acq_init(rade_acq *, const rade_ofdm *, float, float);
extern int reference_rade_acq_check_pilots(rade_acq *, const RADE_COMP *, int, float, int *, int *);

static rade_rx_state *a, *b;
static struct rade *rb_handle;
static unsigned workers;
static uint32_t rng = 0x6bc45a21u;
static unsigned calls, failures, feature_calls, parallel_calls, search_calls, sync_calls, sync_entries;
static unsigned states_seen;
static const char *case_name;
static unsigned case_start_calls,case_start_parallel,case_start_features,case_start_failures,case_start_sync;
static int force_search;

static uint32_t next_random(void) {
    rng ^= rng << 13; rng ^= rng >> 17; rng ^= rng << 5; return rng;
}
static float random_signed(void) {
    return ((int32_t)(next_random() >> 8) - 8388608) * 0x1p-23f;
}
static void make_noise(RADE_COMP *out, size_t n, float scale) {
    for (size_t i=0;i<n;i++) {
        out[i].real=random_signed()*scale;
        out[i].imag=random_signed()*scale;
    }
}
static int unequal(const void *x,const void *y,size_t n,const char *field) {
    if (!memcmp(x,y,n)) return 0;
    fprintf(stderr,"%s call %u: unequal %s\n",case_name,calls,field);
    return 1;
}
#define EQ_FIELD(obj,field) unequal(&(a->obj.field),&(b->obj.field),sizeof(a->obj.field),#obj "." #field)
#define EQ_RX(field) unequal(&(a->field),&(b->field),sizeof(a->field),#field)
static int compare_state(void) {
    int bad=0;
    bad|=EQ_RX(ofdm); bad|=EQ_RX(bpf); bad|=EQ_RX(bpf_en);
    bad|=EQ_RX(dec_model); bad|=EQ_RX(dec_state);
    bad|=EQ_RX(bottleneck); bad|=EQ_RX(auxdata); bad|=EQ_RX(num_features);
    bad|=EQ_RX(coarse_mag); bad|=EQ_RX(time_offset); bad|=EQ_RX(state);
    bad|=EQ_RX(valid_count); bad|=EQ_RX(synced_count); bad|=EQ_RX(uw_errors);
    bad|=EQ_RX(Nmf_unsync); bad|=EQ_RX(synced_count_one_sec);
    bad|=EQ_RX(tmax); bad|=EQ_RX(tmax_candidate); bad|=EQ_RX(fmax);
    bad|=EQ_RX(rx_phase); bad|=EQ_RX(nin); bad|=EQ_RX(rx_buf);
    bad|=EQ_RX(snrdB_3k_est); bad|=EQ_RX(mf); bad|=EQ_RX(verbose); bad|=EQ_RX(disable_unsync);
    bad|=EQ_FIELD(acq,fs); bad|=EQ_FIELD(acq,m); bad|=EQ_FIELD(acq,ncp); bad|=EQ_FIELD(acq,nmf);
    bad|=EQ_FIELD(acq,fcoarse_range); bad|=EQ_FIELD(acq,n_fcoarse);
    bad|=EQ_FIELD(acq,p_w); bad|=EQ_FIELD(acq,sigma_p); bad|=EQ_FIELD(acq,p); bad|=EQ_FIELD(acq,pend);
    bad|=EQ_FIELD(acq,Dt1); bad|=EQ_FIELD(acq,Dt2); bad|=EQ_FIELD(acq,Dthresh);
    bad|=EQ_FIELD(acq,Dtmax12); bad|=EQ_FIELD(acq,Dtmax12_eoo); bad|=EQ_FIELD(acq,f_ind_max);
    bad|=EQ_FIELD(acq,Pacq_error1); bad|=EQ_FIELD(acq,Pacq_error2);
    return bad;
}
static void finish_case(void) {
    if(!case_name)return;
    printf("{\"case\":\"%s\",\"receiver_calls\":%u,\"parallel_calls\":%u,\"feature_calls\":%u,\"sync_entries\":%u,\"failures\":%u}\n",case_name,calls-case_start_calls,parallel_calls-case_start_parallel,feature_calls-case_start_features,sync_entries-case_start_sync,failures-case_start_failures);
    fflush(stdout);
}
static void init_case(const char *name,int bpf) {
    finish_case();
    case_name=name;
    force_search=0;
    case_start_calls=calls;case_start_parallel=parallel_calls;case_start_features=feature_calls;case_start_failures=failures;case_start_sync=sync_entries;
    if(rb_handle)rade_close(rb_handle);
    char built_in[]="built-in";
    rb_handle=rade_open(built_in,RADE_VERBOSE_0);
    if(!rb_handle)abort();
    b=&rb_handle->rx;
    if (reference_rade_rx_init(a,NULL,3,1,bpf)||rade_rx_init(b,NULL,3,1,bpf)) abort();
    if (rade_set_acquisition_workers(rb_handle,workers) || rade_get_acquisition_workers(rb_handle)!=workers) abort();
    a->verbose=b->verbose=0;
    if (compare_state()) abort();
}
static int step(const RADE_COMP *input) {
    float fa[RADE_NZMF*RADE_FRAMES_PER_STEP*RADE_NB_TOTAL_FEATURES];
    float fb[sizeof(fa)/sizeof(*fa)];
    float ea[(RADE_NS-1)*RADE_NC*2], eb[sizeof(ea)/sizeof(*ea)];
    memset(fa,0xa5,sizeof(fa)); memset(fb,0xa5,sizeof(fb));
    memset(ea,0x5a,sizeof(ea)); memset(eb,0x5a,sizeof(eb));
    /* Adversarial detector/configuration cases deliberately remain in SEARCH.
     * Independent modem streams below exercise ordinary state transitions. */
    if(force_search) { a->state=b->state=RADE_STATE_SEARCH; a->valid_count=b->valid_count=0; }
    int previous=a->state;
    unsigned seed=0x35a2u+calls*7919u;
    srand(seed); int ra=reference_rade_rx_process(a,fa,ea,input);
    struct rade_acquisition_stats before,after;
    if(rade_get_acquisition_stats(rb_handle,&before))abort();
    int has_eoo=-1;
    srand(seed); int nfeatures=rade_rx(rb_handle,fb,&has_eoo,eb,(RADE_COMP *)input);
    int rb=(nfeatures?1:0)|(has_eoo?2:0);
    if(nfeatures!=((ra&1)?rade_rx_n_features_out(a):0))failures++;
    if(rade_get_acquisition_stats(rb_handle,&after))abort();
    if(after.calls<before.calls || after.calls!=after.serial_calls+after.parallel_calls)failures++;
    if(after.calls-before.calls!=(uint64_t)(previous!=RADE_STATE_SYNC))failures++;
    for(unsigned j=0;j<3;j++) {
        uint64_t delta=after.helper_jobs[j]-before.helper_jobs[j];
        if(delta!=(j+1<workers?after.parallel_calls-before.parallel_calls:0))failures++;
    }
    calls++;
    parallel_calls += (unsigned)(after.parallel_calls-before.parallel_calls);
    search_calls+=previous!=RADE_STATE_SYNC; sync_calls+=previous==RADE_STATE_SYNC;
    states_seen|=1u<<a->state;
    sync_entries+=(previous!=RADE_STATE_SYNC&&a->state==RADE_STATE_SYNC);
    if (ra&1)feature_calls++;
    if (ra!=rb||compare_state()||unequal(fa,fb,sizeof(fa),"features")||unequal(ea,eb,sizeof(ea),"EOO")) failures++;
    return ra;
}
static void noise_stream(const char *name,float scale,int count,int bpf) {
    RADE_COMP input[RADE_NMF+RADE_M]; init_case(name,bpf);
    for (int i=0;i<count;i++) {
        make_noise(input,(size_t)a->nin,scale);
        step(input);
    }
}
static void reset_and_shifts(void) {
    RADE_COMP input[RADE_NMF+RADE_M]; init_case("reset-and-shifts",0);force_search=1;
    make_noise(input,RADE_NMF+RADE_M,.04f);
    step(input);step(input);
    reference_rade_rx_reset(a);rade_rx_reset(b);
    step(input);step(input);
    const int shifts[]={0,800,1120,960};
    for(size_t i=0;i<sizeof(shifts)/sizeof(*shifts);i++) {
        a->nin=b->nin=shifts[i];step(input);
        a->nin=b->nin=RADE_NMF;
        step(input);
    }
}
static void config_changes(void) {
    RADE_COMP input[RADE_NMF+RADE_M];init_case("configuration-invalidation",0);force_search=1;
    make_noise(input,RADE_NMF+RADE_M,.04f);step(input);step(input);
    a->acq.p_w[0][0].real=b->acq.p_w[0][0].real*=.75f;step(input);step(input);
    a->acq.n_fcoarse=b->acq.n_fcoarse=17;step(input);step(input);
    a->acq.fcoarse_range[0]=b->acq.fcoarse_range[0]=-.125f;step(input);step(input);
    a->acq.Pacq_error1=b->acq.Pacq_error1=.02f;step(input);step(input);
}
static void retained_mutation_and_tails(void) {
    RADE_COMP input[RADE_NMF+RADE_M];init_case("retained-mutation-and-tail-dimensions",0);force_search=1;
    make_noise(input,RADE_NMF+RADE_M,.04f);step(input);step(input);
    a->rx_buf[RADE_NMF+11].real=b->rx_buf[RADE_NMF+11].real=.25f;
    step(input);step(input);
    a->acq.nmf=b->acq.nmf=RADE_NMF-1;step(input);step(input);
    a->acq.nmf=b->acq.nmf=RADE_NMF;step(input);step(input);
    a->acq.m=b->acq.m=RADE_M-1;step(input);step(input);
    a->acq.m=b->acq.m=RADE_M;step(input);step(input);
}
static void numeric_transitions(void) {
    RADE_COMP input[RADE_NMF+RADE_M];init_case("numeric-transitions",0);force_search=1;
    make_noise(input,RADE_NMF+RADE_M,.04f);step(input);step(input);
    if(fesetround(FE_UPWARD))abort();
    step(input);step(input);
    if(fesetround(FE_TONEAREST))abort();
    step(input);step(input);
    input[10].real=FLT_TRUE_MIN;step(input);step(input);
    make_noise(input,RADE_NMF+RADE_M,.04f);step(input);step(input);step(input);
    input[10].real=NAN;step(input);step(input);
    make_noise(input,RADE_NMF+RADE_M,.04f);step(input);step(input);step(input);
    input[10].imag=INFINITY;step(input);step(input);
}
static void fp_control_transition(void) {
    RADE_COMP input[RADE_NMF+RADE_M];init_case("FP-flush-control-transition",0);force_search=1;
    make_noise(input,RADE_NMF+RADE_M,.04f);step(input);step(input);
    uint32_t original,changed;
#if defined(__arm__) && defined(__VFP_FP__)
    __asm__ volatile("vmrs %0, fpscr":"=r"(original));
    /* Toggle both legal ARM FZ and default-NaN controls after helpers exist. */
    changed=original^((1u<<24)|(1u<<25));
    __asm__ volatile("vmsr fpscr, %0"::"r"(changed));
    step(input);step(input);
    __asm__ volatile("vmsr fpscr, %0"::"r"(original));
#elif defined(__SSE__)
    __asm__ volatile("stmxcsr %0":"=m"(original));
    changed=original^(1u<<15);
    __asm__ volatile("ldmxcsr %0"::"m"(changed));
    step(input);step(input);
    __asm__ volatile("ldmxcsr %0"::"m"(original));
#else
#error "This research harness needs an audited FP control register"
#endif
    step(input);step(input);
}
static void reinitialization(void) {
    RADE_COMP input[RADE_NMF+RADE_M],unrelated[RADE_RX_BUF_SIZE];
    init_case("acquisition-reinitialization",0);force_search=1;
    make_noise(input,RADE_NMF+RADE_M,.04f);make_noise(unrelated,RADE_RX_BUF_SIZE,.07f);
    step(input);step(input);
    reference_rade_acq_init(&a->acq,&a->ofdm,RADE_ACQ_FRANGE,RADE_ACQ_FSTEP);
    rade_acq_init(&b->acq,&b->ofdm,RADE_ACQ_FRANGE,RADE_ACQ_FSTEP);
    step(input);step(input);
    reference_rade_acq_init(&a->acq,&a->ofdm,RADE_ACQ_FRANGE,RADE_ACQ_FSTEP);
    rade_acq_init(&b->acq,&b->ofdm,RADE_ACQ_FRANGE,RADE_ACQ_FSTEP);
    int ta=-1,tb=-1;float fa=NAN,fb=NAN;
    int ra=reference_rade_acq_detect_pilots(&a->acq,unrelated,&ta,&fa);
    int rb=rade_acq_detect_pilots(&b->acq,unrelated,&tb,&fb);
    if(ra!=rb||ta!=tb||memcmp(&fa,&fb,sizeof(fa))||compare_state())failures++;
    step(input);step(input);
}
static void direct_calls(void) {
    RADE_COMP input[RADE_NMF+RADE_M], buf[RADE_RX_BUF_SIZE];
    init_case("standalone-detector",0);force_search=1;make_noise(input,RADE_NMF+RADE_M,.04f);
    step(input);step(input);
    for(int i=0;i<3;i++) {
        make_noise(buf,RADE_RX_BUF_SIZE,.03f);
        int ta=-1,tb=-1;float fa=NAN,fb=NAN;
        int ra=reference_rade_acq_detect_pilots(&a->acq,buf,&ta,&fa);
        int rb=rade_acq_detect_pilots(&b->acq,buf,&tb,&fb);
        if(ra!=rb||ta!=tb||memcmp(&fa,&fb,sizeof(fa))||compare_state())failures++;
    }
    step(input);step(input);
    int va=-1,vb=-1,ea=-1,eb=-1;
    srand(20261027);int ra=reference_rade_acq_check_pilots(&a->acq,a->rx_buf,300,0,&va,&ea);
    srand(20261027);int rb=rade_acq_check_pilots(&b->acq,b->rx_buf,300,0,&vb,&eb);
    if(ra!=rb||va!=vb||ea!=eb||compare_state())failures++;
    step(input);
}
static void interleaved_instances(void) {
    RADE_COMP input[RADE_NMF+RADE_M];
    init_case("interleaved-receiver-instances",0);force_search=1;
    rade_rx_state *first_a=a,*first_b=b;
    struct rade *first_handle=rb_handle;
    rade_rx_state *second_a=calloc(1,sizeof(*a));
    char built_in[]="built-in";
    struct rade *second_handle=rade_open(built_in,RADE_VERBOSE_0);
    if(!second_a||!second_handle)abort();
    rade_rx_state *second_b=&second_handle->rx;
    if(reference_rade_rx_init(second_a,NULL,3,1,0)||rade_rx_init(second_b,NULL,3,1,0))abort();
    if(rade_set_acquisition_workers(second_handle,workers))abort();
    second_a->verbose=second_b->verbose=0;
    for(int i=0;i<4;i++) {
        a=first_a;b=first_b;rb_handle=first_handle;make_noise(input,RADE_NMF,.03f);step(input);
        a=second_a;b=second_b;rb_handle=second_handle;make_noise(input,RADE_NMF,.07f);step(input);
    }
    a=first_a;b=first_b;rb_handle=first_handle;free(second_a);rade_close(second_handle);
}
static void exact_timing_boundary(int timing) {
    char name[64];snprintf(name,sizeof(name),"coarse-pilot-timing-%d",timing);
    /* Keep the label alive until finish_case is called before returning. */
    init_case(name,0);force_search=1;
    size_t length=RADE_RX_BUF_SIZE+6*RADE_NMF;
    RADE_COMP *stream=malloc(length*sizeof(*stream));if(!stream)abort();
    make_noise(stream,length,.01f);
    for(size_t i=0;i<length;i++) {
        int phase=((int)i-timing)%RADE_NMF;if(phase<0)phase+=RADE_NMF;
        if(phase<RADE_M)stream[i]=rade_cadd(stream[i],a->ofdm.p[phase]);
    }
    memcpy(a->rx_buf,stream,sizeof(a->rx_buf));memcpy(b->rx_buf,stream,sizeof(b->rx_buf));
    for(int i=0;i<6;i++) {
        step(stream+RADE_RX_BUF_SIZE+(size_t)i*RADE_NMF);
        if(a->tmax!=timing||a->fmax!=0.0f) {
            fprintf(stderr,"%s: expected t=%d f=0, got t=%d f=%g\n",name,timing,a->tmax,a->fmax);failures++;
        }
    }
    free(stream);finish_case();case_name=NULL;
}
static void modem_stream(const char *name,float amplitude,float noise,float cfo,int offset,int interrupt) {
    const size_t length=(interrupt?76:22)*RADE_NMF+RADE_RX_BUF_SIZE;
    RADE_COMP *stream=malloc(length*sizeof(*stream));if(!stream)abort();
    init_case(name,0);make_noise(stream,length,noise);
    float z[RADE_NZMF*RADE_LATENT_DIM];RADE_COMP frame[RADE_NMF];
    for(int i=0;i<RADE_NZMF*RADE_LATENT_DIM;i++)z[i]=random_signed()*.4f;
    if(rade_ofdm_mod_frame(&a->ofdm,frame,z)!=RADE_NMF)abort();
    for(size_t pos=(size_t)offset;pos+RADE_NMF<=length;pos+=RADE_NMF) {
        if(interrupt&&pos>=22*RADE_NMF&&pos<51*RADE_NMF)continue;
        for(int n=0;n<RADE_NMF;n++) {
            RADE_COMP v=rade_cmul(frame[n],rade_cexp(2.0f*(float)M_PI*cfo*(float)(pos+n)/RADE_FS));
            stream[pos+n]=rade_cadd(stream[pos+n],rade_cscale(v,amplitude));
        }
    }
    size_t pos=0;int count=0,post_interrupt_sync=0;
    while(pos+(size_t)a->nin<=length && count++<(interrupt?74:20)) {
        int n=a->nin;step(stream+pos);pos+=(size_t)n;
        if(interrupt&&pos>=51*RADE_NMF&&a->state==RADE_STATE_SYNC)post_interrupt_sync++;
    }
    if(amplitude>=1 && sync_entries-case_start_sync<(unsigned)(interrupt?2:1)) {
        fprintf(stderr,"%s: expected actual acquisition%s\n",name,interrupt?" and reacquisition":"");failures++;
    }
    if(interrupt&&!post_interrupt_sync) { fprintf(stderr,"%s: no sync after interruption\n",name);failures++; }
    free(stream);
}
int main(int argc,char **argv) {
    char *end=NULL;
    long requested=argc>=2?strtol(argv[1],&end,10):0;
    if(argc<2||argc>3||!end||*end||requested<1||requested>4)return 2;
    workers=(unsigned)requested;
    int quick=argc>2&&!strcmp(argv[2],"--quick");
    int boundaries=argc>2&&!strcmp(argv[2],"--boundaries");
    if(argc>2&&!quick&&!boundaries)return 2;
    a=calloc(1,sizeof(*a));if(!a)return 2;
    if(boundaries) {
        exact_timing_boundary(0);exact_timing_boundary(RADE_NMF-1);
        printf("{\"summary\":true,\"boundaries_only\":true,\"receiver_calls\":%u,\"parallel_calls\":%u,\"failures\":%u}\n",calls,parallel_calls,failures);
        free(a);rade_close(rb_handle);return failures?1:0;
    }
    noise_stream("digital-zero",0,5,0);
    noise_stream("seeded-nonzero-noise",.04f,6,0);
    noise_stream("seeded-noise-bpf",.04f,5,1);
    reset_and_shifts();config_changes();retained_mutation_and_tails();numeric_transitions();
    fp_control_transition();reinitialization();direct_calls();interleaved_instances();
    exact_timing_boundary(0);exact_timing_boundary(RADE_NMF-1);
    modem_stream("valid-modem-and-recovery",1,.01f,1.25f,173,!quick);
    if(!quick) {
        noise_stream("low-normal-numeric-domain",1e-20f,4,0);
        noise_stream("high-level-noise",1000,4,0);
        modem_stream("weak-between-bins",.15f,.10f,-48.75f,0,0);
        modem_stream("weak-upper-bin-timing-boundary",.15f,.10f,47.5f,959,0);
        modem_stream("strong-lower-bin",1,.01f,-50,959,0);
    }
    finish_case();
    if(!feature_calls||!(states_seen&(1u<<RADE_STATE_CANDIDATE))||!(states_seen&(1u<<RADE_STATE_SYNC)))failures++;
#if RADE_ACQ_EXPECT_NEON
    if(workers>1&&!parallel_calls)failures++;
#else
    if(parallel_calls)failures++;
#endif
    printf("{\"summary\":true,\"workers\":%u,\"quick\":%s,\"receiver_calls\":%u,\"search_calls\":%u,\"sync_calls\":%u,\"parallel_calls\":%u,\"feature_calls\":%u,\"states_seen\":%u,\"failures\":%u,\"radio_qualified\":false}\n",workers,quick?"true":"false",calls,search_calls,sync_calls,parallel_calls,feature_calls,states_seen,failures);
    free(a);rade_close(rb_handle);return failures?1:0;
}
