// SPDX-License-Identifier: LGPL-2.1-or-later
#pragma once
#include <stdbool.h>
#include <stddef.h>
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
#ifdef __cplusplus
}
#endif
