// SPDX-License-Identifier: LGPL-2.1-or-later
// Execute against the exact SDK FT8 shared library before it enters an image.
#include <message.h>
#include <stdio.h>
#include <string.h>

int main(void) {
    const char *calls[] = {"PA/ON6URE", "OT4ABCD", "VK2ABC/P"};
    for (unsigned i = 0; i < sizeof(calls) / sizeof(calls[0]); ++i) {
        char input[FTX_MAX_MESSAGE_LENGTH], decoded[FTX_MAX_MESSAGE_LENGTH];
        ftx_message_t message;
        snprintf(input, sizeof(input), "CQ %s JO20", calls[i]);
        ftx_message_init(&message);
        if (ftx_message_encode(&message, NULL, input) != FTX_MESSAGE_RC_OK ||
            ftx_message_decode(&message, NULL, decoded) != FTX_MESSAGE_RC_OK ||
            !strstr(decoded, calls[i]) || strchr(decoded, '<')) {
            fprintf(stderr, "FAIL: CQ must carry its own plain callsign: %s\n", input);
            return 1;
        }
        printf("PASS: %s -> %s\n", input, decoded);
    }
    puts("PASS: exact SDK FT8 compound-callsign library; emulated correctness only");
    return 0;
}
