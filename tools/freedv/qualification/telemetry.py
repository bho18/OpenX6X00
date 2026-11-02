#!/usr/bin/env python3
"""Bounded read-only native telemetry capture; never keys or stops the radio."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import socket
import time


def rpc(command, path='/tmp/x6100_cfg.sock'):
    if command not in ('freedv_get','freedv_stats'):
        raise ValueError('read-only native telemetry commands only')
    with socket.socket(socket.AF_UNIX) as sock:
        sock.settimeout(2);sock.connect(path)
        sock.sendall((json.dumps({'cmd':command})+'\n').encode())
        data=b''
        while b'\n' not in data:
            part=sock.recv(8192)
            if not part:raise ValueError('incomplete telemetry response')
            data+=part
            if len(data)>32768:raise ValueError('oversized telemetry response')
    result=json.loads(data.split(b'\n',1)[0])
    expected='freedv' if command=='freedv_get' else 'freedv_stats'
    if result.get('t')!=expected:raise ValueError('wrong telemetry response')
    return result


def process_counts(pid):
    proc=Path('/proc')/str(pid)
    fields=(proc/'stat').read_text().rsplit(') ',1)[1].split()
    status=dict(line.split(':',1) for line in (proc/'status').read_text().splitlines() if ':' in line)
    return {'start_ticks':int(fields[19]),'cpu_ticks':int(fields[11])+int(fields[12]),
            'rss_kib':int(status['VmRSS'].split()[0]),'threads':len(list((proc/'task').iterdir())),
            'descriptors':len(list((proc/'fd').iterdir()))}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pid',type=int,required=True);parser.add_argument('--seconds',type=int,default=120)
    parser.add_argument('--interval-ms',type=int,default=100);parser.add_argument('--socket',default='/tmp/x6100_cfg.sock')
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if not 1<=args.seconds<=600 or not 50<=args.interval_ms<=1000:parser.error('duration 1..600 s; interval 50..1000 ms')
    args.output.mkdir(parents=True,exist_ok=False)
    report={'status':'INCONCLUSIVE','hardware_qualified':False,'listening':'UNASSESSED',
            'scope':'read-only telemetry, not RF timing','pid':args.pid,'requested_seconds':args.seconds,
            'interval_ms':args.interval_ms,'complete':False,'samples':0,'missed':0}
    try:
        first=process_counts(args.pid);binary=Path('/proc')/str(args.pid)/'exe'
        rpc('freedv_get',args.socket);rpc('freedv_stats',args.socket)
        report['executable']=os.readlink(binary);report['executable_sha256']=hashlib.sha256(binary.read_bytes()).hexdigest()
        report['clock_ticks_per_second']=os.sysconf('SC_CLK_TCK')
        start=time.monotonic();deadline=start+args.seconds;next_sample=start
        with (args.output/'samples.jsonl').open('x') as output:
            while time.monotonic()<deadline:
                row={'collector_monotonic_ns':time.monotonic_ns()}
                try:
                    row.update(state=rpc('freedv_get',args.socket),audio=rpc('freedv_stats',args.socket),process=process_counts(args.pid))
                    if row['process']['start_ticks']!=first['start_ticks']:raise RuntimeError('application process restarted')
                    if row['audio'].get('available'):
                        profile=row['audio'].get('profile_id')
                        if report.get('profile_id') and profile!=report['profile_id']:raise RuntimeError('profile changed during capture')
                        report['profile_id']=profile
                except (OSError,ValueError) as error:
                    row['error']=str(error);report['missed']+=1
                output.write(json.dumps(row,separators=(',',':'))+'\n');report['samples']+=1
                if output.tell()>16*1024*1024:raise ValueError('bounded telemetry output exhausted')
                next_sample+=args.interval_ms/1000
                time.sleep(max(0,min(next_sample,deadline)-time.monotonic()))
        report['complete']=True;report['duration_seconds']=time.monotonic()-start
        report['samples_sha256']=hashlib.sha256((args.output/'samples.jsonl').read_bytes()).hexdigest()
        report['reason']='Attach build/profile/preflight identities and assess; this capture alone cannot pass qualification'
    except (OSError,ValueError,RuntimeError) as error:
        report['reason']=str(error);report['status']='BLOCKED' if not report['samples'] else 'INCONCLUSIVE'
    (args.output/'telemetry.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2))
    return 0 if report['complete'] else 1


if __name__=='__main__':raise SystemExit(main())
