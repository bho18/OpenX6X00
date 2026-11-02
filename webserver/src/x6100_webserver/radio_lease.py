"""Cross-process settings admission shared with freedv/writer_gate.c."""
import fcntl
import os

PATH = '/tmp/openx6x00-settings.lock'


def acquire(path=PATH):
    fd = os.open(path, os.O_RDONLY | os.O_CREAT | os.O_CLOEXEC | os.O_NOFOLLOW, 0o666)
    try:
        fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        raise
    return fd


def release(fd):
    os.close(fd)
