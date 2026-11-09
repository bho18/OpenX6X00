/* SPDX-License-Identifier: LGPL-2.1-or-later */
#pragma once

#include "control_owner.h"
#include "freedv/writer_gate.h"
#include "process_lifecycle.h"

/* GUI-thread admission for one external transaction. A request queued before
 * a mode transition must not acquire new meaning after that mode exits. The
 * ordinary writer scope remains on this thread until every setter completes;
 * no dispatch mutex is held while another thread waits for the GUI. */
class RemoteWriteScope {
  public:
    explicit RemoteWriteScope(unsigned generation) : generation_(generation) {}
    ~RemoteWriteScope() {
#ifdef ENABLE_FREEDV_CONTROL
        if (owned_) fd_writer_exit();
#endif
    }
    RemoteWriteScope(const RemoteWriteScope &) = delete;
    RemoteWriteScope &operator=(const RemoteWriteScope &) = delete;

    bool enter() {
        if (attempted_) return error_ == nullptr;
        attempted_ = true;
#ifdef ENABLE_FREEDV_CONTROL
        owned_ = fd_writer_enter();
        if (!owned_ || ctl_diagnostic_input_blocked()) {
            error_ = "Radio settings are owned by FreeDV or diagnostics; exit the mode before remote changes";
        } else
#endif
        if (generation_ != ctl_remote_input_generation()) {
            error_ = "Radio control changed before this request was applied; retry";
        } else if (process_lifecycle_stopping()) {
            error_ = "Radio is stopping";
        }
        return error_ == nullptr;
    }
    const char *error() const { return error_; }

  private:
    unsigned generation_;
    bool attempted_ = false;
#ifdef ENABLE_FREEDV_CONTROL
    bool owned_ = false;
#endif
    const char *error_ = nullptr;
};
