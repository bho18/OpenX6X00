/*
 *  SPDX-License-Identifier: LGPL-2.1-or-later
 *
 *  OpenX6X00 - Who controls the radio (control_owner.h)
 *
 *  Copyright (c) 2026 Joeri Van Dooren ON6URE / Guru-RF
 */

#include "control_owner.h"
#include "radio.h"
#include "freedv/writer_gate.h"

#include <pthread.h>
#include <stdatomic.h>
#include <stdio.h>
#include <string.h>
#include <time.h>

#define INJECT_MS 150   /* an input event this soon after an injection is the injection, not the operator */

static pthread_mutex_t mux = PTHREAD_MUTEX_INITIALIZER;
static ctl_info_t      owner;
#ifdef ENABLE_FREEDV_DIAGNOSTICS
static ctl_info_t      before_diagnostic;
#endif
static atomic_uint     version;
static atomic_uint_fast64_t injected_ms;

static uint64_t now_ms(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return ts.tv_sec * 1000ULL + ts.tv_nsec / 1000000;
}

void ctl_init(void) {
    memset(&owner, 0, sizeof(owner));
}

bool ctl_transmitting(void) {
    return radio_get_state() != RADIO_RX;
}

static void set_owner(ctl_kind_t kind, int id, const char *name) {
    owner.kind = kind;
    owner.id   = id;
    snprintf(owner.name, sizeof(owner.name), "%s", name ? name : "");
    owner.since_ms = now_ms();
    atomic_fetch_add(&version, 1);
}

bool ctl_claim(ctl_kind_t kind, int id, const char *name, bool take) {
    bool ok;

    pthread_mutex_lock(&mux);
#ifdef ENABLE_FREEDV_DIAGNOSTICS
    if (kind == CTL_DIAGNOSTIC || owner.kind == CTL_DIAGNOSTIC) {
        pthread_mutex_unlock(&mux);
        return false;
    }
#endif
    if (owner.kind == kind && owner.id == id) {
        ok = true;
    } else if (owner.kind == CTL_NONE || (take && !ctl_transmitting())) {
        set_owner(kind, id, name);
        ok = true;
    } else {
        ok = false;
    }
    pthread_mutex_unlock(&mux);
    return ok;
}

void ctl_release(ctl_kind_t kind, int id) {
#ifdef ENABLE_FREEDV_DIAGNOSTICS
    if (kind == CTL_DIAGNOSTIC) return; /* only the diagnostic owner releases its lease */
#endif
    pthread_mutex_lock(&mux);
    if (owner.kind == kind && owner.id == id)
        set_owner(CTL_NONE, 0, "");
    pthread_mutex_unlock(&mux);
}

bool ctl_holds(ctl_kind_t kind, int id) {
    pthread_mutex_lock(&mux);
    bool h = owner.kind == kind && owner.id == id;
    pthread_mutex_unlock(&mux);
    return h;
}

void ctl_get(ctl_info_t *out) {
    pthread_mutex_lock(&mux);
    *out = owner;
    pthread_mutex_unlock(&mux);
}

uint32_t ctl_version(void) {
    return atomic_load(&version);
}

void ctl_injected(void) {
    atomic_store(&injected_ms, now_ms());
}

void ctl_local_input(void) {
    /* The diagnostic barrier already drained every admitted remote event and
     * blocks all new ones. Fresh front input no longer needs the approximate
     * legacy time heuristic to reclaim local ownership. */
    if (
#ifdef ENABLE_FREEDV_DIAGNOSTICS
        !ctl_diagnostic_input_blocked() &&
#endif
        now_ms() - atomic_load(&injected_ms) < INJECT_MS)
        return;
    pthread_mutex_lock(&mux);
#ifdef ENABLE_FREEDV_DIAGNOSTICS
    if (owner.kind == CTL_DIAGNOSTIC) {
        pthread_mutex_unlock(&mux);
        return;
    }
#endif
    if (owner.kind != CTL_LOCAL)
        set_owner(CTL_LOCAL, 0, "");
    pthread_mutex_unlock(&mux);
}

#ifdef ENABLE_FREEDV_DIAGNOSTICS
bool ctl_diagnostic_acquire(void) {
    pthread_mutex_lock(&mux);
    bool ok = (owner.kind == CTL_NONE || owner.kind == CTL_LOCAL) && !ctl_transmitting();
    if (ok) ok = fd_writer_reserve();
    if (ok) {
        before_diagnostic = owner;
        set_owner(CTL_DIAGNOSTIC, 0, "FreeDV diagnostics");
    }
    pthread_mutex_unlock(&mux);
    return ok;
}

bool ctl_diagnostic_release(bool restoration_complete_and_rx_confirmed) {
    pthread_mutex_lock(&mux);
    bool ok = owner.kind == CTL_DIAGNOSTIC && restoration_complete_and_rx_confirmed && !ctl_transmitting();
    if (ok) {
        set_owner(before_diagnostic.kind, before_diagnostic.id, before_diagnostic.name);
        fd_writer_release();
    }
    pthread_mutex_unlock(&mux);
    return ok;
}
#endif

#ifdef ENABLE_FREEDV_DIAGNOSTICS
#include <limits.h>
static pthread_mutex_t remote_input_mux = PTHREAD_MUTEX_INITIALIZER;
static atomic_bool diagnostic_input_blocked;
static atomic_uint remote_input_generation;
bool ctl_remote_input_begin(unsigned *generation) {
    pthread_mutex_lock(&remote_input_mux);
    if (atomic_load_explicit(&diagnostic_input_blocked, memory_order_acquire)) {
        pthread_mutex_unlock(&remote_input_mux);
        return false;
    }
    if (generation) *generation = atomic_load_explicit(&remote_input_generation, memory_order_relaxed);
    return true;
}
void ctl_remote_input_end(void) { pthread_mutex_unlock(&remote_input_mux); }
unsigned ctl_remote_input_generation(void) {
    return atomic_load_explicit(&remote_input_generation, memory_order_acquire);
}
bool ctl_diagnostic_input_blocked(void) {
    return atomic_load_explicit(&diagnostic_input_blocked, memory_order_acquire);
}
bool ctl_diagnostic_input_block(void) {
    pthread_mutex_lock(&remote_input_mux);
    unsigned generation = atomic_load_explicit(&remote_input_generation, memory_order_relaxed);
    bool ok = !atomic_load_explicit(&diagnostic_input_blocked, memory_order_relaxed) && generation != UINT_MAX;
    if (ok) {
        atomic_store_explicit(&remote_input_generation, generation + 1, memory_order_release);
        atomic_store_explicit(&diagnostic_input_blocked, true, memory_order_release);
    }
    pthread_mutex_unlock(&remote_input_mux);
    return ok;
}
void ctl_diagnostic_input_unblock(void) {
    pthread_mutex_lock(&remote_input_mux);
    if (atomic_load_explicit(&diagnostic_input_blocked, memory_order_relaxed)) {
        unsigned generation = atomic_load_explicit(&remote_input_generation, memory_order_relaxed);
        if (generation != UINT_MAX) atomic_store_explicit(&remote_input_generation, generation + 1, memory_order_release);
        atomic_store_explicit(&diagnostic_input_blocked, false, memory_order_release);
    }
    pthread_mutex_unlock(&remote_input_mux);
}
#endif
