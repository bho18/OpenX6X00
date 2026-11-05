// SPDX-License-Identifier: LGPL-2.1-or-later
#include "../../src/hw/native_voice.h"
#include <assert.h>
int main(void) {
    hw_native_voice_capabilities_t c={.abi_version=HW_NATIVE_VOICE_ABI};
    assert(!hw_native_voice_has(c,HW_NATIVE_KEYING_OWNERSHIP));
    c.known=HW_NATIVE_HAND_PTT;
    assert(!hw_native_voice_has(c,HW_NATIVE_HAND_PTT));
    c.supported=HW_NATIVE_KEYING_OWNERSHIP; /* unsupported claims cannot grant */
    assert(!hw_native_voice_has(c,HW_NATIVE_KEYING_OWNERSHIP));
    c.known=c.supported=HW_NATIVE_ALL;
    assert(hw_native_voice_has(c,HW_NATIVE_ALL));
    c.abi_version++;
    assert(!hw_native_voice_has(c,HW_NATIVE_ALL));
    c.abi_version=HW_NATIVE_VOICE_ABI;c.known|=1u<<20;
    assert(!hw_native_voice_has(c,HW_NATIVE_ALL));
    return 0;
}
