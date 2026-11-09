// SPDX-License-Identifier: LGPL-2.1-or-later
#define _POSIX_C_SOURCE 200809L
#include <assert.h>
#include <errno.h>
#include <fcntl.h>
#include <pthread.h>
#include <signal.h>
#include <stdatomic.h>
#include <stdint.h>
#include <stdio.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>

/* Hold the real production coordinator before its first read. This lets the
 * real kernel eventfd reach EAGAIN, instead of faking a notification failure
 * on an empty descriptor (which would not model the kernel's contract). */
static atomic_bool release_coordinator;
static void *(*start_function)(void *);
static void *start_argument;
static int delayed_create(pthread_t *,const pthread_attr_t *,void *(*)(void *),void *);
#define pthread_create delayed_create
#include "../../src/process_lifecycle.c"
#undef pthread_create

static void *delayed_start(void *unused) {
    (void)unused;
    while(!atomic_load(&release_coordinator)) {
        struct timespec pause={.tv_nsec=1000000};nanosleep(&pause,NULL);
    }
    return start_function(start_argument);
}
static int delayed_create(pthread_t *thread,const pthread_attr_t *attr,void *(*function)(void *),void *arg) {
    start_function=function;start_argument=arg;
    return pthread_create(thread,attr,delayed_start,NULL);
}
static int evidence;
static void bypass_marker(void *unused) {
    (void)unused;char c='B';assert(write(evidence,&c,1)==1);
}
int main(void) {
    int pipefd[2];assert(!pipe(pipefd));pid_t child=fork();assert(child>=0);
    if(!child) {
        close(pipefd[0]);evidence=pipefd[1];
        process_lifecycle_ops ops={.bypass=bypass_marker};assert(process_lifecycle_init(&ops));
        assert(fcntl(wake_fd,F_GETFD)&FD_CLOEXEC);
        uint64_t full=UINT64_MAX-1;assert(write(wake_fd,&full,sizeof(full))==(ssize_t)sizeof(full));
        char c='R';assert(write(evidence,&c,1)==1);
        while(!process_lifecycle_stopping()) {struct timespec t={.tv_nsec=1000000};nanosleep(&t,NULL);}
        uint64_t one=1;assert(write(wake_fd,&one,sizeof(one))<0&&errno==EAGAIN);
        c='F';assert(write(evidence,&c,1)==1);atomic_store(&release_coordinator,true);
        for(;;)pause();
    }
    close(pipefd[1]);char c;assert(read(pipefd[0],&c,1)==1&&c=='R');assert(!kill(child,SIGTERM));
    assert(read(pipefd[0],&c,1)==1&&c=='F');assert(read(pipefd[0],&c,1)==1&&c=='B');
    int status;assert(waitpid(child,&status,0)==child&&WIFSIGNALED(status)&&WTERMSIG(status)==SIGTERM);
    close(pipefd[0]);puts("PASS: full nonblocking eventfd cannot lose a TERM request; CLOEXEC retained");return 0;
}
