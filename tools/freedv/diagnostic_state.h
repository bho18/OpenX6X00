// SPDX-License-Identifier: LGPL-2.1-or-later
#pragma once
#include <stdbool.h>
#include <stdint.h>

typedef enum { FD_IDLE, FD_READY, FD_ARMED, FD_RUNNING, FD_WAIT_RX, FD_RESTORE } fd_state;
/* Bits are RECORD, MODEM, software PTT, in that order. Every combination is
 * potentially keyed and therefore needs local arming before execution. */
typedef enum {
    FD_R0_M0_P0 = 0, FD_R0_M0_P1, FD_R0_M1_P0, FD_R0_M1_P1,
    FD_R1_M0_P0, FD_R1_M0_P1, FD_R1_M1_P0, FD_R1_M1_P1
} fd_matrix_case;
typedef struct {
    bool known;
    bool fresh;       /* caller's explicit freshness limit, not inferred from !tx */
    bool tx;          /* baseband packet observation, not the software command */
    uint64_t sequence; /* changes for every received packet */
} fd_observation;
typedef enum { FD_CASE_PASSIVE, FD_CASE_ROUTE, FD_CASE_RX_PLAYBACK, FD_CASE_MIC_TX } fd_case_kind;
typedef struct {
    fd_case_kind kind;
    int matrix_case; /* -1 only for passive; otherwise 0..7 */
} fd_case;
typedef struct {
    fd_state state;
    bool tx_case;
    fd_case selected_case;
    uint8_t qualified_rx_routes; /* session-only, cleared on acquire/release */
    bool selected;
    bool software_cleared;
    int matrix_case; /* -1 for passive receive only */
    uint64_t deadline_ms;
    uint64_t clear_sequence;
} fd_session;

/* Caller serializes all operations, acquires exclusive radio/audio ownership and
 * snapshots state under the radio lock. Do not use this prototype as the owner. */
bool fd_acquire(fd_session *s, bool busy, bool commands_quiescent, fd_observation observed);
/* Passive receive only: no route changes or generated playback. */
bool fd_select_passive_rx(fd_session *s);
/* Selecting a matrix case changes model state only; it must never key RF. */
bool fd_select_matrix(fd_session *s, fd_matrix_case selected);
bool fd_matrix_bits(fd_matrix_case selected, bool *record, bool *modem, bool *ptt);
/* The caller must establish the request came from local controls. */
bool fd_arm(fd_session *s, bool local);
/* The caller must recheck the transmit frequency and guards immediately before
 * running. Every potentially keyed matrix combination is a TX case. */
bool fd_run(fd_session *s, uint64_t now_ms, bool tx_permitted, fd_observation observed);
/* True requests immediate audio flush and clearing software MODEM/PTT. */
bool fd_stop(fd_session *s);
bool fd_tick(fd_session *s, uint64_t now_ms);
/* After the flush and command clear actually succeed, record the latest packet
 * sequence. A later packet is required before ordinary routing can return. */
bool fd_commands_cleared(fd_session *s, uint64_t latest_sequence);
/* Restore only after a known, fresh, post-clear packet reports TX=false. */
bool fd_rx_observed(fd_session *s, fd_observation observed);
/* Call only after successful restoration; otherwise the session stays blocked. */
bool fd_restored(fd_session *s);
bool fd_release(fd_session *s);

/* Receive playback never authorizes a route itself. The controller records a
 * completed probe and explicit local operator confirmation before qualification. */
bool fd_select_rx_playback(fd_session *s, fd_matrix_case route);
bool fd_select_mic_tx(fd_session *s, fd_matrix_case route);
