#!/usr/bin/env python3
"""Exercise the assembled mode with local FIFOs and private silent PA devices.

Requires a HOST_BUILD with FREEDV_HOST_TEST_PROFILE=ON. Never contacts hardware.
The virtual profile and stub PTT observations cannot qualify an RF profile.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import time
import telemetry

ROOT = Path(__file__).resolve().parents[3]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def status():
    return telemetry.rpc('freedv_get')


def wait_state(wanted, timeout=12):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        try:
            last = status()
            if last['state'] == wanted and (wanted!=0 or not last.get('active',False)):
                return last
        except (OSError, ValueError, KeyError):
            pass
        time.sleep(.05)
    raise RuntimeError(f'expected native state {wanted}, observed {last}')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--turns', type=int, default=1)
    parser.add_argument('--cycles', type=int, default=1)
    args = parser.parse_args()
    build, out = args.build.resolve(), args.output.resolve()
    if not 1 <= args.turns <= 1000 or not 1 <= args.cycles <= 1000:
        parser.error('turns and cycles must be 1-1000')
    cache = dict(line.split('=', 1) for line in (build/'CMakeCache.txt').read_text().splitlines()
                 if '=' in line and not line.startswith(('#', '//')))
    for option in ('HOST_BUILD', 'FREEDV_HOST_TEST_PROFILE', 'ENABLE_FREEDV_VOICE'):
        if cache.get(option+':BOOL') != 'ON':
            parser.error(f'{option}=ON is required; never run against a physical radio')
    # Reject collisions instead of replacing an existing application/socket.
    for port in (50001, 50002):
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', port))
    try:
        with socket.socket(socket.AF_UNIX) as sock:
            sock.connect('/tmp/x6100_cfg.sock')
    except OSError:
        pass
    else:
        parser.error('another GUI owns the configuration socket')
    out.mkdir(parents=True, exist_ok=False)
    if Path(cache['CMAKE_HOME_DIRECTORY:INTERNAL']).resolve()!=ROOT.resolve():
        parser.error('build belongs to another source checkout')
    with (out/'build.log').open('w') as log:
        subprocess.run(['cmake','--build',str(build),'--target','x6100_gui','--parallel','2'],
                       stdout=log,stderr=subprocess.STDOUT,check=True)
    source_files=('src/freedv/mode.c','src/freedv/voice_radio.inc','src/freedv/voice_session.c',
                  'src/freedv/voice_audio.c','src/freedv/voice_engine.c','src/audio.c','src/radio.c',
                  'src/tx_meters.c','src/cfg_socket.cpp')
    source_hashes={p:hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in source_files}
    fixture_hash=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    telemetry_hash=hashlib.sha256(Path(telemetry.__file__).read_bytes()).hexdigest()
    data = Path(cache['HOST_DATA_DIR:PATH'])
    if data.exists() and any(data.iterdir()):
        parser.error('fixture requires an unused HOST_DATA_DIR')
    data.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(build/'params.db', data/'params.db')
    pulse = out/'pulse'
    pulse.mkdir(mode=0o700)
    modules = Path(shutil.which('pulseaudio')).resolve().parents[1]/'lib/pulse-17.0+dfsg1/modules'
    if not modules.is_dir():
        parser.error('matching PulseAudio 17 modules required')
    conf = pulse/'default.pa'
    conf.write_text(f'.fail\nload-module module-native-protocol-unix socket={pulse}/native auth-anonymous=1\n'
                    'load-module module-null-sink sink_name=fixture rate=44100 channels=2\n'
                    'load-module module-null-source source_name=fixture_mic rate=44100 channels=2\n'
                    'set-default-sink fixture\nset-default-source fixture_mic\n')
    env = dict(os.environ, PULSE_SERVER=f'unix:{pulse}/native', PULSE_RUNTIME_PATH=str(pulse),
               PULSE_STATE_PATH=str(pulse), XDG_CONFIG_HOME=str(out/'config'),
               SDL_VIDEODRIVER='dummy', SDL_AUDIODRIVER='dummy',
               LD_LIBRARY_PATH=str(modules)+os.pathsep+os.environ.get('LD_LIBRARY_PATH', ''))
    processes = []
    observations = []
    try:
        with (out/'pulse.log').open('w') as log:
            processes.append(subprocess.Popen(['pulseaudio', '--daemonize=no', '--use-pid-file=no',
                '--exit-idle-time=-1', '--disable-shm', '--dl-search-path='+str(modules), '-nF', str(conf)],
                env=env, stdout=log, stderr=subprocess.STDOUT))
        for _ in range(60):
            if (pulse/'native').exists():
                break
            time.sleep(.1)
        binary = build/'src/x6100_gui'
        shutil.copyfile(binary, out/'gui-binary')
        with (out/'gui.log').open('w') as log:
            processes.append(subprocess.Popen([str(binary)], env=env, stdout=log,
                                               stderr=subprocess.STDOUT, cwd=ROOT))
        # General GUI services/FFTW startup can be cold after a host restart.
        # This is not the separately measured native-mode preparation time.
        wait_state(0,timeout=60)
        time.sleep(3)
        helper = load('voice_ui_helper', ROOT/'tools/freedv/host_ui_check.py')
        client = load('voice_ui_client', ROOT/'tools/tci/tci_client_test.py')
        client.PORT = 50002
        def screenshot(name):
            ws = client.WS()
            ready=time.monotonic()+.4  # the viewer triggers a complete LVGL repaint
            try:
                for _ in range(150):
                    opcode, frame = ws.frame()
                    if opcode == 2 and frame[:4]==b'\x20\x03\xe0\x01' and frame[8:10] == b'\xff\xd8' and time.monotonic()>=ready:
                        (out/(name+'.jpg')).write_bytes(frame[8:]);return
            finally:
                ws.s.close()
            raise RuntimeError('no screen frame')
        def streams():
            return subprocess.check_output(['pactl','list','short','sink-inputs'],env=env,text=True).splitlines()
        def resources():
            proc = Path('/proc')/str(processes[-1].pid)
            memory = dict(line.split(':',1) for line in (proc/'status').read_text().splitlines() if ':' in line)
            return {'threads':len(list((proc/'task').iterdir())), 'descriptors':len(list((proc/'fd').iterdir())),
                    'rss_kib':int(memory['VmRSS'].split()[0])}
        drive = load('voice_ui_drive', ROOT/'tools/screenshots/x6drive.py')
        drive.emit = helper.emit_fifo
        drive.KEYPAD = str(data/'run/keypad')
        for name, axis in (('mfk', 2), ('main', 0), ('vol', 1)):
            drive.KNOBS[name] = (str(data/'run'/name), axis, -1)
        def key(name):
            drive.key(name, 100)
            time.sleep(.25)
        key('APP'); key('F2')
        drive.turn('mfk', 13); key('MFK')
        first = wait_state(2)
        observations.append({'automatic_receive': first})
        screenshot('receiving')
        deadline=time.monotonic()+3
        while True:
            audio=telemetry.rpc('freedv_stats')
            if audio.get('available'):break
            assert time.monotonic()<deadline;time.sleep(.05)
        assert audio['engine']['rade_creations']==1 and audio['engine']['resampler_creations']==4,audio
        assert audio['playback']['target_bytes']>0 and audio['playback']['maxlength_bytes']>0,audio
        assert not audio['failed'],audio
        observations.append({'receive_audio_telemetry':audio})
        with (out/'telemetry.log').open('w') as log:
            subprocess.run([shutil.which('python3'),str(Path(telemetry.__file__)),
                '--pid',str(processes[-1].pid),'--seconds','2','--output',str(out/'telemetry')],
                stdout=log,stderr=subprocess.STDOUT,check=True)
        capture=json.loads((out/'telemetry/telemetry.json').read_text())
        assert capture['complete'] and capture['profile_id']==audio['profile_id'],capture
        observations.append({'read_only_telemetry_rehearsal':capture})
        drive.turn('main', 3)
        time.sleep(.8)
        tuned = wait_state(2)
        assert tuned['frequency'] != first['frequency'], (first, tuned)
        observations.append({'local_receive_tuning': tuned})
        key('AB')
        time.sleep(.8)
        vfo = wait_state(2)
        assert vfo['vfo'] != tuned['vfo'], (tuned, vfo)
        observations.append({'local_vfo_change': vfo})
        # Radio-side PTT enters before the ordinary analog handler. Test the
        # retained engine, capture flush, PA stream and real device drain.
        helper.emit_fifo(drive.KEYPAD, [(1, drive.KEYS['PTT'], 1)])
        tx = wait_state(4)
        screenshot('transmitting')
        drive.turn('main', 5); key('AB'); key('BAND_UP')
        time.sleep(.5)
        during = status()
        assert during['frequency'] == tx['frequency'] and during['vfo'] == tx['vfo'], (tx, during)
        helper.emit_fifo(drive.KEYPAD, [(1, drive.KEYS['PTT'], 0)])
        observations.append({'tx_tuning_rejected': during})
        observations.append({'graceful_return_to_receive': wait_state(2)})
        retained = streams()
        for turn in range(1, args.turns):
            helper.emit_fifo(drive.KEYPAD, [(1, drive.KEYS['PTT'], 1)])
            wait_state(4)
            time.sleep(.2)
            helper.emit_fifo(drive.KEYPAD, [(1, drive.KEYS['PTT'], 0)])
            wait_state(2)
            assert streams() == retained, f'playback streams recreated on turn {turn}'
        helper.emit_fifo(drive.KEYPAD, [(1, drive.KEYS['PTT'], 1)])
        wait_state(4)
        key('MFK')  # Stop is the initial focus: abort while physical PTT holds.
        stopped=wait_state(6)
        assert stopped['radio_held'] and stopped['fault']==1,stopped
        observations.append({'local_stop_held_lockout':stopped})
        screenshot('stop-held-fault')
        helper.emit_fifo(drive.KEYPAD, [(1, drive.KEYS['PTT'], 0)])
        wait_state(2)
        helper.emit_fifo(drive.KEYPAD, [(1, drive.KEYS['PTT'], 1)])
        wait_state(4)
        key('APP');key('F2')  # Modes opens only after asynchronous shutdown.
        exiting = wait_state(8)
        assert exiting['radio_held'], exiting
        screenshot('held-ptt-exit')
        observations.append({'held_ptt_exit_pending': exiting})
        helper.emit_fifo(drive.KEYPAD, [(1, drive.KEYS['PTT'], 0)])
        ended = wait_state(0)
        assert not ended['active'], ended
        observations.append({'released_exit_complete': ended})
        screenshot('destination-modes')
        key('GEN')
        time.sleep(.2)
        counts = [resources()]
        for cycle in range(1, args.cycles):
            key('APP');key('F2');key('MFK')
            wait_state(2)
            key('GEN');wait_state(0)
            time.sleep(.1)
            counts.append(resources())
        baseline = counts[min(9, len(counts)-1)]
        assert counts[-1]['threads'] == baseline['threads'], counts
        assert counts[-1]['descriptors'] == baseline['descriptors'], counts
        assert counts[-1]['rss_kib'] <= baseline['rss_kib']+8192, counts
        assert source_hashes=={p:hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in source_files},'source changed during run'
        assert fixture_hash==hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'fixture changed during run'
        assert telemetry_hash==hashlib.sha256(Path(telemetry.__file__).read_bytes()).hexdigest(),'collector changed during run'
        (out/'result.json').write_text(json.dumps({'status':'PASS', 'scope':'virtual host only',
            'binary_sha256':hashlib.sha256(binary.read_bytes()).hexdigest(),
            'hardware_qualified':False, 'intelligibility':'UNASSESSED', 'observations':observations,
            'normal_turns':args.turns, 'enter_exit_cycles':args.cycles, 'cycle_resources':counts,
            'source_sha256':source_hashes,'build_log_sha256':hashlib.sha256((out/'build.log').read_bytes()).hexdigest(),
            'fixture_sha256':fixture_hash,'telemetry_sha256':telemetry_hash,
            'retained_playback_streams':retained}, indent=2)+'\n')
        print('PASS: assembled native mode, local tuning/VFO, TX guards, actual PA drain, held-PTT exit')
    finally:
        for process in reversed(processes):
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill(); process.wait()


if __name__ == '__main__':
    main()
