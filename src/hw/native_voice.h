// SPDX-License-Identifier: LGPL-2.1-or-later
#pragma once
#include <stdbool.h>
#include <stdint.h>

/* Host-side capability contract, not new baseband command/register bits.
 * Unknown is different from a known unsupported interface. A cached command
 * or a successful I2C write is never evidence of an electrical audio route. */
enum {
    HW_NATIVE_VOICE_ABI = 2,
    HW_NATIVE_RX_PAIR = 1u << 0,
    HW_NATIVE_TX_PAIR = 1u << 1,
    HW_NATIVE_MIC_ISOLATION = 1u << 2,
    HW_NATIVE_RAW_RX_ISOLATION = 1u << 3,
    HW_NATIVE_HAND_PTT = 1u << 4,
    HW_NATIVE_KEYING_OWNERSHIP = 1u << 5,
    HW_NATIVE_APPLIED_ROUTE_ACK = 1u << 6,
    HW_NATIVE_FAIL_CLOSED_LEASE = 1u << 7,
    HW_NATIVE_CONTROL = HW_NATIVE_HAND_PTT | HW_NATIVE_KEYING_OWNERSHIP |
                        HW_NATIVE_APPLIED_ROUTE_ACK | HW_NATIVE_FAIL_CLOSED_LEASE,
    HW_NATIVE_ALL = (1u << 8) - 1,
    HW_NATIVE_ACK_MAX_AGE_MS = 250,
    HW_NATIVE_LEASE_MAX_MS = 1000
};
typedef struct {
    unsigned abi_version;
    uint32_t known, supported;
    bool simulation;
    const char *evidence;
    /* Reviewed MCU/protocol binding, not a display version or cached command. */
    const char *contract_id;
} hw_native_voice_capabilities_t;

typedef enum {
    HW_NATIVE_ROUTE_NONE, HW_NATIVE_ROUTE_QUIET, HW_NATIVE_ROUTE_RX, HW_NATIVE_ROUTE_TX
} hw_native_voice_route_t;
typedef struct {
    bool valid, physical_keying_inhibited, autonomous_tx_inhibited;
    bool direct_mic_rf_inhibited, raw_rx_speaker_inhibited;
    uint64_t boot_id, sequence, monotonic_ms;
    uint64_t lease_token, lease_deadline_ms, route_generation;
    hw_native_voice_route_t applied_route;
    const char *contract_id;
} hw_native_voice_state_t;

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

/* Abstract host contracts only: these are NOT invented X6200 wire commands.
 * Unsupported backends return false without hardware writes. Supported
 * backends must bind them to a reviewed versioned MCU protocol. Request
 * success means accepted for transport, never an applied-route acknowledgement.
 * observe supplies a coherent MCU-applied snapshot, with the MCU boot identity
 * and a fresh observation sequence; a command cache/I2C ACK cannot supply it.
 * Timestamps/deadlines must be conservatively mapped to host monotonic time.
 * A lease expires in the MCU within HW_NATIVE_LEASE_MAX_MS without coordinator
 * renewals, inhibiting RF even if Linux stalls and physical PTT remains held.
 * Acquiring a lease enters QUIET at route_generation 0. QUIET disconnects direct
 * mic-to-RF and raw-RX-to-speaker bypasses without treating PGA gain 0 as mute.
 * apply preserves that isolation while selecting the contract's explicit RX/TX
 * pair. Autonomous TX inhibition covers physical inputs, stale software PTT /
 * MODEM/CIV sources, VOX, tune/calibration and keyer/CW hang; only a current
 * leased TX request may key. Protection always overrides it. Other I2C traffic
 * must neither renew the lease nor revive expired sources. Release is allowed
 * only from acknowledged QUIET, and the MCU must atomically reject release if
 * any raw physical control is held (a Linux precheck alone has a race).
 * apply must reject non-increasing generations, so a newer QUIET request
 * prevents an older in-flight TX request from reviving transmission.
 * The state acknowledgements describe applied configuration, not physical RF
 * or electrical-isolation measurements. Those still require qualification.
 * renew and request functions are coordinator/owner operations; observe may be
 * called concurrently and must neither renew a lease nor restamp cached data. */
bool hw_native_voice_acquire(uint64_t lease_token);
bool hw_native_voice_apply(uint64_t lease_token, uint64_t generation, hw_native_voice_route_t route);
bool hw_native_voice_renew(uint64_t lease_token);
bool hw_native_voice_observe(hw_native_voice_state_t *);
bool hw_native_voice_release(uint64_t lease_token);
