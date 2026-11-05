// SPDX-License-Identifier: LGPL-2.1-or-later
#pragma once
#include <stdbool.h>
#include <stdint.h>

/* Host-side capability contract, not new baseband command/register bits.
 * Unknown is different from a known unsupported interface. A cached command
 * or a successful I2C write is never evidence of an electrical audio route. */
enum {
    HW_NATIVE_VOICE_ABI = 1,
    HW_NATIVE_RX_PAIR = 1u << 0,
    HW_NATIVE_TX_PAIR = 1u << 1,
    HW_NATIVE_MIC_ISOLATION = 1u << 2,
    HW_NATIVE_RAW_RX_ISOLATION = 1u << 3,
    HW_NATIVE_HAND_PTT = 1u << 4,
    HW_NATIVE_KEYING_OWNERSHIP = 1u << 5,
    HW_NATIVE_ALL = (1u << 6) - 1
};
typedef struct {
    unsigned abi_version;
    uint32_t known, supported;
    bool simulation;
    const char *evidence;
} hw_native_voice_capabilities_t;

/* Keying ownership means physical PTT is observable independently while the
 * native mode can inhibit direct keying and clear its RF on a fault. It must
 * not be inferred from a software-PTT setter or aggregate TX observation.
 * Independent hand PTT includes a retained release sequence so a release and
 * re-press between level polls cannot revive an earlier pending turn. */
static inline bool hw_native_voice_has(hw_native_voice_capabilities_t c, uint32_t mask) {
    return c.abi_version == HW_NATIVE_VOICE_ABI && !(c.known & ~HW_NATIVE_ALL) &&
        !(c.supported & ~c.known) && (c.supported & mask) == mask;
}
hw_native_voice_capabilities_t hw_native_voice_capabilities(void);
