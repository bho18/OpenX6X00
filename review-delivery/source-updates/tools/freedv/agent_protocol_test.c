// SPDX-License-Identifier: LGPL-2.1-or-later
#include "src/freedv/agent_protocol.h"
#include <assert.h>
#include <stdio.h>
#include <string.h>
static bool parse(const char *text,fd_agent_stage *out){return fd_agent_parse_stage(text,strlen(text),out);}
int main(void){
    fd_agent_stage s={0};
    assert(parse("2 18446744073709551615 25 4 0 0 0 5\n",&s));
    assert(s.request_id==UINT64_MAX && s.selected==25 && s.route==4 && s.repeat==5);
    assert(parse("2 1 20 6 0 0 0 1\n",&s));
    assert(parse("2 2 28 6 0 0 0 1\n",&s) && s.selected==28);
    const char *bad[]={"1 25 4 0 0 0\n","2 1 25 4 0 0 0","2 1 25 4 0 0 0 5 extra",
        "2 1 25 4 0 0 0 5 9","2 1 20 6 0 0 0 5","2 1 28 6 0 0 0 5","2 1 25 8 0 0 0 1",
        "2 1 25 4 2 0 0 1","2 1 25 4 0 4 0 1","2 1 25 4 0 0 5 1",
        "2 1 29 4 0 0 0 1","2 0 25 4 0 0 0 1","2 -1 25 4 0 0 0 1",
        "2 +1 25 4 0 0 0 1","2 18446744073709551616 25 4 0 0 0 1",
        "2 1 4294967321 4 0 0 0 1","2 1x 25 4 0 0 0 1","2 1 25 4 0 0 0 0",""};
    for(unsigned i=0;i<sizeof(bad)/sizeof(*bad);++i)assert(!parse(bad[i],&s));
    char long_request[300];memset(long_request,' ',sizeof(long_request));
    memcpy(long_request,"2 1 25 4 0 0 0 1",strlen("2 1 25 4 0 0 0 1"));long_request[sizeof(long_request)-1]=0;
    assert(!parse(long_request,&s));
    puts("PASS: strict bounded v2 stage parser rejects malformed fields and RF repetition");
}
