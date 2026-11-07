// SPDX-License-Identifier: LGPL-2.1-or-later
#pragma once
#include <stdbool.h>
#include <stdint.h>
#ifdef __cplusplus
extern "C" {
#endif
/* The normal screen stays alive; these describe only the temporary FreeDV
 * controls and entry/exit transaction, not an app_window dialog. */
#if defined(ENABLE_FREEDV_USB_VOICE) || defined(ENABLE_FREEDV_USB_TEST)
bool freedv_usb_active(void);
bool freedv_usb_entry_pending(void);
bool freedv_usb_popup_active(void);
bool freedv_usb_popup_key(uint32_t key);
bool freedv_usb_volume(int delta);
bool freedv_usb_volume_percent(unsigned *percent,bool *muted);
bool freedv_usb_finishing(void);
bool freedv_usb_softkey(unsigned index,bool hold);
void freedv_usb_terminal_leave(void);
#else
static inline bool freedv_usb_entry_pending(void){return false;}
static inline bool freedv_usb_popup_active(void){return false;}
static inline bool freedv_usb_popup_key(uint32_t key){(void)key;return false;}
static inline bool freedv_usb_volume(int delta){(void)delta;return false;}
static inline bool freedv_usb_volume_percent(unsigned *percent,bool *muted){(void)percent;(void)muted;return false;}
static inline bool freedv_usb_finishing(void){return false;}
static inline bool freedv_usb_softkey(unsigned index,bool hold){(void)index;(void)hold;return false;}
static inline void freedv_usb_terminal_leave(void){}
#endif
#ifdef __cplusplus
}
#endif
