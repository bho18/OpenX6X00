// SPDX-License-Identifier: LGPL-2.1-or-later
#pragma once
#ifdef ENABLE_FREEDV_DIAGNOSTICS
#include <stdbool.h>
#include <unistd.h>
#include <errno.h>
#include <linux/input.h>
/* Nonblocking input fds only. A continuously flooded device prevents local
 * consent instead of holding the GUI forever. EOF is a drained host FIFO. */
static inline bool fd_diagnostic_drain_input(int fd) {
    if (fd < 0) return true;
    struct input_event event;
    for (unsigned n = 0; n < 4096; ++n) {
        ssize_t bytes = read(fd, &event, sizeof(event));
        if (!bytes || (bytes < 0 && (errno == EAGAIN || errno == EWOULDBLOCK))) return true;
        if (bytes < 0 && errno == EINTR) continue;
        if (bytes != (ssize_t)sizeof(event)) return false;
    }
    return false;
}
#endif
