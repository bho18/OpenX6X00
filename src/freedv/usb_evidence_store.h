#ifndef FD_USB_EVIDENCE_STORE_H
#define FD_USB_EVIDENCE_STORE_H

#define FD_USB_EVIDENCE_SLOTS 16u
#define FD_USB_EVIDENCE_MAX_RECORD 16384u
#define FD_USB_EVIDENCE_MAX_SESSION 96u

/* Persistent incident metadata only; never PCM. Call outside audio/control
 * threads. Returns 0 or a negative errno. Records are immutable: a duplicate
 * kind for the same session returns -EEXIST, without replacing its bytes.
 *
 * Start with kind="metadata"; later kinds are "fault" and "completion".
 * Starting a new session marks the previous uncompleted session "aborted".
 * A rotating ring retains the last 16 committed sessions; a late write cannot
 * join another session's records. The current-session.json pointer identifies the latest
 * committed metadata. Every record carries its authoritative session identity.
 *
 * JSON must be one object. If it supplies session_id, that identity must match.
 * Atomic replacement and fsync provide normal filesystem durability, not a
 * guarantee against arbitrary power loss or broken storage hardware. */
int fd_usb_evidence_store(const char *directory, const char *session_id,
                          const char *kind, const char *json);

#endif
