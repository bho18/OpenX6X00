// SPDX-License-Identifier: LGPL-2.1-or-later
#define _GNU_SOURCE
#include "src/freedv/writer_gate.h"
#include <sys/file.h>
#include <sys/wait.h>
#include <fcntl.h>
#include <unistd.h>
#include <assert.h>
#include <stdio.h>
static int lockfile(void){int fd=open("/tmp/openx6x00-settings.lock",O_RDONLY|O_CREAT|O_CLOEXEC,0666);assert(fd>=0);return fd;}
int main(void){
    int web=lockfile();assert(!flock(web,LOCK_SH|LOCK_NB));
    assert(!fd_writer_voice_reserve());close(web);
    assert(fd_writer_voice_reserve());assert(!fd_writer_enter());
    pid_t child=fork();assert(child>=0);
    if(!child){int remote=lockfile();_exit(flock(remote,LOCK_SH|LOCK_NB)<0?0:1);}
    int status;assert(waitpid(child,&status,0)==child && WIFEXITED(status) && !WEXITSTATUS(status));
    assert(!fd_writer_hardware_begin(false));
    int safe=fd_writer_hardware_begin(true);assert(safe);fd_writer_hardware_end(safe);
    assert(fd_writer_voice_begin() && fd_writer_voice_authorized());
    assert(fd_writer_enter());fd_writer_exit();fd_writer_voice_end();assert(!fd_writer_voice_authorized());
    fd_writer_release();web=lockfile();assert(!flock(web,LOCK_SH|LOCK_NB));close(web);
    assert(fd_writer_enter());fd_writer_exit();
    puts("PASS: cross-process web/voice exclusion in both orders, owned scopes and hardware safe-off");
}
