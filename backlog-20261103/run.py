from pathlib import Path
import os,subprocess,tempfile,time,json,lzma,hashlib
root=Path(__file__).resolve().parent
lib=root/'librade.so.0.1';lib.write_bytes(lzma.decompress(b''.join(f.read_bytes() for f in sorted(root.glob('librade.so.0.1.xz.part*')))))
binary=root/'voice-probe'
print(json.dumps({'probe_sha256':hashlib.sha256(binary.read_bytes()).hexdigest(),'rade_sha256':hashlib.sha256(lib.read_bytes()).hexdigest()}),flush=True)
for slow in [False,True]:
 fixed=True
 with tempfile.TemporaryDirectory(prefix='freedv-pulse-probe-',dir='/tmp') as temp:
  p=Path(temp);(p/'config').mkdir();out=root/('slow-legacy-idle-results' if slow else 'isolated-capture-results');out.mkdir();conf=p/'default.pa'
  option=' norewinds=1' if fixed else ''
  conf.write_text(f'.fail\nload-module module-native-protocol-unix socket={p}/native auth-anonymous=1\nload-module module-null-sink sink_name=freedv_probe_sink rate=44100 channels=2{option}\nload-module module-null-source source_name=freedv_probe_source rate=44100 channels=2\nset-default-sink freedv_probe_sink\nset-default-source freedv_probe_source\n')
  env=dict(os.environ,PULSE_SERVER=f'unix:{p}/native',PULSE_RUNTIME_PATH=str(p),PULSE_STATE_PATH=str(p),XDG_CONFIG_HOME=str(p/'config'),LD_LIBRARY_PATH=str(root))
  server=subprocess.Popen(['pulseaudio','--daemonize=no','--use-pid-file=no','--exit-idle-time=-1','--disable-shm','--realtime=no','--high-priority=no','-nF',str(conf)],env=env)
  try:
   deadline=time.monotonic()+5
   while not (p/'native').exists() and server.poll() is None and time.monotonic()<deadline:time.sleep(.05)
   assert (p/'native').exists()
   print('CASE '+('corrected-50ms-fixture' if fixed else 'baseline-2s-fixture'),flush=True)
   if slow:env.update(FREEDV_PROBE_SLOW_LEGACY='1',FREEDV_PROBE_LONG_IDLE='1')
   result=subprocess.run([str(binary),str(out)],env=env,timeout=125)
   turns=[json.loads(f.read_text()) for f in sorted(out.glob('turn-*/result.json'))]
   for turn in turns:print(json.dumps(turn),flush=True)
   if fixed:
    assert result.returncode==0 and len(turns)==4
    assert all(t['failures']==0 and t['input_samples']>300000 and t['output_samples']>300000 and t['eoo']==t['turn'] and t['rade_creations']==1 and t['flush_acknowledged'] for t in turns)
  finally:
   server.terminate();server.wait(timeout=5)
print('PASS: retained bounded capture, four turns normally and four with 600 ms legacy-consumer delay plus 20 s idle gaps. Synthetic interference regression; physical second-turn root cause remains unconfirmed.',flush=True)
