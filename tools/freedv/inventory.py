#!/usr/bin/env python3
"""Collect a limited read-only host inventory; never controls the radio."""
import argparse
from datetime import datetime, timezone
import json
import platform
from pathlib import Path
import subprocess


def command(args):
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=10, check=False)
        return {'exit_code': result.returncode, 'stdout': result.stdout, 'stderr': result.stderr}
    except (FileNotFoundError, subprocess.TimeoutExpired) as error:
        return {'exit_code': None, 'error': type(error).__name__}


def read_text(path):
    try:
        return Path(path).read_text().strip()
    except OSError:
        return None


def collect():
    cpus = sorted(Path('/sys/devices/system/cpu').glob('cpu[0-9]*'))
    cpus = [p for p in cpus if p.name[3:].isdigit()]
    return {
        'schema_version': 1,
        'collected_utc': datetime.now(timezone.utc).isoformat(),
        'kernel': platform.release(),
        'machine': platform.machine(),
        'processor': platform.processor(),
        'memory': read_text('/proc/meminfo'),
        'cpus': [{'cpu': p.name, 'frequency_khz': read_text(p / 'cpufreq/scaling_cur_freq'),
                  'governor': read_text(p / 'cpufreq/scaling_governor')} for p in cpus],
        'audio': {
            'alsa_capture': command(['arecord', '-l']),
            'alsa_playback': command(['aplay', '-l']),
            'pulse_sources': command(['pactl', 'list', 'short', 'sources']),
            'pulse_sinks': command(['pactl', 'list', 'short', 'sinks']),
        },
        'radio_hardware_tested': False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True, help='new JSON file')
    args = parser.parse_args()
    result = collect()
    with args.output.open('x') as output:
        json.dump(result, output, indent=2)
        output.write('\n')
    print(f'Inventory written to {args.output}; radio behavior was not tested.')


if __name__ == '__main__':
    main()
