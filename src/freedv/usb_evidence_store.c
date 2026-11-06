#define _POSIX_C_SOURCE 200809L
#include "usb_evidence_store.h"
#include <ctype.h>
#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/file.h>
#include <sys/stat.h>
#include <unistd.h>

typedef struct { const char *at; unsigned depth; char identity[FD_USB_EVIDENCE_MAX_SESSION+1]; bool seen; } json_reader;
static bool valid_id(const char *s) {
    if(!s||!isalnum((unsigned char)*s)||strlen(s)>FD_USB_EVIDENCE_MAX_SESSION)return false;
    for(;*s;++s)if(!isalnum((unsigned char)*s)&&*s!='_'&&*s!='-'&&*s!='.')return false;
    return true;
}
static void space(json_reader *r){while(*r->at==' '||*r->at=='\t'||*r->at=='\r'||*r->at=='\n')++r->at;}
static bool string(json_reader *r,char *out,size_t cap){
    if(*r->at++!='"')return false;
    size_t n=0;
    while(*r->at&&*r->at!='"'){
        unsigned c=(unsigned char)*r->at++;
        if(c<32)return false;
        if(c=='\\'){
            c=(unsigned char)*r->at++;if(!c)return false;
            if(c=='u'){
                unsigned code=0;
                for(unsigned i=0;i<4;++i){unsigned h=(unsigned char)*r->at++;if(!h||!isxdigit(h))return false;code=code*16+(isdigit(h)?h-'0':(unsigned)tolower(h)-'a'+10);}
                c=code<128?code:'?';
            }else if(c=='b')c='\b';else if(c=='f')c='\f';else if(c=='n')c='\n';else if(c=='r')c='\r';else if(c=='t')c='\t';
            else if(c!='"'&&c!='\\'&&c!='/')return false;
        }
        if(out){if(n+1>=cap||!c)return false;out[n++]=(char)c;}
    }
    if(*r->at!='"')return false;
    ++r->at;if(out)out[n]=0;return true;
}
static bool value(json_reader *r);
static bool object(json_reader *r){
    bool root=r->depth==0;if(++r->depth>32)return false;
    ++r->at;space(r);if(*r->at=='}'){++r->at;--r->depth;return true;}
    for(;;){
        char key[128];if(!string(r,key,sizeof(key)))return false;
        space(r);if(*r->at++!=':')return false;space(r);
        if(root&&!strcmp(key,"session_id")){
            if(r->seen||!string(r,r->identity,sizeof(r->identity))||!valid_id(r->identity))return false;
            r->seen=true;
        }else if(!value(r))return false;
        space(r);if(*r->at=='}'){++r->at;--r->depth;return true;}
        if(*r->at++!=',')return false;
        space(r);
    }
}
static bool value(json_reader *r){
    space(r);
    if(*r->at=='{')return object(r);
    if(*r->at=='['){
        if(++r->depth>32)return false;
        ++r->at;space(r);
        if(*r->at==']'){++r->at;--r->depth;return true;}
        for(;;){if(!value(r))return false;space(r);if(*r->at==']'){++r->at;--r->depth;return true;}if(*r->at++!=',')return false;}
    }
    if(*r->at=='"')return string(r,NULL,0);
    for(unsigned i=0;i<3;++i){const char *word=i==0?"true":i==1?"false":"null";size_t n=strlen(word);if(!strncmp(r->at,word,n)){r->at+=n;return true;}}
    if(*r->at=='-')++r->at;
    if(*r->at=='0')++r->at;
    else{if(*r->at<'1'||*r->at>'9')return false;while(isdigit((unsigned char)*r->at))++r->at;}
    if(*r->at=='.'){++r->at;if(!isdigit((unsigned char)*r->at))return false;while(isdigit((unsigned char)*r->at))++r->at;}
    if(*r->at=='e'||*r->at=='E'){++r->at;if(*r->at=='+'||*r->at=='-')++r->at;if(!isdigit((unsigned char)*r->at))return false;while(isdigit((unsigned char)*r->at))++r->at;}
    return true;
}
static bool json_object(const char *json,char *identity){
    json_reader r={.at=json};space(&r);
    if(*r.at!='{'||!object(&r))return false;
    space(&r);if(*r.at)return false;
    if(identity)strcpy(identity,r.seen?r.identity:"");
    return true;
}
/* Optional runtime tags are copied from the old metadata when synthesizing
 * an interrupted-session record. The store never invents a build identity. */
