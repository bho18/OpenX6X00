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
        .baseband="stub",.control_version="stub"};
    if(argc>1 && !strcmp(argv[1],"empty")){assert(!fd_native_trace_export(stdout));return 0;}
    fd_native_trace_begin(&p,true);
    fd_voice_status s={.state=FD_VS_RX,.generation=1,.validation=true};
    fd_voice_observation o={.valid=true,.sequence=4,.monotonic_ms=100};
    s.ptt_unknown[1]=true;
    fd_native_trace_note(&s,&o);
    for(int i=0;i<20;++i){++o.sequence;fd_native_trace_note(&s,&o);}
    s.state=FD_VS_TX_STARTING;s.generation=2;s.ptt_held[0]=true;
    fd_native_trace_note(&s,&o);fd_native_trace_command(2,true,false);
    fd_native_audio_trace a={.generation=2,.input_samples=44100,.worker_creations=3,
        .adapter_instance=1,.engine_instance=2,.capture_instance=3,.playback_instance=4};
    fd_native_trace_audio(&a);
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
        transitions = [r for r in rows if r["kind"] == "transition"]
        self.assertEqual(len(transitions), 2)
        self.assertFalse(transitions[0]["ptt"][1]["known"])
        self.assertEqual(transitions[1]["generation"], 2)
        command = next(r for r in rows if r["kind"] == "software_tx_command")
        self.assertFalse(command["physical_readback"])
        audio = next(r for r in rows if r["kind"] == "audio")
        self.assertEqual(audio["engine_instance"], 2)
        self.assertEqual(audio["playback_instance"], 4)
        self.assertTrue(rows[-1]["complete"])

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
