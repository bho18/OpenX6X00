// SPDX-License-Identifier: LGPL-2.1-or-later
#pragma once
#include "../hw/hw.h"

/* A locked read of the backend's cached commands, not a complete session
 * snapshot. The future session also needs ALSA/audio state, radio-owned
 * overrides, exclusive ownership and a separately observed packet TX state. */
bool radio_get_freedv_cached_snapshot(hw_freedv_cached_snapshot_t *snapshot);
