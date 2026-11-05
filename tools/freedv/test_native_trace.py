"""Exercise evidence serialization, bounded loss and session separation."""
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
HARNESS = r'''
#include "src/freedv/native_trace.h"
#include <assert.h>
#include <string.h>
int main(int argc,char **argv) {
    fd_qualified_profile p={.id="test\"profile\n",.hardware="host",.build_id="test-build",
        .baseband="stub",.control_version="stub",.native_contract_id="host-test-v2",
        .sample_rate=44100,.rx_audio={1,2},.tx_audio={2,1}};
    if(argc>1 && !strcmp(argv[1],"empty")){assert(!fd_native_trace_export(stdout));return 0;}
    fd_native_trace_begin(&p,true);
    fd_voice_status s={.state=FD_VS_RX,.generation=1,.validation=true};
    fd_voice_observation o={.valid=true,.sequence=4,.monotonic_ms=100};
    s.ptt_unknown[1]=true;
    fd_native_trace_note(&s,&o);
    for(int i=0;i<20;++i){++o.sequence;fd_native_trace_note(&s,&o);}
    s.state=FD_VS_TX_STARTING;s.generation=2;s.ptt_held[0]=true;
    fd_native_trace_note(&s,&o);fd_native_trace_command(2,true,false);
    if(argc>1 && !strcmp(argv[1],"hidden-release")) {
        /* Same observed level and session state: the input retained a release
         * and repress between polls. Losing this would conceal why a pending
         * turn must be revoked. Poll sequence alone is deliberately elided. */
        ++o.ptt[0].release_sequence;fd_native_trace_note(&s,&o);
        o.ptt[1].valid=true;fd_native_trace_note(&s,&o);
    }
    fd_native_audio_trace a={.generation=2,.input_samples=44100,.worker_creations=3,
        .adapter_instance=1,.engine_instance=2,.capture_instance=3,.playback_instance=4,
        .capture_channel=2,.playback_channel=1};
    fd_native_trace_audio(&a);
    if(argc>1 && !strcmp(argv[1],"control")) {
        fd_native_trace_control("apply_request",11,7,HW_NATIVE_ROUTE_TX,NULL);
        char contract[]="host-test-v2";
        hw_native_voice_state_t ack={.valid=true,.physical_keying_inhibited=true,.autonomous_tx_inhibited=true,
            .direct_mic_rf_inhibited=true,.raw_rx_speaker_inhibited=true,
            .boot_id=51,.sequence=83,.monotonic_ms=1234,.lease_token=11,
            .lease_deadline_ms=1734,.route_generation=7,.applied_route=HW_NATIVE_ROUTE_TX,
            .contract_id=contract};
        fd_native_trace_control("apply_ack",11,7,HW_NATIVE_ROUTE_TX,&ack);
        /* An exported row must keep its own identity and state. */
        contract[0]='X';ack.route_generation=99;
    }
    if(argc>1 && !strcmp(argv[1],"overflow"))
        for(int i=0;i<2500;++i)fd_native_trace_command(2,false,false);
    if(argc>1 && !strcmp(argv[1],"reset"))fd_native_trace_begin(&p,false);
    assert(fd_native_trace_export(stdout));return 0;
}
'''


class TraceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.binary = Path(cls.temp.name) / "trace"
        source = Path(cls.temp.name) / "harness.c"
        source.write_text(HARNESS)
        subprocess.run(["cc", "-std=c11", "-Wall", "-Wextra", "-Werror", "-pthread",
                        "-I", str(ROOT), str(source), str(ROOT / "src/freedv/native_trace.c"),
                        "-o", str(cls.binary)], check=True)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def rows(self, *args):
        return [json.loads(line) for line in subprocess.check_output(
            [str(self.binary), *args], text=True).splitlines()]

    def test_json_identity_and_unknown_observations_are_truthful(self):
        rows = self.rows()
        self.assertEqual(rows[0]["profile"], 'test"profile\n')
        self.assertTrue(rows[0]["simulation"])
        self.assertFalse(rows[0]["hardware_qualified"])
        self.assertFalse(rows[0]["physical_rf_evidence"])
        self.assertEqual(rows[0]["native_contract_id"], "host-test-v2")
        self.assertEqual(rows[0]["audio_slots"]["rx"], {"capture": 1, "playback": 2})
        self.assertEqual(rows[0]["audio_slots"]["tx"], {"capture": 2, "playback": 1})
        self.assertFalse(rows[0]["audio_slots"]["physical_endpoint_mapping_proven"])
        semantics = rows[0]["observation_semantics"]
        self.assertEqual(semantics["software_tx_command"], "requested")
        for key in ("aggregate_tx_proves_rf_off", "aggregate_tx_proves_ptt_release",
                    "host_cache_proves_route_applied", "i2c_presence_proves_native_lease"):
            self.assertFalse(semantics[key])
        transitions = [r for r in rows if r["kind"] == "transition"]
        self.assertEqual(len(transitions), 2)
        self.assertFalse(transitions[0]["ptt"][1]["known"])
        self.assertEqual(transitions[1]["generation"], 2)
        command = next(r for r in rows if r["kind"] == "software_tx_command")
        self.assertFalse(command["physical_readback"])
        self.assertFalse(command["applied_acknowledgement"])
        audio = next(r for r in rows if r["kind"] == "audio")
        self.assertEqual(audio["engine_instance"], 2)
        self.assertEqual(audio["playback_instance"], 4)
        self.assertEqual(audio["capture_channel"], 2)
        self.assertEqual(audio["playback_channel"], 1)
        self.assertTrue(rows[-1]["complete"])

    def test_retained_release_and_input_validity_survive_trace_deduplication(self):
        transitions = [r for r in self.rows("hidden-release") if r["kind"] == "transition"]
        self.assertEqual(len(transitions), 4)
        self.assertEqual(transitions[-3]["state"], transitions[-2]["state"])
        self.assertEqual(transitions[-3]["ptt"][0]["held"], transitions[-2]["ptt"][0]["held"])
        self.assertEqual(transitions[-3]["ptt"][0]["release_sequence"], 0)
        self.assertEqual(transitions[-2]["ptt"][0]["release_sequence"], 1)
        self.assertFalse(transitions[-2]["ptt"][1]["valid"])
        self.assertTrue(transitions[-1]["ptt"][1]["valid"])

    def test_request_does_not_fabricate_applied_state_and_ack_is_retained_separately(self):
        controls = [r for r in self.rows("control") if r["kind"] == "native_control"]
        self.assertEqual(len(controls), 2)
        self.assertEqual(controls[0]["operation"], "apply_request")
        self.assertIsNone(controls[0]["applied_state"])
        self.assertEqual(controls[1]["operation"], "apply_ack")
        self.assertEqual(controls[1]["requested"]["generation"], 7)
        ack = controls[1]["applied_state"]
        self.assertEqual(ack["generation"], 7)
        self.assertEqual(ack["contract_id"], "host-test-v2")
        self.assertEqual(ack["boot_id"], 51)
        self.assertEqual(ack["lease_deadline_ms"], 1734)
        self.assertTrue(ack["autonomous_tx_inhibited"])
        self.assertFalse(controls[1]["physical_rf_evidence"])

    def test_overflow_is_bounded_and_incomplete(self):
        rows = self.rows("overflow")
        self.assertEqual(rows[-1]["rows"], 2048)
        self.assertFalse(rows[-1]["complete"])
        self.assertGreater(rows[-1]["discarded"], 0)
        self.assertEqual(len(rows), 2050)

    def test_new_session_does_not_retain_previous_turns(self):
        rows = self.rows("reset")
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[-1]["rows"], 0)
        self.assertFalse(rows[0]["simulation"])
        self.assertFalse(rows[0]["physical_rf_evidence"])

    def test_unstarted_export_does_not_invent_session(self):
        self.assertEqual(self.rows("empty"), [])


if __name__ == "__main__":
    unittest.main()
