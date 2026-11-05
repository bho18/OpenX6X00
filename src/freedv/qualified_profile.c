// SPDX-License-Identifier: LGPL-2.1-or-later
#include "qualified_profile.h"
#include <string.h>
static bool present(const char *s) { return s && *s; }
bool fd_profile_candidate_valid(const fd_qualified_profile *p, const char *hw, const char *build) {
    if (!p || !hw || !build || !present(p->hardware) || !present(p->build_id) ||
        strcmp(p->hardware, hw) || strcmp(p->build_id, build) ||
        (p->capabilities & ~FD_CAP_ALL) || !p->upper_sideband ||
        (p->local_mode != FD_PROFILE_USB && p->local_mode != FD_PROFILE_USB_DIG) ||
        !present(p->id) || !present(p->baseband) || !present(p->control_version) ||
        !present(p->capture_device) || !present(p->playback_device) || !present(p->mixer) ||
        (p->sample_rate != 8000 && p->sample_rate != 44100 && p->sample_rate != 48000) ||
        p->capture_channel > 3 || p->rx_low_hz < 0 || p->tx_low_hz < 0 ||
        p->rx_high_hz <= p->rx_low_hz || p->tx_high_hz <= p->tx_low_hz ||
        p->rx2_low_hz<0 || p->rx2_high_hz<p->rx2_low_hz ||
        p->agc<0 || p->agc>3 || (p->rx_route & ~FD_ROUTE_RECORD) || (p->tx_route & ~7u) ||
        p->power_tenths_watt <= 0) return false;
    return true;
}
bool fd_profile_valid(const fd_qualified_profile *p, const char *hw, const char *build) {
    return fd_profile_candidate_valid(p,hw,build) && p->complete_pass && p->capabilities==FD_CAP_ALL &&
        p->evidence_sha256 && strlen(p->evidence_sha256)==64 &&
        strspn(p->evidence_sha256,"0123456789abcdef")==64 &&
        (p->fixed_rx2_filter_attested || p->rx2_high_hz>p->rx2_low_hz);
}
const fd_qualified_profile *fd_profile_select(const fd_qualified_profile *usb,
        const fd_qualified_profile *dig, const char *hw, const char *build) {
    if (fd_profile_valid(dig, hw, build) && dig->local_mode == FD_PROFILE_USB_DIG) return dig;
    if (fd_profile_valid(usb, hw, build) && usb->local_mode == FD_PROFILE_USB) return usb;
    return NULL;
}
const fd_qualified_profile *fd_profile_production(void) { return NULL; }
