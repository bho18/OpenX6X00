// SPDX-License-Identifier: LGPL-2.1-or-later
#define _POSIX_C_SOURCE 200809L
#include "usb_headsets_internal.h"
#include <pthread.h>
#include <stdatomic.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <errno.h>
#include <time.h>

static const char *property(pa_proplist *p,const char *key){const char *s=p?pa_proplist_gets(p,key):NULL;return s&&*s?s:NULL;}
static fd_usb_headset_pair *physical(fd_usb_headset_collection *c,pa_proplist *p,const char *driver,const char *description){
    const char *bus=property(p,"device.bus"),*card=property(p,"alsa.card");
    if(!driver||strncmp(driver,"module-alsa-",12)||!bus||strcmp(bus,"usb")||!card)return NULL;
    const char *vendor=property(p,"device.vendor.id"),*product=property(p,"device.product.id");
    const char *serial=property(p,"device.serial"),*path=property(p,"device.bus_path");
    if(!path)path=property(p,"sysfs.path");
    if(!serial&&!path)return NULL; /* Never bind a saved choice to an ALSA index. */
    fd_usb_headset_pair *pair=NULL;
    for(unsigned i=0;i<c->count;++i)if(!strcmp(c->pairs[i].card,card)){pair=&c->pairs[i];break;}
    if(!pair){if(c->count==FD_USB_HEADSET_MAX)return NULL;pair=&c->pairs[c->count++];snprintf(pair->card,sizeof(pair->card),"%s",card);}
    char id[256];int n=snprintf(id,sizeof(id),"usb:%s:%s:%s:%s",vendor?vendor:"unknown",product?product:"unknown",serial?"serial":"path",serial?serial:path);
    if(n<0||(size_t)n>=sizeof(id))return NULL; /* Truncation must not alias devices. */
    if(pair->device.id[0]&&strcmp(pair->device.id,id))return NULL;
    snprintf(pair->device.id,sizeof(pair->device.id),"%s",id);
    const char *label=property(p,"alsa.card_name");if(!label)label=property(p,"device.product.name");if(!label)label=description;
    snprintf(pair->device.name,sizeof(pair->device.name),"%s",label?label:"USB headset");return pair;
}
void fd_usb_headsets_source(fd_usb_headset_collection *c,const pa_source_info *i){
    if(!i||!i->name||strlen(i->name)>=sizeof(c->pairs[0].device.source)||i->monitor_of_sink!=PA_INVALID_INDEX)return;
    fd_usb_headset_pair *p=physical(c,i->proplist,i->driver,i->description);if(!p)return;
    ++p->sources;snprintf(p->device.source,sizeof(p->device.source),"%s",i->name?i->name:"");
}
void fd_usb_headsets_sink(fd_usb_headset_collection *c,const pa_sink_info *i){
    if(!i||!i->name||strlen(i->name)>=sizeof(c->pairs[0].device.sink))return;
    fd_usb_headset_pair *p=physical(c,i->proplist,i->driver,i->description);if(!p)return;
    ++p->sinks;snprintf(p->device.sink,sizeof(p->device.sink),"%s",i->name?i->name:"");
}
unsigned fd_usb_headsets_catalog(const fd_usb_headset_collection *c,fd_usb_headset *out,unsigned capacity){
    unsigned count=0;if(c->failed)return 0;
    for(unsigned i=0;i<c->count&&count<capacity;++i){const fd_usb_headset_pair *p=&c->pairs[i];if(p->sources!=1||p->sinks!=1||!p->device.id[0]||!p->device.source[0]||!p->device.sink[0])continue;
        bool duplicate=false;for(unsigned j=0;j<c->count;++j)if(i!=j&&!strcmp(p->device.id,c->pairs[j].device.id)){duplicate=true;break;}
        if(!duplicate)out[count++]=p->device;
    }return count;
}
struct fd_usb_headset_scan {pthread_t worker;pthread_mutex_t mutex;atomic_bool cancel,done;fd_usb_headset_catalog result;fd_usb_headset_collection collection;};
static uint64_t milliseconds(void){struct timespec t;clock_gettime(CLOCK_MONOTONIC,&t);return (uint64_t)t.tv_sec*1000+t.tv_nsec/1000000;}
static void sources(pa_context *ctx,const pa_source_info *i,int end,void *data){(void)ctx;fd_usb_headset_collection *c=&((fd_usb_headset_scan *)data)->collection;if(end<0)c->failed=true;else if(!end)fd_usb_headsets_source(c,i);}
static void sinks(pa_context *ctx,const pa_sink_info *i,int end,void *data){(void)ctx;fd_usb_headset_collection *c=&((fd_usb_headset_scan *)data)->collection;if(end<0)c->failed=true;else if(!end)fd_usb_headsets_sink(c,i);}
static bool iterate(fd_usb_headset_scan *s,pa_mainloop *loop,uint64_t until){
    if(atomic_load(&s->cancel)||milliseconds()>=until)return false;
    if(pa_mainloop_iterate(loop,0,NULL)<0)return false;
    struct timespec delay={0,1000000};nanosleep(&delay,NULL);return true;
}
static bool operation(fd_usb_headset_scan *s,pa_mainloop *loop,pa_operation *op){
    if(!op)return false;
    uint64_t until=milliseconds()+1000;
    while(pa_operation_get_state(op)==PA_OPERATION_RUNNING&&iterate(s,loop,until)){}
    bool ok=pa_operation_get_state(op)==PA_OPERATION_DONE;if(!ok)pa_operation_cancel(op);pa_operation_unref(op);return ok;
}
static void *scan(void *data){
    fd_usb_headset_scan *s=data;pa_mainloop *loop=pa_mainloop_new();pa_context *ctx=loop?pa_context_new(pa_mainloop_get_api(loop),"X6200 USB headset catalog"):NULL;int error=EIO;
    if(ctx&&pa_context_connect(ctx,NULL,PA_CONTEXT_NOAUTOSPAWN,NULL)>=0){
        uint64_t until=milliseconds()+5000;
        while(pa_context_get_state(ctx)!=PA_CONTEXT_READY&&PA_CONTEXT_IS_GOOD(pa_context_get_state(ctx))&&iterate(s,loop,until)){}
        if(pa_context_get_state(ctx)==PA_CONTEXT_READY&&operation(s,loop,pa_context_get_source_info_list(ctx,sources,s))&&operation(s,loop,pa_context_get_sink_info_list(ctx,sinks,s)))error=0;
    }
    if(ctx){pa_context_disconnect(ctx);pa_context_unref(ctx);}if(loop)pa_mainloop_free(loop);
    pthread_mutex_lock(&s->mutex);s->result.error=atomic_load(&s->cancel)?ECANCELED:error;s->result.pending=false;
    if(s->collection.failed&&!s->result.error)s->result.error=EIO;
    if(!s->result.error)s->result.count=fd_usb_headsets_catalog(&s->collection,s->result.devices,FD_USB_HEADSET_MAX);
    pthread_mutex_unlock(&s->mutex);atomic_store(&s->done,true);return NULL;
}
fd_usb_headset_scan *fd_usb_headsets_scan_open(void){
    fd_usb_headset_scan *s=calloc(1,sizeof(*s));if(!s)return NULL;pthread_mutex_init(&s->mutex,NULL);s->result.pending=true;
    if(pthread_create(&s->worker,NULL,scan,s)){pthread_mutex_destroy(&s->mutex);free(s);return NULL;}return s;
}
fd_usb_headset_catalog fd_usb_headsets_scan_snapshot(fd_usb_headset_scan *s){
    if(!s)return (fd_usb_headset_catalog){.error=EINVAL};
    pthread_mutex_lock(&s->mutex);fd_usb_headset_catalog result=s->result;pthread_mutex_unlock(&s->mutex);return result;
}
void fd_usb_headsets_scan_cancel(fd_usb_headset_scan *s){if(s)atomic_store(&s->cancel,true);}
bool fd_usb_headsets_scan_close(fd_usb_headset_scan *s){if(!s)return true;if(!atomic_load(&s->done))return false;pthread_join(s->worker,NULL);pthread_mutex_destroy(&s->mutex);free(s);return true;}
