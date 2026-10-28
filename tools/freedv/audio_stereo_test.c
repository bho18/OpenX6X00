// SPDX-License-Identifier: LGPL-2.1-or-later
/* Exercise actual diagnostic stereo/ALSA adapters without opening devices. */
#ifdef HOST_BUILD
#undef HOST_BUILD
#endif
#define BOARD_X6200 1
#define ENABLE_FREEDV_DIAGNOSTICS 1
#include "src/audio.c"
#include <assert.h>

static int token_loop, token_context, token_capture, token_stereo, token_network, token_mixer, token_elems[3];
static unsigned locks, disconnects, dsp_sample_count;
static const void *peek_data;
static size_t peek_bytes;
static char selected_elem[64];
static long play_values[2] = {160, 161}, capture_values[2] = {145, 146};
static unsigned enum_values[2] = {3, 2};
static const char *enum_names[] = {"Stereo", "Reverse Stereo", "Sum Mono", "Mix Mono"};
static bool fail_read;

void pa_threaded_mainloop_lock(pa_threaded_mainloop *m) { assert(m == mloop && !locks); ++locks; }
void pa_threaded_mainloop_unlock(pa_threaded_mainloop *m) { assert(m == mloop && locks == 1); --locks; }
void pa_threaded_mainloop_signal(pa_threaded_mainloop *m, int accept) { (void)m; (void)accept; }
void pa_threaded_mainloop_wait(pa_threaded_mainloop *m) { (void)m; assert(0); }
pa_stream_state_t pa_stream_get_state(const pa_stream *s) { assert(s && locks == 1); return PA_STREAM_READY; }
pa_channel_map *pa_channel_map_init_stereo(pa_channel_map *map) {
    map->channels = 2; map->map[0] = PA_CHANNEL_POSITION_FRONT_LEFT; map->map[1] = PA_CHANNEL_POSITION_FRONT_RIGHT;
    return map;
}
pa_stream *pa_stream_new(pa_context *c, const char *name, const pa_sample_spec *spec, const pa_channel_map *map) {
    (void)name; assert(c == ctx && locks == 1);
    if (spec->channels == 2) { assert(map && map->channels == 2); return (pa_stream *)&token_stereo; }
    assert(spec->channels == 1 && !map); return (pa_stream *)&token_network;
}
void pa_stream_set_state_callback(pa_stream *s, pa_stream_notify_cb_t cb, void *arg) { (void)s; (void)cb; assert(!arg && locks == 1); }
void pa_stream_set_read_callback(pa_stream *s, pa_stream_request_cb_t cb, void *arg) { (void)s; (void)cb; assert(!arg && locks == 1); }
int pa_stream_connect_record(pa_stream *s, const char *device, const pa_buffer_attr *attr, pa_stream_flags_t flags) {
    (void)device; (void)flags; assert(s == (pa_stream *)&token_stereo && locks == 1);
    assert(attr->fragsize == 1764 && attr->maxlength == 35280); return 0;
}
int pa_stream_connect_playback(pa_stream *s, const char *device, const pa_buffer_attr *attr, pa_stream_flags_t flags,
                               const pa_cvolume *volume, pa_stream *sync) {
    (void)device; (void)flags; assert(s == (pa_stream *)&token_network && attr && !volume && !sync && locks == 1); return 0;
}
size_t pa_usec_to_bytes(pa_usec_t usec, const pa_sample_spec *spec) { return usec * spec->rate * spec->channels * 2 / 1000000; }
int pa_stream_disconnect(pa_stream *s) { assert(s && locks == 1); ++disconnects; return 0; }
void pa_stream_unref(pa_stream *s) { assert(s && locks == 1); }
int pa_stream_peek(pa_stream *s, const void **data, size_t *bytes) { assert(s); *data=peek_data; *bytes=peek_bytes; return 0; }
int pa_stream_drop(pa_stream *s) { assert(s); return 0; }
int pa_context_errno(const pa_context *c) { assert(c == ctx); return 0; }
const char *pa_strerror(int error) { (void)error; return "mock"; }
void dsp_put_audio_samples(size_t count, int16_t *samples) { assert(samples); dsp_sample_count += (unsigned)count; }
void _lv_log_add(lv_log_level_t level, const char *file, int line, const char *func, const char *format, ...) {
    (void)level; (void)file; (void)line; (void)func; (void)format;
}

