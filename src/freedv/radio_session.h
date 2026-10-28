// SPDX-License-Identifier: LGPL-2.1-or-later
#pragma once
#include "../hw/hw.h"
#include "../../tools/freedv/diagnostic_state.h"
#ifdef __cplusplus
extern "C" {
#endif
#define FD_RADIO_PACKET_MAX_AGE_MS 250u
/* Radio-owned state only. The coordinator additionally owns a complete ALSA /
 * stream snapshot outside the radio lock and must acknowledge its audio flush
 * before calling restore. No function below changes persistent preferences. */
typedef struct {
    hw_freedv_cached_snapshot_t saved;
    hw_freedv_cached_snapshot_t restored;
    bool owned, ready, case_active, route_applied, restoration_complete, software_cleared;
    bool readback_match;
    unsigned base_errors_at_acquire, base_error_events;
    unsigned abort_reason; /* fd_radio_error, retained after successful cleanup */
    bool transport_ok; /* last explicit resend, never inferred from setter return */
    unsigned transport_attempts, transport_failures, restoration_failures;
    unsigned swr_trips_at_acquire;
    int mic_override;
    bool modem_on, modem_moved, split_held;
    bool mute, tci_mute, rec_monitor, digital_power_cap;
    uint64_t acquired_sequence, clear_sequence;
    unsigned last_error; /* fd_radio_error */
} fd_radio_session;
typedef enum {
    FD_RADIO_OK, FD_RADIO_BUSY, FD_RADIO_STATUS, FD_RADIO_UNSUPPORTED,
    FD_RADIO_STATE_CHANGED, FD_RADIO_TX_GUARD, FD_RADIO_TRANSPORT,
    FD_RADIO_READBACK, FD_RADIO_BAD_ARGUMENT, FD_RADIO_STARTUP
} fd_radio_error;
const char *radio_freedv_error_string(fd_radio_error);
/* True means a lease/snapshot was acquired; ready additionally attests the
 * post-reservation busy/status recheck. The controller must reject ready=false
 * and safely release/clear/wait if physical PTT races that recheck. False means
 * no ownership was acquired. Acquiring never changes routes or key commands. */
bool radio_freedv_session_acquire(fd_radio_session *, fd_observation *);
bool radio_freedv_session_observe(fd_observation *);
bool radio_freedv_session_tx_permitted(fd_radio_session *);
bool radio_freedv_session_apply(fd_radio_session *, const fd_case *, radio_mic_sel_t);
/* Caller first fences audio atomically. This clears software key commands and
 * samples the packet sequence; it does not claim held hardware PTT was released. */
bool radio_freedv_session_clear(fd_radio_session *, uint64_t *sequence);
bool radio_freedv_session_restore(fd_radio_session *, uint64_t clear_sequence);
bool radio_freedv_session_release(fd_radio_session *);
/* Diagnostic startup waits for fresh RX before analog microphone restoration. */
bool radio_freedv_startup_waiting(void);
#ifdef __cplusplus
}
#endif
