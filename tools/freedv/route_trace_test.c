// SPDX-License-Identifier: LGPL-2.1-or-later
#include "src/freedv/route_trace.h"
#include <assert.h>
#include <stdio.h>
#include <string.h>
int main(void){
    fd_route_trace_note("disabled",6,0,1,true);
    FILE *idle=tmpfile();assert(idle);fd_route_trace_export(idle);rewind(idle);
    char unused[512];assert(fgets(unused,sizeof(unused),idle) && strstr(unused,"\"trace_complete\":true"));
    assert(!fgets(unused,sizeof(unused),idle));fclose(idle);
    fd_route_trace_begin(42);
    fd_route_trace_note("record_start",-1,0,0,true);
    fd_route_trace_note("command",6,0,1,false);
    fd_route_trace_note("bulk_resend",-1,0,0,true);
    fd_route_trace_end();fd_route_trace_note("after_end",6,0,1,true);
    FILE *f=tmpfile();assert(f);fd_route_trace_export(f);rewind(f);
    char line[512];assert(fgets(line,sizeof(line),f));
    assert(strstr(line,"\"generation\":42") && strstr(line,"\"transport_success\":null"));
    assert(fgets(line,sizeof(line),f) && strstr(line,"\"transport_success\":false"));
    assert(fgets(line,sizeof(line),f) && strstr(line,"\"transport_success\":true"));
    assert(fgets(line,sizeof(line),f) && strstr(line,"\"trace_complete\":true"));assert(!fgets(line,sizeof(line),f));fclose(f);
    fd_route_trace_begin(43);
    for(unsigned i=0;i<1030;++i)fd_route_trace_note("command",6,0,1,true);
    f=tmpfile();assert(f);fd_route_trace_export(f);rewind(f);
    for(unsigned i=0;i<1024;++i)assert(fgets(line,sizeof(line),f));
    assert(fgets(line,sizeof(line),f) && strstr(line,"\"trace_complete\":false") && strstr(line,"\"discarded\":6"));
    fclose(f);puts("PASS: phase markers do not imply transport success; trace overflow stays bounded");
}