int snd_mixer_open(snd_mixer_t **m, int mode) { assert(mode == 0); *m=(snd_mixer_t *)&token_mixer; return 0; }
int snd_mixer_attach(snd_mixer_t *m, const char *name) { assert(m && !strcmp(name,"default")); return 0; }
int snd_mixer_selem_register(snd_mixer_t *m, struct snd_mixer_selem_regopt *options, snd_mixer_class_t **classp) {
    assert(m && !options && !classp); return 0;
}
int snd_mixer_load(snd_mixer_t *m) { assert(m); return 0; }
int snd_mixer_close(snd_mixer_t *m) { assert(m); return 0; }
size_t snd_mixer_selem_id_sizeof(void) { return 8; }
void snd_mixer_selem_id_set_index(snd_mixer_selem_id_t *id, unsigned index) { assert(id && !index); }
void snd_mixer_selem_id_set_name(snd_mixer_selem_id_t *id, const char *name) { assert(id); snprintf(selected_elem,sizeof(selected_elem),"%s",name); }
snd_mixer_elem_t *snd_mixer_find_selem(snd_mixer_t *m, const snd_mixer_selem_id_t *id) {
    assert(m && id);
    const char *names[]={"AIF1 DA0","ADC","AIF1 AD0 Stereo"};
    for(unsigned i=0;i<3;++i) if(!strcmp(selected_elem,names[i])) return (snd_mixer_elem_t *)&token_elems[i];
    return NULL;
}
int snd_mixer_selem_is_enumerated(snd_mixer_elem_t *e) { return e==(snd_mixer_elem_t *)&token_elems[2]; }
int snd_mixer_selem_get_enum_items(snd_mixer_elem_t *e) { assert(e==(snd_mixer_elem_t *)&token_elems[2]); return 4; }
int snd_mixer_selem_get_enum_item_name(snd_mixer_elem_t *e, unsigned item, size_t size, char *name) {
    assert(e==(snd_mixer_elem_t *)&token_elems[2] && item<4); snprintf(name,size,"%s",enum_names[item]); return 0;
}
int snd_mixer_selem_get_playback_volume(snd_mixer_elem_t *e, snd_mixer_selem_channel_id_t c, long *v) {
    assert(e==(snd_mixer_elem_t *)&token_elems[0] && (unsigned)c<2); if(fail_read)return -1; *v=play_values[c]; return 0;
}
int snd_mixer_selem_get_capture_volume(snd_mixer_elem_t *e, snd_mixer_selem_channel_id_t c, long *v) {
    assert(e==(snd_mixer_elem_t *)&token_elems[1] && (unsigned)c<2); *v=capture_values[c]; return 0;
}
int snd_mixer_selem_set_playback_volume(snd_mixer_elem_t *e, snd_mixer_selem_channel_id_t c, long v) {
    assert(e==(snd_mixer_elem_t *)&token_elems[0] && (unsigned)c<2); play_values[c]=v; return 0;
}
int snd_mixer_selem_set_capture_volume(snd_mixer_elem_t *e, snd_mixer_selem_channel_id_t c, long v) {
    assert(e==(snd_mixer_elem_t *)&token_elems[1] && (unsigned)c<2); capture_values[c]=v; return 0;
}
int snd_mixer_selem_get_enum_item(snd_mixer_elem_t *e, snd_mixer_selem_channel_id_t c, unsigned *v) {
    assert(e==(snd_mixer_elem_t *)&token_elems[2] && (unsigned)c<2); *v=enum_values[c]; return 0;
}
int snd_mixer_selem_set_enum_item(snd_mixer_elem_t *e, snd_mixer_selem_channel_id_t c, unsigned v) {
    assert(e==(snd_mixer_elem_t *)&token_elems[2] && (unsigned)c<2 && v<4); enum_values[c]=v; return 0;
}
int main(void) {
    mloop=(pa_threaded_mainloop *)&token_loop; ctx=(pa_context *)&token_context; capture_stm=(pa_stream *)&token_capture;
    audio_freedv_mixer_snapshot_t *snapshot=audio_freedv_mixer_snapshot(); assert(snapshot);
    char description[256];
    assert(audio_freedv_mixer_snapshot_describe(snapshot,description,sizeof(description)));
    assert(strstr(description,"[160,161]") && strstr(description,"[145,146]") && strstr(description,"[3,2]"));
    char items[128], small[8];
    assert(audio_freedv_mixer_items(items,sizeof(items)) && !strcmp(items,"Stereo\nReverse Stereo\nSum Mono\nMix Mono"));
    assert(!audio_freedv_mixer_items(small,sizeof(small)) && !small[0]);
    assert(!audio_freedv_capture_configure(1,"Unknown") && enum_values[0]==3 && enum_values[1]==2);
    assert(audio_freedv_capture_configure(1,"Stereo") && enum_values[0]==0 && enum_values[1]==0);
    assert(audio_freedv_capture_begin());
    assert(!audio_freedv_capture_configure(2,"Mix Mono"));
    const int16_t stereo[]={10,100,20,200,-30,-300}; int16_t mono[8], raw[16];
    peek_data=stereo; peek_bytes=sizeof(stereo);
    diagnostic_stereo_read(diagnostic_stereo,0,NULL);
    assert(audio_freedv_capture_read(mono,8)==3 && mono[0]==10 && mono[1]==20 && mono[2]==-30);
    assert(audio_freedv_capture_read_stereo(raw,8)==3 && !memcmp(raw,stereo,sizeof(stereo)));
    /* Normal mono capture still reaches DSP, but does not duplicate diagnostics. */
    read_callback(capture_stm,0,NULL);
    assert(dsp_sample_count==6 && !audio_freedv_capture_read(mono,8));
    audio_freedv_capture_end();
    assert(audio_freedv_capture_configure(2,NULL) && audio_freedv_capture_begin());
    diagnostic_stereo_read(diagnostic_stereo,0,NULL);
    assert(audio_freedv_capture_read(mono,8)==3 && mono[0]==100 && mono[2]==-300);
    assert(audio_freedv_capture_read_stereo(raw,8)==3);
    audio_freedv_capture_end();
    assert(audio_freedv_capture_configure(3,"Sum Mono") && audio_freedv_capture_begin());
    diagnostic_stereo_read(diagnostic_stereo,0,NULL);
    assert(audio_freedv_capture_read(mono,8)==3 && mono[0]==55 && mono[2]==-165);
    assert(audio_freedv_capture_read_stereo(raw,8)==3);
    peek_data=NULL; peek_bytes=8; diagnostic_stereo_read(diagnostic_stereo,0,NULL);
    assert(audio_freedv_capture_stats().hole_samples==2);
    assert(audio_freedv_capture_read(mono,8)==2 && mono[0]==0 && mono[1]==0);
    assert(audio_freedv_capture_read_stereo(raw,8)==2 && raw[0]==0 && raw[3]==0);
    audio_freedv_capture_end();
    assert(audio_freedv_capture_configure(1,NULL) && audio_freedv_capture_begin());
    int16_t *too_many = calloc(2u * (FREEDV_CAPTURE_CAPACITY + 1u), sizeof(int16_t)); assert(too_many);
    peek_data=too_many; peek_bytes=2u*(FREEDV_CAPTURE_CAPACITY+1u)*sizeof(int16_t);
    diagnostic_stereo_read(diagnostic_stereo,0,NULL);
    assert(audio_freedv_capture_stats().dropped_samples == 3 && audio_freedv_capture_stats().capture_errors);
    audio_freedv_capture_end(); free(too_many);
    assert(audio_freedv_capture_configure(0,NULL));
    play_values[0]=1; capture_values[1]=2;
    fail_read=true; assert(!audio_freedv_mixer_restore(snapshot)); fail_read=false;
    assert(audio_freedv_mixer_restore(snapshot));
    assert(play_values[0]==160 && play_values[1]==161 && capture_values[0]==145 && capture_values[1]==146 && enum_values[0]==3 && enum_values[1]==2);
    audio_freedv_mixer_snapshot_free(snapshot);
    assert(!audio_freedv_other_audio_busy());
    assert(audio_play_lowlat_begin() && audio_freedv_other_audio_busy());
    audio_play_lowlat_end(); assert(!audio_freedv_other_audio_busy());
    assert(!locks);
    puts("PASS: stereo separation, raw evidence, mixer alternatives, exact restoration and ordinary-stream exclusion");
}
