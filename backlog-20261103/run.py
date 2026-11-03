from pathlib import Path
import os,subprocess,tempfile,time
binary=Path(__file__).parent/'pulse-playback-probe'
with tempfile.TemporaryDirectory(prefix='freedv-pulse-probe-',dir='/tmp') as temp:
 p=Path(temp);(p/'config').mkdir();conf=p/'default.pa'
 conf.write_text(f'.fail\nload-module module-native-protocol-unix socket={p}/native auth-anonymous=1\nload-module module-null-sink sink_name=freedv_probe_sink rate=44100 channels=2\nload-module module-null-source source_name=freedv_probe_source rate=44100 channels=2\nset-default-sink freedv_probe_sink\nset-default-source freedv_probe_source\n')
 env=dict(os.environ,PULSE_SERVER=f'unix:{p}/native',PULSE_RUNTIME_PATH=str(p),PULSE_STATE_PATH=str(p),XDG_CONFIG_HOME=str(p/'config'))
 server=subprocess.Popen(['pulseaudio','--daemonize=no','--use-pid-file=no','--exit-idle-time=-1','--disable-shm','--realtime=no','--high-priority=no','-nF',str(conf)],env=env)
 try:
  deadline=time.monotonic()+5
  while not (p/'native').exists() and server.poll() is None and time.monotonic()<deadline:time.sleep(.05)
  assert (p/'native').exists()
  for variant in ['baseline','trigger']:
   print('CASE '+variant,flush=True);subprocess.run([str(binary),variant],env=env,timeout=20,check=True)
 finally:
  server.terminate();server.wait(timeout=5)
