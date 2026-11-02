// SPDX-License-Identifier: LGPL-2.1-or-later
#pragma once
#include <stdbool.h>
#include <stdint.h>
#ifdef __cplusplus
extern "C" {
#endif
typedef enum { FD_PROFILE_USB, FD_PROFILE_USB_DIG } fd_profile_mode;
enum { FD_ROUTE_RECORD = 1u, FD_ROUTE_MODEM = 2u, FD_ROUTE_PTT = 4u };
enum {
    FD_CAP_MODEM_RF = 1u << 0, FD_CAP_HAND_MIC = 1u << 1,
    FD_CAP_NO_ANALOG_LEAK = 1u << 2, FD_CAP_RF_WITH_SPEAKER = 1u << 3,
    FD_CAP_RADIO_PTT = 1u << 4, FD_CAP_HAND_PTT_RELEASE = 1u << 5,
    FD_CAP_ALL = (1u << 6) - 1
};
/* Static, immutable, one complete profile per session. Orientation describes
 * the waveform; local_mode describes this radio's settings group. */
typedef struct {
    const char *id, *hardware, *build_id, *baseband, *control_version;
    const char *evidence_sha256;
    fd_profile_mode local_mode;
    bool upper_sideband;
    const char *capture_device, *playback_device, *mixer;
    unsigned sample_rate, capture_channel;
    uint32_t rx_route, tx_route;
    int rx_low_hz, rx_high_hz, tx_low_hz, tx_high_hz;
    int rx2_low_hz, rx2_high_hz;
    int agc, mic_gain, line_gain, monitor_gain, power_tenths_watt;
    int line_out_gain, rf_gain, squelch;
    bool preamp, attenuation, mixer_levels_attested;
    long mixer_playback[2], mixer_capture[2]; /* exact qualified ALSA channel levels */
    bool compression, nr, notch, eq, microphone_mixing;
    /* An unavailable command may be a measured fixed baseband characteristic;
     * that fact must be explicitly attested, never inferred from USB/DIGI. */
    bool fixed_tx_filter_attested, fixed_eq_bypass_attested;
    bool fixed_rx2_filter_attested, fixed_if_shift_attested;
    unsigned capabilities;
    bool complete_pass;
} fd_qualified_profile;
bool fd_profile_valid(const fd_qualified_profile *, const char *hardware, const char *build_id);
/* Both complete: prefer USB-DIG. A partial success is never eligible. */
const fd_qualified_profile *fd_profile_select(const fd_qualified_profile *usb,
        const fd_qualified_profile *digital, const char *hardware, const char *build_id);
/* No production profile is registered until physical qualification passes. */
const fd_qualified_profile *fd_profile_production(void);
#ifdef __cplusplus
}
#endif
