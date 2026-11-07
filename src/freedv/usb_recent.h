// SPDX-License-Identifier: LGPL-2.1-or-later
#pragma once
#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>
#include <inttypes.h>
#include <stdarg.h>
#include <stdio.h>
#include <string.h>
#include <errno.h>
#include <limits.h>

/* Private flight recorder. The caller holds its recorder mutex for every
 * operation, including serialization. Push/freeze copy values only and never
 * perform I/O, allocate memory, acquire a lock, or call a radio/audio API.
 * Labels are explicitly bounded diagnostic prefixes; the original immutable
 * first-fault record remains the authority for its full origin text. */
#define FD_USB_RECENT_EVENTS 64u
#define FD_USB_RECENT_SNAPSHOTS 16u
#define FD_USB_RECENT_TEXT 24u
#define FD_USB_RECENT_MAX_JSON 15000u
_Static_assert(UINT_MAX==UINT32_MAX && INT_MAX==INT32_MAX,
    "Recent JSON bound assumes the supported 32-bit int ABI");

typedef struct {
    uint64_t ms, duration_ms;
    unsigned state;
    int status, error;
    char name[FD_USB_RECENT_TEXT], operation[FD_USB_RECENT_TEXT];
    unsigned stream;
} fd_usb_recent_event;
typedef struct {
    uint64_t ms, generation, turns, display_generation;
    unsigned state, fault, mic_health, mic_age_ms, rx_age_ms, io_age_ms, volume_percent;
    bool volume_pending, ptt, rf_tx, display_dark, drawing_enabled;
} fd_usb_recent_snapshot;
typedef struct {
    fd_usb_recent_event events[FD_USB_RECENT_EVENTS];
    fd_usb_recent_snapshot snapshots[FD_USB_RECENT_SNAPSHOTS];
    unsigned event_head, event_count, snapshot_head, snapshot_count;
    uint64_t events_evicted, snapshots_evicted, events_rejected, snapshots_rejected;
} fd_usb_recent_window;
typedef struct {
    fd_usb_recent_window live, frozen;
    bool has_frozen;
} fd_usb_recent;

static inline void fd_usb_recent_init(fd_usb_recent *recent) {
    if (recent) memset(recent,0,sizeof(*recent));
}
static inline void fd_usb_recent_increment(uint64_t *count) {
    if (*count<UINT64_MAX) ++*count;
}
static inline bool fd_usb_recent_text_valid(const char *text) {
    for (unsigned i=0;i<FD_USB_RECENT_TEXT;++i) {
        unsigned char c=(unsigned char)text[i];
        if (!c) return true;
        if (c<0x20 || c>0x7e) return false;
    }
    return false;
}
static inline bool fd_usb_recent_push_event(fd_usb_recent *recent,const fd_usb_recent_event *event) {
    if (!recent) return false;
    fd_usb_recent_window *window=&recent->live;
    if (!event || event->state>255 || event->stream>4 ||
        !fd_usb_recent_text_valid(event->name) || !fd_usb_recent_text_valid(event->operation)) {
        fd_usb_recent_increment(&window->events_rejected);return false;
    }
    unsigned index=(window->event_head+window->event_count)%FD_USB_RECENT_EVENTS;
    window->events[index]=*event;
    if (window->event_count<FD_USB_RECENT_EVENTS) ++window->event_count;
    else {window->event_head=(window->event_head+1)%FD_USB_RECENT_EVENTS;
        fd_usb_recent_increment(&window->events_evicted);}
    return true;
}
static inline bool fd_usb_recent_push_snapshot(fd_usb_recent *recent,const fd_usb_recent_snapshot *snapshot) {
    if (!recent) return false;
    fd_usb_recent_window *window=&recent->live;
    if (!snapshot || snapshot->state>255 || snapshot->fault>65535 ||
        snapshot->mic_health>255 || snapshot->volume_percent>100) {
        fd_usb_recent_increment(&window->snapshots_rejected);return false;
    }
    unsigned index=(window->snapshot_head+window->snapshot_count)%FD_USB_RECENT_SNAPSHOTS;
    window->snapshots[index]=*snapshot;
    if (window->snapshot_count<FD_USB_RECENT_SNAPSHOTS) ++window->snapshot_count;
    else {window->snapshot_head=(window->snapshot_head+1)%FD_USB_RECENT_SNAPSHOTS;
        fd_usb_recent_increment(&window->snapshots_evicted);}
    return true;
}
static inline bool fd_usb_recent_freeze(fd_usb_recent *recent) {
    if (!recent || recent->has_frozen) return false;
    recent->frozen=recent->live;recent->has_frozen=true;return true;
}

