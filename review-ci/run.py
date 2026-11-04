from pathlib import Path
import base64,lzma,json,hashlib,os,subprocess,tempfile,time,threading
root=Path(__file__).resolve().parent
for name,spec in json.loads((root/'payload.json').read_text()).items():
 data=lzma.decompress(b''.join(base64.b64decode((root/f).read_bytes(),validate=True) for f in spec['parts']))
 assert hashlib.sha256(data).hexdigest()==spec['sha256'];(root/name).write_bytes(data)
(root/'probe').chmod(0o755)
(root/'playback-probe').chmod(0o755)
ref=Path('reference/backlog-20261103')
lib=lzma.decompress(b''.join(p.read_bytes() for p in sorted(ref.glob('librade.so.0.1.xz.part*'))))
assert hashlib.sha256(lib).hexdigest()=='7b7fc87081950a88175396a872fc0a0ee9be3762b0571e1dffa69018994f63e6'
(root/'librade.so.0.1').write_bytes(lib)
summary=[]
for name,turns,duration,cycles in [('speech-retained',4,8000,False)]:
 out=root/name;out.mkdir()
 with tempfile.TemporaryDirectory(prefix='freedv-pulse-probe-',dir='/tmp') as temp:
  p=Path(temp);(p/'config').mkdir();conf=p/'default.pa'
  conf.write_text(f'.fail\nload-module module-native-protocol-unix socket={p}/native auth-anonymous=1\nload-module module-null-sink sink_name=freedv_probe_sink rate=44100 channels=2 norewinds=1\nload-module module-null-sink sink_name=freedv_probe_input rate=44100 channels=1 norewinds=1\nset-default-sink freedv_probe_sink\nset-default-source freedv_probe_input.monitor\n')
  env=dict(os.environ,PULSE_SERVER=f'unix:{p}/native',PULSE_RUNTIME_PATH=str(p),PULSE_STATE_PATH=str(p),XDG_CONFIG_HOME=str(p/'config'),LD_LIBRARY_PATH=str(root),FREEDV_PROBE_SOURCE='freedv_probe_input.monitor',FREEDV_PROBE_TURNS=str(turns),FREEDV_PROBE_DURATION_MS=str(duration))
  if cycles:env['FREEDV_PROBE_CYCLES']='1'
  server=subprocess.Popen(['pulseaudio','--daemonize=no','--use-pid-file=no','--exit-idle-time=-1','--disable-shm','--realtime=no','--high-priority=no','-nF',str(conf)],env=env)
  feeder=None
  try:
   end=time.monotonic()+5
   while not (p/'native').exists() and server.poll() is None and time.monotonic()<end:time.sleep(.05)
   assert (p/'native').exists()
   feeder=subprocess.Popen(['pacat','--playback','--raw','--format=s16le','--rate=44100','--channels=1','--device=freedv_probe_input'],stdin=subprocess.PIPE,env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
   pcm=(root/'speech.raw').read_bytes()
   def feed(proc,pcm):
    try:
     while proc.poll() is None:proc.stdin.write(pcm);proc.stdin.flush()
    except (OSError,ValueError):pass
   threading.Thread(target=feed,args=(feeder,pcm),daemon=True).start()
   with (out/'probe.log').open('w') as log:subprocess.run([str(root/'probe'),str(out)],env=env,stdout=log,stderr=subprocess.STDOUT,timeout=turns*4+60 if cycles else 60,check=True)
   results=[json.loads(f.read_text()) for f in out.glob('turn-*/result.json')]
   assert len(results)==turns and all(t['failures']==0 and t['eoo']==(1 if cycles else t['turn']) and t['rade_creations']==1 and t['capture_creations']==(t['turn'] if cycles else 1) for t in results)
   with (out/'playback.log').open('w') as log:subprocess.run([str(root/'playback-probe'),'baseline'],env=env,stdout=log,stderr=subprocess.STDOUT,timeout=20,check=True)
   playback=[json.loads(line) for line in (out/'playback.log').read_text().splitlines() if line.startswith('{')]
   drains=[row for row in playback if 'drained' in row]
   assert len(drains)==3 and all(row['drained']==1 for row in drains)
   summary.append({'case':'one-block-playback','status':'COMPLETED','turns':3,'scope':'actual retained playback timing and underflow notifications; no emitted-waveform or RF qualification','observations':playback})
   summary.append({'case':name,'status':'PASS','turns':turns,'duration_ms':duration,'cycles':cycles})
  finally:
   if feeder is not None and feeder.poll() is None:feeder.terminate();feeder.wait(timeout=5)
   server.terminate();server.wait(timeout=5)
(root/'summary.json').write_text(json.dumps({'status':'PASS','cases':summary,'scope':'Actual engine/adapter host binary with frozen reference host codec and PA16; no RF, mic-route or listening qualification'},indent=2)+'\n')
print((root/'summary.json').read_text())
