// SPDX-License-Identifier: LGPL-2.1-or-later
#define _POSIX_C_SOURCE 200809L
#include "../../src/process_lifecycle.h"
#include <assert.h>
#include <errno.h>
#include <fcntl.h>
#include <poll.h>
#include <pthread.h>
#include <signal.h>
#include <spawn.h>
#include <stdatomic.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>

enum scenario {ORDINARY_TERM,ORDINARY_INT,ACTIVE_COMPLETE,ACTIVE_INT_COMPLETE,STALLED_GUI,RESTART_AUDIO};
extern char **environ;
static int output;
static enum scenario selected;
static atomic_bool requested;
static char journal_path[96];
static uint64_t clock_ms(void) {
    struct timespec t;clock_gettime(CLOCK_MONOTONIC,&t);
    return (uint64_t)t.tv_sec*1000u+(unsigned long)t.tv_nsec/1000000u;
}
static void sleep_ms(unsigned ms) {
    struct timespec t={.tv_sec=ms/1000,.tv_nsec=(long)(ms%1000)*1000000};
    while(nanosleep(&t,&t)&&errno==EINTR){}
}
static void event(char value) {assert(write(output,&value,1)==1);}
static bool active(void *unused) {(void)unused;return selected>=ACTIVE_COMPLETE;}
static void request(void *unused) {
    (void)unused;assert(process_lifecycle_stopping());event('L');atomic_store(&requested,true);
}
static void abort_request(void *unused) {
    (void)unused;assert(process_lifecycle_aborting());assert(process_lifecycle_incomplete());event('A');
    /* A GUI ACK racing after the deadline must not erase INCOMPLETE. */
    process_lifecycle_cleanup_complete();
}
static void bypass(void *unused) {
    (void)unused;assert(process_lifecycle_stopping());event('B');
}
static void *inherited_mask(void *unused) {
    (void)unused;sigset_t mask;assert(!pthread_sigmask(SIG_BLOCK,NULL,&mask));
    assert(!sigismember(&mask,SIGTERM)&&!sigismember(&mask,SIGINT));
    event('M');return NULL;
}
static void exec_signals(void) {
    /* Reproduce the concrete sigwait regression: an exec child must not
     * inherit blocked TERM/INT from the GUI's lifecycle implementation. */
    const char *commands[]={"kill -TERM $$", "kill -INT $$"};
    const int signals[]={SIGTERM,SIGINT};
    for(unsigned i=0;i<2;i++){
        char *args[]={"sh","-c",(char *)commands[i],NULL};pid_t pid;int status;
        assert(!posix_spawn(&pid,"/bin/sh",NULL,NULL,args,environ));
        assert(waitpid(pid,&status,0)==pid&&WIFSIGNALED(status)&&WTERMSIG(status)==signals[i]);
    }
    int status=system("kill -TERM $$");assert(WIFSIGNALED(status)&&WTERMSIG(status)==SIGTERM);
    FILE *pipe=popen("kill -TERM $$","r");assert(pipe);assert(fgetc(pipe)==EOF);
    status=pclose(pipe);assert(WIFSIGNALED(status)&&WTERMSIG(status)==SIGTERM);
    event('E');
}
static void child(enum scenario scenario,int fd) {
    selected=scenario;output=fd;
    assert(!process_lifecycle_stopping());assert(!process_lifecycle_request_audio_restart());
    process_lifecycle_ops ops={.active=active,.request_cleanup=request,.abort_requested=abort_request,.bypass=bypass,.journal_path=journal_path};
    assert(process_lifecycle_init(&ops));assert(!process_lifecycle_init(&ops)&&errno==EALREADY);
    process_lifecycle_set_session("freedv-usb-lifecycle-fixture");
    pthread_t worker;assert(!pthread_create(&worker,NULL,inherited_mask,NULL));assert(!pthread_join(worker,NULL));
    if(scenario==ORDINARY_TERM)exec_signals();
    event('R');
    if(scenario==RESTART_AUDIO) {
        /* The caller-side radio proof belongs to the audio integration test;
         * this proves one request takes the existing exit-code-3 daemon path. */
        assert(process_lifecycle_request_audio_restart());
        for(;;)pause();
    }
    for(;;) {
        if((scenario==ACTIVE_COMPLETE || scenario==ACTIVE_INT_COMPLETE) && atomic_load(&requested)) {
            sleep_ms(30);event('T');process_lifecycle_cleanup_complete();
            for(;;)pause();
        }
        sleep_ms(1);
    }
}
static void run(enum scenario scenario) {
    snprintf(journal_path,sizeof(journal_path),"/tmp/x6200-lifecycle-test-XXXXXX");
    int journal=mkstemp(journal_path);assert(journal>=0);close(journal);
    int pipefd[2];assert(!pipe(pipefd));
    pid_t pid=fork();assert(pid>=0);
    if(!pid){close(pipefd[0]);child(scenario,pipefd[1]);_exit(100);}
    close(pipefd[1]);int flags=fcntl(pipefd[0],F_GETFL);assert(flags>=0);assert(!fcntl(pipefd[0],F_SETFL,flags|O_NONBLOCK));
    char events[64]={0};unsigned count=0;bool signaled=false;int status=0;uint64_t began=clock_ms(),sent=0,last_repeat=0;
    for(;;) {
        char data[16];ssize_t n=read(pipefd[0],data,sizeof(data));
        if(n>0){assert(count+(unsigned)n<sizeof(events));memcpy(events+count,data,(size_t)n);count+=(unsigned)n;}
        if(!signaled&&strchr(events,'R')&&scenario!=RESTART_AUDIO){
            sent=clock_ms();assert(!kill(pid,(scenario==ORDINARY_INT || scenario==ACTIVE_INT_COMPLETE)?SIGINT:SIGTERM));signaled=true;last_repeat=sent;
        }
        if(scenario==STALLED_GUI&&signaled&&clock_ms()-last_repeat>=100){
            /* An INT after the first TERM must neither replace its exit
             * status nor extend the first signal's five-second deadline. */
            assert(!kill(pid,SIGINT));last_repeat=clock_ms();
        }
        pid_t done=waitpid(pid,&status,WNOHANG);assert(done>=0);
        if(done==pid)break;
        if(clock_ms()-began>8000){kill(pid,SIGKILL);waitpid(pid,&status,0);assert(!"lifecycle exceeded its independent bound");}
        struct pollfd p={.fd=pipefd[0],.events=POLLIN};poll(&p,1,2);
    }
    for(;;){char c;ssize_t n=read(pipefd[0],&c,1);if(n<=0)break;assert(count+1<sizeof(events));events[count++]=c;}
    close(pipefd[0]);
    assert(strchr(events,'M')&&strchr(events,'R')&&strchr(events,'B'));
    if(scenario==ORDINARY_TERM)assert(strchr(events,'E'));
    if(scenario==RESTART_AUDIO){assert(WIFEXITED(status)&&WEXITSTATUS(status)==3);assert(!strchr(events,'L'));}
    else {
        assert(WIFSIGNALED(status)&&WTERMSIG(status)==((scenario==ORDINARY_INT || scenario==ACTIVE_INT_COMPLETE)?SIGINT:SIGTERM));
        if(scenario==ACTIVE_COMPLETE || scenario==ACTIVE_INT_COMPLETE){assert(strchr(events,'L')&&strchr(events,'T'));assert(strchr(events,'T')<strchr(events,'B'));assert(!strchr(events,'A'));}
        else if(scenario==STALLED_GUI){assert(strchr(events,'L')&&strchr(events,'A'));assert(strchr(events,'A')<strchr(events,'B'));assert(clock_ms()-sent>=4500&&clock_ms()-sent<7000);}
        else assert(!strchr(events,'L')&&!strchr(events,'A'));
    }
    unsigned char bytes[800];journal=open(journal_path,O_RDONLY);assert(journal>=0);
    assert(read(journal,bytes,sizeof(bytes))==(ssize_t)sizeof(bytes));close(journal);
    assert(!memcmp(bytes,"X6LIFEV1",8));
    uint32_t word;memcpy(&word,bytes+32,4);assert(word==(uint32_t)pid);
    memcpy(&word,bytes+44,4);assert(word==(uint32_t)(scenario==STALLED_GUI?4:3));
    assert(!strcmp((char *)bytes+128,"freedv-usb-lifecycle-fixture"));
    if(scenario==STALLED_GUI){
        pid_t next=fork();assert(next>=0);
        if(!next){process_lifecycle_ops ops={.journal_path=journal_path};assert(process_lifecycle_init(&ops));process_lifecycle_set_session("freedv-usb-next-run");_exit(0);}
        assert(waitpid(next,&status,0)==next&&WIFEXITED(status)&&WEXITSTATUS(status)==0);
        journal=open(journal_path,O_RDONLY);assert(journal>=0);assert(read(journal,bytes,sizeof(bytes))==(ssize_t)sizeof(bytes));close(journal);
        memcpy(&word,bytes+428,4);assert(word==4);
        memcpy(&word,bytes+416,4);assert(word==(uint32_t)pid);
        assert(!strcmp((char *)bytes+512,"freedv-usb-lifecycle-fixture"));
        assert(!strcmp((char *)bytes+128,"freedv-usb-next-run"));
    }
    assert(!unlink(journal_path));
}
int main(void) {
    for(enum scenario scenario=ORDINARY_TERM;scenario<=RESTART_AUDIO;++scenario)run(scenario);
    puts("PASS: exec-child signals, ordinary/active TERM/INT, accepted tail, five-second mixed-signal deadline, sticky restart-durable outcome and one-shot code-3 restart");
    return 0;
}
