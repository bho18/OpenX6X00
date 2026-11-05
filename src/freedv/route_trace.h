// SPDX-License-Identifier: LGPL-2.1-or-later
#pragma once
#include <stdint.h>
#include <stdbool.h>
#include <stdio.h>
void fd_route_trace_begin(uint64_t generation);
void fd_route_trace_note(const char *phase, int command, uint32_t previous, uint32_t requested, bool success);
void fd_route_trace_export(FILE *out);
