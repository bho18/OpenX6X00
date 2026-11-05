#!/usr/bin/env python3
"""Synthetic schema tests only; no fixture here constitutes physical evidence."""
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from assess_native_conversation import assess, BUDGETS, ENDPOINTS, IDENTITIES, INHIBIT_SCOPE, RETAINED, ROUTES, SCHEMA


def example(root):
    """Manufacture claims to test the parser, never to qualify a radio."""
    artifacts = {}
    for kind in ("native_trace", "ptt_trace", "rf_capture", "test_plan",
                 "route_observation", "speaker_observation", "remote_decode_observation",
                 "control_command", "effective_state", "board_mapping", "hardware_contract"):
        payload = ("SYNTHETIC UNIT TEST: " + kind).encode()
        (root / kind).write_bytes(payload)
        artifacts[kind] = {"path": kind, "sha256": hashlib.sha256(payload).hexdigest(), "kind": kind}
    identities = {key: "a" * length if length else "test-identity" for key, length in IDENTITIES.items()}
    retained = {key: "synthetic-" + key for key in RETAINED}
    speech = lambda phrase, kind: {"expected_phrase": phrase, "heard_phrase": phrase,
                                   "observer": "synthetic unit fixture", "intelligible": True, "evidence": kind}
    report = {
        "schema": SCHEMA, "evidence_domain": "physical_radio", "mode": "RADE_V1",
        "operating_path": "native_voice_session", "artifacts": artifacts,
        "identities": {"expected": identities, "observed": identities.copy(), "evidence": "native_trace"},
        "budgets": {**{key: 1000 for key in BUDGETS}, "rss_growth_bytes": 100000,
                    "fixed_before_run": True, "evidence": "test_plan"},
        "session": {**retained, "engine_create_count": 1, "capture_open_count": 1,
                    "playback_open_count": 1, "wav_taps_enabled": False,
                    "restoration_confirmed": True, "local_settings_preserved": True,
                    "start_ms": 0, "end_ms": 360000, "evidence": "native_trace"},
        "routes": {key: {"observed": True, "observed_at_ms": 130500,
                         "observation_basis": "physical_signal_measurement",
                         "method": "synthetic claim", "evidence": "route_observation",
                         "requested": {"lease_token": 1, "generation": 2, "route": 3, "at_ms": 130089,
                                       "evidence": "control_command"},
                         "applied": {"lease_token": 1, "generation": 2, "route": 3, "boot_id": 1,
                                     "sequence": 20, "at_ms": 130090, "source": "effective_hardware_ack",
                                     "evidence": "effective_state"}}
                   for key in ROUTES},
        "endpoints": {key: {"host_device": "synthetic-host", "host_channel": 0,
                            "board_net": "synthetic-net", "physical_endpoint": endpoint,
                            "evidence": "board_mapping"} for key, endpoint in ENDPOINTS.items()},
        "hardware_contract": {
            "ptt": {"source": "raw_physical_edges", "retained_release_counter": True,
                    "minimum_retained_release_ms": 10,
                    "source_map": {"panel": "synthetic-panel", "hand": "synthetic-hand"},
                    "evidence": "board_mapping"},
            "lease": {"controller": "independent_controller", "expiry_clears_pending_requests": True,
                      "inhibit_ack_source": "effective_hardware_ack",
                      "inhibit_scope": list(INHIBIT_SCOPE),
                      "evidence": "hardware_contract"},
            "shutdown": {"source": "independent_rf", "max_rf_off_ms": 100, "uncertainty_ms": 2,
                         "trigger": "native_lease_expiry", "initial_rf_present": True,
                         "physical_ptt_held": True, "ordinary_i2c_traffic_continued": True,
                         "budget_ms": 500, "evidence": "rf_capture"}},
        "rf_observation": {"source": "independent_rf", "resolution_ms": 1, "evidence": "rf_capture",
                           "clock_alignment_max_error_ms": 1, "clock_alignment_method": "synthetic fixture"},
        "reference_decoder": {"application": "official_FreeDV", "version": "test", "track": "installed_release"},
        "rx": {"source": "live_rf", "output": "radio_speaker", "live_decoded_ms": 140000,
               "evidence": "native_trace", "speech": speech("start receive finish", "speaker_observation"),
               "live_intervals": [{"start_ms": 0, "end_ms": 60000}, {"start_ms": 62000, "end_ms": 122000}],
               "reacquisition": {"lost_ms": 60000, "restored_ms": 61500, "decoded_ms": 62000,
                                 "speech": speech("return heard again", "speaker_observation")}},
        "queues": {**{key: {"capacity_samples": 1000, "high_water_samples": 500,
                            "measurement": "event_high_water"} for key in ("capture", "engine_input", "modem_output")},
                   "evidence": "native_trace"},
        "resources": {"rss_start_bytes": 1000000, "rss_end_bytes": 1010000, "rss_peak_bytes": 1020000,
                      "evidence": "native_trace"}, "turns": [],
    }
    for key, route in report["routes"].items():
        if key not in ("tx_mic_survives", "analog_mic_isolated"):
            route["requested"].update(generation=3, route=2, at_ms=140498)
            route["applied"].update(generation=3, route=2, sequence=30, at_ms=140499)
            route["observed_at_ms"] = 140800
    for index in range(10):
        press = 130000 + index * 20000
        reply = {"first_audio_ms": press + 11000, "last_audio_ms": press + 13000,
                 "speech": speech(f"reply {index} received", "speaker_observation")}
        report["turns"].append({
            **retained, "generation": index * 2 + 2, "bench_grant_id": f"grant-{index}",
            "ptt": {"control": "panel" if index < 5 else "hand", "source": "independent_physical",
                    "raw_source_id": "synthetic-panel" if index < 5 else "synthetic-hand",
                    "release_sequence_at_press": index % 5,
                    "release_sequence_at_release": index % 5 + 1,
                    "max_age_ms": 10, "press_sequence": index % 5 * 2 + 1,
                    "release_sequence": index % 5 * 2 + 2, "evidence": "ptt_trace"},
            "speech": speech(f"first turn number {index} last", "remote_decode_observation"),
            "press_ms": press, "admit_ms": press + 90, "rf_on_ms": press + 100,
            "release_ms": press + 10000, "last_mic_sample_ms": press + 9999,
            "rf_off_ms": press + 10200, "rx_ready_ms": press + 10500,
            "reply": reply, "accepted_mic_samples": 440000, "encoded_mic_samples": 440000,
            "unaccounted_samples": 0, "capture_drops": 0, "stale_audio_samples": 0,
            "playback_underruns_before_drain": 0, "fault_count": 0, "tx_end_queues_empty": True,
            "evidence": "native_trace",
        })
        report["rx"]["live_intervals"].append({"start_ms": reply["first_audio_ms"], "end_ms": reply["last_audio_ms"]})
    trace = [{"schema": 1, "kind": "native_session", "simulation": False,
              "session_id": retained["session_id"], "baseband": identities["baseband"],
              "native_contract_id": identities["route_contract_id"]}]
    for turn in report["turns"]:
        control_row = {"kind": "native_control", "operation": "apply_ack", "monotonic_ns": turn["admit_ms"] * 1000000,
                      "requested": {"lease_token": 1, "generation": turn["generation"], "route": 3},
                      "applied_state": {"valid": True, "boot_id": 1, "sequence": turn["generation"] * 10,
                                        "stamp_ms": turn["admit_ms"], "lease_token": 1,
                                        "lease_deadline_ms": turn["admit_ms"] + 500,
                                        "generation": turn["generation"], "route": 3,
                                        "physical_keying_inhibited": True, "autonomous_tx_inhibited": True,
                                        "direct_mic_rf_inhibited": True, "raw_rx_speaker_inhibited": True,
                                        "contract_id": identities["route_contract_id"]}}
        trace.append(control_row)
        for state, stamp in ((4, turn["rf_on_ms"]), (5, turn["release_ms"])):
            trace.append({"kind": "transition", "state": state, "generation": turn["generation"],
                          "validation": True, "monotonic_ns": stamp * 1000000})
        rx_control = copy.deepcopy(control_row)
        rx_control["monotonic_ns"] = (turn["rx_ready_ms"] - 1) * 1000000
        rx_control["requested"].update(generation=turn["generation"] + 1, route=2)
        rx_control["applied_state"].update(generation=turn["generation"] + 1, route=2,
                                           sequence=(turn["generation"] + 1) * 10,
                                           stamp_ms=turn["rx_ready_ms"] - 1,
                                           lease_deadline_ms=turn["rx_ready_ms"] + 499)
        trace.append(rx_control)
        trace.append({"kind": "transition", "state": 2, "generation": turn["generation"] + 1,
                      "validation": True, "monotonic_ns": turn["rx_ready_ms"] * 1000000})
    trace.append({"kind": "trace_end", "complete": True, "discarded": 0, "rows": len(trace) - 1})
    payload = ("\n".join(json.dumps(row) for row in trace) + "\n").encode()
    (root / "native_trace").write_bytes(payload)
    report["artifacts"]["native_trace"]["sha256"] = hashlib.sha256(payload).hexdigest()
    return report


class ConversationEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.report = example(self.root)

    def result(self, report=None):
        return assess(self.report if report is None else report, self.root)

    def test_complete_claims_are_consistent_but_do_not_qualify_release(self):
        result = self.result()
        self.assertEqual(result["status"], "PASS", result["findings"])
        self.assertEqual(result["release_qualification"], "NOT_ASSESSED")

    def test_absent_and_unknown_evidence_is_blocked(self):
        for change in (
            lambda r: r["routes"]["tx_mic_survives"].update(observed=None),
            lambda r: r["rf_observation"].pop("evidence"),
            lambda r: r["turns"][0]["speech"].update(heard_phrase="unknown"),
            lambda r: r["identities"]["observed"].pop("baseband"),
            lambda r: r["turns"][0].pop("session_id"),
            lambda r: r["turns"][0]["ptt"].pop("control"),
            lambda r: r["rf_observation"].pop("clock_alignment_max_error_ms"),
        ):
            report = copy.deepcopy(self.report)
            change(report)
            self.assertEqual(self.result(report)["status"], "BLOCKED")
        self.assertEqual(self.result({})["status"], "BLOCKED")

    def test_simulation_file_and_qemu_evidence_never_pass(self):
        for domain in ("simulation", "qemu", "file_processing", "virtual_audio"):
            self.report["evidence_domain"] = domain
            self.assertEqual(self.result()["status"], "BLOCKED")

    def test_native_trace_cannot_be_relabelled_as_physical_or_complete(self):
        original = (self.root / "native_trace").read_text()
        changes = [lambda rows: rows[0].update(simulation=True),
                   lambda rows: rows[-1].update(discarded=1),
                   lambda rows: rows[-1].update(rows=1),
                   lambda rows: rows[2].update(generation=900),
                   lambda rows: rows[2].update(validation=False),
                   lambda rows: rows[5].update(state=1)]
        for change in changes:
            rows = [json.loads(line) for line in original.splitlines()]
            change(rows)
            payload = ("\n".join(json.dumps(row) for row in rows) + "\n").encode()
            (self.root / "native_trace").write_bytes(payload)
            self.report["artifacts"]["native_trace"]["sha256"] = hashlib.sha256(payload).hexdigest()
            self.assertEqual(self.result()["status"], "BLOCKED")

    def test_mismatched_identity_reopened_stream_and_reused_generation_fail(self):
        changes = [lambda r: r["identities"]["observed"].update(gui_sha256="b" * 64),
                   lambda r: r["turns"][1].update(capture_stream_id="reopened"),
                   lambda r: r["session"].update(engine_create_count=2),
                   lambda r: r["turns"][1].update(generation=2),
                   lambda r: r["turns"][1].update(bench_grant_id="grant-0"),
                   lambda r: r["turns"][1]["ptt"].update(press_sequence=1)]
        for change in changes:
            report = copy.deepcopy(self.report)
            change(report)
            self.assertEqual(self.result(report)["status"], "FAIL")

    def test_wrong_turn_mix_missing_words_and_stale_audio_fail(self):
        changes = [lambda r: r["turns"].pop(),
                   lambda r: r["turns"][0]["ptt"].update(control="hand"),
                   lambda r: r["turns"][0]["ptt"].update(source="aggregate_tx"),
                   lambda r: r["turns"][0]["speech"].update(heard_phrase="turn zero last"),
                   lambda r: r["turns"][0]["speech"].update(intelligible=False),
                   lambda r: r["turns"][1].update(speech=r["turns"][0]["speech"].copy()),
                   lambda r: r["turns"][0].update(stale_audio_samples=1)]
        for change in changes:
            report = copy.deepcopy(self.report)
            change(report)
            self.assertEqual(self.result(report)["status"], "FAIL")

    def test_route_queue_accounting_and_timing_violations_fail(self):
        changes = [lambda r: r["routes"]["analog_mic_isolated"].update(observed=False),
                   lambda r: r["queues"]["capture"].update(high_water_samples=1001),
                   lambda r: r["queues"]["capture"].update(measurement="sampled_maximum"),
                   lambda r: r["turns"][0].update(encoded_mic_samples=439999),
                   lambda r: r["turns"][0].update(last_mic_sample_ms=140001),
                   lambda r: r["turns"][0].update(rf_off_ms=142000),
                   lambda r: r["budgets"].update(release_to_rf_off_ms=2000),
                   lambda r: r["rf_observation"].update(resolution_ms=1000),
                   lambda r: r["resources"].update(rss_end_bytes=2000000),
                   lambda r: r["session"].update(wav_taps_enabled=True),
                   lambda r: r["turns"][0]["ptt"].update(max_age_ms=1001)]
        for change in changes:
            report = copy.deepcopy(self.report)
            change(report)
            self.assertEqual(self.result(report)["status"], "FAIL")

    def test_requested_applied_and_physical_route_evidence_are_not_interchangeable(self):
        changes = [
            (lambda r: r["routes"]["tx_mic_survives"]["applied"].update(source="command_cache"), "FAIL"),
            (lambda r: r["routes"]["tx_mic_survives"]["applied"].update(generation=99), "FAIL"),
            (lambda r: r["routes"]["tx_mic_survives"].update(applied=None), "BLOCKED"),
            (lambda r: r["routes"]["analog_mic_isolated"].update(observation_basis="zero_pga_gain"), "FAIL"),
            (lambda r: r["routes"]["analog_mic_isolated"].update(observation_basis="monitor_zero"), "FAIL"),
            (lambda r: r["routes"]["raw_modem_suppressed"].update(observation_basis="lineout_zero"), "FAIL"),
            (lambda r: r["endpoints"]["rx_capture"].update(board_net=None), "BLOCKED"),
            (lambda r: r["endpoints"]["rx_capture"].update(physical_endpoint="adc_sum"), "FAIL"),
        ]
        for change, expected in changes:
            report = copy.deepcopy(self.report)
            change(report)
            self.assertEqual(self.result(report)["status"], expected)

    def test_i2c_presence_aggregate_tx_and_incomplete_inhibit_cannot_authorize_tx(self):
        changes = [
            (lambda r: r["hardware_contract"]["lease"].update(controller="i2c_stop_presence"), "FAIL"),
            (lambda r: r["hardware_contract"]["lease"].update(expiry_clears_pending_requests=False), "FAIL"),
            (lambda r: r["hardware_contract"]["lease"].update(inhibit_scope=["software_tx"]), "FAIL"),
            (lambda r: r["hardware_contract"]["lease"].update(inhibit_ack_source="cached_command"), "FAIL"),
            (lambda r: r["hardware_contract"]["shutdown"].update(source="aggregate_tx"), "FAIL"),
            (lambda r: r["hardware_contract"]["shutdown"].update(trigger="i2c_presence_timeout"), "FAIL"),
            (lambda r: r["hardware_contract"]["shutdown"].update(ordinary_i2c_traffic_continued=False), "FAIL"),
            (lambda r: r["hardware_contract"]["shutdown"].update(initial_rf_present=False), "FAIL"),
            (lambda r: r["hardware_contract"]["shutdown"].update(max_rf_off_ms=None), "BLOCKED"),
            (lambda r: r["hardware_contract"]["shutdown"].update(max_rf_off_ms=1200), "FAIL"),
        ]
        for change, expected in changes:
            report = copy.deepcopy(self.report)
            change(report)
            self.assertEqual(self.result(report)["status"], expected)

    def test_physical_connector_and_retained_release_evidence_are_required(self):
        changes = [lambda r: r["hardware_contract"]["ptt"].update(source="admitted_tx_shadow"),
                   lambda r: r["hardware_contract"]["ptt"].update(retained_release_counter=False),
                   lambda r: r["turns"][0]["ptt"].update(raw_source_id="unmapped-PH6"),
                   lambda r: r["turns"][0]["ptt"].update(release_sequence_at_release=0),
                   lambda r: r["turns"][1]["ptt"].update(release_sequence_at_press=0)]
        for change in changes:
            report = copy.deepcopy(self.report)
            change(report)
            self.assertEqual(self.result(report)["status"], "FAIL")

    def test_report_cannot_hide_null_stale_expired_or_misattributed_native_ack(self):
        original = (self.root / "native_trace").read_text()
        changes = [lambda row: row.update(applied_state=None),
                   lambda row: row["applied_state"].update(generation=999),
                   lambda row: row["applied_state"].update(stamp_ms=129000),
                   lambda row: row["applied_state"].update(lease_deadline_ms=130090),
                   lambda row: row["applied_state"].update(boot_id=2),
                   lambda row: row["applied_state"].update(boot_id=True),
                   lambda row: row["applied_state"].update(direct_mic_rf_inhibited=False),
                   lambda row: row["applied_state"].update(contract_id="different-contract")]
        for change in changes:
            rows = [json.loads(line) for line in original.splitlines()]
            change(rows[1])
            payload = ("\n".join(json.dumps(row) for row in rows) + "\n").encode()
            (self.root / "native_trace").write_bytes(payload)
            self.report["artifacts"]["native_trace"]["sha256"] = hashlib.sha256(payload).hexdigest()
            result = self.result()
            self.assertEqual(result["status"], "BLOCKED", result["findings"])
            self.assertTrue(any("effective-state" in finding["detail"] for finding in result["findings"]))

    def test_v1_report_is_not_silently_promoted_to_v2(self):
        self.report["schema"] = "x6200.native-conversation.v1"
        self.assertEqual(self.result()["status"], "FAIL")

    def test_half_duplex_receive_duration_recovery_and_live_replies(self):
        changes = [lambda r: r["rx"].update(live_decoded_ms=119999),
                   lambda r: r["rx"]["live_intervals"][0].update(end_ms=60001),
                   lambda r: r["rx"]["live_intervals"][1].update(end_ms=140000),
                   lambda r: r["rx"]["reacquisition"].update(restored_ms=60000),
                   lambda r: r["rx"].update(source="wav_file"),
                   lambda r: r["turns"][0]["reply"].update(last_audio_ms=144000),
                   lambda r: r["turns"][0]["reply"]["speech"].update(intelligible=False)]
        for change in changes:
            report = copy.deepcopy(self.report)
            change(report)
            self.assertEqual(self.result(report)["status"], "FAIL")

    def test_missing_corrupted_empty_and_escaping_artifacts(self):
        target = self.root / "rf_capture"
        target.unlink()
        self.assertEqual(self.result()["status"], "BLOCKED")
        target.write_bytes(b"corrupt")
        self.assertEqual(self.result()["status"], "FAIL")
        target.write_bytes(b"")
        self.assertEqual(self.result()["status"], "BLOCKED")
        self.report["artifacts"]["rf_capture"]["path"] = "../outside"
        self.assertEqual(self.result()["status"], "FAIL")

    def test_nonfinite_boolean_and_negative_measurements_fail(self):
        for value in (float("nan"), float("inf"), True, -1, "100", 10 ** 1000, -(10 ** 1000)):
            report = copy.deepcopy(self.report)
            report["turns"][0]["rf_off_ms"] = value
            self.assertEqual(self.result(report)["status"], "FAIL")

    def test_cli_status_and_invalid_json(self):
        path = self.root / "report.json"
        script = Path(__file__).with_name("assess_native_conversation.py")
        for text, expected_code in (("{}", 2), ("broken json", 2), (json.dumps(self.report), 0)):
            path.write_text(text)
            result = subprocess.run([sys.executable, str(script), str(path)], capture_output=True, text=True)
            self.assertEqual(result.returncode, expected_code, result.stderr)
            self.assertEqual(json.loads(result.stdout)["release_qualification"], "NOT_ASSESSED")


if __name__ == "__main__":
    unittest.main()