static bool identity_tag(const char *json,const char *wanted,char *out){
    json_reader r={.at=json,.depth=1};space(&r);if(*r.at++!='{')return false;space(&r);
    while(*r.at&&*r.at!='}'){
        char key[128];if(!string(&r,key,sizeof(key)))return false;space(&r);
        if(*r.at++!=':')return false;
        space(&r);
        if(!strcmp(key,wanted))return string(&r,out,FD_USB_EVIDENCE_MAX_SESSION+1)&&valid_id(out);
        if(!value(&r))return false;
        space(&r);if(*r.at!=',')return false;++r.at;space(&r);
    }
    return false;
}
static void filename(char *out,size_t cap,unsigned slot,const char *kind){snprintf(out,cap,"slot-%02u-%s.json",slot,kind);}
static int read_file(int dir,const char *name,char *out){
    int fd=openat(dir,name,O_RDONLY|O_NOFOLLOW);if(fd<0)return -errno;
    struct stat s;if(fstat(fd,&s)<0){int e=-errno;close(fd);return e;}
    if(!S_ISREG(s.st_mode)||s.st_size<=0||s.st_size>FD_USB_EVIDENCE_MAX_RECORD){close(fd);return -EBADMSG;}
    size_t done=0;while(done<(size_t)s.st_size){ssize_t n=read(fd,out+done,(size_t)s.st_size-done);if(n<0&&errno==EINTR)continue;if(n<=0){int e=n<0?-errno:-EIO;close(fd);return e;}done+=(size_t)n;}
    close(fd);out[done]=0;if(memchr(out,0,done))return -EBADMSG;return 0;
}
static int atomic_file(int dir,const char *name,const char *data){
    if(unlinkat(dir,".txn.json",0)<0&&errno!=ENOENT)return -errno;
    int fd=openat(dir,".txn.json",O_WRONLY|O_CREAT|O_EXCL|O_NOFOLLOW,0600);if(fd<0)return -errno;
    size_t left=strlen(data);const char *p=data;int result=0;
    while(left){ssize_t n=write(fd,p,left);if(n<0&&errno==EINTR)continue;if(n<=0){result=n<0?-errno:-EIO;break;}left-=(size_t)n;p+=n;}
    if(!result&&fsync(fd)<0)result=-errno;
    if(close(fd)<0&&!result)result=-errno;
    if(!result&&renameat(dir,".txn.json",dir,name)<0)result=-errno;
    if(!result&&fsync(dir)<0)result=-errno;
    if(result)(void)unlinkat(dir,".txn.json",0);
    return result;
}
static int sync_parent(const char *directory){
    char *parent=strdup(directory);if(!parent)return -ENOMEM;
    size_t n=strlen(parent);while(n>1&&parent[n-1]=='/')parent[--n]=0;
    char *slash=strrchr(parent,'/');if(slash==parent)parent[1]=0;else *slash=0;
    int fd=open(parent,O_RDONLY|O_DIRECTORY|O_NOFOLLOW),result=fd<0?-errno:0;free(parent);
    if(fd<0)return result;
    if(fsync(fd)<0)result=-errno;
    close(fd);return result;
}
static int read_record(int dir,unsigned slot,const char *kind,char *identity){
    char name[64],data[FD_USB_EVIDENCE_MAX_RECORD+1],prefix[256];filename(name,sizeof(name),slot,kind);
    int e=read_file(dir,name,data);if(e)return e;
    if(!json_object(data,identity)||!valid_id(identity))return -EBADMSG;
    snprintf(prefix,sizeof(prefix),"{\"schema\":\"x6200.usb.incident.v1\",\"session_id\":\"%s\",\"kind\":\"%s\",\"record\":",identity,kind);
    size_t length=strlen(data),head=strlen(prefix);char nested[FD_USB_EVIDENCE_MAX_SESSION+1];
    if(strncmp(data,prefix,head)||length<head+4||data[length-2]!='}'||data[length-1]!='\n')return -EBADMSG;
    data[length-2]=0;
    if(!json_object(data+head,nested)||(nested[0]&&strcmp(nested,identity)))return -EBADMSG;
    return 0;
}
static int write_record(int dir,unsigned slot,const char *id,const char *kind,const char *json){
    char name[64],identity[FD_USB_EVIDENCE_MAX_SESSION+1],data[FD_USB_EVIDENCE_MAX_RECORD+1];
    int e=read_record(dir,slot,kind,identity);
    if(!e)return !strcmp(id,identity)?-EEXIST:-ESTALE;
    if(e!=-ENOENT)return e;
    int n=snprintf(data,sizeof(data),"{\"schema\":\"x6200.usb.incident.v1\",\"session_id\":\"%s\",\"kind\":\"%s\",\"record\":%s}\n",id,kind,json);
    if(n<0||(size_t)n>=sizeof(data))return -EFBIG;
    filename(name,sizeof(name),slot,kind);return atomic_file(dir,name,data);
}
static int read_current(int dir,char *id,unsigned *slot){
    char data[FD_USB_EVIDENCE_MAX_RECORD+1],expected[256];int e=read_file(dir,"current-session.json",data);if(e)return e;
    if(!json_object(data,id)||!valid_id(id))return -EBADMSG;
    for(unsigned i=0;i<FD_USB_EVIDENCE_SLOTS;++i){
        snprintf(expected,sizeof(expected),"{\"schema\":\"x6200.usb.incident-pointer.v1\",\"session_id\":\"%s\",\"slot\":%u}\n",id,i);
        if(!strcmp(data,expected)){*slot=i;return 0;}
    }
    return -EBADMSG;
}
static int find_session(int dir,const char *wanted,unsigned *slot){
    bool found=false;char id[FD_USB_EVIDENCE_MAX_SESSION+1];
    for(unsigned i=0;i<FD_USB_EVIDENCE_SLOTS;++i){
        int e=read_record(dir,i,"metadata",id);
        if(e==-ENOENT)continue;
        if(e)return e;
        if(!strcmp(wanted,id)){if(found)return -EBADMSG;*slot=i;found=true;}
    }
    return found?0:-ENOENT;
}
static int mark_previous(int dir,const char *next){
    char old[FD_USB_EVIDENCE_MAX_SESSION+1],id[FD_USB_EVIDENCE_MAX_SESSION+1];
    unsigned slot;int e=read_current(dir,old,&slot);if(e==-ENOENT)return 0;if(e)return e;if(!strcmp(old,next))return 0;
    e=read_record(dir,slot,"metadata",id);if(e)return e;if(strcmp(old,id))return -ESTALE;
    e=read_record(dir,slot,"completion",id);
    if(!e)return strcmp(old,id)?-ESTALE:0;
    if(e!=-ENOENT)return e;
    char data[FD_USB_EVIDENCE_MAX_RECORD+1],name[64],prefix[256],build[FD_USB_EVIDENCE_MAX_SESSION+1],source[FD_USB_EVIDENCE_MAX_SESSION+1];
    filename(name,sizeof(name),slot,"metadata");e=read_file(dir,name,data);if(e)return e;
    snprintf(prefix,sizeof(prefix),"{\"schema\":\"x6200.usb.incident.v1\",\"session_id\":\"%s\",\"kind\":\"metadata\",\"record\":",old);
    data[strlen(data)-2]=0;const char *payload=data+strlen(prefix);
    bool have_build=identity_tag(payload,"build_id",build),have_source=identity_tag(payload,"source_commit",source);
    char json[768];snprintf(json,sizeof(json),"{\"session_id\":\"%s\",\"reason\":\"session-replaced-without-completion\",\"next_session_id\":\"%s\"%s%s%s%s%s%s}",old,next,
        have_build?",\"build_id\":\"":"",have_build?build:"",have_build?"\"":"",
        have_source?",\"source_commit\":\"":"",have_source?source:"",have_source?"\"":"");
    e=write_record(dir,slot,old,"aborted",json);return e==-EEXIST?0:e;
}
int fd_usb_evidence_store(const char *directory,const char *id,const char *kind,const char *json){
    if(!directory||directory[0]!='/'||!valid_id(id)||!kind||!json)return -EINVAL;
    if(strcmp(kind,"metadata")&&strcmp(kind,"fault")&&strcmp(kind,"completion"))return -EINVAL;
    size_t input_size=strnlen(json,FD_USB_EVIDENCE_MAX_RECORD+1);
    if(input_size>FD_USB_EVIDENCE_MAX_RECORD)return -EFBIG;
    int overhead=snprintf(NULL,0,"{\"schema\":\"x6200.usb.incident.v1\",\"session_id\":\"%s\",\"kind\":\"%s\",\"record\":}\n",id,kind);
    if(overhead<0||input_size+(size_t)overhead>FD_USB_EVIDENCE_MAX_RECORD)return -EFBIG;
    char input_id[FD_USB_EVIDENCE_MAX_SESSION+1];if(!json_object(json,input_id))return -EINVAL;
    if(input_id[0]&&strcmp(input_id,id))return -ESTALE;
    if(mkdir(directory,0700)<0&&errno!=EEXIST)return -errno;
    int dir=open(directory,O_RDONLY|O_DIRECTORY|O_NOFOLLOW);if(dir<0)return -errno;
    struct stat st;int result=0,lock=-1;
    if(fstat(dir,&st)<0){result=-errno;goto done;}
    if(!(st.st_mode&0222)){result=-EROFS;goto done;}
    /* Also make the incident directory's own parent entry durable. Syncing
     * only files within a newly created directory does not cover that entry. */
    result=sync_parent(directory);if(result)goto done;
    lock=openat(dir,".lock",O_RDWR|O_CREAT|O_NOFOLLOW,0600);if(lock<0){result=-errno;goto done;}
    if(flock(lock,LOCK_EX)<0){result=-errno;goto done;}
    unsigned slot=0;int e=find_session(dir,id,&slot);
    if(!strcmp(kind,"metadata")){
        if(!e){result=-EEXIST;goto done;}
        if(e&&e!=-ENOENT){result=e;goto done;}
        char current[FD_USB_EVIDENCE_MAX_SESSION+1];unsigned previous;
        e=read_current(dir,current,&previous);
        if(e&&e!=-ENOENT){result=e;goto done;}
        slot=e==-ENOENT?0:(previous+1)%FD_USB_EVIDENCE_SLOTS;
        result=mark_previous(dir,id);if(result)goto done;
        const char *kinds[]={"metadata","fault","completion","aborted"};
        for(unsigned i=0;i<4;++i){char name[64];filename(name,sizeof(name),slot,kinds[i]);if(unlinkat(dir,name,0)<0&&errno!=ENOENT){result=-errno;goto done;}}
        if(fsync(dir)<0){result=-errno;goto done;}
        result=write_record(dir,slot,id,kind,json);if(result)goto done;
        char pointer[256];snprintf(pointer,sizeof(pointer),"{\"schema\":\"x6200.usb.incident-pointer.v1\",\"session_id\":\"%s\",\"slot\":%u}\n",id,slot);
        result=atomic_file(dir,"current-session.json",pointer);
    }else{
        if(e){result=e==-ENOENT?-ESTALE:e;goto done;}
        result=write_record(dir,slot,id,kind,json);
    }
done:
    if(lock>=0)close(lock);
    close(dir);return result;
}
