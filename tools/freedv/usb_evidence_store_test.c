#define _POSIX_C_SOURCE 200809L
#include "../../src/freedv/usb_evidence_store.h"
#include <assert.h>
#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/wait.h>
#include <unistd.h>

static void read_all(const char *file,char *out,size_t cap){FILE *f=fopen(file,"rb");assert(f);size_t n=fread(out,1,cap-1,f);assert(!ferror(f)&&feof(f));out[n]=0;assert(fclose(f)==0);}
static bool exists(const char *file){struct stat st;return lstat(file,&st)==0;}
static int locate(const char *dir,const char *id){
    char p[512],data[17000],prefix[256];snprintf(prefix,sizeof(prefix),"{\"schema\":\"x6200.usb.incident.v1\",\"session_id\":\"%s\",\"kind\":\"metadata\"",id);
    for(unsigned i=0;i<16;++i){snprintf(p,sizeof(p),"%s/slot-%02u-metadata.json",dir,i);if(exists(p)){read_all(p,data,sizeof(data));if(!strncmp(data,prefix,strlen(prefix)))return (int)i;}}
    return -1;
}
static void path(char *out,size_t cap,const char *dir,const char *id,const char *kind){int n=locate(dir,id);assert(n>=0);snprintf(out,cap,"%s/slot-%02d-%s.json",dir,n,kind);}
static void clear_dir(const char *dir){
    DIR *d=opendir(dir);assert(d);struct dirent *e;char p[512];
    while((e=readdir(d)))if(strcmp(e->d_name,".")&&strcmp(e->d_name,"..")){snprintf(p,sizeof(p),"%s/%s",dir,e->d_name);assert(unlink(p)==0);}
    closedir(d);assert(rmdir(dir)==0);
}
static void lifecycle(const char *base){
    char dir[256],p[512],before[17000],after[17000];snprintf(dir,sizeof(dir),"%s/lifecycle",base);
    assert(fd_usb_evidence_store(dir,"run-one","metadata","{\"session_id\":\"run-one\",\"build_id\":\"usb-v3\",\"source_commit\":\"abc123\",\"rf_disabled\":true}")==0);
    assert(fd_usb_evidence_store(dir,"run-one","fault","{\"code\":17,\"origin\":\"capture-stale\"}")==0);
    path(p,sizeof(p),dir,"run-one","fault");read_all(p,before,sizeof(before));
    assert(strstr(before,"\"schema\":\"x6200.usb.incident.v1\"")&&strstr(before,"\"session_id\":\"run-one\""));
    assert(fd_usb_evidence_store(dir,"run-one","fault","{\"code\":99}")==-EEXIST);
    read_all(p,after,sizeof(after));assert(!strcmp(before,after));
    assert(fd_usb_evidence_store(dir,"run-two","metadata","{}")==0);
    path(p,sizeof(p),dir,"run-one","aborted");read_all(p,after,sizeof(after));
    assert(strstr(after,"session-replaced-without-completion")&&strstr(after,"run-two")&&strstr(after,"\"build_id\":\"usb-v3\"")&&strstr(after,"\"source_commit\":\"abc123\""));
    assert(fd_usb_evidence_store(dir,"run-two","completion","{\"rf_clear_confirmed\":true}")==0);
    assert(fd_usb_evidence_store(dir,"run-three","metadata","{}")==0);
    path(p,sizeof(p),dir,"run-two","aborted");assert(!exists(p));
    snprintf(p,sizeof(p),"%s/current-session.json",dir);read_all(p,after,sizeof(after));assert(strstr(after,"run-three"));
    clear_dir(dir);
}
static void rotation_and_retention(const char *base){
    char dir[256],id[64],p[512],data[17000];snprintf(dir,sizeof(dir),"%s/collisions",base);
    const char *old="earlier-incomplete";strcpy(id,"next-session");
    assert(fd_usb_evidence_store(dir,old,"metadata","{}")==0);
    assert(fd_usb_evidence_store(dir,old,"fault","{\"first\":true}")==0);
    assert(fd_usb_evidence_store(dir,id,"metadata","{}")==0);
    assert(locate(dir,old)!=locate(dir,id));
    assert(fd_usb_evidence_store(dir,old,"fault","{}")==-EEXIST);
    path(p,sizeof(p),dir,old,"aborted");read_all(p,data,sizeof(data));assert(strstr(data,"session-replaced-without-completion"));
    path(p,sizeof(p),dir,id,"metadata");read_all(p,data,sizeof(data));assert(strstr(data,id)&&!strstr(data,old));
    path(p,sizeof(p),dir,id,"fault");assert(!exists(p));
    assert(fd_usb_evidence_store(dir,id,"completion","{}")==0);
    for(unsigned n=0;n<100;++n){snprintf(id,sizeof(id),"retention-%u",n);assert(fd_usb_evidence_store(dir,id,"metadata","{}")==0);assert(fd_usb_evidence_store(dir,id,"completion","{}")==0);}
    DIR *d=opendir(dir);assert(d);struct dirent *e;unsigned files=0,metadata=0;
    while((e=readdir(d)))if(strcmp(e->d_name,".")&&strcmp(e->d_name,"..")){
        ++files;if(strstr(e->d_name,"-metadata.json"))++metadata;
        assert(strcmp(e->d_name,".txn.json"));snprintf(p,sizeof(p),"%s/%s",dir,e->d_name);struct stat st;assert(stat(p,&st)==0);assert(st.st_size<=FD_USB_EVIDENCE_MAX_RECORD);
    }
    closedir(d);assert(files<=FD_USB_EVIDENCE_SLOTS*4+2&&metadata<=FD_USB_EVIDENCE_SLOTS);
    assert(metadata==FD_USB_EVIDENCE_SLOTS&&locate(dir,old)<0);
    for(unsigned n=0;n<100;++n){snprintf(id,sizeof(id),"retention-%u",n);assert((locate(dir,id)>=0)==(n>=84));}
    assert(fd_usb_evidence_store(dir,old,"fault","{}")==-ESTALE);
    assert(fd_usb_evidence_store(dir,"retention-84","fault","{\"late\":true}")==0);
    snprintf(p,sizeof(p),"%s/current-session.json",dir);read_all(p,data,sizeof(data));assert(strstr(data,"retention-99"));
    clear_dir(dir);
}
static void rejected_inputs_and_failure(const char *base){
    char dir[256],p[512],before[17000],after[17000];snprintf(dir,sizeof(dir),"%s/rejections",base);
    assert(fd_usb_evidence_store(dir,"valid","metadata","{}")==0);
    snprintf(p,sizeof(p),"%s/current-session.json",dir);read_all(p,before,sizeof(before));
    assert(fd_usb_evidence_store(dir,"../escape","metadata","{}")==-EINVAL);
    assert(fd_usb_evidence_store(dir,"valid","other","{}")==-EINVAL);
    assert(fd_usb_evidence_store(dir,"valid","fault","[]")==-EINVAL);
    assert(fd_usb_evidence_store(dir,"valid","fault","{\"x\":01}")==-EINVAL);
    assert(fd_usb_evidence_store(dir,"valid","fault","{\"x\":\"unterminated}")==-EINVAL);
    assert(fd_usb_evidence_store(dir,"valid","fault","{\"session_id\":\"other\"}")==-ESTALE);
    assert(fd_usb_evidence_store(dir,"valid","fault","{\"session_id\":\"valid\",\"session_id\":\"valid\"}")==-EINVAL);
    char *large=malloc(FD_USB_EVIDENCE_MAX_RECORD+1);assert(large);memset(large,' ',FD_USB_EVIDENCE_MAX_RECORD);large[0]='{';large[1]='}';large[FD_USB_EVIDENCE_MAX_RECORD]=0;
    assert(fd_usb_evidence_store(dir,"too-large","metadata",large)==-EFBIG);free(large);
    read_all(p,after,sizeof(after));assert(!strcmp(before,after));
    assert(chmod(dir,0500)==0);assert(fd_usb_evidence_store(dir,"valid","fault","{}")==-EROFS);assert(chmod(dir,0700)==0);
    /* A failed temporary-file creation must not alter an existing record or
     * pointer. This also exercises an interrupted/foreign transaction path. */
    snprintf(p,sizeof(p),"%s/.txn.json",dir);assert(mkdir(p,0700)==0);
    assert(fd_usb_evidence_store(dir,"valid","fault","{}")<0);assert(rmdir(p)==0);
    snprintf(p,sizeof(p),"%s/current-session.json",dir);read_all(p,after,sizeof(after));assert(!strcmp(before,after));
    assert(fd_usb_evidence_store(dir,"valid","fault","{\"nested\":[true,false,null,-1.25e+2,{\"text\":\"line\\ntext\"}]}")==0);
    assert(fd_usb_evidence_store("relative","valid","metadata","{}")==-EINVAL);
    assert(fd_usb_evidence_store("/definitely-not-present-usb-test/child","valid","metadata","{}")<0);
    clear_dir(dir);
}
static void no_symlink_or_foreign_pair(const char *base){
    char dir[256],p[512],outside[512],data[64];snprintf(dir,sizeof(dir),"%s/symlink",base);
    snprintf(outside,sizeof(outside),"%s/outside",base);FILE *f=fopen(outside,"wb");assert(f);fputs("untouched",f);assert(fclose(f)==0);
    assert(fd_usb_evidence_store(dir,"safe","metadata","{}")==0);
    path(p,sizeof(p),dir,"safe","fault");assert(symlink(outside,p)==0);
    assert(fd_usb_evidence_store(dir,"safe","fault","{}")<0);read_all(outside,data,sizeof(data));assert(!strcmp(data,"untouched"));assert(unlink(p)==0);
    /* A corrupt existing slot must fail closed, rather than append a record
     * under an identity inferred only from a filename. */
    path(p,sizeof(p),dir,"safe","metadata");f=fopen(p,"wb");assert(f);fputs("{\"session_id\":\"foreign\"}\n",f);assert(fclose(f)==0);
    assert(fd_usb_evidence_store(dir,"safe","completion","{}")==-EBADMSG);
    clear_dir(dir);assert(unlink(outside)==0);
}
static void atomic_visibility(const char *base){
    char dir[256],pointer[512];snprintf(dir,sizeof(dir),"%s/atomic",base);snprintf(pointer,sizeof(pointer),"%s/current-session.json",dir);
    assert(fd_usb_evidence_store(dir,"atomic-initial","metadata","{}")==0);assert(fd_usb_evidence_store(dir,"atomic-initial","completion","{}")==0);
    int signal[2];assert(pipe(signal)==0);assert(fcntl(signal[0],F_SETFL,O_NONBLOCK)==0);pid_t child=fork();assert(child>=0);
    if(!child){
        close(signal[1]);char data[17000],done;
        do{read_all(pointer,data,sizeof(data));size_t n=strlen(data);assert(n&&data[0]=='{'&&data[n-2]=='}'&&data[n-1]=='\n');assert(strstr(data,"x6200.usb.incident-pointer.v1"));}while(read(signal[0],&done,1)<0&&errno==EAGAIN);
        close(signal[0]);_exit(0);
    }
    close(signal[0]);
    for(unsigned i=0;i<40;++i){char id[64];snprintf(id,sizeof(id),"atomic-%u",i);assert(fd_usb_evidence_store(dir,id,"metadata","{}")==0);assert(fd_usb_evidence_store(dir,id,"completion","{}")==0);}
    assert(write(signal[1],"x",1)==1);close(signal[1]);int status;assert(waitpid(child,&status,0)==child);assert(WIFEXITED(status)&&WEXITSTATUS(status)==0);
    clear_dir(dir);
}
int main(void){
    char base[]="/tmp/usb-evidence-store-XXXXXX";assert(mkdtemp(base));
    lifecycle(base);rotation_and_retention(base);rejected_inputs_and_failure(base);no_symlink_or_foreign_pair(base);atomic_visibility(base);assert(rmdir(base)==0);
    puts("PASS persistent USB incident lifecycle, first-fault immutability, interrupted session, bounded retention, identity fencing, invalid storage and atomic visibility");return 0;
}
