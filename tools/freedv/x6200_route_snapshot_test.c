// SPDX-License-Identifier: LGPL-2.1-or-later
#include "src/hw/hw.h"
#include <aether_radio/x6200_control/low/control.h>
#include <assert.h>
#include <stdio.h>

static uint32_t register_value;
static bool fake_control_cmd(x6200_cmd_enum_t command, uint32_t value) {
    (void)command;
    (void)value;
    return true;
}
/* The backend's constructor resolves the real library through dlsym. The
 * test never writes a radio: provide the constructor a harmless stand-in. */
void *dlsym(void *handle, const char *symbol) {
    (void)handle;
    (void)symbol;
    return (void *)fake_control_cmd;
}
uint32_t x6200_control_get(x6200_cmd_enum_t command) {
    assert(command == x6200_sple_atue_trx);
    return register_value;
}

int main(void) {
    hw_commanded_audio_route_t route;
    assert(!hw_get_commanded_audio_route(NULL));
    for (unsigned i = 0; i < 8; ++i) {
        register_value = (i & 1 ? x6200_voice_rec : 0) |
                         (i & 2 ? x6200_modem_trx : 0) |
                         (i & 4 ? x6200_iptt : 0) | x6200_atu_tune;
        assert(hw_get_commanded_audio_route(&route));
        assert(route.record == !!(i & 1));
        assert(route.modem == !!(i & 2));
        assert(route.ptt == !!(i & 4));
    }
    puts("PASS: X6200 cached route bits for all eight combinations");
}
