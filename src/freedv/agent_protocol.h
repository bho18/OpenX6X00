// SPDX-License-Identifier: LGPL-2.1-or-later
#pragma once
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <ctype.h>
#include <errno.h>
#include <stdlib.h>

/* Staging can select controls only. No wire operation acquires, arms or runs.
 * Parse independently of scanf so malformed integer fields cannot overrun a
 * destination or silently truncate, and require the complete bounded request. */
typedef struct {
    uint64_t request_id;
    unsigned selected, route, mic, channel, mixer, repeat;
} fd_agent_stage;
static inline bool fd_agent_parse_stage(const char *wire, size_t length, fd_agent_stage *out) {
    if(!wire || !out || !length || length>=256 || wire[length])return false;
    uint64_t values[8];const char *p=wire;
    for(unsigned i=0;i<8;++i) {
        while(isspace((unsigned char)*p))++p;
        if(!isdigit((unsigned char)*p))return false;
        errno=0;char *end;unsigned long long value=strtoull(p,&end,10);
        if(errno==ERANGE || end==p || (*end && !isspace((unsigned char)*end)))return false;
        values[i]=(uint64_t)value;p=end;
    }
    while(isspace((unsigned char)*p))++p;
    if(*p || values[0]!=2 || !values[1] || values[2]>28 || values[3]>=8 ||
       values[4]>=2 || values[5]>=4 || values[6]>=5 ||
       (values[7]!=1 && values[7]!=5) ||
       (values[7]==5 && (values[2]<23 || values[2]>26)))return false;
    *out=(fd_agent_stage){values[1],(unsigned)values[2],(unsigned)values[3],
        (unsigned)values[4],(unsigned)values[5],(unsigned)values[6],(unsigned)values[7]};
    return true;
}
