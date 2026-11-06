#!/usr/bin/env python3
"""Keep standalone CI compilation on the production host-test code path."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from build_usb_controller_host import host_definitions


class HostCompilationContractTests(unittest.TestCase):
    def test_compiler_sees_host_hook_gate_and_exact_string_identities(self):
        compiler = shutil.which('cc')
        self.assertIsNotNone(compiler, 'This build-configuration regression requires a C compiler')
        source = '''
#ifndef HOST_BUILD
#error The host operation-phase hook would not be compiled
#endif
#if HOST_BUILD != 1 || RADE_ACQ_PARALLEL != 1
#error Wrong production host test configuration
#endif
static const char *build = FREEDV_BUILD_ID;
static const char *revision = FREEDV_SOURCE_COMMIT;
int main(void) { return build[0] == 'u' && revision[0] == 'a' ? 0 : 1; }
'''
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)/'contract.c';path.write_text(source)
            flags = host_definitions('usb-controller-regression', 'a'*40)
            result = subprocess.run([compiler, '-std=c11', '-Wall', '-Wextra', '-Werror',
                                     '-fsyntax-only', *flags, str(path)], capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            # Prove the check detects the precise defect from the first CI run.
            result = subprocess.run([compiler, '-std=c11', '-fsyntax-only',
                                     *[flag for flag in flags if not flag.startswith('-DHOST_BUILD=')], str(path)],
                                    capture_output=True, text=True, timeout=10)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('host operation-phase hook', result.stderr)


if __name__ == '__main__':
    unittest.main()
