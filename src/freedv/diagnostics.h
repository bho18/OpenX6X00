// SPDX-License-Identifier: LGPL-2.1-or-later
#pragma once
#ifdef __cplusplus
extern "C" {
#endif
/* LVGL thread only. Opens a small control panel over the normal waterfall.
 * Arm/Run/confirm require fenced, fresh local front-panel input. */
void freedv_diagnostics_open(void);
#ifdef __cplusplus
}
#endif
