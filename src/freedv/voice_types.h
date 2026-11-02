// SPDX-License-Identifier: LGPL-2.1-or-later
#pragma once
#include <stdbool.h>
#include <stdint.h>
typedef enum { FD_VS_OFF, FD_VS_PREPARING, FD_VS_RX, FD_VS_TX_STARTING, FD_VS_TX,
    FD_VS_TX_DRAIN, FD_VS_FAULT, FD_VS_WAIT_PTT_RELEASE, FD_VS_EXITING } fd_voice_state;
typedef enum { FD_PTT_RADIO, FD_PTT_HAND, FD_PTT_SOURCES } fd_ptt_source;
typedef enum { FD_ABORT_NONE, FD_ABORT_STOP, FD_ABORT_TIMEOUT, FD_ABORT_HEALTH,
    FD_ABORT_DEVICE, FD_ABORT_DRAIN, FD_ABORT_PROTECTION } fd_abort_reason;
typedef struct {
    bool valid, pressed;
    uint64_t sequence, monotonic_ms;
} fd_ptt_observation;
typedef struct {
    bool valid, tx, protection, device_error;
    uint64_t sequence, monotonic_ms;
    fd_ptt_observation ptt[FD_PTT_SOURCES];
} fd_voice_observation;
typedef enum { FD_LOCAL_FREQUENCY, FD_LOCAL_BAND, FD_LOCAL_VFO, FD_LOCAL_VOLUME, FD_LOCAL_MEMORY } fd_local_operation;
typedef struct {
    int32_t frequency;
    unsigned vfo;
    fd_local_operation operation;
    int32_t request_value;
} fd_voice_tuning;
typedef struct {
    fd_voice_state state;
    fd_abort_reason fault;
    uint64_t generation, turns;
    unsigned timeout_seconds;
    bool ptt_held[FD_PTT_SOURCES], ptt_unknown[FD_PTT_SOURCES];
    bool synchronized, microphone_active, exit_requested;
    fd_voice_tuning tuning;
} fd_voice_status;
