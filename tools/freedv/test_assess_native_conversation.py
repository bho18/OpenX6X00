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

from assess_native_conversation import assess, BUDGETS, IDENTITIES, RETAINED, ROUTES, SCHEMA


def example(root):
    """Manufacture claims to test the parser, never to qualify a radio."""
    artifacts = {}
    for kind in ("native_trace", "ptt_trace", "rf_capture", "test_plan",
                 "route_observation", "speaker_observation", "remote_decode_observation"):
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
        "routes": {key: {"observed": True, "method": "synthetic claim", "evidence": "route_observation"}
                   for key in ROUTES},
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
    for index in range(10):
        press = 130000 + index * 20000
        reply = {"first_audio_ms": press + 11000, "last_audio_ms": press + 13000,
                 "speech": speech(f"reply {index} received", "speaker_observation")}
        report["turns"].append({
            **retained, "generation": index * 2 + 2, "bench_grant_id": f"grant-{index}",
            "ptt": {"control": "panel" if index < 5 else "hand", "source": "independent_physical",
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
              "session_id": retained["session_id"], "baseband": identities["baseband"]}]
    for turn in report["turns"]:
        for state, stamp in ((4, turn["rf_on_ms"]), (5, turn["release_ms"])):
            trace.append({"kind": "transition", "state": state, "generation": turn["generation"],
                          "validation": True, "monotonic_ns": stamp * 1000000})
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
                   lambda rows: rows[1].update(generation=900),
                   lambda rows: rows[1].update(validation=False),
                   lambda rows: rows[3].update(state=1)]
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
