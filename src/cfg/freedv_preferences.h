// SPDX-License-Identifier: LGPL-2.1-or-later
#pragma once
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#ifdef __cplusplus
extern "C" {
#endif
bool cfg_freedv_usage_hidden(void);
/* Acknowledgement is saved immediately, including while voice owns controls. */
bool cfg_freedv_usage_set_hidden(bool hidden);
void cfg_freedv_usb_headset_id(char *out,size_t size);
bool cfg_freedv_usb_select_headset(const char *id);
int cfg_freedv_usb_gain_for(const char *id,const char *name);
bool cfg_freedv_usb_save_gain(const char *id,int gain);
typedef struct {
    bool present,legacy_import;
    unsigned percent;
    bool muted;
} cfg_freedv_usb_volume;
/* GUI entry calls this before selecting a device. The old selected identity
 * is retained separately so a newly selected headset cannot inherit its level. */
bool cfg_freedv_usb_capture_legacy_volume(void);
bool cfg_freedv_usb_volume_target(const char *id,cfg_freedv_usb_volume *);
bool cfg_freedv_usb_confirm_legacy_volume(const char *id,unsigned percent,bool muted);
bool cfg_freedv_usb_save_volume(const char *id,unsigned percent,bool muted);
/* Observe only actual readback under the immutable physical headset identity.
 * settled means initialized, no request pending, confirmed revision equals the
 * latest request and that revision did not fail. Writes debounce for 500 ms. */
bool cfg_freedv_usb_volume_observe(const char *id,uint64_t revision,bool settled,
    unsigned percent,bool muted,uint64_t monotonic_ms);
bool cfg_freedv_usb_volume_flush(void); /* last still-settled value, e.g. exit */
bool cfg_freedv_usb_volume_saved(void);
bool cfg_freedv_usb_preview_enabled(void);
bool cfg_freedv_usb_set_preview_enabled(bool enabled);
#ifdef __cplusplus
}
#endif
