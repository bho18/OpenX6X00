#!/usr/bin/env python3
"""Exercise the real GUI service with process commands replaced by fixtures."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


SERVICE = Path(__file__).resolve().parents[2] / "rootfs/etc/init.d/S95gui"


class ServiceShutdown(unittest.TestCase):
    def run_service(self, action, gui_checks):
        with tempfile.TemporaryDirectory(prefix="freedv-service-") as directory:
            root = Path(directory)
            state = root / "state.json"
            state.write_text(json.dumps({"remaining": gui_checks, "checks": 0, "starts": 0}))
            command = root / "command"
            command.write_text("""#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
state = Path(os.environ['FD_SERVICE_STATE'])
value = json.loads(state.read_text())
name = Path(sys.argv[0]).name
result = 0
if name == 'pidof':
    result = 1
    if sys.argv[1] == 'x6100_gui':
        value['checks'] += 1
        result = 0 if value['remaining'] else 1
        if value['remaining'] > 0:
            value['remaining'] -= 1
elif name == 'start-stop-daemon' and '-S' in sys.argv:
    value['starts'] += 1
state.write_text(json.dumps(value))
sys.exit(result)
""")
            command.chmod(0o755)
            for name in ("pidof", "killall", "start-stop-daemon", "sleep", "rm"):
                (root / name).symlink_to(command)
            env = dict(os.environ, PATH=f"{root}:{os.environ['PATH']}", FD_SERVICE_STATE=str(state))
            result = subprocess.run(["sh", str(SERVICE), action], env=env, capture_output=True, text=True)
            return result, json.loads(state.read_text())

    def test_stop_waits_for_gui_after_daemon_is_gone(self):
        result, state = self.run_service("stop", 3)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(state["checks"], 4)
        self.assertEqual(state["starts"], 0)
        self.assertIn("OK", result.stdout)

    def test_failed_gui_stop_refuses_restart(self):
        result, state = self.run_service("restart", -1)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(state["checks"], 20)
        self.assertEqual(state["starts"], 0)
        self.assertIn("restart refused", result.stdout)


if __name__ == "__main__":
    unittest.main()
