// SPDX-License-Identifier: LGPL-2.1-or-later
#pragma once
#include "../freedv/voice_types.h"
#ifdef __cplusplus
extern "C" {
#endif
bool cfg_voice_resolve(fd_local_operation, int32_t value, fd_voice_tuning *);
bool cfg_voice_apply(fd_voice_tuning *); /* owned GUI transaction only */
#ifdef __cplusplus
}
#endif
