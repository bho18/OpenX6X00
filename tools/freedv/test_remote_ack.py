#!/usr/bin/env python3
"""Compile real CAT/socket handler bodies with synthetic endpoints and writer gate.

Extracting the complete production functions avoids cloning their protocol
branches or linking hardware, networking and the entire GUI into this test.
"""
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


def function(source, marker):
    start = source.index(marker)
    opening = source.index('{', start)
    depth = 1
    end = opening + 1
    while depth:
        depth += (source[end] == '{') - (source[end] == '}')
        end += 1
    return source[start:end]


class RemoteAcknowledgements(unittest.TestCase):
    def test_production_handlers(self):
        cat = (ROOT/'src/cat.cpp').read_text()
        socket = (ROOT/'src/cfg_socket.cpp').read_text()
        handler = function(cat, 'static Frame *process_req(')
        fields = sorted(set(re.findall(r'cfg_sm\.(\w+)', handler)) - {'cfg_band_vfo_copy'})
        replacements = {
            'CFG_FIELDS': '\n'.join(f'    Parameter<{"float" if f == "p_pwr" else "int32_t"}> {f};' for f in fields)
                + '\n    Parameter<float> p_auto_level_offset;',
            'CAT_DEFINES': '\n'.join(line for line in cat.splitlines() if re.match(r'#define (?:FRAME_|CODE_|[CSM]_|MEM_)', line)),
            'BCD_FUNCTIONS': '\n'.join(function(cat, marker) for marker in
                ('void to_bcd(', 'void to_bcd_be(', 'uint64_t from_bcd(', 'uint64_t from_bcd_be(')),
            'CAT_HANDLER': function(cat, 'static void set_unsupported(') + '\n' + handler,
            'SOCKET_TYPES': socket[socket.index('typedef struct {\n    char    name[24];'):socket.index('/* LVGL thread */')],
            'SOCKET_CALLBACKS': function(socket, 'static void disp_apply(') + '\n' + function(socket, 'static void eth_apply_cb('),
            'SOCKET_HANDLER': function(socket, 'static int handle_line('),
        }
        template = (ROOT/'tools/freedv/remote_ack_harness.cpp.in').read_text()
        for key, value in replacements.items():
            template = template.replace('@'+key+'@', value)
        with tempfile.TemporaryDirectory(prefix='freedv-remote-ack-') as directory:
            build = Path(directory)
            source = build/'handlers.cpp'
            source.write_text(template)
            objects = []
            for name in ('src/freedv/writer_gate.c', 'src/cJSON.c'):
                obj = build/(Path(name).stem+'.o')
                subprocess.run([os.environ.get('CC', 'cc'), '-std=c11', '-D_GNU_SOURCE', '-DENABLE_FREEDV_DIAGNOSTICS=1',
                                '-I'+str(ROOT), '-c', str(ROOT/name), '-o', str(obj)], check=True)
                objects.append(str(obj))
            executable = build/'remote-ack'
            subprocess.run([os.environ.get('CXX', 'c++'), '-std=c++17', '-DENABLE_FREEDV_DIAGNOSTICS=1',
                            '-I'+str(ROOT), str(source), *objects, '-pthread', '-lm', '-o', str(executable)], check=True)
            subprocess.run([str(executable)], check=True, timeout=15)


if __name__ == '__main__':
    unittest.main()
