/* SPDX-License-Identifier: BSD-2-Clause
 * Live USB speech TX, then paced file-channel RX; one retained real engine.
 * No radio, I2C, RF or PTT access. argv: source sink output_dir seconds turns.
 * Both PA streams remain open/running; inactive input is discarded, output is
 * silence except decoded speech. PCM: 48000 Hz mono S16LE. */
#define _POSIX_C_SOURCE 200809L
#include "../../src/freedv/voice_engine.h"
#include <pulse/pulseaudio.h>
#include <dirent.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#define RATE 48000u
#define LIMIT 24000u
#define TURNS 3u
typedef struct { int16_t pcm[LIMIT]; unsigned head,n,high; } queue;
static queue microphone, speaker;
static pa_mainloop *loop;
static pa_context *context;
static pa_stream *input,*output;
static fd_voice_engine *engine;
static const char *source,*sink,*fault="",*phase="prepare",*fault_phase="";
static FILE *trace;
static unsigned turn,seconds,turns,admit,render,render_started,payload_window;
static unsigned holes,overflows,pa_underflows,gaps,padding,first_audio_ms;
static unsigned sink_rate,source_rate,sink_channels,source_channels;
static unsigned granted_fragment,granted_target,granted_prebuffer;
static unsigned capture_callbacks,max_capture_gap,stream_creations;
static uint64_t start,last_capture,generation,admitted,target,sink_samples,rx_started,last_trace,last_timing;
static uint64_t timing_start_ms,timing_end_ms,baseline_index,latest_index;
static unsigned timing_samples;
static int timing_pending;
static void fail(const char *why){if(!*fault){fault=why;fault_phase=phase;fprintf(stderr,"STOP: %s\n",why);}}
static uint64_t ms(void){struct timespec t;clock_gettime(CLOCK_MONOTONIC,&t);return (uint64_t)t.tv_sec*1000+t.tv_nsec/1000000;}
static int name_ok(const char *name,const char *prefix){return strlen(name)<512 && !strncmp(name,prefix,strlen(prefix)) && strspn(name,"abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-")==strlen(name);}
static unsigned descriptors(void){DIR *d=opendir("/proc/self/fd");if(!d)return 0;unsigned n=0;struct dirent *e;while((e=readdir(d)))if(e->d_name[0]!='.')++n;closedir(d);return n;}
static void push(queue *q,const int16_t *p,unsigned n){
    if(n>LIMIT-q->n){fail("PCM queue exceeded 500 ms bound");return;}
    for(unsigned i=0;i<n;++i)q->pcm[(q->head+q->n+i)%LIMIT]=p[i];
    q->n+=n;if(q->n>q->high)q->high=q->n;
}
static unsigned pop(queue *q,int16_t *p,unsigned n){
    if(n>q->n)n=q->n;
    for(unsigned i=0;i<n;++i)p[i]=q->pcm[(q->head+i)%LIMIT];
    q->head=(q->head+n)%LIMIT;q->n-=n;return n;
}
static void binding(pa_stream *s,void *unused){(void)unused;const char *actual=pa_stream_get_device_name(s);if(!actual || strcmp(actual,s==input?source:sink))fail("USB endpoint binding changed");}
static void context_state(pa_context *c,void *unused){(void)unused;if(!PA_CONTEXT_IS_GOOD(pa_context_get_state(c)))fail("Pulse context failed");}
static void stream_state(pa_stream *s,void *unused){(void)unused;pa_stream_state_t state=pa_stream_get_state(s);if(state==PA_STREAM_READY)binding(s,NULL);else if(!PA_STREAM_IS_GOOD(state))fail("USB stream failed or disappeared");}
static void overflow(pa_stream *s,void *unused){(void)s;(void)unused;++overflows;fail("Capture overflow");}
static void underflow(pa_stream *s,void *unused){(void)s;(void)unused;if(start)++pa_underflows;}
static void read_pcm(pa_stream *s,size_t requested,void *unused){
    (void)requested;(void)unused;
    for(unsigned loops=0;loops<64;++loops){
        const void *data=NULL;size_t bytes=0;
        if(pa_stream_peek(s,&data,&bytes)<0){fail("Capture peek failed");return;}
        if(!bytes)return;
        uint64_t now=ms();++capture_callbacks;
        if(last_capture && now-last_capture>max_capture_gap)max_capture_gap=(unsigned)(now-last_capture);
        last_capture=now;
        if(!data){++holes;fail("Capture hole");}
        else if(bytes%2)fail("Capture partial sample");
        else if(admit && admitted<target && !*fault){
            unsigned n=(unsigned)(bytes/2);if(n>target-admitted)n=(unsigned)(target-admitted);
            push(&microphone,data,n);admitted+=n;
        }
        if(pa_stream_drop(s)<0){fail("Capture drop failed");return;}
    }
    fail("Unbounded capture burst");
}
static void write_pcm(pa_stream *s,size_t bytes,void *unused){
    (void)unused;int16_t pcm[2048];
    if(bytes>RATE*2){fail("Unbounded playback request");return;}
    while(bytes && !*fault){
        unsigned n=(unsigned)(bytes/2);if(n>2048)n=2048;if(!n){fail("Playback partial sample");break;}
        memset(pcm,0,n*2);
        if(render){
            if(!render_started && speaker.n>=RATE/4){render_started=1;first_audio_ms=(unsigned)(ms()-rx_started);}
            if(render_started){
                unsigned got=pop(&speaker,pcm,n);
                if(got<n){padding+=n-got;if(payload_window)++gaps;}
            }
        }
        if(pa_stream_write(s,pcm,n*2,NULL,0,PA_SEEK_RELATIVE)<0){fail("Playback write failed");break;}
        sink_samples+=n;bytes-=n*2;
    }
}
static void sink_info(pa_context *c,const pa_sink_info *info,int end,void *unused){(void)c;(void)unused;if(!end && info){sink_rate=info->sample_spec.rate;sink_channels=info->sample_spec.channels;}}
static void source_info(pa_context *c,const pa_source_info *info,int end,void *unused){(void)c;(void)unused;if(!end && info){source_rate=info->sample_spec.rate;source_channels=info->sample_spec.channels;}}
static void timing(pa_stream *s,int success,void *unused){
    (void)unused;timing_pending=0;const pa_timing_info *t=success?pa_stream_get_timing_info(s):NULL;
    if(t && !t->read_index_corrupt && t->read_index>=0){
        uint64_t now=ms();latest_index=(uint64_t)t->read_index;timing_end_ms=now;
        if(!timing_start_ms && start && now-start>=1000){timing_start_ms=now;baseline_index=latest_index;}
        if(timing_start_ms)++timing_samples;
    }
}
static int tick(void){
    if(*fault)return 0;
    int result=0;if(pa_mainloop_iterate(loop,0,&result)<0)fail("Pulse mainloop failed");
    uint64_t now=ms();
    if(start && !timing_pending && now-last_timing>=500 && pa_stream_get_state(output)==PA_STREAM_READY){
        last_timing=now;timing_pending=1;
        pa_operation *o=pa_stream_update_timing_info(output,timing,NULL);if(o)pa_operation_unref(o);else timing_pending=0;
        o=pa_context_get_sink_info_by_name(context,sink,sink_info,NULL);if(o)pa_operation_unref(o);
        o=pa_context_get_source_info_by_name(context,source,source_info,NULL);if(o)pa_operation_unref(o);
    }
    if(engine && start && now-last_trace>=100){
        last_trace=now;fd_voice_stats s=fd_voice_observe(engine);
        if(s.failures || s.cancelled)fail("Codec engine failed or cancelled");
        if(s.input_depth>=LIMIT || s.output_depth>RATE/2)fail("Codec backlog exceeded 500 ms bound");
        fprintf(trace,"{\"ms\":%llu,\"turn\":%u,\"phase\":\"%s\",\"generation\":%llu,\"input_depth\":%u,\"output_depth\":%u,\"mic_queue\":%u,\"speaker_queue\":%u,\"read_index\":%llu,\"read_snapshot_ms\":%llu,\"sink_rate\":%u,\"source_rate\":%u,\"render_gap_callbacks\":%u}\n",
            (unsigned long long)(now-start),turn,phase,(unsigned long long)generation,s.input_depth,s.output_depth,
            microphone.n,speaker.n,(unsigned long long)latest_index,(unsigned long long)(timing_end_ms?timing_end_ms-start:0),sink_rate,source_rate,gaps);
        if(ferror(trace))fail("Trace write failed");
    }
    struct timespec pause={0,1000000};nanosleep(&pause,NULL);return !*fault;
}
static int wait_ms(unsigned duration){uint64_t end=ms()+duration;while(ms()<end && tick()){}return !*fault;}
static int transition(fd_voice_direction direction){
    admit=render=render_started=payload_window=0;microphone.head=microphone.n=speaker.head=speaker.n=0;
    if(!fd_voice_transition(engine,direction,++generation)){fail("Codec direction transition failed");return 0;}return 1;
}
static FILE *new_file(const char *dir,unsigned n,const char *suffix){char p[2048];snprintf(p,sizeof(p),"%s/turn-%u-%s",dir,n,suffix);FILE *f=fopen(p,"wbx");if(!f)fail("Evidence file open failed");return f;}
static unsigned engine_output(FILE *file,int to_speaker){
    int16_t pcm[4096];unsigned total=0;
    for(unsigned i=0;i<16;++i){
        unsigned capacity=to_speaker?LIMIT-speaker.n:4096;if(capacity>4096)capacity=4096;if(!capacity)break;
        unsigned n=(unsigned)fd_voice_receive(engine,generation,pcm,capacity);if(!n)break;
        if(fwrite(pcm,2,n,file)!=n){fail("Evidence PCM write failed");break;}
        if(to_speaker)push(&speaker,pcm,n);
        total+=n;
    }
    return total;
}
static int tx_turn(const char *dir){
    FILE *mic=new_file(dir,turn,"microphone.raw"),*modem=new_file(dir,turn,"modem.raw");
    if(!mic || !modem){if(mic)fclose(mic);if(modem)fclose(modem);return 0;}
    phase="countdown";printf("Turn %u: speak for %u seconds at GO. Say 'Turn %u. One two three four five. End turn %u.'\n",turn,seconds,turn,turn);
    for(unsigned n=3;n && !*fault;--n){printf("%u\n",n);wait_ms(1000);}
    if(!*fault)transition(FD_VOICE_TX);
    admitted=0;target=seconds*RATE;phase="TX";admit=1;
    uint64_t began=ms(),deadline=began+(seconds+3)*1000;
    printf("GO - turn %u, speak now.\n",turn);
    while(!*fault && (admitted<target || microphone.n)){
        if(ms()>deadline){fail("Live microphone deadline exceeded");break;}
        int16_t pcm[480];unsigned n=pop(&microphone,pcm,480);
        if(n){if(fwrite(pcm,2,n,mic)!=n)fail("Microphone file write failed");if(fd_voice_submit(engine,generation,pcm,n)!=n)fail("Live TX input rejected");}
        engine_output(modem,0);tick();
    }
    admit=0;phase="TX_tail";uint64_t released=ms();
    if(!*fault && !fd_voice_finish(engine,generation))fail("TX finish rejected");
    while(!*fault){engine_output(modem,0);fd_voice_stats s=fd_voice_observe(engine);if(s.finished && !s.output_depth)break;if(ms()-released>3000){fail("TX modem tail exceeded 3 s bound");break;}tick();}
    fd_voice_stats s=fd_voice_observe(engine);
    fprintf(trace,"{\"turn\":%u,\"event\":\"tx_end\",\"capture_ms\":%llu,\"tail_ms\":%llu,\"admitted_samples\":%llu,\"eoo_count\":%llu}\n",turn,(unsigned long long)(released-began),(unsigned long long)(ms()-released),(unsigned long long)admitted,(unsigned long long)s.eoo_count);
    if(fclose(mic))fail("Microphone file close failed");
    if(fclose(modem))fail("Modem file close failed");
    if(!*fault && (admitted!=target || s.eoo_count!=turn))fail("TX sample or EOO count mismatch");
    return !*fault;
}
static int rx_turn(const char *dir){
    char path[2048];snprintf(path,sizeof(path),"%s/turn-%u-modem.raw",dir,turn);
    FILE *in=fopen(path,"rb"),*decoded=new_file(dir,turn,"decoded.raw");
    if(!in || !decoded){if(in)fclose(in);if(decoded)fclose(decoded);fail("RX channel file unavailable");return 0;}
    if(fseek(in,0,SEEK_END) || ftell(in)<=0 || ftell(in)>(long)(RATE*2*20) || ftell(in)%2){fail("RX channel file length invalid");goto done;}
    uint64_t channel_samples=(uint64_t)ftell(in)/2;rewind(in);
    if(!transition(FD_VOICE_RX))goto done;
    phase="RX";render=1;rx_started=ms();first_audio_ms=0;unsigned turn_gaps=gaps;
    uint64_t sent=0,last_output=ms(),decoded_samples=0;
    printf("Turn %u: RX - listen to your decoded speech now.\n",turn);
    while(!*fault && sent<channel_samples+RATE){
        unsigned received=engine_output(decoded,1);decoded_samples+=received;if(received)last_output=ms();
        payload_window=sent+RATE<channel_samples;
        if(ms()-rx_started>1000*(channel_samples/RATE+5)){fail("Live RX deadline exceeded");break;}
        if((ms()-rx_started)*RATE/1000>=sent){
            int16_t pcm[480]={0};unsigned n=480;
            if(sent<channel_samples){if(n>channel_samples-sent)n=(unsigned)(channel_samples-sent);if(fread(pcm,2,n,in)!=n){fail("RX channel file read failed");break;}}
            else if(n>channel_samples+RATE-sent)n=(unsigned)(channel_samples+RATE-sent);
            if(fd_voice_submit(engine,generation,pcm,n)!=n){fail("Paced RX input rejected");break;}sent+=n;
        }
        tick();
    }
    payload_window=0;phase="RX_tail";uint64_t tail=ms();
    while(!*fault && ms()-tail<2000){
        unsigned n=engine_output(decoded,1);decoded_samples+=n;if(n)last_output=ms();
        if(render_started && !speaker.n && ms()-last_output>500 && ms()-tail>500)break;
        tick();
    }
    if(!render_started || decoded_samples<RATE)fail("RX did not produce playable decoded speech");
    if(speaker.n)fail("Decoded speech tail did not leave queue");
    fprintf(trace,"{\"turn\":%u,\"event\":\"rx_end\",\"elapsed_ms\":%llu,\"decoded_samples\":%llu,\"first_audio_ms\":%u,\"payload_gap_callbacks\":%u}\n",turn,(unsigned long long)(ms()-rx_started),(unsigned long long)decoded_samples,first_audio_ms,gaps-turn_gaps);
    render=render_started=0;
done:
    if(fclose(in))fail("Channel input close failed");
    if(fclose(decoded))fail("Decoded output close failed");
    return !*fault;
}
int main(int argc,char **argv){
    if(argc!=6 || !name_ok(argv[1],"alsa_input.usb-") || !name_ok(argv[2],"alsa_output.usb-"))return 2;
    source=argv[1];sink=argv[2];char *end=NULL;seconds=(unsigned)strtoul(argv[4],&end,10);if(!*argv[4] || *end || seconds<2 || seconds>12)return 2;
    turns=(unsigned)strtoul(argv[5],&end,10);if(!*argv[5] || *end || !turns || turns>TURNS)return 2;
    setbuf(stdout,NULL);char path[2048];snprintf(path,sizeof(path),"%s/trace.jsonl",argv[3]);trace=fopen(path,"wx");if(!trace)return 2;
    printf("Preparing retained actual codec. No RF or PTT access. Keep all PTT released.\n");
    engine=fd_voice_create(RATE);if(!engine){fail("Codec prepare failed");goto done;}
    loop=pa_mainloop_new();context=loop?pa_context_new(pa_mainloop_get_api(loop),"X6200 half-duplex USB test"):NULL;
    if(!context){fail("Pulse allocation failed");goto done;}
    pa_context_set_state_callback(context,context_state,NULL);if(pa_context_connect(context,NULL,PA_CONTEXT_NOAUTOSPAWN,NULL)<0){fail("Pulse connect failed");goto done;}
    uint64_t deadline=ms()+5000;while(pa_context_get_state(context)!=PA_CONTEXT_READY && tick()){if(ms()>deadline){fail("Pulse connect deadline exceeded");break;}}
    if(*fault)goto done;
    pa_sample_spec spec={PA_SAMPLE_S16LE,RATE,1};pa_buffer_attr attr={.maxlength=19200,.tlength=9600,.prebuf=0,.minreq=1920,.fragsize=1920};
    output=pa_stream_new(context,"Retained USB decoded speech",&spec,NULL);input=pa_stream_new(context,"Retained USB microphone",&spec,NULL);
    if(!output || !input){fail("Stream allocation failed");goto done;}stream_creations=2;
    pa_stream_set_state_callback(output,stream_state,NULL);pa_stream_set_moved_callback(output,binding,NULL);pa_stream_set_write_callback(output,write_pcm,NULL);pa_stream_set_underflow_callback(output,underflow,NULL);
    pa_stream_set_state_callback(input,stream_state,NULL);pa_stream_set_moved_callback(input,binding,NULL);pa_stream_set_read_callback(input,read_pcm,NULL);pa_stream_set_overflow_callback(input,overflow,NULL);
    pa_cvolume volume;pa_cvolume_set(&volume,1,PA_VOLUME_NORM/2);pa_stream_flags_t flags=PA_STREAM_ADJUST_LATENCY|PA_STREAM_DONT_MOVE|PA_STREAM_AUTO_TIMING_UPDATE;
    if(pa_stream_connect_playback(output,sink,&attr,flags,&volume,NULL)<0 || pa_stream_connect_record(input,source,&attr,flags)<0){fail("Named USB endpoint unavailable");goto done;}
    deadline=ms()+5000;while((pa_stream_get_state(output)!=PA_STREAM_READY || pa_stream_get_state(input)!=PA_STREAM_READY) && tick()){if(ms()>deadline){fail("Stream ready deadline exceeded");break;}}
    if(*fault)goto done;
    const pa_buffer_attr *actual=pa_stream_get_buffer_attr(output);if(actual){granted_target=actual->tlength;granted_prebuffer=actual->prebuf;}
    actual=pa_stream_get_buffer_attr(input);if(actual)granted_fragment=actual->fragsize;
    transition(FD_VOICE_IDLE);
    unsigned baseline=descriptors();start=ms();phase="idle";
    wait_ms(1000);
    for(turn=1;turn<=turns && !*fault;++turn){
        if(!tx_turn(argv[3]) || !rx_turn(argv[3]))break;
        transition(FD_VOICE_IDLE);phase="idle";wait_ms(1000);
        if(descriptors()!=baseline)fail("Descriptor count grew across turns");
    }
    if(!*fault)phase="complete";
done:
    admit=render=0;fd_voice_stats stats=fd_voice_observe(engine);
    if(engine)fd_voice_cancel(engine);
    uint64_t elapsed=start?ms()-start:0;
    if(input){pa_stream_set_state_callback(input,NULL,NULL);pa_stream_disconnect(input);pa_stream_unref(input);}
    if(output){pa_stream_set_state_callback(output,NULL,NULL);pa_stream_disconnect(output);pa_stream_unref(output);}
    if(context){pa_context_set_state_callback(context,NULL,NULL);pa_context_disconnect(context);pa_context_unref(context);}
    if(loop)pa_mainloop_free(loop);
    fd_voice_destroy(engine);
    double read_rate=(timing_end_ms>timing_start_ms && latest_index>=baseline_index)?(latest_index-baseline_index)*500.0/(timing_end_ms-timing_start_ms):0;
    snprintf(path,sizeof(path),"%s/session.json",argv[3]);FILE *report=fopen(path,"wx");
    if(report){fprintf(report,"{\"schema\":\"x6200.usb-half-duplex.v1\",\"ok\":%s,\"fault\":\"%s\",\"phase\":\"%s\",\"client_rate\":%u,\"seconds_per_tx\":%u,\"turns_requested\":%u,\"turns_completed\":%u,\"stream_creations\":%u,\"rade_creations\":%u,\"encoder_creations\":%u,\"decoder_creations\":%u,\"worker_creations\":%u,\"eoo_count\":%llu,\"speech_frames\":%llu,\"synthesis_blocks\":%llu,\"failures\":%u,\"input_high_water\":%u,\"output_high_water\":%u,\"mic_queue_high_water\":%u,\"speaker_queue_high_water\":%u,\"holes\":%u,\"capture_overflows\":%u,\"pa_underflows\":%u,\"render_gap_callbacks\":%u,\"render_padding_samples\":%u,\"sink_rate\":%u,\"source_rate\":%u,\"sink_channels\":%u,\"source_channels\":%u,\"read_rate_hz\":%.3f,\"timing_samples\":%u,\"granted_fragment_bytes\":%u,\"granted_target_bytes\":%u,\"granted_prebuffer_bytes\":%u,\"max_capture_gap_ms\":%u,\"capture_callbacks\":%u,\"elapsed_ms\":%llu,\"rf_tested\":false,\"ptt_tested\":false,\"listening\":\"UNASSESSED\"}\n",
        *fault?"false":"true",fault,*fault?fault_phase:phase,RATE,seconds,turns,*fault?(turn?turn-1:0):turns,stream_creations,stats.rade_creations,stats.encoder_creations,stats.decoder_creations,stats.worker_creations,(unsigned long long)stats.eoo_count,(unsigned long long)stats.speech_frames,(unsigned long long)stats.synthesis_blocks,stats.failures,stats.input_high_water,stats.output_high_water,microphone.high,speaker.high,holes,overflows,pa_underflows,gaps,padding,sink_rate,source_rate,sink_channels,source_channels,read_rate,timing_samples,granted_fragment,granted_target,granted_prebuffer,max_capture_gap,capture_callbacks,(unsigned long long)elapsed);
        if(fclose(report))fail("Session report close failed");
    }else fail("Session report unavailable");
    if(fclose(trace))fail("Trace close failed");
    printf("Session %s. Listening review required; no RF qualification.\n",*fault?"BLOCKED":"COMPLETE");return *fault?1:0;
}
