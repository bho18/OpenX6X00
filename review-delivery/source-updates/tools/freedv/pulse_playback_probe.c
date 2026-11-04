/* Actual audio.c playback, private virtual PulseAudio only. No radio/codec. */
#include "src/audio.c"
#include <assert.h>
#include <stdarg.h>
void freedv_mode_device_fault(void){abort();}
void _lv_log_add(lv_log_level_t level,const char *file,int line,const char *func,const char *fmt,...){
 (void)level;(void)file;(void)line;(void)func;va_list a;va_start(a,fmt);vfprintf(stderr,fmt,a);va_end(a);fputc('\n',stderr);
}
static void report(audio_freedv_playback_t *p,unsigned turn,unsigned elapsed,uint64_t accepted){
 pa_threaded_mainloop_lock(mloop);
 pa_operation *op=pa_stream_update_timing_info(p->stream,NULL,NULL);if(op)pa_operation_unref(op);
 const pa_timing_info *t=pa_stream_get_timing_info(p->stream);const pa_buffer_attr *b=pa_stream_get_buffer_attr(p->stream);
 printf("{\"turn\":%u,\"ms\":%u,\"accepted\":%llu,\"writable\":%zu,\"corked\":%d,\"prebuf\":%u,\"tlength\":%u,\"minreq\":%u,\"underflows\":%llu,\"timing\":%s,\"playing\":%d,\"write_index\":%lld,\"read_index\":%lld,\"sink_usec\":%llu}\n",
 turn,elapsed,(unsigned long long)accepted,pa_stream_writable_size(p->stream),pa_stream_is_corked(p->stream),b->prebuf,b->tlength,b->minreq,(unsigned long long)p->stats.generation_underflows,t?"true":"false",t?t->playing:-1,(long long)(t?t->write_index:0),(long long)(t?t->read_index:0),(unsigned long long)(t?t->sink_usec:0));
 fflush(stdout);pa_threaded_mainloop_unlock(mloop);
}
int main(int argc,char **argv){
 assert(argc==2 && getenv("PULSE_SERVER") && !strncmp(getenv("PULSE_SERVER"),"unix:/tmp/freedv-pulse-probe-",27));
 bool trigger=!strcmp(argv[1],"trigger");play_device="freedv_probe_sink";
 mloop=pa_threaded_mainloop_new();assert(!pa_threaded_mainloop_start(mloop));mlapi=pa_threaded_mainloop_get_api(mloop);context_connect();
 audio_freedv_playback_t *p=audio_freedv_playback_open();assert(p);
 for(unsigned turn=1;turn<=3;++turn){
  /* Production retains an uncorked empty stream between turns. Introducing
   * cork/uncork here exercises device resume instead of that lifecycle. */
  assert(audio_freedv_playback_cancel(p));
  unsigned start=audio_clock_ms();while(!audio_freedv_playback_quiescent(p)){assert(audio_clock_ms()-start<500);usleep(2000);}
  assert(audio_freedv_playback_rearm(p,turn));
  int16_t pcm[441]={0};uint64_t accepted=0;unsigned last=0;bool fired=false;start=audio_clock_ms();
  while(audio_clock_ms()-start<1800){
   unsigned elapsed=audio_clock_ms()-start;
   /* Production: one first block, including the measured 110-sample SRC delay. */
   uint64_t available=elapsed>=200?((elapsed-200)/120+1)*5292-110:0;
   /* Match bounded production submission and never invent the fractional
    * first-block samples still held by the resampler. */
   size_t submitted=0;
   for(unsigned attempt=0;attempt<64 && submitted<44100/5 && accepted<available;++attempt){
    size_t wanted=available-accepted<441?(size_t)(available-accepted):441;
    if(wanted>44100/5-submitted)wanted=44100/5-submitted;
    ssize_t n=audio_freedv_playback_submit_generation(p,turn,pcm,wanted);assert(n>=0);
    if(!n)break;accepted+=(uint64_t)n;submitted+=(size_t)n;
   }
   if(trigger && elapsed>600 && !fired){pa_threaded_mainloop_lock(mloop);pa_operation *op=pa_stream_trigger(p->stream,NULL,NULL);assert(op);pa_operation_unref(op);pa_threaded_mainloop_unlock(mloop);fired=true;}
   if(elapsed>=last+100){report(p,turn,elapsed,accepted);last=elapsed;}
   usleep(2000);
  }
  assert(audio_freedv_playback_drain(p,turn));start=audio_clock_ms();
  while(!audio_freedv_playback_drained(p,turn) && audio_clock_ms()-start<1500)usleep(2000);
  printf("{\"turn\":%u,\"drained\":%d,\"accepted\":%llu}\n",turn,audio_freedv_playback_drained(p,turn),(unsigned long long)accepted);
 }
 audio_freedv_playback_close(p);pa_threaded_mainloop_stop(mloop);pa_threaded_mainloop_free(mloop);return 0;
}
