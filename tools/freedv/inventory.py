#!/usr/bin/env python3
"""Collect a limited read-only host inventory; never controls the radio."""
import argparse
from datetime import datetime, timezone
import json
import os
import platform
from pathlib import Path
import re
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
    card_list = read_text('/proc/asound/cards')
    card_ids = re.findall(r'^\s*(\d+)\s+\[', card_list or '', flags=re.MULTILINE)
    thermal = sorted(Path('/sys/class/thermal').glob('thermal_zone[0-9]*'))
    try:
        affinity = sorted(os.sched_getaffinity(0))
    except (AttributeError, OSError):
        affinity = None
    return {
        'schema_version': 2,
        'collected_utc': datetime.now(timezone.utc).isoformat(),
        'kernel': platform.release(),
        'machine': platform.machine(),
        'processor': platform.processor(),
        'uptime_proc': read_text('/proc/uptime'),
        'memory': read_text('/proc/meminfo'),
        'cpus': [{'cpu': p.name, 'frequency_khz': read_text(p / 'cpufreq/scaling_cur_freq'),
                  'min_frequency_khz': read_text(p / 'cpufreq/cpuinfo_min_freq'),
                  'max_frequency_khz': read_text(p / 'cpufreq/cpuinfo_max_freq'),
                  'governor': read_text(p / 'cpufreq/scaling_governor')} for p in cpus],
        'collector_cpu_affinity': affinity,
        'thermal_zones': [{'name': p.name, 'type': read_text(p / 'type'),
                           'temperature_millidegrees_c': read_text(p / 'temp')} for p in thermal],
        'firmware': {
            'application_version_file': read_text('/usr/share/x6100/app-version'),
            'image_metadata_file': read_text('/etc/openx6x00-image'),
            'baseband_version': None,
            'baseband_version_note': 'Record from the radio Info page; this helper does not query the baseband.',
        },
        'audio': {
            'alsa_cards': card_list,
            'alsa_capture': command(['arecord', '-l']),
            'alsa_playback': command(['aplay', '-l']),
            'alsa_capture_endpoints': command(['arecord', '-L']),
            'alsa_playback_endpoints': command(['aplay', '-L']),
            'alsa_mixers': [{'card': int(card), 'contents': command(['amixer', '-c', card, 'scontents'])}
                            for card in card_ids],
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
