#!/usr/bin/env python3
"""Assess a supplied physical-conversation evidence bundle, never a release.

This checks report consistency and artifact integrity, not the truth of human
listening observations or RF measurements. It cannot create a qualified profile.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path
import re


SCHEMA = "x6200.native-conversation.v2"
IDENTITIES = {
    "source_commit": 40, "gui_sha256": 64, "baseband": None,
    "control_commit": 40, "buildroot_commit": 40,
    "rade_model_sha256": 64, "route_contract_id": None,
}
RETAINED = ("session_id", "engine_id", "capture_stream_id", "playback_stream_id")
ROUTES = ("tx_mic_survives", "analog_mic_isolated", "rx_capture_survives_speaker",
          "raw_modem_suppressed", "feedback_absent")
ENDPOINTS = {"rx_capture": "rf_demodulated_audio", "rx_playback": "radio_speaker",
             "tx_capture": "physical_hand_microphone", "tx_playback": "rf_modulator"}
INHIBIT_SCOPE = ("software_tx", "physical_ptt", "civ", "vox", "tune_calibration", "cw_tail")
TIMES = ("press_ms", "admit_ms", "rf_on_ms", "release_ms",
         "last_mic_sample_ms", "rf_off_ms", "rx_ready_ms")
BUDGETS = ("press_to_rf_ms", "release_to_rf_off_ms", "release_to_rx_ms",
           "reacquisition_ms", "ptt_max_age_ms", "rss_growth_bytes")


class Checks:
    def __init__(self, report, root):
        self.report = report
        self.root = Path(root).resolve()
        self.findings = []
        self.artifacts = {}
        self.native_traces = {}

    def issue(self, status, field, detail):
        self.findings.append({"status": status, "field": field, "detail": detail})

    def get(self, path):
        value = self.report
        for part in path.split("."):
            value = value.get(part) if isinstance(value, dict) else None
        return value

    def known(self, path):
        value = self.get(path)
        if value is None or value == "unknown":
            self.issue("BLOCKED", path, "Evidence is missing or unknown.")
            return None
        return value

    def expect(self, path, expected):
        value = self.known(path)
        if value is not None and (type(value) is not type(expected) or value != expected):
            self.issue("FAIL", path, "Expected " + repr(expected) + ".")

    def text(self, path):
        value = self.known(path)
        if value is not None and (not isinstance(value, str) or not value.strip()):
            self.issue("FAIL", path, "A nonempty string is required.")
            return None
        return value

    def number(self, path, minimum=0, integer=False):
        value = self.known(path)
        if value is None:
            return None
        if (type(value) not in (int, float) or value > (1 << 64) - 1
                or value < minimum or not math.isfinite(value) or (integer and type(value) is not int)):
            self.issue("FAIL", path, "A finite " + ("integer" if integer else "number")
                       + " >= " + str(minimum) + " is required.")
            return None
        return value

    def limit(self, field, value, maximum):
        if value is not None and maximum is not None and value > maximum:
            self.issue("FAIL", field, f"Measured {value} exceeds limit {maximum}.")

    def order(self, field, values):
        if all(v is not None for v in values) and values != sorted(values):
            self.issue("FAIL", field, "Event order is inconsistent.")

    def artifact(self, path, kind):
        name = self.text(path)
        if name is not None:
            if name not in self.artifacts:
                self.issue("BLOCKED", path, "No verified artifact with this ID.")
            elif self.artifacts[name] != kind:
                self.issue("FAIL", path, "Artifact must have kind " + kind + ".")

    def verify_artifacts(self):
        artifacts = self.known("artifacts")
        if not isinstance(artifacts, dict) or not artifacts:
            self.issue("BLOCKED", "artifacts", "A nonempty artifact inventory is required.")
            return
        for name, item in artifacts.items():
            field = "artifacts." + name
            if not isinstance(item, dict) or "." in name:
                self.issue("FAIL", field, "Invalid artifact entry or ID.")
                continue
            relative, digest, kind = (self.text(field + "." + key)
                                      for key in ("path", "sha256", "kind"))
            if None in (relative, digest, kind):
                continue
            if not re.fullmatch(r"[0-9a-f]{64}", digest):
                self.issue("FAIL", field, "Invalid SHA256.")
                continue
            target = (self.root / relative).resolve()
            if Path(relative).is_absolute() or not target.is_relative_to(self.root):
                self.issue("FAIL", field, "Artifacts must remain inside the bundle.")
                continue
            try:
                actual = hashlib.sha256()
                size = 0
                with target.open("rb") as source:
                    for chunk in iter(lambda: source.read(1024 * 1024), b""):
                        size += len(chunk)
                        actual.update(chunk)
                if not size:
                    self.issue("BLOCKED", field, "Artifact is empty.")
                elif actual.hexdigest() != digest:
                    self.issue("FAIL", field, "Artifact checksum mismatch.")
                else:
                    self.artifacts[name] = kind
                    if kind == "native_trace":
                        self.verify_native_trace(name, target)
            except OSError:
                self.issue("BLOCKED", field, "Artifact cannot be read.")

    def verify_native_trace(self, name, target):
        field = "artifacts." + name
        try:
            with target.open(encoding="utf-8") as source:
                rows = [json.loads(line) for line in source if line.strip()]
            if len(rows) < 2 or any(not isinstance(row, dict) for row in rows):
                raise ValueError("missing header/footer")
            header, footer = rows[0], rows[-1]
            if header.get("schema") != 1 or header.get("kind") != "native_session":
                raise ValueError("unsupported native trace header")
            if header.get("simulation") is not False:
                self.issue("BLOCKED", field, "Trace is simulated or its physical provenance is unknown.")
            if (footer.get("kind") != "trace_end" or footer.get("complete") is not True
                    or type(footer.get("discarded")) is not int or footer["discarded"] != 0
                    or type(footer.get("rows")) is not int or footer["rows"] != len(rows) - 2):
                self.issue("BLOCKED", field, "Native trace is incomplete or has discarded rows.")
            previous = -1
            for row in rows[1:-1]:
                stamp = row.get("monotonic_ns")
                if type(stamp) is not int or not 0 <= stamp <= (1 << 64) - 1 or stamp < previous:
                    raise ValueError("invalid native trace timeline")
                previous = stamp
            self.native_traces[name] = rows
        except (OSError, ValueError, UnicodeError) as error:
            self.issue("BLOCKED", field, "Native JSONL trace cannot be verified: " + str(error))

    def speech(self, prefix, kind):
        expected = self.text(prefix + ".expected_phrase")
        heard = self.text(prefix + ".heard_phrase")
        self.text(prefix + ".observer")
        self.expect(prefix + ".intelligible", True)
        self.artifact(prefix + ".evidence", kind)
        words = lambda text: re.findall(r"\w+", text.casefold()) if text else []
        expected_words, heard_words = words(expected), words(heard)
        if expected is not None and heard is not None:
            if (not expected_words or not heard_words
                    or expected_words[0] != heard_words[0]
                    or expected_words[-1] != heard_words[-1]):
                self.issue("FAIL", prefix, "Expected first and last words were not heard.")
        return tuple(expected_words)


def assess(report, bundle_root):
    """Return PASS/FAIL/BLOCKED for this conversation report only."""
    c = Checks(report, bundle_root)
    c.expect("schema", SCHEMA)
    domain = c.known("evidence_domain")
    if domain is not None and domain != "physical_radio":
        c.issue("BLOCKED", "evidence_domain", "Simulation, QEMU and file processing cannot qualify physical operation.")
    c.expect("mode", "RADE_V1")
    c.expect("operating_path", "native_voice_session")
    c.verify_artifacts()
    c.artifact("identities.evidence", "native_trace")
    for key, length in IDENTITIES.items():
        expected = c.text("identities.expected." + key)
        observed = c.text("identities.observed." + key)
        for label, value in (("expected", expected), ("observed", observed)):
            if value and length and not re.fullmatch("[0-9a-f]{" + str(length) + "}", value):
                c.issue("FAIL", "identities." + label + "." + key, "Invalid pinned identity.")
        if expected is not None and observed is not None and expected != observed:
            c.issue("FAIL", "identities." + key, "Expected and observed identities differ.")

    budgets = {key: c.number("budgets." + key, 0 if key == "rss_growth_bytes" else 0.001)
               for key in BUDGETS}
    c.expect("budgets.fixed_before_run", True)
    c.artifact("budgets.evidence", "test_plan")
    c.limit("budgets.release_to_rf_off_ms", budgets["release_to_rf_off_ms"], 1500)
    c.artifact("session.evidence", "native_trace")
    retained = {key: c.text("session." + key) for key in RETAINED}
    trace_id = c.get("session.evidence")
    native_rows = c.native_traces.get(trace_id, []) if isinstance(trace_id, str) else []
    if native_rows:
        if retained["session_id"] is not None and native_rows[0].get("session_id") != retained["session_id"]:
            c.issue("FAIL", "session.session_id", "Report differs from the native trace session.")
        observed_baseband = c.get("identities.observed.baseband")
        if observed_baseband not in (None, "unknown") and native_rows[0].get("baseband") != observed_baseband:
            c.issue("FAIL", "identities.observed.baseband", "Report differs from the native trace baseband.")
        contract = c.get("identities.observed.route_contract_id")
        if contract not in (None, "unknown") and native_rows[0].get("native_contract_id") != contract:
            c.issue("FAIL", "identities.observed.route_contract_id", "Report differs from the native trace contract.")
    for key in ("engine_create_count", "capture_open_count", "playback_open_count"):
        c.expect("session." + key, 1)
    c.expect("session.wav_taps_enabled", False)
    c.expect("session.restoration_confirmed", True)
    c.expect("session.local_settings_preserved", True)
    start, end = (c.number("session." + key) for key in ("start_ms", "end_ms"))
    if start is not None and end is not None and end - start < 300000:
        c.issue("FAIL", "session", "At least five measured minutes are required.")

    # A command/cache echo is not an effective route acknowledgement; neither
    # proves the physical endpoint. Keep all three evidence layers separate.
    applied_boots = set()
    for route in ROUTES:
        prefix = "routes." + route
        requested = {key: c.number(prefix + ".requested." + key, 1, integer=True)
                     for key in ("lease_token", "generation", "route")}
        applied = {key: c.number(prefix + ".applied." + key, 1, integer=True)
                   for key in ("lease_token", "generation", "route", "boot_id", "sequence")}
        c.expect(prefix + ".requested.route", 3 if route in ("tx_mic_survives", "analog_mic_isolated") else 2)
        for key in requested:
            if requested[key] is not None and applied[key] is not None and requested[key] != applied[key]:
                c.issue("FAIL", prefix + ".applied." + key, "Acknowledgement does not match this route request.")
        if applied["boot_id"] is not None:
            applied_boots.add(applied["boot_id"])
        matches = False
        for row in native_rows:
            request, state = row.get("requested"), row.get("applied_state")
            if row.get("kind") != "native_control" or not isinstance(request, dict) or not isinstance(state, dict):
                continue
            if (not all(type(request.get(key)) is int and request[key] == value and value is not None
                        for key, value in requested.items())
                    or not all(type(state.get(key)) is int and state[key] == value and value is not None
                               for key, value in applied.items())):
                continue
            if (state.get("valid") is not True or state.get("contract_id") != c.get("identities.observed.route_contract_id")
                    or any(state.get(key) is not True for key in ("physical_keying_inhibited", "autonomous_tx_inhibited",
                                                                 "direct_mic_rf_inhibited", "raw_rx_speaker_inhibited"))):
                continue
            stamp, deadline = state.get("stamp_ms"), state.get("lease_deadline_ms")
            observed_ms = row.get("monotonic_ns", 0) / 1000000
            if (type(stamp) not in (int, float) or type(deadline) not in (int, float)
                    or not 0 <= stamp <= (1 << 64) - 1 or not 0 <= deadline <= (1 << 64) - 1
                    or not 0 <= observed_ms - stamp <= 250 or not observed_ms < deadline <= observed_ms + 1000):
                continue
            if stamp != c.get(prefix + ".applied.at_ms"):
                continue
            matches = True
        if not matches:
            c.issue("BLOCKED", prefix + ".applied", "Native trace lacks a matching fresh, leased effective-state acknowledgement.")
        c.expect(prefix + ".applied.source", "effective_hardware_ack")
        c.artifact(prefix + ".requested.evidence", "control_command")
        c.artifact(prefix + ".applied.evidence", "effective_state")
        c.order(prefix + ".timeline", [c.number(prefix + ".requested.at_ms"),
                                       c.number(prefix + ".applied.at_ms"),
                                       c.number(prefix + ".observed_at_ms")])
        c.expect(prefix + ".observed", True)
        c.expect(prefix + ".observation_basis", "physical_signal_measurement")
        c.text(prefix + ".method")
        c.artifact(prefix + ".evidence", "route_observation")
    if len(applied_boots) > 1:
        c.issue("FAIL", "routes", "MCU boot identity changed within the retained session.")
    for endpoint, destination in ENDPOINTS.items():
        prefix = "endpoints." + endpoint
        c.text(prefix + ".host_device")
        c.number(prefix + ".host_channel", integer=True)
        c.text(prefix + ".board_net")
        c.expect(prefix + ".physical_endpoint", destination)
        c.artifact(prefix + ".evidence", "board_mapping")

    c.expect("hardware_contract.ptt.source", "raw_physical_edges")
    c.expect("hardware_contract.ptt.retained_release_counter", True)
    c.number("hardware_contract.ptt.minimum_retained_release_ms", 0.001)
    for control in ("panel", "hand"):
        c.text("hardware_contract.ptt.source_map." + control)
    c.artifact("hardware_contract.ptt.evidence", "board_mapping")
    c.expect("hardware_contract.lease.controller", "independent_controller")
    c.expect("hardware_contract.lease.expiry_clears_pending_requests", True)
    c.expect("hardware_contract.lease.inhibit_ack_source", "effective_hardware_ack")
    scope = c.known("hardware_contract.lease.inhibit_scope")
    if scope is not None and (not isinstance(scope, list)
                             or not all(item in scope for item in INHIBIT_SCOPE)):
        c.issue("FAIL", "hardware_contract.lease.inhibit_scope", "Native inhibit omits a documented software, physical or autonomous TX source.")
    c.artifact("hardware_contract.lease.evidence", "hardware_contract")
    c.expect("hardware_contract.shutdown.source", "independent_rf")
    c.expect("hardware_contract.shutdown.trigger", "native_lease_expiry")
    c.expect("hardware_contract.shutdown.initial_rf_present", True)
    c.expect("hardware_contract.shutdown.physical_ptt_held", True)
    c.expect("hardware_contract.shutdown.ordinary_i2c_traffic_continued", True)
    shutdown = c.number("hardware_contract.shutdown.max_rf_off_ms")
    shutdown_uncertainty = c.number("hardware_contract.shutdown.uncertainty_ms")
    shutdown_budget = c.number("hardware_contract.shutdown.budget_ms", 0.001)
    if shutdown is not None and shutdown_uncertainty is not None:
        c.limit("hardware_contract.shutdown.max_rf_off_ms", shutdown + shutdown_uncertainty, shutdown_budget)
    c.artifact("hardware_contract.shutdown.evidence", "rf_capture")
    c.expect("rf_observation.source", "independent_rf")
    c.artifact("rf_observation.evidence", "rf_capture")
    resolution = c.number("rf_observation.resolution_ms", 0.001)
    alignment = c.number("rf_observation.clock_alignment_max_error_ms")
    c.text("rf_observation.clock_alignment_method")
    rf_uncertainty = 2 * resolution + alignment if resolution is not None and alignment is not None else None
    c.text("reference_decoder.version")
    c.expect("reference_decoder.application", "official_FreeDV")
    c.text("reference_decoder.track")

    c.expect("rx.source", "live_rf")
    c.expect("rx.output", "radio_speaker")
    decoded_ms = c.number("rx.live_decoded_ms", 120000)
    c.artifact("rx.evidence", "native_trace")
    c.speech("rx.speech", "speaker_observation")
    recovery = [c.number("rx.reacquisition." + key)
                for key in ("lost_ms", "restored_ms", "decoded_ms")]
    c.order("rx.reacquisition", recovery)
    if None not in recovery:
        if recovery[0] == recovery[1]:
            c.issue("FAIL", "rx.reacquisition", "A nonzero interruption is required.")
        c.limit("rx.reacquisition", recovery[2] - recovery[1], budgets["reacquisition_ms"])
        c.order("rx.reacquisition.session", [start, *recovery, end])
    c.speech("rx.reacquisition.speech", "speaker_observation")

    for queue in ("capture", "engine_input", "modem_output"):
        prefix = "queues." + queue
        capacity = c.number(prefix + ".capacity_samples", 1, integer=True)
        peak = c.number(prefix + ".high_water_samples", integer=True)
        c.expect(prefix + ".measurement", "event_high_water")
        c.limit(prefix, peak, capacity)
    c.artifact("queues.evidence", "native_trace")
    rss = [c.number("resources." + key, 1, integer=True)
           for key in ("rss_start_bytes", "rss_end_bytes", "rss_peak_bytes")]
    if None not in rss:
        c.limit("resources.rss_growth", rss[1] - rss[0], budgets["rss_growth_bytes"])
        c.limit("resources.rss_start_bytes", rss[0], rss[2])
        c.limit("resources.rss_end_bytes", rss[1], rss[2])
    c.artifact("resources.evidence", "native_trace")

    turns = c.known("turns")
    if not isinstance(turns, list):
        c.issue("BLOCKED", "turns", "Ten physical turns are required.")
        turns = []
    if len(turns) != 10:
        c.issue("FAIL" if turns else "BLOCKED", "turns", "Exactly ten milestone turns are required.")
    previous_rx, previous_generation = start, 0
    previous_sequences = {"panel": 0, "hand": 0}
    previous_releases = {"panel": 0, "hand": 0}
    tx_intervals, reply_intervals = [], []
    controls, phrases, grants = [], set(), set()
    for index, turn in enumerate(turns):
        # Reuse the same field validators for turn objects without modifying input.
        original = c.report
        c.report = {"turn": turn}
        before = len(c.findings)
        control = c.text("turn.ptt.control")
        controls.append(control)
        if control is not None and control not in ("panel", "hand"):
            c.issue("FAIL", "turn.ptt.control", "Use panel or hand physical PTT.")
        c.expect("turn.ptt.source", "independent_physical")
        raw_source = c.text("turn.ptt.raw_source_id")
        expected_source = original.get("hardware_contract", {})
        if isinstance(expected_source, dict):
            expected_source = expected_source.get("ptt", {})
        if isinstance(expected_source, dict):
            expected_source = expected_source.get("source_map", {})
        expected_source = expected_source.get(control) if isinstance(expected_source, dict) else None
        if raw_source is not None and expected_source is not None and raw_source != expected_source:
            c.issue("FAIL", "turn.ptt.raw_source_id", "Physical control does not match the measured board/input mapping.")
        released_before = c.number("turn.ptt.release_sequence_at_press", integer=True)
        released_after = c.number("turn.ptt.release_sequence_at_release", 1, integer=True)
        if released_before is not None and released_after is not None and released_after <= released_before:
            c.issue("FAIL", "turn.ptt.release_sequence_at_release", "Raw release was not retained independently of admitted TX state.")
        if released_before is not None and released_before < previous_releases.get(control, 0):
            c.issue("FAIL", "turn.ptt.release_sequence_at_press", "Retained release counter regressed across turns.")
        if released_after is not None:
            previous_releases[control] = released_after
        c.artifact("turn.ptt.evidence", "ptt_trace")
        age = c.number("turn.ptt.max_age_ms")
        c.limit("turn.ptt.max_age_ms", age, budgets["ptt_max_age_ms"])
        press_seq, release_seq = (c.number("turn.ptt." + key, 1, integer=True)
                                  for key in ("press_sequence", "release_sequence"))
        if press_seq is not None and release_seq is not None:
            if not previous_sequences.get(control, 0) < press_seq < release_seq:
                c.issue("FAIL", "turn.ptt", "PTT event sequences must increase for each physical source.")
            previous_sequences[control] = release_seq
        generation = c.number("turn.generation", 1, integer=True)
        if generation is not None:
            if generation <= previous_generation:
                c.issue("FAIL", "turn.generation", "Turn generations must strictly increase.")
            previous_generation = generation
            transitions = [row for row in native_rows if row.get("kind") == "transition"
                           and row.get("generation") == generation]
            if not all(any(row.get("state") == state and row.get("validation") is True
                           for row in transitions) for state in (4, 5)):
                c.issue("BLOCKED", "turn.generation", "Native trace lacks this validation TX and drain generation.")
        grant = c.text("turn.bench_grant_id")
        if grant is not None and grant in grants:
            c.issue("FAIL", "turn.bench_grant_id", "A bench grant cannot authorize multiple turns.")
        if grant is not None:
            grants.add(grant)
        for key, value in retained.items():
            observed = c.text("turn." + key)
            if value is not None and observed is not None and observed != value:
                c.issue("FAIL", "turn." + key, "Retained resource identity changed.")
        phrase = c.speech("turn.speech", "remote_decode_observation")
        if phrase and phrase in phrases:
            c.issue("FAIL", "turn.speech", "Each turn needs a distinct phrase.")
        if phrase:
            phrases.add(phrase)
        times = {key: c.number("turn." + key) for key in TIMES}
        c.order("turn.timing", [previous_rx, times["press_ms"], times["rf_on_ms"],
                                times["release_ms"], times["rf_off_ms"], times["rx_ready_ms"], end])
        c.order("turn.capture_admission", [times["press_ms"], times["admit_ms"],
                                          times["last_mic_sample_ms"], times["release_ms"]])
        if times["rf_on_ms"] is not None and times["rf_off_ms"] is not None:
            tx_intervals.append((times["rf_on_ms"], times["rf_off_ms"]))
        reply_start, reply_end = (c.number("turn.reply." + key)
                                  for key in ("first_audio_ms", "last_audio_ms"))
        c.order("turn.reply", [times["rx_ready_ms"], reply_start, reply_end, end])
        if reply_start is not None and reply_start == reply_end:
            c.issue("FAIL", "turn.reply", "A nonempty live received reply is required.")
        c.speech("turn.reply.speech", "speaker_observation")
        if generation is not None and reply_start is not None and times["release_ms"] is not None:
            resumed = any(row.get("kind") == "transition" and row.get("state") == 2
                          and type(row.get("generation")) is int and row["generation"] > generation
                          and times["release_ms"] <= row.get("monotonic_ns", -1) / 1000000 <= reply_start
                          for row in native_rows)
            if not resumed:
                c.issue("BLOCKED", "turn.reply", "Native trace lacks the fresh RX generation preceding this reply.")
        if reply_start is not None and reply_end is not None:
            reply_intervals.append((reply_start, reply_end))
        previous_rx = reply_end
        for budget, first, last in (("press_to_rf_ms", "press_ms", "rf_on_ms"),
                                     ("release_to_rf_off_ms", "release_ms", "rf_off_ms"),
                                     ("release_to_rx_ms", "release_ms", "rx_ready_ms")):
            if times[first] is not None and times[last] is not None:
                uncertainty = 0 if budget == "release_to_rx_ms" else rf_uncertainty
                if uncertainty is not None:
                    c.limit("turn." + budget, times[last] - times[first] + uncertainty, budgets[budget])
        accepted = c.number("turn.accepted_mic_samples", 1, integer=True)
        encoded = c.number("turn.encoded_mic_samples", 1, integer=True)
        if accepted is not None and encoded is not None and accepted != encoded:
            c.issue("FAIL", "turn.encoded_mic_samples", "Accepted microphone sample accounting differs.")
        for key in ("unaccounted_samples", "capture_drops", "stale_audio_samples",
                    "playback_underruns_before_drain", "fault_count"):
            c.expect("turn." + key, 0)
        c.expect("turn.tx_end_queues_empty", True)
        c.artifact("turn.evidence", "native_trace")
        for finding in c.findings[before:]:
            finding["field"] = finding["field"].replace("turn", f"turns[{index}]", 1)
        c.report = original
    if controls.count("panel") != 5 or controls.count("hand") != 5:
        c.issue("FAIL" if controls and None not in controls else "BLOCKED", "turns", "Five turns per physical PTT control are required.")
    intervals = c.known("rx.live_intervals")
    if not isinstance(intervals, list) or not intervals:
        c.issue("BLOCKED", "rx.live_intervals", "Measured live decoded intervals are required.")
    else:
        total, previous, valid_intervals, complete = 0, start, [], True
        for index, interval in enumerate(intervals):
            original = c.report
            c.report = {"interval": interval}
            first = c.number("interval.start_ms")
            last = c.number("interval.end_ms")
            c.report = original
            label = f"rx.live_intervals[{index}]"
            c.order(label, [previous, first, last, end])
            previous = last
            if first is not None and last is not None:
                if last <= first:
                    c.issue("FAIL", label, "A nonempty interval is required.")
                total += last - first
                valid_intervals.append((first, last))
                if any(first < off and last > on for on, off in tx_intervals):
                    c.issue("FAIL", label, "Live RF reception overlaps this half-duplex radio's TX.")
            else:
                complete = False
        if complete and decoded_ms is not None and not math.isclose(total, decoded_ms, abs_tol=0.001, rel_tol=0):
            c.issue("FAIL", "rx.live_decoded_ms", "Decoded duration differs from measured intervals.")
        for first, last in reply_intervals:
            if complete and not any(begin <= first < last <= finish for begin, finish in valid_intervals):
                c.issue("FAIL", "rx.live_intervals", "A conversation reply is absent from decoded intervals.")
    status = "FAIL" if any(f["status"] == "FAIL" for f in c.findings) else "BLOCKED" if c.findings else "PASS"
    return {"schema": SCHEMA, "scope": "supplied_physical_conversation_evidence",
            "status": status, "release_qualification": "NOT_ASSESSED",
            "findings": c.findings}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path, help="JSON report; artifact paths are relative to its directory")
    args = parser.parse_args()
    try:
        report = json.loads(args.report.read_text(encoding="utf-8"))
        result = assess(report, args.report.parent)
    except (OSError, ValueError) as error:
        result = {"schema": SCHEMA, "scope": "supplied_physical_conversation_evidence",
                  "status": "BLOCKED", "release_qualification": "NOT_ASSESSED",
                  "findings": [{"status": "BLOCKED", "field": "report", "detail": str(error)}]}
    print(json.dumps(result, indent=2, allow_nan=False))
    return {"PASS": 0, "FAIL": 1, "BLOCKED": 2}[result["status"]]


if __name__ == "__main__":
    raise SystemExit(main())