typedef struct {char *data;size_t capacity,used;int error;} fd_usb_recent_json;
static inline void fd_usb_recent_append(fd_usb_recent_json *json,const char *format,...) {
    if (json->error) return;
    va_list args;va_start(args,format);
    int length=vsnprintf(json->data+json->used,json->capacity-json->used,format,args);
    va_end(args);
    if (length<0) json->error=-EINVAL;
    else if ((size_t)length>=json->capacity-json->used) json->error=-ENOSPC;
    else json->used+=(size_t)length;
}
static inline void fd_usb_recent_quote(fd_usb_recent_json *json,const char *value) {
    fd_usb_recent_append(json,"\"");
    for (unsigned i=0;value[i] && i<FD_USB_RECENT_TEXT-1;++i) {
        if (value[i]=='\"' || value[i]=='\\') fd_usb_recent_append(json,"\\%c",value[i]);
        else fd_usb_recent_append(json,"%c",value[i]);
    }
    fd_usb_recent_append(json,"\"");
}
/* Bytes exclude the trailing NUL. A failed serialization clears the output,
 * so an undersized destination cannot be mistaken for a complete JSON record.
 * All accepted input domains, 64/16 full rings, 23-byte escaped labels and
 * UINT64_MAX counters fit FD_USB_RECENT_MAX_JSON; the unit test exercises the
 * maximal field widths, leaving at least 1 KiB for the identity envelope. */
static inline int fd_usb_recent_serialize(const fd_usb_recent *recent,char *buffer,size_t capacity,bool frozen) {
    if (!recent || !buffer || !capacity) return -EINVAL;
    buffer[0]=0;
    if (frozen && !recent->has_frozen) return -ENOENT;
    const fd_usb_recent_window *window=frozen?&recent->frozen:&recent->live;
    fd_usb_recent_json json={.data=buffer,.capacity=capacity};
    fd_usb_recent_append(&json,"{\"schema\":\"x6200.usb.recent-window.v1\",\"frozen\":%s,"
        "\"event_capacity\":64,\"snapshot_capacity\":16,\"events_evicted\":%"PRIu64","
        "\"snapshots_evicted\":%"PRIu64",\"events_rejected\":%"PRIu64",\"snapshots_rejected\":%"PRIu64","
        "\"labels\":\"printable-ascii-prefixes-23B\","
        "\"event_columns\":[\"ms\",\"state\",\"name\",\"operation\",\"stream\",\"status\",\"error\",\"duration_ms\"],"
        "\"snapshot_columns\":[\"ms\",\"state\",\"fault\",\"mic_health\",\"mic_age_ms\",\"rx_age_ms\",\"io_age_ms\","
        "\"generation\",\"turns\",\"volume_percent\",\"volume_pending\",\"ptt\",\"rf_tx\",\"display_generation\","
        "\"display_dark\",\"drawing_enabled\"],\"events\":[",frozen?"true":"false",
        window->events_evicted,window->snapshots_evicted,window->events_rejected,window->snapshots_rejected);
    for (unsigned i=0;i<window->event_count;++i) {
        const fd_usb_recent_event *event=&window->events[(window->event_head+i)%FD_USB_RECENT_EVENTS];
        fd_usb_recent_append(&json,"%s[%"PRIu64",%u,",i?",":"",event->ms,event->state);
        fd_usb_recent_quote(&json,event->name);fd_usb_recent_append(&json,",");
        fd_usb_recent_quote(&json,event->operation);
        fd_usb_recent_append(&json,",%u,%d,%d,%"PRIu64"]",event->stream,event->status,event->error,event->duration_ms);
    }
    fd_usb_recent_append(&json,"],\"snapshots\":[");
    for (unsigned i=0;i<window->snapshot_count;++i) {
        const fd_usb_recent_snapshot *s=&window->snapshots[(window->snapshot_head+i)%FD_USB_RECENT_SNAPSHOTS];
        fd_usb_recent_append(&json,"%s[%"PRIu64",%u,%u,%u,%u,%u,%u,%"PRIu64",%"PRIu64",%u,%s,%s,%s,%"PRIu64",%s,%s]",
            i?",":"",s->ms,s->state,s->fault,s->mic_health,s->mic_age_ms,s->rx_age_ms,s->io_age_ms,
            s->generation,s->turns,s->volume_percent,s->volume_pending?"true":"false",s->ptt?"true":"false",
            s->rf_tx?"true":"false",s->display_generation,s->display_dark?"true":"false",s->drawing_enabled?"true":"false");
    }
    fd_usb_recent_append(&json,"]}");
    if (json.error || json.used>FD_USB_RECENT_MAX_JSON) {
        buffer[0]=0;return json.error?json.error:-EOVERFLOW;
    }
    return (int)json.used;
}
