// SPDX-License-Identifier: LGPL-2.1-or-later
#pragma once
#include <stdbool.h>
#ifdef __cplusplus
extern "C" {
#endif
/* A reservation fails while a writer is in flight. Once reserved, new writers
 * are rejected before mutation. Nesting is allowed on an admitted writer's
 * thread; callers must end on the same thread. No lock is held across callbacks. */
bool fd_writer_enter(void);
void fd_writer_exit(void);
bool fd_writer_reserve(void);
void fd_writer_release(void);
#ifdef __cplusplus
}
#endif
#ifdef ENABLE_FREEDV_DIAGNOSTICS
#ifdef __cplusplus
class FdWriterGuard {
public:
    const bool admitted;
    FdWriterGuard() : admitted(fd_writer_enter()) {}
    ~FdWriterGuard() { if (admitted) fd_writer_exit(); }
    FdWriterGuard(const FdWriterGuard &) = delete;
    FdWriterGuard &operator=(const FdWriterGuard &) = delete;
};
#define FD_WRITE_GUARD(on_busy) FdWriterGuard fd_write_guard; if (!fd_write_guard.admitted) { on_busy; }
#else
typedef struct { bool admitted; } fd_writer_guard;
static inline void fd_writer_cleanup(fd_writer_guard *g) { if (g->admitted) fd_writer_exit(); }
#define FD_WRITE_GUARD(on_busy) fd_writer_guard fd_write_guard __attribute__((cleanup(fd_writer_cleanup))) = {fd_writer_enter()}; if (!fd_write_guard.admitted) { on_busy; }
#endif
#else
#define FD_WRITE_GUARD(on_busy)
#endif
