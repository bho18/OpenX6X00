from pathlib import Path
import os, subprocess, tempfile, time, json, lzma, hashlib, signal
root = Path(__file__).resolve().parent
lib = root / 'librade.so.0.1'
lib.write_bytes(lzma.decompress(b''.join(f.read_bytes() for f in sorted(root.glob('librade.so.0.1.xz.part*')))))
binary = root / 'voice-probe'
binary.write_bytes(lzma.decompress((root / 'voice-probe.xz').read_bytes()))
binary.chmod(0o755)
print(json.dumps({'probe_sha256': hashlib.sha256(binary.read_bytes()).hexdigest(), 'rade_sha256': hashlib.sha256(lib.read_bytes()).hexdigest()}), flush=True)
for case, pause in [('retained-normal', 0), ('retained-idle-suspend', 0), ('capture-pause-250ms', .250), ('capture-pause-750ms', .750)]:
    with tempfile.TemporaryDirectory(prefix='freedv-pulse-probe-', dir='/tmp') as temp:
        p = Path(temp); (p / 'config').mkdir()
        out = root / case; out.mkdir()
        conf = p / 'default.pa'
        conf.write_text(f'.fail\nload-module module-native-protocol-unix socket={p}/native auth-anonymous=1\nload-module module-null-sink sink_name=freedv_probe_sink rate=44100 channels=2 norewinds=1\nload-module module-null-source source_name=freedv_probe_source rate=44100 channels=2\nload-module module-suspend-on-idle timeout=1\nset-default-sink freedv_probe_sink\nset-default-source freedv_probe_source\n')
        env = dict(os.environ, PULSE_SERVER=f'unix:{p}/native', PULSE_RUNTIME_PATH=str(p), PULSE_STATE_PATH=str(p), XDG_CONFIG_HOME=str(p / 'config'), LD_LIBRARY_PATH=str(root))
        server = subprocess.Popen(['pulseaudio', '--daemonize=no', '--use-pid-file=no', '--exit-idle-time=-1', '--disable-shm', '--realtime=no', '--high-priority=no', '-nF', str(conf)], env=env)
        probe = None
        try:
            deadline = time.monotonic() + 5
            while not (p / 'native').exists() and server.poll() is None and time.monotonic() < deadline: time.sleep(.05)
            assert (p / 'native').exists()
            print('CASE ' + case, flush=True)
            if case == 'retained-idle-suspend': env.update(FREEDV_PROBE_SLOW_LEGACY='1', FREEDV_PROBE_LONG_IDLE='1')
            probe = subprocess.Popen([str(binary), str(out)], env=env)
            deadline = time.monotonic() + 130
            injected = False; sink_checked = False
            while probe.poll() is None:
                assert time.monotonic() < deadline, 'probe deadline'
                trace = out / 'turn-1/trace.jsonl'
                if pause and not injected and trace.exists() and '"elapsed_ms":1' in trace.read_text():
                    before = time.monotonic_ns()
                    os.kill(server.pid, signal.SIGSTOP)
                    try: time.sleep(pause)
                    finally: os.kill(server.pid, signal.SIGCONT)
                    duration = time.monotonic_ns() - before
                    print(json.dumps({'case': case, 'injected_pause_ns': duration}), flush=True)
                    injected = True
                if case == 'retained-idle-suspend' and not sink_checked and (out / 'turn-1/result.json').exists():
                    time.sleep(3)
                    sinks = json.loads(subprocess.check_output(['pactl', '--format=json', 'list', 'sinks'], env=env, text=True))
                    inputs = json.loads(subprocess.check_output(['pactl', '--format=json', 'list', 'sink-inputs'], env=env, text=True))
                    assert len(sinks) == 1 and sinks[0]['state'] != 'SUSPENDED', sinks
                    voice = [i for i in inputs if i.get('properties', {}).get('media.name') == 'FreeDV diagnostic']
                    assert len(voice) == 1 and voice[0]['corked'] is False, inputs
                    print(json.dumps({'retained_idle_sink_state': sinks[0]['state'], 'corked': voice[0]['corked']}), flush=True)
                    sink_checked = True
                time.sleep(.025)
            turns = [json.loads(f.read_text()) for f in sorted(out.glob('turn-*/result.json'))]
            for turn in turns: print(json.dumps(turn), flush=True)
            if pause == .750:
                assert injected and probe.returncode == 1 and len(turns) == 1, (probe.returncode, turns)
                assert turns[0]['first_audio_fault'] == 16 and turns[0]['failures'] and turns[0]['eoo'] == 0, turns
            else:
                assert probe.returncode == 0 and len(turns) == 4, (probe.returncode, turns)
                assert all(t['failures'] == 0 and t['input_samples'] > 350000 and t['output_samples'] > 300000 and t['eoo'] == t['turn'] and t['rade_creations'] == 1 and t['capture_creations'] == 1 and t['capture_granted_maxlength_bytes'] == 88200 and t['flush_acknowledged'] for t in turns), turns
                if pause: assert injected
                if case == 'retained-idle-suspend': assert sink_checked
        finally:
            os.kill(server.pid, signal.SIGCONT)
            if probe and probe.poll() is None: probe.terminate(); probe.wait(timeout=5)
            server.terminate(); server.wait(timeout=5)
print('PASS: retained actual engine/adapter/PA, idle-suspend prevention, tolerated 250 ms transport pause, explicit capture-stall failure for 750 ms pause. Virtual devices; no RF/PTT/microphone qualification.', flush=True)
