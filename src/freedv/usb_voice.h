// SPDX-License-Identifier: LGPL-2.1-or-later
#pragma once
#include <stdbool.h>
#include <stdint.h>
#include <stdatomic.h>
#include "voice_engine.h"
#include "qualified_profile.h"
#define FD_USB_MIC_GAIN_MIN_DB (-12)
#define FD_USB_MIC_GAIN_MAX_DB 24
/* Values 0..6 are retained for older evidence readers. */
typedef enum {
    FD_USB_PREPARE, FD_USB_RX, FD_USB_START, FD_USB_TX, FD_USB_DRAIN,
    FD_USB_FAULT, FD_USB_DONE, FD_USB_KEY_PENDING, FD_USB_UNKEY, FD_USB_RX_READY,
    FD_USB_TUNING
} fd_usb_state;
typedef struct {
    bool valid,pressed,rf_valid,rf_tx,protection;
    uint64_t release,sequence;
    /* Operational radio configuration is submitted asynchronously. These are
     * software admission/readiness facts, not physical tuning readback. */
    bool configuration_ready,tx_eligible;
    uint64_t tuning_revision;
} fd_usb_inputs;
typedef struct {
    void *context;
    bool (*observe)(void *,fd_usb_inputs *);
    bool (*key)(void *,uint64_t expected_release);
    void (*clear)(void *,bool halt);
    /* Optional immediate admission fence: must only update nonblocking state.
     * key/clear themselves run on the serialized control worker. */
    void (*inhibit)(void *);
    /* Nonblocking TX-admission gate. False prevents new key requests while a
     * graceful active turn may still finish. Operational sessions require it. */
    bool (*permit)(void *,bool);
} fd_usb_control;
typedef struct {
    const char *source,*sink,*radio_source,*radio_sink,*trace_prefix,*incident_dir;
    fd_usb_control control;
    bool rf_disabled;
    bool operational; /* false retains version-3 diagnostic behavior */
    bool retain_runtime_traces; /* UI /tmp timestamp namespace: current + one previous */
    unsigned tx_timeout_seconds; /* operational: 30..600, zero selects 180 */
    const char *headset_id; /* stable physical USB device ID; NULL/empty = unique auto */
    int mic_gain_db; /* Digital speech gain; zero preserves microphone PCM exactly. */
    unsigned qualification_turns,qualification_control_delay_ms,qualification_idle_ms,trace_pcm_seconds;
} fd_usb_config;
typedef struct fd_usb_audio fd_usb_audio;
typedef struct {
    fd_usb_state state;unsigned fault;uint64_t generation,turns,heartbeat_ms;
    unsigned input_queue,output_queue,holes,underflows,gaps;
    bool armed,pressed,rf_valid,rf_tx,stopped;
    /* startup_ready retains initial proof; armed/configuration_ready describe current admission. */
    bool operational,startup_ready,leaving,configuration_ready,tx_eligible;
    uint64_t tuning_revision;
    bool rf_disabled,qualification_complete,qualification_running,qualification_loopback,rf_clear_confirmed;
    unsigned tx_timeout_seconds;
    unsigned qualification_target,qualification_turns,mic_rate_hz,rx_rate_hz,qualification_idle_remaining_ms;
    uint64_t submitted_mic,rendered_modem,modem_tap_samples,transport_tail_frames,modem_written_frames;
    unsigned render_queue_high_water,render_deferred_count,render_final_deferred_count,render_final_flush_calls;
    uint64_t mic_rate_window_ms,rx_rate_window_ms;
    unsigned first_fault_code,first_fault_pa_error;
    uint64_t first_fault_operation_ms,first_fault_mic_age_ms,first_fault_rx_age_ms,mic_age_ms,rx_age_ms;
    char first_fault_origin[64],first_fault_stream[16];
    uint64_t mic_captured,rx_captured,mic_discarded,rx_discarded;
    uint64_t mic_max_gap_ms,rx_max_gap_ms;
    unsigned mic_holes,rx_holes,headset_underflows,modem_underflows;
    int mic_gain_db,mic_input_rms_dbfs10,mic_input_peak_dbfs10,mic_rms_dbfs10,mic_peak_dbfs10;
    bool mic_meter_ready;
    uint64_t mic_clipped_samples; /* Accepted TX samples only; RX meter previews do not increment it. */
    unsigned headset_volume_percent;
    bool headset_volume_ready,headset_muted;
    char headset_id[256],headset_name[160];
    fd_voice_stats engine;
} fd_usb_snapshot;
fd_usb_audio *fd_usb_audio_open(const fd_usb_config *);
void fd_usb_audio_arm(fd_usb_audio *,bool);
bool fd_usb_audio_set_tx_timeout(fd_usb_audio *,unsigned seconds);
bool fd_usb_audio_set_mic_gain(fd_usb_audio *,int);
bool fd_usb_audio_adjust_headset_volume(fd_usb_audio *,int delta_percent);
bool fd_usb_audio_start_qualification(fd_usb_audio *);
bool fd_usb_audio_enable_rf(fd_usb_audio *);
void fd_usb_audio_stop(fd_usb_audio *); /* fences admission and queues priority OFF; never waits for audio/I2C */
void fd_usb_audio_leave(fd_usb_audio *); /* graceful finite speech/tail completion, then OFF */
fd_usb_snapshot fd_usb_audio_snapshot(fd_usb_audio *);
void fd_usb_audio_close(fd_usb_audio *); /* only after snapshot.stopped */
const char *fd_usb_fault_text(unsigned);

#if defined(ENABLE_FREEDV_USB_VOICE) || defined(ENABLE_FREEDV_USB_TEST)
bool freedv_usb_available(void);
bool freedv_usb_active(void);
void freedv_usb_open(void);
bool freedv_usb_ptt(bool);
void freedv_usb_abort(void);
bool freedv_usb_leave(void);
const fd_qualified_profile *freedv_usb_profile_view(void);
#else
static inline bool freedv_usb_available(void){return false;}
static inline bool freedv_usb_active(void){return false;}
static inline void freedv_usb_open(void){}
static inline bool freedv_usb_ptt(bool p){(void)p;return false;}
static inline void freedv_usb_abort(void){}
static inline bool freedv_usb_leave(void){return false;}
static inline const fd_qualified_profile *freedv_usb_profile_view(void){return 0;}
#endif
